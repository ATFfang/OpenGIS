"""Validation for LLM-authored execute_code payloads."""

from __future__ import annotations

import re


_THINK_TAG_RE = re.compile(r"</?\s*think\s*>", re.IGNORECASE)
_MARKDOWN_FENCE_RE = re.compile(r"```")
_TOOL_CONFUSION_RE = re.compile(
    r"execute_code\s*中.*?(不能|无法|不行)|"
    r"(不能|无法|不行).*?execute_code\s*中|"
    r"standalone tool calls.*?cannot access|"
    r"无法在\s*execute_code",
    re.IGNORECASE,
)
_SLEEP_CALL_RE = re.compile(r"(?:time|asyncio)\.sleep\(\s*([0-9]+(?:\.[0-9]+)?)")
_REASONING_COMMENT_MARKERS = (
    "我们需要",
    "我们将",
    "我们可以",
    "让我们",
    "相反",
    "因此",
    "由于",
    "但这样",
    "实际上",
    "考虑到",
    "改变策略",
    "更好的方法",
    "需要先",
    "但不行",
)
_WORKER_WAIT_MARKERS = (
    "worker",
    "dynamic map",
    "dynamic_layer",
    "emit_dynamic",
    "等待",
    "完成更新",
    "更新完成",
)


def validate_execute_code_payload(code: str) -> str | None:
    """Return an error message when an execute_code payload is not code-only."""
    if _THINK_TAG_RE.search(code):
        return (
            "execute_code.code contains a <think> tag. Send only executable "
            "Python code in the code argument; put no hidden reasoning, "
            "strategy narration, or XML-style thinking tags in code."
        )
    if _MARKDOWN_FENCE_RE.search(code):
        return (
            "execute_code.code contains Markdown code fences. Send the raw "
            "Python source only, without ``` fences or surrounding prose."
        )

    sleep_seconds: list[float] = []
    for match in _SLEEP_CALL_RE.finditer(code):
        try:
            sleep_seconds.append(float(match.group(1)))
        except ValueError:
            continue
    if sleep_seconds and max(sleep_seconds) >= 15:
        lowered = code.lower()
        meaningful_lines = [
            line.strip()
            for line in code.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        wait_only = all(
            line.startswith(("import ", "from ", "print(", "time.sleep(", "asyncio.sleep(", "sleep("))
            for line in meaningful_lines
        )
        mentions_worker_wait = any(marker in lowered for marker in _WORKER_WAIT_MARKERS)
        if wait_only or mentions_worker_wait:
            return (
                "execute_code.code appears to be waiting for a resident worker "
                "with a long sleep. Do not block the agent loop with time.sleep; "
                "call wait_worker_update(worker_id=..., timeout=...) or get_worker "
                "to inspect worker progress."
            )

    suspicious_total = 0
    suspicious_run = 0
    max_suspicious_run = 0
    comment_total = 0
    scanned = 0
    for raw_line in code.splitlines()[:100]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        scanned += 1
        is_comment = stripped.startswith("#")
        if not is_comment:
            suspicious_run = 0
            continue
        comment_total += 1
        text = stripped.lstrip("#").strip()
        if _TOOL_CONFUSION_RE.search(text):
            return (
                "execute_code.code contains tool-planning commentary inside "
                "Python comments. If another tool is needed, call that tool "
                "directly as a function call in the next step. The code "
                "argument must contain executable Python only."
            )
        if any(marker in text for marker in _REASONING_COMMENT_MARKERS):
            suspicious_total += 1
            suspicious_run += 1
            max_suspicious_run = max(max_suspicious_run, suspicious_run)
        else:
            suspicious_run = 0

    if (
        scanned >= 12
        and comment_total >= 8
        and (max_suspicious_run >= 5 or suspicious_total >= 8)
    ):
        return (
            "execute_code.code appears to contain a chain-of-thought or "
            "strategy monologue in comments. Keep comments short and factual; "
            "send only the Python needed to perform the action."
        )
    return None


__all__ = ["validate_execute_code_payload"]
