"""Custom Agent Loop -- the heart of OpenGIS's Hybrid CodeAct architecture.

Replaces smolagents' CodeAgent with a direct litellm-based loop that
lets the LLM autonomously decide at each step whether to:

1. Reply with plain text -- for greetings, explanations, clarifications.
2. Emit a ```python ... ``` code block -- executed in the subprocess sandbox.
3. Call final_answer() inside code -- signals the run is complete.

Termination strategy (aligned with mainstream open-source agents):
- Explicit tool termination: final_answer() in code -> loop exits.
- Response format detection: pure text reply -> task considered done,
  BUT only immediately when code_steps == 0 (simple conversation).
  When code_steps > 0, the loop nudges the LLM once to continue or
  call final_answer() explicitly. If it still replies with text, accept.
  This prevents premature termination from mid-task text explanations
  while adding at most one extra LLM call overhead.

References:
- CodeAct paper: "Executable Code Actions Elicit Better LLM Agents" (Wang et al., 2024)
- Claude Code: hybrid text/code agent loop with context compression
- OpenHands/OpenDevin: CodeAct + browsing in a sandboxed environment
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from opengis_backend.agent.context_manager import ContextManager
from opengis_backend.constants import (  # noqa: E402 module-level import
    DEFAULT_MAX_ITERATIONS,
    AGENT_LOOP_SAFETY_MULTIPLIER,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Retry configuration for transient LLM call failures
# ─────────────────────────────────────────────────────────────────────

LLM_MAX_RETRIES = 3
LLM_BASE_DELAY = 1.0  # seconds
LLM_RETRYABLE_EXCEPTIONS: tuple = (
    ConnectionError,
    TimeoutError,
    OSError,  # covers network-level issues like ConnectionResetError
)

# Include litellm exceptions (primary LLM library used in this project)
try:
    from litellm.exceptions import (
        APIConnectionError as LiteLLMConnectionError,
        InternalServerError as LiteLLMInternalError,
        RateLimitError as LiteLLMRateLimitError,
        ServiceUnavailableError as LiteLLMServiceUnavailable,
        BadGatewayError as LiteLLMBadGateway,
        APIError as LiteLLMAPIError,
    )
    LLM_RETRYABLE_EXCEPTIONS = (
        *LLM_RETRYABLE_EXCEPTIONS,
        LiteLLMConnectionError,
        LiteLLMInternalError,
        LiteLLMRateLimitError,
        LiteLLMServiceUnavailable,
        LiteLLMBadGateway,
        LiteLLMAPIError,
    )
except ImportError:
    pass

# Try to include httpx exceptions if available
try:
    import httpx
    LLM_RETRYABLE_EXCEPTIONS = (*LLM_RETRYABLE_EXCEPTIONS, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError)
except ImportError:
    pass

# Try to include openai SDK exceptions if available
try:
    from openai import APIConnectionError, APITimeoutError, RateLimitError
    LLM_RETRYABLE_EXCEPTIONS = (*LLM_RETRYABLE_EXCEPTIONS, APIConnectionError, APITimeoutError, RateLimitError)
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────
# Code block extraction
# ─────────────────────────────────────────────────────────────────────

# We support both markdown fences and <code> tags for code extraction.
# The LLM is prompted to use ```python fences (more natural than <code>
# tags now that we control the prompt).
_CODE_FENCE_RE = re.compile(
    r"```(?:python|py)?\s*\n(.*?)```",
    re.DOTALL,
)
_CODE_TAG_RE = re.compile(
    r"<code>\s*\n?(.*?)</code>",
    re.DOTALL,
)


def extract_code_block(text: str) -> Optional[str]:
    """Extract the first Python code block from LLM output.

    Tries markdown fences first, then <code> tags for backward compat.
    Returns None if no code block is found.
    """
    m = _CODE_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _CODE_TAG_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def extract_thought(text: str) -> str:
    """Extract the 'thought' portion -- everything before the code block."""
    # Remove code blocks to get the thought text.
    cleaned = _CODE_FENCE_RE.sub("", text)
    cleaned = _CODE_TAG_RE.sub("", cleaned)
    return cleaned.strip()


# ─────────────────────────────────────────────────────────────────────
# Streaming parser — splits token deltas into thought vs code as they arrive
# ─────────────────────────────────────────────────────────────────────

# Recognise an opening Python fence: ``` optionally followed by python/py
# and the rest of the line. We scan the buffer manually rather than with
# a regex because we need to distinguish "fence is fully open" from
# "fence is being typed character-by-character" (the LLM may stream
# `'`, `'`, `'` on three separate ticks).
_FENCE_OPEN_PREFIXES = ("```python", "```py", "```")
_FENCE_CLOSE = "```"


@dataclass
class StreamingParser:
    """State machine that classifies incoming LLM text as thought or code.

    The LLM's response is a single text stream that may contain at most
    one ```` ```python ... ``` ```` block (per CodeAct conventions).
    We feed each chunk in and emit:

    - ``on_thought_delta(text)`` — text outside fences
    - ``on_code_start()``        — first time we cross into a code body
    - ``on_code_delta(text)``    — text inside fences
    - ``on_code_end()``          — when we cross out of the code body

    Callbacks may be ``None`` and are called at most a few times per
    chunk. The parser holds at most a handful of chars in its
    look-ahead buffer (to avoid emitting half a fence as plain text).
    """

    on_thought_delta: Optional[Callable[[str], None]] = None
    on_code_start: Optional[Callable[[], None]] = None
    on_code_delta: Optional[Callable[[str], None]] = None
    on_code_end: Optional[Callable[[], None]] = None

    # Internal state.
    _state: str = field(default="thought", init=False)  # "thought" | "in_fence_open" | "code" | "in_fence_close"
    _pending: str = field(default="", init=False)       # bytes we're not sure about yet
    _emitted_open: bool = field(default=False, init=False)
    # When we just exited a code fence, the very next char may be a
    # trailing newline that the LLM put right after ``` (cosmetic).
    # We swallow it so the post-code thought doesn't begin with a blank
    # line in the chat UI. Resets after one consumption attempt.
    _swallow_next_newline: bool = field(default=False, init=False)

    # Maximum bytes we're willing to hold back as look-ahead. The longest
    # ambiguous prefix is `\n```python\n` (~12 chars); we round up.
    _MAX_HOLD = 16

    def feed(self, chunk: str) -> None:
        """Feed one streamed chunk into the parser."""
        if not chunk:
            return
        self._pending += chunk
        # Loop until we can't make progress without more input.
        while True:
            advanced = self._step()
            if not advanced:
                break

    def finish(self) -> None:
        """Flush any held-back text. Called once the stream ends."""
        if not self._pending:
            return
        if self._state == "thought":
            self._emit_thought(self._pending)
        elif self._state == "code":
            self._emit_code_delta(self._pending)
            self._emit_code_end()
        # Half-typed fences at the very end → just spit them back out
        # as thought; harmless edge case.
        elif self._state == "in_fence_open":
            self._emit_thought(self._pending)
        elif self._state == "in_fence_close":
            # We were inside code and saw the start of `````, but it
            # never finished. Treat the held bytes as code body.
            self._emit_code_delta(self._pending)
            self._emit_code_end()
        self._pending = ""

    # ── internals ──

    def _step(self) -> bool:
        """Try to consume some of `_pending`. Returns True if it did."""
        if not self._pending:
            return False

        if self._state == "thought":
            return self._step_thought()
        if self._state == "in_fence_open":
            return self._step_fence_open()
        if self._state == "code":
            return self._step_code()
        if self._state == "in_fence_close":
            return self._step_fence_close()
        return False

    # state: "thought" — looking for the start of a fence
    def _step_thought(self) -> bool:
        # Honour the post-code newline-swallow request.
        if self._swallow_next_newline and self._pending:
            self._swallow_next_newline = False
            if self._pending[0] == "\r":
                # Possible \r\n
                if len(self._pending) >= 2 and self._pending[1] == "\n":
                    self._pending = self._pending[2:]
                else:
                    self._pending = self._pending[1:]
                if not self._pending:
                    return False
            elif self._pending[0] == "\n":
                self._pending = self._pending[1:]
                if not self._pending:
                    return False

        idx = self._pending.find("`")
        if idx == -1:
            # No backtick anywhere → safe to flush everything.
            self._emit_thought(self._pending)
            self._pending = ""
            return False  # nothing left

        # Flush text before the suspect backtick.
        if idx > 0:
            self._emit_thought(self._pending[:idx])
            self._pending = self._pending[idx:]

        # `_pending` now starts with `. Switch into "in_fence_open"
        # so we can wait for enough chars to confirm or reject the fence.
        self._state = "in_fence_open"
        return True

    # state: "in_fence_open" — saw ` and waiting to confirm/refute fence
    def _step_fence_open(self) -> bool:
        buf = self._pending
        # Is what we have a prefix of any fence opener?
        # Possible openers we care about:
        #   ```\n   ```python\n   ```py\n
        # We need to either see a full opener (with newline), or see
        # something that *can't* be one and bail out.
        for opener in _FENCE_OPEN_PREFIXES:
            # Full match with terminating newline?
            for suffix in ("\n", "\r\n"):
                full = opener + suffix
                if buf.startswith(full):
                    # Open the code body.
                    self._pending = buf[len(full):]
                    self._enter_code()
                    return True
        # Could it still become an opener with more input?
        # i.e. is buf a strict prefix of any candidate?
        candidates = [op + s for op in _FENCE_OPEN_PREFIXES for s in ("\n", "\r\n")]
        if any(c.startswith(buf) for c in candidates):
            # Need more input — but if we've held too much, give up
            # and flush as plain text (this means the LLM wrote
            # backticks for a different reason, e.g. inline ```bash`).
            if len(buf) > self._MAX_HOLD:
                self._emit_thought(buf[0])
                self._pending = buf[1:]
                self._state = "thought"
                return True
            return False  # wait for more chunks
        # Definitively not a fence opener — emit one char and re-scan.
        self._emit_thought(buf[0])
        self._pending = buf[1:]
        self._state = "thought"
        return True

    # state: "code" — inside a code block, watching for closing fence
    def _step_code(self) -> bool:
        idx = self._pending.find("`")
        if idx == -1:
            self._emit_code_delta(self._pending)
            self._pending = ""
            return False

        if idx > 0:
            self._emit_code_delta(self._pending[:idx])
            self._pending = self._pending[idx:]

        self._state = "in_fence_close"
        return True

    # state: "in_fence_close" — saw ` inside code, waiting to see if it's ```
    def _step_fence_close(self) -> bool:
        buf = self._pending
        if buf.startswith(_FENCE_CLOSE):
            # Found the closer.
            self._pending = buf[len(_FENCE_CLOSE):]
            # Skip any trailing newline immediately after ``` (cosmetic).
            if self._pending.startswith("\r\n"):
                self._pending = self._pending[2:]
            elif self._pending.startswith("\n"):
                self._pending = self._pending[1:]
            else:
                # The newline may arrive in a later chunk; mark it for
                # one-shot swallowing the next time we touch thought.
                self._swallow_next_newline = True
            self._exit_code()
            # Whatever's left is post-code thought.
            return True
        if _FENCE_CLOSE.startswith(buf):
            # Maybe — wait for more.
            if len(buf) > self._MAX_HOLD:
                # Give up; spit back as code.
                self._emit_code_delta(buf[0])
                self._pending = buf[1:]
                self._state = "code"
                return True
            return False
        # Not a closer — those backticks are part of the code (e.g. a
        # literal `` inside a string).
        self._emit_code_delta(buf[0])
        self._pending = buf[1:]
        self._state = "code"
        return True

    # ── transitions ──

    def _enter_code(self) -> None:
        self._state = "code"
        if not self._emitted_open:
            self._emitted_open = True
            if self.on_code_start:
                try:
                    self.on_code_start()
                except Exception:
                    logger.exception("on_code_start callback raised")

    def _exit_code(self) -> None:
        self._state = "thought"
        if self.on_code_end:
            try:
                self.on_code_end()
            except Exception:
                logger.exception("on_code_end callback raised")

    def _emit_thought(self, text: str) -> None:
        if not text or not self.on_thought_delta:
            return
        try:
            self.on_thought_delta(text)
        except Exception:
            logger.exception("on_thought_delta callback raised")

    def _emit_code_delta(self, text: str) -> None:
        if not text or not self.on_code_delta:
            return
        try:
            self.on_code_delta(text)
        except Exception:
            logger.exception("on_code_delta callback raised")

    def _emit_code_end(self) -> None:
        if self.on_code_end and self._emitted_open:
            try:
                self.on_code_end()
            except Exception:
                logger.exception("on_code_end callback raised")


# ─────────────────────────────────────────────────────────────────────
# Step result from executor
# ─────────────────────────────────────────────────────────────────────

@dataclass
class CodeExecResult:
    """Result of executing a code block in the subprocess."""
    output: Any = None
    logs: str = ""
    error: Optional[str] = None
    is_final_answer: bool = False


# ─────────────────────────────────────────────────────────────────────
# Step callback protocol
# ─────────────────────────────────────────────────────────────────────

@dataclass
class AgentStep:
    """One step in the agent loop -- passed to step callbacks."""
    step_num: int
    thought: str = ""
    code: str = ""
    output: str = ""
    error: Optional[str] = None
    is_final_answer: bool = False
    is_text_reply: bool = False
    text_reply: str = ""
    duration_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────
# The Agent Loop
# ─────────────────────────────────────────────────────────────────────

@dataclass
class AgentLoop:
    """Custom agent loop implementing Hybrid CodeAct.

    The LLM decides at each step whether to respond with text or code.
    Code is executed in the subprocess sandbox; results feed back as
    observations for the next step.

    Termination uses a layered "nudge" strategy:
    - final_answer() in code -> explicit termination (preferred)
    - Pure text reply at code_steps==0 -> immediate exit (conversation)
    - Pure text reply at code_steps>0 -> nudge once, then accept
    - No extra LLM self-evaluation call (at most one nudge)

    Parameters
    ----------
    llm_call:
        Callable that takes a list of chat messages and returns the
        LLM's response as a string. This is the only LLM interface
        the loop needs -- provider routing is handled upstream.
    executor_call:
        Callable that takes a code string and returns a CodeExecResult.
        Wraps the SubprocessPythonExecutor.
    system_prompt:
        The full system prompt (with skill signatures baked in).
    max_steps:
        Hard cap on reasoning steps. After this many code executions,
        the loop stops and returns a best-effort summary.
    step_callback:
        Optional callback invoked after each step with an AgentStep.
    context:
        Optional pre-existing ContextManager (for multi-turn conversations).
    """

    llm_call: Callable[..., str]
    executor_call: Callable[[str], CodeExecResult]
    system_prompt: str
    max_steps: int = DEFAULT_MAX_ITERATIONS
    step_callback: Optional[Callable[[AgentStep], None]] = None
    progress_callback: Optional[Callable[[str, str], None]] = None
    # Streaming hooks — invoked from the LLM worker thread as tokens
    # arrive. The agent loop uses StreamingParser to dispatch into
    # these granular callbacks so the UI can render code as it's
    # written and collapse it once finished.
    on_thought_delta: Optional[Callable[[str], None]] = None
    on_code_start: Optional[Callable[[int], None]] = None  # arg: step number (1-indexed code step)
    on_code_delta: Optional[Callable[[int, str], None]] = None
    on_code_end: Optional[Callable[[int], None]] = None
    # Reasoning lifecycle hooks. A "reasoning round" wraps one LLM call.
    # If the round ends with code, we keep the reasoning collapsed.
    # If it ends with a pure text reply, we ask the UI to *promote* the
    # streamed reasoning into a normal assistant text bubble — that way
    # users see the same content but in the right place.
    on_reasoning_start: Optional[Callable[[int], None]] = None  # arg: reasoning round id
    on_reasoning_end: Optional[Callable[[int], None]] = None
    on_reasoning_promote: Optional[Callable[[int], None]] = None  # round became a text reply
    context: ContextManager = field(default_factory=ContextManager)
    user_instructions: Optional[str] = None
    # Set by external code (e.g. cancel handler) to signal the loop to
    # stop at the next safe point. Checked at the top of each iteration.
    _interrupted: bool = field(default=False, init=False, repr=False)
    _nudged_this_turn: bool = field(default=False, init=False, repr=False)

    def interrupt(self) -> None:
        """Signal the loop to stop at the next safe point."""
        import threading
        logger.info("[LOOP-DEBUG] interrupt() called from thread=%d, setting _interrupted=True", threading.get_ident())
        self._interrupted = True

    # -- Public API --------------------------------------------------

    def run(self, user_message: str) -> str:
        """Run the agent loop synchronously. Returns the final answer.

        This method blocks until the LLM produces a final answer, hits
        the step limit, or encounters an unrecoverable error.

        Called from a worker thread (via asyncio.to_thread) so it's safe
        to block on LLM calls and subprocess execution.
        """
        self.context.add_user_message(user_message)

        code_steps = 0  # Only count code execution steps toward the limit.
        reasoning_round_seq = 0  # Monotonic id for reasoning bubbles.
        self._nudged_this_turn = False  # Reset nudge state for this run.

        for iteration in range(self.max_steps * AGENT_LOOP_SAFETY_MULTIPLIER):  # Safety cap on total iterations
            # Check for external interruption.
            logger.info("[LOOP-DEBUG] iteration=%d, code_steps=%d, _interrupted=%s, thread=%d",
                        iteration, code_steps, self._interrupted, __import__('threading').get_ident())
            if self._interrupted:
                logger.info("[LOOP-DEBUG] EXITING due to _interrupted=True at iteration top")
                logger.info("Agent loop interrupted externally after %d code steps.", code_steps)
                return "(Task interrupted by user.)"
            # 1. Build messages with context compression.
            messages = self.context.build_messages(self.system_prompt, user_instructions=self.user_instructions)

            # 2. Call LLM — notify the UI that we're waiting.
            if self.progress_callback:
                stage = "calling_llm" if code_steps == 0 else "thinking_next_step"
                detail = (
                    f"Calling LLM (step {code_steps + 1})..."
                    if code_steps > 0
                    else "Calling LLM..."
                )
                try:
                    self.progress_callback(stage, detail)
                except Exception:
                    pass

            t0 = time.monotonic()

            # Build a streaming parser for this LLM call. We tee the raw
            # token stream into:
            #   - the on_thought_delta UI callback (text outside fences)
            #   - the on_code_* UI callbacks (text inside ```python```)
            #
            # The full assembled string is still returned by llm_call for
            # the regex-based extract_code_block fallback below — that
            # way if the parser ever misclassifies something, the
            # agent's behaviour is unchanged.
            tentative_step = code_steps + 1
            code_started = {"v": False}
            # Each LLM call starts with a fresh reasoning round; if the
            # LLM emits text *after* the code fence too (some models do),
            # we open a *second* reasoning round for that tail.
            reasoning_round_seq += 1
            current_round = {"id": reasoning_round_seq}
            reasoning_open = {"v": False}

            def _bump_round() -> None:
                nonlocal reasoning_round_seq
                reasoning_round_seq += 1
                current_round["id"] = reasoning_round_seq

            def _open_reasoning_if_needed() -> None:
                if reasoning_open["v"]:
                    return
                reasoning_open["v"] = True
                if self.on_reasoning_start:
                    try:
                        self.on_reasoning_start(current_round["id"])
                    except Exception:
                        logger.exception("on_reasoning_start failed")

            def _close_reasoning_if_open() -> None:
                if not reasoning_open["v"]:
                    return
                if self.on_reasoning_end:
                    try:
                        self.on_reasoning_end(current_round["id"])
                    except Exception:
                        logger.exception("on_reasoning_end failed")
                reasoning_open["v"] = False

            def _on_chunk_thought(text: str) -> None:
                # Lazily open the reasoning card on the first thought
                # token. This avoids creating an empty card when the
                # LLM jumps straight into a code fence.
                _open_reasoning_if_needed()
                if self.on_thought_delta:
                    try:
                        self.on_thought_delta(text)
                    except Exception:
                        logger.exception("on_thought_delta failed")

            def _on_chunk_code_start() -> None:
                code_started["v"] = True
                # Close out the reasoning card \u2014 the next thing the
                # user sees will be a code block.
                _close_reasoning_if_open()
                # Allocate a fresh round id for any post-code thought
                # that may follow the code fence in this same response.
                _bump_round()
                if self.on_code_start:
                    try:
                        self.on_code_start(tentative_step)
                    except Exception:
                        logger.exception("on_code_start failed")

            def _on_chunk_code_delta(text: str) -> None:
                if self.on_code_delta:
                    try:
                        self.on_code_delta(tentative_step, text)
                    except Exception:
                        logger.exception("on_code_delta failed")

            def _on_chunk_code_end() -> None:
                if self.on_code_end:
                    try:
                        self.on_code_end(tentative_step)
                    except Exception:
                        logger.exception("on_code_end failed")

            parser = StreamingParser(
                on_thought_delta=_on_chunk_thought,
                on_code_start=_on_chunk_code_start,
                on_code_delta=_on_chunk_code_delta,
                on_code_end=_on_chunk_code_end,
            )

            def _on_llm_delta(piece: str) -> None:
                parser.feed(piece)

            logger.info("[LOOP-DEBUG] LLM call START, _interrupted=%s", self._interrupted)
            response = None
            for _retry_attempt in range(LLM_MAX_RETRIES + 1):
                try:
                    response = self.llm_call(messages, on_delta=_on_llm_delta)
                    parser.finish()
                    break
                except TypeError:
                    # llm_call doesn't accept on_delta (older shim) — fall
                    # back to non-streaming and emit a single thought delta.
                    response = self.llm_call(messages)
                    if response and self.on_thought_delta:
                        try:
                            self.on_thought_delta(response)
                        except Exception as e:
                            logger.warning("on_thought_delta failed: %s", e)
                    break
                except LLM_RETRYABLE_EXCEPTIONS as e:
                    if self._interrupted:
                        logger.info("[LOOP-DEBUG] Interrupted during retry, not retrying.")
                        raise
                    if _retry_attempt >= LLM_MAX_RETRIES:
                        logger.error("[LOOP-DEBUG] LLM call failed after %d retries: %s(%s)",
                                     LLM_MAX_RETRIES, type(e).__name__, e)
                        raise
                    delay = LLM_BASE_DELAY * (2 ** _retry_attempt) + random.uniform(0, 0.5)
                    logger.warning(
                        "[LOOP-RETRY] LLM call attempt %d/%d failed (%s: %s), retrying in %.1fs...",
                        _retry_attempt + 1, LLM_MAX_RETRIES, type(e).__name__, e, delay,
                    )
                    if self.progress_callback:
                        try:
                            self.progress_callback("retrying", f"Connection error, retrying ({_retry_attempt + 1}/{LLM_MAX_RETRIES})...")
                        except Exception:
                            pass
                    time.sleep(delay)
                    # Reset the streaming parser for the retry attempt
                    parser = StreamingParser(
                        on_thought_delta=_on_chunk_thought,
                        on_code_start=_on_chunk_code_start,
                        on_code_delta=_on_chunk_code_delta,
                        on_code_end=_on_chunk_code_end,
                    )
                except Exception as e:
                    logger.error("[LOOP-DEBUG] LLM call EXCEPTION (non-retryable): %s(%s), _interrupted=%s",
                                 type(e).__name__, e, self._interrupted)
                    raise

            duration_ms = (time.monotonic() - t0) * 1000
            logger.info("[LOOP-DEBUG] LLM call END, duration=%.0fms, _interrupted=%s, response_len=%d",
                        duration_ms, self._interrupted, len(response) if response else 0)

            # 3. Parse response: extract thought + code block.
            code_block = extract_code_block(response)
            thought = extract_thought(response)

            # 4a. Pure text reply -- no code block.
            #
            # Termination strategy (layered):
            #   - code_steps == 0: Simple conversation / greeting. The LLM
            #     chose not to write code at all -> trust it, exit immediately.
            #   - code_steps > 0: The agent is mid-task. A text-only reply
            #     is likely an accidental pause (LLM explaining next steps
            #     instead of writing code). We give it ONE nudge to continue.
            #     If it replies with text again, accept that as completion.
            #   - No extra LLM self-evaluation call -- at most one nudge.
            if code_block is None:
                # Mid-task nudge: if we've already executed code and haven't
                # nudged yet this turn, push the LLM to continue or finish
                # explicitly. This prevents premature termination when the
                # LLM emits "Next I will..." without a code block.
                if code_steps > 0 and not getattr(self, '_nudged_this_turn', False):
                    self._nudged_this_turn = True
                    logger.info(
                        "Text-only reply mid-task (code_steps=%d) -- nudging LLM to continue.",
                        code_steps,
                    )
                    # Close reasoning bubble for this intermediate text.
                    _close_reasoning_if_open()
                    self.context.add_assistant_message(response)
                    self.context.add_user_message(
                        "[System] You are mid-task. Either write a ```python code block "
                        "to continue, or call final_answer(\"summary\") to finish. "
                        "A plain text reply will end the task."
                    )
                    continue

                # Genuine completion: either first-turn text or post-nudge text.
                self._nudged_this_turn = False  # Reset for next run.
                # No code arrived this round -> the streamed reasoning IS
                # the final answer. Ask the UI to convert the reasoning
                # bubble into a normal text bubble.
                if reasoning_open["v"] and self.on_reasoning_promote:
                    try:
                        self.on_reasoning_promote(current_round["id"])
                    except Exception:
                        logger.exception("on_reasoning_promote failed")
                    reasoning_open["v"] = False
                self.context.add_assistant_message(response)
                step = AgentStep(
                    step_num=code_steps + 1,
                    thought=thought,
                    is_text_reply=True,
                    text_reply=response,
                    duration_ms=duration_ms,
                )
                if self.step_callback:
                    try:
                        self.step_callback(step)
                    except Exception:
                        logger.exception("step_callback failed")

                logger.info(
                    "Pure text reply after %d code steps -- treating as task complete.",
                    code_steps,
                )
                return response

            # 4b. Code block found -> execute.
            code_steps += 1
            self._nudged_this_turn = False  # Reset: next text-only gets a fresh nudge.
            # If the LLM wrote some text *after* the code fence (post-code
            # thought / explanation), close that reasoning bubble too \u2014
            # we don't promote it because the round did contain code.
            _close_reasoning_if_open()
            self.context.add_assistant_message(response)

            # Auto-install missing packages before execution.
            try:
                from opengis_backend.agent.auto_install import auto_install_missing
                auto_install_missing(
                    code_block,
                    python_executable=None,  # uses sys.executable (same as subprocess)
                    progress_callback=self.progress_callback,
                )
            except Exception:
                logger.debug("auto_install check failed (non-fatal)", exc_info=True)

            # Notify the UI that code is about to execute.
            if self.progress_callback:
                try:
                    self.progress_callback(
                        "executing_code",
                        f"Executing code (step {code_steps})...",
                    )
                except Exception:
                    pass

            t1 = time.monotonic()
            logger.info("[LOOP-DEBUG] CODE EXEC START step=%d, _interrupted=%s, code_len=%d",
                        code_steps, self._interrupted, len(code_block))
            try:
                result = self.executor_call(code_block)
            except Exception as e:
                # Executor-level failure (child died, timeout, etc.)
                error_msg = f"{type(e).__name__}: {e}"
                logger.info("[LOOP-DEBUG] CODE EXEC EXCEPTION: %s, _interrupted=%s", error_msg, self._interrupted)
                result = CodeExecResult(error=error_msg)
            exec_duration_ms = (time.monotonic() - t1) * 1000
            logger.info("[LOOP-DEBUG] CODE EXEC END step=%d, duration=%.0fms, error=%s, is_final=%s, _interrupted=%s",
                        code_steps, exec_duration_ms, result.error is not None, result.is_final_answer, self._interrupted)

            # Build the step record.
            step = AgentStep(
                step_num=code_steps,
                thought=thought,
                code=code_block,
                output=result.logs or str(result.output or ""),
                error=result.error,
                is_final_answer=result.is_final_answer,
                duration_ms=duration_ms + exec_duration_ms,
            )

            # Fire step callback (feeds StepRecorder -> events).
            if self.step_callback:
                try:
                    self.step_callback(step)
                except Exception:
                    logger.exception("step_callback failed")

            # Check for final_answer.
            if result.is_final_answer:
                return str(result.output) if result.output is not None else "(done)"

            # Add observation to context.
            # Detect skill calls so the context manager can flag this
            # tool result as protected (never pruned). Skills carry
            # artifacts (layer ids, file paths, snapshot ids) the agent
            # references many turns later.
            tool_name = "skill" if "skill(" in (code_block or "") else None
            self.context.add_code_output(
                step=code_steps,
                code=code_block,
                output=result.logs or str(result.output or ""),
                error=result.error,
                tool_name=tool_name,
            )

            # Context compression check.
            should_compress, reason = self.context.should_compress()
            if should_compress:
                logger.info("Compression triggered: %s", reason)
                self.context.compress(self.llm_call)

            # Step limit check.
            if code_steps >= self.max_steps:
                return self._generate_max_steps_summary(code_steps)

        # Should never reach here, but safety net.
        return self._generate_max_steps_summary(code_steps)

    # -- Internal ----------------------------------------------------

    def _generate_max_steps_summary(self, steps_taken: int) -> str:
        """Generate a best-effort summary when the step limit is reached.

        We ask the LLM to summarize what was accomplished so far.
        """
        summary_prompt = (
            f"You have reached the maximum number of steps ({steps_taken}). "
            "Please summarize what you've accomplished so far and what "
            "remains to be done. Do NOT write any code -- just provide a "
            "text summary."
        )
        self.context.add_user_message(summary_prompt)
        messages = self.context.build_messages(self.system_prompt)
        try:
            response = self.llm_call(messages)
            self.context.add_assistant_message(response)
            return response
        except Exception as e:
            logger.error("Summary generation failed: %s", e)
            return (
                f"Reached maximum steps ({steps_taken}). "
                "Unable to generate summary due to an error."
            )


__all__ = [
    "AgentLoop",
    "AgentStep",
    "CodeExecResult",
    "StreamingParser",
    "extract_code_block",
    "extract_thought",
]
