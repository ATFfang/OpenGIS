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

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from opengis_backend.agent.agent_loop import (
    AgentStep, CodeExecResult, StreamingParser,
    LLM_MAX_RETRIES, LLM_BASE_DELAY, LLM_RETRYABLE_EXCEPTIONS,
)
from opengis_backend.agent.context_manager import ContextManager
from opengis_backend.agent.tool_runtime import ToolRuntime, parse_tool_arguments, validate_execute_code_payload

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
    input_contract: str = ""
    output_contract: str = ""
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
                input_contract=n.get("inputContract", n.get("input_contract", "")),
                output_contract=n.get("outputContract", n.get("output_contract", "")),
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

    def to_dict(self) -> dict[str, Any]:
        """Serialize the workflow document to a stable JSON-compatible shape."""
        return {
            "name": self.name,
            "description": self.description,
            "nodes": [
                {
                    "id": node.id,
                    "title": node.title,
                    "description": node.description,
                    "inputContract": node.input_contract,
                    "outputContract": node.output_contract,
                    "type": node.node_type,
                    "config": dict(node.config),
                    "max_retries": node.max_retries,
                    "hooks": list(node.hooks),
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "label": edge.label,
                }
                for edge in self.edges
            ],
            "metadata": dict(self.metadata),
        }


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

    if node.input_contract or node.output_contract:
        parts.append("## Node Communication Contract (HIGH PRIORITY)")
        if node.input_contract:
            parts.append(
                "**Receives from upstream**: "
                f"{node.input_contract}\n"
            )
        if node.output_contract:
            parts.append(
                "**Must hand off downstream**: "
                f"{node.output_contract}\n"
            )
        parts.append(
            "Treat this contract as stronger than generic task wording. "
            "Use upstream results according to the receive contract. "
            "Before finishing this step, make sure the handoff contract is "
            "satisfied and explicitly stated in your final plain-text step "
            "summary with exact file paths, layer ids, field names, metrics, "
            "or other identifiers needed by downstream nodes.\n"
        )

    if predecessor_outputs:
        parts.append("**Results from previous steps**:")
        for pred_id, output in predecessor_outputs.items():
            # Truncate long outputs
            display = output[:2000] + "..." if len(output) > 2000 else output
            parts.append(f"- Step `{pred_id}`: {display}")
        parts.append("")

    parts.append(
        "**Instructions**: Accomplish this step by calling function tools. "
        "Use `execute_code` for Python work; do not output Markdown code "
        "blocks as executable work. Use the results from previous steps as "
        "needed. Keep calling tools until the step is FULLY complete "
        "(files saved, layers displayed, etc.). When this step is done, "
        "reply with plain text to signal completion and summarize what was "
        "accomplished. If a downstream handoff contract is defined, your "
        "final text MUST list the concrete handoff values.\n"
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
    # Streaming hooks — same as AgentLoop. When provided, the LLM call
    # uses stream=True and tokens are forwarded in real-time.
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
        self._emit_plan(execution_order, node_outputs, total_steps)

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
            self._emit_plan(execution_order, node_outputs, total_steps)

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

        error_count = 0
        max_errors = node.max_retries or self.max_retries_per_node
        accumulated_output: list[str] = []
        nudged = False  # Track whether we've nudged the LLM to call tools.
        tool_steps = 0

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
            streamed_tool_code: dict[int, dict[str, Any]] = {}

            def _wf_on_tool_delta(tool_index: int, tool_name: str, payload: dict[str, Any]) -> None:
                if tool_name != "execute_code":
                    return
                code = payload.get("code")
                if not isinstance(code, str):
                    return
                state = streamed_tool_code.setdefault(
                    tool_index,
                    {"step": step_index * 100 + tool_index + 1, "length": 0, "open": False, "invalid": False},
                )
                if state.get("invalid"):
                    return
                if validate_execute_code_payload(code):
                    state["invalid"] = True
                    return
                if not state["open"]:
                    code_started["v"] = True
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

            response = None
            for _retry_attempt in range(LLM_MAX_RETRIES + 1):
                try:
                    response = self.llm_call(
                        messages,
                        on_delta=_on_llm_delta,
                        on_tool_delta=_wf_on_tool_delta,
                        tools=self.tool_schemas,
                    )
                    parser.finish()
                    break
                except TypeError as te:
                    # Only fall back if the TypeError is about on_delta.
                    if "on_delta" not in str(te) and "on_tool_delta" not in str(te) and "keyword" not in str(te).lower() and "unexpected" not in str(te).lower():
                        raise
                    response = self.llm_call(messages, tools=self.tool_schemas)
                    response_text = self._response_text(response)
                    if response_text and self.on_thought_delta:
                        try:
                            self.on_thought_delta(response_text)
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
                    # Notify UI that we're retrying (not failed)
                    if self.progress_callback:
                        try:
                            self.progress_callback(
                                "retrying",
                                f"Connection interrupted, retrying ({_retry_attempt + 1}/{LLM_MAX_RETRIES})...",
                            )
                        except Exception:
                            pass
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

            response_text = self._response_text(response)
            tool_calls = self._response_tool_calls(response)

            for state in streamed_tool_code.values():
                if state.get("open") and self.on_code_end:
                    try:
                        self.on_code_end(int(state["step"]))
                    except Exception:
                        logger.exception("on_code_end failed")

            if tool_calls:
                self.context.add_assistant_with_tool_calls(response_text, tool_calls)
                self.context.mark_recent_scope(
                    "workflow",
                    count=1,
                    kind="workflow_tool_calls",
                )
                for tool_index, tc in enumerate(tool_calls):
                    tc_id = tc.get("id", "")
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    arguments = parse_tool_arguments(func.get("arguments", "{}"))

                    if self.progress_callback:
                        try:
                            self.progress_callback(
                                "tool_call",
                                f"Step {step_index}: {node.title} — calling {tool_name}...",
                            )
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
                            if isinstance(updated_metadata, dict) and result_metadata is not None:
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

                    self.context.add_tool_result(
                        tc_id,
                        tool_name,
                        result_content,
                        meta=result_metadata,
                    )
                    self.context.mark_recent_scope(
                        "workflow",
                        count=1,
                        kind="workflow_tool_result",
                    )
                    self.context.prune_tool_results()
                    accumulated_output.append(f"[{tool_name}] {result_content}")
                    tool_steps += 1

                    if result_error:
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
                                f"[System] The tool `{tool_name}` failed with error:\n"
                                f"```\n{result_error}\n```\n"
                                "Fix the arguments or patch/rerun the persisted script if this was "
                                "`execute_code`. Continue with function tools only; do not output "
                                "Markdown code blocks as executable work.\n"
                                f"(Error {error_count}/{max_errors})"
                            ),
                            meta={"kind": "workflow_tool_error_feedback", "scope": "workflow"},
                        )

                nudged = False
                continue

            # ── No tool_calls: handle as node completion text ──
            #
            # Function-call architecture rule: plain text is never executed.
            # Python must arrive through the execute_code tool. This removes
            # the old CodeAct path where Markdown code fences were executed.
            thought = response_text
            if not self._should_accept_text_completion(
                response_text,
                tool_steps=tool_steps,
                nudged=nudged,
                node=node,
            ):
                nudged = True
                logger.info(
                    "Text-only reply before node %s completed (iteration %d) — nudging to call tools.",
                    node.id, iteration,
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

    def _should_accept_text_completion(
        self,
        text: str,
        *,
        tool_steps: int,
        nudged: bool,
        node: WorkflowNode,
    ) -> bool:
        """Conservative completion detector for workflow node text replies."""
        stripped = (text or "").strip()
        if not stripped:
            return False
        if tool_steps > 0 or nudged:
            return True
        if node.node_type in {"output", "decision"}:
            return True

        lowered = stripped.lower()
        incomplete_markers = (
            "i will",
            "i'll",
            "next i",
            "接下来",
            "下一步",
            "将会",
            "需要先",
            "not complete",
            "need to call",
            "need to run",
        )
        if any(marker in lowered for marker in incomplete_markers):
            return False

        completion_markers = (
            "done",
            "completed",
            "finished",
            "successfully",
            "已完成",
            "已经完成",
            "处理完成",
            "执行完成",
            "完成了",
        )
        return any(marker in lowered for marker in completion_markers)

    def _write_step_output(
        self,
        node: WorkflowNode,
        step_index: int,
        full_output: str,
        workspace: str,
    ) -> str | None:
        """Write full step output to an intermediate markdown file.

        Returns the file path, or None if writing failed.
        """
        try:
            from pathlib import Path
            steps_dir = Path(workspace) / ".opengis" / "workflow_steps"
            steps_dir.mkdir(parents=True, exist_ok=True)
            path = steps_dir / f"step{step_index}_{node.id}.md"

            lines = [
                f"# Step {step_index}: {node.title}\n",
                f"## Full Output\n",
                full_output,
            ]
            path.write_text("\n".join(lines), encoding="utf-8")
            logger.debug("Step output written to %s (%d chars)", path, len(full_output))
            return str(path)
        except Exception as e:
            logger.warning("Failed to write step output: %s", e)
            return None

    def _summarize_step(
        self,
        node: WorkflowNode,
        step_index: int,
        full_output: str,
        file_path: str | None,
    ) -> str:
        """Extract a compact summary from step output.

        Heuristic extraction — no extra LLM call. Pulls out:
        - Data paths (file paths ending in common GIS extensions)
        - Key numbers (feature counts, statistics)
        - The first meaningful line of output
        """
        import re

        lines = full_output.strip().split("\n")

        # Extract file paths mentioned in output
        path_pattern = re.compile(
            r"(/[\w./\-]+\.(?:geojson|shp|gpkg|csv|json|tif|tiff|png|jpg|pdf|md))"
        )
        paths = list(set(path_pattern.findall(full_output)))

        # Extract key numbers (feature counts, statistics)
        number_pattern = re.compile(
            r"(?:要素|记录|features?|rows?|count|数量|总计|共)\D*?(\d[\d,]+)",
            re.IGNORECASE,
        )
        numbers = number_pattern.findall(full_output)[:5]

        # Build summary
        parts = [f"Step {step_index}: {node.title}"]

        if node.output_contract:
            parts.append(f"交付契约: {node.output_contract}")

        if paths:
            path_strs = [f"  - {p}" for p in paths[:8]]
            parts.append("产出:\n" + "\n".join(path_strs))

        if numbers:
            parts.append(f"关键数据: {', '.join(numbers[:5])}")

        # First meaningful line
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) > 10 and not stripped.startswith("#"):
                preview = stripped[:150] + ("..." if len(stripped) > 150 else "")
                parts.append(f"摘要: {preview}")
                break

        if file_path:
            parts.append(f"详情: {file_path}")

        return "\n".join(parts)

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
        # Write full output to intermediate file
        file_path = None
        if self.workspace:
            file_path = self._write_step_output(node, step_index, full_output, self.workspace)

        # Generate compact summary
        summary = self._summarize_step(node, step_index, full_output, file_path)

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
        total_steps: int,
    ) -> None:
        """Emit a plan_update event showing current workflow progress."""
        if not self.plan_callback:
            return

        steps = []
        for i, node in enumerate(execution_order, 1):
            if node.id in node_outputs:
                output = node_outputs[node.id]
                if output.startswith("(Step '") and "failed" in output:
                    status = "failed"
                elif output.startswith("(Workflow interrupted"):
                    status = "skipped"
                else:
                    status = "done"
            elif i == len([n for n in execution_order[:i] if n.id in node_outputs]) + 1:
                status = "in_progress"
            else:
                status = "pending"

            steps.append({
                "id": node.id,
                "title": f"{i}. {node.title}",
                "status": status,
            })

        try:
            self.plan_callback({
                "plan_id": f"workflow-{id(self)}",
                "steps": steps,
                "title": self.workflow.name,
            })
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
                response_text = (
                    f"Workflow '{self.workflow.name}' completed all {len(execution_order)} steps. "
                    "Summary generation returned no response."
                )
            else:
                response_text = self._response_text(response)
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

    @staticmethod
    def _response_text(response: Any) -> str:
        if response is None:
            return ""
        if hasattr(response, "content"):
            return getattr(response, "content", None) or ""
        return str(response)

    @staticmethod
    def _response_tool_calls(response: Any) -> list[dict] | None:
        if response is None or not hasattr(response, "tool_calls"):
            return None
        calls = getattr(response, "tool_calls", None)
        return calls if calls else None


__all__ = [
    "WorkflowLoop",
    "WorkflowDocument",
    "WorkflowNode",
    "WorkflowEdge",
    "topological_sort",
    "get_predecessors",
    "build_step_prompt",
]
