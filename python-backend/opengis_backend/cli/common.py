"""Shared helpers for OpenGIS CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def configure_stdio() -> None:
    """Force UTF-8 output for Windows/Electron spawned shells."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def add_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--protocol", default=None, help="LLM protocol/provider adapter")
    parser.add_argument("--model", default=None, help="LLM model name")
    parser.add_argument("--api-key", default=None, help="LLM API key")
    parser.add_argument("--base-url", default=None, help="LLM API base URL")


def read_prompt(args: argparse.Namespace) -> str:
    prompt = getattr(args, "prompt", None)
    prompt_file = getattr(args, "prompt_file", None)
    if prompt and prompt_file:
        raise SystemExit("Use either --prompt or --prompt-file, not both.")
    if prompt_file:
        return Path(prompt_file).expanduser().read_text(encoding="utf-8")
    if prompt:
        return str(prompt)
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    raise SystemExit("Missing prompt. Provide --prompt, --prompt-file, or stdin.")


def parse_key_values(values: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in values or []:
        if "=" not in item:
            raise SystemExit(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"Empty key in KEY=VALUE item: {item}")
        out[key] = _parse_scalar(value)
    return out


def load_attachments(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("attachments")
    if not isinstance(raw, list):
        raise SystemExit("Attachment file must contain a JSON array or {attachments: [...]}.")
    return [dict(item) for item in raw if isinstance(item, dict)]


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
