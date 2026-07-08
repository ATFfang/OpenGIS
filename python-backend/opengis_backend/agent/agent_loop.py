"""Agent Loop — function-call-first architecture.

The LLM can respond in two ways at each step:
1. **Tool calls** (primary): structured function calls executed directly.
2. **Text replies**: final answer or a short nudge target after tool work.

Termination:
- Tool calls → loop continues until LLM stops calling tools (finish_reason="stop")
- Text reply → treated as final answer
- Max steps exceeded → LLM summarization
- OpenCode-style: function-call tools with bounded tool output and context compression
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from opengis_backend.agent.context_manager import ContextManager
from opengis_backend.agent.tool_runtime import (
    ToolRuntime,
    parse_tool_arguments,
    validate_execute_code_payload,
)
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
# Streaming parser — splits token deltas into thought vs code as they arrive
# ─────────────────────────────────────────────────────────────────────

# Recognise an opening Python fence: ``` optionally followed by python/py
# and the rest of the line. We scan the buffer manually rather than with
# a regex because we need to distinguish "fence is fully open" from
# "fence is being typed character-by-character" (the LLM may stream
# `'`, `'`, `'` on three separate ticks).
_FENCE_OPEN_MARKER = "```"
_FENCE_CLOSE = "```"


@dataclass
class StreamingParser:
    """State machine that classifies incoming LLM text as thought or code.

    The LLM's response is a single text stream that may contain at most
    one ```` ```python ... ``` ```` block while the execute_code tool
    arguments are being streamed.
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
        # Only explicit Python fences are streamed/executed as code.
        # Non-Python fences are assistant text and must not open a code
        # execution card.
        if buf.startswith(_FENCE_OPEN_MARKER):
            # Look for the terminating newline after ```.
            for suffix in ("\n", "\r\n"):
                idx = buf.find(suffix, len(_FENCE_OPEN_MARKER))
                if idx >= 0:
                    fence_header = buf[len(_FENCE_OPEN_MARKER):idx].strip().lower()
                    if fence_header in {"python", "py"}:
                        self._pending = buf[idx + len(suffix):]
                        self._enter_code()
                    else:
                        self._emit_thought(buf[:idx + len(suffix)])
                        self._pending = buf[idx + len(suffix):]
                        self._state = "thought"
                    return True
            # No newline yet — could still be typing.  But if we've
            # held too much (e.g. ``` followed by a very long non-newline
            # token), give up and emit one char.
            if len(buf) > self._MAX_HOLD:
                self._emit_thought(buf[0])
                self._pending = buf[1:]
                self._state = "thought"
                return True
            return False  # wait for more chunks
        # Doesn't start with ``` — emit one char and re-scan.
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
    """Result of executing Python through the subprocess executor."""
    output: Any = None
    logs: str = ""
    error: Optional[str] = None


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
    is_text_reply: bool = False
    text_reply: str = ""
    duration_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────
# The Agent Loop
# ─────────────────────────────────────────────────────────────────────

@dataclass
class AgentLoop:
    """Custom agent loop implementing function-call tool use.

    The LLM decides at each step whether to call a structured tool or reply
    with text. Python is executed only through the ``execute_code`` tool;
    Markdown code fences in assistant text are never executed.

    Termination uses a layered "nudge" strategy:
    - Tool calling → loop continues until LLM stops calling tools
    - Text reply at code_steps==0 -> immediate exit (conversation)
    - Text reply at code_steps>0 -> nudge once if it does not look complete
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
        The full system prompt (with tool signatures baked in).
    max_steps:
        Hard cap on reasoning steps. After this many code executions,
        the loop stops and returns a best-effort summary.
    step_callback:
        Optional callback invoked after each step with an AgentStep.
    context:
        Optional pre-existing ContextManager (for multi-turn conversations).
    """

    llm_call: Callable[..., Any]  # Returns LLMResponse (or str for legacy)
    executor_call: Callable[[str], CodeExecResult]
    system_prompt: str
    max_steps: int = DEFAULT_MAX_ITERATIONS
    step_callback: Optional[Callable[[AgentStep], None]] = None
    progress_callback: Optional[Callable[[str, str], None]] = None
    # Tool calling support
    tool_runtime: Optional[ToolRuntime] = None
    tool_schemas: Optional[list[dict]] = None
    on_tool_start: Optional[Callable[[str, dict, str], None]] = None
    on_tool_result: Optional[Callable[..., None]] = None
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
    exclude_workflow_context: bool = True
    # Set by external code (e.g. cancel handler) to signal the loop to
    # stop at the next safe point. Checked at the top of each iteration.
    _interrupted: bool = field(default=False, init=False, repr=False)
    _nudged_this_turn: bool = field(default=False, init=False, repr=False)

    def interrupt(self) -> None:
        """Signal the loop to stop at the next safe point."""
        logger.debug("[AGENT] interrupt() called from thread=%d, setting _interrupted=True", threading.get_ident())
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
            logger.debug("[AGENT] iteration=%d, code_steps=%d, _interrupted=%s, thread=%d",
                        iteration, code_steps, self._interrupted, threading.get_ident())
            if self._interrupted:
                logger.debug("[AGENT] EXITING due to _interrupted=True at iteration top")
                logger.info("Agent loop interrupted externally after %d code steps.", code_steps)
                return "(Task interrupted by user.)"
            # 0. Compression check BEFORE building messages. Doing it here
            #    (rather than only after a code step) means every LLM call
            #    is covered — including pure-text replies, nudges, and the
            #    final summary turn — so the context can never silently
            #    overflow between code steps.
            should_compress, reason = self.context.should_compress()
            if should_compress:
                logger.info("Compression triggered (pre-call): %s", reason)
                self.context.compress(self.llm_call)

            # 1. Build messages with context compression.
            messages = self.context.build_messages(
                self.system_prompt,
                user_instructions=self.user_instructions,
                exclude_workflow_context=self.exclude_workflow_context,
            )

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
            # context and final text handling.
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
            streamed_tool_code: dict[int, dict[str, Any]] = {}

            def _on_tool_delta(tool_index: int, tool_name: str, payload: dict[str, Any]) -> None:
                if tool_name != "execute_code":
                    return
                code = payload.get("code")
                if not isinstance(code, str):
                    return
                state = streamed_tool_code.setdefault(
                    tool_index,
                    {"step": tentative_step + tool_index, "length": 0, "open": False, "invalid": False},
                )
                if state.get("invalid"):
                    return
                if validate_execute_code_payload(code):
                    state["invalid"] = True
                    return
                if not state["open"]:
                    code_started["v"] = True
                    _close_reasoning_if_open()
                    _bump_round()
                    if self.on_code_start:
                        try:
                            self.on_code_start(int(state["step"]))
                        except Exception:
                            logger.exception("on_code_start failed")
                    state["open"] = True
                previous_length = int(state["length"])
                if len(code) > previous_length and self.on_code_delta:
                    try:
                        self.on_code_delta(int(state["step"]), code[previous_length:])
                    except Exception:
                        logger.exception("on_code_delta failed")
                    state["length"] = len(code)

            def _on_llm_delta(piece: str) -> None:
                parser.feed(piece)

            logger.debug("[AGENT] LLM call START, _interrupted=%s", self._interrupted)
            llm_response = None
            for _retry_attempt in range(LLM_MAX_RETRIES + 1):
                try:
                    llm_response = self.llm_call(
                        messages,
                        on_delta=_on_llm_delta,
                        on_tool_delta=_on_tool_delta,
                        tools=self.tool_schemas,
                    )
                    parser.finish()
                    break
                except TypeError as te:
                    # Only fall back to non-streaming if the TypeError is
                    # about the on_delta parameter (older shim that doesn't
                    # accept it).  Re-raise any other TypeError so real bugs
                    # aren't silently swallowed.
                    if "on_delta" not in str(te) and "on_tool_delta" not in str(te) and "keyword" not in str(te).lower() and "unexpected" not in str(te).lower():
                        raise
                    llm_response = self.llm_call(messages, tools=self.tool_schemas)
                    # Extract content for UI callback
                    _content = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
                    if _content and self.on_thought_delta:
                        try:
                            self.on_thought_delta(_content)
                        except Exception as e:
                            logger.warning("on_thought_delta failed: %s", e)
                    break
                except LLM_RETRYABLE_EXCEPTIONS as e:
                    if self._interrupted:
                        logger.debug("[AGENT] Interrupted during retry, not retrying.")
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

            # Extract content and tool_calls from response
            if hasattr(llm_response, 'content'):
                # LLMResponse object (tool-calling path)
                response_text = llm_response.content or ""
                tool_calls = llm_response.tool_calls
            else:
                # Legacy string response (backward compat)
                response_text = str(llm_response) if llm_response else ""
                tool_calls = None

            logger.debug("[AGENT] LLM call END, duration=%.0fms, _interrupted=%s, content_len=%d, tool_calls=%d",
                        duration_ms, self._interrupted, len(response_text), len(tool_calls) if tool_calls else 0)

            for state in streamed_tool_code.values():
                if state.get("open") and self.on_code_end:
                    try:
                        self.on_code_end(int(state["step"]))
                    except Exception:
                        logger.exception("on_code_end failed")

            # ── Handle tool_calls (primary path) ──
            if tool_calls:
                _close_reasoning_if_open()

                # Store assistant message with tool_calls in context
                self.context.add_assistant_with_tool_calls(response_text, tool_calls)

                # Execute each tool call
                for tool_index, tc in enumerate(tool_calls):
                    tc_id = tc.get("id", "")
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    raw_args = func.get("arguments", "{}")

                    arguments = parse_tool_arguments(raw_args)
                    try:
                        import json as _json
                        _args_preview = _json.dumps(arguments, ensure_ascii=False)[:200]
                    except Exception:
                        _args_preview = repr(arguments)[:200]
                    logger.info("TOOL CALL: %s(%s)", tool_name, _args_preview)

                    # Notify progress
                    if self.progress_callback:
                        try:
                            self.progress_callback("tool_call", f"Calling {tool_name}...")
                        except Exception:
                            pass

                    if self.on_tool_start:
                        try:
                            self.on_tool_start(tool_name, arguments, tc_id)
                        except Exception:
                            logger.exception("on_tool_start failed")

                    if self.tool_runtime is None:
                        result_content = '{"success": false, "error": "Tool runtime not configured"}'
                        result_error = "Tool runtime not configured"
                        result_ms = 0.0
                        result_metadata = None
                    else:
                        tool_result = self.tool_runtime.execute(tool_name, arguments)
                        result_content = tool_result.content
                        result_error = tool_result.error
                        result_ms = tool_result.duration_ms
                        result_metadata = dict(tool_result.metadata or {})
                        if tool_name == "execute_code":
                            stream_state = streamed_tool_code.get(tool_index)
                            if stream_state is not None:
                                result_metadata["code_step"] = int(stream_state["step"])

                    if self.on_tool_result:
                        try:
                            updated_metadata = self.on_tool_result(
                                tool_name,
                                result_content,
                                result_error,
                                result_ms,
                                tc_id,
                                result_metadata,
                            )
                            if isinstance(updated_metadata, dict):
                                result_metadata.update(updated_metadata)
                        except Exception:
                            logger.exception("on_tool_result failed")

                    if tool_name == "execute_code" and result_metadata:
                        script_path = result_metadata.get("script_path") or result_metadata.get("script_abs_path")
                        if script_path:
                            persisted_note = (
                                "\n\n[script] Persisted script path: "
                                f"{script_path}\n"
                                "If this code failed or needs refinement, read this file and patch it "
                                "with edit_file, then call run_script_file(script_path=...) instead "
                                "of creating a near-duplicate script."
                            )
                            result_content = f"{result_content}{persisted_note}"

                    # Store tool result in context
                    self.context.add_tool_result(
                        tc_id,
                        tool_name,
                        result_content,
                        meta=result_metadata,
                    )
                    self.context.prune_tool_results()

                code_steps += max(1, len(tool_calls))
                # Keep nudge state scoped to the whole user turn. Resetting it
                # after every tool call lets a short completed task be pushed
                # into unrelated follow-up work repeatedly.
                continue  # Let LLM see the tool results

            # ── No tool_calls: handle as text response ──
            #
            # Function-call architecture rule: plain text is never executed.
            # Custom Python must arrive through the execute_code tool. This
            # removes the unsafe path where a Markdown code fence in
            # the assistant reply could accidentally run as Python.
            response = response_text
            thought = response

            logger.info("LLM text response: %d chars", len(response) if response else 0)
            if response:
                logger.debug("[AGENT] LLM response preview: %.300s", response)

            if code_steps > 0 and self._looks_like_completion(response):
                logger.info(
                    "Text completion after %d tool/code steps -- accepting without nudge.",
                    code_steps,
                )
            elif code_steps > 0 and not getattr(self, '_nudged_this_turn', False):
                self._nudged_this_turn = True
                logger.info(
                    "Text reply mid-task (code_steps=%d) -- nudging LLM to continue.",
                    code_steps,
                )
                _close_reasoning_if_open()
                self.context.add_assistant_message(response)
                self.context.add_user_message(
                    "[System] You are mid-task. Call a function tool to continue "
                    "(use execute_code for Python), or reply with a concise summary to finish.\n"
                    "[系统] 任务尚未完成。请调用 function tool 继续（Python 使用 execute_code），"
                    "或回复简短总结结束任务。不要输出待执行的 Markdown 代码块。"
                )
                continue

            self._nudged_this_turn = False
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
                "Text reply after %d tool/code steps -- treating as task complete.",
                code_steps,
            )
            return response

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
        messages = self.context.build_messages(
            self.system_prompt,
            exclude_workflow_context=self.exclude_workflow_context,
        )
        try:
            response = self.llm_call(messages)
            if hasattr(response, "content"):
                response_text = response.content or ""
            else:
                response_text = str(response) if response is not None else ""
            self.context.add_assistant_message(response_text)
            return response_text
        except Exception as e:
            logger.error("Summary generation failed: %s", e)
            return (
                f"Reached maximum steps ({steps_taken}). "
                "Unable to generate summary due to an error."
            )


    def _looks_like_completion(self, text: str) -> bool:
        """Conservative completion detector for post-tool text replies."""
        if not text or not text.strip():
            return False
        lowered = text.lower()
        continuation_markers = (
            "next i will",
            "i will now",
            "下一步",
            "接下来",
            "继续",
            "还需要",
            "尚未",
            "not complete",
            "need to",
        )
        if any(marker in lowered for marker in continuation_markers):
            return False
        completion_markers = (
            "done",
            "completed",
            "finished",
            "successfully",
            "all set",
            "已完成",
            "已经完成",
            "处理完成",
            "执行完成",
            "已按要求完成",
        )
        return any(marker in lowered for marker in completion_markers)


__all__ = [
    "AgentLoop",
    "AgentStep",
    "CodeExecResult",
    "StreamingParser",
]
