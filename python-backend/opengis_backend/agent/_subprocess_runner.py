"""Child-process Python runner used by ``SubprocessPythonExecutor``.

This module is launched with ``python -u -m opengis_backend.agent._subprocess_runner``
as a **subprocess** from the parent agent process. All communication happens
over stdin/stdout using newline-delimited JSON.

Design principles
-----------------
1.  The parent owns the Tool functions (they close over SkillContext, etc.).
    The child cannot call them directly. Instead each tool becomes a **stub**
    in the child's namespace that RPC-calls back to the parent over the pipe.
2.  The child's state persists across ``exec`` calls — globals accumulate,
    so ``gdf = gpd.read_file(...)`` followed by a separate ``print(gdf.head())``
    works exactly like in Jupyter.
3.  No sandbox walls here. This is the "Claude Code philosophy": open filesystem,
    open network, open pip. Safety comes from the parent's permission gate
    (Stage 4) and workspace git snapshots, not from a whitelist.

Protocol (see SubprocessPythonExecutor for the parent side)
-----------------------------------------------------------
Parent → child (stdin, one JSON per line)::

    {"kind": "init", "tool_names": [...], "authorized_imports": [...]}
    {"kind": "set_var", "name": "...", "value": <json>}
    {"kind": "exec", "code": "..."}
    {"kind": "tool_result", "call_id": "...", "ok": true, "value": ...}
    {"kind": "tool_result", "call_id": "...", "ok": false, "error": "..."}
    {"kind": "shutdown"}

Child → parent (stdout, one JSON per line)::

    {"kind": "ready"}
    {"kind": "stdout", "text": "..."}
    {"kind": "tool_call", "call_id": "...", "name": "...", "args": {...}, "kwargs": {...}}
    {"kind": "done", "ok": true, "output": <json|null>, "is_final_answer": bool, "logs": "..."}
    {"kind": "done", "ok": false, "error": "traceback text", "logs": "..."}

Stderr is free-form — unbuffered forwards of the child's own ``sys.stderr``
go there and the parent folds them into logs.
"""

from __future__ import annotations

import builtins
import io
import json
import sys
import traceback
import uuid
from typing import Any

# final_answer convention — LLM calls ``final_answer(x)`` to
# signal the run is done. We mirror that behaviour here so that the
# agent loop terminates correctly when the LLM chooses to finish.
_FINAL_ANSWER_SENTINEL = "__opengis_final_answer__"


class _FinalAnswer(BaseException):
    """Raised by the ``final_answer`` stub to unwind out of exec()."""

    def __init__(self, value: Any) -> None:
        super().__init__(_FINAL_ANSWER_SENTINEL)
        self.value = value


# ─────────────────────────────────────────────────────────────────────
# IPC helpers
# ─────────────────────────────────────────────────────────────────────


def _emit(obj: dict) -> None:
    """Send a JSON message to the parent. Always flushes."""
    sys.__stdout__.write(json.dumps(obj, default=_json_default) + "\n")
    sys.__stdout__.flush()


def _json_default(o: Any) -> Any:
    """Fallback encoder for values we can't easily serialise.

    The idea is to never crash the pipe. Unserialisable values become
    their ``repr``; the parent and LLM see a best-effort string.
    """
    try:
        return repr(o)
    except Exception:
        return f"<unserialisable {type(o).__name__}>"


def _read_message() -> dict | None:
    """Blocking read of one newline-delimited JSON from stdin."""
    line = sys.__stdin__.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        _emit({"kind": "stderr", "text": f"[runner] bad JSON from parent: {e}\n"})
        return {}


# ─────────────────────────────────────────────────────────────────────
# Tool stub: forwards calls to parent over the pipe
# ─────────────────────────────────────────────────────────────────────


def _make_tool_stub(name: str):
    """Return a callable that RPC-invokes ``name`` on the parent."""

    def stub(*args, **kwargs):
        call_id = uuid.uuid4().hex
        _emit({
            "kind": "tool_call",
            "call_id": call_id,
            "name": name,
            # We send args as a list so JSON works; the parent rebuilds.
            "args": list(args),
            "kwargs": kwargs,
        })
        # Wait for a tool_result with the matching call_id. Any other
        # message types arriving here are protocol bugs — surface loudly.
        while True:
            msg = _read_message()
            if msg is None:
                raise RuntimeError(f"Parent closed pipe while tool '{name}' was in flight")
            if msg.get("kind") == "tool_result" and msg.get("call_id") == call_id:
                if msg.get("ok"):
                    return msg.get("value")
                raise RuntimeError(
                    f"Tool '{name}' failed: {msg.get('error') or 'unknown error'}"
                )
            # Unexpected interleaving — log and keep waiting. We do NOT
            # try to handle nested tool calls here; the parent is
            # responsible for serialising.
            _emit({
                "kind": "stderr",
                "text": f"[runner] unexpected msg while waiting for tool_result: {msg!r}\n",
            })

    stub.__name__ = name
    stub.__qualname__ = name
    return stub


def _final_answer_stub(value: Any = None) -> None:
    """Convention — LLM calls this to end the run."""
    raise _FinalAnswer(value)


# ─────────────────────────────────────────────────────────────────────
# Risky-op hooks (D3) — observe, don't block.
# ─────────────────────────────────────────────────────────────────────
#
# We patch a small, curated set of write-effect builtins so every
# destructive / write call the LLM makes shows up in meta.json.risky_ops.
# These are **observations**, not enforcement — we never refuse the call.
# Rationale (MEMORY "OpenGIS 第 1 号产品定位"): the safety net is workspace
# git snapshot + Stop button, not a permission wall.
#
# Tested indirectly via tests/test_subprocess_executor.py (existing) and
# added coverage in tests/test_risky_ops_hook.py.


def _install_risky_op_hooks() -> None:
    """Monkey-patch os / shutil / pathlib / builtins.open to emit risky_op."""
    import os as _os
    import pathlib as _pl
    import shutil as _shutil

    def _report(op: str, path: Any, **extra: Any) -> None:
        try:
            payload = {"kind": "risky_op", "op": op, "path": str(path)}
            if extra:
                payload["extra"] = {k: str(v) for k, v in extra.items()}
            _emit(payload)
        except Exception:
            # Never let telemetry break the user's code.
            pass

    # ─── os.remove / os.unlink ───
    _os_remove = _os.remove
    _os_unlink = _os.unlink

    def remove(path, *a, **kw):
        _report("os.remove", path)
        return _os_remove(path, *a, **kw)

    def unlink(path, *a, **kw):
        _report("os.unlink", path)
        return _os_unlink(path, *a, **kw)

    _os.remove = remove  # type: ignore[assignment]
    _os.unlink = unlink  # type: ignore[assignment]

    # ─── shutil.rmtree ───
    _rmtree = _shutil.rmtree

    def rmtree(path, *a, **kw):
        _report("shutil.rmtree", path)
        return _rmtree(path, *a, **kw)

    _shutil.rmtree = rmtree  # type: ignore[assignment]

    # ─── pathlib.Path.unlink / .write_text / .write_bytes ───
    _path_unlink = _pl.Path.unlink
    _path_write_text = _pl.Path.write_text
    _path_write_bytes = _pl.Path.write_bytes

    def path_unlink(self, *a, **kw):
        _report("Path.unlink", self)
        return _path_unlink(self, *a, **kw)

    def path_write_text(self, data, *a, **kw):
        _report("Path.write_text", self, bytes=len(data) if isinstance(data, str) else 0)
        return _path_write_text(self, data, *a, **kw)

    def path_write_bytes(self, data, *a, **kw):
        _report("Path.write_bytes", self, bytes=len(data) if isinstance(data, (bytes, bytearray)) else 0)
        return _path_write_bytes(self, data, *a, **kw)

    _pl.Path.unlink = path_unlink  # type: ignore[assignment]
    _pl.Path.write_text = path_write_text  # type: ignore[assignment]
    _pl.Path.write_bytes = path_write_bytes  # type: ignore[assignment]

    # ─── builtins.open with write mode ───
    # We DO NOT report reads ('r', 'rb', ''); reads are not risky. We only
    # flag modes containing 'w' / 'a' / 'x' / '+'.
    _real_open = builtins.open

    def open_hook(file, mode="r", *a, **kw):  # noqa: D401
        try:
            if isinstance(mode, str) and any(c in mode for c in ("w", "a", "x", "+")):
                _report("open", file, mode=mode)
        except Exception:
            pass
        return _real_open(file, mode, *a, **kw)

    builtins.open = open_hook  # type: ignore[assignment]
# ─────────────────────────────────────────────────────────────────────
# Matplotlib pyplot patch -- auto-invoke save_plot() on plt.savefig/show
# ─────────────────────────────────────────────────────────────────────

def _install_pyplot_patch(namespace: dict) -> None:
    """Monkey-patch matplotlib.pyplot so plt.savefig/show auto-call save_plot().

    Must be called AFTER namespace is built (save_plot must be available
    as a callable in the namespace).

    IMPORTANT: We must NOT call _do_patch while matplotlib.pyplot is still
    being initialised (i.e. during a sub-import triggered by pyplot's own
    module body). Accessing ``plt_module.savefig`` on a partially-loaded
    module raises ``AttributeError`` and corrupts the module cache, making
    ALL subsequent imports of matplotlib fail for the rest of the process.

    Strategy: use a re-entrancy guard (``_import_depth``) so that
    ``_do_patch`` is only attempted when the *outermost* import call
    returns — at that point the module is fully initialised.
    """
    import builtins
    import sys

    save_plot_fn = namespace.get("save_plot")
    if save_plot_fn is None:
        return

    _original_import = builtins.__import__
    _import_depth = [0]  # mutable counter shared by the closure

    def _do_patch(plt_module):
        if getattr(plt_module, "_opengis_patched", False):
            return
        # Safety: only patch if the module is fully loaded (has savefig).
        if not hasattr(plt_module, "savefig"):
            return
        _original_savefig = plt_module.savefig

        def patched_savefig(*a, **kw):
            try:
                save_plot_fn(auto_close=False)
            except Exception:
                pass
            return _original_savefig(*a, **kw)

        def patched_show(*a, **kw):
            try:
                save_plot_fn(auto_close=False)
            except Exception:
                pass

        plt_module.savefig = patched_savefig
        plt_module.show = patched_show
        plt_module._opengis_patched = True

    def patched_import(name, globals=None, locals=None, fromlist=(), level=0):
        _import_depth[0] += 1
        try:
            result = _original_import(name, globals, locals, fromlist, level)
        finally:
            _import_depth[0] -= 1

        # Only attempt the patch when we are back at the outermost import
        # call — this guarantees matplotlib.pyplot is fully initialised.
        if _import_depth[0] == 0 and "matplotlib.pyplot" in sys.modules:
            plt_mod = sys.modules["matplotlib.pyplot"]
            if not getattr(plt_mod, "_opengis_patched", False):
                _do_patch(plt_mod)
        return result

    builtins.__import__ = patched_import

    if "matplotlib.pyplot" in sys.modules:
        _do_patch(sys.modules["matplotlib.pyplot"])



# ─────────────────────────────────────────────────────────────────────
# Captured-stdout wrapper
# ─────────────────────────────────────────────────────────────────────


class _TeeStdout(io.TextIOBase):
    """Captures user ``print`` output: both buffers it for logs and streams
    it to the parent as ``stdout`` messages in near real time.

    We keep a rolling buffer so that ``CodeOutput.logs`` matches what
    the agent loop expects (the concatenated prints from this exec call).
    """

    def __init__(self) -> None:
        self._buf: list[str] = []

    def writable(self) -> bool:  # noqa: D401
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf.append(s)
        _emit({"kind": "stdout", "text": s})
        return len(s)

    def flush(self) -> None:  # nothing to flush; _emit already flushed
        pass

    def getvalue(self) -> str:
        return "".join(self._buf)


# ─────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────


def _make_local_save_plot():
    """Build a subprocess-local ``save_plot`` that saves the figure in-process.

    The parent's ``save_plot`` skill runs in a *different* process and
    therefore cannot see the child's matplotlib figures (``plt.gcf()``
    returns an empty figure there).  This local implementation:

    1. Calls ``plt.gcf()`` in the child where the figure actually lives.
    2. Saves the PNG to ``<cwd>/assets/plots/``.
    3. Emits a ``plot_saved`` message so the parent can notify the UI.
    """
    import os
    import time as _time
    from pathlib import Path as _Path

    def save_plot(
        caption: str | None = None,
        filename: str | None = None,
        dpi: float | None = None,
        auto_close: bool = True,
    ) -> str:
        try:
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise ImportError(
                "matplotlib is required for save_plot. "
                "Install it with: pip install matplotlib"
            ) from exc

        fig = plt.gcf()
        if not fig.get_axes():
            raise RuntimeError(
                "save_plot: no active matplotlib figure to save. "
                "Build a chart first (plt.plot / sns.histplot / ...) "
                "then call save_plot()."
            )

        # Resolve output directory: <cwd>/assets/plots/
        base = _Path(os.getcwd())
        target = base / "assets" / "plots"
        target.mkdir(parents=True, exist_ok=True)

        stem = (filename or f"plot_{int(_time.time() * 1000)}").strip()
        stem = _Path(stem).name or f"plot_{int(_time.time() * 1000)}"
        fpath = target / f"{stem}.png"

        fig.savefig(
            str(fpath),
            dpi=int(dpi) if dpi else 150,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
        )

        abs_path = str(fpath.resolve())

        # Notify the parent so it can push the image to the chat UI.
        payload: dict = {"path": abs_path}
        if caption:
            payload["caption"] = caption
        _emit({"kind": "plot_saved", **payload})

        if auto_close:
            plt.close(fig)

        # Print confirmation so the LLM agent sees the success in stdout
        # and does NOT retry the same step.
        print(f"[save_plot] ✅ Plot saved → {abs_path}")

        return abs_path

    save_plot.__name__ = "save_plot"
    save_plot.__qualname__ = "save_plot"
    return save_plot


def _build_namespace(tool_names: list[str]) -> dict[str, Any]:
    """Build the globals dict the child uses for every ``exec`` call."""
    ns: dict[str, Any] = {
        "__name__": "__main__",
        "__builtins__": builtins,
    }
    for name in tool_names:
        # save_plot is implemented locally in the subprocess so it can
        # access the child's matplotlib figures directly.
        if name == "save_plot":
            ns[name] = _make_local_save_plot()
        else:
            ns[name] = _make_tool_stub(name)
    # final_answer is always available, matching the agent loop's behaviour.
    ns["final_answer"] = _final_answer_stub
    return ns


def _run_exec(code: str, namespace: dict[str, Any]) -> None:
    """Execute a block of code inside ``namespace``.

    We emit exactly one ``done`` message on the way out, regardless of
    success, failure, or final_answer.
    """
    captured = _TeeStdout()
    real_stdout = sys.stdout
    sys.stdout = captured
    output: Any = None
    is_final_answer = False
    try:
        # Prefer eval for single-expression code so the expression value
        # becomes the CodeOutput.output — mirrors LocalPythonInterpreter.
        compiled = None
        try:
            compiled = compile(code, "<agent>", "eval")
            output = eval(compiled, namespace, namespace)
        except SyntaxError:
            exec(compile(code, "<agent>", "exec"), namespace, namespace)
    except _FinalAnswer as fa:
        output = fa.value
        is_final_answer = True
    except BaseException:  # noqa: BLE001 — we *want* to catch everything
        tb = traceback.format_exc()
        sys.stdout = real_stdout
        _emit({"kind": "done", "ok": False, "error": tb, "logs": captured.getvalue()})
        return
    finally:
        sys.stdout = real_stdout

    _emit({
        "kind": "done",
        "ok": True,
        "output": output,
        "is_final_answer": is_final_answer,
        "logs": captured.getvalue(),
    })


def main() -> None:
    # Force unbuffered stdio. We also reopen stdout in text mode with
    # line buffering just to be safe on Windows where default buffering
    # bites pip progress bars.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    # Install D3 risky-op telemetry BEFORE we accept any exec. Patches
    # os / shutil / pathlib / builtins.open so write-side-effects show
    # up in the parent's run archive.
    _install_risky_op_hooks()

    tool_names: list[str] = []
    namespace: dict[str, Any] = {}

    _emit({"kind": "ready"})

    while True:
        msg = _read_message()
        if msg is None:
            # stdin EOF — parent vanished. Exit cleanly.
            return
        if not msg:
            continue

        kind = msg.get("kind")
        if kind == "init":
            tool_names = list(msg.get("tool_names") or [])
            namespace = _build_namespace(tool_names)

            _install_pyplot_patch(namespace)

            _emit({"kind": "init_ok"})
        elif kind == "set_var":
            name = msg.get("name")
            value = msg.get("value")
            if isinstance(name, str):
                namespace[name] = value
                _emit({"kind": "set_var_ok", "name": name})
        elif kind == "exec":
            code = msg.get("code") or ""
            _run_exec(code, namespace)
        elif kind == "shutdown":
            _emit({"kind": "bye"})
            return
        else:
            _emit({"kind": "stderr", "text": f"[runner] unknown kind: {kind!r}\n"})


if __name__ == "__main__":
    main()
