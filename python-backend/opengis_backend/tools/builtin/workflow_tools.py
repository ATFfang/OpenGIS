"""Workflow authoring tools.

These tools let the agent create reusable OpenGIS workflow documents without
hand-writing the frontend schema by itself.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opengis_backend.tools.builtin._asset_refresh import notify_asset_refresh
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool


WORKFLOW_SCHEMA_VERSION = 1
WORKFLOW_FILE_EXT = ".flow.json"


def _workspace_path(ctx: ToolContext | None) -> Path | None:
    meta = getattr(ctx, "meta", None) or {}
    raw = meta.get("workspace_path")
    if not raw:
        return None
    return Path(str(raw)).expanduser().resolve()


def _slugify_name(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", name.strip())
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = cleaned.strip(".- ")
    return cleaned or "workflow"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _normalise_ports(raw: Any, fallback_name: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        return [{"name": fallback_name, "label": fallback_name.title(), "type": "Any"}]
    ports: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            ports.append({"name": item or f"{fallback_name}_{index + 1}", "label": item or fallback_name, "type": "Any"})
        elif isinstance(item, dict):
            name = _as_string(item.get("name"), f"{fallback_name}_{index + 1}")
            ports.append({
                "name": name,
                "label": _as_string(item.get("label"), name),
                "type": _as_string(item.get("type"), "Any"),
                "description": _as_string(item.get("description"), ""),
            })
    return ports or [{"name": fallback_name, "label": fallback_name.title(), "type": "Any"}]


def _normalise_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raw = {"title": str(raw)}
        raw_id = _as_string(raw.get("id"), "")
        node_id = _slugify_name(raw_id) if raw_id else f"step_{index + 1}"
        if node_id in seen:
            node_id = f"{node_id}_{index + 1}"
        seen.add(node_id)
        result.append({
            "id": node_id,
            "title": _as_string(raw.get("title"), f"Step {index + 1}"),
            "description": _as_string(raw.get("description"), ""),
            "inputContract": _as_string(raw.get("inputContract", raw.get("input_contract")), ""),
            "outputContract": _as_string(raw.get("outputContract", raw.get("output_contract")), ""),
            "scriptPath": _as_string(raw.get("scriptPath", raw.get("script_path")), ""),
            "inputs": _normalise_ports(raw.get("inputs"), "input"),
            "outputs": _normalise_ports(raw.get("outputs"), "output"),
            "params": raw.get("params") if isinstance(raw.get("params"), dict) else {},
            "position": raw.get("position") if isinstance(raw.get("position"), dict) else {"x": 0, "y": (index + 1) * 100},
            "notes": _as_string(raw.get("notes"), "") or None,
            "hooks": raw.get("hooks") if isinstance(raw.get("hooks"), list) else [],
            "maxRetries": int(raw.get("maxRetries", raw.get("max_retries", 3)) or 3),
            "nodeType": _as_string(raw.get("nodeType", raw.get("type")), "process"),
            "_createdAt": now,
        })
    for node in result:
        node.pop("_createdAt", None)
    return result


def _normalise_edges(
    edges: list[dict[str, Any]] | None,
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    node_ids = {node["id"] for node in nodes}
    if not edges:
        return [
            {
                "id": f"edge_{i + 1}",
                "source": nodes[i]["id"],
                "sourceHandle": "output",
                "target": nodes[i + 1]["id"],
                "targetHandle": "input",
            }
            for i in range(max(0, len(nodes) - 1))
        ]

    result: list[dict[str, Any]] = []
    for index, raw in enumerate(edges):
        if not isinstance(raw, dict):
            continue
        source = _as_string(raw.get("source"), "")
        target = _as_string(raw.get("target"), "")
        if source not in node_ids or target not in node_ids:
            continue
        result.append({
            "id": _as_string(raw.get("id"), f"edge_{index + 1}"),
            "source": source,
            "sourceHandle": _as_string(raw.get("sourceHandle", raw.get("source_handle")), "output"),
            "target": target,
            "targetHandle": _as_string(raw.get("targetHandle", raw.get("target_handle")), "input"),
        })
    return result


@tool(
    name="create_workflow",
    display_name="Create Workflow",
    description=(
        "Create and save an OpenGIS workflow (.flow.json) in the current workspace. "
        "Use this when the user asks to design, create, save, or reuse a workflow. "
        "Each node can include description plus inputContract/outputContract describing "
        "what it receives from upstream and hands off downstream. If edges are omitted, "
        "nodes are connected sequentially in the order provided."
    ),
    category="orchestration",
    params=[
        {"name": "name", "type": "string", "description": "Workflow name. Used as the display name and file name."},
        {"name": "description", "type": "string", "required": False, "description": "Short description of what the workflow does."},
        {
            "name": "nodes",
            "type": "array",
            "description": (
                "Ordered workflow nodes. Each item may include id, title, description, "
                "inputContract, outputContract, params, hooks, inputs, outputs, maxRetries, nodeType."
            ),
        },
        {
            "name": "edges",
            "type": "array",
            "required": False,
            "description": "Optional edges with source, target, sourceHandle, targetHandle. Omit for sequential linking.",
        },
        {"name": "overwrite", "type": "boolean", "required": False, "description": "Overwrite an existing workflow file with the same name. Default false."},
    ],
    returns="dict with keys: success, path, workflow, node_count, edge_count, error",
    needs_context=True,
    group="workflow",
    examples=[
        (
            "create_workflow(name='poi-cleaning', nodes=["
            "{'title':'Load POI','description':'Read raw POI CSV',"
            "'outputContract':'Cleaned CSV path and row count'},"
            "{'title':'Map POI','inputContract':'Cleaned CSV path from previous step',"
            "'description':'Convert to GeoJSON and add to map'}])"
        ),
    ],
)
def create_workflow(
    ctx: ToolContext,
    name: str,
    nodes: list[dict[str, Any]],
    description: str = "",
    edges: list[dict[str, Any]] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    workspace = _workspace_path(ctx)
    if workspace is None:
        return {
            "success": False,
            "path": None,
            "workflow": None,
            "node_count": 0,
            "edge_count": 0,
            "error": "No workspace is open. Open a workspace before creating workflows.",
        }
    if not isinstance(nodes, list) or not nodes:
        return {
            "success": False,
            "path": None,
            "workflow": None,
            "node_count": 0,
            "edge_count": 0,
            "error": "create_workflow requires at least one node.",
        }

    workflow_name = name.strip() or "Workflow"
    file_stem = _slugify_name(workflow_name)
    workflows_dir = workspace / "workflows"
    path = (workflows_dir / f"{file_stem}{WORKFLOW_FILE_EXT}").resolve()
    if not str(path).startswith(str(workspace)):
        return {
            "success": False,
            "path": str(path),
            "workflow": None,
            "node_count": 0,
            "edge_count": 0,
            "error": "Resolved workflow path is outside the workspace.",
        }
    if path.exists() and not overwrite:
        return {
            "success": False,
            "path": str(path),
            "workflow": None,
            "node_count": 0,
            "edge_count": 0,
            "error": "Workflow already exists. Call create_workflow with overwrite=true to replace it.",
            "already_exists": True,
        }

    normalised_nodes = _normalise_nodes(nodes)
    normalised_edges = _normalise_edges(edges, normalised_nodes)
    now = _now_iso()
    workflow = {
        "schemaVersion": WORKFLOW_SCHEMA_VERSION,
        "name": workflow_name,
        "description": description or "",
        "createdAt": now,
        "updatedAt": now,
        "nodes": normalised_nodes,
        "edges": normalised_edges,
    }

    workflows_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    notify_asset_refresh(ctx, path, reason="create_workflow")

    return {
        "success": True,
        "path": str(path),
        "workflow": workflow,
        "node_count": len(normalised_nodes),
        "edge_count": len(normalised_edges),
        "error": None,
    }
