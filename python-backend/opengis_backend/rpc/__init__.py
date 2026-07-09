"""JSON-RPC transport handlers."""

__all__ = ["RpcHandler", "dispatch_http"]


def __getattr__(name: str):
    if name == "RpcHandler":
        from opengis_backend.rpc.handler import RpcHandler

        return RpcHandler
    if name == "dispatch_http":
        from opengis_backend.rpc.http import dispatch_http

        return dispatch_http
    raise AttributeError(name)
