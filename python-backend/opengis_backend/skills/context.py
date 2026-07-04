"""
SkillContext — bridges Python skills with the IPC notification channel.

A skill can be marked as "needs_context=True" in its @skill decorator.
When that skill is executed, a SkillContext instance is injected as the
first positional argument. The skill can then call ctx.notify(...) to
push commands back to the frontend (e.g. add a layer, fly the camera).

This is the Python → Frontend command bus.
"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


# Type alias for the notify callback installed by the protocol layer.
NotifyFn = Callable[[str, dict], Awaitable[None]]
RequestFn = Callable[[str, dict], Awaitable[Any]]


@dataclass
class SkillContext:
    """
    Per-execution context handed to context-aware skills.

    Fields:
        notify_fn: Async callback that ships a JSON-RPC notification
                   to the frontend (set by RpcHandler).
        conversation_id: Optional conversation/session id for routing.
        meta: Free-form bag for additional context (e.g. workspace path).
    """

    notify_fn: Optional[NotifyFn] = None
    request_fn: Optional[RequestFn] = None
    conversation_id: Optional[str] = None
    meta: dict = field(default_factory=dict)

    async def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification to the frontend (fire-and-forget)."""
        if self.notify_fn is None:
            # Dev / test mode — no IPC channel attached. Log and continue.
            print(f"[SkillContext] (no IPC) notify {method}: {params}")
            return
        await self.notify_fn(method, params or {})

    async def request(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC request to the frontend and return its result."""
        if self.request_fn is None:
            raise RuntimeError(f"No request channel attached for {method}")
        return await self.request_fn(method, params or {})


# ── ContextVar for cross-async-call propagation ──────────────────────────
# The CodeAgent runs user-generated code in a sandbox. That code calls
# skill functions synchronously. We need the skill function to be able to
# look up the current SkillContext without being passed it explicitly,
# because smolagents Tool wrappers don't naturally accept a context arg.
#
# Solution: stash the context in a ContextVar before the agent runs, and
# expose `get_current_context()` for skills that opt in via @skill(needs_context=True).

_current_context: contextvars.ContextVar[Optional[SkillContext]] = contextvars.ContextVar(
    "opengis_skill_context", default=None
)


def set_current_context(ctx: Optional[SkillContext]) -> contextvars.Token:
    """Install a SkillContext for the current async/sync stack."""
    return _current_context.set(ctx)


def reset_current_context(token: contextvars.Token) -> None:
    """Restore the previous context (paired with set_current_context)."""
    _current_context.reset(token)


def get_current_context() -> SkillContext:
    """
    Retrieve the active SkillContext.

    If no context is installed (e.g. when a skill is invoked directly
    via skill.execute outside an agent run), returns an empty context
    so notify() becomes a no-op.
    """
    ctx = _current_context.get()
    if ctx is None:
        return SkillContext()
    return ctx


def run_async_from_sync(coro: Awaitable[Any]) -> Any:
    """
    Helper for sync skill code to await a coroutine.

    The agent's sandbox runs Python code synchronously, but ctx.notify()
    is async. We need a way to drive it without forcing every skill author
    to write `async def`. This helper handles both cases:

      - If there's a running event loop in *this* thread (rare in our
        sandbox), schedule the coroutine and wait via asyncio.run_coroutine_threadsafe.
      - Otherwise, just asyncio.run() it.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Hand off to the loop's thread; block until done.
        future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
        return future.result()
    else:
        return asyncio.run(coro)  # type: ignore[arg-type]
