"""Shared agent loop data types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CodeExecResult:
    """Result of executing Python through the subprocess executor."""

    output: Any = None
    logs: str = ""
    error: str | None = None


@dataclass
class AgentStep:
    """One observable step in an agent or workflow run."""

    step_num: int
    thought: str = ""
    code: str = ""
    output: str = ""
    error: str | None = None
    is_text_reply: bool = False
    text_reply: str = ""
    duration_ms: float = 0.0


__all__ = ["AgentStep", "CodeExecResult"]
