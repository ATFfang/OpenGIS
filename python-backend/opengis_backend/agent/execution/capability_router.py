"""Per-turn capability routing for the OpenGIS harness.

The router is the narrow bridge between accumulated reusable capabilities and
one provider request. It keeps the stable harness prompt small while selecting
only task-relevant tool packs, skills, operations, and evolution assets for the
dynamic tail of the current turn.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from opengis_backend.agent.execution.tool_packs import infer_tool_packs_for_text
from opengis_backend.operations import OperationStore
from opengis_backend.skills.discovery import UserSkillDiscovery, UserSkillInfo
from opengis_backend.workspace.evolution_asset_registry import EvolutionAssetRecord, EvolutionAssetRegistry


_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-\.]+|[\u4e00-\u9fff]{1,4}")
_DEFAULT_CACHE_TTL_SECONDS = 5.0
_INVENTORY_CACHE: dict[str, "_CachedInventory"] = {}


@dataclass(frozen=True)
class RoutedCapability:
    kind: str
    name: str
    description: str = ""
    source: str = ""
    state: str = ""
    score: float = 0.0
    content: str = ""

    def to_prompt_line(self) -> str:
        parts = [f"- {self.kind}:{self.name}"]
        if self.state:
            parts.append(f"state={self.state}")
        if self.source:
            parts.append(f"source={self.source}")
        if self.description:
            parts.append(f"desc={_compact(self.description, 180)}")
        line = " ".join(parts)
        if self.content:
            line += f"\n  guidance={_compact(self.content, 900)}"
        return line


@dataclass(frozen=True)
class CapabilitySelection:
    tool_packs: set[str]
    skills: tuple[RoutedCapability, ...] = ()
    operations: tuple[RoutedCapability, ...] = ()
    evolution_assets: tuple[RoutedCapability, ...] = ()
    reason: str = ""
    total_skills: int = 0
    total_operations: int = 0
    total_evolution_assets: int = 0
    inventory_cache_status: str = ""
    min_score: float = 0.0

    def to_prompt(self) -> str:
        lines = [
            "## Relevant Capabilities For This Turn",
            "This section is turn-specific. It narrows attention; provider tool schemas remain the exact callable surface.",
            f"tool_packs={', '.join(sorted(self.tool_packs)) or 'none'}",
        ]
        if self.skills:
            lines.append("skills:")
            lines.extend(item.to_prompt_line() for item in self.skills)
            lines.append("rule=call load_skill(name=...) before following a listed skill.")
        if self.operations:
            lines.append("operations:")
            lines.extend(item.to_prompt_line() for item in self.operations)
            lines.append("rule=use list_operations/get_operation/run_operation for a listed matching operation before rebuilding code.")
        if self.evolution_assets:
            lines.append("harness_assets:")
            lines.extend(item.to_prompt_line() for item in self.evolution_assets)
            lines.append(
                "rule=apply enabled harness assets only when they match this turn; "
                "they are dynamic-tail guidance, not stable prompt mutations."
            )
        lines.append(
            "budget="
            f"skills {len(self.skills)}/{self.total_skills}, "
            f"operations {len(self.operations)}/{self.total_operations}, "
            f"evolution_assets {len(self.evolution_assets)}/{self.total_evolution_assets}"
        )
        if self.inventory_cache_status:
            lines.append(f"router=inventory:{self.inventory_cache_status} min_score={self.min_score:g}")
        if self.reason:
            lines.append(f"reason={_compact(self.reason, 220)}")
        return "\n".join(lines)

    def to_telemetry(self) -> dict[str, Any]:
        return {
            "tool_packs": sorted(self.tool_packs),
            "tool_pack_count": len(self.tool_packs),
            "skill_count": len(self.skills),
            "operation_count": len(self.operations),
            "evolution_asset_count": len(self.evolution_assets),
            "total_skills": self.total_skills,
            "total_operations": self.total_operations,
            "total_evolution_assets": self.total_evolution_assets,
            "skill_names": [item.name for item in self.skills],
            "operation_names": [item.name for item in self.operations],
            "evolution_asset_names": [item.name for item in self.evolution_assets],
            "reason": self.reason,
            "inventory_cache_status": self.inventory_cache_status,
            "min_score": self.min_score,
        }


@dataclass(frozen=True)
class _CapabilityInventory:
    skills: tuple[UserSkillInfo, ...] = ()
    operations: tuple[dict[str, Any], ...] = ()
    evolution_assets: tuple[EvolutionAssetRecord, ...] = ()
    cache_status: str = "disabled"


@dataclass(frozen=True)
class _CachedInventory:
    created_at: float
    inventory: _CapabilityInventory


@dataclass(frozen=True)
class CapabilityRouter:
    workspace_path: str | None = None
    max_skills: int = 4
    max_operations: int = 4
    max_evolution_assets: int = 4
    min_score: float = 2.0
    cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS

    def select(
        self,
        text: str,
        *,
        base_tool_packs: set[str] | None = None,
        allow_3d: bool = False,
        force_tool_packs: Iterable[str] | None = None,
    ) -> CapabilitySelection:
        query = str(text or "")
        tool_packs = infer_tool_packs_for_text(
            query,
            base_packs=base_tool_packs,
            allow_3d=allow_3d,
        )
        for pack in force_tool_packs or ():
            if pack:
                tool_packs.add(str(pack))
        if tool_packs.intersection({"style", "raster"}):
            tool_packs.add("map")
        if not allow_3d:
            tool_packs.discard("map_3d")

        inventory = self._inventory()
        skills = self._select_skills(query, inventory.skills, limit=self.max_skills)
        operations = self._select_operations(
            query,
            inventory.operations,
            inventory.evolution_assets,
            limit=self.max_operations,
        )
        assets = self._select_evolution_assets(query, inventory.evolution_assets, limit=self.max_evolution_assets)

        if skills:
            tool_packs.add("skill")
        if operations:
            tool_packs.add("operation")

        return CapabilitySelection(
            tool_packs=tool_packs,
            skills=tuple(skills),
            operations=tuple(operations),
            evolution_assets=tuple(assets),
            reason="deterministic lexical routing over current turn objective, pending intent, and recent failures",
            total_skills=len(inventory.skills),
            total_operations=len(inventory.operations),
            total_evolution_assets=len(inventory.evolution_assets),
            inventory_cache_status=inventory.cache_status,
            min_score=self.min_score,
        )

    def _inventory(self) -> _CapabilityInventory:
        if not self.workspace_path:
            return _CapabilityInventory()
        workspace = str(Path(self.workspace_path).expanduser().resolve())
        now = time.monotonic()
        cached = _INVENTORY_CACHE.get(workspace)
        ttl = max(0.0, float(self.cache_ttl_seconds))
        if cached is not None and ttl > 0 and now - cached.created_at <= ttl:
            return _CapabilityInventory(
                skills=cached.inventory.skills,
                operations=cached.inventory.operations,
                evolution_assets=cached.inventory.evolution_assets,
                cache_status="hit",
            )
        inventory = self._load_inventory(workspace)
        _INVENTORY_CACHE[workspace] = _CachedInventory(
            created_at=now,
            inventory=inventory,
        )
        return _CapabilityInventory(
            skills=inventory.skills,
            operations=inventory.operations,
            evolution_assets=inventory.evolution_assets,
            cache_status="miss",
        )

    def _load_inventory(self, workspace: str) -> _CapabilityInventory:
        skills: tuple[UserSkillInfo, ...] = ()
        operations: tuple[dict[str, Any], ...] = ()
        assets: tuple[EvolutionAssetRecord, ...] = ()
        try:
            skills = tuple(UserSkillDiscovery(workspace_path=workspace).list())
        except Exception:
            skills = ()
        try:
            operations = tuple(OperationStore.from_workspace(workspace).list(limit=200))
        except Exception:
            operations = ()
        try:
            assets = tuple(EvolutionAssetRegistry(workspace).list(limit=200))
        except Exception:
            assets = ()
        return _CapabilityInventory(
            skills=skills,
            operations=operations,
            evolution_assets=assets,
            cache_status="miss",
        )

    def _select_skills(self, query: str, skills: tuple[UserSkillInfo, ...], *, limit: int) -> list[RoutedCapability]:
        ranked: list[tuple[float, UserSkillInfo]] = []
        for skill in skills:
            score = _score(query, [skill.name, skill.description or "", " ".join(skill.tags), skill.source])
            if score >= self.min_score:
                ranked.append((score, skill))
        ranked.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [
            RoutedCapability(
                kind="skill",
                name=skill.name,
                description=skill.description or "",
                source=_display_source(skill.location, self.workspace_path),
                state=skill.evolution_asset_state or "",
                score=score,
            )
            for score, skill in ranked[: max(0, limit)]
        ]

    def _select_operations(
        self,
        query: str,
        operations: tuple[dict[str, Any], ...],
        assets: tuple[EvolutionAssetRecord, ...],
        *,
        limit: int,
    ) -> list[RoutedCapability]:
        if not self.workspace_path:
            return []
        operation_assets = {
            record.name: record
            for record in assets
            if record.kind == "operation"
        }
        ranked: list[tuple[float, dict[str, Any]]] = []
        for operation in operations:
            op_id = str(operation.get("id") or operation.get("name") or "")
            asset = operation_assets.get(op_id)
            if asset is not None:
                if asset.state != "enabled" or not self._asset_is_loadable(asset):
                    continue
            score = _score(
                query,
                [
                    op_id,
                    str(operation.get("name") or ""),
                    str(operation.get("description") or ""),
                    str(operation.get("status") or ""),
                    str(operation.get("scope") or ""),
                ],
            )
            if score >= self.min_score:
                ranked.append((score, operation))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("id") or "").lower()))
        return [
            RoutedCapability(
                kind="operation",
                name=str(operation.get("id") or operation.get("name") or ""),
                description=str(operation.get("description") or operation.get("name") or ""),
                source=str(operation.get("scope") or ""),
                state=str(operation.get("evolution_asset_state") or operation.get("status") or ""),
                score=score,
            )
            for score, operation in ranked[: max(0, limit)]
        ]

    def _select_evolution_assets(self, query: str, records: tuple[EvolutionAssetRecord, ...], *, limit: int) -> list[RoutedCapability]:
        if not self.workspace_path:
            return []
        ranked: list[tuple[float, EvolutionAssetRecord]] = []
        details_by_name: dict[str, _AssetPromptDetails] = {}
        for record in records:
            # Operations and user skills already have dedicated routed
            # sections above. Repeating them as generic evolution cards
            # needlessly duplicates prompt tokens and can create conflicting
            # instructions.
            if record.kind in {"operation", "skill"}:
                continue
            if record.state != "enabled":
                continue
            if record.kind not in {"prompt_patch", "tool_policy"}:
                continue
            if not self._asset_is_loadable(record):
                continue
            details = _load_asset_prompt_details(record)
            details_by_name[record.name] = details
            score = _score(
                query,
                [
                    record.kind,
                    record.name,
                    record.state,
                    str(record.metadata.get("asset_name") or ""),
                    str(record.metadata.get("materialization_mode") or ""),
                    str(record.metadata.get("materialization_status") or ""),
                    details.name,
                    details.summary,
                    " ".join(details.tags),
                    details.content,
                ],
            )
            if score >= self.min_score:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], item[1].kind, item[1].name))
        return [
            RoutedCapability(
                kind=record.kind,
                name=record.name,
                description=(
                    details_by_name.get(record.name, _AssetPromptDetails()).summary
                    or str(record.metadata.get("asset_name") or record.metadata.get("materialization_mode") or "")
                ),
                source=_display_source(record.path, self.workspace_path),
                state=record.state,
                score=score,
                content=details_by_name.get(record.name, _AssetPromptDetails()).content,
            )
            for score, record in ranked[: max(0, limit)]
        ]

    def _asset_is_loadable(self, asset: EvolutionAssetRecord) -> bool:
        asset_path = Path(asset.path).expanduser() if asset.path else None
        if asset_path is None:
            return False
        try:
            if not asset_path.exists():
                return False
        except OSError:
            return False
        if asset.kind == "operation":
            return (asset_path / "operation.json").is_file() and (asset_path / "main.py").is_file()
        if asset.kind == "skill":
            return (asset_path / "SKILL.md").is_file()
        if asset.kind == "prompt_patch":
            return (asset_path / "prompt_patch.json").is_file() and (asset_path / "PATCH.md").is_file()
        if asset.kind == "tool_policy":
            return (asset_path / "tool_policy.json").is_file() and (asset_path / "POLICY.md").is_file()
        return False


def skill_loading_policy_prompt(skill_count: int) -> str:
    if skill_count <= 0:
        return (
            "Skills are supported but no loadable skills are currently discovered. "
            "Do not invent skill names."
        )
    return (
        f"{skill_count} loadable skill(s) are installed. The stable prompt does "
        "not enumerate them. Use only skills listed in the turn-specific "
        "Relevant Capabilities section, then call load_skill(name=...)."
    )


def _score(query: str, fields: list[str]) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    query_lower = str(query or "").strip().lower()
    haystack = " ".join(str(field or "") for field in fields).lower()
    field_tokens = _tokens(haystack)
    if not field_tokens:
        return 0.0
    score = 0.0
    for token in query_tokens:
        if token in field_tokens:
            score += 4.0 if len(token) > 1 else 1.0
        elif len(token) >= 2 and token in haystack:
            score += 2.0
    for token in field_tokens:
        if len(token) >= 2 and token in query_lower:
            score += 2.0
    if str(query or "").strip().lower() in haystack:
        score += 6.0
    return score


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(str(text or ""))}


def _display_source(path: str, workspace_path: str | None) -> str:
    clean = str(path or "")
    if not clean:
        return ""
    if workspace_path:
        try:
            return str(Path(clean).expanduser().resolve().relative_to(Path(workspace_path).expanduser().resolve()))
        except Exception:
            pass
    return clean


def _compact(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


@dataclass(frozen=True)
class _AssetPromptDetails:
    name: str = ""
    summary: str = ""
    tags: tuple[str, ...] = ()
    content: str = ""


def _load_asset_prompt_details(record: EvolutionAssetRecord) -> _AssetPromptDetails:
    asset_path = Path(record.path).expanduser() if record.path else None
    if asset_path is None:
        return _AssetPromptDetails()
    manifest_name = "prompt_patch.json" if record.kind == "prompt_patch" else "tool_policy.json"
    body_name = "PATCH.md" if record.kind == "prompt_patch" else "POLICY.md"
    manifest: dict[str, Any] = {}
    try:
        manifest_raw = (asset_path / manifest_name).read_text(encoding="utf-8")
        parsed = json.loads(manifest_raw)
        if isinstance(parsed, dict):
            manifest = parsed
    except Exception:
        manifest = {}
    try:
        content = (asset_path / body_name).read_text(encoding="utf-8")
    except Exception:
        content = ""
    tags = manifest.get("tags") if isinstance(manifest.get("tags"), list) else []
    max_chars = 900
    injection = manifest.get("injection") if isinstance(manifest.get("injection"), dict) else {}
    routing = manifest.get("routing") if isinstance(manifest.get("routing"), dict) else {}
    configured_max = injection.get("max_chars", routing.get("max_chars"))
    try:
        max_chars = max(240, min(1400, int(configured_max or max_chars)))
    except (TypeError, ValueError):
        max_chars = 900
    return _AssetPromptDetails(
        name=str(manifest.get("name") or record.name),
        summary=str(manifest.get("summary") or record.metadata.get("asset_name") or ""),
        tags=tuple(str(item) for item in tags if str(item).strip()),
        content=_compact(content, max_chars),
    )


__all__ = [
    "CapabilityRouter",
    "CapabilitySelection",
    "RoutedCapability",
    "skill_loading_policy_prompt",
]
