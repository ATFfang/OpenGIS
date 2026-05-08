"""LLM configuration + model factory — decoupled from the agent core.

This module owns *everything* about "which LLM to talk to":

- ``LLMConfig`` — a small dataclass carrying the user-configurable knobs
  (model id, api key, base url). This is the currency the
  rest of the agent layer should pass around instead of three loose strings.
- ``build_llm_caller(config)`` — the single factory that turns an
  ``LLMConfig`` into a callable ``(messages, *, on_delta=None) -> str``.

v3.1 (2026-04): Removed smolagents dependency. Now uses litellm directly
for all LLM calls.

v3.3 (2026-04): Added streaming support. The returned caller now
accepts an optional ``on_delta(text)`` callback. When supplied, the
LLM is invoked with ``stream=True`` and each content delta is forwarded
to the callback as it arrives. The function still returns the full
assembled text so existing call sites stay backward-compatible.

v3.4 (2026-04): Removed ``api_format`` entirely. The model name is
expected to carry the provider prefix (e.g. ``"openai/gpt-4o"``,
``"anthropic/claude-3"``, ``"deepseek/deepseek-chat"``). litellm
infers the wire format from the prefix. No model-name inspection or
format guessing happens in this module — the frontend/settings layer is
responsible for populating the model field correctly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import litellm

logger = logging.getLogger(__name__)

# Default timeout for LLM API calls (seconds). Prevents indefinite hangs
# when the provider is slow or unresponsive. 5 minutes is generous enough
# for complex reasoning while still catching stuck connections.
DEFAULT_LLM_TIMEOUT_SEC: float = 300.0


# ──────────────────────────────────────────────────────────────────
# Config — no api_format, just provider/model + base_url
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMConfig:
    """User-configurable LLM knobs.

    Immutable by design — a single ``LLMConfig`` represents one frozen
    decision about *which* LLM we're about to call.

    ``protocol`` determines the API wire format:
      - ``"openai"``   → OpenAI-style /chat/completions
      - ``"anthropic"`` → Anthropic-style /v1/messages

    ``model`` is the model name as expected by the provider.
    If no provider prefix is present, the ``protocol`` value is prepended
    automatically (e.g. ``"gpt-4o"`` → ``"openai/gpt-4o"``).

    ``base_url`` overrides the default endpoint for the protocol.
    Leave empty to use litellm's default routing.
    """

    protocol: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""


# ──────────────────────────────────────────────────────────────────
# Routing — trivial pass-through (litellm owns provider inference)
# ──────────────────────────────────────────────────────────────────


def _resolve_llm_route(model: str, user_base_url: str, protocol: str = "openai") -> tuple[str, str]:
    """Normalize model name and base URL.

    Generic — uses ``protocol`` to ensure the correct provider
    prefix is present on the model name.

    Rules:
      - If ``protocol`` is ``"openai"`` and model lacks a prefix,
        prepend ``"openai/"``.
      - If ``protocol`` is ``"anthropic"`` and model lacks a prefix,
        prepend ``"anthropic/"``.
      - If model already has a prefix (e.g. ``"deepseek/..."``),
        leave it unchanged — the user explicitly set it.

    litellm uses the prefix to select the correct wire format.
    """
    model = (model or "").strip()
    base = (user_base_url or "").strip()

    # Ensure the model has the correct provider prefix for litellm.
    prefix = protocol.lower() + "/"
    if prefix not in model:
        # Don't add prefix if user already included one.
        if "/" not in model:
            model = prefix + model

    return model, base


# ──────────────────────────────────────────────────────────────────
# Streaming chunk extraction
# ──────────────────────────────────────────────────────────────────


def _extract_content_delta(chunk: Any) -> str:
    """Best-effort: pull out a textual content delta from a litellm chunk.

    Different providers shape their chunks differently — OpenAI puts
    text under ``choices[0].delta.content``, Anthropic-style providers
    sometimes nest it inside a ``content_block_delta`` etc. We try the
    common shapes and quietly return ``""`` when nothing is present
    (e.g. role-only opener, finish chunk, thinking-only delta).
    """
    try:
        choices = getattr(chunk, "choices", None) or chunk.get("choices")  # type: ignore[union-attr]
        if not choices:
            return ""
        first = choices[0]
        delta = getattr(first, "delta", None) or first.get("delta")  # type: ignore[union-attr]
        if delta is None:
            return ""
        content = getattr(delta, "content", None)
        if content is None and isinstance(delta, dict):
            content = delta.get("content")
        if isinstance(content, str):
            return content
        # Anthropic-style: content can be a list of blocks.
        if isinstance(content, list):
            out: list[str] = []
            for block in content:
                t = (
                    getattr(block, "text", None)
                    if not isinstance(block, dict)
                    else block.get("text")
                )
                if isinstance(t, str):
                    out.append(t)
            return "".join(out)
    except Exception:
        logger.debug("_extract_content_delta: chunk shape not recognised", exc_info=True)
    return ""


# ──────────────────────────────────────────────────────────────────
# Factory — direct litellm caller (no smolagents dependency)
# ──────────────────────────────────────────────────────────────────
LLMCaller = Callable[..., str]


def build_llm_caller(config: LLMConfig) -> LLMCaller:
    """Build a callable ``(messages, *, on_delta=None) -> str``.

    When ``on_delta`` is supplied the call uses ``stream=True`` and
    forwards each text delta to the callback as it arrives. The full
    assembled response is still returned so callers that don't care
    about streaming behave exactly as before.

    The returned callable is synchronous (blocking) — designed to be
    called from the agent loop's worker thread.
    """
    resolved_model, resolved_base = _resolve_llm_route(
        config.model, config.base_url, config.protocol
    )
    api_key = config.api_key or None
    api_base = resolved_base or None

    def _call(
        messages: list[dict],
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "timeout": DEFAULT_LLM_TIMEOUT_SEC,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base

        # Non-streaming fast path — preserves old behaviour exactly.
        if on_delta is None:
            response = litellm.completion(**kwargs)
            choice = response.choices[0]
            return choice.message.content or ""

        # Streaming path.
        kwargs["stream"] = True
        try:
            stream = litellm.completion(**kwargs)
        except Exception:
            # If the provider rejects streaming (rare) fall back to a
            # blocking call so the agent run doesn't crash.
            logger.warning("streaming completion failed, retrying non-streaming", exc_info=True)
            kwargs.pop("stream", None)
            response = litellm.completion(**kwargs)
            full = response.choices[0].message.content or ""
            try:
                on_delta(full)
            except Exception:
                logger.exception("on_delta callback raised on fallback path")
            return full

        parts: list[str] = []
        for chunk in stream:
            piece = _extract_content_delta(chunk)
            if not piece:
                continue
            parts.append(piece)
            try:
                on_delta(piece)
            except Exception:
                # The callback must never break the LLM call.
                logger.exception("on_delta callback raised — continuing")
        return "".join(parts)

    return _call


# ──────────────────────────────────────────────────────────────────
# Legacy compatibility shim
# ──────────────────────────────────────────────────────────────────


def build_llm_model(config: LLMConfig) -> LLMCaller:
    """Legacy alias for ``build_llm_caller``."""
    return build_llm_caller(config)


__all__ = [
    "LLMConfig",
    "LLMCaller",
    "_resolve_llm_route",
    "build_llm_caller",
    "build_llm_model",
]
