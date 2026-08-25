"""CLI benchmark runner for OpenGIS agent tasks."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opengis_backend.cli.common import (
    add_model_args,
    configure_stdio,
    parse_key_values,
    print_json,
)
from opengis_backend.runtime.config import settings


@dataclass
class BenchTask:
    id: str
    prompt: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opengis-bench", description="OpenGIS benchmark CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run a benchmark task set")
    run.add_argument("--workspace", required=True, help="Workspace directory")
    run.add_argument("--task-set", required=True, help="JSON or JSONL task-set path")
    run.add_argument("--output-dir", default=None, help="Directory for runs.jsonl and summary.json")
    run.add_argument("--limit", type=int, default=None, help="Run at most N tasks")
    run.add_argument("--conversation-prefix", default="bench", help="Conversation id prefix")
    run.add_argument("--profile", default=None, help="Agent profile name")
    run.add_argument("--tool-group", action="append", default=None, help="Visible tool group")
    run.add_argument("--feature-flag", action="append", default=[], help="Feature flag as KEY=VALUE")
    run.add_argument("--user-instructions", default=None, help="Additional user instruction text")
    run.add_argument(
        "--approval-policy",
        choices=["deny", "allow"],
        default="deny",
        help="Non-interactive approval behavior for tools that require approval.",
    )
    run.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue remaining benchmark cases after a failed run.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate task loading and write skipped metrics without calling the LLM.",
    )
    add_model_args(run)
    return parser


async def run_command(args: argparse.Namespace) -> int:
    from opengis_backend.harness.metrics import append_jsonl, summarize_metrics, write_json

    workspace = str(Path(args.workspace).expanduser().resolve())
    Path(workspace).mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(args.task_set)
    if args.limit is not None:
        tasks = tasks[: max(0, args.limit)]
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else _default_output_dir(workspace)
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_jsonl = output_dir / "runs.jsonl"
    summary_json = output_dir / "summary.json"
    events_dir = output_dir / "events"
    if runs_jsonl.exists():
        runs_jsonl.unlink()

    records: list[dict[str, Any]] = []
    feature_flags = parse_key_values(args.feature_flag)
    runner = None

    for index, task in enumerate(tasks, start=1):
        if args.dry_run:
            record = {
                "case_id": task.id,
                "status": "skipped",
                "dry_run": True,
                "tags": task.tags,
                "metadata": task.metadata,
            }
            append_jsonl(runs_jsonl, record)
            records.append(record)
            continue

        if runner is None:
            from opengis_backend.harness.headless import HeadlessAgentRunner, HeadlessRunConfig
            from opengis_backend.harness.headless import make_jsonl_event_sink

            runner = HeadlessAgentRunner()
        event_sink = make_jsonl_event_sink(events_dir / f"{task.id}.jsonl")
        runner.event_sink = event_sink
        started = time.monotonic()
        try:
            result = await runner.run(
                HeadlessRunConfig(
                    workspace_path=workspace,
                    prompt=task.prompt,
                    conversation_id=f"{args.conversation_prefix}-{task.id}",
                    protocol=args.protocol or settings.llm_protocol,
                    model=args.model or settings.llm_model,
                    api_key=args.api_key if args.api_key is not None else settings.llm_api_key,
                    base_url=args.base_url if args.base_url is not None else settings.llm_base_url,
                    profile_name=args.profile,
                    tool_groups=args.tool_group,
                    user_instructions=args.user_instructions,
                    feature_flags=feature_flags,
                    attachments=task.attachments,
                    approval_policy=args.approval_policy,
                )
            )
            metrics = result.metrics.to_dict() if result.metrics else {}
            record = {
                **metrics,
                "case_id": task.id,
                "index": index,
                "status": result.status,
                "run_id": result.run_id,
                "run_dir": result.run_dir,
                "duration_ms": result.duration_ms,
                "error": result.error,
                "tags": task.tags,
                "metadata": task.metadata,
            }
        except Exception as exc:
            record = {
                "case_id": task.id,
                "index": index,
                "status": "error",
                "run_id": None,
                "run_dir": None,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
                "tags": task.tags,
                "metadata": task.metadata,
            }
            if not args.continue_on_error:
                append_jsonl(runs_jsonl, record)
                records.append(record)
                write_json(summary_json, summarize_metrics(records))
                print_json({"output_dir": str(output_dir), "summary": summarize_metrics(records)})
                return 1

        append_jsonl(runs_jsonl, record)
        records.append(record)

    summary = summarize_metrics(records)
    payload = {
        "output_dir": str(output_dir),
        "runs_jsonl": str(runs_jsonl),
        "summary_json": str(summary_json),
        "summary": summary,
    }
    write_json(summary_json, payload)
    print_json(payload)
    return 0 if summary["failed"] == 0 else 1


def load_tasks(path: str | Path) -> list[BenchTask]:
    task_path = Path(path).expanduser().resolve()
    if task_path.suffix.lower() == ".jsonl":
        raw_items = []
        for line in task_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                raw_items.append(json.loads(line))
    else:
        raw = json.loads(task_path.read_text(encoding="utf-8"))
        raw_items = raw.get("tasks") if isinstance(raw, dict) else raw
    if not isinstance(raw_items, list):
        raise SystemExit("Task set must be a JSON array, {tasks: [...]}, or JSONL.")

    tasks: list[BenchTask] = []
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"Task #{idx} must be an object.")
        prompt = item.get("prompt") or item.get("input") or item.get("question")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SystemExit(f"Task #{idx} is missing prompt/input/question.")
        task_id = str(item.get("id") or item.get("case_id") or f"case-{idx:04d}")
        attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        tasks.append(
            BenchTask(
                id=_safe_case_id(task_id),
                prompt=prompt,
                attachments=[dict(att) for att in attachments if isinstance(att, dict)],
                tags=[str(tag) for tag in tags],
                metadata=dict(metadata),
            )
        )
    return tasks


def _safe_case_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    return safe or "case"


def _default_output_dir(workspace: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(workspace).expanduser().resolve() / ".opengis" / "bench" / stamp


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(run_command(args))
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
