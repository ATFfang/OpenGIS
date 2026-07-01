"""
WorkflowLoop — DAG-driven agent loop for workflow execution.

Unlike the free-form AgentLoop (Hybrid CodeAct), the WorkflowLoop
forces the LLM to follow a predefined DAG of steps. Each node in the
DAG becomes a constrained LLM call where the model MUST produce code
to accomplish that specific step.

Key differences from AgentLoop:
- Execution order is determined by topological sort of the DAG
- Each step has a focused prompt derived from the node's description
- Predecessor outputs are injected as context for downstream nodes
- Per-node retry logic with error feedback
- Hook validation (optional) after each step

v3.2 (2026-04): Initial implementation.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from opengis_backend.agent.agent_loop import (
    AgentStep, CodeExecResult, StreamingParser,
    LLM_MAX_RETRIES, LLM_BASE_DELAY, LLM_RETRYABLE_EXCEPTIONS,
    extract_code_block, extract_thought,
)
from opengis_backend.agent.context_manager import ContextManager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Workflow document parsing
# ──────────────────────────────────────────────────────────────────────

@dataclass
class WorkflowNode:
    """A single node in the workflow DAG."""
    id: str
    title: str
    description: str = ""
    node_type: str = "process"  # process | input | output | decision
    config: dict = field(default_factory=dict)
    max_retries: int = 3
    # Validation hooks — parsed but NOT yet evaluated. Stored so they
    # round-trip through from_json and are available for future use.
    hooks: list[dict] = field(default_factory=list)


@dataclass
class WorkflowEdge:
    """A directed edge in the workflow DAG."""
    source: str
    target: str
    label: str = ""


@dataclass
class WorkflowDocument:
    """Parsed workflow document from a .flow.json file."""
    name: str
    description: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: str | dict) -> "WorkflowDocument":
        """Parse a workflow document from JSON string or dict."""
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw

        nodes = []
        for n in data.get("nodes", []):
            hooks_raw = n.get("hooks", [])
            hooks = hooks_raw if isinstance(hooks_raw, list) else []

            nodes.append(WorkflowNode(
                id=n.get("id", ""),
                title=n.get("title", n.get("label", "Untitled")),
                description=n.get("description", ""),
                # Accept both frontend (camelCase) and backend (snake_case) names.
                node_type=n.get("nodeType", n.get("type", "process")),
                config=n.get("params", n.get("config", {})),
                max_retries=n.get("maxRetries", n.get("max_retries", 3)),
                hooks=hooks,
            ))

        edges = []
        for e in data.get("edges", []):
            edges.append(WorkflowEdge(
                source=e.get("source", ""),
                target=e.get("target", ""),
                label=e.get("label", ""),
            ))

        return cls(
            name=data.get("name", "Untitled Workflow"),
            description=data.get("description", ""),
            nodes=nodes,
            edges=edges,
            metadata=data.get("metadata", {}),
        )


# ──────────────────────────────────────────────────────────────────────
# DAG utilities
# ──────────────────────────────────────────────────────────────────────

def topological_sort(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[WorkflowNode]:
    """Topological sort of workflow nodes using Kahn's algorithm.

    Returns nodes in execution order. Raises ValueError if the graph
    has a cycle.
    """
    node_map = {n.id: n for n in nodes}
    in_degree: dict[str, int] = {n.id: 0 for n in nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in nodes}

    for edge in edges:
        if edge.source in adjacency and edge.target in in_degree:
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

    # Start with nodes that have no incoming edges.
    queue: deque[str] = deque()
    for nid, deg in in_degree.items():
        if deg == 0:
            queue.append(nid)

    result: list[WorkflowNode] = []
    while queue:
        nid = queue.popleft()
        result.append(node_map[nid])
        for neighbor in adjacency[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(nodes):
        raise ValueError(
            f"Workflow DAG has a cycle! Sorted {len(result)} of {len(nodes)} nodes."
        )

    return result


def get_predecessors(node_id: str, edges: list[WorkflowEdge]) -> list[str]:
    """Get all predecessor node IDs for a given node."""
    return [e.source for e in edges if e.target == node_id]


# ──────────────────────────────────────────────────────────────────────
# Step prompt builder
# ──────────────────────────────────────────────────────────────────────

def build_step_prompt(
    node: WorkflowNode,
    step_index: int,
    total_steps: int,
    user_intent: str,
    predecessor_outputs: dict[str, str],
    workflow_name: str = "",
) -> str:
    """Build a focused prompt for a single workflow step.

    The prompt constrains the LLM to accomplish the specific task
    described by the node, using outputs from predecessor nodes.
    """
    parts: list[str] = []

    parts.append(
        f"## Workflow Step {step_index}/{total_steps}: {node.title}\n"
    )

    if workflow_name:
        parts.append(f"**Workflow**: {workflow_name}\n")

    parts.append(f"**User's original request**: {user_intent}\n")

    if node.description:
        parts.append(f"**Task for this step**: {node.description}\n")

    if predecessor_outputs:
        parts.append("**Results from previous steps**:")
        for pred_id, output in predecessor_outputs.items():
            # Truncate long outputs
            display = output[:2000] + "..." if len(output) > 2000 else output
            parts.append(f"- Step `{pred_id}`: {display}")
        parts.append("")

    parts.append(
        "**Instructions**: Write Python code to accomplish this step. "
        "Use the results from previous steps as needed. "
        "You may write MULTIPLE code blocks for this step — each will be "
        "executed and the output fed back to you. Keep writing code until "
        "the step is FULLY complete (files saved, layers displayed, etc.). "
        "When this step is done, reply with plain text (no code block) to "
        "signal completion and summarize what was accomplished.\n"
    )

    parts.append(
        "**Important**: Save all output files to the workspace directory. "
        "If the step description mentions displaying on the map, call "
        "`add_layer(...)` and `zoom_to_layer(...)`. Do NOT stop after just "
        "loading data — complete the ENTIRE task described above.\n"
    )

    if node.config:
        parts.append(f"**Step configuration**: {json.dumps(node.config, ensure_ascii=False)}\n")

    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────
# The Workflow Loop
# ──────────────────────────────────────────────────────────────────────

@dataclass
class WorkflowLoop:
    """DAG-driven agent loop for workflow execution.

    The LLM is guided step-by-step through a predefined workflow DAG.
    At each node, the LLM receives a focused prompt and must produce
    code to accomplish that specific task.

    Parameters
    ----------
    llm_call:
        Callable that takes a list of chat messages and returns the
        LLM's response as a string.
    executor_call:
        Callable that takes a code string and returns a CodeExecResult.
    system_prompt:
        The base system prompt (with skill signatures).
    workflow:
        The parsed WorkflowDocument to execute.
    max_retries_per_node:
        Default max retries per node (overridden by node.max_retries).
    step_callback:
        Optional callback invoked after each step with an AgentStep.
    context:
        Optional pre-existing ContextManager.
    """

    llm_call: Callable[..., str]
    executor_call: Callable[[str], CodeExecResult]
    system_prompt: str
    workflow: WorkflowDocument
    max_retries_per_node: int = 3
    step_callback: Optional[Callable[[AgentStep], None]] = None
    progress_callback: Optional[Callable[[str, str], None]] = None
    # Streaming hooks — same as AgentLoop. When provided, the LLM call
    # uses stream=True and tokens are forwarded in real-time.
    on_thought_delta: Optional[Callable[[str], None]] = None
    on_code_start: Optional[Callable[[int], None]] = None
    on_code_delta: Optional[Callable[[int, str], None]] = None
    on_code_end: Optional[Callable[[int], None]] = None
    context: ContextManager = field(default_factory=ContextManager)
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
        self.context.add_user_message(workflow_intro)

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
        """Execute a single workflow node with a mini agent loop.

        Unlike the previous version that returned on the first successful
        code execution, this version lets the LLM run **multiple code
        blocks** within a single node — just like the free-form AgentLoop.

        The node is considered complete when:
        - The LLM replies with pure text (no code block) — implicit done.
        - The LLM calls final_answer() in code — explicit done.
        - The iteration limit is reached.

        This is critical because a single workflow node (e.g. "河网提取")
        may require multiple code executions: load data → compute → save
        → display on map.

        Returns the node's accumulated output string.
        """
        max_iterations = (node.max_retries or self.max_retries_per_node) * 3
        # Allow more iterations than retries: retries are for errors,
        # but a node may need multiple successful code blocks.

        # Add the step prompt as a user message.
        self.context.add_user_message(step_prompt)

        error_count = 0
        max_errors = node.max_retries or self.max_retries_per_node
        accumulated_output: list[str] = []
        nudged = False  # Track whether we've nudged the LLM to write code.
        sub_step = 0    # Incremented per code execution within this node.

        for iteration in range(max_iterations):
            # Check for external interruption.
            if self._interrupted:
                logger.info("Workflow node %s interrupted externally.", node.id)
                return "(Node interrupted by user.)"

            # Build messages and call LLM.
            messages = self.context.build_messages(self.system_prompt)

            # Notify the UI that we're calling the LLM.
            if self.progress_callback:
                try:
                    self.progress_callback(
                        "calling_llm",
                        f"Step {step_index}: {node.title} — calling LLM (iteration {iteration + 1})...",
                    )
                except Exception:
                    pass

            t0 = time.monotonic()

            # Build a streaming parser for this LLM call when streaming
            # callbacks are provided. Tokens are forwarded to the UI in
            # real-time, just like in the free-form AgentLoop.
            code_started = {"v": False}

            def _wf_on_thought(text: str) -> None:
                if self.on_thought_delta:
                    try:
                        self.on_thought_delta(text)
                    except Exception:
                        logger.exception("on_thought_delta failed")

            def _wf_on_code_start() -> None:
                code_started["v"] = True
                if self.on_code_start:
                    try:
                        self.on_code_start(step_index)
                    except Exception:
                        logger.exception("on_code_start failed")

            def _wf_on_code_delta(text: str) -> None:
                if self.on_code_delta:
                    try:
                        self.on_code_delta(step_index, text)
                    except Exception:
                        logger.exception("on_code_delta failed")

            def _wf_on_code_end() -> None:
                if self.on_code_end:
                    try:
                        self.on_code_end(step_index)
                    except Exception:
                        logger.exception("on_code_end failed")

            parser = StreamingParser(
                on_thought_delta=_wf_on_thought,
                on_code_start=_wf_on_code_start,
                on_code_delta=_wf_on_code_delta,
                on_code_end=_wf_on_code_end,
            )

            def _on_llm_delta(piece: str) -> None:
                parser.feed(piece)

            response = None
            for _retry_attempt in range(LLM_MAX_RETRIES + 1):
                try:
                    response = self.llm_call(messages, on_delta=_on_llm_delta)
                    parser.finish()
                    break
                except TypeError as te:
                    # Only fall back if the TypeError is about on_delta.
                    if "on_delta" not in str(te) and "keyword" not in str(te).lower() and "unexpected" not in str(te).lower():
                        raise
                    response = self.llm_call(messages)
                    if response and self.on_thought_delta:
                        try:
                            self.on_thought_delta(response)
                        except Exception:
                            logger.exception("on_thought_delta failed on fallback")
                    break
                except LLM_RETRYABLE_EXCEPTIONS as e:
                    if self._interrupted:
                        raise
                    if _retry_attempt >= LLM_MAX_RETRIES:
                        logger.error("LLM call failed after %d retries at node %s: %s",
                                     LLM_MAX_RETRIES, node.id, e)
                        raise
                    import random
                    delay = LLM_BASE_DELAY * (2 ** _retry_attempt) + random.uniform(0, 0.5)
                    logger.warning(
                        "[WF-RETRY] node %s attempt %d/%d failed (%s: %s), retrying in %.1fs...",
                        node.id, _retry_attempt + 1, LLM_MAX_RETRIES, type(e).__name__, e, delay,
                    )
                    time.sleep(delay)
                    # Reset streaming parser for retry
                    parser = StreamingParser(
                        on_thought_delta=_wf_on_thought,
                        on_code_start=_wf_on_code_start,
                        on_code_delta=_wf_on_code_delta,
                        on_code_end=_wf_on_code_end,
                    )
                except Exception as e:
                    logger.error("LLM call failed at node %s: %s", node.id, e)
                    raise
            duration_ms = (time.monotonic() - t0) * 1000

            # Parse response.
            code_block = extract_code_block(response)
            thought = extract_thought(response)

            # If no code block, the LLM considers this node DONE.
            if code_block is None:
                # Nudge: if no code has been executed yet for this node,
                # the LLM might be explaining its plan instead of doing
                # the work. Give it ONE nudge to write actual code.
                if not accumulated_output and not nudged:
                    nudged = True
                    logger.info(
                        "Text-only reply at start of node %s (iteration %d) — nudging to write code.",
                        node.id, iteration,
                    )
                    self.context.add_assistant_message(response)
                    self.context.add_user_message(
                        "[System] This step requires code execution. "
                        "Write a ```python code block to accomplish the task. "
                        "A plain text reply will end this step.\n"
                        "[系统] 此步骤需要执行代码。请写 ```python 代码块来完成任务。"
                        "纯文本回复将结束此步骤。"
                    )
                    continue

                # Genuine completion: either after code execution or after nudge.
                self.context.add_assistant_message(response)
                step = AgentStep(
                    step_num=step_index,
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
                # Return accumulated output + final text.
                accumulated_output.append(response)
                return "\n".join(accumulated_output)

            # Execute the code block.
            sub_step += 1
            self.context.add_assistant_message(response)

            # Notify the UI that code is about to execute.
            if self.progress_callback:
                try:
                    self.progress_callback(
                        "executing_code",
                        f"Step {step_index}: {node.title} — executing code...",
                    )
                except Exception:
                    pass

            t1 = time.monotonic()
            try:
                result = self.executor_call(code_block)
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                result = CodeExecResult(error=error_msg)
            exec_duration_ms = (time.monotonic() - t1) * 1000

            # Build step record. Use a unique step number that accounts
            # for multiple code executions within the same node.
            unique_step = step_index * 100 + sub_step
            output_str = result.logs or str(result.output or "")
            step = AgentStep(
                step_num=unique_step,
                thought=thought,
                code=code_block,
                output=output_str,
                error=result.error,
                is_final_answer=result.is_final_answer,
                duration_ms=duration_ms + exec_duration_ms,
            )

            # Fire step callback.
            if self.step_callback:
                try:
                    self.step_callback(step)
                except Exception:
                    logger.exception("step_callback failed")

            # Check for final_answer().
            if result.is_final_answer:
                accumulated_output.append(str(result.output) if result.output else "(done)")
                return "\n".join(accumulated_output)

            if result.error is None:
                # Success — feed the output back to the LLM so it can
                # continue with the next sub-step of this node.
                self.context.add_code_output(
                    step=unique_step,
                    code=code_block,
                    output=output_str,
                    error=None,
                )
                accumulated_output.append(output_str)
                # Do NOT return here — let the LLM decide if more code
                # is needed for this node.
            else:
                # Error — feed back to LLM for retry.
                error_count += 1
                self.context.add_code_output(
                    step=unique_step,
                    code=code_block,
                    output=result.logs or "",
                    error=result.error,
                )
                if error_count >= max_errors:
                    logger.warning(
                        "Node %s exhausted error budget (%d errors).",
                        node.id, error_count,
                    )
                    return f"(Step '{node.title}' failed after {error_count} errors)"

                error_feedback = (
                    f"The code failed with error:\n"
                    f"```\n{result.error}\n```\n"
                    f"Please fix the code and try again. "
                    f"(Error {error_count}/{max_errors})"
                )
                self.context.add_user_message(error_feedback)

                logger.warning(
                    "Node %s error %d/%d: %s",
                    node.id, error_count, max_errors,
                    result.error[:200],
                )

        # Iteration limit reached for this node.
        logger.warning(
            "Node %s reached iteration limit (%d).", node.id, max_iterations,
        )
        return "\n".join(accumulated_output) if accumulated_output else f"(Step '{node.title}' reached iteration limit)"

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

        self.context.add_user_message(summary_prompt)
        messages = self.context.build_messages(self.system_prompt)

        # Notify the UI that we're generating the summary.
        if self.progress_callback:
            try:
                self.progress_callback(
                    "generating_summary",
                    "All steps complete — generating summary...",
                )
            except Exception:
                pass

        try:
            response = None

            # Build a streaming parser for the summary call if streaming
            # callbacks are available — same pattern as _execute_node.
            summary_parser = StreamingParser(
                on_thought_delta=lambda t: self.on_thought_delta(t) if self.on_thought_delta else None,
                on_code_start=None,
                on_code_delta=None,
                on_code_end=None,
            )

            for _retry_attempt in range(LLM_MAX_RETRIES + 1):
                try:
                    response = self.llm_call(messages, on_delta=lambda p: summary_parser.feed(p))
                    summary_parser.finish()
                    break
                except TypeError:
                    # llm_call doesn't accept on_delta — fall back.
                    response = self.llm_call(messages)
                    break
                except LLM_RETRYABLE_EXCEPTIONS as e:
                    if _retry_attempt >= LLM_MAX_RETRIES:
                        raise
                    import random
                    delay = LLM_BASE_DELAY * (2 ** _retry_attempt) + random.uniform(0, 0.5)
                    logger.warning(
                        "[WF-RETRY] summary attempt %d/%d failed (%s), retrying in %.1fs...",
                        _retry_attempt + 1, LLM_MAX_RETRIES, type(e).__name__, delay,
                    )
                    time.sleep(delay)
                    summary_parser = StreamingParser(
                        on_thought_delta=lambda t: self.on_thought_delta(t) if self.on_thought_delta else None,
                        on_code_start=None,
                        on_code_delta=None,
                        on_code_end=None,
                    )
            if response is None:
                logger.error("Workflow summary LLM call returned None after all retries")
                response = (
                    f"Workflow '{self.workflow.name}' completed all {len(execution_order)} steps. "
                    "Summary generation returned no response."
                )
            self.context.add_assistant_message(response)
            # Emit as a text reply step.
            step = AgentStep(
                step_num=len(execution_order) + 1,
                thought=response,
                is_text_reply=True,
                text_reply=response,
                duration_ms=0,
            )
            if self.step_callback:
                try:
                    self.step_callback(step)
                except Exception:
                    logger.exception("step_callback failed")
            return response
        except Exception as e:
            logger.error("Workflow summary generation failed: %s", e)
            return (
                f"Workflow '{self.workflow.name}' completed all {len(execution_order)} steps. "
                "Unable to generate summary due to an error."
            )


__all__ = [
    "WorkflowLoop",
    "WorkflowDocument",
    "WorkflowNode",
    "WorkflowEdge",
    "topological_sort",
    "get_predecessors",
    "build_step_prompt",
]
