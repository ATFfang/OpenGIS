import base64
import json
import shutil
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.builtin.bash_tool import _bash_sync
from opengis_backend.tools.builtin.display import get_map_state as get_map_state_tool
from opengis_backend.tools.builtin.display import get_raster_info as get_raster_info_tool
from opengis_backend.tools.builtin.display import highlight_features as highlight_features_tool
from opengis_backend.tools.builtin.display import add_raster as add_raster_tool
from opengis_backend.tools.builtin.display import set_raster_style as set_raster_style_tool
from opengis_backend.tools.builtin.display import set_basemap as set_basemap_tool
from opengis_backend.tools.builtin.display import set_categorized_style as set_categorized_style_tool
from opengis_backend.tools.builtin.display import set_extrusion_style as set_extrusion_style_tool
from opengis_backend.tools.builtin.display import set_graduated_style as set_graduated_style_tool
from opengis_backend.tools.builtin.display import set_layer_filter as set_layer_filter_tool
from opengis_backend.tools.builtin.display import set_layer_label as set_layer_label_tool
from opengis_backend.tools.builtin.display import set_layer_order as set_layer_order_tool
from opengis_backend.tools.builtin.display import set_layer_visual_variables as set_layer_visual_variables_tool
from opengis_backend.tools.builtin.display import update_legend_spec as update_legend_spec_tool
from opengis_backend.tools.builtin.display import update_layer_style as update_layer_style_tool
from opengis_backend.tools.builtin.csv_to_geojson import csv_to_geojson as csv_to_geojson_tool
from opengis_backend.tools.builtin.agent_debug_tools import debug_agent_context as debug_agent_context_tool
from opengis_backend.tools.builtin.edit_file_tool import edit_file as edit_file_tool
from opengis_backend.tools.builtin.file_ops import file_exists as file_exists_tool
from opengis_backend.tools.builtin.file_ops import list_directory as list_directory_tool
from opengis_backend.tools.builtin.glob_tool import glob as glob_tool
from opengis_backend.tools.builtin.grep_tool import grep as grep_tool
from opengis_backend.tools.builtin.operation_tools import create_operation as create_operation_tool
from opengis_backend.tools.builtin.operation_tools import copy_operation_to_workspace as copy_operation_to_workspace_tool
from opengis_backend.tools.builtin.operation_tools import edit_operation as edit_operation_tool
from opengis_backend.tools.builtin.operation_tools import get_operation as get_operation_tool
from opengis_backend.tools.builtin.operation_tools import list_operations as list_operations_tool
from opengis_backend.tools.builtin.operation_tools import promote_script_to_operation as promote_script_to_operation_tool
from opengis_backend.tools.builtin.operation_tools import run_operation as run_operation_tool
from opengis_backend.tools.builtin.operation_tools import validate_operation as validate_operation_tool
from opengis_backend.tools.builtin.read_file_tool import read_file as read_file_tool
from opengis_backend.tools.builtin.web_tools import webfetch as webfetch_tool
from opengis_backend.tools.builtin.write_file_tool import write_file as write_file_tool
from opengis_backend.tools.builtin.script_tools import list_scripts as list_scripts_tool
from opengis_backend.tools.builtin.script_tools import read_script as read_script_tool
from opengis_backend.agent.factory_common import compose_system_prompt
from opengis_backend.agent.tools import filter_agent_tools
from opengis_backend.agent.governance.profile import AgentProfile
from opengis_backend.agent.execution.tool_runtime import ToolRuntime, build_tool_schemas, validate_execute_code_payload
from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.context.context_persistence import save_context
from opengis_backend.agent.session.session import SessionStore
from opengis_backend.integrations.osm.overpass import _normalize_overpass_query
from opengis_backend.integrations.osm.tools import osm_call as osm_call_tool
from opengis_backend.integrations.datasource.tools import datasource_call as datasource_call_tool
from opengis_backend.runs.archive import RunArchive
from opengis_backend.worker.manager import ResidentWorkerManager

read_file = read_file_tool.__wrapped__
edit_file = edit_file_tool.__wrapped__
write_file = write_file_tool.__wrapped__
webfetch = webfetch_tool.__wrapped__
list_scripts = list_scripts_tool.__wrapped__
read_script = read_script_tool.__wrapped__
set_basemap = set_basemap_tool.__wrapped__
get_map_state = get_map_state_tool.__wrapped__
get_raster_info = get_raster_info_tool.__wrapped__
update_layer_style = update_layer_style_tool.__wrapped__
set_raster_style = set_raster_style_tool.__wrapped__
set_graduated_style = set_graduated_style_tool.__wrapped__
set_categorized_style = set_categorized_style_tool.__wrapped__
set_extrusion_style = set_extrusion_style_tool.__wrapped__
set_layer_filter = set_layer_filter_tool.__wrapped__
set_layer_label = set_layer_label_tool.__wrapped__
highlight_features = highlight_features_tool.__wrapped__
add_raster = add_raster_tool.__wrapped__
set_layer_order = set_layer_order_tool.__wrapped__
set_layer_visual_variables = set_layer_visual_variables_tool.__wrapped__
update_legend_spec = update_legend_spec_tool.__wrapped__
osm_call = osm_call_tool.__wrapped__
datasource_call = datasource_call_tool.__wrapped__
csv_to_geojson = csv_to_geojson_tool.__wrapped__
debug_agent_context = debug_agent_context_tool.__wrapped__
file_exists = file_exists_tool.__wrapped__
list_directory = list_directory_tool.__wrapped__
glob = glob_tool.__wrapped__
grep = grep_tool.__wrapped__
create_operation = create_operation_tool.__wrapped__
copy_operation_to_workspace = copy_operation_to_workspace_tool.__wrapped__
edit_operation = edit_operation_tool.__wrapped__
get_operation = get_operation_tool.__wrapped__
list_operations = list_operations_tool.__wrapped__
promote_script_to_operation = promote_script_to_operation_tool.__wrapped__
run_operation = run_operation_tool.__wrapped__
validate_operation = validate_operation_tool.__wrapped__


class AgentToolUpgradeTests(unittest.TestCase):
    def test_operation_lifecycle_create_list_get_run_and_promote(self) -> None:
        operation_code = """\
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
params = payload["params"]
values = params["values"]
result = {
    "success": True,
    "metrics": {"sum": sum(values)},
    "artifacts": [],
    "layers": [],
    "summary": f"sum={sum(values)}",
}
Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
"""
        with TemporaryDirectory() as tmp:
            ctx = ToolContext(meta={"workspace_path": tmp})
            input_schema = {
                "type": "object",
                "required": ["values"],
                "properties": {"values": {"type": "array"}},
            }

            created = create_operation(
                ctx,
                operation_id="sum_values",
                name="Sum Values",
                description="Sum numeric values.",
                code=operation_code,
                input_schema=input_schema,
                dependencies=["numpy"],
            )
            self.assertTrue(created["success"], created)
            self.assertEqual(created["operation"]["id"], "sum_values")

            listed = list_operations(ctx, query="sum_values")
            self.assertEqual(len(listed["operations"]), 1)
            self.assertEqual(listed["operations"][0]["id"], "sum_values")
            self.assertEqual(listed["operations"][0]["scope"], "workspace")

            loaded = get_operation(ctx, "sum_values", include_code=True)
            self.assertIn("--input", loaded["operation"]["code"])

            edited_code = operation_code.replace('"sum": sum(values)', '"sum": sum(values), "count": len(values)')
            edited = edit_operation(
                ctx,
                operation_id="sum_values",
                code=edited_code,
                description="Sum numeric values and count records.",
                input_schema=input_schema,
                dependencies=["numpy", "pandas"],
                status="draft",
            )
            self.assertTrue(edited["success"], edited)
            self.assertEqual(edited["operation"]["revision"], 2)
            self.assertEqual(edited["operation"]["status"], "draft")
            self.assertEqual(
                edited["operation"]["runtime"]["dependencies"],
                ["numpy", "pandas"],
            )

            run = run_operation(ctx, "sum_values", {"values": [1, 2, 3]})
            self.assertTrue(run["success"], run)
            self.assertEqual(run["output"]["metrics"]["sum"], 6)
            self.assertEqual(run["output"]["metrics"]["count"], 3)

            loaded_after_run = get_operation(ctx, "sum_values")
            self.assertEqual(loaded_after_run["operation"]["status"], "validated")
            self.assertEqual(
                loaded_after_run["operation"]["provenance"]["last_success_run"],
                run["run_id"],
            )

            script_dir = Path(tmp) / "script"
            script_dir.mkdir()
            script = script_dir / "demo_operation.py"
            script.write_text(operation_code, encoding="utf-8")
            promoted = promote_script_to_operation(
                ctx,
                script_path="script/demo_operation.py",
                operation_id="demo_operation",
                name="Demo Operation",
            )
            self.assertTrue(promoted["success"], promoted)
            self.assertEqual(promoted["operation"]["id"], "demo_operation")

    def test_builtin_operations_are_shared_and_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            ctx = ToolContext(meta={"workspace_path": tmp})

            listed = list_operations(ctx, query="kernel_density")
            self.assertTrue(listed["operations"], listed)
            self.assertEqual(listed["operations"][0]["id"], "kernel_density")
            self.assertEqual(listed["operations"][0]["scope"], "builtin")
            self.assertTrue(listed["operations"][0]["read_only"])

            loaded = get_operation(ctx, "kernel_density")
            self.assertEqual(loaded["operation"]["scope"], "builtin")
            self.assertTrue(loaded["operation"]["read_only"])
            self.assertEqual(loaded["operation"]["path"], "builtin://kernel_density")

            with self.assertRaises(Exception):
                edit_operation(ctx, operation_id="kernel_density", description="Should not edit builtin.")

            copied = copy_operation_to_workspace(ctx, "kernel_density")
            self.assertTrue(copied["success"], copied)
            self.assertEqual(copied["operation"]["scope"], "workspace")
            self.assertFalse(copied["operation"]["read_only"])
            self.assertEqual(copied["operation"]["status"], "draft")

            edited = edit_operation(ctx, operation_id="kernel_density", description="Workspace override.")
            self.assertTrue(edited["success"], edited)
            self.assertEqual(edited["operation"]["description"], "Workspace override.")

    def test_debug_agent_context_returns_projection_anchors(self) -> None:
        with TemporaryDirectory() as tmp:
            conversation_id = "conv-debug"
            ctx_manager = ContextManager(provider_raw_recent=4, recent_user_turns_for_provider=4)
            ctx_manager.add_user_message("能不能价格高的在上面")
            ctx_manager.add_assistant_message("已调整价格顺序。")
            ctx_manager.add_user_message("你要修正我的operation，而不是饶过他自己写脚本")
            ctx_manager.add_tool_result(
                "call-run",
                "run_operation",
                json.dumps(
                    {
                        "success": False,
                        "operation_id": "dbscan_clustering",
                        "status": "failed",
                        "error": "KeyError: input_path",
                    },
                    ensure_ascii=False,
                ),
            )
            save_context(tmp, conversation_id, ctx_manager)
            ctx = ToolContext(meta={"workspace_path": tmp}, conversation_id=conversation_id)

            result = debug_agent_context(ctx)

            self.assertTrue(result["success"], result)
            self.assertIn("Recent User Requests", result["recent_user_anchor"])
            self.assertIn("你要修正我的operation", result["recent_user_anchor"])
            self.assertIn("Runtime State Anchors", result["runtime_anchor"])
            self.assertIn("dbscan_clustering", result["runtime_anchor"])

    def test_operation_contract_validation_catches_code_required_params_before_run(self) -> None:
        operation_code = """\
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
params = payload["params"]
input_path = params["input_path"]
Path(args.output).write_text(json.dumps({"success": True, "input_path": input_path}), encoding="utf-8")
"""
        with TemporaryDirectory() as tmp:
            ctx = ToolContext(meta={"workspace_path": tmp})
            created = create_operation(
                ctx,
                operation_id="needs_input_path",
                name="Needs Input Path",
                description="Reads input_path from params.",
                code=operation_code,
                input_schema={"type": "object", "required": [], "properties": {}},
            )
            self.assertTrue(created["success"], created)

            validation = validate_operation(ctx, "needs_input_path", {})

            self.assertFalse(validation["ok"], validation)
            self.assertEqual(validation["errors"][0]["code"], "missing_code_required_params")
            self.assertIn("input_path", validation["errors"][0]["keys"])
            self.assertIn("input_path", validation["warnings"][0]["keys"])

            with self.assertRaisesRegex(Exception, "contract validation failed.*input_path"):
                run_operation(ctx, "needs_input_path", {})

            runs_dir = Path(tmp) / ".opengis" / "operations" / "needs_input_path" / "runs"
            self.assertFalse(runs_dir.exists() and any(runs_dir.iterdir()))

    def test_system_prompt_renders_literal_edit_file_batch_example(self) -> None:
        with TemporaryDirectory() as tmp:
            ctx = ToolContext(meta={"workspace_path": tmp})
            prompt = compose_system_prompt([], ctx, include_workspace_write_note=True)

            self.assertIn('{"old_string": "...", "new_string": "..."}', prompt)
            self.assertIn("## Executable Tools", prompt)
            self.assertIn("Do not switch the basemap", prompt)

    def test_agent_tool_filter_removes_set_basemap(self) -> None:
        set_basemap_record = SimpleNamespace(schema=SimpleNamespace(name="set_basemap"))
        list_layers_record = SimpleNamespace(schema=SimpleNamespace(name="list_layers"))

        filtered = filter_agent_tools([set_basemap_record, list_layers_record])

        self.assertEqual([item.schema.name for item in filtered], ["list_layers"])

    def test_default_build_profile_excludes_orchestration_tool_groups(self) -> None:
        profile = AgentProfile.gis_build(max_steps=4)
        records = [
            SimpleNamespace(schema=SimpleNamespace(name="execute_code", group="core")),
            SimpleNamespace(schema=SimpleNamespace(name="osm_call", group="osm")),
            SimpleNamespace(schema=SimpleNamespace(name="run_subagent", group="subagent")),
            SimpleNamespace(schema=SimpleNamespace(name="start_worker", group="worker")),
            SimpleNamespace(schema=SimpleNamespace(name="create_workflow", group="workflow")),
            SimpleNamespace(schema=SimpleNamespace(name="report_write", group="report")),
        ]

        selected = [
            item for item in records
            if profile.tool_groups is None or item.schema.group in profile.tool_groups
        ]

        self.assertEqual(
            [item.schema.name for item in selected],
            ["execute_code", "osm_call"],
        )

    def test_set_basemap_tool_rejects_agent_initiated_switches(self) -> None:
        ctx = ToolContext(meta={})
        result = set_basemap(ctx, "satellite")

        self.assertFalse(result["success"])
        self.assertIn("disabled", result["error"])

    def test_get_map_state_reads_frontend_state(self) -> None:
        async def request(method: str, params: dict):
            self.assertEqual(method, "rpc.ui.map.get_state")
            self.assertEqual(params, {})
            return {"basemap_visible": True, "layer_count": 2}

        ctx = ToolContext(request_fn=request)

        self.assertEqual(get_map_state(ctx), {"basemap_visible": True, "layer_count": 2})

    def test_add_raster_accepts_raster_path_alias(self) -> None:
        requests: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests.append((method, params))
            return {
                "layer_id": params["layer_id"],
                "name": params["name"],
                "bbox": [121.0, 31.0, 122.0, 32.0],
                "width": 256,
                "height": 128,
                "band_count": 1,
                "crs": "EPSG:4326",
                "nodata": None,
                "band_stats": [{"min": 0, "max": 1}],
            }

        with TemporaryDirectory() as tmp:
            raster = Path(tmp) / "dem.tif"
            raster.write_bytes(b"not-a-real-tiff")
            ctx = ToolContext(meta={"workspace_path": tmp}, request_fn=request)

            result = add_raster(ctx, raster_path="dem.tif", name="DEM", opacity=0.5)

        self.assertEqual(result["name"], "DEM")
        self.assertEqual(result["bbox"], [121.0, 31.0, 122.0, 32.0])
        self.assertEqual(result["width"], 256)
        self.assertEqual(result["height"], 128)
        self.assertEqual(result["band_count"], 1)
        self.assertEqual(result["crs"], "EPSG:4326")
        self.assertEqual(len(requests), 1)
        method, payload = requests[0]
        self.assertEqual(method, "rpc.ui.map.add_raster_from_file")
        self.assertEqual(payload["path"], str(raster.resolve()))
        self.assertEqual(payload["opacity"], 0.5)

    def test_get_raster_info_reads_frontend_layer_state(self) -> None:
        async def request(method: str, params: dict):
            self.assertEqual(method, "rpc.ui.map.get_raster_info")
            self.assertEqual(params, {"layer_id": "raster-1"})
            return {"layer_id": "raster-1", "band_count": 1}

        ctx = ToolContext(request_fn=request)

        self.assertEqual(get_raster_info(ctx, layer_id="raster-1")["band_count"], 1)

    def test_set_raster_style_sends_color_ramp_contract(self) -> None:
        requests: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests.append((method, params))
            return {"layer_id": params["layer_id"], "raster_style": params["raster"]}

        ctx = ToolContext(request_fn=request)

        result = set_raster_style(
            ctx,
            layer_id="raster-1",
            ramp="terrain",
            stops='[{"value":0,"color":"#0000ff","opacity":0.2},{"value":1,"color":"#ff0000","opacity":0.9}]',
            opacity=0.7,
            band=2,
        )

        self.assertEqual(result["raster_style"]["ramp"], "terrain")
        self.assertEqual(result["raster_style"]["band"], 2)
        self.assertEqual(result["raster_style"]["opacity"], 0.7)
        self.assertEqual(result["raster_style"]["stops"][0]["opacity"], 0.2)
        self.assertEqual(requests[0][0], "rpc.ui.map.set_raster_style")

    def test_update_layer_style_uses_layer_geometry_for_point_paint(self) -> None:
        notifications: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            self.assertEqual(method, "rpc.ui.map.get_layer")
            self.assertEqual(params, {"layer_id": "poi"})
            return {"geometry_type": "Point"}

        async def notify(method: str, params: dict):
            notifications.append((method, params))

        ctx = ToolContext(request_fn=request, notify_fn=notify)

        result = update_layer_style(
            ctx,
            "poi",
            color="#ff0000",
            opacity=0.4,
            point_size=7,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(notifications[0][0], "rpc.ui.map.set_layer_style")
        self.assertEqual(
            notifications[0][1]["style"]["paint"],
            {
                "circle-color": "#ff0000",
                "circle-radius": 7.0,
                "circle-opacity": 0.4,
            },
        )

    def test_set_categorized_style_sends_explicit_categories_and_colors(self) -> None:
        requests_seen: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests_seen.append((method, params))
            return {"renderer": "categorized", **params}

        ctx = ToolContext(request_fn=request)

        result = set_categorized_style(
            ctx,
            "poi",
            "type",
            max_categories=3,
            colors={"咖啡": "#ef4444", "茶饮": "#22c55e"},
            categories=["茶饮", "咖啡"],
            other_color="#64748b",
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(requests_seen[0][0], "rpc.ui.map.set_layer_renderer")
        self.assertEqual(
            requests_seen[0][1]["categorized"],
            {
                "field": "type",
                "maxCategories": 3,
                "otherColor": "#64748b",
                "colors": {"咖啡": "#ef4444", "茶饮": "#22c55e"},
                "categories": ["茶饮", "咖啡"],
            },
        )

    def test_set_graduated_style_expands_named_palette_and_manual_breaks(self) -> None:
        requests_seen: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests_seen.append((method, params))
            return {"renderer": "graduated", **params}

        ctx = ToolContext(request_fn=request)

        result = set_graduated_style(
            ctx,
            "districts",
            "population",
            classes=4,
            palette="purples",
            breaks=[100, 200, 300],
        )

        graduated = requests_seen[0][1]["graduated"]
        self.assertTrue(result["success"], result)
        self.assertEqual(graduated["method"], "manual")
        self.assertEqual(graduated["breaks"], [100.0, 200.0, 300.0])
        self.assertEqual(len(graduated["palette"]), 4)
        self.assertTrue(all(color.startswith("#") for color in graduated["palette"]))
        self.assertEqual(graduated["palette"][0], "#f3e8ff")
        self.assertEqual(graduated["palette"][-1], "#581c87")

    def test_set_extrusion_style_sets_renderer_without_camera_by_default(self) -> None:
        requests_seen: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests_seen.append((method, params))
            if method == "rpc.ui.map.set_layer_renderer":
                return {"layer_id": params["layer_id"], "renderer": params["renderer"], "extrusion": params["extrusion"]}
            raise AssertionError(f"Unexpected request: {method}")

        ctx = ToolContext(request_fn=request)

        result = set_extrusion_style(ctx, "buildings", "height")

        self.assertTrue(result["success"], result)
        self.assertEqual([method for method, _ in requests_seen], ["rpc.ui.map.set_layer_renderer"])
        self.assertIsNone(result["camera"])

    def test_set_extrusion_style_sets_renderer_style_and_camera_when_requested(self) -> None:
        requests_seen: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests_seen.append((method, params))
            if method == "rpc.ui.map.set_layer_renderer":
                return {"layer_id": params["layer_id"], "renderer": params["renderer"], "extrusion": params["extrusion"]}
            if method == "rpc.ui.map.set_layer_style":
                return {"layer_id": params["layer_id"], "style": params["style"]}
            if method == "rpc.ui.map.set_camera":
                return {"pitch": params.get("pitch"), "bearing": params.get("bearing"), "zoom": params.get("zoom")}
            raise AssertionError(f"Unexpected request: {method}")

        ctx = ToolContext(request_fn=request)

        result = set_extrusion_style(
            ctx,
            "buildings",
            "height",
            height_multiplier=1.5,
            base_field="base_height",
            color="#f59e0b",
            opacity=0.75,
            enter_3d=True,
            zoom=16,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(requests_seen[0][0], "rpc.ui.map.set_layer_renderer")
        self.assertEqual(requests_seen[0][1], {
            "layer_id": "buildings",
            "renderer": "extrusion",
            "extrusion": {
                "heightField": "height",
                "heightMultiplier": 1.5,
                "baseField": "base_height",
            },
        })
        self.assertEqual(requests_seen[1][0], "rpc.ui.map.set_layer_style")
        self.assertEqual(requests_seen[1][1]["style"]["paint"], {
            "fill-color": "#f59e0b",
            "fill-opacity": 0.75,
        })
        self.assertEqual(requests_seen[2][0], "rpc.ui.map.set_camera")
        self.assertEqual(requests_seen[2][1]["pitch"], 60.0)
        self.assertEqual(requests_seen[2][1]["bearing"], -25.0)
        self.assertEqual(requests_seen[2][1]["zoom"], 16)

    def test_layer_filter_label_order_and_legend_tools_emit_canonical_rpc(self) -> None:
        notifications: list[tuple[str, dict]] = []
        requests_seen: list[tuple[str, dict]] = []

        async def notify(method: str, params: dict):
            notifications.append((method, params))

        async def request(method: str, params: dict):
            requests_seen.append((method, params))
            return {"ok": True, **params}

        ctx = ToolContext(request_fn=request, notify_fn=notify)

        set_layer_filter(ctx, "poi", [{"field": "rating", "op": ">=", "value": 4.5}])
        set_layer_label(ctx, "poi", field="name", font_size=12, color="#111827", halo_color="#ffffff", halo_width=2)
        order = set_layer_order(ctx, "poi", "above", "roads")
        legend = update_legend_spec(
            ctx,
            "poi",
            title="POI 类型",
            labels={"cafe": "咖啡"},
            order=["cafe"],
            visible=True,
        )

        self.assertEqual(notifications[0][0], "rpc.ui.map.set_layer_filter")
        self.assertEqual(
            notifications[0][1],
            {
                "layer_id": "poi",
                "filter": {"attribute": [{"field": "rating", "op": ">=", "value": 4.5}]},
            },
        )
        self.assertEqual(notifications[1][0], "rpc.ui.map.set_layer_label")
        self.assertEqual(notifications[1][1]["field"], "name")
        self.assertEqual(requests_seen[0], ("rpc.ui.map.set_layer_order", {
            "layer_id": "poi",
            "position": "above",
            "target_layer_id": "roads",
        }))
        self.assertEqual(requests_seen[1][0], "rpc.ui.map.update_legend_spec")
        self.assertEqual(requests_seen[1][1]["legend"]["labels"], {"cafe": "咖啡"})
        self.assertEqual(order["position"], "above")
        self.assertEqual(legend["legend"]["title"], "POI 类型")

    def test_set_layer_visual_variables_requests_semantic_rpc(self) -> None:
        requests_seen: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests_seen.append((method, params))
            return {
                "layer_id": params["layer_id"],
                "size_variable": params.get("size_variable"),
                "opacity_variable": params.get("opacity_variable"),
            }

        ctx = ToolContext(request_fn=request)

        result = set_layer_visual_variables(
            ctx,
            "poi",
            size_field="评论数",
            size_method="equal_interval",
            size_classes=4,
            size_range=[4, 18],
            opacity_field="评分",
            opacity_classes=5,
            opacity_values=[0.25, 0.4, 0.55, 0.7, 0.9],
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(requests_seen[0][0], "rpc.ui.map.update_visual_variables")
        self.assertEqual(requests_seen[0][1]["size_variable"], {
            "field": "评论数",
            "method": "equal-interval",
            "classes": 4,
            "range": [4.0, 18.0],
        })
        self.assertEqual(requests_seen[0][1]["opacity_variable"]["field"], "评分")
        self.assertEqual(requests_seen[0][1]["opacity_variable"]["values"], [0.25, 0.4, 0.55, 0.7, 0.9])

    def test_highlight_features_requests_overlay_with_style(self) -> None:
        requests_seen: list[tuple[str, dict]] = []

        async def request(method: str, params: dict):
            requests_seen.append((method, params))
            return {"highlight_layer_id": "highlight_poi", "feature_count": 2}

        ctx = ToolContext(request_fn=request)

        result = highlight_features(
            ctx,
            "poi",
            attribute_filter=json.dumps({"attribute": [{"field": "type", "op": "in", "value": ["咖啡", "茶饮"]}]}),
            name="高亮饮品",
            color="#f59e0b",
            opacity=0.8,
            point_size=9,
        )

        self.assertEqual(result["highlight_layer_id"], "highlight_poi")
        self.assertEqual(requests_seen[0][0], "rpc.ui.map.highlight_features")
        self.assertEqual(requests_seen[0][1]["filter"]["attribute"][0]["op"], "in")
        self.assertEqual(requests_seen[0][1]["style"]["paint"]["circle-color"], "#f59e0b")
        self.assertEqual(requests_seen[0][1]["style"]["paint"]["circle-radius"], 9.0)

    def test_bash_prefers_backend_python_on_path(self) -> None:
        result = _bash_sync(
            "python -c 'import sys; print(sys.executable)'",
            timeout=30_000,
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(Path(str(result["output"]).strip()).resolve(), Path(sys.executable).resolve())

    def test_execute_code_rejects_think_tag_before_execution(self) -> None:
        calls: list[str] = []

        def fake_executor(code: str):
            calls.append(code)
            return SimpleNamespace(output="ran", logs="", error=None)

        runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={},
            executor_call=fake_executor,
        )
        result = runtime.execute("execute_code", {"code": "print('a')</think>print('b')"})

        self.assertEqual(result.error, "invalid_execute_code_payload")
        self.assertEqual(calls, [])
        payload = json.loads(result.content)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "invalid_execute_code_payload")

    def test_execute_code_rejects_reasoning_comment_monologue(self) -> None:
        code = "\n".join(
            [
                "import json",
                "# 我们将直接调用osm_call工具，但execute_code中不能直接调用工具。",
                "# 因此，我们需要使用另一种方法。",
                "# 由于数据量很大，我们无法在这里复制。",
                "# 让我们改变策略。",
                "# 我们可以通过读取之前工具输出的文件来获取数据。",
                "print('should not run')",
            ]
        )

        self.assertIsNotNone(validate_execute_code_payload(code))

    def test_execute_code_rejects_long_worker_wait_sleep(self) -> None:
        code = "import time\nprint('等待60秒，让worker完成更新...')\ntime.sleep(60)\nprint('done')\n"

        message = validate_execute_code_payload(code)

        self.assertIsNotNone(message)
        self.assertIn("wait_worker_update", message or "")

    def test_execute_code_allows_short_factual_comments(self) -> None:
        calls: list[str] = []

        def fake_executor(code: str):
            calls.append(code)
            return SimpleNamespace(output="ok", logs="", error=None)

        runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={},
            executor_call=fake_executor,
        )
        result = runtime.execute(
            "execute_code",
            {"code": "import math\n# 计算半径对应面积\nprint(math.pi * 2 ** 2)\n"},
        )

        self.assertIsNone(result.error)
        self.assertEqual(result.content, "ok")
        self.assertEqual(len(calls), 1)

    def test_edit_file_uses_fuzzy_matching_and_returns_diff_stats(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text("def demo():\n    value = 1   \n    return value\n", encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})
            read_file(ctx, str(path))

            result = edit_file(
                ctx,
                str(path),
                "def demo():\n    value = 1\n    return value\n",
                "def demo():\n    value = 2\n    return value\n",
            )

            self.assertTrue(result["success"], result)
            self.assertIn(result["match_strategy"], {"trim_trailing_whitespace", "exact"})
            self.assertGreaterEqual(result["additions"], 1)
            self.assertGreaterEqual(result["deletions"], 1)
            self.assertIn("-    value = 1", result["diff"])
            self.assertIn("+    value = 2", result["diff"])

    def test_edit_file_batches_multiple_edits_into_one_atomic_diff(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text("alpha = 1\nbeta = 1\ngamma = 1\n", encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})
            read_file(ctx, str(path))

            result = edit_file(
                ctx,
                str(path),
                edits=[
                    {"old_string": "alpha = 1", "new_string": "alpha = 2"},
                    {"old_string": "gamma = 1", "new_string": "gamma = 3"},
                ],
            )

            self.assertTrue(result["success"], result)
            self.assertEqual(result["edit_count"], 2)
            self.assertEqual(result["replacements"], 2)
            self.assertIn("-alpha = 1", result["diff"])
            self.assertIn("+alpha = 2", result["diff"])
            self.assertIn("-gamma = 1", result["diff"])
            self.assertIn("+gamma = 3", result["diff"])
            self.assertEqual(path.read_text(encoding="utf-8"), "alpha = 2\nbeta = 1\ngamma = 3\n")

    def test_edit_file_batch_failure_does_not_write_partial_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            original = "alpha = 1\nbeta = 1\n"
            path.write_text(original, encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})
            read_file(ctx, str(path))

            result = edit_file(
                ctx,
                str(path),
                edits=[
                    {"old_string": "alpha = 1", "new_string": "alpha = 2"},
                    {"old_string": "missing = 1", "new_string": "missing = 2"},
                ],
            )

            self.assertFalse(result["success"], result)
            self.assertEqual(result["edit_index"], 2)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_write_file_requires_prior_read_for_existing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text("print('old')\n", encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})

            blocked = write_file(ctx, str(path), "print('new')\n")
            self.assertFalse(blocked["success"])
            self.assertTrue(blocked["requires_read"])

            read_file(ctx, str(path))
            written = write_file(ctx, str(path), "print('new')\n")
            self.assertTrue(written["success"], written)
            self.assertEqual(written["diagnostic_error_count"], 0)

    def test_write_file_reports_python_syntax_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.py"
            ctx = ToolContext(meta={"workspace_path": tmp})

            result = write_file(ctx, str(path), "def bad(:\n", overwrite=True)

            self.assertTrue(result["success"], result)
            self.assertGreaterEqual(result["diagnostic_error_count"], 1)
            self.assertEqual(result["diagnostics"][-1]["source"], "python")

    def test_read_file_returns_image_attachment_and_missing_suggestions(self) -> None:
        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "chart.png"
            image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
            ctx = ToolContext(meta={"workspace_path": tmp})

            result = read_file(ctx, str(image))
            self.assertEqual(result["type"], "image")
            self.assertEqual(result["encoding"], "base64")
            self.assertEqual(result["mime"], "image/png")

            missing = read_file(ctx, str(Path(tmp) / "char.png"))
            self.assertEqual(missing["error"], "file_not_found")
            self.assertIn(str(image.resolve()), missing["suggestions"])

    def test_bash_returns_parse_warnings_and_metadata(self) -> None:
        result = _bash_sync("cat /etc/hosts", workdir="/tmp", description="read hosts")
        self.assertIn("parsed", result)
        self.assertIn("cat", result["parsed"]["commands"])
        self.assertTrue(result["parsed"]["external_paths"])
        self.assertTrue(result["warnings"])

    def test_webfetch_converts_html_to_markdown(self) -> None:
        html = b"<html><body><h1>Title</h1><p>Hello <a href=\"https://example.com/a\">link</a></p></body></html>"

        class FakeResponse:
            headers = {"content-type": "text/html; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return html

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = webfetch("https://example.com", format="markdown")

        self.assertTrue(result["success"], json.dumps(result, ensure_ascii=False))
        self.assertIn("# Title", result["output"])
        self.assertIn("Hello link (https://example.com/a)", result["output"])

    def test_osm_call_saves_download_to_workspace_path(self) -> None:
        overpass_data = {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "tags": {"highway": "residential", "name": "A"},
                    "geometry": [
                        {"lon": 121.0, "lat": 31.0},
                        {"lon": 121.1, "lat": 31.1},
                    ],
                }
            ]
        }
        with TemporaryDirectory() as tmp, patch(
            "opengis_backend.integrations.osm.tools.overpass_query",
            return_value=overpass_data,
        ):
            ctx = ToolContext(meta={"workspace_path": tmp})
            result = osm_call(
                ctx,
                "download_bbox",
                json.dumps(
                    {
                        "south": 31.0,
                        "west": 121.0,
                        "north": 31.2,
                        "east": 121.2,
                        "key": "highway",
                        "value": "*",
                        "output_path": "osm/roads.geojson",
                    }
                ),
            )

            self.assertTrue(result["success"], result)
            self.assertEqual(result["feature_count"], 1)
            self.assertEqual(result["geometry_types"], ["LineString"])
            self.assertTrue(Path(result["path"]).exists())
            self.assertEqual(Path(result["path"]).resolve(), Path(tmp, "osm", "roads.geojson").resolve())
            saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["features"][0]["properties"]["name"], "A")
            self.assertNotIn("geojson", result)

    def test_osm_call_keeps_small_download_inline_without_output_path(self) -> None:
        overpass_data = {
            "elements": [
                {"type": "node", "id": 1, "lat": 31.0, "lon": 121.0, "tags": {"amenity": "cafe"}}
            ]
        }
        with TemporaryDirectory() as tmp, patch(
            "opengis_backend.integrations.osm.tools.overpass_query",
            return_value=overpass_data,
        ):
            ctx = ToolContext(meta={"workspace_path": tmp})
            result = osm_call(
                ctx,
                "download_bbox",
                json.dumps(
                    {
                        "south": 31.0,
                        "west": 121.0,
                        "north": 31.2,
                        "east": 121.2,
                        "key": "amenity",
                        "geometry_type": "node",
                    }
                ),
            )

            self.assertEqual(result["type"], "FeatureCollection")
            self.assertEqual(result["features"][0]["geometry"]["type"], "Point")
            self.assertFalse((Path(tmp) / "osm").exists())

    def test_osm_overpass_query_normalizes_existing_header(self) -> None:
        query = "[out:xml][timeout:10];\nnode[\"amenity\"=\"cafe\"](1,2,3,4);out geom;"
        normalized = _normalize_overpass_query(query)

        self.assertIn("[out:json]", normalized)
        self.assertIn("[timeout:10]", normalized)
        self.assertNotIn("[out:xml]", normalized)

    def test_osm_search_timeout_returns_structured_retryable_error(self) -> None:
        with TemporaryDirectory() as tmp, patch(
            "opengis_backend.integrations.osm.overpass.requests.get",
            side_effect=requests.exceptions.ReadTimeout("nominatim timeout"),
        ) as get_mock, patch("opengis_backend.integrations.osm.overpass.time.sleep"):
            ctx = ToolContext(meta={"workspace_path": tmp})
            result = osm_call(
                ctx,
                "search",
                json.dumps({"query": "华东师范大学 闵行校区", "timeout": 5, "retries": 1}),
            )

            self.assertFalse(result["success"], result)
            self.assertEqual(result["error"], "osm_network_timeout")
            self.assertTrue(result["retryable"])
            self.assertIn("download_bbox", result["suggestion"])
            self.assertEqual(get_mock.call_count, 2)

    def test_datasource_fetch_saves_to_workspace_path(self) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [121.0, 31.0]},
                    "properties": {"name": "demo"},
                }
            ],
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        with TemporaryDirectory() as tmp, patch(
            "opengis_backend.integrations.datasource.tools._find_source",
            return_value={"name": "测试源", "description": "demo", "url": "https://example.com/demo.geojson"},
        ), patch("urllib.request.urlopen", return_value=FakeResponse()):
            ctx = ToolContext(meta={"workspace_path": tmp})
            result = datasource_call(
                ctx,
                "fetch",
                json.dumps({"name": "测试源", "output_path": "data/demo.geojson"}),
            )

            self.assertTrue(result["success"], result)
            self.assertEqual(result["feature_count"], 1)
            self.assertEqual(result["geometry_types"], ["Point"])
            self.assertEqual(Path(result["path"]).resolve(), Path(tmp, "data", "demo.geojson").resolve())
            self.assertTrue(Path(result["path"]).exists())
            self.assertNotIn("geojson", result)

    def test_csv_to_geojson_resolves_paths_against_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            csv_path = workspace / "points.csv"
            csv_path.write_text("name,lat,lon\nA,31.1,121.2\nB,bad,121.3\n", encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})

            result = csv_to_geojson(ctx, "points.csv", output_path="out/points.geojson")

            self.assertTrue(result["success"], result)
            self.assertEqual(result["feature_count"], 1)
            self.assertEqual(result["skipped_rows"], 1)
            self.assertEqual(Path(result["path"]).resolve(), (workspace / "out" / "points.geojson").resolve())
            saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["features"][0]["properties"]["name"], "A")

    def test_csv_to_geojson_rejects_output_outside_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "points.csv").write_text("lat,lon\n31.1,121.2\n", encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})

            with self.assertRaises(ValueError):
                csv_to_geojson(ctx, "points.csv", output_path="/tmp/outside.geojson")

    def test_file_listing_tools_resolve_relative_paths_against_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "src").mkdir()
            target = workspace / "src" / "demo.py"
            target.write_text("needle = 1\n", encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})

            exists = file_exists(ctx, "src/demo.py")
            listed = list_directory(ctx, "src")

            self.assertTrue(exists["exists"], exists)
            self.assertEqual(Path(exists["path"]).resolve(), target.resolve())
            self.assertEqual(Path(listed["path"]).resolve(), (workspace / "src").resolve())
            self.assertEqual(listed["entries"][0]["name"], "demo.py")

    def test_glob_and_grep_default_to_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "src").mkdir()
            target = workspace / "src" / "demo.py"
            target.write_text("needle = 1\n", encoding="utf-8")
            ctx = ToolContext(meta={"workspace_path": tmp})

            globbed = glob(ctx, "*.py", path="src")
            grepped = grep(ctx, "needle", path="src", include="*.py")

            self.assertEqual(Path(globbed["search_path"]).resolve(), (workspace / "src").resolve())
            self.assertIn(str(target), globbed["output"])
            self.assertEqual(Path(grepped["search_path"]).resolve(), (workspace / "src").resolve())
            self.assertIn("needle", grepped["output"])

    def test_worker_start_reports_fast_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            result = manager.start_worker(
                workspace_path=tmp,
                name="bad worker",
                code="raise RuntimeError('boom')\n",
                initial_health_timeout=0.6,
            )

            self.assertEqual(result["status"], "failed", json.dumps(result, ensure_ascii=False))
            self.assertEqual(result["health"]["state"], "failed")
            self.assertIn("process exited", result["last_error"])

    def test_worker_start_marks_silent_process_uncertain(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            result = manager.start_worker(
                workspace_path=tmp,
                name="silent worker",
                code="import time\ntime.sleep(10)\n",
                initial_health_timeout=0.25,
            )
            try:
                self.assertEqual(result["status"], "running", json.dumps(result, ensure_ascii=False))
                self.assertEqual(result["health"]["state"], "uncertain")
                self.assertEqual(result["startup_check"]["state"], "uncertain")
            finally:
                manager.pause_all(reason="test_cleanup")

    def test_worker_wait_update_observes_new_output(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="waitable worker",
                code=(
                    "import time\n"
                    "print('ready', flush=True)\n"
                    "time.sleep(0.25)\n"
                    "print('tick', flush=True)\n"
                    "time.sleep(1)\n"
                ),
                initial_health_timeout=0.15,
            )
            try:
                waited = manager.wait_worker_update(
                    started["id"],
                    workspace_path=tmp,
                    timeout=2.0,
                    include_logs=True,
                )
                self.assertFalse(waited["wait"]["timed_out"], json.dumps(waited, ensure_ascii=False))
                self.assertTrue(waited["wait"]["changed"], json.dumps(waited, ensure_ascii=False))
                self.assertIn("tick", "\n".join(item["text"] for item in waited["logs"]))
            finally:
                manager.pause_all(reason="test_cleanup")

    def test_worker_restart_reuses_folder_and_reports_health(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            failed = manager.start_worker(
                workspace_path=tmp,
                name="restartable worker",
                code="raise RuntimeError('first version fails')\n",
                initial_health_timeout=0.5,
            )
            restarted = manager.restart_worker(
                failed["id"],
                code="import time\nprint('ready', flush=True)\ntime.sleep(10)\n",
                reason="test_restart",
                initial_health_timeout=0.35,
            )
            try:
                self.assertEqual(restarted["id"], failed["id"])
                self.assertEqual(restarted["folder"], failed["folder"])
                self.assertEqual(restarted["status"], "running", json.dumps(restarted, ensure_ascii=False))
                self.assertEqual(restarted["health"]["state"], "ok")
                self.assertIn("ready", "\n".join(item["text"] for item in restarted["logs"]))
                self.assertIn("ready", Path(restarted["script_path"]).read_text(encoding="utf-8"))
            finally:
                manager.pause_all(reason="test_cleanup")

    def test_worker_entrypoint_is_main_py(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="entrypoint worker",
                code="import time\nprint('ready', flush=True)\ntime.sleep(10)\n",
                initial_health_timeout=0.35,
            )
            try:
                self.assertEqual(Path(started["script_path"]).name, "main.py")
                self.assertTrue((Path(started["folder"]) / "main.py").exists())
                self.assertFalse((Path(started["folder"]) / "worker.py").exists())
            finally:
                manager.pause_all(reason="test_cleanup")

    def test_worker_start_creates_service_package(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="service worker",
                description="structured worker",
                code="import time\nprint('ready', flush=True)\ntime.sleep(10)\n",
                initial_health_timeout=0.35,
            )
            try:
                folder = Path(started["folder"])
                self.assertTrue((folder / "manifest.json").exists())
                self.assertTrue((folder / "README.md").exists())
                self.assertTrue((folder / "config.json").exists())
                self.assertTrue((folder / "src" / "datasource.py").exists())
                self.assertTrue((folder / "src" / "service.py").exists())
                self.assertTrue((folder / "src" / "publisher.py").exists())
                self.assertEqual(started["manifest"]["entrypoint"], "main.py")
                self.assertEqual(started["package"]["entrypoint"], "main.py")
                self.assertIn("src/service.py", started["package"]["src_files"])
            finally:
                manager.pause_all(reason="test_cleanup")

    def test_worker_start_accepts_structured_package_files(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="structured worker",
                code=(
                    "import time\n"
                    "from src.service import message\n"
                    "print(message(), flush=True)\n"
                    "time.sleep(10)\n"
                ),
                files={
                    "src/service.py": "def message():\n    return 'structured-ready'\n",
                    "tests/test_service.py": "from src.service import message\n\ndef test_message():\n    assert message()\n",
                },
                manifest={"kind": "dynamic-map-worker", "layers": [{"id": "live_points", "type": "point"}]},
                initial_health_timeout=0.35,
            )
            try:
                folder = Path(started["folder"])
                self.assertIn("structured-ready", "\n".join(item["text"] for item in started["logs"]))
                self.assertEqual(started["manifest"]["kind"], "dynamic-map-worker")
                self.assertEqual(started["manifest"]["layers"][0]["id"], "live_points")
                self.assertTrue((folder / "tests" / "test_service.py").exists())
            finally:
                manager.pause_all(reason="test_cleanup")

    def test_worker_package_files_cannot_override_platform_files(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            with self.assertRaises(ValueError):
                manager.start_worker(
                    workspace_path=tmp,
                    name="bad package",
                    code="print('ready')\n",
                    files={"opengis_worker.py": "# nope\n"},
                    initial_health_timeout=0.1,
                )

    def test_worker_restart_can_update_package_module(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="module worker",
                code=(
                    "import time\n"
                    "from src.service import message\n"
                    "print(message(), flush=True)\n"
                    "time.sleep(10)\n"
                ),
                files={"src/service.py": "def message():\n    return 'v1'\n"},
                initial_health_timeout=0.35,
            )
            restarted = manager.restart_worker(
                started["id"],
                files={"src/service.py": "def message():\n    return 'v2'\n"},
                reason="test_module_update",
                initial_health_timeout=0.35,
            )
            try:
                self.assertEqual(restarted["folder"], started["folder"])
                self.assertIn("v2", "\n".join(item["text"] for item in restarted["logs"]))
                self.assertIn("return 'v2'", (Path(restarted["folder"]) / "src" / "service.py").read_text(encoding="utf-8"))
            finally:
                manager.pause_all(reason="test_cleanup")

    def test_running_worker_is_removed_when_folder_disappears(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="externally deleted worker",
                code="import time\nprint('ready', flush=True)\ntime.sleep(10)\n",
                initial_health_timeout=0.35,
            )
            shutil.rmtree(started["folder"])
            listed = manager.list_workers(workspace_path=tmp, include_logs=False)
            self.assertEqual(listed, [])

    def test_delete_worker_removes_on_disk_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="deletable worker",
                code="import time\nprint('ready', flush=True)\ntime.sleep(10)\n",
                initial_health_timeout=0.35,
            )
            folder = Path(started["folder"])
            deleted = manager.delete_worker(started["id"], workspace_path=tmp)
            self.assertEqual(deleted["status"], "deleted")
            self.assertTrue(deleted["folder_deleted"], json.dumps(deleted, ensure_ascii=False))
            self.assertFalse(folder.exists())
            self.assertEqual(manager.list_workers(workspace_path=tmp, include_logs=False), [])

    def test_deleted_worker_folder_residue_is_cleaned_on_delete(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            folder = workspace / "worker" / "residue-worker_123"
            folder.mkdir(parents=True)
            (folder / "main.py").write_text("print('stale')\n", encoding="utf-8")
            (folder / "metadata.json").write_text(
                json.dumps({
                    "id": "worker_123",
                    "name": "residue",
                    "workspace_path": str(workspace),
                    "folder": str(folder),
                    "script_path": str(folder / "main.py"),
                    "status": "deleted",
                }),
                encoding="utf-8",
            )
            manager = ResidentWorkerManager()
            deleted = manager.delete_worker("worker_123", workspace_path=tmp)
            self.assertEqual(deleted["status"], "deleted")
            self.assertTrue(deleted["folder_deleted"], json.dumps(deleted, ensure_ascii=False))
            self.assertFalse(folder.exists())

    def test_worker_metadata_restores_after_manager_restart(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            started = manager.start_worker(
                workspace_path=tmp,
                name="persistent worker",
                code="import time\nprint('ready', flush=True)\ntime.sleep(10)\n",
                initial_health_timeout=0.35,
            )
            manager.pause_all(reason="test_shutdown")

            restored_manager = ResidentWorkerManager()
            listed = restored_manager.list_workers(workspace_path=tmp, include_logs=True)
            self.assertEqual(len(listed), 1, json.dumps(listed, ensure_ascii=False))
            self.assertEqual(listed[0]["id"], started["id"])
            self.assertEqual(listed[0]["status"], "paused")
            self.assertEqual(listed[0]["folder"], started["folder"])

            restarted = restored_manager.restart_worker(
                started["id"],
                workspace_path=tmp,
                reason="test_restore_restart",
                initial_health_timeout=0.35,
            )
            try:
                self.assertEqual(restarted["id"], started["id"])
                self.assertEqual(restarted["folder"], started["folder"])
                self.assertEqual(restarted["status"], "running", json.dumps(restarted, ensure_ascii=False))
                self.assertEqual(restarted["health"]["state"], "ok")
            finally:
                restored_manager.pause_all(reason="test_cleanup")

    def test_worker_helper_emits_moving_objects_points_and_tracks(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            events: list[tuple[str, dict]] = []
            unsubscribe = manager.subscribe_events(lambda method, params: events.append((method, params)))
            try:
                result = manager.start_worker(
                    workspace_path=tmp,
                    name="moving objects",
                    code=(
                        "from opengis_worker import emit_moving_objects\n"
                        "import time\n"
                        "emit_moving_objects(\n"
                        "    point_layer_id='live_points',\n"
                        "    track_layer_id='live_tracks',\n"
                        "    points=[{'id': 'v1', 'lon': 121.5, 'lat': 31.2, 'properties': {'speed': 12}}],\n"
                        "    tracks={'v1': [[121.49, 31.19], [121.5, 31.2]]},\n"
                        "    sequence=1,\n"
                        "    full=True,\n"
                        ")\n"
                        "time.sleep(1)\n"
                    ),
                    initial_health_timeout=0.6,
                )
                self.assertIn(result["status"], {"running", "stopped"}, json.dumps(result, ensure_ascii=False))
                layer_ids = [params.get("layer_id") for method, params in events if method == "rpc.ui.map.dynamic_layer_update"]
                self.assertIn("live_points", layer_ids)
                self.assertIn("live_tracks", layer_ids)
            finally:
                unsubscribe()
                manager.pause_all(reason="test_cleanup")

    def test_worker_helper_auto_full_then_diff_for_dynamic_points(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = ResidentWorkerManager()
            events: list[tuple[str, dict]] = []
            unsubscribe = manager.subscribe_events(lambda method, params: events.append((method, params)))
            try:
                result = manager.start_worker(
                    workspace_path=tmp,
                    name="auto full points",
                    code=(
                        "from opengis_worker import emit_dynamic_points\n"
                        "import time\n"
                        "emit_dynamic_points(\n"
                        "    layer_id='live_points',\n"
                        "    name='Live Points',\n"
                        "    points=[{'id': 'v1', 'lon': 121.5, 'lat': 31.2}],\n"
                        "    sequence=1,\n"
                        ")\n"
                        "emit_dynamic_points(\n"
                        "    layer_id='live_points',\n"
                        "    name='Live Points',\n"
                        "    points=[{'id': 'v1', 'lon': 121.51, 'lat': 31.21}],\n"
                        "    sequence=2,\n"
                        ")\n"
                        "time.sleep(1)\n"
                    ),
                    initial_health_timeout=0.6,
                )
                self.assertIn(result["status"], {"running", "stopped"}, json.dumps(result, ensure_ascii=False))
                updates = [
                    params
                    for method, params in events
                    if method == "rpc.ui.map.dynamic_layer_update" and params.get("layer_id") == "live_points"
                ]
                self.assertGreaterEqual(len(updates), 2, json.dumps(updates, ensure_ascii=False))
                self.assertEqual(updates[0]["mode"], "full")
                self.assertEqual(updates[1]["mode"], "diff")
                self.assertIn("geojson", updates[0])
                self.assertIn("diff", updates[1])
                self.assertEqual(updates[1]["diff"]["update"][0]["type"], "Feature")
            finally:
                unsubscribe()
                manager.pause_all(reason="test_cleanup")

    def test_execute_code_blocks_resident_dynamic_map_loop(self) -> None:
        with TemporaryDirectory() as tmp:
            called = {"executor": False}

            class Result:
                logs = ""
                output = None
                error = None

            def fake_executor(_code: str):
                called["executor"] = True
                return Result()

            runtime = ToolRuntime(
                tool_schemas=build_tool_schemas([]),
                tool_callables={},
                executor_call=fake_executor,
                workspace_path=tmp,
            )
            result = runtime.execute(
                "execute_code",
                {
                    "code": (
                        "import time\n"
                        "from opengis_worker import emit_moving_objects\n"
                        "while True:\n"
                        "    emit_moving_objects(point_layer_id='p', track_layer_id='t', points=[], tracks={}, sequence=1)\n"
                        "    time.sleep(1)\n"
                    )
                },
            )

            self.assertFalse(called["executor"])
            self.assertIn("resident_worker_required", result.content)
            self.assertIn("start_dynamic_map_worker", result.content)

    def test_script_tools_list_read_and_mark_for_edit(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script_dir = workspace / "script"
            script_dir.mkdir()
            script_path = script_dir / "demo.py"
            script_path.write_text("print('old')\n", encoding="utf-8")
            script_path.with_suffix(".metadata.json").write_text(
                json.dumps({"semantic_name": "demo", "description": "test script"}, ensure_ascii=False),
                encoding="utf-8",
            )
            ctx = ToolContext(meta={"workspace_path": tmp})

            listed = list_scripts(ctx, query="demo")
            self.assertTrue(listed["success"], listed)
            self.assertEqual(len(listed["scripts"]), 1)
            self.assertEqual(listed["scripts"][0]["path"], "script/demo.py")

            read = read_script(ctx, "script/demo.py")
            self.assertTrue(read["success"], read)
            self.assertIn("print('old')", read["content"])

            edited = edit_file(ctx, str(script_path), "print('old')", "print('new')")
            self.assertTrue(edited["success"], edited)
            self.assertIn("print('new')", script_path.read_text(encoding="utf-8"))

    def test_run_script_file_executes_existing_script_asset(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script_dir = workspace / "script"
            script_dir.mkdir()
            script_path = script_dir / "demo.py"
            script_path.write_text("value = 41 + 1\nprint(value)\n", encoding="utf-8")
            captured: dict[str, str] = {}

            class Result:
                logs = "42"
                output = None
                error = None

            def fake_executor(code: str):
                captured["code"] = code
                return Result()

            runtime = ToolRuntime(
                tool_schemas=build_tool_schemas([]),
                tool_callables={},
                executor_call=fake_executor,
                workspace_path=tmp,
            )

            result = runtime.execute("run_script_file", {"script_path": "script/demo.py"})
            self.assertIsNone(result.error, result.content)
            self.assertIn("value = 41 + 1", captured["code"])
            self.assertIn("42", result.content)
            self.assertEqual(result.metadata["script_path"], "script/demo.py")
            self.assertTrue(result.metadata["rerun_script"])

    def test_session_store_recovers_stale_running_sessions(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sessions_path = workspace / ".opengis" / "sessions.json"
            sessions_path.parent.mkdir(parents=True)
            stale_ts = time.time() - 2 * 24 * 60 * 60
            sessions_path.write_text(
                json.dumps(
                    {
                        "sessions": {
                            "parent": {
                                "id": "parent",
                                "kind": "chat",
                                "profile_name": "gis-build",
                                "status": "error",
                                "created_at": stale_ts,
                                "updated_at": stale_ts,
                                "children": ["child"],
                                "metadata": {},
                            },
                            "child": {
                                "id": "child",
                                "kind": "subagent",
                                "profile_name": "gis-subagent",
                                "parent_id": "parent",
                                "status": "running",
                                "created_at": stale_ts,
                                "updated_at": stale_ts,
                                "children": [],
                                "metadata": {},
                            },
                        },
                        "inbox": {
                            "inbox1": {
                                "id": "inbox1",
                                "prompt": "old",
                                "status": "running",
                                "created_at": stale_ts,
                                "updated_at": stale_ts,
                                "metadata": {},
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            store = SessionStore(tmp)
            sessions = {item["id"]: item for item in store.list_recent(limit=10)}
            inbox = {item["id"]: item for item in store.list_inbox(limit=10)}

            self.assertEqual(sessions["child"]["status"], "error")
            self.assertTrue(sessions["child"]["metadata"]["recovered_from_running"])
            self.assertEqual(inbox["inbox1"]["status"], "error")
            self.assertTrue(inbox["inbox1"]["metadata"]["recovered_from_running"])

    def test_run_archive_list_recovers_stale_running_meta(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            run_dir = workspace / ".opengis" / "runs" / "run_stale"
            run_dir.mkdir(parents=True)
            meta_path = run_dir / "meta.json"
            meta_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_stale",
                        "status": "running",
                        "prompt": "old",
                        "created_at": "2020-01-01T00:00:00",
                        "finished_at": None,
                        "step_count": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            runs = RunArchive.list_runs(tmp, limit=10)
            self.assertEqual(runs[0].status, "error")
            updated = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["status"], "error")
            self.assertTrue(updated["recovered_from_running"])


if __name__ == "__main__":
    unittest.main()
