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
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.loop.loop_kernel import LoopKernel, LoopKernelHooks, LoopTurnRequest
from opengis_backend.agent.loop.retry_policy import (
    LLM_BASE_DELAY,
    LLM_MAX_RETRIES,
    LLM_RETRYABLE_EXCEPTIONS,
)
from opengis_backend.agent.loop.streaming import StreamingParser
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer, is_tool_visibility_miss
from opengis_backend.agent.execution.tool_runtime import ToolRuntime
from opengis_backend.agent.loop.turn_runner import (
    decide_text_continuation,
)
from opengis_backend.agent.loop.types import AgentStep, CodeExecResult
from opengis_backend.runtime.constants import (  # noqa: E402 module-level import
    DEFAULT_MAX_ITERATIONS,
    AGENT_LOOP_SAFETY_MULTIPLIER,
)

logger = logging.getLogger(__name__)


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
        Callable that takes provider messages and returns ``LLMResponse``.
        Provider routing is handled upstream.
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

    llm_call: Callable[..., Any]
    executor_call: Callable[[str], CodeExecResult]
    system_prompt: str
    max_steps: int = DEFAULT_MAX_ITERATIONS
    step_callback: Optional[Callable[[AgentStep], None]] = None
    progress_callback: Optional[Callable[[str, str], None]] = None
    # Tool calling support
    tool_runtime: Optional[ToolRuntime] = None
    tool_schemas: Optional[list[dict]] = None
    tool_materializer: Optional[ToolMaterializer] = None
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
        if self.tool_materializer is None and self.tool_schemas:
            self.tool_materializer = ToolMaterializer(self.tool_schemas)

        code_steps = 0  # Only count code execution steps toward the limit.
        tool_steps = 0  # Count non-code tool settlements for telemetry/guardrails.
        reasoning_round_seq = 0  # Reserved for provider-native reasoning summaries.
        self._nudged_this_turn = False  # Reset nudge state for this run.
        force_all_tools_once = False
        kernel = LoopKernel(
            llm_call=self.llm_call,
            context=self.context,
            tool_runtime=self.tool_runtime,
            tool_schemas=self.tool_schemas,
            tool_materializer=self.tool_materializer,
            streaming_parser_factory=StreamingParser,
            retryable_exceptions=LLM_RETRYABLE_EXCEPTIONS,
            max_retries=LLM_MAX_RETRIES,
            base_delay=LLM_BASE_DELAY,
            progress_callback=self.progress_callback,
            interrupted=lambda: self._interrupted,
            hooks=LoopKernelHooks(
                on_thought_delta=self.on_thought_delta,
                on_code_start=self.on_code_start,
                on_code_delta=self.on_code_delta,
                on_code_end=self.on_code_end,
                on_reasoning_start=self.on_reasoning_start,
                on_reasoning_end=self.on_reasoning_end,
                on_tool_start=self.on_tool_start,
                on_tool_result=self.on_tool_result,
            ),
        )

        for iteration in range(self.max_steps * AGENT_LOOP_SAFETY_MULTIPLIER):  # Safety cap on total iterations
            # Check for external interruption.
            logger.debug("[AGENT] iteration=%d, code_steps=%d, tool_steps=%d, _interrupted=%s, thread=%d",
                        iteration, code_steps, tool_steps, self._interrupted, threading.get_ident())
            if self._interrupted:
                logger.debug("[AGENT] EXITING due to _interrupted=True at iteration top")
                logger.info(
                    "Agent loop interrupted externally after %d code steps and %d tool steps.",
                    code_steps,
                    tool_steps,
                )
                return "(Task interrupted by user.)"
            tentative_step = code_steps + 1
            reasoning_round_seq += 1

            logger.debug("[AGENT] LLM call START, _interrupted=%s", self._interrupted)
            stage = "calling_llm" if code_steps == 0 else "thinking_next_step"
            detail = (
                f"Calling LLM (step {code_steps + 1})..."
                if code_steps > 0
                else "Calling LLM..."
            )
            outcome = kernel.run_turn(
                LoopTurnRequest(
                    iteration=iteration,
                    code_steps=code_steps,
                    tool_steps=tool_steps,
                    system_prompt=self.system_prompt,
                    user_instructions=self.user_instructions,
                    exclude_workflow_context=self.exclude_workflow_context,
                    progress_stage=stage,
                    progress_detail=detail,
                    text_code_step=tentative_step,
                    code_step_for_tool=lambda tool_index: tentative_step + tool_index,
                    reasoning_round_seq=reasoning_round_seq,
                    enable_reasoning_lifecycle=False,
                    retry_detail="Connection error",
                    logger_prefix="LOOP",
                    force_all_tools=force_all_tools_once,
                )
            )
            force_all_tools_once = False
            provider_result = outcome.provider_result
            updated_round = outcome.updated_reasoning_round
            telemetry = outcome.telemetry
            if updated_round is not None:
                reasoning_round_seq = int(updated_round)
            duration_ms = provider_result.duration_ms
            response_text = provider_result.response_text
            tool_calls = provider_result.tool_calls

            logger.debug("[AGENT] LLM call END, duration=%.0fms, _interrupted=%s, content_len=%d, tool_calls=%d",
                        duration_ms, self._interrupted, len(response_text), len(tool_calls) if tool_calls else 0)

            # ── Handle tool_calls (primary path) ──
            if tool_calls:
                code_step_delta = sum(1 for item in outcome.settlements if item.counts_as_code_step)
                tool_step_delta = sum(1 for item in outcome.settlements if not item.counts_as_code_step)
                code_steps += code_step_delta
                tool_steps += tool_step_delta
                telemetry.code_steps = code_steps
                telemetry.tool_steps = tool_steps
                telemetry.continuation = "tool_results"
                telemetry.log()
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

            if (
                outcome.materialization is not None
                and outcome.materialization.reason != "all"
                and is_tool_visibility_miss(response)
            ):
                logger.info(
                    "Text reply appears to be tool visibility confusion; retrying once with all tools."
                )
                if provider_result.reasoning_open and self.on_reasoning_end:
                    try:
                        self.on_reasoning_end(int(provider_result.current_reasoning_round or 0))
                    except Exception:
                        logger.exception("on_reasoning_end failed before tool visibility retry")
                self.context.add_assistant_message(response)
                self.context.add_user_message(
                    "[System] The previous reply may have confused dynamic tool visibility "
                    "with platform capability. Retry once with the full function tool set. "
                    "If the needed tool exists, call it. If it truly does not exist, answer "
                    "with the exact missing capability."
                )
                telemetry.continuation = "tool_visibility_retry"
                telemetry.log()
                force_all_tools_once = True
                continue

            decision = decide_text_continuation(
                response,
                code_steps=code_steps,
                tool_steps=tool_steps,
                max_work_steps=self.max_steps,
                nudged=getattr(self, '_nudged_this_turn', False),
            )
            total_work_steps = code_steps + tool_steps
            if decision.should_accept:
                logger.info(
                    "Text response accepted after %d tool/code steps (reason=%s).",
                    total_work_steps,
                    decision.reason,
                )
            elif decision.should_nudge and total_work_steps > 0:
                self._nudged_this_turn = True
                logger.info(
                    "Text reply mid-task (code_steps=%d, tool_steps=%d, reason=%s) -- nudging LLM to continue.",
                    code_steps,
                    tool_steps,
                    decision.reason,
                )
                if provider_result.reasoning_open and self.on_reasoning_end:
                    try:
                        self.on_reasoning_end(int(provider_result.current_reasoning_round or 0))
                    except Exception:
                        logger.exception("on_reasoning_end failed before nudge")
                self.context.add_assistant_message(response)
                self.context.add_user_message(
                    "[System] You are mid-task. Call a function tool to continue "
                    "(use execute_code for Python), or reply with a concise summary to finish.\n"
                    "[系统] 任务尚未完成。请调用 function tool 继续（Python 使用 execute_code），"
                    "或回复简短总结结束任务。不要输出待执行的 Markdown 代码块。"
                )
                continue

            self._nudged_this_turn = False
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
                "Text reply after %d code steps and %d tool steps -- treating as task complete.",
                code_steps,
                tool_steps,
            )
            telemetry.code_steps = code_steps
            telemetry.tool_steps = tool_steps
            telemetry.continuation = "complete"
            telemetry.log()
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
            response_text = response.content or ""
            self.context.add_assistant_message(response_text)
            return response_text
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
]
