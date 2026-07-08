"""
Sub-agent delegation tools (Agent-as-Tool).

This implements the "Agent-as-Tool" pattern: the main agent can delegate a
self-contained sub-task to an **isolated** child agent. The child runs in a
brand-new context (its own ContextManager + its own subprocess executor),
does the messy work, and returns **only a compact summary** to the parent.

Why this exists
---------------
The value of a sub-agent is *context isolation*, NOT raw speed. A sub-task
that produces a lot of disposable intermediate tokens (scanning 50 files,
exploring an unknown dataset, reading a long log) would otherwise pollute
the main agent's single, precious context window. Running it in a throw-away
child context keeps the parent's history clean — the parent only ever sees a
one-paragraph conclusion.

Two entry points
----------------
- ``run_subagent(task)``      — one isolated child, serial (a context firewall).
- ``run_subagents(tasks=[…])``— several *independent* children in parallel
  (fan-out / gather), bounded by a thread pool.

Design notes
------------
- **Isolation**: each branch gets a fresh ``ContextManager`` and a fresh
  ``SubprocessPythonExecutor``. State never leaks between branches or up to
  the parent except via the returned summary string.
- **Reuse, not new architecture**: branches are built with the existing
  ``build_agent_loop`` factory — same loop, same executor, same tools. No new
  scheduler, no agent-to-agent protocol, no multi-track rendering.
- **Silent by default**: branches run with ``notify_fn=None`` so their
  intermediate steps don't interleave into the main chat stream.
- **Bounded & cancellable**: parallel fan-out is capped (``max_parallel``);
  a depth guard forbids recursive spawning; a cooperative cancel path
  interrupts every live branch (loop + subprocess) when the user stops the run.
- **Wiring**: the parent stashes ``_tool_registry`` and ``_llm_config`` into
  ``ctx.meta`` (see ``GISCodeAgent.run``); these tools read them back to
  build child loops. ``ctx.meta`` is copied into each child so the workspace
  path (and the deps) propagate automatically.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional, Union

from opengis_backend.tools.context import ToolContext, run_async_from_sync
from opengis_backend.tools.registry import tool

logger = logging.getLogger(__name__)

# ── Hard limits / defaults ───────────────────────────────────────────────
# Depth 0 = the main agent. A child runs at depth 1 and is NOT allowed to
# spawn further children — this is the primary safeguard against an
# exponential explosion of subprocesses.
_MAX_DEPTH = 1

# Defensive ceilings. Parallel fan-out is the expensive path (one subprocess
# per live branch), so keep the concurrency small.
_MAX_TASKS = 8
_MAX_TASK_LEN = 4000
_MAX_PARALLEL_CAP = 4
_DEFAULT_PARALLEL = 3

# A child gets a smaller step budget than the main agent — sub-tasks are
# meant to be focused. Clamped to a sane band.
_DEFAULT_SUBAGENT_MAX_STEPS = 6
_MIN_SUBAGENT_MAX_STEPS = 1
_MAX_SUBAGENT_MAX_STEPS = 20

# Each branch summary is truncated so a chatty child can't blow up the
# parent's context — defeating the whole purpose of delegation.
_RESULT_TRUNCATE = 4000


# ── Frontend "sub-agent running" affordance ──────────────────────────────
# The child agents themselves stay silent (notify_fn=None) — their messy
# intermediate steps must NOT pollute the main chat. But the *parent* can
# surface a single, content-free "a sub-agent is running" card so the user
# isn't staring at a frozen UI while a long delegation churns. This mirrors
# opencode's collapsed sub-agent indicator: we show task titles + status,
# never the child's internal reasoning/output.
#
# Wire contract (upserted by ``subagent_id`` on the frontend, exactly like
# ``rpc.ui.chat.plan_update``):
#   rpc.ui.chat.subagent_update {
#     subagent_id, status: 'running'|'done', parallel: bool,
#     tasks: [{title, status: 'running'|'done'|'failed'}],
#     ok_count?, total?, run_id?
#   }

def _notify_subagent(ctx: ToolContext, payload: dict) -> None:
    """Best-effort push of a sub-agent status card to the chat UI.

    Never raises — a notify failure must not abort the (more important)
    delegation work.
    """
    try:
        run_async_from_sync(ctx.notify("rpc.ui.chat.subagent_update", payload))
    except Exception:  # noqa: BLE001
        logger.debug("subagent notify failed (ignored)", exc_info=True)


def _task_title(task: str) -> str:
    """Short, single-line label for a task — never the full instruction."""
    line = (task or "").strip().splitlines()[0] if task and task.strip() else ""
    return line if len(line) <= 80 else line[:79] + "…"


# ── Input coercion ───────────────────────────────────────────────────────

def _coerce_task(raw: Any) -> str:
    """Normalise a single task into a clean, length-capped string."""
    if isinstance(raw, dict):
        raw = (
            raw.get("task")
            or raw.get("title")
            or raw.get("description")
            or raw.get("prompt")
            or raw.get("goal")
            or ""
        )
    text = str(raw).strip()
    if not text:
        return ""
    return text[:_MAX_TASK_LEN]


def _coerce_tasks(tasks: Any) -> list[str]:
    """Turn whatever the LLM passed into a clean list[str].

    Accepts a JSON-encoded string, a single string, a dict, a list of
    strings, or a list of dicts ({task|title|description|prompt}).
    """
    if isinstance(tasks, str):
        s = tasks.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return [_coerce_task(s)] if _coerce_task(s) else []
        tasks = parsed if isinstance(parsed, (list, tuple)) else [parsed]

    if isinstance(tasks, dict):
        tasks = [tasks]

    if not isinstance(tasks, (list, tuple)):
        return []

    out: list[str] = []
    for raw in tasks[:_MAX_TASKS]:
        ct = _coerce_task(raw)
        if ct:
            out.append(ct)
    return out


def _resolve_branch_groups(requested: Any) -> Optional[list[str]]:
    """Resolve the tool-group filter for a child loop.

    ``None`` → child sees all groups (the full toolset). A list narrows the
    child's capability surface. Recursion is blocked by the depth guard, not
    by group filtering.
    """
    if requested is None:
        return None
    if isinstance(requested, str):
        s = requested.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, (list, tuple)):
                return [str(g).strip() for g in parsed if str(g).strip()]
        except json.JSONDecodeError:
            return [g.strip() for g in s.split(",") if g.strip()]
        return [s]
    if isinstance(requested, (list, tuple)):
        groups = [str(g).strip() for g in requested if str(g).strip()]
        return groups or None
    return None


def _resolve_max_steps(raw: Any) -> int:
    try:
        steps = int(raw) if raw is not None else _DEFAULT_SUBAGENT_MAX_STEPS
    except (TypeError, ValueError):
        steps = _DEFAULT_SUBAGENT_MAX_STEPS
    return max(_MIN_SUBAGENT_MAX_STEPS, min(_MAX_SUBAGENT_MAX_STEPS, steps))


def _resolve_parallelism(raw: Any, n_tasks: int) -> int:
    try:
        p = int(raw) if raw is not None else _DEFAULT_PARALLEL
    except (TypeError, ValueError):
        p = _DEFAULT_PARALLEL
    p = max(1, min(_MAX_PARALLEL_CAP, p))
    return min(p, max(1, n_tasks))


# ── Cancellation tracker (parallel path) ─────────────────────────────────

class _BranchTracker:
    """Thread-safe registry of in-flight (loop, executor) pairs.

    Pool worker threads do NOT receive the KeyboardInterrupt that the parent
    worker thread gets on cancel. So when the parent is interrupted, it calls
    :meth:`cancel_all`, which cooperatively stops every live branch:
    ``loop.interrupt()`` (stops at the next safe point) and
    ``executor.interrupt()`` (kills the branch's subprocess so a running
    ``exec`` returns promptly).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[int, tuple] = {}
        self._cancel = threading.Event()
        self._counter = 0

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def add(self, loop: Any, executor: Any) -> int:
        with self._lock:
            key = self._counter
            self._counter += 1
            self._live[key] = (loop, executor)
        return key

    def remove(self, key: int) -> None:
        with self._lock:
            self._live.pop(key, None)

    def cancel_all(self) -> None:
        self._cancel.set()
        with self._lock:
            items = list(self._live.values())
        for loop, executor in items:
            try:
                loop.interrupt()
            except Exception:
                pass
            try:
                executor.interrupt()
            except Exception:
                pass


# ── The isolated branch primitive ────────────────────────────────────────

def _run_branch(
    parent_meta: dict,
    task: str,
    tool_groups: Optional[list[str]],
    max_steps: int,
    tracker: Optional[_BranchTracker] = None,
) -> dict:
    """Run ONE isolated sub-agent to completion. Never raises.

    Returns a result dict: ``{"task", "ok", "result"|"error"}``.
    """
    # Lazy imports: avoid any import-order coupling at tool-load time and
    # mirror the pattern build_agent_loop itself uses.
    from opengis_backend.agent.agent_factory import build_agent_loop
    from opengis_backend.agent.context_manager import ContextManager
    from opengis_backend.agent.profile import AgentProfile
    from opengis_backend.agent.session import AgentSession, SessionKind, SessionStatus, SessionStore

    registry = parent_meta.get("_tool_registry")
    llm_config = parent_meta.get("_llm_config")
    if registry is None or llm_config is None:
        return {
            "task": task,
            "ok": False,
            "error": (
                "sub-agent is not wired (missing _tool_registry / _llm_config "
                "in context meta). Do the work directly instead."
            ),
        }

    if tracker is not None and tracker.cancelled:
        return {"task": task, "ok": False, "error": "cancelled before start"}

    # Child context: fresh memory, silent notifications, depth incremented.
    depth = int(parent_meta.get("_subagent_depth", 0))
    child_meta = {**parent_meta, "_subagent_depth": depth + 1}
    # Drop the parent's per-run identity so a child can't collide with the
    # parent's plan/run bookkeeping (notifications are silenced anyway).
    child_meta.pop("run_id", None)
    child_meta.pop("script_dir", None)
    workspace = child_meta.get("workspace_path")
    parent_session = child_meta.get("_agent_session")
    child_session = AgentSession.create(
        kind=SessionKind.SUBAGENT,
        profile_name="gis-subagent",
        parent_id=getattr(parent_session, "id", None),
        run_id=parent_meta.get("run_id"),
        title=_task_title(task),
        metadata={"workspace_path": workspace},
    )
    child_meta["_agent_session"] = child_session
    if parent_session is not None:
        try:
            parent_session.add_child(child_session)
            SessionStore(workspace).upsert(parent_session)
        except Exception:
            logger.debug("sub-agent parent session update failed", exc_info=True)
    try:
        SessionStore(workspace).upsert(child_session)
    except Exception:
        logger.debug("sub-agent session create failed", exc_info=True)
    child_ctx = ToolContext(notify_fn=None, conversation_id=None, meta=child_meta)

    sub_loop, sub_executor = build_agent_loop(
        tools=registry,
        llm_config=llm_config,
        ctx=child_ctx,
        max_steps=max_steps,
        context=ContextManager(),  # isolated memory — the whole point
        tool_groups=tool_groups,
        agent_profile=AgentProfile.subagent(max_steps=max_steps, tool_groups=tool_groups),
    )

    key = tracker.add(sub_loop, sub_executor) if tracker is not None else None
    try:
        if tracker is not None and tracker.cancelled:
            sub_loop.interrupt()
        result = sub_loop.run(task)
        child_session.finish(status=SessionStatus.SUCCESS, summary=str(result)[:2000])
        try:
            SessionStore(workspace).upsert(child_session)
        except Exception:
            logger.debug("sub-agent success session update failed", exc_info=True)
        return {"task": task, "ok": True, "result": result}
    except KeyboardInterrupt:
        # Branch threads aren't directly injected, but be defensive.
        try:
            sub_loop.interrupt()
        except Exception:
            pass
        child_session.finish(status=SessionStatus.CANCELLED, summary="interrupted")
        try:
            SessionStore(workspace).upsert(child_session)
        except Exception:
            logger.debug("sub-agent cancel session update failed", exc_info=True)
        return {"task": task, "ok": False, "error": "interrupted"}
    except Exception as e:  # noqa: BLE001 — a child failure must not kill siblings
        logger.exception("sub-agent branch failed for task=%r", task[:120])
        child_session.finish(status=SessionStatus.ERROR, summary=f"{type(e).__name__}: {e}")
        try:
            SessionStore(workspace).upsert(child_session)
        except Exception:
            logger.debug("sub-agent error session update failed", exc_info=True)
        return {"task": task, "ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            sub_executor.cleanup()  # MUST run — owns a subprocess handle
        except Exception:
            logger.exception("sub-agent executor cleanup failed")
        if tracker is not None and key is not None:
            tracker.remove(key)


# ── Result formatting ────────────────────────────────────────────────────

def _truncate(text: str) -> str:
    if len(text) > _RESULT_TRUNCATE:
        return text[:_RESULT_TRUNCATE] + "\n…(truncated)"
    return text


def _format_results(results: list[Optional[dict]]) -> str:
    n = len(results)
    ok = sum(1 for r in results if r and r.get("ok"))
    header = (
        f"## Sub-agent result"
        + ("" if n == 1 else "s")
        + f" — {ok}/{n} succeeded\n"
    )
    lines = [header]
    for i, r in enumerate(results, 1):
        if not r:
            lines.append(f"### Task {i} — (no result)\n")
            continue
        status = "✅" if r.get("ok") else "❌"
        task = str(r.get("task", ""))
        task_short = task if len(task) <= 200 else task[:199] + "…"
        lines.append(f"### Task {i} {status}")
        lines.append(f"> {task_short}")
        if r.get("ok"):
            body = str(r.get("result") or "(no output)")
        else:
            body = "**Error:** " + str(r.get("error") or "unknown error")
        lines.append(_truncate(body))
        lines.append("")
    return "\n".join(lines).rstrip()


# ── Tool: single isolated sub-agent (Tier 1) ─────────────────────────────

@tool(
    name="run_subagent",
    display_name="Run Sub-agent (isolated)",
    description=(
        "Delegate ONE self-contained sub-task to an isolated child agent. The "
        "child works in a fresh, throw-away context and returns ONLY a short "
        "summary — so heavy intermediate output (file scans, dataset "
        "exploration, long-log analysis) never pollutes your main context. "
        "Use it as a 'context firewall' when a sub-task will produce a lot of "
        "disposable tokens you won't need afterward. Do NOT use it for simple "
        "single-step work, or for tasks that need tight back-and-forth with "
        "your current context (the child can't see your history)."
    ),
    category="orchestration",
    params=[
        {
            "name": "task",
            "type": "string",
            "required": True,
            "description": (
                "A complete, self-contained instruction for the child agent. "
                "Include every detail it needs (paths, parameters, expected "
                "output) because it cannot see the main conversation. Ask it to "
                "end with a concise summary of its findings/result."
            ),
        },
        {
            "name": "tool_groups",
            "type": "array",
            "required": False,
            "description": (
                "Optional list of tool groups to narrow the child's toolset "
                "(e.g. ['core']). Omit to give it the full toolset."
            ),
        },
        {
            "name": "max_steps",
            "type": "number",
            "required": False,
            "description": (
                "Optional cap on the child's code-execution steps "
                f"(default {_DEFAULT_SUBAGENT_MAX_STEPS}, max {_MAX_SUBAGENT_MAX_STEPS})."
            ),
        },
    ],
    returns=(
        "A markdown summary of the child's final answer (its intermediate "
        "steps are discarded). On failure, a structured error line."
    ),
    examples=[
        "Scan all *.shp under data/ and report which ones are not EPSG:4326",
        "Explore an unfamiliar CSV and summarise its columns, dtypes and ranges",
    ],
    tags=["subagent", "delegate", "isolation", "context", "orchestration"],
    needs_context=True,
    group="core",
)
def run_subagent(
    ctx: ToolContext,
    task: Union[str, dict],
    max_steps: Any = None,
    tool_groups: Any = None,
) -> str:
    meta = getattr(ctx, "meta", None) or {}

    depth = int(meta.get("_subagent_depth", 0))
    if depth >= _MAX_DEPTH:
        return (
            "Error: nested sub-agents are not allowed (depth limit reached). "
            "You are already a sub-agent — complete the task directly."
        )

    clean_task = _coerce_task(task)
    if not clean_task:
        return "Error: 'task' is empty — pass a non-empty instruction string."

    # Surface a content-free "running" card so the UI shows progress while the
    # isolated child churns. Upserted on the frontend by subagent_id.
    sub_id = uuid.uuid4().hex
    run_id = meta.get("run_id")
    _notify_subagent(ctx, {
        "subagent_id": sub_id,
        "status": "running",
        "parallel": False,
        "tasks": [{"title": _task_title(clean_task), "status": "running"}],
        "total": 1,
        "run_id": run_id,
    })

    # Expose a mini-tracker so cancel handler can interrupt the single branch.
    agent_ref = meta.get("_agent_ref")
    _single_tracker = _BranchTracker()
    if agent_ref is not None:
        agent_ref._active_subagent_tracker = _single_tracker

    try:
        res = _run_branch(
            parent_meta=meta,
            task=clean_task,
            tool_groups=_resolve_branch_groups(tool_groups),
            max_steps=_resolve_max_steps(max_steps),
            tracker=_single_tracker,
        )
    finally:
        if agent_ref is not None:
            agent_ref._active_subagent_tracker = None

    ok = bool(res.get("ok"))
    _notify_subagent(ctx, {
        "subagent_id": sub_id,
        "status": "done",
        "parallel": False,
        "tasks": [{"title": _task_title(clean_task), "status": "done" if ok else "failed"}],
        "ok_count": 1 if ok else 0,
        "total": 1,
        "run_id": run_id,
    })

    return _format_results([res])


# ── Tool: parallel sub-agents (Tier 2 — fan-out / gather) ────────────────

@tool(
    name="run_subagents",
    display_name="Run Sub-agents (parallel)",
    description=(
        "Delegate SEVERAL INDEPENDENT sub-tasks to isolated child agents that "
        "run in PARALLEL, then gather their summaries. Use this ONLY when the "
        "tasks are mutually independent (no task needs another's output) AND "
        "each is slow enough that parallelism pays off — e.g. running the same "
        "analysis over several separate datasets. Each child has its own "
        "context and subprocess, so tasks must NOT write to the same output "
        "files (that would race). For dependent or trivial work, do it "
        "directly or use run_subagent instead."
    ),
    category="orchestration",
    params=[
        {
            "name": "tasks",
            "type": "array",
            "required": True,
            "description": (
                "A list of self-contained instruction strings (or dicts with a "
                "'task' field), one per child. Each must be fully specified — "
                "children cannot see the main conversation or each other. "
                f"Max {_MAX_TASKS} tasks."
            ),
        },
        {
            "name": "tool_groups",
            "type": "array",
            "required": False,
            "description": (
                "Optional list of tool groups to narrow every child's toolset. "
                "Omit to give the full toolset."
            ),
        },
        {
            "name": "max_steps",
            "type": "number",
            "required": False,
            "description": (
                "Optional per-child cap on code-execution steps "
                f"(default {_DEFAULT_SUBAGENT_MAX_STEPS}, max {_MAX_SUBAGENT_MAX_STEPS})."
            ),
        },
        {
            "name": "max_parallel",
            "type": "number",
            "required": False,
            "description": (
                "Optional max number of children running at once "
                f"(default {_DEFAULT_PARALLEL}, hard cap {_MAX_PARALLEL_CAP}). "
                "Each running child holds one subprocess, so keep this small."
            ),
        },
    ],
    returns=(
        "A markdown report aggregating every child's summary, with a "
        "'k/n succeeded' header. Failures are reported per-task and do NOT "
        "abort the others (partial success is preserved)."
    ),
    examples=[
        "Run a buffer+intersect analysis independently on three city datasets",
        "Validate the CRS of five separate shapefiles concurrently",
    ],
    tags=["subagent", "parallel", "fan-out", "delegate", "orchestration"],
    needs_context=True,
    group="core",
)
def run_subagents(
    ctx: ToolContext,
    tasks: Any,
    max_steps: Any = None,
    max_parallel: Any = None,
    tool_groups: Any = None,
) -> str:
    meta = getattr(ctx, "meta", None) or {}

    depth = int(meta.get("_subagent_depth", 0))
    if depth >= _MAX_DEPTH:
        return (
            "Error: nested sub-agents are not allowed (depth limit reached). "
            "You are already a sub-agent — complete the tasks directly."
        )

    task_list = _coerce_tasks(tasks)
    if not task_list:
        return (
            "Error: no valid tasks provided. Pass a non-empty list of "
            "instruction strings, e.g. run_subagents(tasks=['…', '…'])."
        )

    groups = _resolve_branch_groups(tool_groups)
    steps = _resolve_max_steps(max_steps)

    n = len(task_list)
    sub_id = uuid.uuid4().hex
    run_id = meta.get("run_id")
    titles = [_task_title(t) for t in task_list]

    def _emit(task_statuses: list[str], status: str) -> None:
        """Push the current fan-out state to the chat UI (best-effort)."""
        ok_count = sum(1 for s in task_statuses if s == "done")
        _notify_subagent(ctx, {
            "subagent_id": sub_id,
            "status": status,
            "parallel": n > 1,
            "tasks": [
                {"title": titles[i], "status": task_statuses[i]}
                for i in range(n)
            ],
            "ok_count": ok_count,
            "total": n,
            "run_id": run_id,
        })

    task_statuses = ["running"] * n
    _emit(task_statuses, "running")

    # One task → no thread-pool overhead, just run it inline.
    if n == 1:
        res = _run_branch(meta, task_list[0], groups, steps)
        task_statuses[0] = "done" if res.get("ok") else "failed"
        _emit(task_statuses, "done")
        return _format_results([res])

    workers = _resolve_parallelism(max_parallel, n)
    tracker = _BranchTracker()
    results: list[Optional[dict]] = [None] * n

    # Expose tracker on the parent agent so the cancel handler can
    # interrupt all branches when the user presses Stop.
    agent_ref = meta.get("_agent_ref")
    if agent_ref is not None:
        agent_ref._active_subagent_tracker = tracker

    logger.info("run_subagents: %d tasks, %d workers", n, workers)

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="subagent") as pool:
            futures = {
                pool.submit(_run_branch, meta, task, groups, steps, tracker): idx
                for idx, task in enumerate(task_list)
            }
            try:
                for fut in as_completed(futures):
                    idx = futures[fut]
                    results[idx] = fut.result()
                    task_statuses[idx] = "done" if (results[idx] or {}).get("ok") else "failed"
                    # Stream incremental progress so the card lights up task-by-task.
                    _emit(task_statuses, "running")
            except KeyboardInterrupt:
                # Parent run was cancelled — cooperatively stop every live branch
                # (loops + subprocesses), let the pool drain, then propagate so the
                # parent loop/runner also unwinds.
                logger.info("run_subagents: cancelled — interrupting %d branches", n)
                tracker.cancel_all()
                raise
    finally:
        # Clear tracker reference so cancel handler doesn't hold stale state.
        if agent_ref is not None:
            agent_ref._active_subagent_tracker = None

    _emit(task_statuses, "done")
    return _format_results(results)


__all__ = ["run_subagent", "run_subagents"]
