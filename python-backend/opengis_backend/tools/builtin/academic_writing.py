"""Academic writing tools — polish, translate, grammar check, abstract generation.

Group: report (attachable, not loaded by default).

Based on GPT-Academic (github.com/binary-husky/gpt_academic) prompt patterns,
adapted for the OpenGIS tool architecture. All tools are prompt-driven —
they send the text to the LLM with academic writing instructions and return
the improved result.

These tools do NOT call the LLM directly. They return structured instructions
that the agent's LLM processes as part of its normal code execution flow.
The agent calls these tools, receives the instructions, and applies them to
the text in its context.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from opengis_backend.tools.registry import tool

logger = logging.getLogger("opengis.academic")


# ── Academic Polish ─────────────────────────────────────────────────

@tool(
    name="academic_polish",
    display_name="Academic Polish",
    description=(
        "Polish academic text to meet publication standards. Improves spelling, "
        "grammar, clarity, concision, and readability while preserving the "
        "original scientific meaning.\n\n"
        "Works with both Chinese and English text. For Chinese text, also "
        "improves sentence structure and reduces redundancy.\n\n"
        "Returns the polished text and a table of changes with explanations."
    ),
    category="writing",
    group="report",
    params=[
        {"name": "text", "type": "string", "required": True,
         "description": "The academic text to polish."},
        {"name": "language", "type": "string", "required": False,
         "description": "Language hint: 'en' for English, 'zh' for Chinese, 'auto' to detect. Default: 'auto'."},
        {"name": "style", "type": "string", "required": False,
         "description": "Writing style: 'formal' (default), 'concise', 'detailed'."},
    ],
    returns="dict with polished_text and changes table",
    needs_context=False,
)
def academic_polish(text: str, language: str = "auto", style: str = "formal") -> dict[str, Any]:
    """Return polishing instructions as structured data for the agent to apply."""

    if language == "auto":
        language = "zh" if any('一' <= c <= '鿿' for c in text) else "en"

    if language == "zh":
        instruction = (
            "作为一名中文学术论文写作改进助理，你的任务是改进所提供文本的拼写、语法、"
            "清晰、简洁和整体可读性，同时分解长句，减少重复，并提供改进建议。"
            "请先提供文本的更正版本，然后在markdown表格中列出修改的内容，并给出修改的理由。"
        )
    else:
        instruction = (
            "Below is a paragraph from an academic paper. Polish the writing to meet "
            "the academic style, improve the spelling, grammar, clarity, concision and "
            "overall readability. When necessary, rewrite the whole sentence. "
            "Firstly, provide the polished paragraph. "
            "Secondly, list all modifications in a markdown table with columns: "
            "Original | Modified | Reason."
        )

    if style == "concise":
        instruction += "\nFocus on making the text as concise as possible without losing meaning."
    elif style == "detailed":
        instruction += "\nExpand abbreviated explanations where clarity would benefit."

    return {
        "instruction": instruction,
        "text": text,
        "language": language,
        "action": "polish",
    }


# ── Academic Translate ──────────────────────────────────────────────

@tool(
    name="academic_translate",
    display_name="Academic Translate",
    description=(
        "Translate academic text between Chinese and English with proper "
        "academic register and terminology. Unlike generic translators, this "
        "preserves technical terms, maintains academic tone, and adapts "
        "sentence structure to the target language's conventions.\n\n"
        "Automatically detects source language and translates to the other."
    ),
    category="writing",
    group="report",
    params=[
        {"name": "text", "type": "string", "required": True,
         "description": "The academic text to translate."},
        {"name": "target_lang", "type": "string", "required": False,
         "description": "Target language: 'en' or 'zh'. Default: opposite of source."},
        {"name": "preserve_terms", "type": "string", "required": False,
         "description": "Comma-separated technical terms that should NOT be translated (e.g. 'GIS,NDVI,shapefile')."},
    ],
    returns="dict with translated_text and detected source language",
    needs_context=False,
)
def academic_translate(
    text: str,
    target_lang: str = "",
    preserve_terms: str = "",
) -> dict[str, Any]:
    """Return translation instructions for the agent."""

    is_chinese = any('一' <= c <= '鿿' for c in text)
    source_lang = "zh" if is_chinese else "en"

    if not target_lang:
        target_lang = "en" if is_chinese else "zh"

    if source_lang == "zh" and target_lang == "en":
        instruction = (
            "你是经验丰富的学术翻译，请把以下中文学术文章段落翻译成英文。"
            "翻译时请注意：\n"
            "1. 使用学术英语表达，避免口语化\n"
            "2. 保持专业术语的准确性\n"
            "3. 必要时调整句子顺序以符合英文表达习惯\n"
            "4. 不要重复原文，只输出翻译结果"
        )
    elif source_lang == "en" and target_lang == "zh":
        instruction = (
            "I want you to act as a scientific English-Chinese translator. "
            "Translate the following academic text into Chinese. "
            "Requirements:\n"
            "1. Use formal academic Chinese\n"
            "2. Preserve technical accuracy\n"
            "3. Adapt sentence structure to Chinese conventions\n"
            "4. Do not repeat the original text"
        )
    else:
        instruction = f"Translate the following text to {target_lang} with academic register."

    if preserve_terms:
        instruction += f"\n\nDo NOT translate these terms, keep them as-is: {preserve_terms}"

    return {
        "instruction": instruction,
        "text": text,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "action": "translate",
    }


# ── Grammar Check ───────────────────────────────────────────────────

@tool(
    name="academic_grammar_check",
    display_name="Grammar Check",
    description=(
        "Check academic text for grammar and spelling errors. Returns a "
        "table of found errors with corrections, and the corrected text.\n\n"
        "Only reports actual errors — does not rephrase or polish."
    ),
    category="writing",
    group="report",
    params=[
        {"name": "text", "type": "string", "required": True,
         "description": "The text to check for grammar and spelling errors."},
    ],
    returns="dict with errors table and corrected_text",
    needs_context=False,
)
def academic_grammar_check(text: str) -> dict[str, Any]:
    """Return grammar check instructions."""

    instruction = (
        "Help me ensure that the grammar and the spelling is correct. "
        "Do not try to polish the text, if no mistake is found, tell me "
        "that this paragraph is good. "
        "If you find grammar or spelling mistakes, please list mistakes "
        "you find in a two-column markdown table, put the original text "
        "in the first column, put the corrected text in the second column "
        "and highlight the key words you fixed. "
        "Finally, please provide the proofread text."
    )

    return {
        "instruction": instruction,
        "text": text,
        "action": "grammar_check",
    }


# ── Abstract Generation ─────────────────────────────────────────────

@tool(
    name="generate_abstract",
    display_name="Generate Abstract",
    description=(
        "Generate a structured academic abstract from a full report or paper. "
        "The abstract covers: background, objective, methods, results, and "
        "conclusions. Supports Chinese and English.\n\n"
        "Typical length: 200-300 words (English) or 300-500 characters (Chinese)."
    ),
    category="writing",
    group="report",
    params=[
        {"name": "text", "type": "string", "required": True,
         "description": "The full report/paper text to summarize."},
        {"name": "language", "type": "string", "required": False,
         "description": "Output language: 'en' or 'zh'. Default: same as input."},
        {"name": "max_words", "type": "number", "required": False,
         "description": "Maximum word count (English) or character count (Chinese). Default: 300."},
    ],
    returns="dict with abstract text",
    needs_context=False,
)
def generate_abstract(text: str, language: str = "", max_words: int = 300) -> dict[str, Any]:
    """Return abstract generation instructions."""

    is_chinese = any('一' <= c <= '鿿' for c in text)
    if not language:
        language = "zh" if is_chinese else "en"

    if language == "zh":
        instruction = (
            f"请根据以下学术报告内容，生成一篇结构化的中文摘要（不超过{max_words}字）。\n"
            "摘要应包含以下要素：\n"
            "1. 研究背景与目的\n"
            "2. 研究方法\n"
            "3. 主要结果\n"
            "4. 结论\n\n"
            "请直接输出摘要文本，不要添加标题或标签。"
        )
    else:
        instruction = (
            f"Based on the following academic report, generate a structured "
            f"abstract (max {max_words} words).\n"
            "The abstract should cover:\n"
            "1. Background and objective\n"
            "2. Methods\n"
            "3. Key results\n"
            "4. Conclusions\n\n"
            "Output only the abstract text, no headings or labels."
        )

    return {
        "instruction": instruction,
        "text": text,
        "language": language,
        "action": "abstract",
    }


# ── Reference Formatting ────────────────────────────────────────────

@tool(
    name="format_references",
    display_name="Format References",
    description=(
        "Format reference entries into a specific citation style. "
        "Supports APA 7th, MLA 9th, Chicago 17th, and GB/T 7714-2015 (Chinese standard).\n\n"
        "Input: raw reference text (one per line or as a list).\n"
        "Output: formatted references in the requested style."
    ),
    category="writing",
    group="report",
    params=[
        {"name": "references", "type": "string", "required": True,
         "description": "Raw reference text, one reference per line."},
        {"name": "style", "type": "string", "required": False,
         "description": "Citation style: 'APA' (default), 'MLA', 'Chicago', 'GB/T 7714'."},
    ],
    returns="dict with formatted references",
    needs_context=False,
)
def format_references(references: str, style: str = "APA") -> dict[str, Any]:
    """Return reference formatting instructions."""

    style_guides = {
        "APA": "APA 7th edition format. Example: Author, A. A. (Year). Title of article. Title of Periodical, volume(issue), pages. https://doi.org/xxx",
        "MLA": "MLA 9th edition format. Example: Author. \"Title of Article.\" Title of Periodical, vol. number, no. number, Year, pp. pages.",
        "Chicago": "Chicago 17th edition (author-date). Example: Author. Year. \"Title of Article.\" Title of Periodical volume (issue): pages.",
        "GB/T 7714": "GB/T 7714-2015 中国国家标准格式。示例：作者. 题名[文献类型标识]. 刊名, 年, 卷(期): 页码.",
    }

    guide = style_guides.get(style, style_guides["APA"])

    instruction = (
        f"Format the following references into {style} style.\n"
        f"Style guide: {guide}\n\n"
        "Rules:\n"
        "1. Each reference should be on its own line\n"
        "2. Preserve all information from the original\n"
        "3. If information is missing, mark it as [missing]\n"
        "4. Number the references sequentially\n"
        "5. Sort alphabetically by first author's last name"
    )

    return {
        "instruction": instruction,
        "references": references,
        "style": style,
        "action": "format_references",
    }
