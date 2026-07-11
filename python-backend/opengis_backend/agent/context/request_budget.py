"""Provider-request token budgeting.

This module is deliberately provider-facing: it estimates the request that is
about to be sent to the model, including system/runtime messages, projected
conversation messages, active tool schemas, memory inserts, and output reserve.
It does not own raw conversation storage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from opengis_backend.agent.context.token_utils import estimate_messages_tokens, estimate_tokens


@dataclass(frozen=True)
class BudgetSection:
    name: str
    tokens: int
    chars: int = 0
    count: int = 0


@dataclass(frozen=True)
class LargeMessage:
    index: int
    role: str
    tokens: int
    chars: int
    label: str = ""
    preview: str = ""


@dataclass(frozen=True)
class RequestBudgetReport:
    total_tokens: int
    usable_input_tokens: int
    output_reserve_tokens: int
    pressure: str
    sections: list[BudgetSection] = field(default_factory=list)
    largest_messages: list[LargeMessage] = field(default_factory=list)
    tool_schema_count: int = 0
    tool_schema_tokens: int = 0

    def section_tokens(self, name: str) -> int:
        for section in self.sections:
            if section.name == name:
                return section.tokens
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "usable_input_tokens": self.usable_input_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "pressure": self.pressure,
            "sections": [section.__dict__ for section in self.sections],
            "largest_messages": [message.__dict__ for message in self.largest_messages],
            "tool_schema_count": self.tool_schema_count,
            "tool_schema_tokens": self.tool_schema_tokens,
        }


@dataclass(frozen=True)
class BudgetLimits:
    """Provider-request limits derived from the current token budget.

    These are intentionally practical knobs, not a second policy layer. The
    loop owns the request; ContextManager uses these numbers to decide how
    much raw history, memory, and observations may enter the next provider
    call. Tool schemas remain a stable profile/provider contract and are not
    trimmed here.
    """

    max_memory_records: int
    provider_raw_recent: int
    recent_user_turns: int
    max_tool_result_chars: int
    max_tool_call_arg_chars: int
    max_execute_code_chars: int
    max_digest_chars: int


class RequestBudgetManager:
    """Estimate and classify a complete provider request."""

    def __init__(
        self,
        *,
        input_token_budget: int = 100_000,
        output_reserve_tokens: int = 4096,
        warm_ratio: float = 0.55,
        hot_ratio: float = 0.75,
        overflow_ratio: float = 0.92,
    ) -> None:
        self.input_token_budget = max(8_000, int(input_token_budget))
        self.output_reserve_tokens = max(512, int(output_reserve_tokens))
        self.usable_input_tokens = max(1_000, self.input_token_budget - self.output_reserve_tokens)
        self.warm_ratio = float(warm_ratio)
        self.hot_ratio = float(hot_ratio)
        self.overflow_ratio = float(overflow_ratio)

    def analyze(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> RequestBudgetReport:
        tools = list(tools or [])
        message_sections = self._message_sections(messages)
        tool_schema_tokens, tool_schema_chars = self._tool_schema_cost(tools)
        sections = [
            *message_sections,
            BudgetSection(
                name="tool_schema",
                tokens=tool_schema_tokens,
                chars=tool_schema_chars,
                count=len(tools),
            ),
            BudgetSection(
                name="output_reserve",
                tokens=self.output_reserve_tokens,
                chars=0,
                count=1,
            ),
        ]
        total = sum(section.tokens for section in sections)
        return RequestBudgetReport(
            total_tokens=total,
            usable_input_tokens=self.usable_input_tokens,
            output_reserve_tokens=self.output_reserve_tokens,
            pressure=self._pressure(total),
            sections=sections,
            largest_messages=self._largest_messages(messages),
            tool_schema_count=len(tools),
            tool_schema_tokens=tool_schema_tokens,
        )

    def suggest_limits(self, *, pressure: str = "ok", live_tokens: int = 0) -> BudgetLimits:
        """Return budget-aware context limits for the next provider request."""
        pressure = pressure if pressure in {"ok", "warm", "hot", "overflow"} else "ok"
        if pressure == "overflow" or live_tokens > self.usable_input_tokens * self.hot_ratio:
            return BudgetLimits(
                max_memory_records=4,
                provider_raw_recent=4,
                recent_user_turns=4,
                max_tool_result_chars=1400,
                max_tool_call_arg_chars=700,
                max_execute_code_chars=420,
                max_digest_chars=3200,
            )
        if pressure == "hot" or live_tokens > self.usable_input_tokens * self.warm_ratio:
            return BudgetLimits(
                max_memory_records=6,
                provider_raw_recent=5,
                recent_user_turns=4,
                max_tool_result_chars=2200,
                max_tool_call_arg_chars=900,
                max_execute_code_chars=560,
                max_digest_chars=4200,
            )
        if pressure == "warm":
            return BudgetLimits(
                max_memory_records=8,
                provider_raw_recent=6,
                recent_user_turns=4,
                max_tool_result_chars=3000,
                max_tool_call_arg_chars=1100,
                max_execute_code_chars=720,
                max_digest_chars=5000,
            )
        return BudgetLimits(
            max_memory_records=10,
            provider_raw_recent=8,
            recent_user_turns=4,
            max_tool_result_chars=4000,
            max_tool_call_arg_chars=1400,
            max_execute_code_chars=900,
            max_digest_chars=6000,
        )

    def _pressure(self, total_tokens: int) -> str:
        ratio = total_tokens / max(1, self.usable_input_tokens)
        if ratio >= self.overflow_ratio:
            return "overflow"
        if ratio >= self.hot_ratio:
            return "hot"
        if ratio >= self.warm_ratio:
            return "warm"
        return "ok"

    @staticmethod
    def _tool_schema_cost(tools: list[dict[str, Any]]) -> tuple[int, int]:
        if not tools:
            return 0, 0
        raw = json.dumps(tools, ensure_ascii=False, default=str)
        return estimate_tokens(raw), len(raw)

    @staticmethod
    def _message_sections(messages: list[dict[str, Any]]) -> list[BudgetSection]:
        buckets: dict[str, list[dict[str, Any]]] = {
            "system": [],
            "runtime": [],
            "memory": [],
            "working_state": [],
            "history": [],
            "tool_observation": [],
        }
        for message in messages:
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role == "tool":
                buckets["tool_observation"].append(message)
            elif role == "system":
                name = _system_bucket(content)
                buckets[name].append(message)
            else:
                buckets["history"].append(message)
        sections: list[BudgetSection] = []
        for name in ("system", "runtime", "memory", "working_state", "history", "tool_observation"):
            items = buckets[name]
            if not items:
                continue
            chars = sum(len(str(item.get("content") or "")) for item in items)
            sections.append(
                BudgetSection(
                    name=name,
                    tokens=estimate_messages_tokens(items),
                    chars=chars,
                    count=len(items),
                )
            )
        return sections

    @staticmethod
    def _largest_messages(messages: list[dict[str, Any]], *, limit: int = 10) -> list[LargeMessage]:
        out: list[LargeMessage] = []
        for index, message in enumerate(messages):
            content = str(message.get("content") or "")
            token_count = estimate_tokens(content)
            label = ""
            if message.get("role") == "tool":
                label = str(message.get("name") or "")
            elif isinstance(message.get("tool_calls"), list):
                label = "tool_calls:" + ",".join(
                    str((call.get("function") or {}).get("name") or "")
                    for call in message["tool_calls"][:4]
                    if isinstance(call, dict)
                )
            elif message.get("role") == "system":
                label = _first_heading(content)
            out.append(
                LargeMessage(
                    index=index,
                    role=str(message.get("role") or ""),
                    tokens=token_count,
                    chars=len(content),
                    label=label,
                    preview=_compact(content, 240),
                )
            )
        out.sort(key=lambda item: item.tokens, reverse=True)
        return out[:limit]


def _system_bucket(content: str) -> str:
    if "Retrieved Project Memory" in content or "Learned Failure Lessons" in content:
        return "memory"
    if "Current Turn Objective" in content or "Runner " in content or "Active Function Tools" in content:
        return "runtime"
    if "Working State" in content:
        return "working_state"
    if "Runtime State Anchors" in content or "Recent User Requests" in content:
        return "working_state"
    return "system"


def _first_heading(content: str) -> str:
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            return stripped[3:].strip()
        if stripped:
            return stripped[:80]
    return ""


def _compact(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    head = limit // 2
    tail = max(0, limit - head - 24)
    return compact[:head] + " ... [omitted] ... " + compact[-tail:]


__all__ = [
    "BudgetLimits",
    "BudgetSection",
    "LargeMessage",
    "RequestBudgetManager",
    "RequestBudgetReport",
]
