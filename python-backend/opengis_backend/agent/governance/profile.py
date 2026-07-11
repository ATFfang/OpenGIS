"""Agent profile definitions.

Profiles are the control-plane identity of an agent. They describe what the
agent is allowed to do, which tools it should see, and how much autonomy it has.
This makes OpenGIS agents configurable instead of baking every behavior into
AgentLoop/WorkflowLoop branches.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PLAN_STEPS = 3
DEFAULT_EXPLORE_STEPS = 4
DEFAULT_WORKFLOW_STEPS = 8
DEFAULT_SUBAGENT_STEPS = 4


class AgentMode(str, Enum):
    """High-level operating modes for OpenGIS agents."""

    BUILD = "build"
    PLAN = "plan"
    EXPLORE = "explore"
    REPORT = "report"
    WORKFLOW = "workflow"
    SUBAGENT = "subagent"
    SYSTEM = "system"


class PermissionLevel(str, Enum):
    """Default autonomy level for a profile."""

    READ_ONLY = "read_only"
    SAFE_WRITE = "safe_write"
    FULL_ACCESS = "full_access"


@dataclass(frozen=True)
class AgentProfile:
    """Configuration for one agent persona/runtime."""

    name: str
    mode: AgentMode
    description: str
    tool_groups: list[str] | None = None
    permission_level: PermissionLevel = PermissionLevel.SAFE_WRITE
    max_steps: int | None = None
    hidden: bool = False
    prompt_suffix: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def gis_build(max_steps: int | None = None) -> "AgentProfile":
        return AgentProfile(
            name="gis-build",
            mode=AgentMode.BUILD,
            description="Default autonomous GIS task execution agent.",
            tool_groups=["core", "qgis", "osm", "datasource", "worker"],
            permission_level=PermissionLevel.SAFE_WRITE,
            max_steps=max_steps,
            metadata={},
        )

    @staticmethod
    def gis_plan(max_steps: int | None = None) -> "AgentProfile":
        steps = int(max_steps or DEFAULT_PLAN_STEPS)
        return AgentProfile(
            name="gis-plan",
            mode=AgentMode.PLAN,
            description="Read-only planning and decomposition agent.",
            tool_groups=["core"],
            permission_level=PermissionLevel.READ_ONLY,
            max_steps=steps,
            metadata={
                "max_provider_turns": steps,
                "max_code_steps": 1,
                "max_tool_steps": steps,
                "max_work_steps": steps,
            },
            prompt_suffix=(
                "\nYou are in planning mode. Prefer reading and reasoning. "
                "Do not modify files or map state unless explicitly requested.\n"
            ),
        )

    @staticmethod
    def gis_explore(max_steps: int | None = None) -> "AgentProfile":
        steps = int(max_steps or DEFAULT_EXPLORE_STEPS)
        return AgentProfile(
            name="gis-explore",
            mode=AgentMode.EXPLORE,
            description="Dataset exploration agent with bounded output.",
            tool_groups=["core", "datasource", "osm"],
            permission_level=PermissionLevel.READ_ONLY,
            max_steps=steps,
            metadata={
                "max_provider_turns": steps,
                "max_code_steps": min(steps, 2),
                "max_tool_steps": steps * 2,
                "max_work_steps": steps * 2,
            },
        )

    @staticmethod
    def workflow_runner(max_steps: int | None = None) -> "AgentProfile":
        steps = int(max_steps or DEFAULT_WORKFLOW_STEPS)
        return AgentProfile(
            name="workflow-runner",
            mode=AgentMode.WORKFLOW,
            description="Structured workflow node execution agent.",
            tool_groups=None,
            permission_level=PermissionLevel.SAFE_WRITE,
            max_steps=steps,
            metadata={
                "max_provider_turns": steps,
            },
        )

    @staticmethod
    def subagent(max_steps: int | None = None, tool_groups: list[str] | None = None) -> "AgentProfile":
        steps = int(max_steps or DEFAULT_SUBAGENT_STEPS)
        return AgentProfile(
            name="gis-subagent",
            mode=AgentMode.SUBAGENT,
            description="Isolated child agent for a self-contained subtask.",
            tool_groups=tool_groups,
            permission_level=PermissionLevel.SAFE_WRITE,
            max_steps=steps,
            metadata={
                "max_provider_turns": steps,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode.value,
            "description": self.description,
            "tool_groups": self.tool_groups,
            "permission_level": self.permission_level.value,
            "max_steps": self.max_steps,
            "hidden": self.hidden,
            "prompt_suffix": self.prompt_suffix,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentProfile":
        return cls(
            name=str(data.get("name") or "gis-build"),
            mode=AgentMode(str(data.get("mode") or AgentMode.BUILD.value)),
            description=str(data.get("description") or ""),
            tool_groups=data.get("tool_groups"),
            permission_level=PermissionLevel(str(data.get("permission_level") or PermissionLevel.SAFE_WRITE.value)),
            max_steps=data.get("max_steps"),
            hidden=bool(data.get("hidden", False)),
            prompt_suffix=str(data.get("prompt_suffix") or ""),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )


DEFAULT_AGENT_PROFILES: dict[str, AgentProfile] = {
    "gis-build": AgentProfile.gis_build(),
    "gis-plan": AgentProfile.gis_plan(),
    "gis-explore": AgentProfile.gis_explore(),
    "workflow-runner": AgentProfile.workflow_runner(),
    "gis-subagent": AgentProfile.subagent(),
}


class AgentProfileStore:
    """Load workspace-configurable profiles with built-in fallbacks."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def path(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / ".opengis" / "agents.json"

    def load_all(self) -> dict[str, AgentProfile]:
        profiles = dict(DEFAULT_AGENT_PROFILES)
        path = self.path
        if path is None or not path.exists():
            return profiles
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_profiles = data.get("profiles") if isinstance(data, dict) else None
            if isinstance(raw_profiles, list):
                for item in raw_profiles:
                    if isinstance(item, dict):
                        profile = AgentProfile.from_dict(item)
                        profiles[profile.name] = profile
        except Exception:
            logger.debug("agent profile load failed (using defaults)", exc_info=True)
        return profiles

    def get(self, name: str, default: str = "gis-build") -> AgentProfile:
        profiles = self.load_all()
        return profiles.get(name) or profiles[default]

    def install_defaults(self) -> str | None:
        path = self.path
        if path is None:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                payload = {"profiles": [p.to_dict() for p in DEFAULT_AGENT_PROFILES.values()]}
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(path)
        except Exception:
            logger.debug("agent profile default install failed", exc_info=True)
            return None


__all__ = [
    "AgentMode",
    "PermissionLevel",
    "AgentProfile",
    "AgentProfileStore",
    "DEFAULT_AGENT_PROFILES",
    "DEFAULT_PLAN_STEPS",
    "DEFAULT_EXPLORE_STEPS",
    "DEFAULT_WORKFLOW_STEPS",
    "DEFAULT_SUBAGENT_STEPS",
]
