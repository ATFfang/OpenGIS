"""Reusable project-level Operations.

Operations are validated, runnable GIS/analysis capsules stored under
``<workspace>/.opengis/operations``. They sit above tools and below workflows:
tools provide platform primitives, operations package reusable domain programs,
and workflows compose multiple operations/tools.
"""

from .store import OperationError, OperationStore

__all__ = ["OperationError", "OperationStore"]
