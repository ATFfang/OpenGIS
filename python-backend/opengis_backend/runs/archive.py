"""RunArchive — persist every agent run to ``<ws>/.opengis/runs/<run_id>/``.

Layout
------
::

    <workspace>/.opengis/runs/<run_id>/
        meta.json            # run-level metadata, rewritten on updates
        steps.jsonl          # one JSON line per executed step
        stdout.log           # child subprocess stdout (optional)
        final_answer.md      # best-effort final answer text
        # NOTE: the step *.py scripts live in <ws>/script/ (owned by
        # ScriptArchive); meta.json.scripts_dir points at it.

If no workspace is open we mirror the ``agent-runs`` fallback used by
:class:`ScriptArchive` so orphan runs still get a structured archive:
``<app_data>/opengis/agent-runs/<run_id>/``.

Concurrency
-----------
A single run writes to a single archive and agent runs are serialised
per-workspace (see D2 lock in RpcHandler). We therefore don't need
filesystem locking around meta.json — overlapping writes cannot
happen by construction.

Failure mode
------------
Every public method swallows its own I/O errors after logging. A
corrupt archive must NEVER be fatal to an agent run: the run is the
primary deliverable, the archive is supplemental.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from opengis_backend.agent.script_archive import _app_data_base  # reuse same root

logger = logging.getLogger(__name__)


class RunArchiveError(RuntimeError):
    """Raised only for programmer errors (bad paths). IO failures are logged, not raised."""


# ─────────────────────────────────────────────────────────────────────
# RunIndex — lightweight listing helper used by rpc.runs.list
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunIndex:
    """Summary row for one archived run."""

    run_id: str
    status: str
    prompt: str
    created_at: str
    finished_at: Optional[str]
    step_count: int
    pre_sha: Optional[str]
    post_sha: Optional[str]

    @classmethod
    def from_meta(cls, meta: dict) -> "RunIndex":
        return cls(
            run_id=str(meta.get("run_id", "")),
            status=str(meta.get("status", "unknown")),
            prompt=str(meta.get("prompt", "")),
            created_at=str(meta.get("created_at", "")),
            finished_at=meta.get("finished_at"),
            step_count=int(meta.get("step_count", 0) or 0),
            pre_sha=meta.get("pre_sha"),
            post_sha=meta.get("post_sha"),
        )


# ─────────────────────────────────────────────────────────────────────
# RunArchive — main class
# ─────────────────────────────────────────────────────────────────────


@dataclass
class RunArchive:
    """Per-run on-disk record.

    Build one with :meth:`RunArchive.open`, not the bare constructor.
    """

    run_id: str
    run_dir: Path
    workspace_path: Optional[Path]
    _meta: dict = field(default_factory=dict)

    # -- Construction --------------------------------------------------

    @classmethod
    def open(
        cls,
        *,
        run_id: str,
        prompt: str,
        workspace_path: Optional[str],
        model: str = "",
        scripts_dir: Optional[Path] = None,
    ) -> "RunArchive":
        """Create (or overwrite) an archive directory + initial meta.json.

        Called once at the beginning of each agent run.
        """
        ws = Path(workspace_path).expanduser().resolve() if workspace_path else None
        if ws is not None:
            run_dir = ws / ".opengis" / "runs" / run_id
        else:
            run_dir = _app_data_base() / "agent-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        meta: dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "prompt": prompt,
            "workspace_path": str(ws) if ws else None,
            "model": model,
            "pre_sha": None,
            "post_sha": None,
            "scripts_dir": str(scripts_dir) if scripts_dir else None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "step_count": 0,
            "risky_ops": [],
            "error": None,
        }
        archive = cls(run_id=run_id, run_dir=run_dir, workspace_path=ws, _meta=meta)
        archive._flush_meta()
        # Touch the steps file so downstream readers don't have to handle "missing".
        (run_dir / "steps.jsonl").touch()
        logger.info("RunArchive opened at %s", run_dir)
        return archive

    # -- Mutation API --------------------------------------------------

    def set_pre_sha(self, sha: Optional[str]) -> None:
        self._meta["pre_sha"] = sha
        self._flush_meta()

    def set_post_sha(self, sha: Optional[str]) -> None:
        self._meta["post_sha"] = sha
        self._flush_meta()

    def record_step(
        self,
        *,
        step: int,
        code: str,
        output: str = "",
        error: Optional[str] = None,
        script_path: Optional[str] = None,
    ) -> None:
        """Append one step record to ``steps.jsonl``. Never raises."""
        entry = {
            "step": step,
            "code": code,
            "output": output,
            "error": error,
            "script_path": script_path,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            with (self.run_dir / "steps.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._meta["step_count"] = int(self._meta.get("step_count", 0)) + 1
            self._flush_meta()
        except Exception:
            logger.exception("record_step failed for run=%s step=%s", self.run_id, step)

    def record_risky_op(self, entry: dict) -> None:
        """Append a write-side-effect observation. Never raises."""
        try:
            ops = self._meta.setdefault("risky_ops", [])
            # Timestamp the entry if caller didn't.
            entry.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
            ops.append(entry)
            # Cheap cap to stop pathological loops from bloating meta.json.
            if len(ops) > 1000:
                del ops[:-1000]
            self._flush_meta()
        except Exception:
            logger.exception("record_risky_op failed for run=%s", self.run_id)

    def append_stdout(self, text: str) -> None:
        """Mirror subprocess stdout into stdout.log. Never raises."""
        if not text:
            return
        try:
            with (self.run_dir / "stdout.log").open("a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            logger.exception("append_stdout failed for run=%s", self.run_id)

    def close(
        self,
        *,
        status: str,
        final_answer: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Finalise the archive: flip status, stamp finished_at, dump final answer."""
        self._meta["status"] = status
        self._meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if error is not None:
            self._meta["error"] = error
        if final_answer:
            try:
                (self.run_dir / "final_answer.md").write_text(
                    final_answer, encoding="utf-8"
                )
            except Exception:
                logger.exception("final_answer write failed for run=%s", self.run_id)
        self._flush_meta()
        logger.info("RunArchive closed run=%s status=%s", self.run_id, status)

    # -- Read API (used by rpc.runs.*) ---------------------------------

    @property
    def meta(self) -> dict:
        # Return a shallow copy so callers can't mutate our state.
        return dict(self._meta)

    def read_steps(self) -> list[dict]:
        """Return the list of step records. Empty on I/O errors."""
        path = self.run_dir / "steps.jsonl"
        if not path.exists():
            return []
        out: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("malformed steps.jsonl line in run=%s", self.run_id)
        except Exception:
            logger.exception("read_steps failed for run=%s", self.run_id)
        return out

    # -- Static helpers for the listing RPC ----------------------------

    @staticmethod
    def runs_root(workspace_path: Optional[str]) -> Path:
        if workspace_path:
            return Path(workspace_path).expanduser().resolve() / ".opengis" / "runs"
        return _app_data_base() / "agent-runs"

    @classmethod
    def list_runs(
        cls,
        workspace_path: Optional[str],
        limit: int = 50,
    ) -> list[RunIndex]:
        """Enumerate archived runs newest-first by meta.json.created_at."""
        root = cls.runs_root(workspace_path)
        if not root.exists():
            return []
        entries: list[RunIndex] = []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            meta_path = child / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("unreadable meta.json in %s", child)
                continue
            entries.append(RunIndex.from_meta(meta))
        entries.sort(key=lambda r: r.created_at, reverse=True)
        return entries[: max(1, limit)]

    @classmethod
    def load(cls, workspace_path: Optional[str], run_id: str) -> Optional["RunArchive"]:
        """Re-open a previously archived run. Returns None if not found."""
        root = cls.runs_root(workspace_path)
        run_dir = root / run_id
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("RunArchive.load failed for %s", run_dir)
            return None
        ws = Path(workspace_path).expanduser().resolve() if workspace_path else None
        return cls(run_id=run_id, run_dir=run_dir, workspace_path=ws, _meta=meta)

    # -- Internals -----------------------------------------------------

    def _flush_meta(self) -> None:
        try:
            (self.run_dir / "meta.json").write_text(
                json.dumps(self._meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("meta.json flush failed for run=%s", self.run_id)


__all__ = ["RunArchive", "RunArchiveError", "RunIndex"]
