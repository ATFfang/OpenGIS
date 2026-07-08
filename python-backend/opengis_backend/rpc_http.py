"""HTTP bridge for JSON-RPC — decoupled from WebSocket transport.

Provides ``dispatch_http`` which accepts a JSON-RPC 2.0 request dict,
routes it through the same ``RpcHandler`` method handlers, and returns
the JSON-RPC response dict directly (no WebSocket needed).

Usage from server.py:
    @app.post("/api/rpc")
    async def http_rpc(body: dict):
        from opengis_backend.rpc_http import dispatch_http
        return await dispatch_http(body, tool_registry)

For streaming methods (chat.*), only the initial acknowledgement is
returned; background notifications are silently discarded.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from opengis_backend.rpc_handler import RpcHandler
from opengis_backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class _DummyWS:
    """Minimal stand-in for FastAPI ``WebSocket``.

    ``RpcHandler`` calls ``send_text()`` to push JSON-RPC responses and
    notifications over the wire.  For HTTP we capture every message into
    ``self.messages`` instead of actually sending anything.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, data: str) -> None:  # noqa: D401
        self.messages.append(data)

    # FastAPI's WebSocket object exposes ``application_state`` — RpcHandler
    # doesn't touch it, but the object needs the attribute to be accepted
    # by the constructor signature.
    application_state: Any = None  # type: ignore[assignment]


async def dispatch_http(
    body: dict, tool_registry: ToolRegistry
) -> dict:
    """Route a single JSON-RPC 2.0 request and return the response dict.

    Parameters
    ----------
    body:
        Raw JSON-RPC 2.0 request: ``{"jsonrpc":"2.0","method":"...","params":{},"id":1}``
    tool_registry:
        Shared tool registry instance.

    Returns
    -------
    dict
        JSON-RPC 2.0 response: ``{"jsonrpc":"2.0","id":...,"result":...}``
        or ``{"jsonrpc":"2.0","id":...,"error":{...}}``.
    """
    # Basic validation
    if body.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32600, "message": "Invalid Request: missing jsonrpc 2.0"},
        }

    method = body.get("method")
    if not method:
        return {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32600, "message": "Invalid Request: missing method"},
        }

    ws = _DummyWS()
    handler = RpcHandler(ws, tool_registry)  # type: ignore[arg-type]

    # RpcHandler expects a raw JSON string — feed it directly.
    await handler.handle_message(json.dumps(body))

    # Return the first response (the JSON-RPC result or error).
    # Notifications (messages without "id") are ignored.
    for msg in ws.messages:
        try:
            parsed = json.loads(msg)
            if "id" in parsed:
                return parsed
        except json.JSONDecodeError:
            continue

    # Should not happen for valid requests, but handle gracefully.
    return {
        "jsonrpc": "2.0",
        "id": body.get("id"),
        "error": {"code": -32603, "message": "No response produced"},
    }
