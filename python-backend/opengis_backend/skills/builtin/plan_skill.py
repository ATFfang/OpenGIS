"""
Plan / TODO management skill.

Gives the agent a lightweight, declarative task-planning tool — the same
mental model as Claude Code's ``TodoWrite`` and opencode's plan mode.

The LLM calls ``update_plan(steps=[...])`` to:
  1. Declare a plan at the start of a multi-step task.
  2. Re-call it whenever progress changes (mark a step done, move to the
     next one, add / skip / fail a step).

Design notes
------------
- **Declarative, full-replace**: every call carries the *complete* current
  plan. This is far more robust with LLMs than incremental diffs — the
  card can never drift out of sync with the model's intent. (Same design
  Claude Code uses for ``TodoWrite``.)
- **needs_context=True**: the skill runs in the parent worker thread and
  uses ``ctx.notify("rpc.ui.chat.plan_update", ...)`` to push the plan to
  the frontend — exactly the same channel ``add_layer`` / ``save_plot``
  use. No new event-core plumbing required.
- **Per-run plan id**: by default the plan is keyed by the current
  ``run_id`` so each agent run owns one plan card (stable *within* a run,
  fresh *per* run). The LLM may pass an explicit ``plan_id`` to maintain
  several plans or to continue a previous one.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Union

from opengis_backend.skills.context import SkillContext, run_async_from_sync
from opengis_backend.skills.registry import skill

logger = logging.getLogger(__name__)

# Canonical step statuses understood by the frontend PlanRow.
_CANONICAL_STATUSES = {"pending", "in_progress", "done", "skipped", "failed"}

# Map the many things an LLM might write → a canonical status.
_STATUS_ALIASES = {
    # pending
    "": "pending",
    "pending": "pending",
    "todo": "pending",
    "to-do": "pending",
    "to_do": "pending",
    "not_started": "pending",
    "notstarted": "pending",
    "not-started": "pending",
    "queued": "pending",
    "waiting": "pending",
    # in progress
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "progress": "in_progress",
    "doing": "in_progress",
    "active": "in_progress",
    "running": "in_progress",
    "started": "in_progress",
    "current": "in_progress",
    "wip": "in_progress",
    # done
    "done": "done",
    "complete": "done",
    "completed": "done",
    "finished": "done",
    "success": "done",
    "succeeded": "done",
    "ok": "done",
    # skipped
    "skipped": "skipped",
    "skip": "skipped",
    "cancelled": "skipped",
    "canceled": "skipped",
    "omitted": "skipped",
    "n/a": "skipped",
    # failed
    "failed": "failed",
    "fail": "failed",
    "error": "failed",
    "blocked": "failed",
}

# Defensive ceiling — a plan with hundreds of steps is almost certainly a
# bug or an abusive prompt, and would bloat the chat / persisted JSON.
_MAX_STEPS = 50
_MAX_TITLE_LEN = 200


def _normalize_status(raw: Any) -> str:
    if raw is None:
        return "pending"
    s = str(raw).strip().lower().replace(" ", "_")
    if s in _CANONICAL_STATUSES:
        return s
    return _STATUS_ALIASES.get(s, "pending")


def _coerce_steps(steps: Any) -> list[dict]:
    """Turn whatever the LLM passed into a clean list[dict].

    Accepts:
      - a JSON-encoded string (some models stringify args)
      - a list of strings           -> {title, status='pending'}
      - a list of dicts             -> {id?, title|content|name|step, status?, note?}
    """
    if isinstance(steps, str):
        s = steps.strip()
        if not s:
            raise ValueError("update_plan: 'steps' is empty.")
        try:
            steps = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(
                "update_plan: 'steps' must be a list of steps "
                f"(strings or dicts), not a plain string. JSON parse failed: {e}"
            ) from e

    if isinstance(steps, dict):
        # A single step passed bare — wrap it.
        steps = [steps]

    if not isinstance(steps, (list, tuple)):
        raise ValueError(
            "update_plan: 'steps' must be a list. Got "
            f"{type(steps).__name__}."
        )

    if len(steps) == 0:
        raise ValueError(
            "update_plan: 'steps' is empty. Pass at least one step, e.g. "
            "update_plan(steps=[{'title': 'Load data', 'status': 'in_progress'}])."
        )

    norm: list[dict] = []
    for idx, raw in enumerate(steps[:_MAX_STEPS], start=1):
        if isinstance(raw, str):
            title = raw.strip()
            status = "pending"
            note = ""
            step_id = f"s{idx}"
        elif isinstance(raw, dict):
            title = (
                raw.get("title")
                or raw.get("content")
                or raw.get("name")
                or raw.get("step")
                or raw.get("task")
                or ""
            )
            title = str(title).strip()
            status = _normalize_status(raw.get("status", raw.get("state")))
            note = str(raw.get("note") or raw.get("detail") or "").strip()
            step_id = str(raw.get("id") or f"s{idx}")
        else:
            # Unknown element type — stringify defensively rather than fail
            # the whole plan over one bad entry.
            title = str(raw).strip()
            status = "pending"
            note = ""
            step_id = f"s{idx}"

        if not title:
            # A step with no title is meaningless; skip it but keep going.
            continue
        if len(title) > _MAX_TITLE_LEN:
            title = title[: _MAX_TITLE_LEN - 1].rstrip() + "…"

        entry: dict = {"id": step_id, "title": title, "status": status}
        if note:
            entry["note"] = note
        norm.append(entry)

    if not norm:
        raise ValueError(
            "update_plan: no valid steps found (every step needs a non-empty title)."
        )
    return norm


def _resolve_plan_id(ctx: SkillContext, plan_id: Optional[str]) -> str:
    if plan_id:
        return str(plan_id)
    meta = getattr(ctx, "meta", None) or {}
    run_id = meta.get("run_id")
    if run_id:
        return f"plan-{run_id}"
    conv = getattr(ctx, "conversation_id", None)
    if conv:
        return f"plan-{conv}"
    return "plan-main"


def _summarize(steps: list[dict]) -> str:
    counts = {"done": 0, "in_progress": 0, "pending": 0, "skipped": 0, "failed": 0}
    for s in steps:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    parts = []
    if counts["done"]:
        parts.append(f"{counts['done']} done")
    if counts["in_progress"]:
        parts.append(f"{counts['in_progress']} in progress")
    if counts["pending"]:
        parts.append(f"{counts['pending']} pending")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped")
    if counts["failed"]:
        parts.append(f"{counts['failed']} failed")
    detail = ", ".join(parts) if parts else "no steps"
    return f"Plan updated — {len(steps)} step(s): {detail}."


@skill(
    name="update_plan",
    display_name="Update Plan / TODO",
    description=(
        "Declare or update a TODO plan for a multi-step task. Pass the FULL "
        "list of steps every time (declarative — it replaces the previous "
        "plan), so the user always sees an accurate checklist. Use it at the "
        "START of any non-trivial multi-step task to lay out the steps, then "
        "call it again after finishing each step to update statuses. Keep "
        "exactly ONE step 'in_progress' at a time. Do NOT use it for simple "
        "one-step tasks, greetings, or pure questions."
    ),
    category="orchestration",
    params=[
        {
            "name": "steps",
            "type": "any",
            "required": True,
            "description": (
                "A list describing the plan. Each item is either a string "
                "(the step title, status defaults to 'pending') or a dict "
                "{'title': str, 'status': str, 'note': str (optional)}. "
                "Valid statuses: 'pending', 'in_progress', 'done', 'skipped', "
                "'failed'. Example: "
                "[{'title': 'Load roads.shp', 'status': 'done'}, "
                "{'title': 'Buffer 500m', 'status': 'in_progress'}, "
                "{'title': 'Render to map', 'status': 'pending'}]."
            ),
        },
        {
            "name": "title",
            "type": "string",
            "required": False,
            "description": "Optional short title / goal shown above the checklist.",
        },
        {
            "name": "plan_id",
            "type": "string",
            "required": False,
            "description": (
                "Optional stable id. Omit it and the plan is keyed to the "
                "current run (one card per run). Pass a fixed id only if you "
                "deliberately want to maintain multiple plans."
            ),
        },
    ],
    returns=(
        "A short confirmation string with the step counts (e.g. 'Plan updated "
        "— 3 step(s): 1 done, 1 in progress, 1 pending.'). The plan checklist "
        "is rendered in the chat panel."
    ),
    examples=[
        "Lay out a plan before running a multi-step buffer + intersect analysis",
        "Mark the data-loading step done and start the buffering step",
    ],
    tags=["plan", "todo", "task", "checklist", "planning"],
    needs_context=True,
    group="core",
)
def update_plan(
    ctx: SkillContext,
    steps: Union[list, tuple, str, dict],
    title: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> str:
    norm_steps = _coerce_steps(steps)
    resolved_id = _resolve_plan_id(ctx, plan_id)

    meta = getattr(ctx, "meta", None) or {}
    payload: dict = {
        "plan_id": resolved_id,
        "steps": norm_steps,
    }
    if title:
        payload["title"] = str(title).strip()[:_MAX_TITLE_LEN]
    run_id = meta.get("run_id")
    if run_id:
        payload["run_id"] = run_id

    run_async_from_sync(ctx.notify("rpc.ui.chat.plan_update", payload))

    return _summarize(norm_steps)


__all__ = ["update_plan"]
