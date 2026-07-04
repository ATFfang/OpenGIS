import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opengis_backend.skills.context import SkillContext
from opengis_backend.skills.builtin.bash_skill import _bash_sync
from opengis_backend.skills.builtin.edit_file_skill import edit_file as edit_file_skill
from opengis_backend.skills.builtin.read_file_skill import read_file as read_file_skill
from opengis_backend.skills.builtin.web_tools import webfetch as webfetch_skill
from opengis_backend.skills.builtin.write_file_skill import write_file as write_file_skill

read_file = read_file_skill.__wrapped__
edit_file = edit_file_skill.__wrapped__
write_file = write_file_skill.__wrapped__
webfetch = webfetch_skill.__wrapped__


class AgentToolUpgradeTests(unittest.TestCase):
    def test_edit_file_uses_fuzzy_matching_and_returns_diff_stats(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text("def demo():\n    value = 1   \n    return value\n", encoding="utf-8")
            ctx = SkillContext(meta={"workspace_path": tmp})
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

    def test_write_file_requires_prior_read_for_existing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text("print('old')\n", encoding="utf-8")
            ctx = SkillContext(meta={"workspace_path": tmp})

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
            ctx = SkillContext(meta={"workspace_path": tmp})

            result = write_file(ctx, str(path), "def bad(:\n", overwrite=True)

            self.assertTrue(result["success"], result)
            self.assertGreaterEqual(result["diagnostic_error_count"], 1)
            self.assertEqual(result["diagnostics"][-1]["source"], "python")

    def test_read_file_returns_image_attachment_and_missing_suggestions(self) -> None:
        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "chart.png"
            image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
            ctx = SkillContext(meta={"workspace_path": tmp})

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


if __name__ == "__main__":
    unittest.main()
