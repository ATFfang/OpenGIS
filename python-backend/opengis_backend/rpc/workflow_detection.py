"""Workflow detection helpers for chat/RPC admission."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from opengis_backend.agent.workflow.workflow_model import WorkflowDocument

logger = logging.getLogger(__name__)

FENCED_JSON_RE = re.compile(r"```(?:json|workflow)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
MAX_CONTEXT_CHARS = 20_000


@dataclass(frozen=True)
class PastedWorkflow:
    workflow: WorkflowDocument
    context: str = ""


def parse_pasted_workflow_message(message: str) -> WorkflowDocument | None:
    """Parse a directly pasted OpenGIS workflow JSON message, if present."""
    detected = detect_pasted_workflow_message(message)
    return detected.workflow if detected is not None else None


def detect_pasted_workflow_message(message: str) -> PastedWorkflow | None:
    """Detect an OpenGIS workflow JSON object inside mixed user text."""
    for raw, context in iter_workflow_json_candidates(message):
        workflow = parse_workflow_candidate(raw)
        if workflow is not None:
            return PastedWorkflow(workflow=workflow, context=normalise_context(context))
    return None


def iter_workflow_json_candidates(message: str):
    raw_message = message.strip()
    if raw_message:
        yield raw_message, ""

    for match in FENCED_JSON_RE.finditer(message):
        raw = match.group(1).strip()
        context = f"{message[:match.start()]}\n{message[match.end():]}"
        yield raw, context

    decoder = json.JSONDecoder()
    for index, char in enumerate(message):
        if char != "{":
            continue
        try:
            data, end = decoder.raw_decode(message[index:])
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        context = f"{message[:index]}\n{message[index + end:]}"
        yield json.dumps(data, ensure_ascii=False), context


def parse_workflow_candidate(raw: str) -> WorkflowDocument | None:
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not looks_like_workflow_document(data):
        return None
    try:
        return WorkflowDocument.from_json(data)
    except Exception as exc:
        logger.warning("Pasted workflow JSON looked valid but failed to parse: %s", exc)
        return None


def normalise_context(context: str) -> str:
    compact = "\n".join(line.rstrip() for line in context.splitlines()).strip()
    if len(compact) <= MAX_CONTEXT_CHARS:
        return compact
    return compact[:MAX_CONTEXT_CHARS] + "\n\n[...user context truncated...]"


def looks_like_workflow_document(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return False
    for node in nodes:
        if not isinstance(node, dict):
            return False
        if not node.get("id"):
            return False
        if not (node.get("title") or node.get("label")):
            return False
    edges = data.get("edges", [])
    return isinstance(edges, list)


def workflow_run_prompt(workflow: WorkflowDocument, user_context: str = "") -> str:
    prompt = (
        f"Execute the pasted OpenGIS workflow `{workflow.name}`.\n\n"
        "The workflow JSON was provided directly in the user message and "
        "has already been parsed by the platform. Follow the structured "
        "workflow nodes and report concise progress/results."
    )
    if user_context.strip():
        prompt += (
            "\n\nAdditional user context outside the workflow JSON:\n"
            f"{user_context.strip()}"
        )
    return prompt


__all__ = [
    "PastedWorkflow",
    "detect_pasted_workflow_message",
    "looks_like_workflow_document",
    "parse_pasted_workflow_message",
    "workflow_run_prompt",
]
