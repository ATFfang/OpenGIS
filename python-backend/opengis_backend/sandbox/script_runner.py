"""
ScriptRunner — execute a user-authored Python script in the same
sandbox the CodeAgent uses.

This is the back-end of the Script Runner panel in the UI. It lets
the user *bypass the LLM* and invoke skills / rpc.* bridges directly,
which is the shortest path for:

* Validating a new skill before teaching the agent to use it.
* Reproducing bugs that only the agent triggers (agent failures are
  non-deterministic; a hand-written script is single-variable).
* Writing end-to-end regression fixtures.

Design rules (see MEMORY §"OpenGIS 第 1 号产品定位"):

* Re-use ``SubprocessPythonExecutor`` unchanged. Whatever the agent
  runs, the user script runs in the same box.
* Re-use ``build_tool_callables`` unchanged. Every ``@skill``
  function becomes a bare Python callable in the child's globals,
  under its own name. So a user script can just do::

      result = add_layer_from_path(path="E:/data/x.geojson")
      layers = list_layers()

  — same symbols the agent gets.
* ctx injection uses the same closure-provider trick as the agent
  (never ContextVar — that was ADR-008's lesson).
* Child stdout streams to the caller via an injected notify function
  under ``rpc.code.stdout``. Stderr + exceptions ship as
  ``rpc.code.stderr`` / the final ``done`` result.
* One run at a time. The runner's ``_current_executor`` guards this —
  callers who need parallelism should spawn multiple ScriptRunner
  instances.

Wire protocol (notifications the runner emits via ``notify_fn``):

* ``rpc.code.script_started`` — ``{run_id}``
* ``rpc.code.stdout``        — ``{run_id, text}``
* ``rpc.code.stderr``        — ``{run_id, text}``
* ``rpc.code.script_done``   — ``{run_id, ok, output?, error?, duration_ms}``

The caller (RpcHandler) returns the final result synchronously as
the RPC response and *also* emits ``script_done`` so the UI gets
the event on the notification channel even if it dropped the response.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from opengis_backend.agent.tools import build_tool_callables
from opengis_backend.agent.executor import (
    ChildDiedError,
    ExecTimeout,
    SubprocessExecutorConfig,
    SubprocessExecutorError,
    SubprocessPythonExecutor,
)
from opengis_backend.skills.context import SkillContext
from opengis_backend.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


# Async notify callback, same shape as SkillContext.notify_fn.
NotifyFn = Callable[[str, dict], Awaitable[None]]


class ScriptRunner:
    """Run a user-provided Python script in the SubprocessPythonExecutor.

    Instances are cheap; the executor itself is created per-run so
    every script starts from a clean Python interpreter (matches the
    agent's "one executor per agent.run" convention — see MEMORY
    §"Stage 2 executor 每 run 重建").
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        notify_fn: Optional[NotifyFn] = None,
    ) -> None:
        self._skills = skill_registry
        self._notify_fn = notify_fn
        self._current_executor: Optional[SubprocessPythonExecutor] = None
        self._current_run_id: Optional[str] = None

    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._current_executor is not None

    # ------------------------------------------------------------------

    async def run(
        self,
        code: str,
        *,
        run_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        exec_timeout: float = 600.0,
    ) -> dict:
        """Execute ``code`` in a fresh child process.

        Parameters
        ----------
        code:
            Python source. May be multiple statements, imports, etc. —
            anything ``exec`` would accept.
        run_id:
            Correlation id. Auto-generated if not provided.
        workspace_path:
            Child cwd. If None, the child inherits our cwd.
        exec_timeout:
            Max seconds for the single exec call. Default 10 min
            (matches agent). Hard-killed on expiry.

        Returns
        -------
        dict with keys:
            * ``ok``        — bool
            * ``run_id``    — str (echo)
            * ``output``    — the expression value of the last line, if any
            * ``logs``      — captured stdout (also streamed live)
            * ``error``     — str, only when ok=False
            * ``duration_ms`` — int

        Raises
        ------
        RuntimeError
            If another script is already running on this instance.
        """
        if self._current_executor is not None:
            raise RuntimeError(
                "ScriptRunner is busy: another script is still running. "
                "Cancel it first via cancel() or wait for it to finish."
            )

        rid = run_id or f"script_{uuid.uuid4().hex[:12]}"
        self._current_run_id = rid

        # Per-run SkillContext — the same object the agent builds for
        # its CodeAgent runs. Ctx-aware skills (map display, etc.)
        # will call ctx.notify_fn(...) which hops back onto the ws loop.
        ctx = SkillContext(
            notify_fn=self._notify_fn,
            conversation_id=None,
            meta={"workspace_path": workspace_path} if workspace_path else {},
        )

        await self._safe_notify("rpc.code.script_started", {"run_id": rid})

        # Capture the caller's event loop so child-thread stdout can
        # hop back onto it to schedule the notification send.
        loop = asyncio.get_running_loop()

        def _on_child_stdout(text: str) -> None:
            # Runs on a reader thread. Schedule notify on the ws loop.
            if not self._notify_fn:
                return
            try:
                asyncio.run_coroutine_threadsafe(
                    self._safe_notify(
                        "rpc.code.stdout",
                        {"run_id": rid, "text": text},
                    ),
                    loop,
                ).result(timeout=5)
            except Exception as e:  # noqa: BLE001
                logger.warning("stdout notify failed: %s", e)

        executor = SubprocessPythonExecutor(
            config=SubprocessExecutorConfig(
                working_dir=workspace_path,
                exec_timeout=exec_timeout,
            ),
            stdout_listener=_on_child_stdout,
        )
        self._current_executor = executor

        started_at = time.monotonic()
        try:
            # Bind skills → callable stubs inside the child.
            # The ctx_provider closure is what makes ctx-aware skills
            # see the *right* SkillContext for THIS run.
            #
            # IMPORTANT: use list_registered() — it returns RegisteredSkill
            # records (which carry raw_function + needs_context).
            # list_all() only gives SkillSchema and breaks the wrapper with
            # "'SkillSchema' object has no attribute 'schema'".
            registered = self._skills.list_registered()
            tool_dict = build_tool_callables(registered, ctx_provider=lambda: ctx)

            # send_tools is synchronous (it does a stdin write + expect).
            # Run it on a worker thread so we don't block the ws loop.
            await asyncio.to_thread(executor.send_tools, tool_dict)

            # Actual exec. Also offloaded so the ws loop stays responsive
            # and we can still receive rpc.code.cancel_script messages.
            code_output = await asyncio.to_thread(executor, code)

            duration_ms = int((time.monotonic() - started_at) * 1000)

            # Check if the child reported an error.
            if code_output.error:
                result = {
                    "ok": False,
                    "run_id": rid,
                    "error": code_output.error,
                    "logs": code_output.logs or "",
                    "duration_ms": duration_ms,
                }
                await self._safe_notify("rpc.code.script_done", result)
                return result

            result = {
                "ok": True,
                "run_id": rid,
                "output": _jsonable(code_output.output),
                "logs": code_output.logs or "",
                "is_final_answer": bool(code_output.is_final_answer),
                "duration_ms": duration_ms,
            }
            await self._safe_notify("rpc.code.script_done", result)
            return result

        except ExecTimeout:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            result = {
                "ok": False,
                "run_id": rid,
                "error": f"exec_timeout ({exec_timeout:.0f}s)",
                "duration_ms": duration_ms,
            }
            await self._safe_notify("rpc.code.script_done", result)
            return result
        except ChildDiedError as e:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            result = {
                "ok": False,
                "run_id": rid,
                "error": f"child_died: {e}",
                "duration_ms": duration_ms,
            }
            await self._safe_notify("rpc.code.script_done", result)
            return result
        except SubprocessExecutorError as e:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            result = {
                "ok": False,
                "run_id": rid,
                "error": f"executor_error: {e}",
                "duration_ms": duration_ms,
            }
            await self._safe_notify("rpc.code.script_done", result)
            return result
        except Exception as e:  # noqa: BLE001
            # Runtime error inside the child; the executor already wraps
            # it as RuntimeError with captured logs.
            duration_ms = int((time.monotonic() - started_at) * 1000)
            result = {
                "ok": False,
                "run_id": rid,
                "error": str(e),
                "duration_ms": duration_ms,
            }
            await self._safe_notify("rpc.code.script_done", result)
            return result
        finally:
            # Tear the child down — scripts run in one-shot sandboxes.
            try:
                await asyncio.to_thread(executor.cleanup)
            except Exception as e:  # noqa: BLE001
                logger.warning("executor cleanup failed: %s", e)
            self._current_executor = None
            self._current_run_id = None

    # ------------------------------------------------------------------

    def cancel(self) -> bool:
        """Interrupt the running script, if any.

        Returns True if a script was running and we issued the
        interrupt; False if nothing to cancel. Safe to call from any
        thread.
        """
        ex = self._current_executor
        if ex is None:
            return False
        try:
            ex.interrupt()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("executor interrupt failed: %s", e)
            return False

    # ------------------------------------------------------------------

    async def _safe_notify(self, method: str, params: dict) -> None:
        if not self._notify_fn:
            return
        try:
            await self._notify_fn(method, params)
        except Exception as e:  # noqa: BLE001
            logger.warning("notify %s failed: %s", method, e)


# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Best-effort convert exec output to something JSON-serialisable.

    SubprocessPythonExecutor already json-serialises its own messages,
    but the ``output`` field may be a bare Python object that survived
    json.dumps(default=str). We do a second pass here: primitives stay,
    everything else falls to repr.
    """
    try:
        import json
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


__all__ = ["ScriptRunner", "NotifyFn"]
