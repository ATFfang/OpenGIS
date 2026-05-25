"""Subprocess-backed Python executor for the OpenGIS agent loop.

Replaces the default in-process executor with a **real** Python subprocess.
The child has the full stdlib, can ``pip install``, ``git clone``, spawn GPU
workloads, and run for tens of minutes — exactly what's needed for
GNNWR-class workloads.

Safety is deferred to Stage 4 (permission gate + workspace git snapshot).
The sandbox philosophy here is explicitly **"Claude Code-style", not
"Docker-style"**.

Protocol
--------
See ``_subprocess_runner.py`` for the full wire format. In short:

* Parent and child exchange newline-delimited JSON over stdin/stdout.
* The child has stub functions for each Tool; calling a stub pipes
  an RPC back to the parent, which invokes the real Tool and sends
  the result back.
* Output value of an ``exec`` call is returned via ``{"kind":"done",
  "ok": true, "output": ..., "is_final_answer": ..., "logs": ...}``.
"""

from __future__ import annotations

import json
import asyncio
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from opengis_backend.agent.agent_loop import CodeExecResult

_IS_WINDOWS = sys.platform == "win32"


# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SubprocessExecutorConfig:
    """Per-run configuration for the subprocess executor."""

    # Python interpreter to run in the child. Defaults to the same one
    # that's running us, which is correct for dev. Stage 4 will switch
    # this to the workspace-local venv so pip installs stay per-project.
    python_executable: str = field(default_factory=lambda: sys.executable)

    # Working directory for the child. Stage 4 / the chat-layer caller
    # should pass the workspace root; None → child inherits cwd.
    working_dir: Optional[str] = None

    # Extra env for the child. We always set ``PYTHONUNBUFFERED=1``.
    env: Optional[dict[str, str]] = None

    # Seconds to wait for a single exec call before we SIGTERM.
    # None → no per-call timeout. Default 10 min matches the existing
    # agent.chat budget in MEMORY.
    exec_timeout: Optional[float] = 600.0

    # Seconds to wait after SIGTERM before we SIGKILL on shutdown.
    kill_grace: float = 2.0


# ─────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────


class SubprocessExecutorError(RuntimeError):
    """Wrapper for anything that breaks the IPC contract."""


class ChildDiedError(SubprocessExecutorError):
    """The child process exited before completing the requested operation."""


class ExecTimeout(SubprocessExecutorError):
    """A single exec call exceeded ``config.exec_timeout``."""


# ─────────────────────────────────────────────────────────────────────
# Executor
# ─────────────────────────────────────────────────────────────────────


class SubprocessPythonExecutor:
    """Subprocess executor for the agent loop.

    Implements the protocol:

    * ``send_tools(tools: dict[str, Any]) -> None``
    * ``send_variables(variables: dict) -> None``
    * ``__call__(code_action: str) -> CodeExecResult``
    * ``cleanup() -> None``

    Lifecycle
    ---------
    The child is spawned **lazily** on first use (first ``send_tools`` or
    first ``__call__``), and re-used across multiple ``__call__`` invocations
    for the same agent.run — so state (imports, dataframes, trained models)
    persists between steps just like in a Jupyter kernel.

    Thread-safety
    -------------
    The agent loop calls ``__call__`` one at a time.
    We therefore *don't* need to serialise exec calls ourselves. We DO use
    a background reader thread to drain stdout into a queue.
    """

    def __init__(
        self,
        config: Optional[SubprocessExecutorConfig] = None,
        stdout_listener: Optional[Callable[[str], None]] = None,
        risky_op_listener: Optional[Callable[[dict], None]] = None,
        plot_saved_listener: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.config = config or SubprocessExecutorConfig()
        self._stdout_listener = stdout_listener
        self._risky_op_listener = risky_op_listener
        self._plot_saved_listener = plot_saved_listener
        # Tool registry. Key is the tool name as the LLM sees it;
        # value is the callable we invoke on RPC (a Tool forward, usually).
        self._tools: dict[str, Any] = {}
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        # Messages coming from the child, except 'stdout' and 'stderr'
        # which we fan out to listeners immediately.
        self._msg_q: "queue.Queue[dict]" = queue.Queue()
        self._shutdown = threading.Event()
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

    # ─────────────────────────────────────────────────────────────────────
    # Agent loop protocol
    # ─────────────────────────────────────────────────────────────────────

    def send_tools(self, tools: dict[str, Any]) -> None:
        """Register tools and re-init the child with their names.

        We snapshot callables here. The child doesn't see the tools
        themselves — it gets *names only* and receives values via
        ``tool_call`` RPCs back to us.

        ``tools`` is a dict mapping tool name → callable.
        """
        self._tools = dict(tools)
        self._ensure_child()
        self._send({
            "kind": "init",
            "tool_names": list(self._tools.keys()),
            # Kept for forward-compat; the subprocess currently ignores
            # this since we don't enforce import whitelists.
            "authorized_imports": ["*"],
        })
        msg = self._expect({"init_ok"})
        if msg.get("kind") != "init_ok":
            raise SubprocessExecutorError(f"Unexpected init response: {msg!r}")

    def send_variables(self, variables: dict[str, Any]) -> None:
        """Inject named values into the child's globals.

        Can be used to pre-seed state (for example an initial plan).
        We route each entry via ``set_var``.
        """
        self._ensure_child()
        for name, value in variables.items():
            # Only JSON-serialisable values survive the pipe. Non-JSON
            # values are dropped with a warning — matches the Docker/E2B
            # executor behaviour.
            try:
                json.dumps(value, default=str)
            except (TypeError, ValueError):
                self._log_stderr(
                    f"[SubprocessPythonExecutor] skipping non-serialisable var '{name}'\n"
                )
                continue
            self._send({"kind": "set_var", "name": name, "value": value})
            self._expect({"set_var_ok"})

    def __call__(self, code_action: str) -> CodeExecResult:
        """Execute ``code_action`` in the child and return a CodeExecResult."""
        self._ensure_child()
        self._send({"kind": "exec", "code": code_action})

        deadline = (
            time.monotonic() + self.config.exec_timeout
            if self.config.exec_timeout is not None
            else None
        )

        while True:
            msg = self._recv(deadline=deadline)
            kind = msg.get("kind")

            if kind == "stdout":
                # Live-streamed print output. Already routed by _reader_loop.
                continue

            if kind == "tool_call":
                self._handle_tool_call(msg)
                continue

            if kind == "done":
                if msg.get("ok"):
                    return CodeExecResult(
                        output=msg.get("output"),
                        logs=msg.get("logs", "") or "",
                        is_final_answer=bool(msg.get("is_final_answer", False)),
                    )
                # Child-side failure: wrap as a CodeExecResult with error.
                err = msg.get("error") or "unknown error"
                logs = msg.get("logs", "") or ""
                return CodeExecResult(
                    error=f"{err}\n--- stdout before error ---\n{logs}",
                    logs=logs,
                )

            # Anything else is a protocol bug; log and keep waiting.
            self._log_stderr(f"[executor] unexpected msg: {msg!r}\n")

    def cleanup(self) -> None:
        """Shut the child down. Safe to call more than once."""
        if self._proc is None:
            return
        self._shutdown.set()
        proc = self._proc
        try:
            if proc.poll() is None:
                try:
                    self._send({"kind": "shutdown"})
                except Exception:
                    pass
                # Give the child a short grace window, then escalate.
                try:
                    proc.wait(timeout=self.config.kill_grace)
                except subprocess.TimeoutExpired:
                    self._terminate(proc)
        finally:
            self._proc = None
            if self._reader_thread and self._reader_thread.is_alive():
                # Reader will drop out once the pipe closes.
                self._reader_thread.join(timeout=5.0)
            self._reader_thread = None
            if self._async_loop is not None and not self._async_loop.is_closed():
                self._async_loop.close()
                self._async_loop = None

    # ─────────────────────────────────────────────────────────────────────
    # Interrupt (called by agent.cancel in Stage 4+)
    # ─────────────────────────────────────────────────────────────────────

    def interrupt(self) -> None:
        """Ask the child to stop what it's doing, SIGKILL if it won't."""
        import logging as _logging
        _log = _logging.getLogger(__name__)
        proc = self._proc
        _log.info("[EXEC-DEBUG] interrupt() called, proc=%s, poll=%s",
                  proc.pid if proc else None,
                  proc.poll() if proc else "N/A")
        if proc is None or proc.poll() is not None:
            _log.info("[EXEC-DEBUG] interrupt() early return: proc already dead or None")
            return
        try:
            if _IS_WINDOWS:
                _log.info("[EXEC-DEBUG] sending CTRL_BREAK_EVENT to pid=%d", proc.pid)
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                _log.info("[EXEC-DEBUG] sending SIGINT to pid=%d", proc.pid)
                proc.send_signal(signal.SIGINT)
        except Exception as e:
            self._log_stderr(f"[executor] interrupt send failed: {e}\n")
        try:
            proc.wait(timeout=self.config.kill_grace)
            _log.info("[EXEC-DEBUG] child exited after signal, code=%s", proc.returncode)
            return
        except subprocess.TimeoutExpired:
            _log.info("[EXEC-DEBUG] child did not exit in %.1fs, escalating...", self.config.kill_grace)
        # Grace window expired — kill the whole tree.
        if _IS_WINDOWS:
            try:
                _log.info("[EXEC-DEBUG] running taskkill /F /T /PID %d", proc.pid)
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                    timeout=5.0,
                )
                _log.info("[EXEC-DEBUG] taskkill done")
            except Exception as e:
                self._log_stderr(f"[executor] taskkill failed: {e}\n")
                self._terminate(proc)
        else:
            self._terminate(proc)

    # ─────────────────────────────────────────────────────────────────────
    # Internals: subprocess lifecycle
    # ─────────────────────────────────────────────────────────────────────

    def _ensure_child(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return

        env = os.environ.copy()
        if self.config.env:
            env.update(self.config.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        creationflags = 0
        start_new_session = False
        if _IS_WINDOWS:
            # CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT later.
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            start_new_session = True  # new process group, for SIGKILL fan-out

        self._proc = subprocess.Popen(
            [
                self.config.python_executable,
                "-u",
                "-m",
                "opengis_backend.agent._subprocess_runner",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.config.working_dir,
            env=env,
            creationflags=creationflags,
            start_new_session=start_new_session,
            bufsize=1,  # line-buffered on the parent side
            text=True,
            encoding="utf-8",
        )
        self._shutdown.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="opengis-exec-reader",
            daemon=True,
        )
        self._reader_thread.start()

        # Also drain stderr in a daemon thread so a chatty child can't
        # deadlock the pipe.
        threading.Thread(
            target=self._stderr_loop,
            name="opengis-exec-stderr",
            daemon=True,
        ).start()

        # Wait for the initial "ready" handshake.
        ready = self._expect({"ready"}, timeout=5.0)
        if ready.get("kind") != "ready":
            raise SubprocessExecutorError(f"Child did not report ready: {ready!r}")

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            if _IS_WINDOWS:
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=self.config.kill_grace)
            except subprocess.TimeoutExpired:
                if _IS_WINDOWS:
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception as e:
            self._log_stderr(f"[executor] terminate failed: {e}\n")

    # ─────────────────────────────────────────────────────────────────────
    # Internals: IPC primitives
    # ─────────────────────────────────────────────────────────────────────

    def _send(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise SubprocessExecutorError("Child not running")
        line = json.dumps(obj, default=str) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ChildDiedError(f"Child pipe closed while writing: {e}") from e

    def _recv(self, deadline: Optional[float] = None) -> dict:
        """Block on the next control message from the child.

        ``stdout`` messages are handled inline by the reader loop (fanned
        out to listeners), so they don't appear here — otherwise long
        prints would spam the caller's message stream.
        """
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Per-call timeout. Kill the child so we don't leave
                    # a runaway training job behind.
                    self._log_stderr("[executor] exec timeout — killing child\n")
                    self.interrupt()
                    raise ExecTimeout("exec call exceeded timeout")
                try:
                    return self._msg_q.get(timeout=remaining)
                except queue.Empty:
                    continue
            # No deadline: wait with a small poll so we notice if the
            # child dies.
            try:
                return self._msg_q.get(timeout=0.5)
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise ChildDiedError(
                        f"Child exited with code {self._proc.returncode}"
                    )

    def _expect(
        self,
        kinds: set[str],
        timeout: Optional[float] = 10.0,
    ) -> dict:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            msg = self._recv(deadline=deadline)
            if msg.get("kind") in kinds:
                return msg
            # Drop unexpected messages, continue waiting.
            self._log_stderr(f"[executor] dropping unexpected msg: {msg!r}\n")

    def _reader_loop(self) -> None:
        """Drain the child's stdout forever."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if self._shutdown.is_set():
                break
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON output from the child — shouldn't happen, but
                # surface it so we don't silently lose it.
                self._log_stderr(f"[child non-JSON] {line}\n")
                continue

            kind = msg.get("kind")
            if kind == "stdout":
                text = msg.get("text", "")
                if self._stdout_listener is not None:
                    try:
                        self._stdout_listener(text)
                    except Exception as e:
                        self._log_stderr(f"[executor] stdout listener raised: {e}\n")
                # Also forward stdout messages into the main queue so
                # __call__ can observe them if it wants — but normally
                # they are dropped by _recv's filter above.
                continue
            if kind == "stderr":
                self._log_stderr(msg.get("text", ""))
                continue
            if kind == "risky_op":
                # D3 telemetry — fan out to the listener, do NOT enqueue
                # (so exec's __call__ loop never sees these).
                if self._risky_op_listener is not None:
                    try:
                        self._risky_op_listener(msg)
                    except Exception as e:
                        self._log_stderr(f"[executor] risky_op listener raised: {e}\n")
                continue
            if kind == "plot_saved":
                # Child saved a matplotlib figure locally and wants the
                # parent to notify the chat UI.
                if self._plot_saved_listener is not None:
                    try:
                        self._plot_saved_listener(msg)
                    except Exception as e:
                        self._log_stderr(f"[executor] plot_saved listener raised: {e}\n")
                continue
            self._msg_q.put(msg)

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            if self._shutdown.is_set():
                break
            self._log_stderr(line)

    def _log_stderr(self, text: str) -> None:
        # Forward the child's stderr through our own stderr so that
        # existing Python logging (RotatingFileHandler in logging_setup.py)
        # picks it up naturally.
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Internals: tool bridge
    # ─────────────────────────────────────────────────────────────────────

    def _handle_tool_call(self, msg: dict) -> None:
        """Invoke a registered tool on behalf of the child."""
        call_id = msg.get("call_id") or uuid.uuid4().hex
        name = msg.get("name")
        args = msg.get("args") or []
        kwargs = msg.get("kwargs") or {}

        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            self._send({
                "kind": "tool_result",
                "call_id": call_id,
                "ok": False,
                "error": f"Tool not found: {name!r}",
            })
            return

        try:
            # Tool is callable: tool(*args, **kwargs) invokes the skill.
            value = tool(*args, **kwargs)
            # Defensive: if the skill is `async def` (it shouldn't be —
            # see docs/ARCHITECTURE.md §Skill Invocation), unwrap the
            # coroutine on a private loop. Without this guard, we'd hand
            # back a `<coroutine object>` to the child and the LLM would
            # be very confused.
            import inspect as _inspect
            if _inspect.iscoroutine(value):
                import asyncio as _asyncio
                if self._async_loop is None or self._async_loop.is_closed():
                    self._async_loop = _asyncio.new_event_loop()
                value = self._async_loop.run_until_complete(value)
        except Exception as e:  # noqa: BLE001
            self._send({
                "kind": "tool_result",
                "call_id": call_id,
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
            })
            return

        # Ensure the return value survives the pipe. If it doesn't,
        # fall back to repr — matches the JSON-RPC handler's NumpyEncoder
        # spirit.
        try:
            json.dumps(value, default=str)
            safe_value = value
        except (TypeError, ValueError):
            safe_value = repr(value)

        self._send({
            "kind": "tool_result",
            "call_id": call_id,
            "ok": True,
            "value": safe_value,
        })


__all__ = [
    "SubprocessPythonExecutor",
    "SubprocessExecutorConfig",
    "SubprocessExecutorError",
    "ChildDiedError",
    "ExecTimeout",
]
