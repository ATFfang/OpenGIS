"""Held-in / held-out replay split for evolution evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field

from .evolution_replay import ReplayCase


@dataclass(frozen=True)
class HoldoutSummary:
    held_in_count: int = 0
    held_out_count: int = 0
    held_in_score: float = 0.0
    held_out_score: float = 0.0
    improvement_delta: float = 0.0
    regression_risk: bool = False
    held_in_run_ids: list[str] = field(default_factory=list)
    held_out_run_ids: list[str] = field(default_factory=list)

    def to_metrics(self) -> dict[str, object]:
        return {
            "held_in_count": self.held_in_count,
            "held_out_count": self.held_out_count,
            "held_in_score": self.held_in_score,
            "held_out_score": self.held_out_score,
            "improvement_delta": self.improvement_delta,
            "regression_risk": self.regression_risk,
            "held_in_run_ids": list(self.held_in_run_ids[:12]),
            "held_out_run_ids": list(self.held_out_run_ids[:12]),
        }


class HoldoutSplitter:
    """Build a deterministic held-out slice from replay cases."""

    def __init__(self, *, baseline_score: float = 0.58, heldout_fraction: float = 0.34) -> None:
        self.baseline_score = max(0.0, min(1.0, float(baseline_score)))
        self.heldout_fraction = max(0.1, min(0.5, float(heldout_fraction)))

    def summarize(self, cases: list[ReplayCase]) -> HoldoutSummary:
        unique = _dedupe_by_run(cases)
        if not unique:
            return HoldoutSummary()
        if len(unique) == 1:
            only = unique[0]
            score = round(float(only.score), 4)
            return HoldoutSummary(
                held_in_count=1,
                held_out_count=0,
                held_in_score=score,
                held_out_score=0.0,
                improvement_delta=round(max(0.0, score - self.baseline_score), 4),
                regression_risk=False,
                held_in_run_ids=[only.source_run_id],
            )

        ordered = sorted(unique, key=lambda case: (case.source_run_id, case.id))
        held_out_count = max(1, int(round(len(ordered) * self.heldout_fraction)))
        held_out = ordered[-held_out_count:]
        held_in = ordered[:-held_out_count] or ordered[:1]

        held_in_score = _avg(held_in)
        held_out_score = _avg(held_out)
        return HoldoutSummary(
            held_in_count=len(held_in),
            held_out_count=len(held_out),
            held_in_score=held_in_score,
            held_out_score=held_out_score,
            improvement_delta=round(max(0.0, held_in_score - self.baseline_score), 4),
            regression_risk=held_out_score < max(0.5, held_in_score - 0.25),
            held_in_run_ids=[case.source_run_id for case in held_in if case.source_run_id],
            held_out_run_ids=[case.source_run_id for case in held_out if case.source_run_id],
        )


def _dedupe_by_run(cases: list[ReplayCase]) -> list[ReplayCase]:
    by_run: dict[str, ReplayCase] = {}
    anonymous: list[ReplayCase] = []
    for case in cases:
        if not case.source_run_id:
            anonymous.append(case)
            continue
        previous = by_run.get(case.source_run_id)
        if previous is None or case.score > previous.score:
            by_run[case.source_run_id] = case
    return list(by_run.values()) + anonymous


def _avg(cases: list[ReplayCase]) -> float:
    if not cases:
        return 0.0
    return round(sum(float(case.score) for case in cases) / len(cases), 4)


__all__ = ["HoldoutSplitter", "HoldoutSummary"]
