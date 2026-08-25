"""Post-run evolution pipeline.

This pipeline sits above candidate extraction, replay evaluation, and
materialization. It keeps the orchestration out of the context store so the
store only triggers the pipeline instead of knowing the full evolution stack.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from opengis_backend.runs.archive import RunArchive

from .evolution_eval import PromotionGate, ReplayEvaluator
from .evolution_extractor import EvolutionExtractor
from .evolution_materializer import EvolutionMaterializer
from .evolution_mining import WeaknessCluster, WeaknessMiner
from .evolution_trace import EvolutionRunTrace, EvolutionTraceStore
from .evolution_store import EvolutionStore
from .evolution_store import EvolutionCandidate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvolutionRunResult:
    candidate_count: int = 0
    weakness_cluster_count: int = 0
    weakness_candidate_count: int = 0
    evaluation_count: int = 0
    evaluation_error_count: int = 0
    promoted_count: int = 0
    rejected_count: int = 0
    materialized_count: int = 0
    materialization_error_count: int = 0
    issue_count: int = 0


class EvolutionPipeline:
    """Orchestrate post-run evolution without touching live loop state."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    def process_run(
        self,
        *,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
        workflow: dict[str, Any] | None = None,
    ) -> EvolutionRunResult:
        if not self.workspace_path:
            return EvolutionRunResult()

        evolution_store = EvolutionStore(self.workspace_path)
        trace = EvolutionRunTrace(
            run_id=run_archive.run_id,
            workspace_path=self.workspace_path,
            metadata={
                "workflow_name": workflow.get("name") if isinstance(workflow, dict) else None,
                "workflow_id": workflow.get("id") if isinstance(workflow, dict) else None,
            },
        )
        stage_statuses: dict[str, str] = {}
        stage_durations: dict[str, float] = {}
        issues: list[str] = []

        candidates: list[EvolutionCandidate] = []
        weakness_clusters: list[WeaknessCluster] = []
        weakness_candidates: list[EvolutionCandidate] = []
        pipeline_status = "ok"
        materialization_duration_ms = 0.0
        evaluation_count = 0
        evaluation_error_count = 0
        promoted_count = 0
        rejected_count = 0
        materialized_count = 0
        materialization_error_count = 0
        candidate_ids: list[str] = []

        try:
            extract_started = time.perf_counter()
            try:
                candidates = EvolutionExtractor(self.workspace_path).extract_run(
                    user_message=user_message,
                    final_answer=final_answer,
                    run_archive=run_archive,
                    workflow=workflow,
                )
                stage_statuses["extract"] = "ok"
            except Exception as exc:
                stage_statuses["extract"] = "failed"
                issues.append(f"extract failed: {exc}")
            finally:
                stage_durations["extract_ms"] = round((time.perf_counter() - extract_started) * 1000.0, 2)

            weakness_started = time.perf_counter()
            try:
                weakness_clusters = WeaknessMiner(self.workspace_path).mine_for_run(run_archive)
                stage_statuses["mine_weakness"] = "ok"
            except Exception as exc:
                stage_statuses["mine_weakness"] = "failed"
                issues.append(f"weakness mining failed: {exc}")
            finally:
                stage_durations["mine_weakness_ms"] = round((time.perf_counter() - weakness_started) * 1000.0, 2)

            weakness_candidates_started = time.perf_counter()
            try:
                weakness_candidates = self._weakness_candidates(
                    user_message=user_message,
                    run_archive=run_archive,
                    clusters=weakness_clusters,
                    evolution_store=evolution_store,
                )
                evolution_store.add_many(weakness_candidates)
                stage_statuses["weakness_candidates"] = "ok"
            except Exception as exc:
                stage_statuses["weakness_candidates"] = "failed"
                issues.append(f"weakness candidate generation failed: {exc}")
                weakness_candidates = []
            finally:
                stage_durations["weakness_candidates_ms"] = round((time.perf_counter() - weakness_candidates_started) * 1000.0, 2)

            candidates = self._attach_weakness_support(
                candidates + weakness_candidates,
                clusters=weakness_clusters,
                evolution_store=evolution_store,
            )
            evaluator = ReplayEvaluator(self.workspace_path)
            gate = PromotionGate()
            materializer = EvolutionMaterializer(self.workspace_path)
            candidate_ids = [candidate.id for candidate in candidates]

            evaluation_started = time.perf_counter()
            for candidate in candidates:
                try:
                    evaluation = evaluator.evaluate(candidate)
                    evaluation_count += 1
                    if gate.should_promote(evaluation):
                        status = "promoted"
                        promoted_count += 1
                        materialized_started = time.perf_counter()
                        try:
                            materialization = materializer.materialize(
                                candidate,
                                run_archive=run_archive,
                                force=True,
                            )
                        except Exception as exc:
                            materialization = None
                            materialization_error_count += 1
                            issues.append(f"materialization failed for {candidate.id[:8]}: {exc}")
                        finally:
                            materialization_duration_ms += (time.perf_counter() - materialized_started) * 1000.0
                        if materializer.is_publishable(materialization):
                            status = "published"
                            materialized_count += 1
                        elif materialization is not None:
                            materialization_error_count += 1
                            issues.append(
                                f"materialization rejected for {candidate.id[:8]}: "
                                f"{materialization.target_kind}/{materialization.asset_id} "
                                f"status={materialization.status}"
                            )
                    elif evaluation.score < 0.45:
                        status = "rejected"
                        rejected_count += 1
                        materialization = None
                    else:
                        status = "evaluated"
                        materialization = None

                    evolution_store.update(
                        candidate.id,
                        status=status,
                        metadata_patch={
                            "last_evaluation_id": evaluation.id,
                            "last_score": evaluation.score,
                            "last_decision": evaluation.decision,
                            "materialized_id": materialization.id if materialization is not None else None,
                            "materialized_kind": materialization.target_kind if materialization is not None else None,
                            "materialized_mode": materialization.materialization_mode if materialization is not None else None,
                            "materialized_asset_id": materialization.asset_id if materialization is not None else None,
                            "materialized_asset_path": materialization.asset_path if materialization is not None else None,
                            "materialized_status": materialization.status if materialization is not None else None,
                        },
                    )
                except Exception as exc:
                    evaluation_error_count += 1
                    issues.append(f"evaluation failed for {candidate.id[:8]}: {exc}")

            stage_durations["evaluate_ms"] = round((time.perf_counter() - evaluation_started) * 1000.0, 2)
            stage_durations["materialize_ms"] = round(materialization_duration_ms, 2)
            stage_statuses["evaluate"] = "degraded" if evaluation_error_count else ("ok" if candidates else "skipped")
            stage_statuses["materialize"] = "degraded" if materialization_error_count else ("ok" if materialized_count else "skipped")
        except Exception as exc:
            pipeline_status = "failed"
            issues.append(f"pipeline failed: {exc}")
            logger.exception("Evolution pipeline failed for run=%s", run_archive.run_id)
        finally:
            trace.stage_statuses = stage_statuses
            trace.stage_durations_ms = stage_durations
            trace.candidate_count = len(candidates)
            trace.weakness_cluster_count = len(weakness_clusters)
            trace.weakness_candidate_count = len(weakness_candidates)
            trace.evaluation_attempt_count = len(candidates)
            trace.evaluation_error_count = evaluation_error_count
            trace.promoted_count = promoted_count
            trace.rejected_count = rejected_count
            trace.materialized_count = materialized_count
            trace.materialization_error_count = materialization_error_count
            trace.candidate_ids = candidate_ids
            trace.issues = issues
            trace.metadata = {
                **trace.metadata,
                "closed_loop": bool(materialized_count),
                "pipeline_status": pipeline_status,
                "issue_count": len(issues),
                "last_issue": issues[-1] if issues else "",
            }
            EvolutionTraceStore(self.workspace_path).record(trace)

        return EvolutionRunResult(
            candidate_count=len(candidates),
            weakness_cluster_count=len(weakness_clusters),
            weakness_candidate_count=len(weakness_candidates),
            evaluation_count=evaluation_count,
            evaluation_error_count=evaluation_error_count,
            promoted_count=promoted_count,
            rejected_count=rejected_count,
            materialized_count=materialized_count,
            materialization_error_count=materialization_error_count,
            issue_count=len(issues),
        )

    def _weakness_candidates(
        self,
        *,
        user_message: str,
        run_archive: RunArchive,
        clusters: list[WeaknessCluster],
        evolution_store: EvolutionStore,
    ) -> list[EvolutionCandidate]:
        out: list[EvolutionCandidate] = []
        existing_by_cluster_key = {
            str(candidate.metadata.get("weakness_cluster_key") or ""): candidate
            for candidate in evolution_store.list(kinds=["weakness_cluster"], limit=1000)
            if str(candidate.metadata.get("weakness_cluster_key") or "")
        }
        for cluster in clusters:
            if cluster.support_count < 2 and cluster.correction_count < 2:
                continue
            existing = existing_by_cluster_key.get(cluster.key)
            if existing is not None:
                metadata = {**existing.metadata, **cluster.to_metadata()}
                patched = replace(
                    existing,
                    confidence=min(0.98, max(existing.confidence, cluster.score)),
                    metadata=metadata,
                )
                evolution_store.update(existing.id, confidence=patched.confidence, metadata_patch=metadata)
                out.append(patched)
                continue
            title = f"Weakness cluster: {cluster.target_component or cluster.kind}"
            summary = (
                f"Repeated {cluster.kind} pattern on {cluster.target_component or 'agent behavior'} "
                f"with {cluster.support_count} supporting run(s)."
            )
            out.append(
                EvolutionCandidate.create(
                    kind="weakness_cluster",
                    scope="harness",
                    title=title,
                    summary=summary,
                    proposed_change=(
                        "Promote a targeted harness instruction/toolpack adjustment only for this "
                        "cluster; reject if held-out replay shows unrelated regression."
                    ),
                    target_component=cluster.target_component or "harness",
                    evidence=[
                        f"Current task: {user_message.strip()[:240]}",
                        *cluster.evidence[:6],
                    ],
                    tags=["weakness", "correction", cluster.kind, cluster.target_component, cluster.target_id],
                    source_run_id=run_archive.run_id,
                    confidence=min(0.95, max(0.7, cluster.score)),
                    metadata=cluster.to_metadata(),
                )
            )
        return out

    def _attach_weakness_support(
        self,
        candidates: list[EvolutionCandidate],
        *,
        clusters: list[WeaknessCluster],
        evolution_store: EvolutionStore,
    ) -> list[EvolutionCandidate]:
        if not candidates or not clusters:
            return candidates
        updated: list[EvolutionCandidate] = []
        for candidate in candidates:
            matches = [cluster for cluster in clusters if self._cluster_matches_candidate(cluster, candidate)]
            if not matches:
                updated.append(candidate)
                continue
            best = matches[0]
            metadata = dict(candidate.metadata)
            metadata.update(best.to_metadata())
            if len(matches) > 1:
                metadata["matched_weakness_clusters"] = [cluster.key for cluster in matches[:8]]
            patched = replace(
                candidate,
                confidence=min(0.98, max(candidate.confidence, best.score)),
                metadata=metadata,
            )
            evolution_store.update(candidate.id, confidence=patched.confidence, metadata_patch=metadata)
            updated.append(patched)
        return updated

    @staticmethod
    def _cluster_matches_candidate(cluster: WeaknessCluster, candidate: EvolutionCandidate) -> bool:
        if candidate.kind == "weakness_cluster":
            return candidate.metadata.get("weakness_cluster_key") == cluster.key
        target = (candidate.target_component or "").lower()
        component = (cluster.target_component or "").lower()
        if target and component and target == component:
            return True
        target_id = cluster.target_id.lower()
        if target_id:
            operation_ids = [str(item).lower() for item in candidate.metadata.get("operation_ids") or []]
            if target_id in operation_ids:
                return True
            if target_id and target_id in " ".join([candidate.title, candidate.summary, candidate.proposed_change]).lower():
                return True
        tool_names = [str(item).lower() for item in candidate.metadata.get("tool_names") or []]
        return bool(component and component in tool_names)


__all__ = ["EvolutionPipeline", "EvolutionRunResult"]
