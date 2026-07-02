"""WorkspaceManager — make a user folder safe for agent runs.

Responsibilities in this module (A2 scope):

*   Ensure the workspace is a git repository.
*   Ensure OpenGIS' ignore patterns are present in ``.gitignore``
    (merging rather than overwriting the user's existing rules).
*   Leave an "initial workspace" commit on first init, so subsequent
    snapshot commits (A3) always have a parent to diff against.

Things this module deliberately does NOT do (belong to A3/A4/A5):

*   Taking a snapshot before / after each agent run.
*   Reverting to a previous snapshot.
*   Telling the LLM about the workspace in its prompt.

Design notes
------------
*   We shell out to ``git`` rather than depending on GitPython. Keeping
    the sidecar dependency surface thin is a deliberate choice.
*   All git calls set ``cwd=workspace`` rather than ``git -C`` so the
    behaviour matches what the user would see in their own terminal.
*   Encoding is pinned to UTF-8 on every subprocess call. Windows
    PowerShell's default GBK would otherwise corrupt non-ASCII paths
    and commit messages.
*   ``ensure_initialized`` is idempotent. Calling it on every chat turn
    is the intended usage pattern.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Public errors
# ─────────────────────────────────────────────────────────────────────


class WorkspaceManagerError(RuntimeError):
    """Base error for workspace lifecycle problems."""


class GitNotAvailableError(WorkspaceManagerError):
    """Raised when the ``git`` executable cannot be located on PATH."""


# ─────────────────────────────────────────────────────────────────────
# Public data classes
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceInfo:
    """Result of :meth:`WorkspaceManager.ensure_initialized`.

    Attributes:
        path: Absolute path of the workspace.
        already_repo: ``True`` if the folder was already a git repo
            before this call. When ``True`` we neither ran ``git init``
            nor made the initial commit.
        initial_commit_sha: Short SHA of the "initial workspace" commit
            we created. ``None`` when ``already_repo`` is ``True``.
        gitignore_updated: Whether we had to append OpenGIS' ignore
            block to ``.gitignore`` during this call.
    """

    path: Path
    already_repo: bool
    initial_commit_sha: str | None
    gitignore_updated: bool


# ─────────────────────────────────────────────────────────────────────
# .gitignore block
# ─────────────────────────────────────────────────────────────────────

# We fence our additions with unmistakable markers so we can find the
# block on repeat calls and stay idempotent even if the user edits
# their own rules around ours.
_GITIGNORE_BEGIN: Final = "# >>> opengis <<<"
_GITIGNORE_END: Final = "# <<< opengis >>>"

_GITIGNORE_BODY: Final = """\
# Managed by OpenGIS WorkspaceManager — edit between the markers
# with care; the block is matched by its fences.
.opengis/runs/
.opengis/conversations/
__pycache__/
*.pyc
*.pyo
.venv/
venv/
"""

_GITIGNORE_BLOCK: Final = f"{_GITIGNORE_BEGIN}\n{_GITIGNORE_BODY}{_GITIGNORE_END}\n"

# Commit messages — kept in English so they render cleanly in any
# terminal regardless of locale.
_INITIAL_COMMIT_MSG: Final = "chore(opengis): initial workspace snapshot"


# ─────────────────────────────────────────────────────────────────────
# WorkspaceManager
# ─────────────────────────────────────────────────────────────────────


class WorkspaceManager:
    """Owns git-level workspace lifecycle operations.

    A single instance is fine per process; none of the state we hold
    here is tied to a particular workspace. Methods accept a
    ``workspace`` ``Path`` explicitly so multiple workspaces can be
    served from the same sidecar without contention.
    """

    #: Label for the initial commit. Exposed for tests.
    INITIAL_COMMIT_MSG: Final = _INITIAL_COMMIT_MSG

    def __init__(self, git_executable: str | None = None) -> None:
        """Construct a manager.

        Args:
            git_executable: Override the ``git`` binary path, used in
                tests that want to point at a specific install. When
                ``None`` we resolve ``git`` from ``PATH`` lazily on
                first use so constructing the manager never raises.
        """
        self._git_override = git_executable
        self._resolved_git: str | None = None

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def ensure_initialized(self, workspace: str | Path) -> WorkspaceInfo:
        """Make ``workspace`` safe for agent runs.

        Guarantees after a successful return:

        *   ``workspace`` exists and is a directory.
        *   ``workspace/.git`` exists.
        *   ``workspace/.gitignore`` contains the OpenGIS ignore block.
        *   There is at least one commit on the current branch, so
            later snapshot commits can always diff against a parent.

        Idempotent: calling on an already-initialised workspace is a
        quick no-op (one ``git rev-parse`` and a file read).

        Raises:
            WorkspaceManagerError: The path is missing or not a dir.
            GitNotAvailableError: No ``git`` binary on PATH.
        """
        ws = Path(workspace).expanduser().resolve()
        if not ws.exists():
            raise WorkspaceManagerError(f"workspace does not exist: {ws}")
        if not ws.is_dir():
            raise WorkspaceManagerError(f"workspace is not a directory: {ws}")

        git = self._git()
        already_repo = self._is_git_repo(ws, git=git)

        if already_repo:
            # Respect the user's existing repo. Only make sure our
            # ignore rules are present — never touch their history.
            updated = self._ensure_gitignore_block(ws)
            self._ensure_builtin_templates(ws)
            logger.info(
                "workspace already a git repo, gitignore_updated=%s: %s",
                updated,
                ws,
            )
            return WorkspaceInfo(
                path=ws,
                already_repo=True,
                initial_commit_sha=None,
                gitignore_updated=updated,
            )

        # Green-field workspace: init, write .gitignore, initial commit.
        self._git_run(["init", "--quiet"], cwd=ws, git=git)
        gitignore_updated = self._ensure_gitignore_block(ws)

        # `git add -A` picks up anything the user already had in the
        # folder. We still pass --allow-empty to survive the "truly
        # empty folder" case where only .gitignore exists and is
        # already staged.
        self._git_run(["add", "-A"], cwd=ws, git=git)
        self._git_run(
            [
                "-c",
                "user.name=OpenGIS",
                "-c",
                "user.email=opengis@local",
                "commit",
                "--allow-empty",
                "-m",
                _INITIAL_COMMIT_MSG,
            ],
            cwd=ws,
            git=git,
        )
        sha = self._git_head_sha(ws, git=git)
        logger.info("workspace initialised at %s (commit=%s)", ws, sha)
        return WorkspaceInfo(
            path=ws,
            already_repo=False,
            initial_commit_sha=sha,
            gitignore_updated=gitignore_updated,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Built-in workflow templates
    # ─────────────────────────────────────────────────────────────────────

    _TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

    def _ensure_builtin_templates(self, ws: Path) -> None:
        """Copy built-in workflow templates to workspace/workflows/ if missing.

        Only copies files that don't already exist — never overwrites
        user-modified templates. Idempotent and safe to call on every init.
        """
        if not self._TEMPLATES_DIR.is_dir():
            return

        wf_dir = ws / "workflows"
        try:
            wf_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Cannot create workflows dir: %s", e)
            return

        for tpl in self._TEMPLATES_DIR.glob("*.flow.json"):
            dest = wf_dir / tpl.name
            if dest.exists():
                continue  # user already has this file
            try:
                import shutil
                shutil.copy2(str(tpl), str(dest))
                logger.info("Installed built-in workflow template: %s", tpl.name)
            except OSError as e:
                logger.warning("Failed to copy template %s: %s", tpl.name, e)

    # .gitignore helpers
    # ─────────────────────────────────────────────────────────────────────

    def _ensure_gitignore_block(self, ws: Path) -> bool:
        """Append the OpenGIS fenced block to ``.gitignore`` if absent.

        Returns:
            ``True`` if the file was modified (created or appended),
            ``False`` if the block was already present.
        """
        gi = ws / ".gitignore"
        if gi.exists():
            existing = gi.read_text(encoding="utf-8", errors="replace")
            if _GITIGNORE_BEGIN in existing and _GITIGNORE_END in existing:
                return False
            # Preserve a single trailing newline before we append, so
            # the user's last rule and our fence don't run together.
            sep = "" if existing.endswith("\n") else "\n"
            gi.write_text(existing + sep + _GITIGNORE_BLOCK, encoding="utf-8")
            return True

        gi.write_text(_GITIGNORE_BLOCK, encoding="utf-8")
        return True

    # ─────────────────────────────────────────────────────────────────────
    # git helpers
    # ─────────────────────────────────────────────────────────────────────

    def _git(self) -> str:
        if self._resolved_git is not None:
            return self._resolved_git
        candidate = self._git_override or shutil.which("git")
        if not candidate:
            raise GitNotAvailableError(
                "`git` executable not found on PATH. OpenGIS needs git "
                "to snapshot agent runs. Please install git and retry."
            )
        self._resolved_git = candidate
        return candidate

    @staticmethod
    def _is_git_repo(ws: Path, *, git: str) -> bool:
        result = subprocess.run(
            [git, "rev-parse", "--is-inside-work-tree"],
            cwd=ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    @staticmethod
    def _git_run(args: list[str], *, cwd: Path, git: str) -> subprocess.CompletedProcess[str]:
        """Run a git subcommand with strict error handling and UTF-8.

        We capture both streams — callers can inspect stderr on failure
        — and raise :class:`WorkspaceManagerError` with a concise cause
        if git exits non-zero. That way the RPC layer gets one error
        type to serialise.
        """
        result = subprocess.run(
            [git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise WorkspaceManagerError(
                f"git {' '.join(args)} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    @classmethod
    def _git_head_sha(cls, ws: Path, *, git: str) -> str:
        """Return the short SHA of HEAD."""
        result = cls._git_run(["rev-parse", "--short", "HEAD"], cwd=ws, git=git)
        return result.stdout.strip()

    # ─────────────────────────────────────────────────────────────────────
    # Snapshot / revert — used by A3 (pre/post-run) and A4 (revert RPC).
    # ─────────────────────────────────────────────────────────────────────

    def snapshot(
        self,
        workspace: str | Path,
        *,
        run_id: str,
        label: str,
    ) -> str:
        """Stage everything and create an ``--allow-empty`` commit.

        Returns the short SHA of the resulting commit. The commit is
        always made — even when there are no changes — so that A3's
        pre-run snapshot has a stable SHA to reset back to later.

        Args:
            workspace: The workspace root. Must already be initialised
                (call :meth:`ensure_initialized` once first).
            run_id: The agent run_id. Embedded in the commit message
                so ``git log --oneline`` is grep-able.
            label: Short label, typically ``"pre"`` or ``"post"``.

        Raises:
            WorkspaceManagerError: Not a git repo, or a git call failed.
        """
        ws = Path(workspace).expanduser().resolve()
        git = self._git()
        if not self._is_git_repo(ws, git=git):
            raise WorkspaceManagerError(
                f"workspace is not a git repo, call ensure_initialized first: {ws}"
            )
        self._git_run(["add", "-A"], cwd=ws, git=git)
        msg = f"opengis: {label} {run_id}"
        self._git_run(
            [
                "-c",
                "user.name=OpenGIS",
                "-c",
                "user.email=opengis@local",
                "commit",
                "--allow-empty",
                "-m",
                msg,
            ],
            cwd=ws,
            git=git,
        )
        return self._git_head_sha(ws, git=git)

    def reset_hard(self, workspace: str | Path, sha: str) -> None:
        """``git reset --hard <sha>`` — used by :meth:`revert_run` / RPC A4.

        We intentionally do NOT touch untracked files: if the LLM's
        subprocess created a brand-new untracked blob, the user can
        inspect it before deciding to wipe it. That matches Claude
        Code's safety default.
        """
        ws = Path(workspace).expanduser().resolve()
        git = self._git()
        self._git_run(["reset", "--hard", sha], cwd=ws, git=git)

    def current_head(self, workspace: str | Path) -> str:
        """Return the short SHA of the workspace's current HEAD."""
        ws = Path(workspace).expanduser().resolve()
        return self._git_head_sha(ws, git=self._git())
