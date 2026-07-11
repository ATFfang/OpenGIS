"""ToolContext bridges Python tools with the IPC notification channel."""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


NotifyFn = Callable[[str, dict], Awaitable[None]]
RequestFn = Callable[[str, dict], Awaitable[Any]]


@dataclass
class ToolContext:
    """Per-execution context handed to context-aware tools."""

    notify_fn: Optional[NotifyFn] = None
    request_fn: Optional[RequestFn] = None
    conversation_id: Optional[str] = None
    meta: dict = field(default_factory=dict)

    async def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification to the frontend."""
        if self.notify_fn is None:
            print(f"[ToolContext] (no IPC) notify {method}: {params}")
            return
        await self.notify_fn(method, params or {})

    async def request(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC request to the frontend and return its result."""
        if self.request_fn is None:
            raise RuntimeError(f"No request channel attached for {method}")
        return await self.request_fn(method, params or {})


_current_context: contextvars.ContextVar[Optional[ToolContext]] = contextvars.ContextVar(
    "opengis_tool_context", default=None
)


def set_current_context(ctx: Optional[ToolContext]) -> contextvars.Token:
    """Install a ToolContext for the current async/sync stack."""
    return _current_context.set(ctx)


def reset_current_context(token: contextvars.Token) -> None:
    """Restore the previous context."""
    _current_context.reset(token)


def get_current_context() -> ToolContext:
    """Retrieve the active ToolContext, or an empty no-op context."""
    ctx = _current_context.get()
    if ctx is None:
        return ToolContext()
    return ctx


def run_async_from_sync(coro: Awaitable[Any]) -> Any:
    """Drive an async helper from sync tool code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
        return future.result()
    return asyncio.run(coro)  # type: ignore[arg-type]
