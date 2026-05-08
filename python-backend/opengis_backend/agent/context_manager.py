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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Rough chars-per-token estimate. GPT/Claude average ~3.5 chars/token
# for English + code mixed content. We use 3.5 as a conservative estimate.
_CHARS_PER_TOKEN = 3.5

# Tool names whose outputs MUST NEVER be pruned (reference: opencode).
# Skills are user-invoked first-class capabilities; their outputs often
# carry artifacts (file paths, layer ids, snapshot ids) that the agent
# refers back to many turns later. Replacing them with a placeholder
# would break that causal chain.
_PRUNE_PROTECTED_TOOLS: tuple[str, ...] = ("skill",)


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate from character length."""
    return int(len(text) / _CHARS_PER_TOKEN)


def _estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across a list of chat messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, list):
            # Multi-part content (e.g. text + image)
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += _estimate_tokens(part.get("text", ""))
        # Add overhead for role/name fields
        total += 4
    return total


def _truncate_output(text: str, max_chars: int = 3000) -> str:
    """Truncate long code output, keeping head and tail."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n... [truncated {len(text) - max_chars} chars] ...\n\n"
        + text[-half:]
    )


# The summary prompt used to compress older conversation turns.
# OpenCode-style structured template.
# Anchored-summary system prompt (reference: opencode `compaction.txt`).
# When we re-summarize, we hand the previous summary back to the LLM inside
# a <previous-summary> block and ask it to MERGE — not concatenate. This
# prevents unbounded summary growth across multiple compaction rounds.
_COMPACTION_SYSTEM_PROMPT = (
    "You are an anchored context summarization assistant for coding sessions.\n\n"
    "Summarize only the conversation history you are given. The newest turns may be "
    "kept verbatim outside your summary, so focus on the older context that still "
    "matters for continuing the work.\n\n"
    "If the prompt includes a <previous-summary> block, treat it as the current "
    "anchored summary. Update it with the new history by preserving still-true "
    "details, removing stale details, and merging in new facts.\n\n"
    "Always follow the exact output structure requested by the user prompt. Keep "
    "every section, preserve exact file paths and identifiers when known, and "
    "prefer terse bullets over paragraphs.\n\n"
    "Do not answer the conversation itself. Do not mention that you are summarizing, "
    "compacting, or merging context. Respond in the same language as the conversation."
)


_SUMMARY_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.

<template>
## Goal
- [single-sentence task summary]

## Constraints & Preferences
- [user constraints, preferences, specs, or "(none)"]

## Progress
### Done
- [completed work or "(none)"]

### In Progress
- [current work or "(none)"]

### Blocked
- [blockers or "(none)"]

## Key Decisions
- [decision and why, or "(none)"]

## Next Steps
- [ordered next actions or "(none)"]

## Critical Context
- [important technical facts, errors, open questions, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, commands, error strings, and identifiers when known.
- Do not mention the summary process or that context was compacted."""


# Chinese version of the summary template (for Gap #4)
_SUMMARY_TEMPLATE_ZH = """请严格按照下方 <template> 中的 Markdown 结构输出，并保持各节顺序不变。不要在回复中包含 <template> 标签。

<template>
## 目标
- [一句话任务摘要]

## 约束与偏好
- [用户约束、偏好、规范，或"（无）"]

## 进度
### 已完成
- [已完成的工作，或"（无）"]

### 进行中
- [当前工作，或"（无）"]

### 受阻
- [阻碍因素，或"（无）"]

## 关键决策
- [决策内容及原因，或"（无）"]

## 下一步
- [有序的下一步行动，或"（无）"]

## 关键上下文
- [重要技术事实、错误、开放问题，或"（无）"]

## 相关文件
- [文件或目录路径：为什么重要，或"（无）"]
</template>

规则：
- 保留每一节，即使为空。
- 使用简洁的项目符号，而非段落文本。
- 尽可能保留准确的文件路径、命令、错误字符串和标识符。
- 不要提及摘要过程或上下文已被压缩。"""



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

    # ── Public API ──────────────────────────────────────────────────────

    def add_user_message(self, content: str) -> None:
        """Append a user message to the history."""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        """Append an assistant message (LLM's raw response) to the history."""
        self.messages.append({"role": "assistant", "content": content})

    def add_code_output(
        self,
        step: int,
        code: str,
        output: str = "",
        error: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> None:
        """Append a code execution result as a system/observation message.

        This is the "Observation" in the ReAct/CodeAct loop — the result
        of executing the LLM's code block.

        Args:
            tool_name: Optional name of the primary skill/tool invoked
                (e.g. ``"skill"`` for skill calls). When set to one of
                :py:data:`_PRUNE_PROTECTED_TOOLS`, this message will be
                exempt from output pruning. Pass ``None`` for plain
                Python code without a recognized skill call.

        Note: We stash a small ``_meta`` dict on the message so that
        :py:meth:`_prune_outputs` can later replace the bulky body with a
        skeletal placeholder (step number, code first line, success/fail)
        instead of a fully opaque "[content cleared]" string.
        """
        parts = [f"[Step {step} execution result]"]
        if output:
            truncated = _truncate_output(output, self.max_output_chars)
            parts.append(f"Output:\n{truncated}")
        if error:
            truncated_err = _truncate_output(error, self.max_output_chars)
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

        self.messages.append({
            "role": "user",
            "content": "\n".join(parts),
            "_meta": {
                "kind": "tool_result",
                "step": step,
                "had_error": error is not None,
                "code_summary": code_first_line,
                "tool_name": tool_name,
            },
        })

    def build_messages(self, system_prompt: str) -> list[dict[str, str]]:
        """Build the messages list for an LLM call.

        Structure:
        1. System prompt (always first)
        2. Summary of older turns (if compressed)
        3. Recent messages (from _summary_cutoff onward)

        Internal-only fields (anything starting with ``_``, e.g. ``_meta``
        used by tool-result pruning) are stripped before returning.
        """
        result: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

        if self._summary:
            result.append({
                "role": "system",
                "content": (
                    f"[Conversation summary of earlier turns]\n{self._summary}"
                ),
            })

        # Append messages from cutoff onward (the "live" window).
        for msg in self.messages[self._summary_cutoff:]:
            result.append({
                k: v for k, v in msg.items() if not k.startswith("_")
            })

        return result

    def should_compress(self) -> tuple[bool, str]:
        """Check if compression should trigger.

        Returns:
            (should_compress, reason)
        """
        live = self.messages[self._summary_cutoff:]
        estimated = _estimate_messages_tokens(live)
        threshold = int(self.token_budget * self.compress_threshold)

        if estimated > threshold:
            return True, f"Token count ({estimated}) exceeded threshold ({threshold})"

        # Check tool result ratio
        tool_result_tokens = sum(
            _estimate_tokens(m.get("content", "")) 
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

        if llm_call is not None:
            try:
                summary = self._llm_summarize(
                    to_summarize, llm_call, previous_summary=self._summary
                )
            except Exception:
                logger.warning("LLM summarization failed, falling back to simple")
                summary = self._simple_summarize(to_summarize)
        else:
            summary = self._simple_summarize(to_summarize)

        # Anchored merge (Gap #1): when the LLM is available, the new
        # summary already INCLUDES the previous one (we passed it inside
        # a <previous-summary> block). Replace, don't concatenate, to
        # prevent unbounded growth across multiple compactions.
        # The simple fallback path appends because it cannot merge.
        if llm_call is not None:
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
        reread_msg = self._build_reread_message()
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

    # ── Internal ──────────────────────────────────────────────────────

    def _is_tool_result(self, msg: dict) -> bool:
        """Check if a message is a tool result.

        Prefers the explicit ``_meta.kind`` marker (set by
        :py:meth:`add_code_output`); falls back to the legacy text-based
        heuristic for messages added via other paths.
        """
        meta = msg.get("_meta")
        if isinstance(meta, dict) and meta.get("kind") == "tool_result":
            return True
        content = msg.get("content", "")
        # Heuristic: tool results contain "[Step N execution result]"
        return "[Step" in content and ("Output:" in content or "Error:" in content)

    def _make_pruned_placeholder(self, msg: dict) -> str:
        """Build a skeletal placeholder that retains step number, the first
        meaningful code line, and success/failure — instead of dropping the
        message into a fully opaque "[content cleared]" string.

        Why: when the LLM later wonders "what did step 5 do?", a totally
        opaque placeholder forces it to re-derive context. Keeping the
        skeleton (~50–80 chars) costs almost nothing yet preserves the
        causal chain that CodeAct depends on.
        """
        meta = msg.get("_meta") or {}
        step = meta.get("step")
        had_error = meta.get("had_error")
        code_summary = meta.get("code_summary") or ""

        # Try to recover step number from legacy text format if no _meta.
        if step is None:
            content = msg.get("content", "")
            m = re.search(r"\[Step\s+(\d+)\s+execution result\]", content)
            if m:
                try:
                    step = int(m.group(1))
                except ValueError:
                    step = None
            if had_error is None:
                had_error = "Error:" in content

        head = f"[Step {step} pruned]" if step is not None else "[Old tool result pruned]"
        status = "error" if had_error else "ok"
        if code_summary:
            return f"{head} ({status}) — code: `{code_summary}` — body removed to save tokens"
        return f"{head} ({status}) — body removed to save tokens"

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
            live_tokens = _estimate_messages_tokens(live_messages)

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

            # Gap #5: skip protected tools (e.g. "skill")
            meta = msg.get("_meta") or {}
            tool_name = meta.get("tool_name") if isinstance(meta, dict) else None
            if tool_name in _PRUNE_PROTECTED_TOOLS:
                continue

            content = msg.get("content", "")

            # Idempotency: detect already-pruned placeholders
            if content.endswith("body removed to save tokens") or content == "[Old tool result content cleared]":
                continue

            original_tokens = _estimate_tokens(content)

            # Claude Code style: replace with a skeletal placeholder that
            # preserves step / code / success info.
            placeholder = self._make_pruned_placeholder(msg)
            msg["content"] = placeholder

            # Mark as pruned in meta for future introspection
            meta = msg.setdefault("_meta", {})
            if isinstance(meta, dict):
                meta["pruned"] = True

            saved_tokens += original_tokens - _estimate_tokens(placeholder)
            pruned += 1

        if pruned:
            logger.info(
                "Pruned %d tool result(s), saved ~%d tokens",
                pruned,
                saved_tokens,
            )

        return saved_tokens

    # --- Gap #2: File re-reading after compression ---

    def track_file_edit(self, file_path: str) -> None:
        """Record that a file was edited."""
        abs_path = str(Path(file_path).resolve())
        # De-duplicate: if already tracked, move to end (LRU style)
        if abs_path in self._recently_edited_files:
            self._recently_edited_files.remove(abs_path)
        self._recently_edited_files.append(abs_path)
        # Only keep the 5 most recent files
        if len(self._recently_edited_files) > 5:
            self._recently_edited_files.pop(0)

    def _read_file_for_reread(self, file_path: str) -> str | None:
        """Read a file for re-reading after compression."""
        try:
            path = Path(file_path)
            if not path.exists() or not path.is_file():
                return f"[File no longer exists: {file_path}]"
            
            content = path.read_text(encoding="utf-8", errors="replace")
            
            # Check file size
            if len(content) > self.max_file_chars_for_reread:
                return f"[File too large to re-read: {len(content)} chars, max {self.max_file_chars_for_reread}]"
            
            return content
        except Exception as e:
            logger.warning("Failed to re-read %s: %s", file_path, e)
            return f"[Failed to re-read: {e}]"

    def _build_reread_message(self) -> str | None:
        """Build a message with re-read file contents."""
        if not self._recently_edited_files:
            return None
        
        parts = ["[Auto-reread after compression: Recently edited files]"]
        for file_path in self._recently_edited_files:
            content = self._read_file_for_reread(file_path)
            if content:
                parts.append(f"\n--- {file_path} ---\n{content}")
        
        # Clear the list (already re-read)
        self._recently_edited_files.clear()
        
        return "\n".join(parts) if len(parts) > 1 else None

    # --- Gap #4: Multi-language support ---

    def _detect_language(self, messages: list[dict]) -> str:
        """Detect the primary language of the conversation.
        
        Returns:
            "zh" for Chinese, "en" for English, etc.
        """
        # Check from most recent messages
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                # Detect Chinese characters
                if re.search(r'[\u4e00-\u9fff]', content):
                    return "zh"
                else:
                    return "en"
        return "en"  # Default to English

    def _simple_summarize(self, messages: list[dict]) -> str:
        """Fallback: concatenate messages into a condensed text summary."""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # Truncate individual messages in the summary.
            if len(content) > 500:
                content = content[:500] + "..."
            parts.append(f"[{role}] {content}")
        return "\n".join(parts)

    def _llm_summarize(
        self,
        messages: list[dict],
        llm_call: Any,
        previous_summary: Optional[str] = None,
    ) -> str:
        """Use the LLM to generate a high-quality anchored summary.

        Reference: opencode `compaction.ts::buildPrompt`. When a previous
        summary exists, we hand it back to the LLM inside a
        ``<previous-summary>`` block and ask it to MERGE — preserving
        still-true facts, dropping stale ones, integrating new history.
        This prevents the unbounded "concat" growth our v1 had.
        """
        # Detect language
        language = self._detect_language(messages)

        # Choose template (kept for backward-compat / when no previous
        # summary exists; when merging, the merge instruction is what
        # actually drives the behaviour).
        if language == "zh":
            template = _SUMMARY_TEMPLATE_ZH
        else:
            template = _SUMMARY_TEMPLATE

        # Build a conversation transcript for the summarizer.
        transcript_parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # Pre-truncate over-long tool outputs in the transcript to
            # keep the summarization call itself cheap. 2K chars matches
            # opencode's TOOL_OUTPUT_MAX_CHARS.
            if len(content) > 2000 and self._is_tool_result(msg):
                content = content[:1000] + "\n... [truncated for summarization] ...\n" + content[-1000:]
            transcript_parts.append(f"[{role}]: {content}")
        transcript = "\n\n".join(transcript_parts)

        # Anchored merge: include previous summary if present.
        if previous_summary:
            anchor_intro = (
                "Update the anchored summary below using the conversation history above.\n"
                "Preserve still-true details, remove stale details, and merge in the new facts.\n"
                "<previous-summary>\n"
                f"{previous_summary}\n"
                "</previous-summary>"
            )
        else:
            anchor_intro = "Create a new anchored summary from the conversation history above."

        user_prompt = (
            f"=== Conversation history ===\n{transcript}\n\n"
            f"=== Instructions ===\n{anchor_intro}\n\n{template}"
        )

        summary_messages = [
            {"role": "system", "content": _COMPACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        return llm_call(summary_messages)



__all__ = ["ContextManager"]
