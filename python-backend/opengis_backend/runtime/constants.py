"""Internal constants for the OpenGIS backend.

These are NOT user-configurable (those go in config.py).
All timeouts are in seconds unless noted otherwise.
"""

# ─────────────────────────────────────────────────────────────────────
# Timeouts
# ─────────────────────────────────────────────────────────────────────

# Min per-run exec timeout (1 s — prevents near-instant retries)
MIN_EXEC_TIMEOUT: float = 1.0

# Max per-run exec timeout (1 h hard cap)
MAX_EXEC_TIMEOUT: float = 3600.0

# Default per-run exec timeout (10 min)
DEFAULT_EXEC_TIMEOUT: float = 600.0

# LLM title generation timeout (3 s)
TITLE_GEN_TIMEOUT: float = 3.0

# Cleanup future wait timeout (10 s)
CLEANUP_FUTURE_TIMEOUT: float = 10.0
