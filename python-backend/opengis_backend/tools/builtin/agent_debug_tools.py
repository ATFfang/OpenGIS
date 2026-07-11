"""Agent runtime debugging tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opengis_backend.agent.context.context_persistence import load_context
from opengis_backend.agent.context.request_budget import RequestBudgetManager
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool


def _workspace(ctx: ToolContext) -> Path:
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    if not workspace:
        raise RuntimeError("A workspace is required to debug agent context.")
    path = Path(str(workspace)).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"workspace_path does not exist: {workspace}")
    return path


def _compact(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    head = limit // 2
    tail = max(0, limit - head - 28)
    return compact[:head] + " ... [omitted] ... " + compact[-tail:]


def _system_section_name(content: str) -> str:
    first = (content or "").splitlines()[0] if content else ""
    if first.startswith("## "):
        return first[3:].strip()
    if first.startswith("[Conversation summary"):
        return "Conversation summary"
    return "System"


@tool(
    name="debug_agent_context",
    display_name="Debug Agent Context",
    description=(
        "Inspect the persisted conversation context and the provider-facing projected messages. "
        "Use this when debugging memory, wrong previous-request recall, loop drift, or tool failure anchors."
    ),
    category="system",
    params=[
        {"name": "conversation_id", "type": "string", "required": False, "description": "Conversation id. Defaults to the active conversation."},
        {"name": "max_messages", "type": "number", "required": False, "description": "Maximum projected messages to preview. Default 12."},
        {"name": "max_chars", "type": "number", "required": False, "description": "Maximum characters per message preview. Default 700."},
    ],
    returns="dict with success, context counts, projected system sections, recent user requests, runtime anchors, and message previews.",
    tags=["debug", "context", "memory", "agent"],
    needs_context=True,
)
def debug_agent_context(
    ctx: ToolContext,
    conversation_id: str = "",
    max_messages: int | float = 12,
    max_chars: int | float = 700,
) -> dict[str, Any]:
    workspace = _workspace(ctx)
    conv_id = str(conversation_id or ctx.conversation_id or "").strip()
    if not conv_id:
        return {
            "success": False,
            "error": "conversation_id is required when no active conversation is attached to the tool context",
        }

    context = load_context(str(workspace), conv_id)
    if context is None:
        return {
            "success": False,
            "error": f"no persisted context found for conversation_id={conv_id}",
            "context_path": str(workspace / ".opengis" / "contexts" / f"{conv_id}.json"),
        }

    projected = context.build_messages(
        "debug_agent_context: placeholder system prompt for projection inspection.",
        exclude_workflow_context=True,
    )
    budget_report = RequestBudgetManager(
        input_token_budget=getattr(context, "token_budget", 100_000),
    ).analyze(messages=projected, tools=[])
    limit_messages = max(1, min(80, int(max_messages or 12)))
    limit_chars = max(120, min(5000, int(max_chars or 700)))

    system_sections: list[dict[str, Any]] = []
    recent_user_anchor = ""
    runtime_anchor = ""
    for index, message in enumerate(projected):
        if message.get("role") != "system":
            continue
        content = str(message.get("content") or "")
        name = _system_section_name(content)
        system_sections.append({
            "index": index,
            "section": name,
            "chars": len(content),
            "preview": _compact(content, limit_chars),
        })
        if "Recent User Requests" in content:
            recent_user_anchor = content
        if "Runtime State Anchors" in content:
            runtime_anchor = content

    previews = []
    for index, message in list(enumerate(projected))[:limit_messages]:
        item: dict[str, Any] = {
            "index": index,
            "role": message.get("role"),
            "chars": len(str(message.get("content") or "")),
            "preview": _compact(str(message.get("content") or ""), limit_chars),
        }
        if message.get("role") == "tool":
            item["tool"] = message.get("name")
            item["tool_call_id"] = message.get("tool_call_id")
        if isinstance(message.get("tool_calls"), list):
            item["tool_calls"] = [
                ((call.get("function") or {}).get("name") if isinstance(call, dict) else "unknown")
                for call in message["tool_calls"][:8]
            ]
        previews.append(item)

    raw_recent_users = [
        str(message.get("content") or "")
        for message in context.messages
        if message.get("role") == "user"
        and not (isinstance(message.get("_meta"), dict) and message["_meta"].get("kind") == "tool_result")
    ][-6:]

    return {
        "success": True,
        "conversation_id": conv_id,
        "context_path": str(workspace / ".opengis" / "contexts" / f"{conv_id}.json"),
        "raw_message_count": len(context.messages),
        "summary_cutoff": context._summary_cutoff,
        "has_summary": context._summary is not None,
        "projected_message_count": len(projected),
        "request_budget": budget_report.to_dict(),
        "system_sections": system_sections,
        "recent_user_anchor": recent_user_anchor,
        "runtime_anchor": runtime_anchor,
        "raw_recent_user_messages": raw_recent_users,
        "projected_messages_preview": previews,
    }
