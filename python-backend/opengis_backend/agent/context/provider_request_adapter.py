"""Provider request adaptation for prompt-caching-aware LLM calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from opengis_backend.agent.context.provider_request import ProviderRequest
from opengis_backend.agent.context.token_utils import estimate_tokens


@dataclass(frozen=True)
class PromptCachePlan:
    """Provider-neutral cache hints derived from prompt sections."""

    enabled: bool
    cache_key: str = ""
    strategy: str = "none"
    prefix_hash: str = ""
    breakpoints: list[str] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    cacheable_prefix_tokens: int = 0
    cacheable_prefix_ratio: float = 0.0
    tool_schema_count: int = 0
    tool_schema_tokens: int = 0
    tool_schema_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "cache_key": self.cache_key,
            "strategy": self.strategy,
            "prefix_hash": self.prefix_hash,
            "breakpoints": list(self.breakpoints),
            "sections": list(self.sections),
            "cacheable_prefix_tokens": self.cacheable_prefix_tokens,
            "cacheable_prefix_ratio": self.cacheable_prefix_ratio,
            "tool_schema_count": self.tool_schema_count,
            "tool_schema_tokens": self.tool_schema_tokens,
            "tool_schema_hash": self.tool_schema_hash,
        }


class ProviderRequestAdapter:
    """Convert sectioned requests into provider-neutral call hints.

    Stage 2 intentionally keeps the provider messages unchanged.  The adapter
    only emits cache metadata and a stable cache key. Provider-specific request
    shape changes, such as Anthropic block-level ``cache_control``, should build
    on this instead of being mixed into ContextManager.
    """

    def __init__(self, provider_request: ProviderRequest) -> None:
        self.provider_request = provider_request

    def prompt_cache_plan(
        self,
        *,
        model: str = "",
        provider: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> PromptCachePlan:
        prefix_hash = self.provider_request.cacheable_prefix_hash
        sections = self.provider_request.section_debug()
        cacheable_prefix_tokens = _cacheable_prefix_tokens(sections)
        request_tokens = max(1, sum(int(section.get("token_estimate") or 0) for section in sections))
        tool_schema_tokens, tool_schema_hash = _tool_schema_fingerprint(tools or [])
        breakpoints = [
            section["id"]
            for section in sections
            if section.get("cache_policy") == "breakpoint"
        ]
        has_cacheable = any(
            section.get("cache_policy") in {"cacheable", "breakpoint"}
            for section in sections
        )
        if not has_cacheable:
            return PromptCachePlan(
                enabled=False,
                strategy="none",
                sections=sections,
                cacheable_prefix_tokens=cacheable_prefix_tokens,
                cacheable_prefix_ratio=cacheable_prefix_tokens / request_tokens,
                tool_schema_count=len(tools or []),
                tool_schema_tokens=tool_schema_tokens,
                tool_schema_hash=tool_schema_hash,
            )
        key_parts = [
            "opengis",
            provider or "provider",
            model or "model",
            prefix_hash[:24],
        ]
        return PromptCachePlan(
            enabled=True,
            cache_key=":".join(_safe_key_part(part) for part in key_parts),
            strategy="section_prefix",
            prefix_hash=prefix_hash,
            breakpoints=breakpoints,
            sections=sections,
            cacheable_prefix_tokens=cacheable_prefix_tokens,
            cacheable_prefix_ratio=cacheable_prefix_tokens / request_tokens,
            tool_schema_count=len(tools or []),
            tool_schema_tokens=tool_schema_tokens,
            tool_schema_hash=tool_schema_hash,
        )


def _safe_key_part(value: str) -> str:
    out = []
    for ch in str(value or ""):
        if ch.isalnum() or ch in {"-", "_", ".", ":"}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)[:96] or "empty"


def _cacheable_prefix_tokens(sections: list[dict[str, Any]]) -> int:
    total = 0
    for section in sections:
        if section.get("cache_policy") not in {"cacheable", "breakpoint"}:
            break
        try:
            total += int(section.get("token_estimate") or 0)
        except Exception:
            pass
    return total


def _tool_schema_fingerprint(tools: list[dict[str, Any]]) -> tuple[int, str]:
    if not tools:
        return 0, ""
    try:
        raw = json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(tools)
    return estimate_tokens(raw), hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["PromptCachePlan", "ProviderRequestAdapter"]
