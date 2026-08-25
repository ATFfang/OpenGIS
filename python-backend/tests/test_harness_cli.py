from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from opengis_backend.cli.bench_run import load_tasks, run_command
from opengis_backend.harness.headless import HeadlessAgentRunner, HeadlessRunConfig
from opengis_backend.harness.metrics import summarize_metrics


class HarnessCliTests(unittest.IsolatedAsyncioTestCase):
    def test_load_tasks_accepts_json_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "case one",
                            "prompt": "Load data",
                            "tags": ["gis"],
                            "attachments": [{"name": "a.csv", "path": "/tmp/a.csv"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            tasks = load_tasks(path)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, "case-one")
        self.assertEqual(tasks[0].prompt, "Load data")
        self.assertEqual(tasks[0].tags, ["gis"])
        self.assertEqual(tasks[0].attachments[0]["name"], "a.csv")

    def test_load_tasks_accepts_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.jsonl"
            path.write_text(
                json.dumps({"case_id": "a", "question": "Zoom to layer"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )

            tasks = load_tasks(path)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, "a")
        self.assertEqual(tasks[0].prompt, "Zoom to layer")

    def test_summarize_metrics_aggregates_token_and_cache_counts(self) -> None:
        summary = summarize_metrics(
            [
                {
                    "status": "success",
                    "duration_ms": 100,
                    "prompt_tokens": 1000,
                    "cached_tokens": 250,
                    "total_tokens": 1200,
                    "tool_calls": 2,
                    "failed_tool_calls": 1,
                    "artifacts": 1,
                },
                {
                    "status": "error",
                    "duration_ms": 300,
                    "prompt_tokens": 500,
                    "cached_tokens": 250,
                    "total_tokens": 700,
                    "tool_calls": 1,
                },
            ]
        )

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["avg_duration_ms"], 200)
        self.assertEqual(summary["total_tokens"], 1900)
        self.assertAlmostEqual(summary["cache_hit_rate"], 500 / 1500)

    async def test_bench_dry_run_writes_jsonl_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_set = root / "tasks.json"
            task_set.write_text(
                json.dumps([{"id": "smoke", "prompt": "Say hi"}]),
                encoding="utf-8",
            )
            out_dir = root / "bench"

            args = Namespace(
                workspace=str(root / "workspace"),
                task_set=str(task_set),
                output_dir=str(out_dir),
                limit=None,
                conversation_prefix="bench",
                profile=None,
                tool_group=None,
                feature_flag=[],
                user_instructions=None,
                approval_policy="deny",
                continue_on_error=False,
                dry_run=True,
                protocol=None,
                model=None,
                api_key=None,
                base_url=None,
            )

            code = await run_command(args)

            self.assertEqual(code, 0)
            self.assertTrue((out_dir / "runs.jsonl").exists())
            self.assertTrue((out_dir / "summary.json").exists())
            run_record = json.loads((out_dir / "runs.jsonl").read_text(encoding="utf-8").strip())
            self.assertEqual(run_record["case_id"], "smoke")
            self.assertTrue(run_record["dry_run"])

    async def test_headless_runner_can_finalize_a_run_archive(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            runner = HeadlessAgentRunner()

            async def fake_run(user_message, context=None, workflow=None, active_tool_groups=None, user_instructions=None):
                from opengis_backend.runs import RunArchive
                from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType

                run_id = "fake-run"
                archive = RunArchive.open(
                    run_id=run_id,
                    prompt=user_message,
                    workspace_path=str(workspace),
                    model="fake-model",
                )
                if context is not None:
                    context.meta["run_id"] = run_id
                archive.record_llm_usage(
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cached_tokens": 3},
                    prompt_cache={"cached_tokens": 3},
                )
                yield AgentEvent(type=AgentEventType.STREAM_DELTA, data="done")
                archive.close(status="success", final_answer="done")

            async def noop() -> None:
                return None

            async def noop_request(method, params=None):
                return {}

            runner.ensure_tools_loaded = noop  # type: ignore[assignment]
            runner.tool_registry.discover_and_load = lambda: None  # type: ignore[assignment]
            runner._emit_event = lambda event: None  # type: ignore[method-assign]
            runner._notify = noop  # type: ignore[method-assign]
            runner._request = noop_request  # type: ignore[method-assign]
            # Patch at module level to avoid importing the real LLM stack.
            import opengis_backend.harness.headless as headless_module
            original_agent_cls = headless_module.OpenGISAgent
            headless_module.OpenGISAgent = lambda *args, **kwargs: SimpleNamespace(run=fake_run)  # type: ignore[assignment]
            try:
                result = await runner.run(
                    HeadlessRunConfig(
                        workspace_path=str(workspace),
                        prompt="hello",
                        approval_policy="deny",
                    )
                )
            finally:
                headless_module.OpenGISAgent = original_agent_cls  # type: ignore[assignment]

            self.assertEqual(result.status, "success")
            self.assertIsNotNone(result.run_id)
            self.assertTrue((workspace / ".opengis" / "runs" / "fake-run" / "meta.json").exists())
            self.assertIsNotNone(result.metrics)
            self.assertEqual(result.metrics.total_tokens, 15)


if __name__ == "__main__":
    unittest.main()
