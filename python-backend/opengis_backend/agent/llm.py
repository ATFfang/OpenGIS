"""LLM configuration + model factory — decoupled from the agent core.

This module owns *everything* about "which LLM to talk to":

- ``LLMConfig`` — a small dataclass carrying the user-configurable knobs
  (model id, api key, base url). This is the currency the
  rest of the agent layer should pass around instead of three loose strings.
- ``build_llm_caller(config)`` — the single factory that turns an
  ``LLMConfig`` into a callable returning ``LLMResponse``.

v3.3 (2026-04): Added streaming support. The returned caller accepts an
optional ``on_delta(text)`` callback. When supplied, the LLM is invoked
with ``stream=True`` and each content delta is forwarded to the callback
as it arrives. The function returns the assembled ``LLMResponse``.

v3.4 (2026-04): Removed ``api_format`` entirely. The model name is
expected to carry the provider prefix (e.g. ``"openai/gpt-4o"``,
``"anthropic/claude-3"``, ``"deepseek/deepseek-chat"``). litellm
infers the wire format from the prefix. No model-name inspection or
format guessing happens in this module — the frontend/settings layer is
responsible for populating the model field correctly.
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from collections.abc import Callable
from dataclasses import dataclass, field
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


@dataclass
class LLMResponse:
    """Structured response from an LLM call.

    Supports both pure text replies and tool_call responses.
    """
    content: str | None = None
    tool_calls: list[dict] | None = None
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "length"
    usage: dict[str, Any] | None = None
    prompt_cache: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_empty(self) -> bool:
        return not self.content and not self.tool_calls


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
# Tool call extraction
# ──────────────────────────────────────────────────────────────────


def _extract_tool_calls(message: Any) -> list[dict] | None:
    """Extract tool_calls from a litellm message object.

    Returns a list of dicts with keys: id, type, function{name, arguments}.
    Returns None if no tool_calls present.
    """
    raw = getattr(message, "tool_calls", None)
    if not raw:
        return None
    result = []
    for tc in raw:
        func = getattr(tc, "function", None)
        if func is None:
            continue
        result.append({
            "id": getattr(tc, "id", "") or "",
            "type": "function",
            "function": {
                "name": getattr(func, "name", "") or "",
                "arguments": getattr(func, "arguments", "{}") or "{}",
            },
        })
    return result if result else None


_XMLISH_TOOL_CALL_RE = re.compile(
    r"<tool_call\b[^>]*>(?P<body>.*?)</tool_call>",
    re.IGNORECASE | re.DOTALL,
)
_XMLISH_FUNCTION_RE = re.compile(
    r"<function\s*=\s*[\"']?(?P<name>[A-Za-z_][\w.-]*)[\"']?\s*>",
    re.IGNORECASE,
)
_XMLISH_PARAMETER_RE = re.compile(
    r"<parameter\s*=\s*[\"']?(?P<name>[A-Za-z_][\w.-]*)[\"']?\s*>"
    r"(?P<value>.*?)"
    r"</parameter>",
    re.IGNORECASE | re.DOTALL,
)


def _extract_xmlish_tool_calls(content: str | None) -> tuple[str | None, list[dict] | None]:
    """Normalize provider-emitted XML-ish tool calls into OpenAI tool_calls.

    Some OpenAI-compatible providers occasionally stream tool calls as plain
    text blocks such as ``<tool_call><function=execute_code>...`` instead of
    populating ``message.tool_calls``. Treating that text as an assistant reply
    leaks protocol markup into chat and prevents the tool from running. This
    adapter keeps the loop protocol clean by repairing the response at the LLM
    boundary.
    """
    if not content or "<tool_call" not in content.lower():
        return content, None

    parsed_calls: list[dict] = []
    parsed_index = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal parsed_index
        body = match.group("body") or ""
        function_match = _XMLISH_FUNCTION_RE.search(body)
        if not function_match:
            return match.group(0)

        function_name = function_match.group("name").strip()
        arguments: dict[str, Any] = {}
        for param_match in _XMLISH_PARAMETER_RE.finditer(body):
            param_name = param_match.group("name").strip()
            value = unescape(param_match.group("value")).strip()
            arguments[param_name] = value

        parsed_index += 1
        parsed_calls.append(
            {
                "id": f"xmlish_call_{parsed_index}",
                "type": "function",
                "function": {
                    "name": function_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
        return ""

    cleaned = _XMLISH_TOOL_CALL_RE.sub(_replace, content)
    if not parsed_calls:
        return content, None
    cleaned = cleaned.strip()
    return cleaned or None, parsed_calls


def _normalize_llm_message(
    content: str | None,
    tool_calls: list[dict] | None,
    *,
    finish_reason: str,
) -> LLMResponse:
    """Build an LLMResponse, repairing text-encoded tool calls if needed."""
    if tool_calls:
        return LLMResponse(content=content or None, tool_calls=tool_calls, finish_reason=finish_reason)
    _, xmlish_tool_calls = _extract_xmlish_tool_calls(content)
    if xmlish_tool_calls:
        logger.warning(
            "[LLM] Parsed %d XML-ish tool_call block(s) from assistant content.",
            len(xmlish_tool_calls),
        )
        return LLMResponse(
            content=None,
            tool_calls=xmlish_tool_calls,
            finish_reason="tool_calls",
        )
    return LLMResponse(content=content or None, tool_calls=None, finish_reason=finish_reason)


def _extract_partial_json_string_field(raw: str, field: str) -> str | None:
    """Best-effort extraction of a string field from partial JSON."""
    if not raw:
        return None
    marker = json.dumps(field)
    idx = raw.find(marker)
    if idx < 0:
        return None
    colon = raw.find(":", idx + len(marker))
    if colon < 0:
        return None
    pos = colon + 1
    while pos < len(raw) and raw[pos].isspace():
        pos += 1
    if pos >= len(raw) or raw[pos] != '"':
        return None
    pos += 1

    escaped: list[str] = []
    i = pos
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= len(raw):
                break
            escaped.append(ch)
            escaped.append(raw[i + 1])
            i += 2
            continue
        if ch == '"':
            break
        escaped.append(ch)
        i += 1

    try:
        return json.loads('"' + "".join(escaped) + '"')
    except Exception:
        return None


def _extract_finish_reason(choice: Any) -> str:
    """Extract finish_reason from a litellm choice object."""
    fr = getattr(choice, "finish_reason", None)
    if fr is None and isinstance(choice, dict):
        fr = choice.get("finish_reason")
    return str(fr or "stop")


def _usage_field(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(name)
    return value


def _to_plain_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_plain_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _to_plain_jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _to_plain_jsonable(value.dict())
        except Exception:
            pass
    try:
        return {
            key: _to_plain_jsonable(getattr(value, key))
            for key in dir(value)
            if not key.startswith("_") and not callable(getattr(value, key, None))
        }
    except Exception:
        return str(value)


def _extract_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}

    plain = _to_plain_jsonable(usage)
    out: dict[str, Any] = dict(plain) if isinstance(plain, dict) else {}
    prompt = int(_usage_field(usage, "prompt_tokens") or _usage_field(usage, "input_tokens") or 0)
    completion = int(_usage_field(usage, "completion_tokens") or _usage_field(usage, "output_tokens") or 0)
    total = int(_usage_field(usage, "total_tokens") or 0)

    cached = 0
    details = _usage_field(usage, "prompt_tokens_details")
    if details is not None:
        cached = int(_usage_field(details, "cached_tokens") or 0)
    input_details = _usage_field(usage, "input_tokens_details") or _usage_field(usage, "input_token_details")
    if input_details is not None:
        cached = max(cached, int(_usage_field(input_details, "cached_tokens") or _usage_field(input_details, "cache_read") or 0))
    cache_read = int(_usage_field(usage, "cache_read_input_tokens") or 0)
    cache_creation = int(_usage_field(usage, "cache_creation_input_tokens") or 0)
    if cache_read:
        cached = max(cached, cache_read)

    out["prompt_tokens"] = prompt
    out["completion_tokens"] = completion
    out["total_tokens"] = total
    out["cached_tokens"] = cached
    out["cache_read_input_tokens"] = cache_read
    out["cache_creation_input_tokens"] = cache_creation
    return out


def _provider_cache_mode(model: str, api_base: str | None) -> str:
    route = f"{model or ''} {api_base or ''}".lower()
    if "deepseek" in route:
        return "deepseek_automatic"
    if "anthropic" in route or "claude" in route:
        return "anthropic_cache_control"
    if "openai" in route or model.startswith("openai/"):
        return "openai_automatic"
    return "provider_reported"


def _build_prompt_cache_info(
    *,
    metadata: dict[str, Any] | None,
    model: str,
    api_base: str | None,
) -> dict[str, Any]:
    info = dict(metadata or {})
    mode = _provider_cache_mode(model, api_base)
    info.setdefault("provider_cache_mode", mode)
    if mode == "deepseek_automatic":
        info.setdefault("prompt_cache_key_sent", False)
        info.setdefault("prompt_cache_key_status", "automatic_provider_cache")
        info.setdefault("provider_cache_note", "deepseek_automatic_cache_no_request_hint")
    else:
        info.setdefault("prompt_cache_key_sent", False)
        info.setdefault("prompt_cache_key_status", "observe_only")
        info.setdefault("provider_cache_note", "provider_usage_accounting_only")
    return info


# ──────────────────────────────────────────────────────────────────
# Factory — direct litellm caller
# ──────────────────────────────────────────────────────────────────
LLMCaller = Callable[..., LLMResponse]


def build_llm_caller(config: LLMConfig) -> LLMCaller:
    """Build a callable ``(messages, *, on_delta=None, tools=None) -> LLMResponse``.

    When ``tools`` is supplied, the LLM may return tool_calls instead of
    (or in addition to) text content. The caller should check
    ``response.has_tool_calls`` to decide the next action.

    When ``on_delta`` is supplied the call uses ``stream=True`` and
    forwards each text delta to the callback as it arrives.

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
        on_tool_delta: Callable[[int, str, dict[str, Any]], None] | None = None,
        tools: list[dict] | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "timeout": DEFAULT_LLM_TIMEOUT_SEC,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        if tools:
            kwargs["tools"] = tools
            logger.info("[LLM] Passing %d tools to LLM: %s", len(tools), [t.get("function", {}).get("name", "?") for t in tools[:10]])
        prompt_cache_info = _build_prompt_cache_info(
            metadata=prompt_cache_metadata,
            model=resolved_model,
            api_base=api_base,
        )
        if prompt_cache_key:
            prompt_cache_info.setdefault("cache_key", prompt_cache_key)

        # Non-streaming fast path
        if on_delta is None:
            response = litellm.completion(**kwargs)
            choice = response.choices[0]
            msg = choice.message
            out = _normalize_llm_message(
                msg.content or None,
                _extract_tool_calls(msg),
                finish_reason=_extract_finish_reason(choice),
            )
            out.usage = _extract_usage(response)
            out.prompt_cache = prompt_cache_info
            return out

        # Streaming path.
        kwargs["stream"] = True
        kwargs.setdefault("stream_options", {"include_usage": True})
        try:
            stream = litellm.completion(**kwargs)
        except Exception:
            logger.warning("streaming completion with usage failed, retrying streaming without usage metadata", exc_info=True)
            kwargs.pop("stream_options", None)
            try:
                stream = litellm.completion(**kwargs)
            except Exception:
                logger.warning("streaming completion failed, retrying non-streaming", exc_info=True)
                kwargs.pop("stream", None)
                response = litellm.completion(**kwargs)
                choice = response.choices[0]
                msg = choice.message
                full = msg.content or None
                repaired = _normalize_llm_message(
                    full,
                    _extract_tool_calls(msg),
                    finish_reason=_extract_finish_reason(choice),
                )
                repaired.usage = _extract_usage(response)
                repaired.prompt_cache = prompt_cache_info
                if repaired.content:
                    try:
                        on_delta(repaired.content)
                    except Exception:
                        logger.exception("on_delta callback raised on fallback path")
                return repaired

        # Collect streaming chunks
        parts: list[str] = []
        # For streaming tool_calls, we need to collect them incrementally
        streaming_tool_calls: dict[int, dict] = {}  # index -> {id, name, arguments_parts}
        finish_reason = "stop"
        stream_usage: dict[str, Any] = {}

        for chunk in stream:
            chunk_usage = _extract_usage(chunk)
            if chunk_usage.get("prompt_tokens") or chunk_usage.get("total_tokens"):
                stream_usage = chunk_usage
            choice = getattr(chunk, "choices", [None])[0] if hasattr(chunk, "choices") else None
            if choice is None:
                continue

            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            # Collect content delta
            piece = _extract_content_delta(chunk)
            if piece:
                parts.append(piece)
                try:
                    on_delta(piece)
                except Exception:
                    logger.exception("on_delta callback raised — continuing")

            # Collect tool_calls delta
            tc_delta = getattr(delta, "tool_calls", None)
            if tc_delta:
                for tc in tc_delta:
                    idx = getattr(tc, "index", 0) or 0
                    if idx not in streaming_tool_calls:
                        streaming_tool_calls[idx] = {
                            "id": getattr(tc, "id", "") or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    entry = streaming_tool_calls[idx]
                    # Update id if provided
                    tc_id = getattr(tc, "id", None)
                    if tc_id:
                        entry["id"] = tc_id
                    # Update function name
                    func = getattr(tc, "function", None)
                    if func:
                        name = getattr(func, "name", None)
                        if name:
                            entry["function"]["name"] = name
                        args = getattr(func, "arguments", None)
                        if args:
                            entry["function"]["arguments"] += args
                        if on_tool_delta:
                            tool_name = entry["function"].get("name", "")
                            payload: dict[str, Any] = {
                                "arguments": entry["function"]["arguments"],
                            }
                            if tool_name == "execute_code":
                                code = _extract_partial_json_string_field(
                                    entry["function"]["arguments"],
                                    "code",
                                )
                                if code is not None:
                                    payload["code"] = code
                            try:
                                on_tool_delta(idx, tool_name, payload)
                            except Exception:
                                logger.exception("on_tool_delta callback raised — continuing")

            # Track finish reason
            fr = getattr(choice, "finish_reason", None)
            if fr:
                finish_reason = str(fr)

        # Build final response
        content = "".join(parts) or None
        tool_calls = None
        if streaming_tool_calls:
            tool_calls = []
            for idx in sorted(streaming_tool_calls.keys()):
                entry = streaming_tool_calls[idx]
                # Try to parse accumulated arguments
                try:
                    json.loads(entry["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Malformed tool_call arguments: %s", entry["function"]["arguments"][:200])
                tool_calls.append(entry)
            logger.info("[LLM] Streaming collected %d tool_calls: %s",
                       len(tool_calls),
                       [tc.get("function", {}).get("name", "?") for tc in tool_calls])

        response = _normalize_llm_message(
            content,
            tool_calls,
            finish_reason=finish_reason,
        )
        response.usage = stream_usage
        response.prompt_cache = prompt_cache_info

        logger.info("[LLM] Response: content_len=%d, tool_calls=%d, finish_reason=%s, cached_tokens=%d/%d",
                   len(response.content) if response.content else 0,
                   len(response.tool_calls) if response.tool_calls else 0,
                   response.finish_reason,
                   stream_usage.get("cached_tokens", 0),
                   stream_usage.get("prompt_tokens", 0))

        return response

    setattr(_call, "opengis_model", resolved_model)
    setattr(_call, "opengis_provider", config.protocol or "")
    setattr(_call, "opengis_base_url", api_base or "")
    return _call


__all__ = [
    "LLMConfig",
    "LLMResponse",
    "LLMCaller",
    "_resolve_llm_route",
    "build_llm_caller",
]
