"""TCP client for QGIS MCP plugin.

Uses the same 4-byte big-endian length-prefixed framing as the
QGIS MCP plugin (struct.Struct('>I')). Thread-safe, lazy-connect,
auto-reconnect on failure.
"""

import json
import logging
import os
import socket
import struct
import threading
from typing import Any

logger = logging.getLogger("opengis.qgis.client")

_HEADER = struct.Struct(">I")
_DEFAULT_HOST = os.environ.get("QGIS_MCP_HOST", "localhost")
_DEFAULT_PORT = int(os.environ.get("QGIS_MCP_PORT", "9876"))
_TIMEOUT = 5.0


class QgisConnectionError(Exception):
    """Raised when QGIS MCP connection fails."""


class QgisClient:
    """Length-prefixed TCP client for the QGIS MCP plugin."""

    def __init__(self, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT):
        self.host = host
        self.port = port
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()

    # ── connection lifecycle ──────────────────────────────────────

    def connect(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_TIMEOUT)
        try:
            sock.connect((self.host, self.port))
        except OSError as exc:
            sock.close()
            raise QgisConnectionError(
                f"Cannot connect to QGIS at {self.host}:{self.port}: {exc}"
            ) from exc
        self._socket = sock
        logger.info("Connected to QGIS at %s:%s", self.host, self.port)

    def disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    # ── protocol ─────────────────────────────────────────────────

    def send_command(self, command_type: str, params: dict[str, Any] | None = None) -> dict:
        """Send a command and return the unwrapped result dict.

        Raises QgisConnectionError on connection failure.
        Raises RuntimeError if QGIS returns status=error.
        """
        with self._lock:
            self.connect()
            command = {"type": command_type, "params": params or {}}
            payload = json.dumps(command).encode("utf-8")
            header = _HEADER.pack(len(payload))

            try:
                self._socket.sendall(header + payload)
                response = self._recv_framed()
            except OSError as exc:
                self.disconnect()
                raise QgisConnectionError(f"Lost connection to QGIS: {exc}") from exc

            if response is None:
                self.disconnect()
                raise QgisConnectionError("QGIS closed the connection")

            if response.get("status") == "error":
                raise RuntimeError(response.get("message", "Unknown QGIS error"))

            return response.get("result", response)

    def _recv_framed(self) -> dict | None:
        header = self._recv_exact(4)
        if header is None:
            return None
        msg_len = _HEADER.unpack(header)[0]
        payload = self._recv_exact(msg_len)
        if payload is None:
            return None
        return json.loads(payload.decode("utf-8"))

    def _recv_exact(self, num_bytes: int) -> bytes | None:
        buf = b""
        while len(buf) < num_bytes:
            chunk = self._socket.recv(num_bytes - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf


# ── singleton ────────────────────────────────────────────────────

_client: QgisClient | None = None
_singleton_lock = threading.Lock()


def get_client() -> QgisClient:
    """Return the singleton QgisClient (lazy-init)."""
    global _client
    if _client is not None:
        return _client
    with _singleton_lock:
        if _client is None:
            _client = QgisClient()
    return _client


def reset_client() -> None:
    """Disconnect and clear the singleton."""
    global _client
    with _singleton_lock:
        if _client is not None:
            _client.disconnect()
            _client = None
