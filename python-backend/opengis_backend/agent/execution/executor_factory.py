"""Factory for the subprocess-backed Python executor used by the agent loop.

Why this is its own module
--------------------------
Building the executor has two concerns that have nothing to do with
the agent loop itself:

1. Decide the subprocess' ``working_dir`` — should be the workspace root
   if one is open (so LLM-authored relative paths resolve against the
   project, and the planned git-snapshot safety net has a stable cwd).
   Fall back to the current process cwd when no workspace is attached.

2. Wire the child's stdout into the parent's logging pipeline so that
   ``print(...)`` from LLM code lands in the rotating file handler that
   Phase 1 installed. Without this, long-running scripts (pip install,
   GNNWR training) look silent to the user.

Both concerns need to know ``ToolContext`` (for the workspace path)
but nothing else — keeping them out of :mod:`open_gis_agent` lets the
agent shell stay a thin orchestrator, and lets tests pin the executor-wiring
contract independently of LLM/tool/prompt plumbing.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from opengis_backend.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Default per-exec timeout. Matches the agent.chat RPC budget — long
# enough for a reasonable pip install or a short training loop, short
# enough that a runaway exec doesn't wedge the backend forever. Callers
# can override.
DEFAULT_EXEC_TIMEOUT_SEC: float = 600.0


def _default_stdout_listener(text: str) -> None:
    """Fan child subprocess stdout into the parent's logger.

    Errors inside the listener MUST be swallowed — losing one print line
    is far less bad than crashing the executor's reader thread, which
    would orphan the running agent step.
    """
    try:
        logger.info("[child stdout] %s", text.rstrip())
    except Exception:
        pass


def resolve_working_dir(ctx: ToolContext) -> Optional[str]:
    """Pull ``workspace_path`` out of the ToolContext metadata.

    Returned value is handed straight to ``SubprocessExecutorConfig``;
    ``None`` means "inherit the parent process cwd" which is the
subprocess default.
    """
    meta = getattr(ctx, "meta", None) or {}
    workspace = meta.get("workspace_path")
    if workspace is None:
        return None
    # Caller may have stashed a pathlib.Path — normalise to str so the
    # downstream subprocess API doesn't care.
    return str(workspace)


def build_subprocess_executor(
    ctx: ToolContext,
    *,
    exec_timeout: float = DEFAULT_EXEC_TIMEOUT_SEC,
    stdout_listener: Optional[Callable[[str], None]] = None,
    risky_op_listener: Optional[Callable[[dict], None]] = None,
    plot_saved_listener: Optional[Callable[[dict], None]] = None,
):
    """Construct a ``SubprocessPythonExecutor`` bound to ``ctx``.

    Parameters
    ----------
    ctx:
        The per-run ToolContext. Only ``ctx.meta['workspace_path']`` is
        read here; nothing else is touched.
    exec_timeout:
        Seconds allotted to a single ``exec`` round-trip in the child.
        Defaults to 600s (10 min).
    stdout_listener:
        Optional callable receiving each stdout chunk from the child.
        Defaults to a logger-backed listener that forwards into the
        ``opengis_backend.agent.execution.executor_factory`` logger namespace.
    risky_op_listener:
        Optional callable receiving every ``{"kind":"risky_op",...}``
        emitted by the child (D3 telemetry). Typically bound to
        :meth:`RunArchive.record_risky_op`. ``None`` disables recording.

    Returns
    -------
    SubprocessPythonExecutor
    A *fresh* executor — not started yet. The agent loop will spawn
    the child on its first ``send_tools`` call. The caller owns the
    lifecycle and MUST call ``executor.cleanup()`` once the run is
    over, win or lose, to avoid leaking the subprocess handle.
    """
    # Late import: the executor module pulls asyncio plumbing
    # we don't want to pay for at import time of this factory.
    from opengis_backend.agent.execution.executor import (  # noqa: WPS433
        SubprocessExecutorConfig,
        SubprocessPythonExecutor,
    )

    listener = stdout_listener if stdout_listener is not None else _default_stdout_listener

    return SubprocessPythonExecutor(
        config=SubprocessExecutorConfig(
            working_dir=resolve_working_dir(ctx),
            exec_timeout=exec_timeout,
        ),
        stdout_listener=listener,
        risky_op_listener=risky_op_listener,
        plot_saved_listener=plot_saved_listener,
    )


__all__ = [
    "DEFAULT_EXEC_TIMEOUT_SEC",
    "build_subprocess_executor",
    "resolve_working_dir",
]
