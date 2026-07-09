"""
Centralized logging configuration for the OpenGIS backend.

Log destinations:
- File: ``<log_dir>/backend-YYYY-MM-DD.log`` (rotating, 10 MB x 7)
- Console (stdout/stderr) -- still emitted so Electron can mirror it too.

The log directory is chosen in this order of preference:
  1. ``--log-dir`` CLI flag (passed to __main__.py)
  2. ``OPENGIS_LOG_DIR`` env var
  3. Per-OS user log directory:
     - Windows: ``%APPDATA%/opengis/logs``
     - macOS:   ``~/Library/Logs/opengis``
     - Linux:   ``~/.local/state/opengis/logs``  (XDG_STATE_HOME if set)

The "opengis" folder name is lowercase to match Electron's ``app.getPath('userData')``
which is derived from ``package.json#name`` ("opengis").

This module exposes a single ``configure_logging(log_dir)`` function that must be
called before any ``logging.getLogger()`` users produce output.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import date
from typing import Optional


_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 7
_APP_DIR_NAME = "opengis"  # lowercase, matches Electron's userData dir name

# Module-level sentinel so repeated imports don't reconfigure.
_configured: bool = False
_active_log_dir: Optional[Path] = None


def default_log_dir() -> Path:
    """Compute the per-OS default log directory for OpenGIS."""
    env_override = os.environ.get("OPENGIS_LOG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / _APP_DIR_NAME / "logs"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / _APP_DIR_NAME

    # Linux / other Unix
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / _APP_DIR_NAME / "logs"


def configure_logging(log_dir: Optional[Path] = None, level: int = logging.INFO) -> Path:
    """
    Configure root logging to write to both a rotating file and stdout.

    Returns the resolved log directory so the caller can print/telemetry it.
    Safe to call twice — subsequent calls are no-ops.
    """
    global _configured, _active_log_dir

    if _configured:
        return _active_log_dir  # type: ignore[return-value]

    resolved = (log_dir or default_log_dir()).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)

    log_file = resolved / f"backend-{date.today().isoformat()}.log"

    root = logging.getLogger()
    root.setLevel(level)

    # Clear any handlers uvicorn/pytest might have attached so our format wins.
    # Logging goes to stdout so Electron labels it [Python] instead of [Python:err].
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Defensive: if __main__.py's utf-8 reconfigure didn't run (e.g. the
    # backend was imported as a library, or a test harness swapped stdout),
    # force the stream we're about to attach onto utf-8 so rich/progress
    # output never hits a gbk encoder.
    _stdout = sys.stdout
    if _stdout is not None and hasattr(_stdout, "reconfigure"):
        try:
            _stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    stdout_handler = logging.StreamHandler(_stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(stdout_handler)

    # uvicorn has its own loggers — make them propagate to root too,
    # otherwise access logs would bypass the file handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    _configured = True
    _active_log_dir = resolved

    logging.getLogger(__name__).info(
        "Logging configured. dir=%s file=%s", resolved, log_file.name
    )
    return resolved


def get_log_dir() -> Optional[Path]:
    """Return the currently active log directory, or None if not configured."""
    return _active_log_dir


def set_level(level: int) -> None:
    """Change the root logger level at runtime.

    Updates all handlers (file + stdout) to the new level.
    Call with ``logging.DEBUG`` for verbose output or ``logging.INFO``
    for normal operation.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)
    logging.getLogger(__name__).info(
        "Log level changed to %s", logging.getLevelName(level)
    )


def get_level() -> str:
    """Return the current root log level as a string."""
    return logging.getLevelName(logging.getLogger().level)
