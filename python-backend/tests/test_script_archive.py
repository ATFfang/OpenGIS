import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from opengis_backend.agent.telemetry.script_archive import ScriptArchive


class ScriptArchiveTests(unittest.TestCase):
    def test_write_step_uses_semantic_filename_and_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = ScriptArchive.for_run(workspace_path=tmp, run_id="abc123")

            path = archive.write_step(
                step=3,
                code="print('ok')",
                user_message="分类一下颜色",
                observations="done",
                semantic_name="颜色分类脚本",
                metadata={"description": "classify layer colors", "loop_kind": "chat"},
            )

            self.assertEqual(path.parent, Path(tmp).resolve() / "script")
            self.assertRegex(path.name, r"^\d{8}-\d{6}-step03-颜色分类脚本\.py$")

            text = path.read_text(encoding="utf-8")
            self.assertIn("run abc123", text)
            self.assertIn("step 3", text)
            self.assertIn("Semantic : 颜色分类脚本", text)
            self.assertIn("Metadata :", text)
            self.assertIn("print('ok')", text)

            metadata_path = path.with_suffix(".metadata.json")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["run_id"], "abc123")
            self.assertEqual(metadata["step"], 3)
            self.assertEqual(metadata["semantic_name"], "颜色分类脚本")
            self.assertEqual(metadata["description"], "classify layer colors")
            self.assertEqual(metadata["loop_kind"], "chat")

            index_path = Path(tmp).resolve() / "script" / "_scripts_index.jsonl"
            index_lines = index_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(index_lines), 1)
            self.assertEqual(json.loads(index_lines[0])["script_path"], str(path))

    def test_workflow_run_uses_dedicated_script_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = ScriptArchive.for_run(
                workspace_path=tmp,
                run_id="wf987",
                workflow_name="上海 饮品店/空间分析",
            )

            self.assertEqual(
                archive.script_dir,
                Path(tmp).resolve() / "script" / "workflows" / "上海-饮品店-空间分析-wf987",
            )

            path = archive.write_step(
                step=1,
                code="print('workflow')",
                user_message="运行工作流",
                metadata={"loop_kind": "workflow", "workflow": {"name": "上海 饮品店/空间分析"}},
            )

            self.assertEqual(path.parent, archive.script_dir)
            metadata = json.loads(path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["loop_kind"], "workflow")
            self.assertEqual(metadata["workflow"]["name"], "上海 饮品店/空间分析")


if __name__ == "__main__":
    unittest.main()
