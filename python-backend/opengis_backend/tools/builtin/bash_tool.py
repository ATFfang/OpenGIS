"""bash tool — execute shell commands (OpenCode-style).

Executes a shell command in the workspace and returns stdout/stderr
with timeout and output truncation.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from opengis_backend.tools.registry import tool

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
_MAX_TIMEOUT_MS = 10 * 60 * 1000
_MUTATING_COMMANDS = {
    "rm", "mv", "cp", "mkdir", "touch", "chmod", "chown", "truncate",
    "git", "npm", "pip", "python", "python3", "node", "tee",
}
_READONLY_COMMANDS = {
    "ls", "pwd", "cat", "sed", "awk", "head", "tail", "find", "rg",
    "grep", "git status", "git diff", "git log",
}


def _is_dangerous(cmd: str) -> bool:
    lower = cmd.lower().strip()
    if any(p in lower for p in _DANGEROUS_PATTERNS):
        return True
    if re.search(r"\brm\s+-[^\n;|&]*[rf][^\n;|&]*\s+/(?:\s|$)", lower):
        return True
    if re.search(r">\s*/dev/(?:sd|disk|rdisk)", lower):
        return True
    return False


def _split_shell_segments(command: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command) if part.strip()]


def _parse_command(command: str, cwd: str) -> dict[str, Any]:
    segments = _split_shell_segments(command)
    commands: list[str] = []
    external_paths: set[str] = set()
    warnings: list[str] = []
    mutates_files = False
    parse_errors: list[str] = []

    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=os.name != "nt")
        except ValueError as e:
            parse_errors.append(str(e))
            tokens = segment.split()
        if not tokens:
            continue
        cmd = tokens[0]
        commands.append(cmd)
        joined = " ".join(tokens[:2])
        if cmd in _MUTATING_COMMANDS and joined not in _READONLY_COMMANDS:
            mutates_files = True
        if re.search(r"(^|[^<])>\s*[^>]", segment) or ">>" in segment:
            mutates_files = True
        for token in tokens[1:]:
            cleaned = token.rstrip(",;")
            if not cleaned.startswith("/"):
                continue
            try:
                resolved = Path(cleaned).expanduser().resolve()
                cwd_path = Path(cwd).expanduser().resolve()
                if not str(resolved).startswith(str(cwd_path)):
                    external_paths.add(str(resolved))
            except Exception:
                continue

    if external_paths:
        warnings.append(
            "Command references external absolute paths: "
            + ", ".join(sorted(external_paths)[:5])
        )
    if mutates_files:
        warnings.append("Command appears to mutate files or environment; prefer dedicated file tools when possible.")
    if parse_errors:
        warnings.append("Shell parse warnings: " + "; ".join(parse_errors[:3]))

    return {
        "commands": commands,
        "external_paths": sorted(external_paths),
        "mutates_files": mutates_files,
        "warnings": warnings,
        "parse_errors": parse_errors,
    }


def _bash_sync(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT_MS,
    workdir: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Synchronous bash execution (runs in executor)."""
    timeout = min(int(timeout or _DEFAULT_TIMEOUT_MS), _MAX_TIMEOUT_MS)
    cwd = workdir or os.getcwd()
    parsed = _parse_command(command, cwd)
    if _is_dangerous(command):
        return {
            "output": f"Refused to execute potentially dangerous command: {command}",
            "exit_code": -1,
            "description": description,
            "truncated": False,
            "error": "dangerous_command",
            "warnings": parsed["warnings"],
            "parsed": parsed,
        }

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
            "warnings": parsed["warnings"],
            "parsed": parsed,
        }

    except subprocess.TimeoutExpired:
        return {
            "output": f"Command timed out after {timeout}ms",
            "exit_code": -1,
            "description": description,
            "truncated": False,
            "error": "timeout",
            "warnings": parsed["warnings"],
            "parsed": parsed,
        }
    except Exception as e:
        logger.error("[bash] execution failed: %s", e)
        return {
            "output": f"Error executing command: {e}",
            "exit_code": -1,
            "description": description,
            "truncated": False,
            "error": str(e),
            "warnings": parsed["warnings"],
            "parsed": parsed,
        }


@tool(
    name="bash",
    display_name="Execute Shell Command",
    description=(
        "Execute one shell command string in the workspace with the host user's filesystem, "
        "process, and network authority. Use bash for git status/diff/log, package manager "
        "commands, test runners, build commands, or CLI tools that have no dedicated OpenGIS "
        "tool. Prefer dedicated tools for reading/writing/editing files, listing directories, "
        "and GIS/map operations because those tools provide structured results and safer checks. "
        "For Python analysis dependencies, prefer execute_code first: it auto-installs missing "
        "imports when allowed. Use bash pip commands only for explicit environment management "
        "or after execute_code reports that auto-install was denied or failed. "
        "Do not use cd; pass workdir. Keep output bounded: use rg/head/tail and quiet flags. "
        "Avoid destructive commands; dangerous patterns are refused. The result includes parsed "
        "command metadata, external path warnings, mutation warnings, exit code, timeout, and "
        "truncation status."
    ),
    category="system",
    params=[
        {"name": "command", "type": "string", "description": "The shell command to execute."},
        {"name": "timeout", "type": "number", "description": "Optional timeout in milliseconds (default 120000)."},
        {"name": "workdir", "type": "string", "description": "Working directory (default current). Use this instead of 'cd'."},
        {"name": "description", "type": "string", "description": "Clear description of what this command does (5-10 words)."},
    ],
    returns="dict with keys: output (str), exit_code (int), truncated (bool), error (str|null), warnings (list), parsed (dict)",
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
    event loop.
    """
    return _bash_sync(command, timeout, workdir, description)
