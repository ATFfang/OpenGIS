"""Multiplexer CLI for OpenGIS harness commands."""

from __future__ import annotations

import argparse
import sys

from opengis_backend.cli import agent_run, bench_run
from opengis_backend.cli.common import configure_stdio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opengis-harness", description="OpenGIS harness CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("agent", help="Show opengis-agent usage")
    sub.add_parser("bench", help="Show opengis-bench usage")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    items = list(sys.argv[1:] if argv is None else argv)
    if items and items[0] == "agent":
        rest = items[1:] or ["--help"]
        return agent_run.main(rest or ["--help"])
    if items and items[0] == "bench":
        rest = items[1:] or ["--help"]
        return bench_run.main(rest or ["--help"])
    parser = build_parser()
    args = parser.parse_args(items)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
