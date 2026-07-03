"""bash skill — execute shell commands (OpenCode-style).

Executes a shell command in the workspace and returns stdout/stderr
with timeout and output truncation.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from opengis_backend.skills.registry import skill

logger = logging.getLogger(__name__)

# 危险命令关键词 — 拒绝执行
_DANGEROUS_PATTERNS = [
    "rm -rf /",
    "mkfs",
    ":(){ :|:& };",   # fork bomb
    "dd if=",
]

_MAX_OUTPUT_BYTES = 50 * 1024   # 50 KB
_DEFAULT_TIMEOUT_MS = 120_000   # 2 minutes


def _is_dangerous(cmd: str) -> bool:
    lower = cmd.lower().strip()
    return any(p in lower for p in _DANGEROUS_PATTERNS)


def _bash_sync(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT_MS,
    workdir: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Synchronous bash execution (runs in executor)."""
    if _is_dangerous(command):
        return {
            "output": f"Refused to execute potentially dangerous command: {command}",
            "exit_code": -1,
            "description": description,
            "truncated": False,
            "error": "dangerous_command",
        }

    cwd = workdir or os.getcwd()
    try:
        # Use shell=True to support pipes, redirects, globbing, and chaining.
        # The _is_dangerous() check above blocks the worst injection vectors.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout / 1000,   # ms → s
            cwd=cwd,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n[stderr]\n"
            output += result.stderr

        truncated = len(output) > _MAX_OUTPUT_BYTES
        if truncated:
            output = (
                output[:_MAX_OUTPUT_BYTES]
                + f"\n... (output truncated, total {len(output)} chars)"
            )

        return {
            "output": output,
            "exit_code": result.returncode,
            "description": description,
            "truncated": truncated,
        }

    except subprocess.TimeoutExpired:
        return {
            "output": f"Command timed out after {timeout}ms",
            "exit_code": -1,
            "description": description,
            "truncated": False,
            "error": "timeout",
        }
    except Exception as e:
        logger.error("[bash] execution failed: %s", e)
        return {
            "output": f"Error executing command: {e}",
            "exit_code": -1,
            "description": description,
            "truncated": False,
            "error": str(e),
        }


@skill(
    name="bash",
    display_name="Execute Shell Command",
    description=(
        "Execute a shell command in the workspace. "
        "Use this for git, pip, npm, or any command-line tool. "
        "Prefer this over multiple separate commands; chain with && or ; when possible. "
        "Do NOT use 'cd' — use the workdir parameter instead."
    ),
    category="system",
    params=[
        {"name": "command", "type": "string", "description": "The shell command to execute."},
        {"name": "timeout", "type": "number", "description": "Optional timeout in milliseconds (default 120000)."},
        {"name": "workdir", "type": "string", "description": "Working directory (default current). Use this instead of 'cd'."},
        {"name": "description", "type": "string", "description": "Clear description of what this command does (5-10 words)."},
    ],
    returns="dict with keys: output (str), exit_code (int), truncated (bool), error (str|null)",
    examples=[
        "bash('git status')",
        "bash('pip list', workdir='/workspace')",
    ],
)
def bash(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT_MS,
    workdir: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Execute a shell command.

    NOTE: Synchronous on purpose — invoked from a worker thread with no
    event loop. See docs/ARCHITECTURE.md §Skill Invocation.
    """
    return _bash_sync(command, timeout, workdir, description)
