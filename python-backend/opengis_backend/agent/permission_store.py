"""In-memory permission request store.

This is the control-plane layer around synchronous approval callbacks: each
ASK decision becomes an observable request with a stable id and final result.
"""

from __future__ import annotations

import time
import uuid
import json
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from opengis_backend.agent.permission import PermissionAction, PermissionDecision


@dataclass
class PermissionRequest:
    id: str
    tool_name: str
    arguments: dict[str, Any]
    decision: PermissionDecision
    status: str = "pending"
    result: PermissionAction | None = None
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None

    @classmethod
    def create(
        cls,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        decision: PermissionDecision,
    ) -> "PermissionRequest":
        return cls(
            id=uuid.uuid4().hex,
            tool_name=tool_name,
            arguments=dict(arguments),
            decision=decision,
        )

    def resolve(self, *, action: PermissionAction, reason: str = "") -> None:
        self.status = "resolved"
        self.result = action
        self.reason = reason
        self.resolved_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "status": self.status,
            "result": self.result.value if self.result else None,
            "reason": self.reason,
            "decision": {
                "action": self.decision.action.value,
                "reason": self.decision.reason,
                "rule": self.decision.rule,
            },
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


class PermissionRequestStore:
    """Small append-only-ish in-memory store for current process approvals."""

    def __init__(self, *, max_history: int = 200) -> None:
        self.max_history = max(10, int(max_history))
        self._requests: dict[str, PermissionRequest] = {}
        self._order: list[str] = []

    def create(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        decision: PermissionDecision,
    ) -> PermissionRequest:
        req = PermissionRequest.create(
            tool_name=tool_name,
            arguments=arguments,
            decision=decision,
        )
        self._requests[req.id] = req
        self._order.append(req.id)
        self._trim()
        return req

    def resolve(self, request_id: str, *, action: PermissionAction, reason: str = "") -> PermissionRequest | None:
        req = self._requests.get(request_id)
        if req is None:
            return None
        req.resolve(action=action, reason=reason)
        return req

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items: list[PermissionRequest] = []
        for request_id in reversed(self._order):
            req = self._requests.get(request_id)
            if req is None:
                continue
            if status and req.status != status:
                continue
            items.append(req)
            if len(items) >= max(1, limit):
                break
        return [req.to_dict() for req in items]

    def _trim(self) -> None:
        while len(self._order) > self.max_history:
            old = self._order.pop(0)
            req = self._requests.get(old)
            if req is not None and req.status == "pending":
                self._order.insert(0, old)
                break
            self._requests.pop(old, None)


class PermissionRuleStore:
    """Workspace-persistent allow/deny rules created from approvals."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def path(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / ".opengis" / "permissions.json"

    def list_rules(self) -> list[dict[str, Any]]:
        data = self._load()
        rules = data.get("rules")
        return list(rules) if isinstance(rules, list) else []

    def add_rule(
        self,
        *,
        tool: str,
        action: PermissionAction,
        scope: str = "workspace",
        reason: str = "",
        profile_name: str | None = None,
    ) -> dict[str, Any] | None:
        path = self.path
        if path is None:
            return None
        rule = {
            "id": uuid.uuid4().hex,
            "tool": tool,
            "action": action.value,
            "scope": scope,
            "reason": reason,
            "profile_name": profile_name,
            "created_at": time.time(),
        }
        data = self._load()
        rules = data.setdefault("rules", [])
        if isinstance(rules, list):
            rules.append(rule)
        self._save(data)
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        data = self._load()
        rules = data.get("rules")
        if not isinstance(rules, list):
            return False
        remaining = [rule for rule in rules if not isinstance(rule, dict) or rule.get("id") != rule_id]
        if len(remaining) == len(rules):
            return False
        data["rules"] = remaining
        self._save(data)
        return True

    def match(self, tool_name: str, *, profile_name: str | None = None) -> PermissionDecision | None:
        for rule in reversed(self.list_rules()):
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("tool") or "")
            if not pattern or not fnmatch(tool_name, pattern):
                continue
            rule_profile = rule.get("profile_name")
            if rule_profile and profile_name and str(rule_profile) != profile_name:
                continue
            try:
                action = PermissionAction(str(rule.get("action")))
            except ValueError:
                continue
            return PermissionDecision(
                action=action,
                reason=str(rule.get("reason") or "Matched persisted permission rule."),
                rule=f"persisted:{rule.get('id', pattern)}",
            )
        return None

    def _load(self) -> dict[str, Any]:
        path = self.path
        if path is None or not path.exists():
            return {"rules": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"rules": []}
        except Exception:
            return {"rules": []}

    def _save(self, data: dict[str, Any]) -> None:
        path = self.path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["PermissionRequest", "PermissionRequestStore", "PermissionRuleStore"]
