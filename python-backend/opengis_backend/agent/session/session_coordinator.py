"""Process-local session coordinator.

This is the harness boundary for one Python backend process. It prevents two
runs for the same conversation/session from owning the same active loop at the
same time and gives cancellation/resume code a single place to find ownership.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass
class SessionLease:
    key: str
    run_id: str
    acquired_at: float = field(default_factory=time.time)


class SessionBusyError(RuntimeError):
    pass


class SessionCoordinator:
    """Small process-local lease table keyed by conversation/session id."""

    _lock = threading.RLock()
    _leases: dict[str, SessionLease] = {}

    @classmethod
    @contextmanager
    def lease(cls, key: str, run_id: str) -> Iterator[SessionLease]:
        lease = cls.acquire(key, run_id)
        try:
            yield lease
        finally:
            cls.release(key, run_id)

    @classmethod
    def acquire(cls, key: str, run_id: str) -> SessionLease:
        normalized = key or "<no-session>"
        with cls._lock:
            current = cls._leases.get(normalized)
            if current and current.run_id != run_id:
                raise SessionBusyError(
                    f"Session {normalized} is already running run {current.run_id}"
                )
            lease = SessionLease(key=normalized, run_id=run_id)
            cls._leases[normalized] = lease
            logger.debug("session lease acquired key=%s run=%s", normalized, run_id)
            return lease

    @classmethod
    def release(cls, key: str, run_id: str) -> None:
        normalized = key or "<no-session>"
        with cls._lock:
            current = cls._leases.get(normalized)
            if current and current.run_id == run_id:
                del cls._leases[normalized]
                logger.debug("session lease released key=%s run=%s", normalized, run_id)

    @classmethod
    def current(cls, key: str) -> SessionLease | None:
        with cls._lock:
            return cls._leases.get(key or "<no-session>")


__all__ = ["SessionBusyError", "SessionCoordinator", "SessionLease"]
