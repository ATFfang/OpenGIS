"""User-loadable skill discovery.

These skills are instruction/resource bundles, not executable tools. A skill
is a directory containing ``SKILL.md`` plus optional ``references/``,
``scripts/`` and assets. The agent loads a skill on demand through the
``load_skill`` tool.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n?", re.DOTALL)
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass
class UserSkillInfo:
    """A discovered instruction skill."""

    name: str
    description: str | None
    location: str
    content: str
    source: str
    tags: list[str] = field(default_factory=list)
    version: str | None = None

    @property
    def directory(self) -> str:
        return str(Path(self.location).parent)

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "location": self.location,
            "directory": self.directory,
            "source": self.source,
            "tags": self.tags,
            "version": self.version,
        }
        if include_content:
            data["content"] = self.content
        return data


class UserSkillDiscovery:
    """Discovers filesystem-backed skill bundles."""

    def __init__(self, workspace_path: str | None = None) -> None:
        self.workspace_path = workspace_path

    def list(self) -> list[UserSkillInfo]:
        skills: dict[str, UserSkillInfo] = {}
        for root, source in self._roots():
            for match in self._scan_root(root):
                info = self._load(match, source)
                if info is None:
                    continue
                # Later roots intentionally override earlier roots. That lets
                # workspace skills shadow global defaults.
                skills[info.name] = info
        return sorted(skills.values(), key=lambda item: item.name.lower())

    def get(self, name: str) -> UserSkillInfo | None:
        for info in self.list():
            if info.name == name:
                return info
        return None

    def require(self, name: str) -> UserSkillInfo:
        info = self.get(name)
        if info is not None:
            return info
        available = ", ".join(item.name for item in self.list()) or "none"
        raise KeyError(f'Skill "{name}" not found. Available skills: {available}')

    def _roots(self) -> list[tuple[Path, str]]:
        roots: list[tuple[Path, str]] = []
        home = Path.home()
        roots.append((home / ".opengis" / "skills", "global"))

        env_paths = os.environ.get("OPENGIS_SKILL_PATHS", "")
        for raw in env_paths.split(os.pathsep):
            item = raw.strip()
            if not item:
                continue
            roots.append((Path(item).expanduser(), "env"))

        for item in _read_source_paths(home / ".opengis" / "skill-sources.json"):
            roots.append((Path(item).expanduser(), "global-source"))

        if self.workspace_path:
            workspace = Path(self.workspace_path).expanduser()
            for item in _read_source_paths(workspace / ".opengis" / "skill-sources.json"):
                path = Path(item).expanduser()
                if not path.is_absolute():
                    path = workspace / path
                roots.append((path, "workspace-source"))
            roots.append((workspace / ".agents" / "skills", "workspace-agents"))
            roots.append((workspace / ".opengis" / "skills", "workspace"))
            roots.append((workspace / "skills", "workspace"))

        return roots

    def _scan_root(self, root: Path) -> list[Path]:
        try:
            root = root.expanduser().resolve()
        except Exception:
            root = root.expanduser()
        if not root.exists() or not root.is_dir():
            return []

        direct = root / "SKILL.md"
        if direct.is_file():
            return [direct]

        matches: list[Path] = []
        # Keep discovery bounded. Skill repos may contain references and
        # scripts, but SKILL.md should live near the skill directory root.
        max_depth = 4
        for path in root.rglob("SKILL.md"):
            try:
                rel_depth = len(path.relative_to(root).parts) - 1
            except ValueError:
                continue
            if rel_depth <= max_depth:
                matches.append(path)
        return sorted(matches)

    def _load(self, path: Path, source: str) -> UserSkillInfo | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        meta, content = _parse_frontmatter(raw)
        name = str(meta.get("name") or path.parent.name).strip()
        if not name or not _VALID_NAME_RE.match(name):
            return None
        description = meta.get("description")
        tags = _normalise_string_list(meta.get("tags"))
        version = meta.get("version")
        return UserSkillInfo(
            name=name,
            description=str(description).strip() if description else None,
            location=str(path),
            content=content.strip(),
            source=source,
            tags=tags,
            version=str(version).strip() if version else None,
        )


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    return _parse_simple_yaml(match.group("meta")), text[match.end():]


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small frontmatter subset skills need.

    Supports ``key: value`` and one-line arrays such as
    ``tags: [gis, analysis]``. This intentionally avoids adding a YAML
    dependency to the backend.
    """

    data: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        index += 1
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current_key and current_list is not None and stripped.startswith("- "):
            current_list.append(_unquote(stripped[2:].strip()))
            continue
        current_key = None
        current_list = None
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value == "":
            data[key] = []
            current_key = key
            current_list = data[key]
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [
                _unquote(item.strip())
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        elif value in {">", ">-", ">|", "|", "|-", "|+"}:
            block: list[str] = []
            while index < len(lines):
                next_line = lines[index]
                if next_line.strip() and not next_line.startswith((" ", "\t")):
                    break
                index += 1
                if next_line.strip():
                    block.append(next_line.strip())
                elif value.startswith("|"):
                    block.append("")
            if value.startswith(">"):
                data[key] = " ".join(part for part in block if part)
            else:
                data[key] = "\n".join(block).strip()
        else:
            data[key] = _unquote(value)
    return data


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _normalise_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _read_source_paths(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_paths: Any = None
    if isinstance(data, dict):
        raw_paths = data.get("paths")
        if raw_paths is None and isinstance(data.get("skills"), dict):
            raw_paths = data["skills"].get("paths")
    if not isinstance(raw_paths, list):
        return []
    return [str(item).strip() for item in raw_paths if str(item).strip()]


def add_source_path(
    source_path: str,
    *,
    workspace_path: str | None = None,
    scope: str = "workspace",
) -> dict[str, Any]:
    """Persist an additional skill source path.

    ``scope`` is ``workspace`` or ``global``. Workspace-relative paths are
    resolved by discovery against the workspace root.
    """

    clean = str(source_path).strip()
    if not clean:
        raise ValueError("source_path is required")
    if scope not in {"workspace", "global"}:
        raise ValueError("scope must be 'workspace' or 'global'")
    if scope == "workspace":
        if not workspace_path:
            raise ValueError("workspace_path is required for workspace scope")
        target = Path(workspace_path).expanduser() / ".opengis" / "skill-sources.json"
    else:
        target = Path.home() / ".opengis" / "skill-sources.json"

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    paths = data.get("paths")
    if not isinstance(paths, list):
        paths = []
    if clean not in [str(item) for item in paths]:
        paths.append(clean)
    data["paths"] = paths
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(target), "source_path": clean, "scope": scope, "count": len(paths)}


def format_available_skills(skills: list[UserSkillInfo]) -> str:
    described = [item for item in skills if item.description]
    if not described:
        return "No user-loadable skills are currently available."
    lines = [
        "Skills provide specialized instructions and workflows for specific tasks.",
        "Use the load_skill tool when the task matches a skill description.",
        "<available_skills>",
    ]
    for item in described:
        lines.extend(
            [
                "  <skill>",
                f"    <name>{item.name}</name>",
                f"    <description>{item.description}</description>",
                f"    <location>{item.location}</location>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


__all__ = ["UserSkillDiscovery", "UserSkillInfo", "add_source_path", "format_available_skills"]
