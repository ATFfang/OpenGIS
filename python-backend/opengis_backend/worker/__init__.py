"""Resident worker runtime for long-running OpenGIS background tasks."""

from opengis_backend.worker.manager import ResidentWorkerManager, get_worker_manager

__all__ = ["ResidentWorkerManager", "get_worker_manager"]

