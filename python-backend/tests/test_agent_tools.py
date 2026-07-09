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
from opengis_backend.tools.builtin.display import set_basemap as set_basemap_tool
from opengis_backend.tools.builtin.csv_to_geojson import csv_to_geojson as csv_to_geojson_tool
from opengis_backend.tools.builtin.edit_file_tool import edit_file as edit_file_tool
from opengis_backend.tools.builtin.file_ops import file_exists as file_exists_tool
from opengis_backend.tools.builtin.file_ops import list_directory as list_directory_tool
from opengis_backend.tools.builtin.glob_tool import glob as glob_tool
from opengis_backend.tools.builtin.grep_tool import grep as grep_tool
from opengis_backend.tools.builtin.read_file_tool import read_file as read_file_tool
from opengis_backend.tools.builtin.web_tools import webfetch as webfetch_tool
from opengis_backend.tools.builtin.write_file_tool import write_file as write_file_tool
from opengis_backend.tools.builtin.script_tools import list_scripts as list_scripts_tool
from opengis_backend.tools.builtin.script_tools import read_script as read_script_tool
from opengis_backend.agent.factory_common import compose_system_prompt
from opengis_backend.agent.tools import filter_agent_tools
from opengis_backend.agent.execution.tool_runtime import ToolRuntime, build_tool_schemas, validate_execute_code_payload
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
osm_call = osm_call_tool.__wrapped__
datasource_call = datasource_call_tool.__wrapped__
csv_to_geojson = csv_to_geojson_tool.__wrapped__
file_exists = file_exists_tool.__wrapped__
list_directory = list_directory_tool.__wrapped__
glob = glob_tool.__wrapped__
grep = grep_tool.__wrapped__


class AgentToolUpgradeTests(unittest.TestCase):
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

    def test_set_basemap_tool_rejects_agent_initiated_switches(self) -> None:
        ctx = ToolContext(meta={})
        result = set_basemap(ctx, "satellite")

        self.assertFalse(result["success"])
        self.assertIn("disabled", result["error"])

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
