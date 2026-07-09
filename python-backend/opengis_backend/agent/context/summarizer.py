"""Conversation summarization helpers for context compaction."""

from __future__ import annotations

import re
from typing import Any, Optional


COMPACTION_SYSTEM_PROMPT = (
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


SUMMARY_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.

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


SUMMARY_TEMPLATE_ZH = """请严格按照下方 <template> 中的 Markdown 结构输出，并保持各节顺序不变。不要在回复中包含 <template> 标签。

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


def detect_language(messages: list[dict]) -> str:
    """Detect the primary language of the conversation."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if re.search(r"[\u4e00-\u9fff]", content):
                return "zh"
            return "en"
    return "en"


def simple_summarize(messages: list[dict]) -> str:
    """Fallback: concatenate messages into a condensed text summary."""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if len(content) > 500:
            content = content[:500] + "..."
        parts.append(f"[{role}] {content}")
    return "\n".join(parts)


def llm_summarize(
    messages: list[dict],
    llm_call: Any,
    *,
    previous_summary: Optional[str] = None,
    is_tool_result: Any = None,
) -> str:
    """Use the LLM to generate a high-quality anchored summary."""
    language = detect_language(messages)
    template = SUMMARY_TEMPLATE_ZH if language == "zh" else SUMMARY_TEMPLATE

    transcript_parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if len(content) > 2000 and is_tool_result is not None and is_tool_result(msg):
            content = content[:1000] + "\n... [truncated for summarization] ...\n" + content[-1000:]
        transcript_parts.append(f"[{role}]: {content}")
    transcript = "\n\n".join(transcript_parts)

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

    response = llm_call([
        {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
    return response.content or ""


__all__ = [
    "COMPACTION_SYSTEM_PROMPT",
    "SUMMARY_TEMPLATE",
    "SUMMARY_TEMPLATE_ZH",
    "detect_language",
    "llm_summarize",
    "simple_summarize",
]
