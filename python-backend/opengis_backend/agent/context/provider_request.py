"""Provider request assembly with prompt-section metadata.

This module is the first boundary for provider-level prompt caching.  It keeps
the actual chat messages behavior-compatible while preserving enough section
metadata to later split stable prefixes from per-turn dynamic suffixes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from opengis_backend.agent.context.token_utils import estimate_messages_tokens

PromptSectionKind = Literal[
    "system",
    "capability_manifest",
    "tool_protocol",
    "user_preferences",
    "conversation_summary",
    "runtime",
    "memory",
    "working_state",
    "history",
    "tool_observation",
]
PromptStability = Literal["static", "workspace_static", "session_static", "turn_dynamic"]
PromptCachePolicy = Literal["none", "cacheable", "breakpoint"]


@dataclass(frozen=True)
class PromptSection:
    """A logical prompt/request section.

    The provider still receives ordinary chat messages.  Section metadata is
    runner-facing: it lets request budgeting, prompt-cache planning, and debug
    UI reason about which parts are stable and which are turn-local.
    """

    id: str
    kind: PromptSectionKind
    messages: list[dict[str, Any]]
    stability: PromptStability = "turn_dynamic"
    cache_policy: PromptCachePolicy = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return estimate_messages_tokens(self.messages)

    @property
    def char_count(self) -> int:
        total = 0
        for message in self.messages:
            total += len(str(message.get("content") or ""))
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                total += len(json.dumps(tool_calls, ensure_ascii=False, default=str))
        return total

    @property
    def digest(self) -> str:
        raw = json.dumps(self.messages, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "stability": self.stability,
            "cache_policy": self.cache_policy,
            "message_count": len(self.messages),
            "token_estimate": self.token_estimate,
            "char_count": self.char_count,
            "digest": self.digest,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class ProviderRequest:
    """Provider-facing messages plus prompt-section metadata."""

    messages: list[dict[str, Any]]
    sections: list[PromptSection]

    @property
    def token_estimate(self) -> int:
        return estimate_messages_tokens(self.messages)

    @property
    def cacheable_prefix_hash(self) -> str:
        """Hash contiguous cacheable/breakpoint sections from the top."""
        cacheable: list[dict[str, Any]] = []
        for section in self.sections:
            if section.cache_policy not in {"cacheable", "breakpoint"}:
                break
            cacheable.append(
                {
                    "id": section.id,
                    "kind": section.kind,
                    "stability": section.stability,
                    "digest": section.digest,
                }
            )
        raw = json.dumps(cacheable, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def system_prefix_hash(self) -> str:
        """Hash of the stable system prefix (all sections before conversation history).

        This is the canonical-layout STABLE PREFIX minus the append-only history.
        In a healthy setup it must stay constant turn-to-turn; any change points at
        dynamic content leaking into the prefix (see docs 3.1.2 / 3.2).
        """
        prefix: list[dict[str, Any]] = []
        for section in self.sections:
            if section.kind == "history":
                break
            prefix.append({"id": section.id, "kind": section.kind, "digest": section.digest})
        raw = json.dumps(prefix, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def dynamic_suffix_hash(self) -> str:
        """Hash of the DYNAMIC TAIL (all sections after the last history section)."""
        last_history = -1
        for index, section in enumerate(self.sections):
            if section.kind == "history":
                last_history = index
        tail = self.sections[last_history + 1:] if last_history >= 0 else []
        raw = json.dumps(
            [{"id": s.id, "kind": s.kind, "digest": s.digest} for s in tail],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def section_debug(self) -> list[dict[str, Any]]:
        return [section.to_debug_dict() for section in self.sections]


class ProviderRequestBuilder:
    """Incrementally assemble a provider request from logical sections."""

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []

    def add_section(
        self,
        *,
        id: str,
        kind: PromptSectionKind,
        messages: list[dict[str, Any]],
        stability: PromptStability = "turn_dynamic",
        cache_policy: PromptCachePolicy = "none",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        clean_messages = [dict(message) for message in messages if message]
        if not clean_messages:
            return
        self._sections.append(
            PromptSection(
                id=id,
                kind=kind,
                messages=clean_messages,
                stability=stability,
                cache_policy=cache_policy,
                metadata=dict(metadata or {}),
            )
        )

    def add_system_text(
        self,
        *,
        id: str,
        kind: PromptSectionKind,
        content: str,
        stability: PromptStability = "turn_dynamic",
        cache_policy: PromptCachePolicy = "none",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not content or not content.strip():
            return
        self.add_section(
            id=id,
            kind=kind,
            messages=[{"role": "system", "content": content}],
            stability=stability,
            cache_policy=cache_policy,
            metadata=metadata,
        )

    def build(self) -> ProviderRequest:
        messages: list[dict[str, Any]] = []
        for section in self._sections:
            messages.extend(dict(message) for message in section.messages)
        return ProviderRequest(messages=messages, sections=list(self._sections))

    @staticmethod
    def with_system_inserts_after_first(
        request: ProviderRequest,
        inserts: list[tuple[str, str, PromptSectionKind]] | list[str],
    ) -> ProviderRequest:
        """Insert runtime system sections after the stable cacheable prefix.

        Runtime inserts are turn-dynamic, so placing them immediately after
        ``system.base`` breaks the contiguous cacheable prefix and prevents
        later stable sections such as ``system.workspace`` from participating
        in prompt-cache planning.
        """
        normalized: list[PromptSection] = []
        for index, item in enumerate(inserts):
            if isinstance(item, tuple):
                section_id, content, kind = item
            else:
                section_id, content, kind = f"runtime_insert_{index + 1}", str(item), "runtime"
            if not content:
                continue
            is_active_tools = section_id == "runtime.active_tools"
            normalized.append(
                PromptSection(
                    id=section_id,
                    kind=kind,
                    messages=[{"role": "system", "content": content}],
                    stability="turn_dynamic",
                    cache_policy="breakpoint" if is_active_tools else "none",
                )
            )
        if not normalized:
            return request
        if not request.sections:
            builder = ProviderRequestBuilder()
            for section in normalized:
                builder.add_section(
                    id=section.id,
                    kind=section.kind,
                    messages=section.messages,
                    stability=section.stability,
                    cache_policy=section.cache_policy,
                    metadata=section.metadata,
                )
            return builder.build()
        insert_at = 0
        for section in request.sections:
            if section.cache_policy not in {"cacheable", "breakpoint"}:
                break
            insert_at += 1
        if insert_at <= 0:
            insert_at = 1
        sections = [*request.sections[:insert_at], *normalized, *request.sections[insert_at:]]
        messages: list[dict[str, Any]] = []
        for section in sections:
            messages.extend(dict(message) for message in section.messages)
        return ProviderRequest(messages=messages, sections=sections)


__all__ = [
    "PromptSection",
    "ProviderRequest",
    "ProviderRequestBuilder",
]
