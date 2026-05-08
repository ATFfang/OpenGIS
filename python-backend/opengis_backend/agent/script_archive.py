"""
ScriptArchive — persist every CodeAgent step to disk.

Layout rules (agreed 2026-04-21):

- If the user has a workspace open (``workspace_path`` provided):
    <workspace>/script/YYYYMMDD-HHMMSS-step{n}.py
    <workspace>/.opengis/runs/<run_id>/run.log

- If no workspace is open:
    <app_data>/opengis/agent-runs/<run_id>/script/YYYYMMDD-HHMMSS-step{n}.py
    <app_data>/opengis/agent-runs/<run_id>/run.log

Every script file starts with a header comment that records the
run_id / step / timestamp / user message for future forensics.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from opengis_backend.logging_setup import default_log_dir

logger = logging.getLogger(__name__)


def _app_data_base() -> Path:
    """Return the OpenGIS app data root (same place logging_setup uses)."""
    # default_log_dir() returns ``<app_data>/opengis/logs``; drop the trailing
    # ``logs`` segment so we can sibling-mount ``agent-runs/``.
    return default_log_dir().parent


def _short_run_id() -> str:
    return uuid.uuid4().hex[:8]


def _ts_prefix() -> str:
    # e.g. 20260421-163245
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class ScriptArchive:
    """
    Handle to the per-run directories for script + log persistence.

    Prefer ``ScriptArchive.for_run(workspace_path=..., run_id=...)`` to
    construct one; the constructor is used directly only in tests.
    """

    run_id: str
    script_dir: Path
    run_log_dir: Path
    # Relative-path base used when reporting script paths back to the UI.
    # For workspace runs this is the workspace root (so the UI can locate
    # the file within its AssetExplorer); for orphan runs it's the run dir.
    path_base: Path = field(default_factory=Path)
    step_counter: int = 0

    # ── Construction helpers ────────────────────────────────────────

    @classmethod
    def for_run(
        cls,
        workspace_path: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> "ScriptArchive":
        rid = run_id or _short_run_id()
        if workspace_path:
            ws = Path(workspace_path).expanduser().resolve()
            script_dir = ws / "script"
            run_log_dir = ws / ".opengis" / "runs" / rid
            path_base = ws
        else:
            root = _app_data_base() / "agent-runs" / rid
            script_dir = root / "script"
            run_log_dir = root
            path_base = root

        script_dir.mkdir(parents=True, exist_ok=True)
        run_log_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "ScriptArchive ready: run_id=%s script_dir=%s run_log_dir=%s",
            rid, script_dir, run_log_dir,
        )

        return cls(
            run_id=rid,
            script_dir=script_dir,
            run_log_dir=run_log_dir,
            path_base=path_base,
        )

    # ── Step persistence ────────────────────────────────────────────

    def next_step_num(self) -> int:
        self.step_counter += 1
        return self.step_counter

    def write_step(
        self,
        step: int,
        code: str,
        *,
        user_message: str = "",
        observations: str = "",
        error: Optional[str] = None,
    ) -> Path:
        """
        Persist one step's code to disk and return the absolute path.

        The observations/error are embedded as a trailing comment block so
        the file remains a self-contained record. The file is ``.py`` for
        editor affinity (syntax highlighting) even though the tail is a
        comment.
        """
        ts = _ts_prefix()
        name = f"{ts}-step{step}.py"
        path = self.script_dir / name

        header = [
            f"# ─── OpenGIS Agent · run {self.run_id} · step {step} ───",
            f"# Timestamp : {datetime.now().isoformat(timespec='seconds')}",
        ]
        if user_message:
            # Truncate very long messages so the header stays readable.
            msg = user_message.strip().replace("\n", " ")
            if len(msg) > 200:
                msg = msg[:200] + "…"
            header.append(f"# User query: {msg}")
        header.append("")

        body = (code or "").rstrip() + "\n"

        tail_parts: list[str] = []
        if observations:
            obs = observations.rstrip()
            tail_parts.append("# ─── Observations ───")
            for line in obs.splitlines() or [""]:
                tail_parts.append(f"# {line}")
        if error:
            tail_parts.append("# ─── Error ───")
            for line in error.rstrip().splitlines():
                tail_parts.append(f"# {line}")

        text = "\n".join(header) + body
        if tail_parts:
            text += "\n" + "\n".join(tail_parts) + "\n"

        path.write_text(text, encoding="utf-8")
        logger.debug("Wrote step %d to %s (%d bytes)", step, path, len(text))
        return path

    # ── Path helpers ────────────────────────────────────────────────

    def to_relative(self, abs_path: Path) -> str:
        """
        Return a UI-friendly relative path rooted at ``path_base``.

        Falls back to the absolute path if the file lives outside the base
        (shouldn't happen, but handled for safety).
        """
        try:
            return str(abs_path.relative_to(self.path_base)).replace("\\", "/")
        except ValueError:
            return str(abs_path).replace("\\", "/")
