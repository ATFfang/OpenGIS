import asyncio
import json
import unittest

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True

if FASTAPI_AVAILABLE:
    from opengis_backend.rpc_handler import (
        DYNAMIC_LAYER_BACKEND_FLUSH_SECONDS,
        DYNAMIC_LAYER_UPDATE_METHOD,
        RpcHandler,
    )
else:
    DYNAMIC_LAYER_BACKEND_FLUSH_SECONDS = 0.05
    DYNAMIC_LAYER_UPDATE_METHOD = "rpc.ui.map.dynamic_layer_update"
    RpcHandler = None
from opengis_backend.tools.registry import ToolRegistry


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, data: str) -> None:
        self.messages.append(data)


def _empty_fc() -> dict:
    return {"type": "FeatureCollection", "features": []}


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is not installed in this lightweight test environment")
class RpcWorkerDynamicCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_dynamic_full_frames_are_coalesced_before_websocket(self) -> None:
        ws = FakeWebSocket()
        handler = RpcHandler(ws, ToolRegistry())  # type: ignore[arg-type]
        handler._loop = asyncio.get_running_loop()
        try:
            handler._notify_worker_event(
                DYNAMIC_LAYER_UPDATE_METHOD,
                {"layer_id": "live", "mode": "full", "geojson": _empty_fc(), "sequence": 1},
            )
            handler._notify_worker_event(
                DYNAMIC_LAYER_UPDATE_METHOD,
                {"layer_id": "live", "mode": "full", "geojson": _empty_fc(), "sequence": 2},
            )

            await asyncio.sleep(DYNAMIC_LAYER_BACKEND_FLUSH_SECONDS + 0.05)

            self.assertEqual(len(ws.messages), 1, ws.messages)
            sent = json.loads(ws.messages[0])
            self.assertEqual(sent["method"], DYNAMIC_LAYER_UPDATE_METHOD)
            self.assertEqual(sent["params"]["sequence"], 2)
        finally:
            handler.mark_closed()

    async def test_worker_dynamic_full_plus_diff_order_is_preserved(self) -> None:
        ws = FakeWebSocket()
        handler = RpcHandler(ws, ToolRegistry())  # type: ignore[arg-type]
        handler._loop = asyncio.get_running_loop()
        try:
            handler._notify_worker_event(
                DYNAMIC_LAYER_UPDATE_METHOD,
                {"layer_id": "live", "mode": "full", "geojson": _empty_fc(), "sequence": 1},
            )
            handler._notify_worker_event(
                DYNAMIC_LAYER_UPDATE_METHOD,
                {"layer_id": "live", "mode": "diff", "diff": {"update": []}, "sequence": 2},
            )

            await asyncio.sleep(DYNAMIC_LAYER_BACKEND_FLUSH_SECONDS + 0.05)

            sent = [json.loads(item) for item in ws.messages]
            self.assertEqual(len(sent), 2, sent)
            self.assertEqual([item["params"]["sequence"] for item in sent], [1, 2])
            self.assertEqual([item["params"]["mode"] for item in sent], ["full", "diff"])
        finally:
            handler.mark_closed()


if __name__ == "__main__":
    unittest.main()
