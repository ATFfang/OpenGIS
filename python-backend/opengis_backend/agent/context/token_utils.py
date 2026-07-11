"""Token estimation and output truncation helpers for agent context."""

from __future__ import annotations

import re


CHARS_PER_TOKEN = 3.5
CHARS_PER_TOKEN_CJK = 1.5
CJK_PATTERN = re.compile(
    r"[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf\uac00-\ud7af\uf900-\ufaff]"
)


def estimate_tokens(text: str) -> int:
    """Rough token count estimate from character length."""
    if not text:
        return 0
    cjk_count = len(CJK_PATTERN.findall(text))
    other_count = len(text) - cjk_count
    return int(cjk_count / CHARS_PER_TOKEN_CJK + other_count / CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across a list of chat messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
        total += 4
    return total


def truncate_output(text: str, max_chars: int = 3000) -> str:
    """Truncate long output, keeping head and tail."""
    if len(text) <= max_chars:
        return text
    head_size = int(max_chars * 0.7)
    tail_size = max_chars - head_size
    truncated_count = len(text) - max_chars
    return (
        text[:head_size]
        + f"\n\n... [⚠️ output truncated: {truncated_count} chars omitted] ...\n\n"
        + text[-tail_size:]
    )


__all__ = [
    "CHARS_PER_TOKEN",
    "CHARS_PER_TOKEN_CJK",
    "CJK_PATTERN",
    "estimate_messages_tokens",
    "estimate_tokens",
    "truncate_output",
]
