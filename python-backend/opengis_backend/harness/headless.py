"""Headless runner for OpenGIS agent tasks.

This module is intentionally below both the desktop WebSocket handler and the
CLI. It owns the reusable, non-UI execution path: build tools, create a
ToolContext, drive OpenGISAgent, then return the generated RunArchive location.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from opengis_backend.agent.engine import OpenGISAgent
from opengis_backend.agent.governance.permission import PermissionAction, PermissionDecision
from opengis_backend.agent.governance.profile import AgentProfileStore
from opengis_backend.agent.telemetry.events import AgentEvent
from opengis_backend.harness.metrics import RunMetrics, append_jsonl, collect_run_metrics
from opengis_backend.runs import RunArchive
from opengis_backend.runtime.config import settings
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None]


@dataclass
class HeadlessRunConfig:
    """Configuration for one non-interactive agent run."""

    workspace_path: str | None
    prompt: str
    conversation_id: str | None = None
    protocol: str = settings.llm_protocol
    model: str = settings.llm_model
    api_key: str = settings.llm_api_key
    base_url: str = settings.llm_base_url
    profile_name: str | None = None
    tool_groups: list[str] | None = None
    user_instructions: str | None = None
    feature_flags: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    approval_policy: str = "deny"


@dataclass
class HeadlessRunResult:
    """Result returned by HeadlessAgentRunner."""

    run_id: str | None
    status: str
    run_dir: str | None
    final_answer: str | None
    error: str | None
    duration_ms: int
    metrics: RunMetrics | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metrics"] = self.metrics.to_dict() if self.metrics else None
        return payload


class HeadlessAgentRunner:
    """Run OpenGISAgent without a WebSocket renderer."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.tool_registry = tool_registry or ToolRegistry()
        self.event_sink = event_sink
        self._loaded = False

    async def ensure_tools_loaded(self) -> None:
        if self._loaded:
            return
        await self.tool_registry.discover_and_load()
        self._loaded = True

    async def run(self, config: HeadlessRunConfig) -> HeadlessRunResult:
        await self.ensure_tools_loaded()
        started = time.monotonic()
        prompt = inject_attachments(config.prompt, config.attachments)
        ctx = ToolContext(
            notify_fn=self._notify,
            request_fn=self._request,
            conversation_id=config.conversation_id or f"headless-{uuid.uuid4().hex}",
            meta={"headless": True},
        )
        if config.workspace_path:
            ctx.meta["workspace_path"] = str(Path(config.workspace_path).expanduser().resolve())
        if config.feature_flags:
            ctx.meta["feature_flags"] = dict(config.feature_flags)
        if config.profile_name:
            ctx.meta["agent_profile"] = AgentProfileStore(config.workspace_path).get(
                str(config.profile_name)
            )
        ctx.meta["_approval_callback"] = self._approval_callback(config.approval_policy)

        agent = OpenGISAgent(
            self.tool_registry,
            protocol=config.protocol,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )

        final_answer: str | None = None
        error: str | None = None
        status = "success"
        try:
            async for event in agent.run(
                prompt,
                context=ctx,
                active_tool_groups=config.tool_groups,
                user_instructions=config.user_instructions,
            ):
                self._emit_event(event)
                if event.type.value == "stream_delta":
                    if isinstance(event.data, str):
                        final_answer = event.data
                    elif isinstance(event.data, dict) and isinstance(event.data.get("content"), str):
                        final_answer = event.data["content"]
                elif event.type.value == "error":
                    status = "error"
                    error = _stringify_event_data(event.data)
        except asyncio.CancelledError:
            status = "cancelled"
            error = "cancelled"
            raise
        except Exception as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("headless agent run failed")

        duration_ms = int((time.monotonic() - started) * 1000)
        run_id = (ctx.meta or {}).get("run_id")
        archive = RunArchive.load(config.workspace_path, str(run_id)) if run_id else None
        metrics = collect_run_metrics(archive)
        if metrics is not None:
            status = metrics.status
        return HeadlessRunResult(
            run_id=str(run_id) if run_id else None,
            status=status,
            run_dir=str(archive.run_dir) if archive else None,
            final_answer=final_answer,
            error=error,
            duration_ms=duration_ms,
            metrics=metrics,
        )

    async def _notify(self, method: str, params: dict | None = None) -> None:
        payload = {"kind": "notification", "method": method, "params": params or {}}
        if self.event_sink is not None:
            self.event_sink(payload)

    async def _request(self, method: str, params: dict | None = None) -> Any:
        payload = {"kind": "request", "method": method, "params": params or {}}
        if self.event_sink is not None:
            self.event_sink(payload)
        return _headless_request_response(method, params or {})

    def _emit_event(self, event: AgentEvent) -> None:
        if self.event_sink is None:
            return
        self.event_sink(
            {
                "kind": "agent_event",
                "type": event.type.value,
                "data": event.data,
            }
        )

    @staticmethod
    def _approval_callback(policy: str):
        normalized = str(policy or "deny").lower()

        def callback(
            tool_name: str,
            args: dict[str, Any],
            decision: PermissionDecision,
        ) -> PermissionDecision:
            if normalized == "allow":
                return PermissionDecision(
                    action=PermissionAction.ALLOW,
                    reason="Allowed by headless --approval-policy allow.",
                    rule=f"headless:{decision.rule or tool_name}",
                )
            return PermissionDecision(
                action=PermissionAction.DENY,
                reason=(
                    f"Headless run denied approval request for {tool_name}. "
                    "Re-run with --approval-policy allow if this is expected."
                ),
                rule=f"headless:{decision.rule or tool_name}",
            )

        return callback


def make_jsonl_event_sink(path: str | Path | None) -> EventSink | None:
    if path is None:
        return None
    output_path = Path(path).expanduser().resolve()

    def sink(payload: dict[str, Any]) -> None:
        append_jsonl(output_path, {"ts": time.time(), **payload})

    return sink


def inject_attachments(message: str, attachments: list[dict[str, Any]]) -> str:
    """Inject text attachment content into a prompt using the desktop contract."""
    if not attachments:
        return message
    max_file_chars = 8000
    max_total_chars = 24000
    binary_ext = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".shp",
        ".dbf",
        ".shx",
        ".gpkg",
        ".sqlite",
        ".parquet",
        ".feather",
        ".zip",
    }
    parts = [message]
    total_chars = len(message)
    for attachment in attachments:
        path = str(attachment.get("path") or "")
        if not path:
            continue
        name = str(attachment.get("name") or Path(path).name or "attachment")
        if total_chars >= max_total_chars:
            parts.append(
                f"\n\n---\nAttachment skipped: `{name}` "
                f"(total attachment size limit reached: {max_total_chars} chars)."
            )
            continue
        file_path = Path(path).expanduser()
        ext = file_path.suffix.lower()
        if ext in binary_ext:
            entry = (
                f"\n\n---\nAttached binary file: `{name}`\n"
                f"Path: `{file_path}`\n"
                "Binary content is not embedded; use the path in tools or code."
            )
            parts.append(entry)
            total_chars += len(entry)
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            entry = f"\n\n---\nAttachment unavailable: `{name}`\nPath: `{file_path}`\nError: {exc}"
            parts.append(entry)
            total_chars += len(entry)
            continue
        truncated = len(content) > max_file_chars
        content = content[:max_file_chars]
        entry = (
            f"\n\n---\nAttached file: `{name}`\n"
            f"Path: `{file_path}`\n"
            "```text\n"
            f"{content}\n"
            "```"
        )
        if truncated:
            entry += f"\n(Content truncated to {max_file_chars} chars.)"
        parts.append(entry)
        total_chars += len(entry)
    return "".join(parts)


def _headless_request_response(method: str, params: dict[str, Any]) -> Any:
    """Return deterministic responses for UI-bound request calls."""
    if method == "rpc.ui.map.list_layers":
        return {"layers": [], "headless": True}
    if method == "rpc.ui.map.get_state":
        return {"layers": [], "layer_count": 0, "headless": True}
    if method == "rpc.ui.map.get_layer":
        return {
            "success": False,
            "error": f"Headless renderer has no layer state for {params.get('layer_id')}.",
            "headless": True,
        }
    if method == "rpc.ui.map.query_features":
        return {
            "success": False,
            "error": "Headless renderer cannot query in-memory frontend map layers.",
            "features": [],
            "headless": True,
        }
    if method.startswith("rpc.ui.map.") or method.startswith("rpc.ui.layout."):
        return {
            "success": False,
            "error": f"{method} requires the OpenGIS renderer; unavailable in headless CLI.",
            "headless": True,
        }
    raise RuntimeError(f"No headless request adapter for {method}")


def _stringify_event_data(data: Any) -> str:
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return str(data)
