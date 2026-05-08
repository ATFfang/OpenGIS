"""Internal constants for the OpenGIS backend.

These are NOT user-configurable (those go in config.py).
All timeouts are in seconds unless noted otherwise.
"""

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

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

# Exec timeout step multiplier (max_steps * this = safety cap)
EXEC_STEP_TIMEOUT_MULTIPLIER: float = 30.0

# ---------------------------------------------------------------------------
# Iteration / step limits
# ---------------------------------------------------------------------------

# Default max agent iterations
DEFAULT_MAX_ITERATIONS: int = 10

# Safety cap: actual loop runs up to max_steps * 2
AGENT_LOOP_SAFETY_MULTIPLIER: int = 2
