"""OpenGIS v3 Protocol Types (Pydantic).

Mirror of `src/types/protocol.ts`. When you change one side, update the other.

Reference:
    docs/api/INTERFACE.md §0.5/0.6
    docs/v3/protocol.md

Why a separate file from `protocol.py`:
    The legacy `protocol.py` is a runtime JSON-RPC handler (pre-v3 code).
    Stage 3 will rename it to `rpc_handler.py`. For now we keep the type
    models isolated here so nothing collides.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Literal, Optional, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field

# ─────────────────────────────────────────────────────────────────────
# Protocol version
# ─────────────────────────────────────────────────────────────────────

PROTOCOL_VERSION: Literal["3.0"] = "3.0"

# ─────────────────────────────────────────────────────────────────────
# Method prefixes (three-channel routing)
# ─────────────────────────────────────────────────────────────────────

METHOD_PREFIX_RPC = "rpc."
METHOD_PREFIX_CHAT = "chat."
METHOD_PREFIX_EVENT = "event."


class MethodChannel(str, Enum):
    RPC = "rpc"
    CHAT = "chat"
    EVENT = "event"


def get_method_channel(method: str) -> Optional[MethodChannel]:
    """Classify a JSON-RPC method into one of the three channels.

    Returns None for unknown / legacy methods (e.g. the pre-v3 flat names
    like ``agent.chat`` or ``gis.loadFile``). Callers should treat None
    as "legacy" and keep the current dispatch path.
    """
    if method.startswith(METHOD_PREFIX_RPC):
        return MethodChannel.RPC
    if method.startswith(METHOD_PREFIX_CHAT):
        return MethodChannel.CHAT
    if method.startswith(METHOD_PREFIX_EVENT):
        return MethodChannel.EVENT
    return None


# ─────────────────────────────────────────────────────────────────────
# Geometry & spatial reference
# ─────────────────────────────────────────────────────────────────────

# [minX, minY, maxX, maxY] — unit depends on CRS; WGS84 = lon/lat.
BBox = tuple[float, float, float, float]

# EPSG code string, e.g. "EPSG:4326", "EPSG:3857".
CRS = str


class GeometryType(str, Enum):
    POINT = "Point"
    MULTI_POINT = "MultiPoint"
    LINE_STRING = "LineString"
    MULTI_LINE_STRING = "MultiLineString"
    POLYGON = "Polygon"
    MULTI_POLYGON = "MultiPolygon"
    GEOMETRY_COLLECTION = "GeometryCollection"
    RASTER = "Raster"


class LayerSource(str, Enum):
    FILE = "file"
    MEMORY = "memory"
    URL = "url"
    POSTGIS = "postgis"


# ─────────────────────────────────────────────────────────────────────
# Rendering style
# ─────────────────────────────────────────────────────────────────────


class LayerStyleType(str, Enum):
    CIRCLE = "circle"
    LINE = "line"
    FILL = "fill"
    RASTER = "raster"
    SYMBOL = "symbol"


class LayerStyle(BaseModel):
    """MapLibre-compatible layer style snapshot."""

    model_config = ConfigDict(extra="forbid")

    type: LayerStyleType
    paint: Optional[dict[str, Any]] = None
    layout: Optional[dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 messages
# ─────────────────────────────────────────────────────────────────────

TParams = TypeVar("TParams")
TResult = TypeVar("TResult")
TErrorData = TypeVar("TErrorData")


class JsonRpcRequest(BaseModel, Generic[TParams]):
    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    method: str
    params: TParams


class JsonRpcErrorObject(BaseModel, Generic[TErrorData]):
    """Error payload — codes defined in docs/api/INTERFACE.md §0.4."""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: Optional[TErrorData] = None


class JsonRpcSuccessResponse(BaseModel, Generic[TResult]):
    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: TResult


class JsonRpcErrorResponse(BaseModel, Generic[TErrorData]):
    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    error: JsonRpcErrorObject[TErrorData]


# Union type for responses — use `JsonRpcResponse[TResult, TErrorData]`
# at usage sites. Pydantic handles discriminated unions on `result`/`error`
# via field presence at validation time.
JsonRpcResponse = Union[
    JsonRpcSuccessResponse[TResult],
    JsonRpcErrorResponse[TErrorData],
]


class JsonRpcNotification(BaseModel, Generic[TParams]):
    """Notifications have no ``id`` and never expect a response."""

    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: TParams


# ─────────────────────────────────────────────────────────────────────
# Well-known JSON-RPC error codes
# (subset of https://www.jsonrpc.org/specification + OpenGIS custom range)
# ─────────────────────────────────────────────────────────────────────


class JsonRpcErrorCode:
    """Numeric error codes we return over the wire."""

    # Standard JSON-RPC 2.0
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # OpenGIS-reserved range: -32000 ~ -32099 (see INTERFACE.md §0.4)
    AGENT_CANCELLED = -32001
    AGENT_TIMEOUT = -32002
    SANDBOX_DENIED = -32010
    SKILL_NOT_FOUND = -32020
    SKILL_TIMEOUT = -32021


__all__ = [
    "PROTOCOL_VERSION",
    "METHOD_PREFIX_RPC",
    "METHOD_PREFIX_CHAT",
    "METHOD_PREFIX_EVENT",
    "MethodChannel",
    "get_method_channel",
    "BBox",
    "CRS",
    "GeometryType",
    "LayerSource",
    "LayerStyleType",
    "LayerStyle",
    "JsonRpcRequest",
    "JsonRpcErrorObject",
    "JsonRpcSuccessResponse",
    "JsonRpcErrorResponse",
    "JsonRpcResponse",
    "JsonRpcNotification",
    "JsonRpcErrorCode",
]
