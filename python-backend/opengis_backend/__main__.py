"""Entry point for running the backend: python -m opengis_backend"""

import argparse
import logging
import sys
from pathlib import Path

# --- Force UTF-8 on stdio BEFORE anything else imports a library that
# captures sys.stdout / sys.stderr (rich.Console, smolagents.AgentLogger,
# uvicorn's default logger, etc.).
#
# Why this matters on Windows: when Electron spawns this sidecar, the
# inherited console codepage is usually cp936 (GBK). Libraries like rich
# wrap sys.stdout with a TextIOWrapper that honours that codepage, and
# any non-GBK character (e.g. smolagents prints a '\u2713' check-mark
# between steps) will raise UnicodeEncodeError deep inside the agent
# loop. That exception surfaces to the user as
# "Agent error: 'gbk' codec can't encode character '\u2713'" and aborts
# the run.
#
# `reconfigure(encoding='utf-8')` (Python 3.7+) swaps the codec on the
# already-open TextIOWrapper without touching file descriptors, which is
# exactly what we want here -- no FD dup, no re-wrapping pipes that
# Electron's stdout detector is already reading.
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            pass

import uvicorn

from opengis_backend.logging_setup import configure_logging


def main():
    parser = argparse.ArgumentParser(description="OpenGIS Backend Server")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Server host")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="Override log directory (default: OS-specific user log dir)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Log level: DEBUG/INFO/WARNING/ERROR (default: INFO)",
    )
    args = parser.parse_args()

    # Configure logging FIRST so every downstream import is captured.
    log_dir_arg = Path(args.log_dir) if args.log_dir else None
    resolved_log_dir = configure_logging(
        log_dir=log_dir_arg,
        level=getattr(logging, args.log_level.upper(), logging.INFO),
    )

    logger = logging.getLogger("opengis_backend")
    logger.info("Starting on %s:%s", args.host, args.port)
    logger.info("Log directory: %s", resolved_log_dir)

    # Also print to stdout so Electron's old stdout detector still works
    # (it scans for 'Uvicorn running' / 'OPENGIS_READY').
    print(f"[OpenGIS Backend] Starting on {args.host}:{args.port}")
    print(f"[OpenGIS Backend] Log dir: {resolved_log_dir}")

    uvicorn.run(
        "opengis_backend.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level.lower(),
        # Let our root logger handle uvicorn records too.
        log_config=None,
    )


if __name__ == "__main__":
    main()
