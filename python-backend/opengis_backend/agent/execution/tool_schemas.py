"""Function-call schemas for built-in execution tools."""

from __future__ import annotations

from typing import Any


CODE_ONLY_TOOLS = {"save_plot"}

EXECUTE_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": (
            "Run Python in a sandbox ONLY when no other tool matches. Has numpy, "
            "pandas, geopandas, shapely, rasterio, matplotlib, seaborn and the "
            "registered OpenGIS tools as top-level functions; missing packages are "
            "auto-installed when permitted (don't downgrade method for that). code "
            "must be code-only Python: no reasoning, no comment narration, no "
            "Markdown fences, no <think> tags. Never use it to sleep while a worker "
            "runs; call wait_worker_update or get_worker instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Raw executable Python only: no Markdown fences, hidden "
                        "reasoning, planning prose, or strategy comments."
                    ),
                },
                "persist": {
                    "type": "boolean",
                    "description": (
                        "Normal chat: persist as reusable/auditable script. "
                        "false for one-off inspection. Workflow runs always persist."
                    ),
                },
                "script_name": {
                    "type": "string",
                    "description": "Short semantic script name used in the persisted filename when persist is true.",
                },
                "description": {
                    "type": "string",
                    "description": "Brief purpose/metadata for this code when persisted.",
                },
            },
            "required": ["code"],
        },
    },
}


RUN_SCRIPT_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_script_file",
        "description": (
            "Run an existing persisted Python script file from the workspace script/ directory. "
            "Use after read_script/edit_file to rerun the same reusable code asset instead of "
            "copying it into a new execute_code call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script_path": {
                    "type": "string",
                    "description": "Path or id of a .py script under the workspace script/ directory.",
                },
            },
            "required": ["script_path"],
        },
    },
}


def build_tool_schemas(registered: list[Any]) -> list[dict]:
    """Build OpenAI-compatible schemas for all executable tools plus execute_code."""
    schemas = [
        rs.schema.to_openai_schema()
        for rs in registered
        if rs.schema.name not in CODE_ONLY_TOOLS
    ]
    schemas.append(EXECUTE_CODE_SCHEMA)
    schemas.append(RUN_SCRIPT_FILE_SCHEMA)
    return schemas


__all__ = [
    "CODE_ONLY_TOOLS",
    "EXECUTE_CODE_SCHEMA",
    "RUN_SCRIPT_FILE_SCHEMA",
    "build_tool_schemas",
]
