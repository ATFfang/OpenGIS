"""Lightweight replay-style evaluation for evolution candidates.

This is intentionally conservative. It does not execute full task replays yet;
it scores candidate quality from the evidence attached to the candidate and
records the decision separately so we can plug in a real replay engine later.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evolution_holdout import HoldoutSplitter
from .evolution_replay import ReplaySuite
from .evolution_store import EvolutionCandidate

logger = logging.getLogger(__name__)

_EVOLUTION_DIR = ".opengis/evolution"
_EVALUATIONS_FILE = "evaluations.jsonl"
_MAX_RECORDS = 500


@dataclass(frozen=True)
class ReplayEvaluation:
    id: str
    candidate_id: str
    candidate_kind: str
    score: float
    decision: str
    strengths: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        candidate_kind: str,
        score: float,
        decision: str,
        strengths: list[str] | None = None,
        risks: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ReplayEvaluation":
        return cls(
            id=uuid.uuid4().hex,
            candidate_id=candidate_id,
            candidate_kind=candidate_kind,
            score=max(0.0, min(1.0, float(score))),
            decision=decision.strip() or "review",
            strengths=[item.strip() for item in (strengths or []) if str(item).strip()],
            risks=[item.strip() for item in (risks or []) if str(item).strip()],
            metrics=metrics or {},
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "score": self.score,
            "decision": self.decision,
            "strengths": list(self.strengths),
            "risks": list(self.risks),
            "metrics": dict(self.metrics),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


class ReplayEvaluator:
    """Score a candidate and produce a promotion recommendation."""

    def __init__(
        self,
        workspace_path: str | None,
        *,
        promotion_threshold: float = 0.75,
        replay_limit: int = 20,
    ) -> None:
        self.workspace_path = workspace_path
        self.promotion_threshold = max(0.0, min(1.0, float(promotion_threshold)))
        self.replay_limit = max(1, int(replay_limit))

    @property
    def root(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / _EVOLUTION_DIR

    def evaluate(self, candidate: EvolutionCandidate, *, baseline: dict[str, Any] | None = None) -> ReplayEvaluation:
        replay_suite = ReplaySuite(self.workspace_path, replay_limit=self.replay_limit)
        replay_cases = replay_suite.build(candidate)
        replay_summary = replay_suite.summarize(replay_cases)
        holdout = HoldoutSplitter().summarize(replay_cases)
        support = {
            "bonus": min(
                0.15,
                float(replay_summary.get("replay_score") or 0.0) * 0.12
                + float(replay_summary.get("replay_case_count") or 0) * 0.01,
            ),
            "supporting_run_ids": list(replay_summary.get("supporting_run_ids") or []),
            "replay_case_count": int(replay_summary.get("replay_case_count") or 0),
            "replay_pass_count": int(replay_summary.get("replay_pass_count") or 0),
            "replay_score": float(replay_summary.get("replay_score") or 0.0),
            "holdout": holdout.to_metrics(),
        }
        score, strengths, risks, metrics = self._score(candidate, baseline=baseline, support=support)
        if holdout.regression_risk:
            risks.append("held-out regression risk")
        decision = "promote" if score >= self.promotion_threshold and not self._has_blocking_risk(risks) else "review"
        evaluation = ReplayEvaluation.create(
            candidate_id=candidate.id,
            candidate_kind=candidate.kind,
            score=score,
            decision=decision,
            strengths=strengths,
            risks=risks,
            metrics=metrics,
            metadata={
                "candidate_title": candidate.title,
                "target_component": candidate.target_component,
                "status": candidate.status,
            },
        )
        self.record(evaluation)
        return evaluation

    def record(self, evaluation: ReplayEvaluation) -> None:
        root = self.root
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / _EVALUATIONS_FILE
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evaluation.to_dict(), ensure_ascii=False, default=str) + "\n")
            self._compact(path)
        except Exception:
            logger.debug("evolution evaluation record failed", exc_info=True)

    def list(self, *, limit: int = 200) -> list[ReplayEvaluation]:
        root = self.root
        if root is None or not root.exists():
            return []
        path = root / _EVALUATIONS_FILE
        records = self._read_file(path)
        records.sort(key=lambda evaluation: (-evaluation.created_at, evaluation.id))
        return records[: max(1, limit)]

    def _score(
        self,
        candidate: EvolutionCandidate,
        *,
        baseline: dict[str, Any] | None = None,
        support: dict[str, Any] | None = None,
    ) -> tuple[float, list[str], list[str], dict[str, Any]]:
        strengths: list[str] = []
        risks: list[str] = []
        metrics: dict[str, Any] = {
            "evidence_count": len(candidate.evidence),
            "tag_count": len(candidate.tags),
            "confidence": candidate.confidence,
        }
        score = 0.2 + min(0.2, candidate.confidence * 0.2)

        if candidate.kind == "failure_repair":
            score += 0.35
            strengths.append("verified failure repair path")
            if candidate.target_component:
                score += 0.08
                strengths.append(f"target component={candidate.target_component}")
            if candidate.evidence:
                score += 0.07
                strengths.append("has failure evidence")
            if candidate.metadata.get("repair_tools"):
                score += 0.1
                strengths.append("explicit repair tools available")
            if candidate.metadata.get("verified_by_tool"):
                score += 0.05
                strengths.append("repair was verified by a later tool call")
            weakness_count = int(candidate.metadata.get("weakness_support_count") or 0)
            if weakness_count >= 2:
                score += min(0.12, weakness_count * 0.03)
                strengths.append(f"weakness cluster support={weakness_count}")
        elif candidate.kind == "recipe":
            score += 0.28
            strengths.append("reusable procedure candidate")
            tool_count = int(candidate.metadata.get("tool_count") or len(candidate.evidence))
            reuse_requested = bool(candidate.metadata.get("reuse_requested"))
            workflow_attached = bool(candidate.metadata.get("workflow"))
            weakness_count = int(candidate.metadata.get("weakness_support_count") or 0)
            metrics["tool_count"] = tool_count
            metrics["reuse_requested"] = reuse_requested
            metrics["workflow_attached"] = workflow_attached
            metrics["weakness_support_count"] = weakness_count
            if tool_count >= 3:
                score += 0.1
                strengths.append("tool sequence is non-trivial")
            if workflow_attached:
                score += 0.1
                strengths.append("workflow context attached")
            if reuse_requested:
                score += 0.06
                strengths.append("explicit reuse intent")
            if weakness_count >= 2:
                score += min(0.08, weakness_count * 0.02)
                strengths.append(f"correction cluster support={weakness_count}")
            if not reuse_requested and not workflow_attached and weakness_count < 2:
                risks.append("generic recipe lacks explicit reuse intent or repeated support")
                score -= 0.18
        elif candidate.kind == "operation_candidate":
            score += 0.3
            strengths.append("operation reuse candidate")
            op_count = int(candidate.metadata.get("operation_call_count") or 0)
            metrics["operation_call_count"] = op_count
            if op_count >= 1:
                score += 0.12
                strengths.append("operation calls observed")
            if candidate.metadata.get("operation_ids"):
                score += 0.08
                strengths.append("stable operation ids attached")
            weakness_count = int(candidate.metadata.get("weakness_support_count") or 0)
            if weakness_count >= 2:
                score += min(0.1, weakness_count * 0.025)
                strengths.append(f"operation correction cluster support={weakness_count}")
        elif candidate.kind == "weakness_cluster":
            score += 0.32
            strengths.append("correction-grounded weakness cluster")
            support_count = int(candidate.metadata.get("weakness_support_count") or 0)
            correction_count = int(candidate.metadata.get("weakness_correction_count") or 0)
            metrics["weakness_support_count"] = support_count
            metrics["weakness_correction_count"] = correction_count
            if support_count >= 2:
                score += min(0.14, support_count * 0.04)
                strengths.append(f"cross-run support={support_count}")
            if correction_count >= 2:
                score += min(0.12, correction_count * 0.03)
                strengths.append(f"repeated correction={correction_count}")
            if candidate.metadata.get("weakness_kind"):
                strengths.append(str(candidate.metadata.get("weakness_kind")))
        else:
            score += 0.15
            strengths.append("generic candidate")

        if baseline:
            score += self._baseline_bonus(candidate, baseline, strengths, risks, metrics)

        if support:
            score += float(support.get("bonus") or 0.0)
            supporting_run_ids = list(support.get("supporting_run_ids") or [])
            metrics["supporting_run_count"] = len(supporting_run_ids)
            metrics["replay_case_count"] = int(support.get("replay_case_count") or 0)
            metrics["replay_pass_count"] = int(support.get("replay_pass_count") or 0)
            metrics["replay_score"] = float(support.get("replay_score") or 0.0)
            holdout = support.get("holdout") if isinstance(support.get("holdout"), dict) else {}
            metrics.update(holdout)
            if holdout.get("held_in_count"):
                strengths.append(
                    f"held-in score={float(holdout.get('held_in_score') or 0.0):.2f}"
                )
            if holdout.get("held_out_count"):
                strengths.append(
                    f"held-out score={float(holdout.get('held_out_score') or 0.0):.2f}"
                )
            if holdout.get("regression_risk"):
                risks.append("held-out regression risk")
            if supporting_run_ids:
                metrics["supporting_run_ids"] = supporting_run_ids[:8]
                strengths.append(f"historical support from {len(supporting_run_ids)} run(s)")

        score -= self._risk_penalty(candidate, risks)
        score = max(0.0, min(1.0, score))
        return score, strengths, risks, metrics

    @staticmethod
    def _baseline_bonus(
        candidate: EvolutionCandidate,
        baseline: dict[str, Any],
        strengths: list[str],
        risks: list[str],
        metrics: dict[str, Any],
    ) -> float:
        bonus = 0.0
        if candidate.kind == "failure_repair" and baseline.get("failure_count"):
            bonus += 0.05
            strengths.append("baseline has repeated failures")
        if candidate.kind == "recipe" and baseline.get("tool_call_count"):
            tool_count = int(baseline.get("tool_call_count") or 0)
            metrics["baseline_tool_call_count"] = tool_count
            if tool_count >= 3:
                bonus += 0.05
                strengths.append("baseline uses longer tool chains")
        if candidate.kind == "operation_candidate" and baseline.get("operation_missing"):
            bonus += 0.05
            strengths.append("baseline lacks a reusable operation")
        if baseline.get("regression_risk"):
            risks.append("baseline regression risk reported")
            bonus -= 0.03
        return bonus

    @staticmethod
    def _risk_penalty(candidate: EvolutionCandidate, risks: list[str]) -> float:
        text = " ".join([
            candidate.title,
            candidate.summary,
            candidate.proposed_change,
            candidate.target_component,
            " ".join(candidate.evidence[:4]),
        ]).lower()
        penalty = 0.0
        if any(word in text for word in ("disable", "bypass", "skip verifier", "turn off", "hack")):
            penalty += 0.35
            risks.append("proposal contains bypass-like wording")
        if any(word in text for word in ("replace production", "overwrite", "force all")):
            penalty += 0.2
            risks.append("proposal looks too broad")
        return penalty

    @staticmethod
    def _has_blocking_risk(risks: list[str]) -> bool:
        return any(
            "bypass" in risk
            or "too broad" in risk
            or "held-out regression" in risk
            or "generic recipe lacks explicit reuse" in risk
            for risk in risks
        )

    @staticmethod
    def _compact(path: Path) -> None:
        if not path.exists():
            return
        try:
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            logger.debug("evolution evaluation read failed", exc_info=True)
            return
        if len(lines) <= _MAX_RECORDS:
            return
        try:
            path.write_text("\n".join(lines[-_MAX_RECORDS:]) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("evolution evaluation compact failed", exc_info=True)

    @staticmethod
    def _read_file(path: Path) -> list[ReplayEvaluation]:
        if not path.exists():
            return []
        out: list[ReplayEvaluation] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    candidate = ReplayEvaluation(
                        id=str(raw.get("id") or uuid.uuid4().hex),
                        candidate_id=str(raw.get("candidate_id") or ""),
                        candidate_kind=str(raw.get("candidate_kind") or "candidate"),
                        score=float(raw.get("score") or 0.0),
                        decision=str(raw.get("decision") or "review"),
                        strengths=[str(item) for item in raw.get("strengths") or []],
                        risks=[str(item) for item in raw.get("risks") or []],
                        metrics=raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {},
                        created_at=float(raw.get("created_at") or time.time()),
                        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
                    )
                    if candidate.candidate_id:
                        out.append(candidate)
        except Exception:
            logger.debug("evolution evaluation read failed", exc_info=True)
        return out


class PromotionGate:
    """Decision helper for whether an evaluated candidate may be promoted."""

    def __init__(
        self,
        *,
        threshold: float = 0.75,
        min_held_in: int = 1,
        min_held_out: int = 1,
        min_replay_cases: int = 2,
        min_delta: float = 0.03,
        min_held_out_delta: float = 0.0,
        require_no_holdout_regression: bool = True,
    ) -> None:
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self.min_held_in = max(1, int(min_held_in))
        self.min_held_out = max(0, int(min_held_out))
        self.min_replay_cases = max(1, int(min_replay_cases))
        self.min_delta = max(0.0, float(min_delta))
        self.min_held_out_delta = float(min_held_out_delta)
        self.require_no_holdout_regression = bool(require_no_holdout_regression)

    def should_promote(self, evaluation: ReplayEvaluation) -> bool:
        metrics = evaluation.metrics or {}
        if evaluation.decision != "promote" or evaluation.score < self.threshold:
            return False
        if int(metrics.get("replay_case_count") or 0) < self.min_replay_cases:
            return False
        if int(metrics.get("held_in_count") or 0) < self.min_held_in:
            return False
        if int(metrics.get("held_out_count") or 0) < self.min_held_out:
            return False
        if float(metrics.get("improvement_delta") or 0.0) < self.min_delta:
            return False
        held_out_count = int(metrics.get("held_out_count") or 0)
        held_out_score = float(metrics.get("held_out_score") or 0.0)
        if held_out_count > 0 and held_out_score < (0.58 + self.min_held_out_delta):
            return False
        if evaluation.candidate_kind == "recipe":
            if not (
                bool(metrics.get("reuse_requested"))
                or bool(metrics.get("workflow_attached"))
                or int(metrics.get("weakness_support_count") or 0) >= 2
            ):
                return False
        if self.require_no_holdout_regression and bool(metrics.get("regression_risk")):
            return False
        return True


__all__ = ["PromotionGate", "ReplayEvaluation", "ReplayEvaluator"]
