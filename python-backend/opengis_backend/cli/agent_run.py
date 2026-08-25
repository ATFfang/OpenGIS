"""CLI for running one OpenGIS agent task from a shell."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from opengis_backend.cli.common import (
    add_model_args,
    configure_stdio,
    load_attachments,
    parse_key_values,
    print_json,
    read_prompt,
)
from opengis_backend.runtime.config import settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opengis-agent", description="OpenGIS agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one prompt through the OpenGIS agent")
    run.add_argument("--workspace", required=True, help="Workspace directory")
    run.add_argument("--prompt", help="Prompt text")
    run.add_argument("--prompt-file", help="File containing the prompt")
    run.add_argument("--conversation-id", default=None, help="Conversation id for memory continuity")
    run.add_argument("--profile", default=None, help="Agent profile name")
    run.add_argument(
        "--tool-group",
        action="append",
        default=None,
        help="Restrict visible tools by group. Can be repeated.",
    )
    run.add_argument(
        "--feature-flag",
        action="append",
        default=[],
        help="Feature flag as KEY=VALUE. Can be repeated.",
    )
    run.add_argument("--user-instructions", default=None, help="Additional user instruction text")
    run.add_argument("--attachments-json", default=None, help="JSON file with attachment descriptors")
    run.add_argument(
        "--approval-policy",
        choices=["deny", "allow"],
        default="deny",
        help="Non-interactive approval behavior for tools that require approval.",
    )
    run.add_argument("--events-jsonl", default=None, help="Write live events/notifications as JSONL")
    run.add_argument("--result-json", default=None, help="Write the final run result JSON")
    run.add_argument("--quiet", action="store_true", help="Only write result-json, do not print JSON")
    add_model_args(run)
    return parser


async def run_command(args: argparse.Namespace) -> int:
    from opengis_backend.harness.headless import HeadlessAgentRunner, HeadlessRunConfig
    from opengis_backend.harness.headless import make_jsonl_event_sink
    from opengis_backend.harness.metrics import write_json

    workspace = str(Path(args.workspace).expanduser().resolve())
    Path(workspace).mkdir(parents=True, exist_ok=True)
    runner = HeadlessAgentRunner(event_sink=make_jsonl_event_sink(args.events_jsonl))
    result = await runner.run(
        HeadlessRunConfig(
            workspace_path=workspace,
            prompt=read_prompt(args),
            conversation_id=args.conversation_id,
            protocol=args.protocol or settings.llm_protocol,
            model=args.model or settings.llm_model,
            api_key=args.api_key if args.api_key is not None else settings.llm_api_key,
            base_url=args.base_url if args.base_url is not None else settings.llm_base_url,
            profile_name=args.profile,
            tool_groups=args.tool_group,
            user_instructions=args.user_instructions,
            feature_flags=parse_key_values(args.feature_flag),
            attachments=load_attachments(args.attachments_json),
            approval_policy=args.approval_policy,
        )
    )
    payload = result.to_dict()
    if args.result_json:
        write_json(args.result_json, payload)
    if not args.quiet:
        print_json(payload)
    return 0 if result.status in {"success", "completed"} else 1


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
