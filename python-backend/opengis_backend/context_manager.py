"""Backward compatibility shim for ContextManager.

Re-exports ContextManager from opengis_backend.agent.context_manager
to maintain backward compatibility with older import paths.

New code should import directly from opengis_backend.agent.context_manager.
"""

from opengis_backend.agent.context_manager import ContextManager

__all__ = ["ContextManager"]
