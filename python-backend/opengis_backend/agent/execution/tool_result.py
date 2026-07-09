"""Normalized tool execution result model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ToolExecutionResult:
    """Normalized result of one function-call tool execution."""

    name: str
    arguments: dict[str, Any]
    content: str
    error: Optional[str] = None
    duration_ms: float = 0.0
    title: str = ""
    metadata: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


__all__ = ["ToolExecutionResult"]
