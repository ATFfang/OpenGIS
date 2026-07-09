"""Workflow document model, DAG utilities, and node prompt construction."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowNode:
    """A single node in the workflow DAG."""

    id: str
    title: str
    description: str = ""
    input_contract: str = ""
    output_contract: str = ""
    node_type: str = "process"
    config: dict = field(default_factory=dict)
    max_retries: int = 3
    hooks: list[dict] = field(default_factory=list)


@dataclass
class WorkflowEdge:
    """A directed edge in the workflow DAG."""

    source: str
    target: str
    label: str = ""


@dataclass
class WorkflowDocument:
    """Parsed workflow document from a .flow.json file."""

    name: str
    description: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: str | dict) -> "WorkflowDocument":
        """Parse a workflow document from JSON string or dict."""
        data = json.loads(raw) if isinstance(raw, str) else raw

        nodes: list[WorkflowNode] = []
        for item in data.get("nodes", []):
            hooks_raw = item.get("hooks", [])
            nodes.append(
                WorkflowNode(
                    id=item.get("id", ""),
                    title=item.get("title", item.get("label", "Untitled")),
                    description=item.get("description", ""),
                    input_contract=item.get("inputContract", item.get("input_contract", "")),
                    output_contract=item.get("outputContract", item.get("output_contract", "")),
                    node_type=item.get("nodeType", item.get("type", "process")),
                    config=item.get("params", item.get("config", {})),
                    max_retries=item.get("maxRetries", item.get("max_retries", 3)),
                    hooks=hooks_raw if isinstance(hooks_raw, list) else [],
                )
            )

        edges = [
            WorkflowEdge(
                source=item.get("source", ""),
                target=item.get("target", ""),
                label=item.get("label", ""),
            )
            for item in data.get("edges", [])
        ]

        return cls(
            name=data.get("name", "Untitled Workflow"),
            description=data.get("description", ""),
            nodes=nodes,
            edges=edges,
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the workflow document to a stable JSON-compatible shape."""
        return {
            "name": self.name,
            "description": self.description,
            "nodes": [
                {
                    "id": node.id,
                    "title": node.title,
                    "description": node.description,
                    "inputContract": node.input_contract,
                    "outputContract": node.output_contract,
                    "type": node.node_type,
                    "config": dict(node.config),
                    "max_retries": node.max_retries,
                    "hooks": list(node.hooks),
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "label": edge.label,
                }
                for edge in self.edges
            ],
            "metadata": dict(self.metadata),
        }


def topological_sort(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[WorkflowNode]:
    """Topologically sort workflow nodes using Kahn's algorithm."""
    node_map = {node.id: node for node in nodes}
    in_degree: dict[str, int] = {node.id: 0 for node in nodes}
    adjacency: dict[str, list[str]] = {node.id: [] for node in nodes}

    for edge in edges:
        if edge.source in adjacency and edge.target in in_degree:
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

    queue = deque(node_id for node_id, degree in in_degree.items() if degree == 0)
    result: list[WorkflowNode] = []
    while queue:
        node_id = queue.popleft()
        result.append(node_map[node_id])
        for neighbor in adjacency[node_id]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(nodes):
        raise ValueError(
            f"Workflow DAG has a cycle! Sorted {len(result)} of {len(nodes)} nodes."
        )
    return result


def get_predecessors(node_id: str, edges: list[WorkflowEdge]) -> list[str]:
    """Get direct predecessor node IDs for a given node."""
    return [edge.source for edge in edges if edge.target == node_id]


def build_step_prompt(
    node: WorkflowNode,
    step_index: int,
    total_steps: int,
    user_intent: str,
    predecessor_outputs: dict[str, str],
    workflow_name: str = "",
) -> str:
    """Build a focused prompt for a single workflow node."""
    parts: list[str] = [f"## Workflow Step {step_index}/{total_steps}: {node.title}\n"]

    if workflow_name:
        parts.append(f"**Workflow**: {workflow_name}\n")
    parts.append(f"**User's original request**: {user_intent}\n")
    if node.description:
        parts.append(f"**Task for this step**: {node.description}\n")

    if node.input_contract or node.output_contract:
        parts.append("## Node Communication Contract (HIGH PRIORITY)")
        if node.input_contract:
            parts.append("**Receives from upstream**: " f"{node.input_contract}\n")
        if node.output_contract:
            parts.append("**Must hand off downstream**: " f"{node.output_contract}\n")
        parts.append(
            "Treat this contract as stronger than generic task wording. "
            "Use upstream results according to the receive contract. "
            "Before finishing this step, make sure the handoff contract is "
            "satisfied and explicitly stated in your final plain-text step "
            "summary with exact file paths, layer ids, field names, metrics, "
            "or other identifiers needed by downstream nodes.\n"
        )

    if predecessor_outputs:
        parts.append("**Results from previous steps**:")
        for pred_id, output in predecessor_outputs.items():
            display = output[:2000] + "..." if len(output) > 2000 else output
            parts.append(f"- Step `{pred_id}`: {display}")
        parts.append("")

    parts.append(
        "**Instructions**: Accomplish this step by calling function tools. "
        "Use `execute_code` for Python work; do not output Markdown code "
        "blocks as executable work. Use the results from previous steps as "
        "needed. Keep calling tools until the step is FULLY complete "
        "(files saved, layers displayed, etc.). When this step is done, "
        "reply with plain text to signal completion and summarize what was "
        "accomplished. If a downstream handoff contract is defined, your "
        "final text MUST list the concrete handoff values.\n"
    )
    parts.append(
        "**Important**: Save all output files to the workspace directory. "
        "If the step description mentions displaying on the map, call "
        "`add_layer(...)` and `zoom_to_layer(...)`. Do NOT stop after just "
        "loading data — complete the ENTIRE task described above.\n"
    )

    if node.config:
        parts.append(f"**Step configuration**: {json.dumps(node.config, ensure_ascii=False)}\n")

    return "\n".join(parts)


__all__ = [
    "WorkflowDocument",
    "WorkflowEdge",
    "WorkflowNode",
    "build_step_prompt",
    "get_predecessors",
    "topological_sort",
]
