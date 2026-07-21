"""
Context manager for the custom agent loop.

Manages conversation history with automatic compression to stay within
the LLM's context window. Strategies:

1. **Sliding window**: always keep the last N messages in full.
2. **Summarization**: compress older messages into a compact summary
   using the LLM itself (deferred to a lightweight model call).
3. **Output truncation**: long code outputs are truncated with [...].
4. **Token budget**: hard cap on total estimated context tokens.

References:
- Claude Code: sliding window + auto-summarize older turns
- OpenHands/OpenDevin: conversation compressor with LLM summaries
- LangGraph: checkpoint-based memory with configurable windows
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from opengis_backend.agent.context.file_reread import build_reread_message, track_recent_file_edit
from opengis_backend.agent.context.provider_projector import (
    ProviderContextProjector,
    ProviderProjectionConfig,
)
from opengis_backend.agent.context.provider_request import ProviderRequest, ProviderRequestBuilder
from opengis_backend.agent.context.summarizer import llm_summarize, simple_summarize
from opengis_backend.agent.context.token_utils import (
    CHARS_PER_TOKEN,
    estimate_messages_tokens,
    estimate_tokens,
    truncate_output,
)
from opengis_backend.agent.context.working_state import WorkingStateProjector

logger = logging.getLogger(__name__)

_PRUNE_PROTECTED_TOOLS = frozenset({
    # Skill instructions are injected through a tool result. If the body is
    # pruned, later turns may keep acting as if a skill was loaded while losing
    # the actual operating instructions.
    "load_skill",
})


@dataclass
class ContextManager:
    """Manages conversation history with automatic compression.

    Usage::

        ctx = ContextManager()
        ctx.add_user_message("Show me a map of Beijing")
        messages = ctx.build_messages(system_prompt)
        # ... call LLM with messages ...
        ctx.add_assistant_message("I'll create a map for you.")
        ctx.add_code_output(step=1, code="...", output="...", error=None)
    """

    # ── Configuration ──────────────────────────────────────────────

    # Total context budget in estimated tokens.
    token_budget: int = 100_000

    # Trigger compression when estimated tokens exceed this fraction
    # of the budget.
    compress_threshold: float = 0.80

    # Always keep the last N messages in full (never compressed).
    keep_recent: int = 8

    # Maximum characters for a single code output before truncation.
    max_output_chars: int = 3000

    # Token-based safety buffer (reference: OpenCode)
    safe_buffer_tokens: int = 40000

    # Whether to enable token-based pruning (default: True)
    use_token_based_pruning: bool = True

    # Cached token count of the system prompt (set by build_messages).
    # Included in should_compress() so the budget accounts for the prompt.
    _system_prompt_tokens: int = field(default=0, init=False)

    # Hard cap (in estimated tokens) for any SINGLE tool-result message,
    # even when it sits inside the protected ``keep_recent`` window. A
    # single giant output (e.g. an accidental full-DataFrame dump) can
    # otherwise blow up the live window on its own. Set to 0 to disable.
    max_single_result_tokens: int = 6000

    # Provider-context projection keeps raw history intact on disk while
    # sending a smaller, task-useful version to the model. The newest messages
    # stay verbatim so the model can repair the immediately previous tool/code
    # step without losing details.
    provider_raw_recent: int = 8
    collapse_old_provider_messages: bool = True
    max_provider_digest_chars: int = 6000
    max_projected_tool_result_chars: int = 4000
    max_projected_tool_call_arg_chars: int = 1400
    max_projected_execute_code_chars: int = 900
    recent_user_turns_for_provider: int = 4
    max_recent_user_chars_for_provider: int = 800

    # ── State ──────────────────────────────────────────────────────

    # The conversation history. Each entry is a standard chat message
    # dict: {"role": "user"|"assistant"|"system", "content": str}.
    messages: list[dict[str, str]] = field(default_factory=list)

    # Compressed summary of older messages (if any). Prepended to the
    # context as a system message when building the final messages list.
    _summary: Optional[str] = None

    # Index into self.messages: everything before this index has been
    # summarized and can be dropped from the full context.
    _summary_cutoff: int = 0

    # For Gap #2: Track recently edited files
    _recently_edited_files: list[str] = field(default_factory=list)

    # Maximum file size to re-read (single file)
    max_file_chars_for_reread: int = 5000

    # ── Serialization ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize the context state to a JSON-safe dict.

        Only persists the fields needed to restore the conversation:
        messages, summary, summary_cutoff, and recently_edited_files.
        Configuration fields (token_budget, etc.) are NOT persisted —
        they come from the constructor defaults.
        """
        return {
            "messages": self.messages,
            "summary": self._summary,
            "summary_cutoff": self._summary_cutoff,
            "recently_edited_files": list(self._recently_edited_files),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextManager":
        """Restore a ContextManager from a serialized dict.

        Returns a fresh ContextManager with the persisted state applied.
        """
        ctx = cls()
        ctx.messages = data.get("messages", [])
        ctx._summary = data.get("summary")
        ctx._summary_cutoff = data.get("summary_cutoff", 0)
        ctx._recently_edited_files = data.get("recently_edited_files", [])
        return ctx

    # ── Public API ──────────────────────────────────────────────────────

    def add_user_message(self, content: str, meta: Optional[dict[str, Any]] = None) -> None:
        """Append a user message to the history."""
        msg: dict[str, Any] = {"role": "user", "content": content}
        if meta:
            msg["_meta"] = meta
        self.messages.append(msg)

    def add_assistant_message(self, content: str, meta: Optional[dict[str, Any]] = None) -> None:
        """Append an assistant message (LLM's raw response) to the history."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if meta:
            msg["_meta"] = meta
        self.messages.append(msg)

    def add_system_message(self, content: str, meta: Optional[dict[str, Any]] = None) -> None:
        """Append a system-scoped message to the history."""
        msg: dict[str, Any] = {"role": "system", "content": content}
        if meta:
            msg["_meta"] = meta
        self.messages.append(msg)

    def add_assistant_with_tool_calls(self, content: str | None, tool_calls: list[dict]) -> None:
        """Append an assistant message that includes tool_calls.

        This is the format required by the OpenAI tool-calling protocol:
        the assistant message must contain the tool_calls array so the LLM
        can correlate tool results back to the original calls.
        """
        msg: dict = {"role": "assistant", "content": content or ""}
        msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def mark_recent_scope(self, scope: str, *, count: int = 1, kind: str | None = None) -> None:
        """Mark the newest messages as scoped/internal without changing protocol shape."""
        for msg in reversed(self.messages):
            if count <= 0:
                break
            meta = msg.get("_meta")
            if not isinstance(meta, dict):
                meta = {}
                msg["_meta"] = meta
            meta["scope"] = scope
            if kind:
                meta.setdefault("kind", kind)
            count -= 1

    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        content: str,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append a tool execution result.

        This is the ``role: "tool"`` message that the OpenAI protocol requires
        after the assistant emits tool_calls. The ``tool_call_id`` must match
        the id from the corresponding tool_call.
        """
        message_meta = {
            "kind": "tool_result",
            "tool_name": tool_name,
        }
        if meta:
            message_meta.update(meta)

        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": content,
            "_meta": message_meta,
        })

    def add_code_output(
        self,
        step: int,
        code: str,
        output: str = "",
        error: Optional[str] = None,
        tool_name: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append a code execution result as a system/observation message.

        This is the observation for Python execution through execute_code.

        Args:
            tool_name: Optional name of the primary tool invoked. When set
                to one of :py:data:`_PRUNE_PROTECTED_TOOLS`, this message
                will be exempt from output pruning. Pass ``None`` for plain
                Python code without a recognized tool call.

        Note: We stash a small ``_meta`` dict on the message so that
        :py:meth:`_prune_outputs` can later replace the bulky body with a
        skeletal placeholder (step number, code first line, success/fail)
        instead of a fully opaque "[content cleared]" string.
        """
        parts = [f"[Step {step} execution result]"]
        if output:
            truncated = truncate_output(output, self.max_output_chars)
            parts.append(f"Output:\n{truncated}")
        if error:
            truncated_err = truncate_output(error, self.max_output_chars)
            parts.append(f"Error:\n{truncated_err}")
        if not output and not error:
            parts.append("(no output)")

        # Skeleton info preserved across pruning (Gap #1 improvement)
        code_first_line = ""
        if code:
            for line in code.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    code_first_line = stripped[:80]
                    break

        message_meta = {
            "kind": "tool_result",
            "step": step,
            "had_error": error is not None,
            "code_summary": code_first_line,
            "tool_name": tool_name,
        }
        if meta:
            message_meta.update(meta)

        self.messages.append({
            "role": "user",
            "content": "\n".join(parts),
            "_meta": message_meta,
        })

    def build_provider_request(
        self,
        system_prompt: str,
        *,
        user_instructions: str | None = None,
        exclude_workflow_context: bool = False,
        projection_limits: Any | None = None,
        stable_system_sections: list[tuple[str, str]] | None = None,
        dynamic_tail_sections: list[tuple[str, str]] | None = None,
    ) -> ProviderRequest:
        """Assemble the canonical provider request (the single assembler).

        Enforces the layout contract from ``docs/prompt-cache-stable-prefix-architecture.md``
        (3.1.2). Physical order is::

            ── STABLE PREFIX (cacheable, append-only) ──
            [S0] system core prompt
            [S1] stable system sections (capability manifest / active tools / rules)
            [S2] user preferences
            [S3] conversation summary
            ── conversation history (append-only) ──
            ── DYNAMIC TAIL (recomputed each turn, never cached) ──
            [D*] turn objective / deviation feedback (dynamic_tail_sections)
            [D1] runtime anchor
            [D2] working state

        The critical invariant: every per-turn-changing block lives in the
        DYNAMIC TAIL, physically *after* the conversation history, so the
        cache prefix is not broken at the top of the request.
        """
        builder = ProviderRequestBuilder()
        # Cache system prompt token count for should_compress().
        self._system_prompt_tokens = estimate_tokens(system_prompt)

        # ── STABLE PREFIX ──────────────────────────────────────────────
        builder.add_system_text(
            id="system.base",
            kind="system",
            content=system_prompt,
            stability="static",
            cache_policy="cacheable",
        )
        for section_id, content in (stable_system_sections or []):
            builder.add_system_text(
                id=section_id,
                kind="system",
                content=content,
                stability="session_static",
                cache_policy="cacheable",
            )
        if user_instructions and user_instructions.strip():
            builder.add_system_text(
                id="system.user_preferences",
                kind="user_preferences",
                content=f"## User Preferences\n{user_instructions.strip()[:2000]}",
                stability="session_static",
                cache_policy="cacheable",
            )
        if self._summary:
            summary = (
                self._strip_workflow_summary(self._summary)
                if exclude_workflow_context
                else self._summary
            )
            if not summary.strip():
                summary = (
                    "Earlier turns contained a structured workflow/report run. "
                    "That context is intentionally hidden from this normal chat turn "
                    "unless the user explicitly asks to continue it."
                )
            builder.add_system_text(
                id="context.summary",
                kind="conversation_summary",
                content=f"[Conversation summary of earlier turns]\n{summary}",
                stability="session_static",
                cache_policy="cacheable",
            )

        # ── CONVERSATION HISTORY (append-only) ─────────────────────────
        projector = ProviderContextProjector(
            config=self._provider_projection_config(projection_limits),
            is_tool_result=self._is_tool_result,
            make_pruned_placeholder=self._make_pruned_placeholder,
            is_workflow_context_message=self._is_workflow_context_message,
        )
        history = projector.project_live_messages(
            self.messages,
            summary_cutoff=self._summary_cutoff,
            exclude_workflow_context=exclude_workflow_context,
        )
        builder.add_section(
            id="context.history",
            kind="history",
            messages=history,
            stability="turn_dynamic",
            cache_policy="cacheable",
        )

        # ── DYNAMIC TAIL (after history, never cached) ─────────────────
        for index, (section_id, content) in enumerate(dynamic_tail_sections or []):
            builder.add_system_text(
                id=section_id or f"runtime.control_{index + 1}",
                kind="runtime",
                content=content,
                stability="turn_dynamic",
                cache_policy="none",
            )
        runtime_anchor = self._build_runtime_anchor_message(exclude_workflow_context=exclude_workflow_context)
        if runtime_anchor:
            builder.add_system_text(
                id="runtime.anchor",
                kind="runtime",
                content=runtime_anchor,
                stability="turn_dynamic",
                cache_policy="none",
            )
        working_state = WorkingStateProjector().project(
            self.messages,
            summary_cutoff=self._summary_cutoff,
            exclude_workflow_context=exclude_workflow_context,
        ).to_prompt()
        if working_state:
            builder.add_system_text(
                id="runtime.working_state",
                kind="working_state",
                content=working_state,
                stability="turn_dynamic",
                cache_policy="none",
            )

        return builder.build()

    def build_messages(
        self,
        system_prompt: str,
        user_instructions: str | None = None,
        *,
        exclude_workflow_context: bool = False,
        projection_limits: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Thin wrapper over :py:meth:`build_provider_request`.

        Kept for callers that only need the flat provider message list (e.g.
        the debug projection tool). It shares the exact same assembly path so
        there is only ONE place that decides request layout.

        Internal-only fields (anything starting with ``_``) are already
        stripped by the underlying builder.
        """
        return self.build_provider_request(
            system_prompt,
            user_instructions=user_instructions,
            exclude_workflow_context=exclude_workflow_context,
            projection_limits=projection_limits,
        ).messages

    def should_compress(self) -> tuple[bool, str]:
        """Check if compression should trigger.

        Returns:
            (should_compress, reason)
        """
        live = self.messages[self._summary_cutoff:]
        estimated = estimate_messages_tokens(live) + self._system_prompt_tokens
        threshold = int(self.token_budget * self.compress_threshold)

        if estimated > threshold:
            return True, f"Token count ({estimated}, incl. prompt {self._system_prompt_tokens}) exceeded threshold ({threshold})"

        # Check tool result ratio
        tool_result_tokens = sum(
            estimate_tokens(m.get("content", "")) 
            for m in live 
            if self._is_tool_result(m)
        )
        if tool_result_tokens > threshold * 0.6:
            return True, f"Tool results ({tool_result_tokens} tokens) dominating context"

        return False, ""

    def compress(self, llm_call: Any = None) -> None:
        """Compress older messages into a summary.

        If ``llm_call`` is provided (a callable that takes messages and
        returns a string), we use the LLM to generate a high-quality
        summary. Otherwise we fall back to a simple concatenation-based
        summary.

        The most recent ``keep_recent`` messages are never compressed.
        """
        live = self.messages[self._summary_cutoff:]
        if len(live) <= self.keep_recent:
            # Nothing to compress — all messages are "recent".
            return

        # Split: older messages to summarize, recent to keep.
        to_summarize = live[:-self.keep_recent]

        # OpenCode-style: prune old tool outputs to free context space.
        self._prune_outputs()

        # Track whether the LLM-based anchored merge actually succeeded.
        # On failure we must NOT replace the existing anchored summary with
        # a lossy simple-concat one (that would silently drop the merged
        # history accumulated over previous compactions).
        llm_merge_ok = False
        if llm_call is not None:
            try:
                summary = llm_summarize(
                    to_summarize,
                    llm_call,
                    previous_summary=self._summary,
                    is_tool_result=self._is_tool_result,
                )
                if summary and summary.strip():
                    llm_merge_ok = True
                else:
                    logger.warning("LLM summarization returned empty, falling back to simple")
                    summary = simple_summarize(to_summarize)
            except Exception:
                logger.warning("LLM summarization failed, falling back to simple")
                summary = simple_summarize(to_summarize)
        else:
            summary = simple_summarize(to_summarize)

        # Anchored merge (Gap #1): when the LLM merge succeeded, the new
        # summary already INCLUDES the previous one (we passed it inside
        # a <previous-summary> block). Replace, don't concatenate, to
        # prevent unbounded growth across multiple compactions.
        #
        # When the LLM merge FAILED (#5 fix), we fell back to a simple
        # concat summary that does NOT contain the previous anchored
        # summary — so we must APPEND it to preserve earlier history
        # instead of overwriting and losing it.
        if llm_merge_ok:
            self._summary = summary
        elif self._summary:
            self._summary = self._summary + "\n\n---\n\n" + summary
        else:
            self._summary = summary

        # Advance the cutoff: everything before the kept messages is now
        # represented by the summary.
        self._summary_cutoff = len(self.messages) - self.keep_recent

        logger.info(
            "Context compressed: %d messages summarized, %d kept live, "
            "summary length=%d chars",
            len(to_summarize),
            self.keep_recent,
            len(self._summary),
        )

        # Gap #2: Re-read recently edited files after compression
        reread_msg = build_reread_message(
            self._recently_edited_files,
            max_chars=self.max_file_chars_for_reread,
        )
        if reread_msg:
            self.messages.append({
                "role": "system",
                "content": reread_msg,
            })
            logger.info("Re-read recently edited files after compression")

    def reset(self) -> None:
        """Clear all history and summaries."""
        self.messages.clear()
        self._summary = None
        self._summary_cutoff = 0
        self._recently_edited_files.clear()

    @property
    def total_messages(self) -> int:
        return len(self.messages)

    @property
    def live_messages(self) -> int:
        return len(self.messages) - self._summary_cutoff

    def estimate_live_tokens(self) -> int:
        """Estimate tokens in the current live conversation window."""
        return estimate_messages_tokens(self.messages[self._summary_cutoff:])

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        """Estimate tokens for a provider message array."""
        return estimate_messages_tokens(messages)

    def estimate_request_tokens(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> int:
        """Estimate total request tokens, including function schemas.

        OpenCode compacts against the full provider request, not just chat
        messages. Tool schemas are repeated on every function-call request,
        so surfacing this number makes loop telemetry and future compaction
        decisions much closer to the actual model load.
        """
        total = estimate_messages_tokens(messages)
        if tools:
            try:
                total += estimate_tokens(json.dumps(tools, ensure_ascii=False, default=str))
            except Exception:
                total += estimate_tokens(str(tools))
        return total

    # ── Internal ──────────────────────────────────────────────────────

    def _is_tool_result(self, msg: dict) -> bool:
        """Check if a message is a tool result."""
        meta = msg.get("_meta")
        return isinstance(meta, dict) and meta.get("kind") == "tool_result"

    def _provider_projection_config(self, limits: Any | None = None) -> ProviderProjectionConfig:
        """Build provider projection config, optionally narrowed by budget limits."""
        if limits is None:
            return ProviderProjectionConfig(
                raw_recent=self.provider_raw_recent,
                collapse_old_messages=self.collapse_old_provider_messages,
                max_digest_chars=self.max_provider_digest_chars,
                max_tool_result_chars=self.max_projected_tool_result_chars,
                max_tool_call_arg_chars=self.max_projected_tool_call_arg_chars,
                max_execute_code_chars=self.max_projected_execute_code_chars,
                recent_user_turns=self.recent_user_turns_for_provider,
                max_recent_user_chars=self.max_recent_user_chars_for_provider,
            )
        return ProviderProjectionConfig(
            raw_recent=max(1, min(self.provider_raw_recent, int(getattr(limits, "provider_raw_recent", self.provider_raw_recent)))),
            collapse_old_messages=self.collapse_old_provider_messages,
            max_digest_chars=max(1200, min(self.max_provider_digest_chars, int(getattr(limits, "max_digest_chars", self.max_provider_digest_chars)))),
            max_tool_result_chars=max(600, min(self.max_projected_tool_result_chars, int(getattr(limits, "max_tool_result_chars", self.max_projected_tool_result_chars)))),
            max_tool_call_arg_chars=max(400, min(self.max_projected_tool_call_arg_chars, int(getattr(limits, "max_tool_call_arg_chars", self.max_projected_tool_call_arg_chars)))),
            max_execute_code_chars=max(240, min(self.max_projected_execute_code_chars, int(getattr(limits, "max_execute_code_chars", self.max_projected_execute_code_chars)))),
            recent_user_turns=max(1, min(self.recent_user_turns_for_provider, int(getattr(limits, "recent_user_turns", self.recent_user_turns_for_provider)))),
            max_recent_user_chars=self.max_recent_user_chars_for_provider,
        )

    def _is_workflow_context_message(self, msg: dict) -> bool:
        """Return True for workflow-only history that should not leak into chat."""
        meta = msg.get("_meta")
        if isinstance(meta, dict):
            kind = str(meta.get("kind") or "")
            scope = str(meta.get("scope") or "")
            if kind.startswith("workflow") or scope == "workflow":
                return True
        return False

    def _build_runtime_anchor_message(self, *, exclude_workflow_context: bool) -> str:
        """Summarize active runtime objects and recent tool failures.

        The raw transcript remains the source of truth, but the model should not
        have to infer current failed operations/layers from dozens of old tool
        payloads. Keep this short and factual.
        """
        active_operation = ""
        active_layer = ""
        failures: list[str] = []
        for msg in reversed(self.messages[self._summary_cutoff:]):
            if exclude_workflow_context and self._is_workflow_context_message(msg):
                continue
            meta = msg.get("_meta") if isinstance(msg.get("_meta"), dict) else {}
            if not self._is_tool_result(msg):
                continue
            tool_name = str(msg.get("name") or meta.get("tool_name") or "")
            content = str(msg.get("content") or "")
            data: dict[str, Any] = {}
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {}
            if not active_operation and tool_name in {"get_operation", "run_operation", "edit_operation"}:
                active_operation = _operation_label_from_tool_payload(tool_name, data)
            if not active_layer and tool_name in {"add_layer", "get_layer", "zoom_to_layer", "set_categorized_style", "update_layer_style"}:
                active_layer = _layer_label_from_tool_payload(tool_name, data, meta)
            failed = data.get("success") is False or bool(meta.get("had_error")) or "runner_guard_blocked" in content[:200]
            if failed and len(failures) < 4:
                error = str(data.get("error") or data.get("reason") or meta.get("runner_guard_reason") or "unknown error")
                failures.append(f"{tool_name}: {' '.join(error.split())[:260]}")
            if active_operation and active_layer and len(failures) >= 4:
                break

        lines = []
        if active_operation:
            lines.append(f"- active_operation: {active_operation}")
        if active_layer:
            lines.append(f"- active_layer: {active_layer}")
        if failures:
            lines.append("- recent_tool_failures:")
            lines.extend(f"  - {failure}" for failure in failures)
        if not lines:
            return ""
        return (
            "## Runtime State Anchors\n"
            "Current active objects and recent failures inferred from settled tool results. "
            "Use these as state hints, not as a replacement for explicit current-state tools.\n"
            + "\n".join(lines)
        )

    def _strip_workflow_summary(self, summary: str) -> str:
        """Remove stale workflow/report lines from a normal-chat summary."""
        blocked = (
            "workflow",
            "工作流",
            "Workflow Step",
            "Academic GIS Report",
            "学术报告",
            "学术分析报告",
        )
        kept = [
            line for line in summary.splitlines()
            if not any(token in line for token in blocked)
        ]
        return "\n".join(kept).strip()

    def _make_pruned_placeholder(self, msg: dict) -> str:
        """Build a skeletal placeholder that retains step number, the first
        meaningful code line, and success/failure — instead of dropping the
        message into a fully opaque "[content cleared]" string.

        Why: when the LLM later wonders "what did step 5 do?", a totally
        opaque placeholder forces it to re-derive context. Keeping the
        skeleton (~50–80 chars) costs almost nothing yet preserves the
        causal chain that later tool calls depend on.
        """
        meta = msg.get("_meta") or {}
        step = meta.get("step")
        had_error = meta.get("had_error")
        code_summary = meta.get("code_summary") or ""

        head = f"[Step {step} pruned]" if step is not None else "[Tool result pruned]"
        status = "error" if had_error else "ok"
        if code_summary:
            base = f"{head} ({status}) — code: `{code_summary}`"
        else:
            base = f"{head} ({status})"

        refs: list[str] = []
        for key in (
            "artifact_layer_id",
            "artifact_layer_name",
            "artifact_path",
            "script_path",
            "script_abs_path",
            "retained_output_path",
        ):
            value = meta.get(key) if isinstance(meta, dict) else None
            if isinstance(value, str) and value:
                refs.append(f"{key}={value}")
        if refs:
            base += " — refs: " + "; ".join(refs[:6])
        return base + " — body removed to save tokens"

    def prune_tool_results(self) -> int:
        """Public, idempotent entry point for tool-result pruning.

        Returns the estimated number of tokens saved. Safe to call on any
        cadence (per-step, per-round, or only inside :py:meth:`compress`).
        """
        return self._prune_outputs()

    def _prune_outputs(self) -> int:
        """Claude Code Layer 1: Replace old tool results with skeletal
        placeholders.

        - Protects the most recent ``keep_recent`` messages.
        - Skips when ``use_token_based_pruning`` is on AND live tokens
          are still under the safe buffer (Gap #3).
        - Idempotent: messages already replaced by a placeholder are
          detected and skipped.

        Returns:
            Number of tokens saved.
        """
        protect = self.keep_recent
        total = len(self.messages)
        cutoff = max(0, total - protect)

        # Check token-based safety buffer (Gap #3)
        if self.use_token_based_pruning:
            live_messages = self.messages[self._summary_cutoff:]
            live_tokens = estimate_messages_tokens(live_messages)

            if live_tokens <= self.safe_buffer_tokens:
                logger.info(
                    "Skipping prune: live tokens (%d) within safe buffer (%d)",
                    live_tokens,
                    self.safe_buffer_tokens,
                )
                return 0

        saved_tokens = 0
        pruned = 0

        for i in range(cutoff):
            msg = self.messages[i]

            # Only process tool results
            if not self._is_tool_result(msg):
                continue

            # Gap #5: skip protected tools (e.g. load_skill)
            meta = msg.get("_meta") or {}
            tool_name = meta.get("tool_name") if isinstance(meta, dict) else None
            if tool_name in _PRUNE_PROTECTED_TOOLS:
                continue

            content = msg.get("content", "")

            # Idempotency: detect already-pruned placeholders
            if content.endswith("body removed to save tokens") or content == "[Old tool result content cleared]":
                continue

            original_tokens = estimate_tokens(content)

            # Claude Code style: replace with a skeletal placeholder that
            # preserves step / code / success info.
            placeholder = self._make_pruned_placeholder(msg)
            msg["content"] = placeholder

            # Mark as pruned in meta for future introspection
            meta = msg.setdefault("_meta", {})
            if isinstance(meta, dict):
                meta["pruned"] = True

            saved_tokens += original_tokens - estimate_tokens(placeholder)
            pruned += 1

        # #6 fix: also hard-cap any single oversized tool result inside the
        # protected window. Protected messages are never replaced by a
        # skeleton, but an individual giant dump still gets head/tail
        # truncated so one message can't dominate the live context.
        if self.max_single_result_tokens > 0:
            cap_chars = int(self.max_single_result_tokens * CHARS_PER_TOKEN)
            for i in range(cutoff, total):
                msg = self.messages[i]
                if not self._is_tool_result(msg):
                    continue
                content = msg.get("content", "")
                if content.endswith("body removed to save tokens"):
                    continue
                if estimate_tokens(content) <= self.max_single_result_tokens:
                    continue
                # Skip protected tools — their artifacts must stay intact.
                meta = msg.get("_meta") or {}
                tool_name = meta.get("tool_name") if isinstance(meta, dict) else None
                if tool_name in _PRUNE_PROTECTED_TOOLS:
                    continue
                original_tokens = estimate_tokens(content)
                msg["content"] = truncate_output(content, cap_chars)
                saved_tokens += original_tokens - estimate_tokens(msg["content"])
                pruned += 1

        if pruned:
            logger.info(
                "Pruned %d tool result(s), saved ~%d tokens",
                pruned,
                saved_tokens,
            )

        return saved_tokens

    # ─── Gap #2: File re-reading after compression ───

    def track_file_edit(self, file_path: str) -> None:
        """Record that a file was edited."""
        track_recent_file_edit(self._recently_edited_files, file_path)


def _operation_label_from_tool_payload(tool_name: str, data: dict[str, Any]) -> str:
    operation = data.get("operation")
    if isinstance(operation, dict):
        op_id = operation.get("id") or operation.get("operation_id")
        status = operation.get("status")
        if op_id:
            return f"{op_id}" + (f" status={status}" if status else "")
    op_id = data.get("operation_id")
    if op_id:
        status = data.get("status")
        return f"{op_id}" + (f" status={status}" if status else "")
    if tool_name:
        return f"(last touched by {tool_name})"
    return ""


def _layer_label_from_tool_payload(tool_name: str, data: dict[str, Any], meta: dict[str, Any]) -> str:
    layer_id = data.get("layer_id") or data.get("id") or meta.get("artifact_layer_id")
    layer_name = data.get("layer_name") or data.get("name") or meta.get("artifact_layer_name")
    if layer_id or layer_name:
        label = str(layer_name or layer_id)
        if layer_id and layer_name and str(layer_id) != str(layer_name):
            label += f" ({layer_id})"
        return label
    if tool_name:
        return f"(last touched by {tool_name})"
    return ""


__all__ = ["ContextManager"]
