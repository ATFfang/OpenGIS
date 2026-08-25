"""Materialize promoted evolution candidates into reusable capabilities.

The evolution pipeline should not stop at scoring. Once a candidate is
promoted, this layer decides how to turn it into a concrete asset and writes
that asset to disk. Planning is kept separate from execution so future asset
types can be added without tangling the evaluator or the agent loop.
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

from opengis_backend.operations import OperationError, OperationStore
from opengis_backend.runs.archive import RunArchive

from .evolution_asset_registry import EvolutionAssetRegistry
from .evolution_store import EvolutionCandidate, EvolutionStore

logger = logging.getLogger(__name__)

_EVOLUTION_DIR = ".opengis/evolution"
_MATERIALIZATIONS_FILE = "materializations.jsonl"
_MAX_RECORDS = 500
_PLATFORM_TOOL_NAMES = {
    "add_layer",
    "add_raster",
    "bash",
    "copy_operation_to_workspace",
    "create_operation",
    "datasource_call",
    "edit_file",
    "edit_operation",
    "execute_code",
    "file_exists",
    "fly_to",
    "get_layer",
    "get_map_state",
    "glob",
    "grep",
    "list_directory",
    "list_layers",
    "list_operations",
    "list_scripts",
    "load_skill",
    "osm_call",
    "promote_script_to_operation",
    "query_features",
    "read_file",
    "read_script",
    "remove_layer",
    "run_operation",
    "run_script_file",
    "set_categorized_style",
    "set_raster_style",
    "update_layer_style",
    "validate_operation",
    "zoom_to_layer",
}


@dataclass(frozen=True)
class EvolutionPromotionPlan:
    candidate_id: str
    candidate_kind: str
    target_kind: str
    materialization_mode: str
    asset_id: str
    asset_name: str
    source_run_id: str
    source_code: str = ""
    source_script_path: str = ""
    summary: str = ""
    rationale: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvolutionMaterialization:
    id: str
    candidate_id: str
    candidate_kind: str
    target_kind: str
    materialization_mode: str
    asset_id: str
    asset_name: str
    asset_path: str
    status: str
    source_run_id: str
    summary: str = ""
    rationale: list[str] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "target_kind": self.target_kind,
            "materialization_mode": self.materialization_mode,
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_path": self.asset_path,
            "status": self.status,
            "source_run_id": self.source_run_id,
            "summary": self.summary,
            "rationale": list(self.rationale),
            "validation": dict(self.validation),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvolutionMaterialization":
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            candidate_id=str(raw.get("candidate_id") or ""),
            candidate_kind=str(raw.get("candidate_kind") or "candidate"),
            target_kind=str(raw.get("target_kind") or "skill"),
            materialization_mode=str(raw.get("materialization_mode") or "skill"),
            asset_id=str(raw.get("asset_id") or ""),
            asset_name=str(raw.get("asset_name") or ""),
            asset_path=str(raw.get("asset_path") or ""),
            status=str(raw.get("status") or "draft"),
            source_run_id=str(raw.get("source_run_id") or ""),
            summary=str(raw.get("summary") or ""),
            rationale=[str(item) for item in raw.get("rationale") or [] if str(item).strip()],
            validation=raw.get("validation") if isinstance(raw.get("validation"), dict) else {},
            created_at=float(raw.get("created_at") or time.time()),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        )


class EvolutionPlanner:
    """Pure planning logic for evolution candidate promotion."""

    def plan(
        self,
        candidate: EvolutionCandidate,
        *,
        run_archive: RunArchive | None = None,
    ) -> EvolutionPromotionPlan:
        operation_ids = [
            str(item).strip()
            for item in (candidate.metadata.get("operation_ids") or [])
            if str(item).strip() and not _is_platform_tool_name(str(item))
        ]
        target_operation_id = operation_ids[0] if operation_ids else _slug(candidate.target_component or candidate.title or candidate.kind)
        source_code, source_script_path = _pick_source_step(run_archive)
        summary = candidate.summary or candidate.proposed_change or candidate.title
        rationale: list[str] = []

        if candidate.kind == "failure_repair" and _is_platform_tool_name(candidate.target_component or target_operation_id):
            rationale.append("platform tool repair is materialized as guidance, not as a new operation")
            return self._skill_plan(
                candidate,
                asset_id=_slug(f"{candidate.target_component or target_operation_id}-repair-guidance"),
                source_code=source_code,
                source_script_path=source_script_path,
                source_run_id=candidate.source_run_id or (run_archive.run_id if run_archive else ""),
                summary=summary,
                rationale=rationale,
            )

        if candidate.kind == "operation_candidate":
            if operation_ids:
                rationale.append("candidate references a reusable operation id")
                return EvolutionPromotionPlan(
                    candidate_id=candidate.id,
                    candidate_kind=candidate.kind,
                    target_kind="operation",
                    materialization_mode="copy_operation",
                    asset_id=target_operation_id,
                    asset_name=candidate.title or target_operation_id,
                    source_run_id=candidate.source_run_id or (run_archive.run_id if run_archive else ""),
                    summary=summary,
                    rationale=rationale,
                    metadata={
                        "operation_ids": operation_ids,
                        "target_component": candidate.target_component,
                    },
                )
            rationale.append("no reusable operation id was found; fallback to skill")

        if candidate.kind in {"prompt_patch", "tool_policy", "weakness_cluster"} or candidate.scope == "harness":
            target_kind = "tool_policy" if _looks_like_tool_policy_candidate(candidate) else "prompt_patch"
            rationale.append(f"harness-scoped candidate is materialized as gated {target_kind}")
            return EvolutionPromotionPlan(
                candidate_id=candidate.id,
                candidate_kind=candidate.kind,
                target_kind=target_kind,
                materialization_mode=target_kind,
                asset_id=_slug(candidate.title or candidate.target_component or candidate.kind),
                asset_name=candidate.title or candidate.target_component or candidate.kind,
                source_run_id=candidate.source_run_id or (run_archive.run_id if run_archive else ""),
                source_code=source_code,
                source_script_path=source_script_path,
                summary=summary,
                rationale=rationale,
                metadata={
                    "target_component": candidate.target_component,
                    "tags": list(candidate.tags[:8]),
                    "weakness_cluster_key": candidate.metadata.get("weakness_cluster_key"),
                    "weakness_kind": candidate.metadata.get("weakness_kind"),
                },
            )

        if candidate.kind == "failure_repair":
            rationale.append("failure repair is safer as a reusable instruction bundle")

        return self._skill_plan(
            candidate,
            asset_id=_slug(candidate.title or candidate.target_component or candidate.kind),
            source_code=source_code,
            source_script_path=source_script_path,
            source_run_id=candidate.source_run_id or (run_archive.run_id if run_archive else ""),
            summary=summary,
            rationale=rationale or ["candidate is better expressed as a reusable instruction bundle"],
        )

    @staticmethod
    def _skill_plan(
        candidate: EvolutionCandidate,
        *,
        asset_id: str,
        source_code: str,
        source_script_path: str,
        source_run_id: str,
        summary: str,
        rationale: list[str],
    ) -> EvolutionPromotionPlan:
        return EvolutionPromotionPlan(
            candidate_id=candidate.id,
            candidate_kind=candidate.kind,
            target_kind="skill",
            materialization_mode="skill",
            asset_id=asset_id,
            asset_name=candidate.title or candidate.target_component or candidate.kind,
            source_run_id=source_run_id,
            source_code=source_code,
            source_script_path=source_script_path,
            summary=summary,
            rationale=rationale,
            metadata={
                "target_component": candidate.target_component,
                "tags": list(candidate.tags[:8]),
            },
        )


class EvolutionMaterializer:
    """Write promoted candidates back as operations or skills."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path
        self.planner = EvolutionPlanner()

    @property
    def root(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / _EVOLUTION_DIR

    def materialize(
        self,
        candidate: EvolutionCandidate,
        *,
        run_archive: RunArchive | None = None,
        force: bool = False,
    ) -> EvolutionMaterialization | None:
        if not self.workspace_path:
            return None
        if not force and candidate.status != "promoted":
            return None
        plan = self.planner.plan(candidate, run_archive=run_archive)
        existing = self.get_by_candidate(candidate.id)
        if existing is not None:
            return existing
        existing_asset = self.get_by_asset(plan.target_kind, plan.asset_id)
        if existing_asset is not None and self.is_publishable(existing_asset):
            EvolutionStore(self.workspace_path).update(
                candidate.id,
                status="published",
                metadata_patch={
                    "materialized_id": existing_asset.id,
                    "materialized_kind": existing_asset.target_kind,
                    "materialized_mode": existing_asset.materialization_mode,
                    "materialized_asset_id": existing_asset.asset_id,
                    "materialized_asset_path": existing_asset.asset_path,
                    "materialized_status": existing_asset.status,
                    "materialized_publishable": True,
                    "materialized_reused_existing": True,
                },
            )
            return existing_asset

        if plan.target_kind == "operation":
            materialization = self._materialize_operation(candidate, plan, run_archive=run_archive)
        elif plan.target_kind == "prompt_patch":
            materialization = self._materialize_prompt_patch(candidate, plan)
        elif plan.target_kind == "tool_policy":
            materialization = self._materialize_tool_policy(candidate, plan)
        else:
            materialization = self._materialize_skill(candidate, plan)
        if materialization is None:
            return None

        self.record(materialization)
        publishable = self.is_publishable(materialization)
        if publishable:
            EvolutionAssetRegistry(self.workspace_path).upsert_from_materialization(
                materialization,
                default_state="draft",
            )
        EvolutionStore(self.workspace_path).update(
            candidate.id,
            status="published" if publishable else "materialization_failed",
            metadata_patch={
                "materialized_id": materialization.id,
                "materialized_kind": materialization.target_kind,
                "materialized_mode": materialization.materialization_mode,
                "materialized_asset_id": materialization.asset_id,
                "materialized_asset_path": materialization.asset_path,
                "materialized_status": materialization.status,
                "materialized_publishable": publishable,
            },
        )
        return materialization

    @staticmethod
    def is_publishable(materialization: EvolutionMaterialization | None) -> bool:
        if materialization is None:
            return False
        if materialization.status not in {"published", "validated"}:
            return False
        validation = materialization.validation if isinstance(materialization.validation, dict) else {}
        return bool(validation.get("ok"))

    def list(self, *, limit: int = 200) -> list[EvolutionMaterialization]:
        root = self.root
        if root is None or not root.exists():
            return []
        path = root / _MATERIALIZATIONS_FILE
        records = self._read_file(path)
        records.sort(key=lambda item: (-item.created_at, item.id))
        return records[: max(1, limit)]

    def get_by_candidate(self, candidate_id: str) -> EvolutionMaterialization | None:
        if not candidate_id:
            return None
        for record in self.list(limit=1000):
            if record.candidate_id == candidate_id:
                return record
        return None

    def get_by_asset(self, target_kind: str, asset_id: str) -> EvolutionMaterialization | None:
        kind = str(target_kind or "").strip()
        asset = str(asset_id or "").strip()
        if not kind or not asset:
            return None
        for record in self.list(limit=1000):
            if record.target_kind == kind and record.asset_id == asset:
                return record
        return None

    def record(self, materialization: EvolutionMaterialization) -> None:
        root = self.root
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / _MATERIALIZATIONS_FILE
            existing = self._read_file(path)
            by_id = {item.id: item for item in existing}
            by_id[materialization.id] = materialization
            rows = sorted(by_id.values(), key=lambda item: (-item.created_at, item.id))
            if len(rows) > _MAX_RECORDS:
                rows = rows[:_MAX_RECORDS]
            path.write_text(
                "".join(json.dumps(row.to_dict(), ensure_ascii=False, default=str) + "\n" for row in rows),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("evolution materialization record failed", exc_info=True)

    def _materialize_operation(
        self,
        candidate: EvolutionCandidate,
        plan: EvolutionPromotionPlan,
        *,
        run_archive: RunArchive | None,
    ) -> EvolutionMaterialization | None:
        workspace = self.workspace_path
        if not workspace:
            return None
        store = OperationStore.from_workspace(workspace)
        validation: dict[str, Any] = {}
        asset: dict[str, Any] | None = None
        status = "draft"
        try:
            if plan.materialization_mode == "copy_operation" and plan.asset_id:
                asset = store.copy_to_workspace(plan.asset_id, overwrite=False, status="draft")
            else:
                code = plan.source_code.strip()
                if not code:
                    return None
                asset = store.create(
                    operation_id=plan.asset_id,
                    name=plan.asset_name or plan.asset_id,
                    description=_operation_description(candidate, plan),
                    code=code,
                    provenance={
                        "materialized_from_candidate": candidate.id,
                        "materialized_from_run": plan.source_run_id,
                        "materialized_mode": plan.materialization_mode,
                    },
                    overwrite=False,
                    status="draft",
                )
            validation = store.validate_contract(plan.asset_id)
            if validation.get("ok"):
                status = "published"
                try:
                    store.update(plan.asset_id, status="validated")
                except OperationError:
                    pass
            else:
                status = "failed"
        except Exception as exc:
            logger.debug("operation materialization failed", exc_info=True)
            return EvolutionMaterialization(
                id=uuid.uuid4().hex,
                candidate_id=candidate.id,
                candidate_kind=candidate.kind,
                target_kind="operation",
                materialization_mode=plan.materialization_mode,
                asset_id=plan.asset_id,
                asset_name=plan.asset_name,
                asset_path="",
                status="failed",
                source_run_id=plan.source_run_id,
                summary=plan.summary,
                rationale=plan.rationale + [f"operation materialization failed: {exc}"],
                validation=validation,
                metadata=plan.metadata,
            )
        if asset is None:
            return None
        return EvolutionMaterialization(
            id=uuid.uuid4().hex,
            candidate_id=candidate.id,
            candidate_kind=candidate.kind,
            target_kind="operation",
            materialization_mode=plan.materialization_mode,
            asset_id=str(asset.get("id") or plan.asset_id),
            asset_name=str(asset.get("name") or plan.asset_name or plan.asset_id),
            asset_path=str(asset.get("abs_path") or asset.get("path") or ""),
            status=status,
            source_run_id=plan.source_run_id,
            summary=plan.summary,
            rationale=plan.rationale,
            validation=validation,
            metadata={
                **plan.metadata,
                "run_id": run_archive.run_id if run_archive else None,
            },
        )

    def _materialize_skill(
        self,
        candidate: EvolutionCandidate,
        plan: EvolutionPromotionPlan,
    ) -> EvolutionMaterialization | None:
        root = self.root
        if root is None:
            return None
        skill_name = plan.asset_id or _slug(candidate.title or candidate.kind)
        skill_dir = Path(self.workspace_path).expanduser().resolve() / ".opengis" / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        references_dir = skill_dir / "references"
        references_dir.mkdir(parents=True, exist_ok=True)

        skill_md = _render_skill_md(candidate, plan, skill_name)
        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (references_dir / "candidate.json").write_text(
            json.dumps(candidate.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        (references_dir / "materialization.json").write_text(
            json.dumps(
                {
                    "candidate_id": candidate.id,
                    "source_run_id": plan.source_run_id,
                    "materialization_mode": plan.materialization_mode,
                    "created_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        return EvolutionMaterialization(
            id=uuid.uuid4().hex,
            candidate_id=candidate.id,
            candidate_kind=candidate.kind,
            target_kind="skill",
            materialization_mode="skill",
            asset_id=skill_name,
            asset_name=plan.asset_name or skill_name,
            asset_path=str(skill_dir),
            status="published",
            source_run_id=plan.source_run_id,
            summary=plan.summary,
            rationale=plan.rationale,
            validation={
                "ok": True,
                "notes": ["skill written to workspace/.opengis/skills"],
            },
            metadata=plan.metadata,
        )

    def _materialize_prompt_patch(
        self,
        candidate: EvolutionCandidate,
        plan: EvolutionPromotionPlan,
    ) -> EvolutionMaterialization | None:
        root = self.root
        if root is None:
            return None
        patch_id = plan.asset_id or _slug(candidate.title or candidate.kind)
        patch_dir = root / "assets" / "prompt_patches" / patch_id
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch_text = _render_prompt_patch_md(candidate, plan)
        manifest = {
            "schema_version": "1.0",
            "kind": "prompt_patch",
            "id": patch_id,
            "name": plan.asset_name or patch_id,
            "summary": plan.summary,
            "source_candidate_id": candidate.id,
            "source_run_id": plan.source_run_id,
            "target_component": candidate.target_component,
            "tags": list(candidate.tags[:12]),
            "created_at": time.time(),
            "injection": {
                "surface": "dynamic_tail",
                "mode": "router_selected",
                "max_chars": 900,
            },
        }
        (patch_dir / "prompt_patch.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        (patch_dir / "PATCH.md").write_text(patch_text, encoding="utf-8")
        return EvolutionMaterialization(
            id=uuid.uuid4().hex,
            candidate_id=candidate.id,
            candidate_kind=candidate.kind,
            target_kind="prompt_patch",
            materialization_mode="prompt_patch",
            asset_id=patch_id,
            asset_name=plan.asset_name or patch_id,
            asset_path=str(patch_dir),
            status="published",
            source_run_id=plan.source_run_id,
            summary=plan.summary,
            rationale=plan.rationale,
            validation={
                "ok": bool(patch_text.strip()),
                "notes": ["prompt patch written as gated dynamic-tail asset"],
            },
            metadata={**plan.metadata, "injection_surface": "dynamic_tail"},
        )

    def _materialize_tool_policy(
        self,
        candidate: EvolutionCandidate,
        plan: EvolutionPromotionPlan,
    ) -> EvolutionMaterialization | None:
        root = self.root
        if root is None:
            return None
        policy_id = plan.asset_id or _slug(candidate.title or candidate.kind)
        policy_dir = root / "assets" / "tool_policies" / policy_id
        policy_dir.mkdir(parents=True, exist_ok=True)
        policy_text = _render_tool_policy_md(candidate, plan)
        manifest = {
            "schema_version": "1.0",
            "kind": "tool_policy",
            "id": policy_id,
            "name": plan.asset_name or policy_id,
            "summary": plan.summary,
            "source_candidate_id": candidate.id,
            "source_run_id": plan.source_run_id,
            "target_component": candidate.target_component,
            "tags": list(candidate.tags[:12]),
            "created_at": time.time(),
            "routing": {
                "surface": "capability_router",
                "mode": "advisory_dynamic_tail",
                "max_chars": 900,
            },
        }
        (policy_dir / "tool_policy.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        (policy_dir / "POLICY.md").write_text(policy_text, encoding="utf-8")
        return EvolutionMaterialization(
            id=uuid.uuid4().hex,
            candidate_id=candidate.id,
            candidate_kind=candidate.kind,
            target_kind="tool_policy",
            materialization_mode="tool_policy",
            asset_id=policy_id,
            asset_name=plan.asset_name or policy_id,
            asset_path=str(policy_dir),
            status="published",
            source_run_id=plan.source_run_id,
            summary=plan.summary,
            rationale=plan.rationale,
            validation={
                "ok": bool(policy_text.strip()),
                "notes": ["tool policy written as gated router advisory asset"],
            },
            metadata={**plan.metadata, "injection_surface": "dynamic_tail"},
        )

    @staticmethod
    def _read_file(path: Path) -> list[EvolutionMaterialization]:
        if not path.exists():
            return []
        out: list[EvolutionMaterialization] = []
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
                    record = EvolutionMaterialization.from_dict(raw)
                    if record.candidate_id:
                        out.append(record)
        except Exception:
            logger.debug("evolution materialization read failed: %s", path, exc_info=True)
        return out


def _pick_source_step(run_archive: RunArchive | None) -> tuple[str, str]:
    if run_archive is None:
        return "", ""
    steps = run_archive.read_steps()
    for step in reversed(steps):
        code = str(step.get("code") or "").strip()
        if not code:
            continue
        script_path = str(step.get("script_path") or "").strip()
        return code, script_path
    return "", ""


def _operation_description(candidate: EvolutionCandidate, plan: EvolutionPromotionPlan) -> str:
    parts = [
        candidate.summary or plan.summary or candidate.title or "Promoted reusable operation.",
        "",
        f"Source run: {plan.source_run_id or 'unknown'}",
        f"Candidate kind: {candidate.kind}",
    ]
    if candidate.proposed_change:
        parts.append(f"Proposed change: {candidate.proposed_change}")
    if plan.rationale:
        parts.append("Rationale: " + "; ".join(plan.rationale))
    return "\n".join(parts).strip()


def _render_skill_md(candidate: EvolutionCandidate, plan: EvolutionPromotionPlan, skill_name: str) -> str:
    tags = sorted({*(candidate.tags or []), "evolution", candidate.kind, plan.target_kind})
    frontmatter = [
        "---",
        f'name: "{skill_name}"',
        f'description: "{_escape_yaml_one_line(candidate.summary or plan.summary or candidate.title or "Promoted reusable skill.")}"',
        f'tags: [{", ".join(tags)}]',
        "---",
        "",
    ]
    body = [
        f"# {candidate.title or skill_name}",
        "",
        candidate.summary or plan.summary or "Reusable instruction bundle promoted from a successful run.",
        "",
        "## When to use",
        candidate.proposed_change or "Use when the task matches this promoted procedure.",
        "",
        "## Source run",
        f"- run_id: {plan.source_run_id or 'unknown'}",
        f"- candidate_id: {candidate.id}",
        f"- materialization_mode: {plan.materialization_mode}",
    ]
    if candidate.evidence:
        body.extend(["", "## Evidence"] + [f"- {item}" for item in candidate.evidence[:8]])
    if plan.rationale:
        body.extend(["", "## Rationale"] + [f"- {item}" for item in plan.rationale])
    return "\n".join(frontmatter + body).strip() + "\n"


def _render_prompt_patch_md(candidate: EvolutionCandidate, plan: EvolutionPromotionPlan) -> str:
    body = [
        "# Harness Prompt Patch",
        "",
        "## Scope",
        candidate.target_component or "OpenGIS harness behavior",
        "",
        "## Instruction",
        candidate.proposed_change or candidate.summary or plan.summary or "Apply this correction only when the current turn matches the evidence.",
        "",
        "## Guardrails",
        "- Inject only through the turn-specific dynamic tail.",
        "- Do not mutate the stable system prompt.",
        "- Ignore this patch when it is not selected by capability routing.",
        "- Roll back by disabling or archiving the evolution asset.",
    ]
    if candidate.evidence:
        body.extend(["", "## Evidence"] + [f"- {item}" for item in candidate.evidence[:8]])
    if plan.rationale:
        body.extend(["", "## Rationale"] + [f"- {item}" for item in plan.rationale])
    return "\n".join(body).strip() + "\n"


def _render_tool_policy_md(candidate: EvolutionCandidate, plan: EvolutionPromotionPlan) -> str:
    body = [
        "# Harness Tool Policy",
        "",
        "## Scope",
        candidate.target_component or "OpenGIS tool routing",
        "",
        "## Policy",
        candidate.proposed_change or candidate.summary or plan.summary or "Prefer the smallest sufficient tool pack set for matching turns.",
        "",
        "## Guardrails",
        "- This is advisory unless the capability router selects it.",
        "- Do not force all tools because one turn needed a missing tool.",
        "- If the requested tool is absent from the current provider schema, use the stable platform capability manifest to decide whether to retry with full tools.",
        "- Roll back by disabling or archiving the evolution asset.",
    ]
    if candidate.evidence:
        body.extend(["", "## Evidence"] + [f"- {item}" for item in candidate.evidence[:8]])
    if plan.rationale:
        body.extend(["", "## Rationale"] + [f"- {item}" for item in plan.rationale])
    return "\n".join(body).strip() + "\n"


def _escape_yaml_one_line(value: str) -> str:
    text = str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return text[:240]


def _slug(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return text or f"evo-{uuid.uuid4().hex[:8]}"


def _is_platform_tool_name(value: str) -> bool:
    return str(value or "").strip().lower() in _PLATFORM_TOOL_NAMES


def _looks_like_tool_policy_candidate(candidate: EvolutionCandidate) -> bool:
    text = " ".join([
        candidate.kind,
        candidate.scope,
        candidate.title,
        candidate.summary,
        candidate.proposed_change,
        candidate.target_component,
        " ".join(candidate.tags),
        str(candidate.metadata.get("weakness_kind") or ""),
    ]).lower()
    return any(token in text for token in ("tool_policy", "toolpack", "tool pack", "工具包", "工具路由", "tool routing"))


__all__ = [
    "EvolutionMaterialization",
    "EvolutionMaterializer",
    "EvolutionPlanner",
    "EvolutionPromotionPlan",
]
