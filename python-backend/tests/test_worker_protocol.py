import json
import unittest

from opengis_backend.worker.protocol import DYNAMIC_LAYER_METHOD, WORKER_HELPER_CODE, parse_worker_event


class WorkerProtocolTests(unittest.TestCase):
    def test_parse_rpc_ui_method_event(self):
        event = parse_worker_event(json.dumps({
            "opengis_method": "rpc.ui.map.dynamic_layer_update",
            "params": {"layer_id": "live", "sequence": 3},
        }))

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.method, DYNAMIC_LAYER_METHOD)
        self.assertEqual(event.params["layer_id"], "live")
        self.assertEqual(event.params["sequence"], 3)

    def test_rejects_non_ui_or_invalid_lines(self):
        self.assertIsNone(parse_worker_event("not-json"))
        self.assertIsNone(parse_worker_event(json.dumps({"opengis_method": "rpc.agent.stop"})))

    def test_helper_contract_mentions_main_dynamic_apis(self):
        self.assertIn("emit_dynamic_layer_update", WORKER_HELPER_CODE)
        self.assertIn("emit_dynamic_layer_diff", WORKER_HELPER_CODE)
        self.assertIn("emit_moving_objects", WORKER_HELPER_CODE)


if __name__ == "__main__":
    unittest.main()
