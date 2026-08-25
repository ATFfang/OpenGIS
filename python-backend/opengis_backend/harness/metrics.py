"""Metrics helpers for headless runs and benchmark batches."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from opengis_backend.runs import RunArchive


@dataclass
class RunMetrics:
    """Compact quantitative summary for one archived agent run."""

    run_id: str
    status: str
    run_dir: str
    duration_ms: int | None
    tool_calls: int
    failed_tool_calls: int
    code_steps: int
    artifacts: int
    llm_turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    cache_hit_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_run_metrics(archive: RunArchive | None) -> RunMetrics | None:
    """Read a RunArchive and return metrics suitable for JSONL output."""
    if archive is None:
        return None

    meta = archive.meta
    tool_calls = archive.read_tool_calls()
    llm_usage = archive.read_llm_usage()
    artifacts = archive.read_artifacts()
    steps = archive.read_steps()

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    cached_tokens = 0

    for record in llm_usage:
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
        prompt_cache = (
            record.get("prompt_cache") if isinstance(record.get("prompt_cache"), dict) else {}
        )
        prompt_tokens += _first_int(usage, "prompt_tokens", "input_tokens")
        completion_tokens += _first_int(usage, "completion_tokens", "output_tokens")
        total_tokens += _first_int(usage, "total_tokens")
        cached_tokens += _first_int(
            usage,
            "cached_tokens",
            "prompt_cache_hit_tokens",
            "cache_read_input_tokens",
        )
        cached_tokens += _first_int(
            prompt_cache,
            "cached_tokens",
            "hit_tokens",
            "cache_read_input_tokens",
        )

    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    cache_hit_rate = (cached_tokens / prompt_tokens) if prompt_tokens > 0 else None

    return RunMetrics(
        run_id=str(meta.get("run_id") or archive.run_id),
        status=str(meta.get("status") or "unknown"),
        run_dir=str(archive.run_dir),
        duration_ms=_duration_ms(meta),
        tool_calls=len(tool_calls),
        failed_tool_calls=sum(1 for item in tool_calls if item.get("error")),
        code_steps=len(steps),
        artifacts=len(artifacts),
        llm_turns=len(llm_usage),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        cache_hit_rate=cache_hit_rate,
    )


def summarize_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-case benchmark records into one summary dict."""
    total = len(items)
    success = sum(1 for item in items if item.get("status") in {"success", "completed"})
    failed = sum(1 for item in items if item.get("status") == "error")
    cancelled = sum(1 for item in items if item.get("status") == "cancelled")
    total_tokens = sum(int(item.get("total_tokens") or 0) for item in items)
    prompt_tokens = sum(int(item.get("prompt_tokens") or 0) for item in items)
    cached_tokens = sum(int(item.get("cached_tokens") or 0) for item in items)
    durations = [int(item["duration_ms"]) for item in items if item.get("duration_ms") is not None]
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "cancelled": cancelled,
        "success_rate": (success / total) if total else 0.0,
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "cache_hit_rate": (cached_tokens / prompt_tokens) if prompt_tokens > 0 else None,
        "tool_calls": sum(int(item.get("tool_calls") or 0) for item in items),
        "failed_tool_calls": sum(int(item.get("failed_tool_calls") or 0) for item in items),
        "artifacts": sum(int(item.get("artifacts") or 0) for item in items),
    }


def latest_run_archive(workspace_path: str | None, run_id: str | None) -> RunArchive | None:
    """Load a run by id, or fall back to the newest run in a workspace."""
    if run_id:
        return RunArchive.load(workspace_path, run_id)
    runs = RunArchive.list_runs(workspace_path, limit=1)
    if not runs:
        return None
    return RunArchive.load(workspace_path, runs[0].run_id)


def _first_int(source: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _duration_ms(meta: dict[str, Any]) -> int | None:
    from datetime import datetime

    created = meta.get("created_at")
    finished = meta.get("finished_at")
    if not created or not finished:
        return None
    try:
        start = datetime.fromisoformat(str(created))
        end = datetime.fromisoformat(str(finished))
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    import json

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    import json

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
