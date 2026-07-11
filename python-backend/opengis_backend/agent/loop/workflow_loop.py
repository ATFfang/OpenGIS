"""
WorkflowLoop — DAG-driven function-call agent loop for workflow execution.

Unlike the free-form AgentLoop, the WorkflowLoop forces the LLM to follow a
predefined DAG of steps. Each node becomes a constrained LLM call where the
model must use function tools, and Python must go through ``execute_code``.

Key differences from AgentLoop:
- Execution order is determined by topological sort of the DAG
- Each step has a focused prompt derived from the node's description
- Predecessor outputs are injected as context for downstream nodes
- Per-node retry logic with error feedback
- Hook validation (optional) after each step

v3.2 (2026-04): Initial implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.loop.loop_kernel import LoopKernel, LoopKernelHooks, LoopTurnRequest
from opengis_backend.agent.loop.retry_policy import (
    LLM_BASE_DELAY,
    LLM_MAX_RETRIES,
    LLM_RETRYABLE_EXCEPTIONS,
)
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer, is_tool_visibility_miss
from opengis_backend.agent.execution.tool_runtime import ToolRuntime
from opengis_backend.agent.loop.turn_runner import (
    decide_text_continuation,
)
from opengis_backend.agent.loop.types import AgentStep, CodeExecResult
from opengis_backend.agent.workflow.workflow_outputs import (
    build_workflow_plan_payload,
    summarize_step_output,
    write_step_output,
)
from opengis_backend.agent.workflow.workflow_model import (
    WorkflowDocument,
    WorkflowNode,
    build_step_prompt,
    get_predecessors,
    topological_sort,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkflowLoop:
    """DAG-driven function-call agent loop for workflow execution.

    The LLM is guided step-by-step through a predefined workflow DAG.
    At each node, the LLM receives a focused prompt and must call
    structured tools to accomplish that specific task.

    Parameters
    ----------
    llm_call:
        Callable that takes a list of chat messages and returns the
        LLM's response as a string.
    executor_call:
        Callable that takes a code string and returns a CodeExecResult.
    system_prompt:
        The base system prompt (with tool signatures).
    workflow:
        The parsed WorkflowDocument to execute.
    max_retries_per_node:
        Default max retries per node (overridden by node.max_retries).
    step_callback:
        Optional callback invoked after each step with an AgentStep.
    context:
        Optional pre-existing ContextManager.
    """

    llm_call: Callable[..., Any]
    executor_call: Callable[[str], CodeExecResult]
    system_prompt: str
    workflow: WorkflowDocument
    max_retries_per_node: int = 3
    step_callback: Optional[Callable[[AgentStep], None]] = None
    progress_callback: Optional[Callable[[str, str], None]] = None
    # Commit hooks. Provider text is committed only after a turn settles;
    # execute_code input can still stream through code callbacks.
    on_thought_delta: Optional[Callable[[str], None]] = None
    on_code_start: Optional[Callable[[int], None]] = None
    on_code_delta: Optional[Callable[[int, str], None]] = None
    on_code_end: Optional[Callable[[int], None]] = None
    on_tool_start: Optional[Callable[[str, dict, str], None]] = None
    on_tool_result: Optional[Callable[..., None]] = None
    # Plan callback — emits plan_update events for compact workflow UI.
    plan_callback: Optional[Callable[[dict], None]] = None
    context: ContextManager = field(default_factory=ContextManager)
    tool_runtime: Optional[ToolRuntime] = None
    tool_schemas: Optional[list[dict]] = None
    tool_materializer: Optional[ToolMaterializer] = None
    # Workspace path for writing intermediate files.
    workspace: str = ""
    # When True, the workflow stops immediately if any node fails
    # (instead of continuing with a failure string as predecessor output).
    halt_on_failure: bool = False
    # Set by external code (e.g. cancel handler) to signal the loop to
    # stop at the next safe point.
    _interrupted: bool = field(default=False, init=False, repr=False)

    def interrupt(self) -> None:
        """Signal the loop to stop at the next safe point."""
        self._interrupted = True

    # ── Public API ─────────────────────────────────────────────────

    def run(self, user_message: str) -> str:
        """Run the workflow loop synchronously. Returns the final summary.

        Executes each node in topological order, feeding predecessor
        outputs into downstream nodes. Blocks until all nodes complete
        or an unrecoverable error occurs.
        """
        # Parse and sort the DAG.
        try:
            execution_order = topological_sort(
                self.workflow.nodes, self.workflow.edges
            )
        except ValueError as e:
            error_msg = f"Workflow DAG error: {e}"
            logger.error(error_msg)
            return error_msg

        total_steps = len(execution_order)
        node_outputs: dict[str, str] = {}
        completed_nodes: list[str] = []

        # Add initial context about the workflow.
        workflow_intro = (
            f"I'm executing the workflow **{self.workflow.name}**.\n"
            f"Description: {self.workflow.description}\n"
            f"Total steps: {total_steps}\n"
            f"User request: {user_message}"
        )
        self.context.add_system_message(
            workflow_intro,
            meta={"kind": "workflow_intro", "scope": "workflow"},
        )

        # Emit initial plan (all steps pending)
        self._emit_plan(execution_order, node_outputs)

        for step_index, node in enumerate(execution_order, 1):
            # Check for external interruption.
            if self._interrupted:
                logger.info("Workflow loop interrupted externally at step %d.", step_index)
                return "(Workflow interrupted by user.)"

            logger.info(
                "Workflow step %d/%d: %s (node=%s)",
                step_index, total_steps, node.title, node.id,
            )

            # Collect predecessor outputs.
            predecessors = get_predecessors(node.id, self.workflow.edges)
            pred_outputs = {
                pid: node_outputs.get(pid, "(no output)")
                for pid in predecessors
                if pid in node_outputs
            }

            # Build the step prompt.
            step_prompt = build_step_prompt(
                node=node,
                step_index=step_index,
                total_steps=total_steps,
                user_intent=user_message,
                predecessor_outputs=pred_outputs,
                workflow_name=self.workflow.name,
            )

            # Execute this node (with retries).
            node_result = self._execute_node(
                node=node,
                step_index=step_index,
                step_prompt=step_prompt,
            )

            node_outputs[node.id] = node_result
            completed_nodes.append(node.id)

            # Update plan after each step
            self._emit_plan(execution_order, node_outputs)

            # Halt on failure: if the node failed and halt_on_failure is set,
            # stop the workflow immediately instead of continuing with bad data.
            if self.halt_on_failure and node_result.startswith("(Step '"):
                logger.warning(
                    "Workflow halted: node '%s' failed and halt_on_failure=True",
                    node.title,
                )
                return (
                    f"Workflow '{self.workflow.name}' halted at step {step_index}/{total_steps} "
                    f"('{node.title}') due to failure.\n\n"
                    f"{node_result}\n\n"
                    "Steps completed before failure:\n"
                    + "\n".join(
                        f"  - {nid}: {node_outputs.get(nid, '(no output)')[:200]}"
                        for nid in completed_nodes[:-1]
                    )
                )

        # Generate final summary.
        return self._generate_workflow_summary(
            user_message=user_message,
            node_outputs=node_outputs,
            execution_order=execution_order,
        )

    # ── Internal ───────────────────────────────────────────────────

    def _execute_node(
        self,
        node: WorkflowNode,
        step_index: int,
        step_prompt: str,
    ) -> str:
        """Execute a single workflow node with a mini function-call loop.

        The node may call multiple tools, including multiple ``execute_code``
        calls. Markdown code fences are never executed. A plain-text response
        is treated as the node completion summary once the model has either
        done tool work, explicitly indicates completion, or has received one
        nudge explaining the function-call contract.
        """
        max_iterations = (node.max_retries or self.max_retries_per_node) * 3
        # Allow more iterations than retries: retries are for errors,
        # but a node may need multiple successful tool calls.

        # Add the step prompt as a user message.
        self.context.add_user_message(
            step_prompt,
            meta={"kind": "workflow_step_prompt", "scope": "workflow"},
        )
        if self.tool_materializer is None and self.tool_schemas:
            self.tool_materializer = ToolMaterializer(self.tool_schemas)
        kernel = LoopKernel(
            llm_call=self.llm_call,
            context=self.context,
            tool_runtime=self.tool_runtime,
            tool_schemas=self.tool_schemas,
            tool_materializer=self.tool_materializer,
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
                on_tool_start=self.on_tool_start,
                on_tool_result=self.on_tool_result,
            ),
        )

        error_count = 0
        max_errors = node.max_retries or self.max_retries_per_node
        accumulated_output: list[str] = []
        nudged = False  # Track whether we've nudged the LLM to call tools.
        tool_steps = 0
        force_all_tools_once = False
        tool_visibility_retried = False

        for iteration in range(max_iterations):
            # Check for external interruption.
            if self._interrupted:
                logger.info("Workflow node %s interrupted externally.", node.id)
                return "(Node interrupted by user.)"

            outcome = kernel.run_turn(
                LoopTurnRequest(
                    iteration=iteration,
                    code_steps=0,
                    tool_steps=tool_steps,
                    system_prompt=self.system_prompt,
                    progress_stage="calling_llm",
                    progress_detail=f"Step {step_index}: {node.title} — thinking...",
                    text_code_step=step_index,
                    code_step_for_tool=lambda tool_index: step_index * 100 + tool_index + 1,
                    retry_detail="Connection interrupted",
                    logger_prefix=f"WF:{node.id}",
                    scope="workflow",
                    assistant_tool_scope_kind="workflow_tool_calls",
                    tool_result_scope_kind="workflow_tool_result",
                    tool_progress_label=f"Step {step_index}: {node.title}",
                    force_all_tools=force_all_tools_once,
                )
            )
            force_all_tools_once = False
            provider_result = outcome.provider_result
            duration_ms = provider_result.duration_ms
            response_text = provider_result.response_text
            tool_calls = provider_result.tool_calls

            if tool_calls:
                for settlement in outcome.settlements:
                    accumulated_output.append(f"[{settlement.name}] {settlement.content}")
                    tool_steps += 1

                    if settlement.error:
                        error_count += 1
                        if error_count >= max_errors:
                            logger.warning(
                                "Node %s exhausted tool error budget (%d errors).",
                                node.id, error_count,
                            )
                            full_output = "\n".join(accumulated_output)
                            error_msg = f"(Step '{node.title}' failed after {error_count} tool errors)"
                            return (
                                self._finalize_step(node, step_index, full_output + "\n" + error_msg)
                                if full_output
                                else error_msg
                            )
                        self.context.add_user_message(
                            (
                                f"[System] The tool `{settlement.name}` failed with error:\n"
                                f"```\n{settlement.error}\n```\n"
                                "Fix the arguments or patch/rerun the persisted script if this was "
                                "`execute_code`. Continue with function tools only; do not output "
                                "Markdown code blocks as executable work.\n"
                                f"(Error {error_count}/{max_errors})"
                            ),
                            meta={"kind": "workflow_tool_error_feedback", "scope": "workflow"},
                        )

                nudged = False
                continue

            # Function-call architecture rule: plain text is never executed.
            # Python must arrive through the execute_code tool.
            thought = response_text
            if (
                not tool_visibility_retried
                and outcome.materialization is not None
                and outcome.materialization.reason != "all"
                and is_tool_visibility_miss(response_text)
            ):
                logger.info(
                    "Workflow node %s appears to hit tool visibility confusion; retrying once with all tools.",
                    node.id,
                )
                tool_visibility_retried = True
                force_all_tools_once = True
                self.context.add_assistant_message(
                    response_text,
                    meta={"kind": "workflow_node_response", "scope": "workflow"},
                )
                self.context.add_user_message(
                    "[System] The previous reply may have confused dynamic tool visibility "
                    "with platform capability. Retry this workflow step once with the full "
                    "function tool set. If the needed tool exists, call it. If it truly "
                    "does not exist, state the exact missing capability.",
                    meta={"kind": "workflow_tool_visibility_retry", "scope": "workflow"},
                )
                continue

            decision = decide_text_continuation(
                response_text,
                code_steps=0,
                tool_steps=tool_steps,
                nudged=nudged,
                accept_after_any_tool=True,
                accept_text_without_tools=node.node_type in {"output", "decision"},
            )
            if decision.should_nudge:
                nudged = True
                logger.info(
                    "Text-only reply before node %s completed (iteration %d, reason=%s) — nudging to call tools.",
                    node.id,
                    iteration,
                    decision.reason,
                )
                self.context.add_assistant_message(
                    response_text,
                    meta={"kind": "workflow_node_response", "scope": "workflow"},
                )
                self.context.add_user_message(
                    "[System] This workflow step is not complete yet. Call an appropriate "
                    "function tool to do the work; use `execute_code` for Python. "
                    "Do not output Markdown code blocks as executable work. If no tool is "
                    "needed, reply with a concise completion summary that satisfies the "
                    "downstream handoff contract.\n"
                    "[系统] 此 workflow 步骤尚未完成。请调用 function tool 完成任务；"
                    "Python 使用 `execute_code`。不要输出待执行的 Markdown 代码块。"
                    "如果确实不需要工具，请回复满足下游交付契约的简短完成摘要。",
                    meta={"kind": "workflow_nudge", "scope": "workflow"},
                )
                continue

            self.context.add_assistant_message(
                response_text,
                meta={"kind": "workflow_node_response", "scope": "workflow"},
            )
            step = AgentStep(
                step_num=step_index,
                thought=thought,
                is_text_reply=True,
                text_reply=response_text,
                duration_ms=duration_ms,
            )
            if self.step_callback:
                try:
                    self.step_callback(step)
                except Exception:
                    logger.exception("step_callback failed")
            accumulated_output.append(response_text)
            return self._finalize_step(
                node, step_index, "\n".join(accumulated_output),
            )

        # Iteration limit reached for this node.
        logger.warning(
            "Node %s reached iteration limit (%d).", node.id, max_iterations,
        )
        full_output = "\n".join(accumulated_output) if accumulated_output else ""
        return self._finalize_step(node, step_index, full_output) if full_output else f"(Step '{node.title}' reached iteration limit)"

    def _finalize_step(
        self,
        node: WorkflowNode,
        step_index: int,
        full_output: str,
    ) -> str:
        """Write full output to file and return a compact summary.

        This is the single exit point for _execute_node. It ensures
        every step's output is persisted to disk and a short summary
        is returned to the caller (for predecessor_outputs).
        """
        file_path = None
        if self.workspace:
            file_path = write_step_output(
                node=node,
                step_index=step_index,
                full_output=full_output,
                workspace=self.workspace,
            )

        summary = summarize_step_output(
            node=node,
            step_index=step_index,
            full_output=full_output,
            file_path=file_path,
        )

        # Proactive compression after each step — prevents context from
        # growing unbounded across 6+ step workflows.
        try:
            should, reason = self.context.should_compress()
            if should:
                logger.info("Workflow step %d compression triggered: %s", step_index, reason)
                self.context.compress(self.llm_call)
        except Exception:
            logger.warning("Post-step compression failed (non-fatal)", exc_info=True)

        logger.info(
            "Step %d (%s) finalized: %d chars output → %d char summary",
            step_index, node.id, len(full_output), len(summary),
        )
        return summary

    def _emit_plan(
        self,
        execution_order: list[WorkflowNode],
        node_outputs: dict[str, str],
    ) -> None:
        """Emit a plan_update event showing current workflow progress."""
        if not self.plan_callback:
            return

        try:
            self.plan_callback(build_workflow_plan_payload(
                plan_id=f"workflow-{id(self)}",
                title=self.workflow.name,
                execution_order=execution_order,
                node_outputs=node_outputs,
            ))
        except Exception:
            logger.debug("plan_callback failed (non-fatal)", exc_info=True)

    def _generate_workflow_summary(
        self,
        user_message: str,
        node_outputs: dict[str, str],
        execution_order: list[WorkflowNode],
    ) -> str:
        """Generate a final summary after all workflow steps complete."""
        summary_prompt = (
            "All workflow steps have been completed. Please provide a brief "
            "summary of what was accomplished:\n\n"
        )
        for i, node in enumerate(execution_order, 1):
            output = node_outputs.get(node.id, "(no output)")
            # Truncate for summary context
            if len(output) > 500:
                output = output[:500] + "..."
            summary_prompt += f"**Step {i} — {node.title}**: {output}\n\n"

        summary_prompt += (
            "\nSummarize the overall results in a user-friendly way. "
            "ONLY mention files that were explicitly confirmed as saved in "
            "the step outputs above. Do NOT invent or assume files exist "
            "if they were not confirmed in the output. "
            "Do NOT write any code."
        )

        self.context.add_user_message(
            summary_prompt,
            meta={"kind": "workflow_summary_prompt", "scope": "workflow"},
        )
        try:
            summary_kernel = LoopKernel(
                llm_call=self.llm_call,
                context=self.context,
                tool_runtime=None,
                tool_schemas=[],
                retryable_exceptions=LLM_RETRYABLE_EXCEPTIONS,
                max_retries=LLM_MAX_RETRIES,
                base_delay=LLM_BASE_DELAY,
                progress_callback=self.progress_callback,
                interrupted=lambda: self._interrupted,
                hooks=LoopKernelHooks(on_thought_delta=self.on_thought_delta),
            )
            outcome = summary_kernel.run_turn(
                LoopTurnRequest(
                    iteration=0,
                    code_steps=0,
                    tool_steps=0,
                    system_prompt=self.system_prompt,
                    progress_stage="generating_summary",
                    progress_detail="All steps complete — generating summary...",
                    text_code_step=len(execution_order) + 1,
                    code_step_for_tool=lambda tool_index: len(execution_order) + tool_index + 1,
                    retry_detail="Connection interrupted",
                    logger_prefix="WF:summary",
                    compress_context=True,
                )
            )
            response_text = outcome.response_text or (
                f"Workflow '{self.workflow.name}' completed all {len(execution_order)} steps. "
                "Summary generation returned no response."
            )
            self.context.add_assistant_message(
                response_text,
                meta={"kind": "workflow_summary", "scope": "workflow"},
            )
            # Emit as a text reply step.
            step = AgentStep(
                step_num=len(execution_order) + 1,
                thought=response_text,
                is_text_reply=True,
                text_reply=response_text,
                duration_ms=0,
            )
            if self.step_callback:
                try:
                    self.step_callback(step)
                except Exception:
                    logger.exception("step_callback failed")
            return response_text
        except Exception as e:
            logger.error("Workflow summary generation failed: %s", e)
            return (
                f"Workflow '{self.workflow.name}' completed all {len(execution_order)} steps. "
                "Unable to generate summary due to an error."
            )

__all__ = [
    "WorkflowLoop",
]
