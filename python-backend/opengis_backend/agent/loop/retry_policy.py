"""Retry policy for transient LLM provider failures."""

from __future__ import annotations

LLM_MAX_RETRIES = 3
LLM_BASE_DELAY = 1.0
LLM_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

try:
    from litellm.exceptions import (
        APIConnectionError as LiteLLMConnectionError,
        InternalServerError as LiteLLMInternalError,
        RateLimitError as LiteLLMRateLimitError,
        ServiceUnavailableError as LiteLLMServiceUnavailable,
        BadGatewayError as LiteLLMBadGateway,
        APIError as LiteLLMAPIError,
    )

    LLM_RETRYABLE_EXCEPTIONS = (
        *LLM_RETRYABLE_EXCEPTIONS,
        LiteLLMConnectionError,
        LiteLLMInternalError,
        LiteLLMRateLimitError,
        LiteLLMServiceUnavailable,
        LiteLLMBadGateway,
        LiteLLMAPIError,
    )
except ImportError:
    pass

try:
    import httpx

    LLM_RETRYABLE_EXCEPTIONS = (
        *LLM_RETRYABLE_EXCEPTIONS,
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        httpx.RemoteProtocolError,
    )
except ImportError:
    pass

try:
    from openai import APIConnectionError, APITimeoutError, RateLimitError

    LLM_RETRYABLE_EXCEPTIONS = (
        *LLM_RETRYABLE_EXCEPTIONS,
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
    )
except ImportError:
    pass


__all__ = ["LLM_BASE_DELAY", "LLM_MAX_RETRIES", "LLM_RETRYABLE_EXCEPTIONS"]
