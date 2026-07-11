"""Tool for loading user skill bundles into the agent context."""

from __future__ import annotations

from pathlib import Path

from opengis_backend.skills.discovery import UserSkillDiscovery
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool


@tool(
    name="load_skill",
    display_name="Load Skill",
    description=(
        "Load a user-provided instruction skill by name. Use this when the "
        "current task matches an item from <available_skills>. The result "
        "contains the SKILL.md content, the base directory, and a sampled "
        "file list. This does not execute code."
    ),
    category="orchestration",
    params=[
        {
            "name": "name",
            "type": "string",
            "description": "Skill name from the available_skills list.",
            "required": True,
        }
    ],
    returns="Skill content and relative-path base directory.",
    examples=["load_skill(name='urban-retail-analysis')"],
    tags=["skill", "instructions", "agent"],
    needs_context=True,
    group="core",
)
def load_skill(ctx: ToolContext, name: str) -> dict:
    workspace = (ctx.meta or {}).get("workspace_path")
    discovery = UserSkillDiscovery(workspace_path=workspace)
    info = discovery.require(name)
    base = Path(info.directory)
    files = _sample_files(base)
    return {
        "success": True,
        "name": info.name,
        "description": info.description,
        "location": info.location,
        "base_directory": str(base),
        "instructions": (
            f'<skill_content name="{info.name}">\n'
            f"# Skill: {info.name}\n\n"
            f"{info.content.strip()}\n\n"
            f"Base directory for this skill: {base}\n"
            "Relative paths in this skill are relative to this base directory.\n"
            "The file list below is sampled.\n"
            "<skill_files>\n"
            + "\n".join(f"<file>{path}</file>" for path in files)
            + "\n</skill_files>\n"
            "</skill_content>"
        ),
        "files": files,
    }


def _sample_files(base: Path, limit: int = 20) -> list[str]:
    files: list[str] = []
    try:
        for path in sorted(base.rglob("*")):
            if len(files) >= limit:
                break
            if not path.is_file():
                continue
            if path.name == "SKILL.md":
                continue
            try:
                rel = path.relative_to(base)
            except ValueError:
                rel = Path(path.name)
            if any(part in {".git", "__pycache__", "node_modules"} for part in rel.parts):
                continue
            files.append(str(path))
    except OSError:
        pass
    return files


__all__ = ["load_skill"]
