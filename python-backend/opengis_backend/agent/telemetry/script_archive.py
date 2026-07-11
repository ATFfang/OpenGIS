"""
ScriptArchive — persist agent-authored Python steps to disk.

Layout rules:

- If the user has a workspace open (``workspace_path`` provided):
    <workspace>/script/YYYYMMDD-HHMMSS-stepNN-semantic-name.py
    <workspace>/.opengis/runs/<run_id>/run.log

- If the run is a workflow:
    <workspace>/script/workflows/<workflow-name>-<run_id>/
        YYYYMMDD-HHMMSS-stepNN-semantic-name.py
        YYYYMMDD-HHMMSS-stepNN-semantic-name.metadata.json
        _scripts_index.jsonl

- If no workspace is open:
    <app_data>/opengis/agent-runs/<run_id>/script/YYYYMMDD-HHMMSS-stepNN-semantic-name.py
    <app_data>/opengis/agent-runs/<run_id>/run.log

Every script file starts with a header comment that records the run_id,
step, timestamp, semantic name, metadata file, and user message for future
forensics. A sibling metadata JSON file and an append-only JSONL index make
scripts discoverable and reusable without parsing Python comments.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from opengis_backend.runtime.logging import default_log_dir

logger = logging.getLogger(__name__)


def _app_data_base() -> Path:
    """Return the OpenGIS app data root used by runtime logging."""
    # default_log_dir() returns ``<app_data>/opengis/logs``; drop the trailing
    # ``logs`` segment so we can sibling-mount ``agent-runs/``.
    return default_log_dir().parent


def _short_run_id() -> str:
    return uuid.uuid4().hex[:8]


def _ts_prefix() -> str:
    # e.g. 20260421-163245
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _slugify(value: str, *, fallback: str = "script", max_len: int = 64) -> str:
    """Return a filesystem-safe semantic slug while preserving CJK text."""
    text = (value or "").strip().replace("\n", " ")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    if not text:
        text = fallback
    return text[:max_len].strip("-._") or fallback


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
        workflow_name: Optional[str] = None,
    ) -> "ScriptArchive":
        rid = run_id or _short_run_id()
        workflow_slug = _slugify(workflow_name or "", fallback="workflow") if workflow_name else None
        if workspace_path:
            ws = Path(workspace_path).expanduser().resolve()
            script_dir = (
                ws / "script" / "workflows" / f"{workflow_slug}-{rid}"
                if workflow_slug
                else ws / "script"
            )
            run_log_dir = ws / ".opengis" / "runs" / rid
            path_base = ws
        else:
            root = _app_data_base() / "agent-runs" / rid
            script_dir = (
                root / "script" / "workflows" / f"{workflow_slug}-{rid}"
                if workflow_slug
                else root / "script"
            )
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
        semantic_name: str = "",
        metadata: Optional[dict] = None,
    ) -> Path:
        """
        Persist one step's code to disk and return the absolute path.

        The observations/error are embedded as a trailing comment block so
        the file remains a self-contained record. The file is ``.py`` for
        editor affinity (syntax highlighting) even though the tail is a
        comment.
        """
        ts = _ts_prefix()
        semantic_slug = _slugify(semantic_name or user_message, fallback="script")
        name = f"{ts}-step{step:02d}-{semantic_slug}.py"
        path = self.script_dir / name
        created_at = datetime.now().isoformat(timespec="seconds")
        meta = {
            "run_id": self.run_id,
            "step": step,
            "timestamp": created_at,
            "semantic_name": semantic_name or semantic_slug,
            "script_path": str(path),
            "user_message": user_message,
            "has_error": bool(error),
            **(metadata or {}),
        }

        header = [
            f"# ─── OpenGIS Agent · run {self.run_id} · step {step} · {semantic_slug} ───",
            f"# Timestamp : {created_at}",
            f"# Semantic : {semantic_name or semantic_slug}",
            f"# Metadata : {path.with_suffix('.metadata.json').name}",
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
        meta_path = path.with_suffix(".metadata.json")
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            with (self.script_dir / "_scripts_index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("Failed to append script metadata index", exc_info=True)
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
