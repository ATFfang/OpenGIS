"""Workflow output persistence, compact summaries, and plan projection."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from opengis_backend.agent.workflow.workflow_model import WorkflowNode

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(
    r"(/[\w./\-]+\.(?:geojson|shp|gpkg|csv|json|tif|tiff|png|jpg|pdf|md))"
)
_COUNT_RE = re.compile(
    r"(?:要素|记录|features?|rows?|count|数量|总计|共)\D*?(\d[\d,]+)",
    re.IGNORECASE,
)


def write_step_output(
    *,
    node: WorkflowNode,
    step_index: int,
    full_output: str,
    workspace: str,
) -> str | None:
    """Persist full workflow step output and return its path."""
    try:
        steps_dir = Path(workspace) / ".opengis" / "workflow_steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        path = steps_dir / f"step{step_index}_{node.id}.md"
        path.write_text(
            "\n".join([
                f"# Step {step_index}: {node.title}\n",
                "## Full Output\n",
                full_output,
            ]),
            encoding="utf-8",
        )
        logger.debug("Step output written to %s (%d chars)", path, len(full_output))
        return str(path)
    except Exception as exc:
        logger.warning("Failed to write step output: %s", exc)
        return None


def summarize_step_output(
    *,
    node: WorkflowNode,
    step_index: int,
    full_output: str,
    file_path: str | None,
) -> str:
    """Extract a compact handoff summary from full step output."""
    lines = full_output.strip().split("\n")
    paths = list(set(_PATH_RE.findall(full_output)))
    numbers = _COUNT_RE.findall(full_output)[:5]

    parts = [f"Step {step_index}: {node.title}"]
    if node.output_contract:
        parts.append(f"交付契约: {node.output_contract}")
    if paths:
        parts.append("产出:\n" + "\n".join(f"  - {path}" for path in paths[:8]))
    if numbers:
        parts.append(f"关键数据: {', '.join(numbers[:5])}")

    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) > 10 and not stripped.startswith("#"):
            preview = stripped[:150] + ("..." if len(stripped) > 150 else "")
            parts.append(f"摘要: {preview}")
            break

    if file_path:
        parts.append(f"详情: {file_path}")
    return "\n".join(parts)


def build_workflow_plan_payload(
    *,
    plan_id: str,
    title: str,
    execution_order: list[WorkflowNode],
    node_outputs: dict[str, str],
) -> dict:
    """Build the compact plan_update payload for workflow progress UI."""
    completed_count = sum(1 for node in execution_order if node.id in node_outputs)
    steps = []
    for index, node in enumerate(execution_order, 1):
        if node.id in node_outputs:
            output = node_outputs[node.id]
            if output.startswith("(Step '") and "failed" in output:
                status = "failed"
            elif output.startswith("(Workflow interrupted"):
                status = "skipped"
            else:
                status = "done"
        elif index == completed_count + 1:
            status = "in_progress"
        else:
            status = "pending"

        steps.append({
            "id": node.id,
            "title": f"{index}. {node.title}",
            "status": status,
        })

    return {
        "plan_id": plan_id,
        "steps": steps,
        "title": title,
    }


__all__ = ["build_workflow_plan_payload", "summarize_step_output", "write_step_output"]
