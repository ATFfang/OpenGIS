"""Resident worker tools.

These tools let the agent start and control long-running Python worker
applications. They are deliberately small wrappers around
ResidentWorkerManager; permission policy forces approval before agent use.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool
from opengis_backend.worker import get_worker_manager

WORKER_PROTOCOL_DOC = str(Path(__file__).resolve().parents[2] / "worker" / "WORKER_PROTOCOL.md")


def _workspace(ctx: ToolContext) -> str:
    workspace = (ctx.meta or {}).get("workspace_path")
    if not workspace:
        raise RuntimeError("A workspace is required to run resident workers.")
    return str(workspace)


@tool(
    name="start_worker",
    display_name="Start Resident Worker",
    description=(
        "Start a long-running Python worker application in the current workspace. "
        "Use for continuous data processing or background dynamic data production. "
        "The worker must print concise progress logs; max two running workers are allowed. "
        "OpenGIS saves the worker as a service package with `manifest.json`, `README.md`, "
        "`config.json`, `src/`, and the only entrypoint `main.py`. Keep `main.py` thin; "
        "put acquisition in `src/datasource.py`, transformation/state in `src/service.py`, "
        "and OpenGIS output in `src/publisher.py`. Do not edit `opengis_worker.py`; it is generated. "
        "For the full worker service protocol, read `python-backend/opengis_backend/worker/WORKER_PROTOCOL.md`. "
        "Dynamic map protocol available inside main.py: "
        "`from opengis_worker import emit_dynamic_layer_update, emit_dynamic_layer_diff`. "
        "Use `emit_dynamic_layer_update(...)` for the initial/full GeoJSON frame. "
        "Use `emit_dynamic_layer_diff(...)` for high-frequency updates after the first frame; "
        "diff mode requires every feature to have a stable `id`; OpenGIS also accepts full "
        "GeoJSON Feature objects in `diff.update` as upserts. Always use a stable `layer_id` "
        "and increasing `sequence`. For performance, pass `bbox`, `schema_changed=False`, and "
        "`size_bytes` when known."
    ),
    category="worker",
    params=[
        {"name": "name", "type": "string", "description": "Human-readable worker name."},
        {
            "name": "code",
            "type": "string",
            "description": (
                "Complete Python worker application code. For dynamic map rendering, import "
                "`emit_dynamic_layer_update` and/or `emit_dynamic_layer_diff` from the generated "
                "`opengis_worker` helper. First emit a full frame with `emit_dynamic_layer_update`; "
                "then use `emit_dynamic_layer_diff` with diff keys: `removeAll`, `remove`, "
                "`add`, and `update`. `update` accepts patch objects or full GeoJSON Features. "
                "Prefer a thin main.py that imports from `src` modules."
            ),
        },
        {
            "name": "files",
            "type": "object",
            "description": (
                "Optional worker package files as {relative_path: content}. Use this to create "
                "`src/datasource.py`, `src/service.py`, `src/publisher.py`, tests, or docs. "
                "Paths must stay inside the worker folder. Do not include main.py, metadata.json, "
                "stdout.log/stderr.log, or opengis_worker.py."
            ),
            "required": False,
        },
        {
            "name": "manifest",
            "type": "object",
            "description": (
                "Optional manifest fields for the worker service contract, such as kind, permissions, "
                "layers, datasource, cadence, and output contracts. OpenGIS controls id and entrypoint."
            ),
            "required": False,
        },
        {"name": "description", "type": "string", "description": "What this worker does.", "required": False},
        {"name": "worker_id", "type": "string", "description": "Optional stable worker id.", "required": False},
        {
            "name": "initial_health_timeout",
            "type": "number",
            "description": "Seconds to wait for the initial worker health check. Default 1.5.",
            "required": False,
        },
    ],
    examples=[
        (
            "Dynamic full frame: from opengis_worker import emit_dynamic_layer_update; "
            "emit_dynamic_layer_update(layer_id='live_points', name='Live Points', geojson=fc, sequence=1)"
        ),
        (
            "Dynamic diff frame: emit_dynamic_layer_diff(layer_id='live_points', "
            "diff={'add': [feature_with_id], 'remove': ['old_id'], "
            "'update': [{'id': 'a', 'addOrUpdateProperties': [{'key': 'speed', 'value': 42}]}]}, "
            "sequence=2, schema_changed=False)"
        ),
    ],
    returns=(
        "Worker metadata including worker_id, status, health, startup_check, folder, logs, and script path. "
        "Do not claim the worker is healthy unless health.state is ok; uncertain means the process is alive "
        "but has not produced enough evidence yet."
    ),
    needs_context=True,
    group="worker",
)
def start_worker(
    ctx: ToolContext,
    name: str,
    code: str,
    files: dict[str, str] | None = None,
    manifest: dict[str, Any] | None = None,
    description: str = "",
    worker_id: str | None = None,
    initial_health_timeout: float = 1.5,
) -> dict[str, Any]:
    result = get_worker_manager().start_worker(
        workspace_path=_workspace(ctx),
        name=name,
        code=code,
        description=description,
        worker_id=worker_id,
        files=files,
        manifest=manifest,
        initial_health_timeout=initial_health_timeout,
    )
    result["protocol_doc"] = WORKER_PROTOCOL_DOC
    return result


@tool(
    name="start_dynamic_map_worker",
    display_name="Start Dynamic Map Worker",
    description=(
        "Start a resident worker specifically for live moving map objects, dynamic points, "
        "and trajectories. Use this instead of execute_code/run_script whenever the user "
        "asks for live points, moving vehicles, refreshing tracks, background polling, "
        "or any map animation that should continue after the agent response finishes. "
        "Worker code should import `emit_moving_objects`, `emit_dynamic_points`, or "
        "`emit_dynamic_tracks` from opengis_worker. These high-level helpers automatically "
        "emit a full frame on first use of each layer id, then diff frames with increasing "
        "sequence numbers. Use the worker service package structure: thin `main.py`, "
        "`src/datasource.py` for polling, `src/service.py` for state/trajectory logic, "
        "and `src/publisher.py` for map emission. For the full worker service protocol, "
        "read `python-backend/opengis_backend/worker/WORKER_PROTOCOL.md`."
    ),
    category="worker",
    params=[
        {"name": "name", "type": "string", "description": "Human-readable worker name."},
        {
            "name": "code",
            "type": "string",
            "description": (
                "Complete Python worker application. Use `from opengis_worker import "
                "emit_moving_objects` for synchronized points + tracks. Keep the loop "
                "inside this worker, not inside execute_code. You normally do not need "
                "to pass `full=True`; high-level helpers full-initialize each layer id. "
                "Prefer a thin main.py that calls modules in `src/`."
            ),
        },
        {
            "name": "files",
            "type": "object",
            "description": (
                "Optional worker package files as {relative_path: content}. Use this for "
                "`src/datasource.py`, `src/service.py`, `src/publisher.py`, tests, and docs."
            ),
            "required": False,
        },
        {
            "name": "manifest",
            "type": "object",
            "description": "Optional worker service manifest fields. OpenGIS controls id and entrypoint.",
            "required": False,
        },
        {"name": "point_layer_id", "type": "string", "description": "Stable layer id for moving point features.", "required": False},
        {"name": "track_layer_id", "type": "string", "description": "Stable layer id for trajectory line features.", "required": False},
        {"name": "description", "type": "string", "description": "What this live map worker does.", "required": False},
        {"name": "worker_id", "type": "string", "description": "Optional stable worker id.", "required": False},
        {
            "name": "initial_health_timeout",
            "type": "number",
            "description": "Seconds to wait for the initial worker health check. Default 1.5.",
            "required": False,
        },
    ],
    examples=[
        (
            "from opengis_worker import emit_moving_objects; "
            "emit_moving_objects(point_layer_id='live_vehicles', track_layer_id='live_vehicle_tracks', "
            "points=[{'id':'v1','lon':121.5,'lat':31.2}], tracks={'v1': [[121.5,31.2],[121.51,31.21]]}, "
            "sequence=1, full=True)"
        ),
    ],
    returns=(
        "Worker metadata including worker_id, status, health, startup_check, folder, logs, and script path. "
        "Only report success when health.state is ok."
    ),
    needs_context=True,
    group="worker",
)
def start_dynamic_map_worker(
    ctx: ToolContext,
    name: str,
    code: str,
    files: dict[str, str] | None = None,
    manifest: dict[str, Any] | None = None,
    point_layer_id: str | None = None,
    track_layer_id: str | None = None,
    description: str = "",
    worker_id: str | None = None,
    initial_health_timeout: float = 1.5,
) -> dict[str, Any]:
    metadata = []
    if point_layer_id:
        metadata.append(f"point_layer_id={point_layer_id}")
    if track_layer_id:
        metadata.append(f"track_layer_id={track_layer_id}")
    combined_description = description
    if metadata:
        combined_description = (description + "\n" if description else "") + "Dynamic map layers: " + ", ".join(metadata)
    dynamic_layers = []
    dynamic_config: dict[str, Any] = {"layers": {}}
    if point_layer_id:
        dynamic_config["layers"]["points"] = point_layer_id
        dynamic_layers.append({
            "id": point_layer_id,
            "role": "points",
            "geometry": "Point",
            "dynamic": True,
        })
    if track_layer_id:
        dynamic_config["layers"]["tracks"] = track_layer_id
        dynamic_layers.append({
            "id": track_layer_id,
            "role": "tracks",
            "geometry": "LineString",
            "dynamic": True,
        })
    merged_manifest = dict(manifest or {})
    if dynamic_layers:
        existing_layers = merged_manifest.get("layers")
        merged_manifest["layers"] = [
            *(existing_layers if isinstance(existing_layers, list) else []),
            *dynamic_layers,
        ]
    result = get_worker_manager().start_worker(
        workspace_path=_workspace(ctx),
        name=name,
        code=code,
        description=combined_description,
        worker_id=worker_id,
        files=files,
        manifest=merged_manifest,
        config=dynamic_config if dynamic_layers else None,
        initial_health_timeout=initial_health_timeout,
    )
    result["protocol_doc"] = WORKER_PROTOCOL_DOC
    return result


@tool(
    name="get_worker",
    display_name="Inspect Resident Worker",
    description=(
        "Inspect one resident worker's current status, health, resources, startup check, paths, and recent logs. "
        "Use this after starting a worker or when the user asks whether a worker is running normally."
    ),
    category="worker",
    params=[
        {"name": "worker_id", "type": "string", "description": "Worker id to inspect."},
        {"name": "include_logs", "type": "boolean", "description": "Whether to include recent logs. Default true.", "required": False},
    ],
    returns="Worker metadata including health.state, resources, last_error, and logs.",
    needs_context=True,
    group="worker",
)
def get_worker(ctx: ToolContext, worker_id: str, include_logs: bool = True) -> dict[str, Any]:
    workspace = _workspace(ctx)
    return get_worker_manager().get_worker(worker_id, include_logs=include_logs, workspace_path=workspace)


@tool(
    name="wait_worker_update",
    display_name="Wait For Worker Update",
    description=(
        "Wait briefly for a resident worker to emit new output, update health, or exit. "
        "Use this after start_worker/restart_worker instead of execute_code with time.sleep. "
        "The timeout is capped at 60 seconds; prefer 5-20 seconds and inspect the returned "
        "wait.changed, wait.timed_out, health.state, and logs before deciding whether to edit/restart."
    ),
    category="worker",
    params=[
        {"name": "worker_id", "type": "string", "description": "Worker id to wait for."},
        {
            "name": "since_ts",
            "type": "number",
            "description": "Optional baseline log timestamp. If omitted, waits for output after the call starts.",
            "required": False,
        },
        {
            "name": "timeout",
            "type": "number",
            "description": "Maximum seconds to wait, capped at 60. Default 20.",
            "required": False,
        },
        {"name": "include_logs", "type": "boolean", "description": "Whether to include recent logs. Default true.", "required": False},
    ],
    returns="Worker metadata plus wait.changed/timed_out/status_changed.",
    needs_context=True,
    group="worker",
)
def wait_worker_update(
    ctx: ToolContext,
    worker_id: str,
    since_ts: float | None = None,
    timeout: float = 20.0,
    include_logs: bool = True,
) -> dict[str, Any]:
    workspace = _workspace(ctx)
    return get_worker_manager().wait_worker_update(
        worker_id,
        workspace_path=workspace,
        since_ts=since_ts,
        timeout=timeout,
        include_logs=include_logs,
    )


@tool(
    name="restart_worker",
    display_name="Restart Resident Worker",
    description=(
        "Restart an existing resident worker in the same folder with the same worker id. "
        "Optionally provide replacement Python code and/or package files after reading the current "
        "main.py, manifest, README, relevant src files, and logs. "
        "Use this after inspecting worker output and fixing code; then check the returned health/startup_check."
    ),
    category="worker",
    params=[
        {"name": "worker_id", "type": "string", "description": "Worker id to restart."},
        {
            "name": "code",
            "type": "string",
            "description": "Optional complete replacement Python code for main.py.",
            "required": False,
        },
        {
            "name": "files",
            "type": "object",
            "description": "Optional worker package files to replace or add, keyed by relative path.",
            "required": False,
        },
        {
            "name": "manifest",
            "type": "object",
            "description": "Optional manifest fields to merge into manifest.json.",
            "required": False,
        },
        {"name": "reason", "type": "string", "description": "Optional restart reason.", "required": False},
        {
            "name": "initial_health_timeout",
            "type": "number",
            "description": "Seconds to wait for the initial worker health check. Default 1.5.",
            "required": False,
        },
    ],
    returns=(
        "Updated worker metadata including health, startup_check, logs, folder, and script path. "
        "Only treat the restart as healthy when health.state is ok."
    ),
    needs_context=True,
    group="worker",
)
def restart_worker(
    ctx: ToolContext,
    worker_id: str,
    code: str | None = None,
    files: dict[str, str] | None = None,
    manifest: dict[str, Any] | None = None,
    reason: str = "agent_restart",
    initial_health_timeout: float = 1.5,
) -> dict[str, Any]:
    workspace = _workspace(ctx)
    return get_worker_manager().restart_worker(
        worker_id,
        code=code,
        files=files,
        manifest=manifest,
        reason=reason,
        initial_health_timeout=initial_health_timeout,
        workspace_path=workspace,
    )


@tool(
    name="list_workers",
    display_name="List Resident Workers",
    description=(
        "List resident workers in the current workspace with status, health, resources, paths, and optional logs. "
        "Use this to monitor background workers instead of assuming they are healthy."
    ),
    category="worker",
    params=[
        {"name": "include_logs", "type": "boolean", "description": "Whether to include recent logs. Default false.", "required": False},
    ],
    returns="List of worker metadata records.",
    needs_context=True,
    group="worker",
)
def list_workers(ctx: ToolContext, include_logs: bool = False) -> dict[str, Any]:
    return {
        "workers": get_worker_manager().list_workers(
            include_logs=include_logs,
            workspace_path=_workspace(ctx),
        )
    }


@tool(
    name="pause_worker",
    display_name="Pause Resident Worker",
    description="Pause a running resident worker by terminating its process while keeping its folder and metadata.",
    category="worker",
    params=[
        {"name": "worker_id", "type": "string", "description": "Worker id to pause."},
        {"name": "reason", "type": "string", "description": "Optional pause reason.", "required": False},
    ],
    returns="Updated worker metadata.",
    needs_context=True,
    group="worker",
)
def pause_worker(ctx: ToolContext, worker_id: str, reason: str = "agent_pause") -> dict[str, Any]:
    workspace = _workspace(ctx)
    return get_worker_manager().pause_worker(worker_id, reason=reason, workspace_path=workspace)


@tool(
    name="delete_worker",
    display_name="Delete Resident Worker",
    description=(
        "Stop a resident worker and delete its workspace worker folder. "
        "This is a destructive operation: after success, the worker registry entry and on-disk folder are gone. "
        "If folder_deleted is false or the tool errors, report that deletion failed."
    ),
    category="worker",
    params=[
        {"name": "worker_id", "type": "string", "description": "Worker id to delete."},
    ],
    returns="Deleted worker metadata including folder_deleted.",
    needs_context=True,
    group="worker",
)
def delete_worker(ctx: ToolContext, worker_id: str) -> dict[str, Any]:
    workspace = _workspace(ctx)
    return get_worker_manager().delete_worker(worker_id, workspace_path=workspace)
