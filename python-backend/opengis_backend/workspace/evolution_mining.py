"""Weakness mining for self-evolution.

The strongest signal in an interactive agent is often not a raw failure. It is
the correction path that follows: edit the same operation, patch the same file,
or repeatedly fix the same tool contract. This module clusters those patterns
from archived runs and feeds them into evolution candidates.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from opengis_backend.agent.context.failure_memory import failure_signature
from opengis_backend.runs.archive import RunArchive


CORRECTION_TOOLS = {
    "edit_operation",
    "edit_file",
    "write_file",
    "apply_patch",
    "create_operation",
    "promote_script_to_operation",
}


@dataclass(frozen=True)
class WeaknessSignal:
    id: str
    kind: str
    cluster_key: str
    source_run_id: str
    target_component: str = ""
    target_id: str = ""
    summary: str = ""
    evidence: list[str] = field(default_factory=list)
    score: float = 0.0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeaknessCluster:
    key: str
    kind: str
    target_component: str = ""
    target_id: str = ""
    support_count: int = 0
    correction_count: int = 0
    source_run_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_metadata(self) -> dict[str, Any]:
        return {
            "weakness_cluster_key": self.key,
            "weakness_kind": self.kind,
            "weakness_support_count": self.support_count,
            "weakness_correction_count": self.correction_count,
            "weakness_source_run_ids": list(self.source_run_ids[:12]),
            "weakness_score": self.score,
        }


class WeaknessMiner:
    """Cluster correction-grounded weaknesses from run archives."""

    def __init__(self, workspace_path: str | None, *, run_limit: int = 80) -> None:
        self.workspace_path = workspace_path
        self.run_limit = max(1, int(run_limit))

    def mine_for_run(self, run_archive: RunArchive) -> list[WeaknessCluster]:
        if not self.workspace_path:
            return []
        signals = self._signals_from_archive(run_archive)
        for run_index in RunArchive.list_runs(self.workspace_path, limit=self.run_limit):
            if run_index.run_id == run_archive.run_id:
                continue
            archive = RunArchive.load(self.workspace_path, run_index.run_id)
            if archive is None:
                continue
            signals.extend(self._signals_from_archive(archive))
        clusters = self._cluster(signals)
        touched = {signal.cluster_key for signal in self._signals_from_archive(run_archive)}
        return [cluster for cluster in clusters if cluster.key in touched]

    def _signals_from_archive(self, archive: RunArchive) -> list[WeaknessSignal]:
        calls = archive.read_tool_calls()
        signals: list[WeaknessSignal] = []
        for index, call in enumerate(calls):
            signature = failure_signature(call)
            if signature is not None:
                repair_tools = _correction_tools_between(calls[index + 1 :])
                cluster_key = "|".join([
                    "failure_repair",
                    signature.tool_name,
                    signature.error_type,
                    signature.target_kind,
                    signature.target_id or "-",
                ])
                signals.append(
                    WeaknessSignal(
                        id=uuid.uuid4().hex,
                        kind="failure_repair",
                        cluster_key=cluster_key,
                        source_run_id=archive.run_id,
                        target_component=signature.tool_name,
                        target_id=signature.target_id,
                        summary=f"{signature.tool_name} {signature.error_type}",
                        evidence=[
                            f"failure={signature.normalized_error}",
                            "corrections=" + " -> ".join(repair_tools) if repair_tools else "corrections=none",
                        ],
                        score=0.65 + min(0.2, 0.04 * len(repair_tools)),
                        metadata={
                            "fingerprint": signature.fingerprint,
                            "repair_tools": repair_tools,
                        },
                    )
                )

            name = str(call.get("name") or "")
            if name not in CORRECTION_TOOLS:
                continue
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            target_kind, target_id = _target_from_args(args)
            if not target_id:
                target_id = _compact(json.dumps(args, ensure_ascii=False, default=str), 80)
            cluster_key = "|".join(["correction", name, target_kind or "target", target_id or "-"])
            signals.append(
                WeaknessSignal(
                    id=uuid.uuid4().hex,
                    kind="correction",
                    cluster_key=cluster_key,
                    source_run_id=archive.run_id,
                    target_component=name,
                    target_id=target_id,
                    summary=f"{name} corrected {target_kind or 'target'} {target_id}".strip(),
                    evidence=[_tool_evidence(call)],
                    score=0.72,
                    metadata={"target_kind": target_kind},
                )
            )
        return signals

    @staticmethod
    def _cluster(signals: list[WeaknessSignal]) -> list[WeaknessCluster]:
        grouped: dict[str, list[WeaknessSignal]] = {}
        for signal in signals:
            grouped.setdefault(signal.cluster_key, []).append(signal)

        clusters: list[WeaknessCluster] = []
        for key, items in grouped.items():
            source_run_ids = _dedupe([item.source_run_id for item in items if item.source_run_id])
            correction_count = sum(1 for item in items if item.kind == "correction" or item.metadata.get("repair_tools"))
            support_count = len(source_run_ids)
            score = min(1.0, 0.45 + 0.14 * support_count + 0.05 * correction_count)
            clusters.append(
                WeaknessCluster(
                    key=key,
                    kind=items[0].kind,
                    target_component=items[0].target_component,
                    target_id=items[0].target_id,
                    support_count=support_count,
                    correction_count=correction_count,
                    source_run_ids=source_run_ids,
                    evidence=_dedupe([e for item in items for e in item.evidence])[:8],
                    score=score,
                )
            )
        clusters.sort(key=lambda item: (-item.score, -item.support_count, item.key))
        return clusters


def _correction_tools_between(calls: list[dict[str, Any]]) -> list[str]:
    tools: list[str] = []
    for call in calls[:8]:
        name = str(call.get("name") or "")
        if name in CORRECTION_TOOLS and name not in tools:
            tools.append(name)
    return tools


def _target_from_args(args: dict[str, Any]) -> tuple[str, str]:
    for kind, keys in (
        ("operation", ("operation_id", "id")),
        ("file", ("file_path", "path")),
        ("layer", ("layer_id", "name")),
        ("workflow", ("workflow_name", "workflow_id")),
    ):
        for key in keys:
            value = str(args.get(key) or "").strip()
            if value:
                return kind, value
    return "", ""


def _tool_evidence(call: dict[str, Any]) -> str:
    name = str(call.get("name") or "")
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    return f"{name}: {_compact(json.dumps(args, ensure_ascii=False, default=str), 220)}"


def _compact(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 18)] + " ... [omitted]"


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


__all__ = ["WeaknessCluster", "WeaknessMiner", "WeaknessSignal"]
