import contextlib
import io
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

    def test_helper_accepts_agent_friendly_point_aliases_and_style_shorthand(self):
        namespace: dict[str, object] = {}
        exec(WORKER_HELPER_CODE, namespace)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            namespace["emit_dynamic_points"](
                layer_id="live_points",
                name="Live Points",
                points=[
                    {"icao24": "a1", "longitude": 121.5, "latitude": 31.2, "callsign": "TEST1"},
                ],
                lon="longitude",
                lat="latitude",
                id_key="icao24",
                label="callsign",
                size=12,
                color="#ff4444",
                opacity=0.75,
                sequence=1,
            )

        event = json.loads(output.getvalue())
        params = event["params"]
        self.assertEqual(event["opengis_method"], DYNAMIC_LAYER_METHOD)
        self.assertEqual(params["mode"], "full")
        self.assertEqual(params["geojson"]["features"][0]["id"], "a1")
        self.assertEqual(params["geojson"]["features"][0]["properties"]["label"], "TEST1")
        self.assertEqual(params["style"]["type"], "circle")
        self.assertEqual(params["style"]["paint"]["circle-color"], "#ff4444")
        self.assertEqual(params["style"]["paint"]["circle-radius"], 12)
        self.assertEqual(params["style"]["paint"]["circle-opacity"], 0.75)

    def test_helper_accepts_color_size_aliases_inside_style_dict(self):
        namespace: dict[str, object] = {}
        exec(WORKER_HELPER_CODE, namespace)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            namespace["emit_dynamic_points"](
                layer_id="live_points",
                name="Live Points",
                points=[{"id": "p1", "lon": 121.5, "lat": 31.2}],
                style={"color": "#e74c3c", "size": 10, "opacity": 0.5},
                sequence=1,
            )

        params = json.loads(output.getvalue())["params"]
        self.assertEqual(params["style"]["type"], "circle")
        self.assertEqual(params["style"]["paint"]["circle-color"], "#e74c3c")
        self.assertEqual(params["style"]["paint"]["circle-radius"], 10)
        self.assertEqual(params["style"]["paint"]["circle-opacity"], 0.5)

    def test_helper_accepts_geojson_track_features(self):
        namespace: dict[str, object] = {}
        exec(WORKER_HELPER_CODE, namespace)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            namespace["emit_dynamic_tracks"](
                layer_id="live_tracks",
                name="Live Tracks",
                tracks={
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": "flight-1",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[121.0, 31.0], [121.1, 31.1], [121.2, 31.2]],
                            },
                            "properties": {"name": "flight-1"},
                        }
                    ],
                },
                width=4,
                color="#22c55e",
                sequence=1,
            )

        event = json.loads(output.getvalue())
        params = event["params"]
        self.assertEqual(params["geojson"]["features"][0]["geometry"]["type"], "LineString")
        self.assertEqual(params["style"]["type"], "line")
        self.assertEqual(params["style"]["paint"]["line-color"], "#22c55e")
        self.assertEqual(params["style"]["paint"]["line-width"], 4)


if __name__ == "__main__":
    unittest.main()
