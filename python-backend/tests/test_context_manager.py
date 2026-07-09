import unittest

from opengis_backend.agent.context.context_manager import ContextManager


class ContextManagerPruneTests(unittest.TestCase):
    def test_prune_tool_results_keeps_protected_tool_outputs(self):
        ctx = ContextManager(
            keep_recent=0,
            use_token_based_pruning=False,
            max_single_result_tokens=0,
        )
        protected = "skill instructions\n" + ("important\n" * 500)
        ordinary = "directory listing\n" + ("file.py\n" * 500)

        ctx.add_tool_result("call-load-skill", "load_skill", protected)
        ctx.add_tool_result("call-list-directory", "list_directory", ordinary)

        saved = ctx.prune_tool_results()

        self.assertGreater(saved, 0)
        self.assertEqual(ctx.messages[0]["content"], protected)
        self.assertIn("body removed to save tokens", ctx.messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
