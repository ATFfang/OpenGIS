"""Permission runtime for agent tool calls.

The first version is intentionally compatibility-preserving: default build
agents can continue to run. The architecture is still important because every
future approval UI, policy setting, and tool safety rule attaches here instead
of being scattered across individual skills.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

from opengis_backend.agent.profile import AgentProfile, PermissionLevel


class PermissionAction(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    reason: str = ""
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == PermissionAction.ALLOW


@dataclass
class PermissionPolicy:
    """Simple policy table for tool-call authorization."""

    default: PermissionAction = PermissionAction.ALLOW
    tool_overrides: dict[str, PermissionAction] = field(default_factory=dict)
    wildcard_rules: list[dict[str, Any]] = field(default_factory=list)
    ask_on_external_paths: bool = True
    workspace_path: str | None = None
    enforce: bool = False


class PermissionRuntime:
    """Evaluate tool calls before execution."""

    def __init__(
        self,
        *,
        profile: AgentProfile,
        policy: PermissionPolicy | None = None,
        approval_callback: Callable[[str, dict[str, Any], PermissionDecision], PermissionDecision] | None = None,
    ) -> None:
        self.profile = profile
        self.policy = policy or self._policy_from_profile(profile)
        self.approval_callback = approval_callback

    @classmethod
    def from_profile(
        cls,
        profile: AgentProfile,
        *,
        workspace_path: str | None = None,
    ) -> "PermissionRuntime":
        policy = cls._policy_from_profile(profile)
        policy.workspace_path = workspace_path
        return cls(profile=profile, policy=policy)

    @staticmethod
    def _policy_from_profile(profile: AgentProfile) -> PermissionPolicy:
        # Approval is the product default. Older profiles may explicitly set
        # permission_enforce=false to keep the historical advisory-only mode,
        # but ASK/DENY rules must be enforced unless a profile opts out.
        enforce = bool((profile.metadata or {}).get("permission_enforce", True))
        if profile.permission_level == PermissionLevel.READ_ONLY:
            policy = PermissionPolicy(
                default=PermissionAction.ALLOW,
                tool_overrides={
                    "execute_code": PermissionAction.ASK,
                    "bash": PermissionAction.ASK,
                    "write_file": PermissionAction.DENY,
                    "create_workflow": PermissionAction.DENY,
                    "edit_file": PermissionAction.DENY,
                    "delete_file": PermissionAction.DENY,
                    "move_file": PermissionAction.DENY,
                    "copy_file": PermissionAction.DENY,
                    "add_layer": PermissionAction.ASK,
                    "remove_layer": PermissionAction.DENY,
                    "update_layer_style": PermissionAction.ASK,
                },
                enforce=enforce,
            )
        elif profile.permission_level == PermissionLevel.FULL_ACCESS:
            policy = PermissionPolicy(default=PermissionAction.ALLOW, enforce=enforce)
        else:
            policy = PermissionPolicy(
                default=PermissionAction.ALLOW,
                tool_overrides={
                    "delete_file": PermissionAction.ASK,
                    "bash": PermissionAction.ALLOW,
                    "execute_code": PermissionAction.ALLOW,
                },
                enforce=enforce,
            )
        return PermissionRuntime._apply_profile_metadata(policy, profile.metadata or {})

    @staticmethod
    def _apply_profile_metadata(
        policy: PermissionPolicy,
        metadata: dict[str, Any],
    ) -> PermissionPolicy:
        default = PermissionRuntime._parse_action(metadata.get("permission_default"))
        if default is not None:
            policy.default = default
        overrides = metadata.get("permission_tool_overrides")
        if isinstance(overrides, dict):
            for tool, action in overrides.items():
                parsed = PermissionRuntime._parse_action(action)
                if parsed is not None:
                    policy.tool_overrides[str(tool)] = parsed
        rules = metadata.get("permission_rules")
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                pattern = str(rule.get("tool") or rule.get("pattern") or "").strip()
                action = PermissionRuntime._parse_action(rule.get("action"))
                if pattern and action is not None:
                    policy.wildcard_rules.append(
                        {
                            "tool": pattern,
                            "action": action,
                            "reason": str(rule.get("reason") or ""),
                            "rule": str(rule.get("rule") or f"profile:{pattern}"),
                        }
                    )
        ask_external = metadata.get("permission_ask_on_external_paths")
        if isinstance(ask_external, bool):
            policy.ask_on_external_paths = ask_external
        return policy

    def evaluate(self, tool_name: str, arguments: dict[str, Any] | None) -> PermissionDecision:
        args = arguments or {}
        persisted = self._persisted_rule_decision(tool_name)
        if persisted is not None:
            return persisted
        decision = self._profile_rule_decision(tool_name)

        if decision.action == PermissionAction.ALLOW:
            risk = self._risk_decision(tool_name, args)
            if risk is not None:
                decision = risk

        if (
            decision.action == PermissionAction.ASK
            and self.approval_callback is not None
            and self.policy.enforce
        ):
            return self.approval_callback(tool_name, args, decision)
        return decision

    def _persisted_rule_decision(self, tool_name: str) -> PermissionDecision | None:
        workspace = self.policy.workspace_path
        if not workspace:
            return None
        try:
            from opengis_backend.agent.permission_store import PermissionRuleStore

            return PermissionRuleStore(workspace).match(
                tool_name,
                profile_name=self.profile.name,
            )
        except Exception:
            return None

    def _profile_rule_decision(self, tool_name: str) -> PermissionDecision:
        if tool_name in self.policy.tool_overrides:
            return PermissionDecision(
                action=self.policy.tool_overrides[tool_name],
                rule=f"tool:{tool_name}",
            )
        for rule in self.policy.wildcard_rules:
            pattern = str(rule.get("tool") or "")
            if fnmatch(tool_name, pattern):
                return PermissionDecision(
                    action=rule["action"],
                    reason=str(rule.get("reason") or ""),
                    rule=str(rule.get("rule") or f"profile:{pattern}"),
                )
        return PermissionDecision(action=self.policy.default, rule="policy:default")

    def _risk_decision(self, tool_name: str, args: dict[str, Any]) -> PermissionDecision | None:
        if tool_name == "execute_code":
            code = str(args.get("code") or "")
            if self.profile.permission_level == PermissionLevel.READ_ONLY:
                if self._looks_mutating_code(code):
                    return PermissionDecision(
                        PermissionAction.DENY,
                        "Read-only agent attempted mutating Python code.",
                        "profile:read_only",
                    )
            if self._looks_package_install(code):
                return PermissionDecision(
                    PermissionAction.ASK,
                    "Python code appears to install packages.",
                    "risk:pip_install",
                )
            if self._looks_destructive_code(code):
                return PermissionDecision(
                    PermissionAction.ASK,
                    "Python code appears to delete or destructively modify files.",
                    "risk:destructive_python",
                )

        if tool_name == "bash":
            command = str(args.get("command") or args.get("cmd") or "")
            if self._looks_destructive_shell(command):
                return PermissionDecision(
                    PermissionAction.ASK,
                    "Shell command appears destructive.",
                    "risk:destructive_shell",
                )

        path = self._extract_path_arg(args)
        if path and self.policy.ask_on_external_paths and self._is_external_path(path):
            return PermissionDecision(
                PermissionAction.ASK,
                f"Tool targets a path outside the workspace: {path}",
                "risk:external_path",
            )

        return None

    @staticmethod
    def _looks_package_install(code: str) -> bool:
        return bool(re.search(r"\b(pip\s+install|python\s+-m\s+pip|subprocess\..*pip)", code))

    @staticmethod
    def _looks_mutating_code(code: str) -> bool:
        patterns = [
            r"\.to_file\s*\(",
            r"\.to_csv\s*\(",
            r"\.write_text\s*\(",
            r"\.write_bytes\s*\(",
            r"\bopen\s*\([^)]*[\"'](?:w|a|x|\+)",
            r"\bos\.(remove|unlink|rename|replace)\s*\(",
            r"\bshutil\.(rmtree|move|copy|copytree)\s*\(",
        ]
        return any(re.search(p, code) for p in patterns)

    @staticmethod
    def _looks_destructive_code(code: str) -> bool:
        patterns = [
            r"\bdelete_file\s*\(",
            r"\bos\.(remove|unlink|rename|replace)\s*\(",
            r"\bPath\s*\([^)]*\)\.unlink\s*\(",
            r"\bpathlib\.Path\s*\([^)]*\)\.unlink\s*\(",
            r"\.unlink\s*\(",
            r"\bshutil\.(rmtree|move)\s*\(",
            r"\bbash\s*\([^)]*\brm\s+",
        ]
        return any(re.search(p, code) for p in patterns)

    @staticmethod
    def _looks_destructive_shell(command: str) -> bool:
        return bool(re.search(r"\b(rm\s+-|rm\s+|mv\s+|chmod\s+|chown\s+|dd\s+)", command))

    @staticmethod
    def _extract_path_arg(args: dict[str, Any]) -> str | None:
        for key in ("path", "file_path", "src", "dst", "input_path", "output_path", "save_path"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _is_external_path(self, raw_path: str) -> bool:
        workspace = self.policy.workspace_path
        if not workspace:
            return False
        try:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                return False
            resolved = path.resolve()
            root = Path(workspace).expanduser().resolve()
            return not str(resolved).startswith(str(root))
        except Exception:
            return False

    @staticmethod
    def _parse_action(value: Any) -> PermissionAction | None:
        if isinstance(value, PermissionAction):
            return value
        if not isinstance(value, str):
            return None
        try:
            return PermissionAction(value)
        except ValueError:
            return None


__all__ = [
    "PermissionAction",
    "PermissionDecision",
    "PermissionPolicy",
    "PermissionRuntime",
]
