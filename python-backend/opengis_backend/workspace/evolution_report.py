"""Read-only summary for self-evolution state.

The report layer sits above candidate storage, replay materialization, and
evaluation records. It is intentionally read-only so UI and diagnostics can
inspect the evolution system without mutating live agent behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evolution_eval import ReplayEvaluator
from .evolution_asset_registry import EvolutionAssetRegistry
from .evolution_materializer import EvolutionMaterializer
from .evolution_replay import ReplaySuite
from .evolution_trace import EvolutionTraceStore
from .evolution_store import EvolutionCandidate, EvolutionStore


@dataclass(frozen=True)
class EvolutionReport:
    workspace_path: str | None
    candidate_count: int
    evaluation_count: int
    replay_case_count: int
    materialization_count: int
    candidate_kind_counts: dict[str, int] = field(default_factory=dict)
    candidate_status_counts: dict[str, int] = field(default_factory=dict)
    evaluation_decision_counts: dict[str, int] = field(default_factory=dict)
    materialization_kind_counts: dict[str, int] = field(default_factory=dict)
    asset_state_counts: dict[str, int] = field(default_factory=dict)
    trace_count: int = 0
    promoted_count: int = 0
    rejected_count: int = 0
    review_count: int = 0
    weakness_cluster_count: int = 0
    heldout_regression_count: int = 0
    average_candidate_confidence: float = 0.0
    average_evaluation_score: float = 0.0
    average_held_in_score: float = 0.0
    average_held_out_score: float = 0.0
    active_candidates: list[dict[str, Any]] = field(default_factory=list)
    recent_evaluations: list[dict[str, Any]] = field(default_factory=list)
    recent_cases: list[dict[str, Any]] = field(default_factory=list)
    recent_materializations: list[dict[str, Any]] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    recent_traces: list[dict[str, Any]] = field(default_factory=list)
    chain_health: dict[str, Any] = field(default_factory=dict)
    health_state: str = "unknown"
    health_summary: list[str] = field(default_factory=list)
    trace_trend: dict[str, Any] = field(default_factory=dict)
    efficiency_metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_path": self.workspace_path,
            "candidate_count": self.candidate_count,
            "evaluation_count": self.evaluation_count,
            "replay_case_count": self.replay_case_count,
            "materialization_count": self.materialization_count,
            "candidate_kind_counts": dict(self.candidate_kind_counts),
            "candidate_status_counts": dict(self.candidate_status_counts),
            "evaluation_decision_counts": dict(self.evaluation_decision_counts),
            "materialization_kind_counts": dict(self.materialization_kind_counts),
            "asset_state_counts": dict(self.asset_state_counts),
            "trace_count": self.trace_count,
            "promoted_count": self.promoted_count,
            "rejected_count": self.rejected_count,
            "review_count": self.review_count,
            "weakness_cluster_count": self.weakness_cluster_count,
            "heldout_regression_count": self.heldout_regression_count,
            "average_candidate_confidence": self.average_candidate_confidence,
            "average_evaluation_score": self.average_evaluation_score,
            "average_held_in_score": self.average_held_in_score,
            "average_held_out_score": self.average_held_out_score,
            "active_candidates": list(self.active_candidates),
            "recent_evaluations": list(self.recent_evaluations),
            "recent_cases": list(self.recent_cases),
            "recent_materializations": list(self.recent_materializations),
            "assets": list(self.assets),
            "recent_traces": list(self.recent_traces),
            "chain_health": dict(self.chain_health),
            "health_state": self.health_state,
            "health_summary": list(self.health_summary),
            "trace_trend": dict(self.trace_trend),
            "efficiency_metrics": dict(self.efficiency_metrics),
            "notes": list(self.notes),
        }


class EvolutionReporter:
    """Build a compact report of the current evolution state."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    def build(self, *, limit: int = 12) -> EvolutionReport:
        candidates = EvolutionStore(self.workspace_path).list(limit=max(1, limit * 4))
        evaluations = ReplayEvaluator(self.workspace_path).list(limit=max(1, limit * 4))
        cases = ReplaySuite(self.workspace_path).list_cases(limit=max(1, limit * 4))
        materializations = EvolutionMaterializer(self.workspace_path).list(limit=max(1, limit * 4))
        assets = EvolutionAssetRegistry(self.workspace_path).list(limit=max(1, limit * 4))
        traces = EvolutionTraceStore(self.workspace_path).list(limit=max(1, limit * 4))
        candidates_by_id = {candidate.id: candidate for candidate in candidates}
        materializations_by_id = {item.id: item for item in materializations}
        materializations_by_candidate: dict[str, Any] = {}
        for item in materializations:
            existing = materializations_by_candidate.get(item.candidate_id)
            if existing is None or item.created_at > existing.created_at:
                materializations_by_candidate[item.candidate_id] = item
        evaluations_by_candidate: dict[str, Any] = {}
        for evaluation in evaluations:
            existing = evaluations_by_candidate.get(evaluation.candidate_id)
            if existing is None or evaluation.created_at > existing.created_at:
                evaluations_by_candidate[evaluation.candidate_id] = evaluation

        candidate_kind_counts = _count_by(candidates, lambda candidate: candidate.kind)
        candidate_status_counts = _count_by(candidates, lambda candidate: candidate.status)
        evaluation_decision_counts = _count_by(evaluations, lambda evaluation: evaluation.decision)
        materialization_kind_counts = _count_by(materializations, lambda item: item.target_kind)
        asset_state_counts = _count_by(assets, lambda item: item.state)
        trace_status_counts = _count_by(traces, lambda trace: _trace_status(trace))

        candidate_confidence = _average([candidate.confidence for candidate in candidates])
        evaluation_score = _average([evaluation.score for evaluation in evaluations])
        held_in_score = _average([
            float(evaluation.metrics.get("held_in_score") or 0.0)
            for evaluation in evaluations
            if evaluation.metrics.get("held_in_count")
        ])
        held_out_score = _average([
            float(evaluation.metrics.get("held_out_score") or 0.0)
            for evaluation in evaluations
            if evaluation.metrics.get("held_out_count")
        ])

        promoted = candidate_status_counts.get("promoted", 0) + candidate_status_counts.get("published", 0)
        rejected = candidate_status_counts.get("rejected", 0)
        review = candidate_status_counts.get("evaluated", 0) + candidate_status_counts.get("draft", 0)
        weakness_cluster_count = candidate_kind_counts.get("weakness_cluster", 0)
        heldout_regression_count = sum(
            1 for evaluation in evaluations
            if bool(evaluation.metrics.get("regression_risk"))
        )

        active_candidates = [
            {
                "id": candidate.id,
                "kind": candidate.kind,
                "status": candidate.status,
                "title": candidate.title,
                "summary": candidate.summary,
                "confidence": candidate.confidence,
                "target_component": candidate.target_component,
                "source_run_id": candidate.source_run_id,
                "weakness_cluster_key": candidate.metadata.get("weakness_cluster_key"),
                "weakness_support_count": candidate.metadata.get("weakness_support_count"),
            }
            for candidate in candidates[:limit]
        ]
        recent_evaluations = [
            {
                "id": evaluation.id,
                "candidate_id": evaluation.candidate_id,
                "candidate_kind": evaluation.candidate_kind,
                "score": evaluation.score,
                "decision": evaluation.decision,
                "held_in_score": evaluation.metrics.get("held_in_score"),
                "held_out_score": evaluation.metrics.get("held_out_score"),
                "regression_risk": evaluation.metrics.get("regression_risk"),
                "strengths": list(evaluation.strengths[:4]),
                "risks": list(evaluation.risks[:4]),
            }
            for evaluation in evaluations[:limit]
        ]
        recent_cases = [
            {
                "id": case.id,
                "candidate_id": case.candidate_id,
                "candidate_kind": case.candidate_kind,
                "score": case.score,
                "status": case.status,
                "prompt": case.prompt,
                "expected_signal": case.expected_signal,
            }
            for case in cases[:limit]
        ]
        recent_materializations = [
            {
                "id": item.id,
                "candidate_id": item.candidate_id,
                "candidate_kind": item.candidate_kind,
                "target_kind": item.target_kind,
                "materialization_mode": item.materialization_mode,
                "asset_id": item.asset_id,
                "asset_name": item.asset_name,
                "asset_path": item.asset_path,
                "status": item.status,
            }
            for item in materializations[:limit]
        ]
        recent_traces = [
            {
                "run_id": trace.run_id,
                "status": _trace_status(trace),
                "candidate_count": trace.candidate_count,
                "weakness_cluster_count": trace.weakness_cluster_count,
                "weakness_candidate_count": trace.weakness_candidate_count,
                "evaluation_attempt_count": trace.evaluation_attempt_count,
                "evaluation_error_count": trace.evaluation_error_count,
                "promoted_count": trace.promoted_count,
                "rejected_count": trace.rejected_count,
                "materialized_count": trace.materialized_count,
                "materialization_error_count": trace.materialization_error_count,
                "stage_statuses": dict(trace.stage_statuses),
                "stage_durations_ms": dict(trace.stage_durations_ms),
                "total_duration_ms": round(sum(float(value) for value in trace.stage_durations_ms.values()), 2),
                "issues": list(trace.issues[:6]),
                "closed_loop": bool(trace.metadata.get("closed_loop")),
                "created_at": trace.created_at,
                "metadata": dict(trace.metadata),
            }
            for trace in traces[:limit]
        ]
        trace_trend = _build_trace_trend(traces[: max(1, limit)])
        registry = EvolutionAssetRegistry(self.workspace_path)
        asset_rows = []
        for item in assets[:limit]:
            candidate = candidates_by_id.get(item.source_candidate_id)
            evaluation = evaluations_by_candidate.get(item.source_candidate_id)
            materialization = materializations_by_id.get(item.source_materialization_id)
            health = registry.inspect(item)
            relation_issues: list[str] = []
            if item.source_candidate_id and candidate is None:
                relation_issues.append("source candidate missing")
            if item.source_materialization_id and materialization is None:
                relation_issues.append("source materialization missing")
            if item.source_candidate_id and evaluation is None:
                relation_issues.append("source evaluation missing")
            if relation_issues:
                health = {**health, "issues": [*health.get("issues", []), *relation_issues]}
            asset_rows.append({
                "id": item.id,
                "kind": item.kind,
                "name": item.name,
                "path": item.path,
                "state": item.state,
                "revision": item.revision,
                "source_candidate_id": item.source_candidate_id,
                "source_materialization_id": item.source_materialization_id,
                "source_run_id": item.source_run_id,
                "updated_at": item.updated_at,
                "metadata": dict(item.metadata),
                "health": health,
                "candidate": {
                    "id": candidate.id,
                    "kind": candidate.kind,
                    "status": candidate.status,
                    "title": candidate.title,
                    "summary": candidate.summary,
                    "proposed_change": candidate.proposed_change,
                    "target_component": candidate.target_component,
                    "confidence": candidate.confidence,
                    "evidence": list(candidate.evidence[:8]),
                    "tags": list(candidate.tags[:8]),
                    "metadata": dict(candidate.metadata),
                } if candidate is not None else None,
                "evaluation": {
                    "id": evaluation.id,
                    "score": evaluation.score,
                    "decision": evaluation.decision,
                    "strengths": list(evaluation.strengths[:6]),
                    "risks": list(evaluation.risks[:6]),
                    "metrics": dict(evaluation.metrics),
                    "created_at": evaluation.created_at,
                } if evaluation is not None else None,
                "materialization": {
                    "id": materialization.id,
                    "target_kind": materialization.target_kind,
                    "materialization_mode": materialization.materialization_mode,
                    "status": materialization.status,
                    "validation": dict(materialization.validation),
                    "rationale": list(materialization.rationale[:6]),
                    "created_at": materialization.created_at,
                } if materialization is not None else None,
                "history": [event.to_dict() for event in registry.history(item.id, limit=6)],
            })
        chain_health = _build_chain_health(
            candidates=candidates,
            evaluations=evaluations,
            materializations=materializations,
            assets=assets,
            traces=traces,
            trace_trend=trace_trend,
            trace_status_counts=trace_status_counts,
            materializations_by_candidate=materializations_by_candidate,
            evaluations_by_candidate=evaluations_by_candidate,
            registry=registry,
        )
        health_state, health_summary = _build_health_overview(chain_health=chain_health, trace_trend=trace_trend)
        efficiency_metrics = _build_efficiency_metrics(
            candidates=candidates,
            evaluations=evaluations,
            materializations=materializations,
            traces=traces,
            chain_health=chain_health,
            promoted_count=promoted,
            rejected_count=rejected,
            held_in_score=held_in_score,
            held_out_score=held_out_score,
            regression_count=heldout_regression_count,
        )

        notes: list[str] = []
        if not candidates:
            notes.append("No evolution candidates have been recorded yet.")
        if candidates and not evaluations:
            notes.append("Candidates exist, but none have been evaluated yet.")
        if evaluations and not cases:
            notes.append("Evaluations exist, but replay cases have not materialized yet.")
        if candidates and not materializations:
            notes.append("No promoted candidates have been materialized into reusable assets yet.")
        if chain_health.get("broken_asset_count"):
            notes.append(
                f"{chain_health['broken_asset_count']} evolution asset(s) are present but not loadable."
            )
        if health_summary:
            notes.extend([f"Health: {item}" for item in health_summary[:2] if item])

        return EvolutionReport(
            workspace_path=self.workspace_path,
            candidate_count=len(candidates),
            evaluation_count=len(evaluations),
            replay_case_count=len(cases),
            materialization_count=len(materializations),
            candidate_kind_counts=candidate_kind_counts,
            candidate_status_counts=candidate_status_counts,
            evaluation_decision_counts=evaluation_decision_counts,
            materialization_kind_counts=materialization_kind_counts,
            asset_state_counts=asset_state_counts,
            trace_count=len(traces),
            promoted_count=promoted,
            rejected_count=rejected,
            review_count=review,
            weakness_cluster_count=weakness_cluster_count,
            heldout_regression_count=heldout_regression_count,
            average_candidate_confidence=candidate_confidence,
            average_evaluation_score=evaluation_score,
            average_held_in_score=held_in_score,
            average_held_out_score=held_out_score,
            active_candidates=active_candidates,
            recent_evaluations=recent_evaluations,
            recent_cases=recent_cases,
            recent_materializations=recent_materializations,
            assets=asset_rows,
            recent_traces=recent_traces,
            chain_health=chain_health,
            health_state=health_state,
            health_summary=health_summary,
            trace_trend=trace_trend,
            efficiency_metrics=efficiency_metrics,
            notes=notes,
        )


def _count_by(items: list[Any], key_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(key_fn(item) or "").strip() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda entry: (-entry[1], entry[0])))


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _trace_status(trace: Any) -> str:
    pipeline_status = str((trace.metadata or {}).get("pipeline_status") or "").strip().lower()
    if pipeline_status == "failed":
        return "failed"
    statuses = list((trace.stage_statuses or {}).values())
    if not statuses:
        return "unknown"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "degraded" for status in statuses):
        return "degraded"
    if all(status == "skipped" for status in statuses):
        return "skipped"
    return "ok"


def _build_chain_health(
    *,
    candidates: list[Any],
    evaluations: list[Any],
    materializations: list[Any],
    assets: list[Any],
    traces: list[Any],
    trace_trend: dict[str, Any],
    trace_status_counts: dict[str, int],
    materializations_by_candidate: dict[str, Any],
    evaluations_by_candidate: dict[str, Any],
    registry: EvolutionAssetRegistry,
) -> dict[str, Any]:
    candidate_ids = {candidate.id for candidate in candidates}
    evaluated_ids = {evaluation.candidate_id for evaluation in evaluations if evaluation.candidate_id}
    materialized_ids = {item.candidate_id for item in materializations if item.candidate_id}
    linked_asset_ids = {item.source_materialization_id for item in assets if item.source_materialization_id}
    broken_assets = [item for item in assets if not registry.inspect(item).get("loadable")]
    closed_traces = sum(1 for trace in traces if bool(trace.metadata.get("closed_loop")))
    recent_status_counts = dict(trace_trend.get("status_counts") or {})
    recent_failed = int(recent_status_counts.get("failed") or 0)
    recent_degraded = int(recent_status_counts.get("degraded") or 0)
    recent_closed = int(trace_trend.get("closed_loop_count") or 0)

    return {
        "candidate_to_evaluation_coverage": _ratio(len(candidate_ids & evaluated_ids), len(candidate_ids)),
        "candidate_to_materialization_coverage": _ratio(len(candidate_ids & materialized_ids), len(candidate_ids)),
        "materialization_to_asset_coverage": _ratio(len(linked_asset_ids), len(materializations)),
        "evaluation_to_promotion_rate": _ratio(len([item for item in evaluations if item.decision == "promote"]), len(evaluations)),
        "activation_rate": _ratio(sum(1 for item in assets if item.state == "enabled"), max(1, len(materializations))),
        "closed_loop_trace_count": closed_traces,
        "trace_status_counts": trace_status_counts,
        "recent_trace_status_counts": recent_status_counts,
        "recent_trace_closed_loop_count": recent_closed,
        "recent_trace_failed_count": recent_failed,
        "recent_trace_degraded_count": recent_degraded,
        "recent_trace_closed_loop_rate": _ratio(recent_closed, len(trace_trend.get("series") or [])),
        "recent_trace_issue_count": int(trace_trend.get("issue_count") or 0),
        "recent_trace_average_duration_ms": float(trace_trend.get("average_duration_ms") or 0.0),
        "stage_failure_counts": dict(trace_trend.get("stage_failure_counts") or {}),
        "stage_skip_counts": dict(trace_trend.get("stage_skip_counts") or {}),
        "stage_average_durations_ms": dict(trace_trend.get("stage_average_durations_ms") or {}),
        "pipeline_failure_count": int(trace_status_counts.get("failed") or 0),
        "broken_asset_count": len(broken_assets),
        "orphan_candidate_count": len([item for item in candidates if item.id not in evaluated_ids and item.id not in materialized_ids]),
        "orphan_materialization_count": len([item for item in materializations if item.candidate_id and item.candidate_id not in candidate_ids]),
        "materialized_candidate_count": len(materializations_by_candidate),
        "evaluated_candidate_count": len(evaluations_by_candidate),
    }


def _build_trace_trend(traces: list[Any]) -> dict[str, Any]:
    series: list[dict[str, Any]] = []
    total_duration_values: list[float] = []
    issue_total = 0
    stage_failure_counts: dict[str, int] = {}
    stage_skip_counts: dict[str, int] = {}
    stage_duration_buckets: dict[str, list[float]] = {}
    for trace in traces:
        status = _trace_status(trace)
        total_duration_ms = round(sum(float(value) for value in (trace.stage_durations_ms or {}).values()), 2)
        total_duration_values.append(total_duration_ms)
        issue_count = len(trace.issues or [])
        issue_total += issue_count
        for stage, stage_status in (trace.stage_statuses or {}).items():
            if stage_status == "failed":
                stage_failure_counts[stage] = stage_failure_counts.get(stage, 0) + 1
            if stage_status == "skipped":
                stage_skip_counts[stage] = stage_skip_counts.get(stage, 0) + 1
        for stage, duration in (trace.stage_durations_ms or {}).items():
            stage_duration_buckets.setdefault(stage, []).append(float(duration))
        series.append({
            "run_id": trace.run_id,
            "status": status,
            "closed_loop": bool(trace.metadata.get("closed_loop")),
            "candidate_count": trace.candidate_count,
            "evaluation_error_count": trace.evaluation_error_count,
            "materialization_error_count": trace.materialization_error_count,
            "issue_count": issue_count,
            "total_duration_ms": total_duration_ms,
            "created_at": trace.created_at,
        })
    stage_average_durations_ms = {
        stage: _average(values)
        for stage, values in stage_duration_buckets.items()
    }
    status_counts = _count_by(traces, lambda trace: _trace_status(trace))
    closed_loop_count = sum(1 for trace in traces if bool(trace.metadata.get("closed_loop")))
    return {
        "series": series,
        "status_counts": status_counts,
        "closed_loop_count": closed_loop_count,
        "issue_count": issue_total,
        "average_duration_ms": _average(total_duration_values),
        "stage_failure_counts": dict(sorted(stage_failure_counts.items(), key=lambda entry: (-entry[1], entry[0]))),
        "stage_skip_counts": dict(sorted(stage_skip_counts.items(), key=lambda entry: (-entry[1], entry[0]))),
        "stage_average_durations_ms": dict(sorted(stage_average_durations_ms.items(), key=lambda entry: (-entry[1], entry[0]))),
    }


def _build_health_overview(
    *,
    chain_health: dict[str, Any],
    trace_trend: dict[str, Any],
) -> tuple[str, list[str]]:
    series = list(trace_trend.get("series") or [])
    if not series:
        return "idle", ["No evolution traces recorded yet."]

    recent_status_counts = dict(trace_trend.get("status_counts") or {})
    closed_rate = float(chain_health.get("recent_trace_closed_loop_rate") or 0.0)
    failed_count = int(recent_status_counts.get("failed") or 0)
    degraded_count = int(recent_status_counts.get("degraded") or 0)
    issue_count = int(chain_health.get("recent_trace_issue_count") or 0)
    broken_assets = int(chain_health.get("broken_asset_count") or 0)
    average_duration = float(chain_health.get("recent_trace_average_duration_ms") or 0.0)

    summary: list[str] = []
    state = "healthy"

    if failed_count > 0:
        state = "critical"
        summary.append(f"{failed_count} recent trace(s) failed.")
    elif degraded_count > 0 or issue_count > 0:
        state = "degraded"
        summary.append(f"{degraded_count} recent trace(s) degraded with {issue_count} issue(s).")
    elif closed_rate >= 0.5:
        summary.append(f"Closed-loop rate is {round(closed_rate * 100, 1)}% in the recent window.")
    else:
        state = "degraded"
        summary.append("Recent traces are mostly open; promotion is not yet closing the loop.")

    if broken_assets:
        state = "critical" if state != "idle" else state
        summary.append(f"{broken_assets} asset(s) are present but not loadable.")
    if average_duration > 0:
        summary.append(f"Average recent trace duration is {round(average_duration, 1)}ms.")

    return state, summary[:3]


def _build_efficiency_metrics(
    *,
    candidates: list[Any],
    evaluations: list[Any],
    materializations: list[Any],
    traces: list[Any],
    chain_health: dict[str, Any],
    promoted_count: int,
    rejected_count: int,
    held_in_score: float,
    held_out_score: float,
    regression_count: int,
) -> dict[str, Any]:
    candidate_count = len(candidates)
    evaluation_count = len(evaluations)
    materialization_count = len(materializations)
    trace_count = len(traces)
    closed_loop_count = int(chain_health.get("closed_loop_trace_count") or 0)

    promotion_rate = _ratio(promoted_count, max(1, evaluation_count))
    closed_loop_rate = _ratio(closed_loop_count, max(1, trace_count))
    regression_rate = _ratio(regression_count, max(1, evaluation_count))
    waste_rate = _ratio(rejected_count, max(1, promoted_count + rejected_count))
    heldout_lift = round(float(held_in_score) - float(held_out_score), 4)
    avg_time_to_promotion_ms = _average([
        max(0.0, (materialization.created_at - candidate.created_at) * 1000.0)
        for materialization in materializations
        for candidate in candidates
        if materialization.candidate_id == candidate.id
    ])

    return {
        "closed_loop_rate": closed_loop_rate,
        "promotion_rate": promotion_rate,
        "heldout_lift": heldout_lift,
        "regression_rate": regression_rate,
        "avg_time_to_promotion_ms": avg_time_to_promotion_ms,
        "candidate_yield_per_trace": _ratio(candidate_count, max(1, trace_count)),
        "materialization_yield_per_candidate": _ratio(materialization_count, max(1, candidate_count)),
        "waste_rate": waste_rate,
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


__all__ = ["EvolutionReport", "EvolutionReporter"]
