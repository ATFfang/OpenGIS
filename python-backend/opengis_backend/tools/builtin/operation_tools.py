"""Agent tools for reusable Operations."""

from __future__ import annotations

import json
from typing import Any, Optional

from opengis_backend.operations import OperationStore
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool


def _store(ctx: ToolContext) -> OperationStore:
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    if not workspace:
        raise RuntimeError("A workspace is required to use reusable operations.")
    return OperationStore.from_workspace(workspace)


def _json_obj(value: Any, *, default: Optional[dict[str, Any]] = None, name: str = "value") -> dict[str, Any]:
    if value is None:
        return dict(default or {})
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return dict(default or {})
        value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return dict(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            return [item.strip() for item in text.split(",") if item.strip()]
    if not isinstance(value, (list, tuple)):
        raise ValueError("dependencies must be a list, JSON array, or comma-separated string")
    return [str(item).strip() for item in value if str(item).strip()]


@tool(
    name="list_operations",
    display_name="List Reusable Operations",
    description=(
        "List reusable Operations from the current workspace and OpenGIS built-ins. "
        "Use this before writing complex GIS/modeling code when an existing operation may solve the task."
    ),
    category="system",
    params=[
        {"name": "query", "type": "string", "required": False, "description": "Optional text filter over id/name/description/status."},
        {"name": "limit", "type": "number", "required": False, "description": "Maximum operations to return. Default 50."},
    ],
    returns="dict with success, operations, operation_root, and operation_roots.",
    examples=["Find reusable regression operations", "List existing hotspot analysis operations"],
    tags=["operation", "reuse", "project"],
    needs_context=True,
)
def list_operations(ctx: ToolContext, query: str = "", limit: int | float = 50) -> dict[str, Any]:
    store = _store(ctx)
    return {
        "success": True,
        "operation_root": str(store.root),
        "operation_roots": store.roots,
        "operations": store.list(query=str(query or ""), limit=int(limit or 50)),
    }


@tool(
    name="get_operation",
    display_name="Get Reusable Operation",
    description="Read an operation contract, README, and optionally its main.py code.",
    category="system",
    params=[
        {"name": "operation_id", "type": "string", "required": True, "description": "Operation id from list_operations."},
        {"name": "include_code", "type": "boolean", "required": False, "description": "Whether to include main.py content."},
        {"name": "max_code_chars", "type": "number", "required": False, "description": "Maximum code chars when include_code=true."},
    ],
    returns="dict with operation metadata, readme, and optional code.",
    examples=["Inspect gnnwr_regression before running it"],
    tags=["operation", "reuse", "read"],
    needs_context=True,
)
def get_operation(
    ctx: ToolContext,
    operation_id: str,
    include_code: bool = False,
    max_code_chars: int | float = 40000,
) -> dict[str, Any]:
    operation = _store(ctx).load(
        operation_id,
        include_readme=True,
        include_code=bool(include_code),
        max_code_chars=int(max_code_chars or 40000),
    )
    return {"success": True, "operation": operation}


@tool(
    name="copy_operation_to_workspace",
    display_name="Copy Operation To Workspace",
    description=(
        "Copy a built-in read-only Operation into the current workspace as an editable draft. "
        "Use this when a built-in operation fails and the user asks to repair or customize the operation; "
        "after copying, use edit_operation on the workspace copy instead of writing one-off scripts."
    ),
    category="system",
    params=[
        {"name": "operation_id", "type": "string", "required": True, "description": "Operation id to copy."},
        {"name": "overwrite", "type": "boolean", "required": False, "description": "Overwrite an existing workspace copy."},
    ],
    returns="dict with success and the workspace operation.",
    examples=["Copy kernel_density to workspace before repairing it"],
    tags=["operation", "copy", "reuse", "repair"],
    needs_context=True,
)
def copy_operation_to_workspace(
    ctx: ToolContext,
    operation_id: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    operation = _store(ctx).copy_to_workspace(operation_id, overwrite=bool(overwrite))
    return {"success": True, "operation": operation}


@tool(
    name="validate_operation",
    display_name="Validate Reusable Operation",
    description=(
        "Validate an Operation's contract before running it. Checks standard --input/--output protocol, "
        "input_schema required keys, code-level params[...] usage, and supplied params. Use this after "
        "get_operation or before rerunning a failed operation."
    ),
    category="system",
    params=[
        {"name": "operation_id", "type": "string", "required": True, "description": "Operation id."},
        {"name": "params", "type": "object", "required": False, "description": "Optional run params to validate against the operation contract."},
    ],
    returns="dict with success, ok, errors, warnings, and inferred contract details.",
    examples=["Validate dbscan_clustering before rerunning it"],
    tags=["operation", "validate", "repair", "contract"],
    needs_context=True,
)
def validate_operation(
    ctx: ToolContext,
    operation_id: str,
    params: Any = None,
) -> dict[str, Any]:
    parsed_params = _json_obj(params, name="params") if params is not None else None
    return _store(ctx).validate_contract(operation_id, params=parsed_params)


@tool(
    name="run_operation",
    display_name="Run Reusable Operation",
    description=(
        "Run a reusable operation using its standard JSON protocol: "
        "python main.py --input <input.json> --output <output.json>. "
        "The operation receives {workspace, operation_id, params}."
    ),
    category="system",
    params=[
        {"name": "operation_id", "type": "string", "required": True, "description": "Operation id."},
        {"name": "params", "type": "object", "required": True, "description": "JSON object matching operation.input_schema."},
        {"name": "timeout_seconds", "type": "number", "required": False, "description": "Execution timeout. Default 600."},
    ],
    returns="dict with success, run_id, output, stdout/stderr paths.",
    examples=["Run poi_hotspot_analysis with a CSV path and coordinate fields"],
    tags=["operation", "run", "reuse"],
    needs_context=True,
)
def run_operation(
    ctx: ToolContext,
    operation_id: str,
    params: Any,
    timeout_seconds: int | float = 600,
) -> dict[str, Any]:
    parsed_params = _json_obj(params, name="params")
    record = _store(ctx).run(operation_id, parsed_params, timeout_seconds=int(timeout_seconds or 600))
    return {"success": True, **record}


@tool(
    name="create_operation",
    display_name="Create Reusable Operation",
    description=(
        "Create a new draft reusable operation from code that already follows the standard "
        "--input/--output JSON protocol. Use this to persist a complex capability for future reuse."
    ),
    category="system",
    params=[
        {"name": "operation_id", "type": "string", "required": True, "description": "Stable id, e.g. gnnwr_regression."},
        {"name": "name", "type": "string", "required": True, "description": "Human-readable operation name."},
        {"name": "description", "type": "string", "required": False, "description": "What this operation does and when to use it."},
        {"name": "code", "type": "string", "required": True, "description": "main.py source code implementing the standard protocol."},
        {"name": "input_schema", "type": "object", "required": False, "description": "JSON object describing required params."},
        {"name": "output_schema", "type": "object", "required": False, "description": "JSON object describing output contract."},
        {"name": "dependencies", "type": "array", "required": False, "description": "Python package dependencies."},
        {"name": "overwrite", "type": "boolean", "required": False, "description": "Overwrite an existing operation draft."},
    ],
    returns="dict with success and operation.",
    examples=["Create a reusable GNNWR operation after validating code"],
    tags=["operation", "create", "reuse"],
    needs_context=True,
)
def create_operation(
    ctx: ToolContext,
    operation_id: str,
    name: str,
    code: str,
    description: str = "",
    input_schema: Any = None,
    output_schema: Any = None,
    dependencies: Any = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    operation = _store(ctx).create(
        operation_id=operation_id,
        name=name,
        description=description,
        code=code,
        input_schema=_json_obj(input_schema, default={}, name="input_schema") or None,
        output_schema=_json_obj(output_schema, default={}, name="output_schema") or None,
        dependencies=_string_list(dependencies),
        overwrite=bool(overwrite),
    )
    return {"success": True, "operation": operation}


@tool(
    name="edit_operation",
    display_name="Edit Reusable Operation",
    description=(
        "Modify an existing reusable Operation after inspection or a failed run. "
        "Use this to fix main.py, schemas, dependencies, README, or metadata instead "
        "of abandoning the operation and writing one-off code. Built-in operations are read-only; "
        "create a workspace copy before editing them."
    ),
    category="system",
    params=[
        {"name": "operation_id", "type": "string", "required": True, "description": "Existing operation id."},
        {"name": "code", "type": "string", "required": False, "description": "Replacement main.py source code. Must implement the --input/--output JSON protocol."},
        {"name": "name", "type": "string", "required": False, "description": "Updated human-readable name."},
        {"name": "description", "type": "string", "required": False, "description": "Updated operation description."},
        {"name": "input_schema", "type": "object", "required": False, "description": "Updated input contract."},
        {"name": "output_schema", "type": "object", "required": False, "description": "Updated output contract."},
        {"name": "dependencies", "type": "array", "required": False, "description": "Updated Python package dependencies."},
        {"name": "readme", "type": "string", "required": False, "description": "Updated README.md content."},
        {"name": "status", "type": "string", "required": False, "description": "Updated status, usually draft after edits or validated after a successful run."},
    ],
    returns="dict with success and updated operation.",
    examples=["Fix dbscan_clustering after a missing input_path error"],
    tags=["operation", "edit", "reuse", "repair"],
    needs_context=True,
)
def edit_operation(
    ctx: ToolContext,
    operation_id: str,
    code: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    input_schema: Any = None,
    output_schema: Any = None,
    dependencies: Any = None,
    readme: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    operation = _store(ctx).update(
        operation_id,
        name=name,
        description=description,
        code=code,
        input_schema=_json_obj(input_schema, default={}, name="input_schema") if input_schema is not None else None,
        output_schema=_json_obj(output_schema, default={}, name="output_schema") if output_schema is not None else None,
        dependencies=_string_list(dependencies) if dependencies is not None else None,
        readme=readme,
        status=status,
    )
    return {"success": True, "operation": operation}


@tool(
    name="promote_script_to_operation",
    display_name="Promote Script To Operation",
    description=(
        "Package a persisted workspace Python script as a draft reusable Operation. "
        "The promoted main.py should be reviewed and adapted to the standard --input/--output protocol before stable reuse."
    ),
    category="system",
    params=[
        {"name": "script_path", "type": "string", "required": True, "description": "Workspace-relative or absolute .py script path."},
        {"name": "operation_id", "type": "string", "required": False, "description": "Stable operation id. Defaults to script stem."},
        {"name": "name", "type": "string", "required": False, "description": "Human-readable operation name."},
        {"name": "description", "type": "string", "required": False, "description": "Operation description."},
        {"name": "input_schema", "type": "object", "required": False, "description": "Input contract."},
        {"name": "output_schema", "type": "object", "required": False, "description": "Output contract."},
        {"name": "dependencies", "type": "array", "required": False, "description": "Python package dependencies."},
        {"name": "run_id", "type": "string", "required": False, "description": "Source agent run id."},
        {"name": "overwrite", "type": "boolean", "required": False, "description": "Overwrite existing operation."},
    ],
    returns="dict with success and operation.",
    examples=["Promote script/poi_dbscan_clustering.py into an operation"],
    tags=["operation", "promote", "reuse"],
    needs_context=True,
)
def promote_script_to_operation(
    ctx: ToolContext,
    script_path: str,
    operation_id: str = "",
    name: str = "",
    description: str = "",
    input_schema: Any = None,
    output_schema: Any = None,
    dependencies: Any = None,
    run_id: Optional[str] = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    operation = _store(ctx).promote_script(
        script_path=script_path,
        operation_id=operation_id,
        name=name,
        description=description,
        input_schema=_json_obj(input_schema, default={}, name="input_schema") or None,
        output_schema=_json_obj(output_schema, default={}, name="output_schema") or None,
        dependencies=_string_list(dependencies),
        run_id=run_id,
        overwrite=bool(overwrite),
    )
    return {"success": True, "operation": operation}
