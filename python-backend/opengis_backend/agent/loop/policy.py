"""Profile-driven loop policy.

OpenCode does not classify user prompts into "simple" or "complex" with
keywords.  Its runner is driven by the selected agent's configuration:
step limit, permission-filtered tools, and a final provider turn with tools
disabled.  OpenGIS keeps the same shape here.  The policy below is derived
from ``AgentProfile`` only; user text is not inspected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opengis_backend.agent.governance.profile import AgentProfile


@dataclass(frozen=True)
class LoopBudget:
    max_provider_turns: int | None
    max_code_steps: int | None
    max_tool_steps: int | None
    max_work_steps: int | None


@dataclass(frozen=True)
class LoopPolicyDecision:
    force_final: bool
    reason: str = ""


@dataclass
class LoopPolicy:
    profile: AgentProfile
    budget: LoopBudget

    @classmethod
    def from_profile(cls, profile: AgentProfile) -> "LoopPolicy":
        metadata = dict(profile.metadata or {})

        def optional_int(key: str) -> int | None:
            value = metadata.get(key)
            if value is None or value == "":
                return None
            return int(value)

        budget = LoopBudget(
            max_provider_turns=optional_int("max_provider_turns"),
            max_code_steps=optional_int("max_code_steps"),
            max_tool_steps=optional_int("max_tool_steps"),
            max_work_steps=optional_int("max_work_steps"),
        )
        return cls(profile=profile, budget=budget)

    def materialization_options(self) -> dict[str, Any]:
        return {}

    def before_provider_turn(
        self,
        *,
        iteration: int,
        code_steps: int,
        tool_steps: int,
        force_final_reason: str | None = None,
    ) -> LoopPolicyDecision:
        if force_final_reason:
            return LoopPolicyDecision(True, force_final_reason)
        if self.budget.max_provider_turns is not None and iteration >= self.budget.max_provider_turns:
            return LoopPolicyDecision(True, "provider_turn_budget")
        if self.budget.max_code_steps is not None and code_steps >= self.budget.max_code_steps:
            return LoopPolicyDecision(True, "code_step_budget")
        if self.budget.max_tool_steps is not None and tool_steps >= self.budget.max_tool_steps:
            return LoopPolicyDecision(True, "tool_step_budget")
        if self.budget.max_work_steps is not None and code_steps + tool_steps >= self.budget.max_work_steps:
            return LoopPolicyDecision(True, "work_step_budget")
        return LoopPolicyDecision(False, "")

    def after_settlements(
        self,
        settlements: list[Any],
        *,
        code_steps: int,
        tool_steps: int,
    ) -> LoopPolicyDecision:
        if not settlements:
            return LoopPolicyDecision(False, "")

        successful = [
            settlement
            for settlement in settlements
            if getattr(settlement, "error", None) in (None, "")
        ]
        if not successful:
            return self.before_provider_turn(
                iteration=0,
                code_steps=code_steps,
                tool_steps=tool_steps,
            )

        return self.before_provider_turn(
            iteration=0,
            code_steps=code_steps,
            tool_steps=tool_steps,
        )


def final_turn_instruction(reason: str) -> str:
    label = reason or "loop_policy"
    return (
        "## Runner Final Step\n"
        f"Reason: {label}.\n"
        "Tools are disabled for this provider turn. Do not call tools. "
        "Give the user a concise final answer based only on the settled tool "
        "results and conversation state. If something failed, state the failure "
        "and the smallest next action instead of starting a new approach."
    )


__all__ = [
    "LoopBudget",
    "LoopPolicy",
    "LoopPolicyDecision",
    "final_turn_instruction",
]
