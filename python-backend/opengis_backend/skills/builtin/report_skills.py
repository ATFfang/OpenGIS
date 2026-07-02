"""Report generation skills — map snapshots, academic reports, PDF export.

Group: report (attachable, not loaded by default).

These skills enable the agent to:
1. Export map views as PNG with specific basemaps and layer configurations
2. Write structured academic reports in Markdown with embedded figures
3. Export Markdown reports as PDF
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from opengis_backend.skills.context import SkillContext, run_async_from_sync
from opengis_backend.skills.registry import skill

logger = logging.getLogger("opengis.report")


# ── Interactive Snapshot ────────────────────────────────────────────

@skill(
    name="interactive_snapshot",
    display_name="Interactive Map Snapshot",
    description=(
        "Request the user to manually adjust the map and take a screenshot. "
        "This skill blocks indefinitely until the user clicks 'Capture' or "
        "'Skip' in the chat panel. The agent waits as long as needed — no timeout.\n\n"
        "The user can pan, zoom, toggle layers, and switch basemaps before "
        "clicking capture. The screenshot is saved to the specified path.\n\n"
        "If the agent is cancelled by the user while waiting, the skill "
        "automatically unblocks."
    ),
    category="report",
    group="report",
    params=[
        {"name": "save_path", "type": "string", "required": True,
         "description": "Absolute path where the screenshot will be saved (e.g. '/workspace/figures/map.png')."},
        {"name": "prompt", "type": "string", "required": False,
         "description": "Instruction shown to the user (e.g. '请调整到中国全境视图'). Default: generic prompt."},
    ],
    returns="dict with saved_to path, or {skipped: true} if user cancelled",
    needs_context=True,
)
def interactive_snapshot(
    ctx: SkillContext,
    save_path: str,
    prompt: str = "",
) -> dict[str, Any]:
    """Request interactive map screenshot from the user.

    Sends a notification to the frontend which renders a capture card
    in the chat. Blocks indefinitely until the user clicks 'Capture'
    or 'Skip'. No timeout — the user decides when to proceed.
    """
    import time
    from uuid import uuid4

    request_id = uuid4().hex
    resolved_path = Path(save_path)
    if not resolved_path.is_absolute():
        workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path", "")
        if workspace:
            resolved_path = Path(workspace) / resolved_path

    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    # Notify frontend to show the capture card
    run_async_from_sync(ctx.notify("rpc.ui.chat.interactive_snapshot", {
        "request_id": request_id,
        "save_path": str(resolved_path),
        "prompt": prompt or "请调整地图到满意位置，然后点击截图。",
    }))

    # Poll for the result file (written by frontend after capture).
    # No timeout — blocks until user acts or agent is interrupted.
    result_path = resolved_path.parent / f".snapshot_{request_id}.result"

    while True:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result_path.unlink(missing_ok=True)
                if result.get("skipped"):
                    return {"skipped": True, "save_path": str(resolved_path)}
                return {
                    "saved_to": str(resolved_path),
                    "width": result.get("width"),
                    "height": result.get("height"),
                }
            except Exception:
                result_path.unlink(missing_ok=True)
                return {"saved_to": str(resolved_path)}
        time.sleep(0.5)


# ── Write Report Section ────────────────────────────────────────────

@skill(
    name="write_report_section",
    display_name="Write Report Section",
    description=(
        "Write one section of a Markdown report. Call this multiple times "
        "to build a report incrementally — each call appends one section "
        "to report.md. First call creates the file with a title header.\n\n"
        "Use this instead of write_file for long reports, because the LLM "
        "cannot generate very long text in a single code block (output token "
        "limit). Writing one section per call avoids truncation.\n\n"
        "Example workflow:\n"
        "  1. write_report_section(dir, title='My Report', heading='Abstract', content='...')\n"
        "  2. write_report_section(dir, heading='1. Introduction', content='...')\n"
        "  3. write_report_section(dir, heading='2. Results', content='...', figures='[{...}]')\n"
        "  4. write_report_section(dir, heading='3. Conclusion', content='...')"
    ),
    category="report",
    group="report",
    params=[
        {"name": "output_dir", "type": "string", "required": True,
         "description": "Directory where report.md and figures/ will be created (e.g. 'report')."},
        {"name": "title", "type": "string", "required": False,
         "description": "Report title. Only needed on the first call — creates report.md with a title header."},
        {"name": "heading", "type": "string", "required": True,
         "description": "Section heading (e.g. 'Abstract', '1. Introduction', '2. Results')."},
        {"name": "content", "type": "string", "required": True,
         "description": "Section content in Markdown. Keep each call under 2000 chars to avoid truncation."},
        {"name": "figures", "type": "string", "required": False,
         "description": "JSON array of figure objects for this section: [{\"path\": \"/abs/path.png\", \"caption\": \"...\"}]. Figures are copied to figures/ dir and referenced in the report."},
    ],
    returns="dict with report_path and section count",
    needs_context=True,
)
def write_report_section(
    ctx: SkillContext,
    output_dir: str,
    heading: str,
    content: str,
    title: str = "",
    figures: str | None = None,
) -> dict[str, Any]:
    """Write one section of a report, appending to report.md."""
    import shutil

    out = Path(output_dir)
    if not out.is_absolute():
        workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path", "")
        if workspace:
            out = Path(workspace) / out

    out.mkdir(parents=True, exist_ok=True)
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    report_path = out / "report.md"
    is_new = not report_path.exists()

    lines: list[str] = []

    # First call: create title header
    if is_new and title:
        lines.append(f"# {title}\n")

    # Parse explicit figures
    fig_list = []
    if figures:
        try:
            fig_list = json.loads(figures) if isinstance(figures, str) else figures
        except json.JSONDecodeError:
            logger.warning("write_report_section: invalid figures JSON")

    # Resolve relative figure paths
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path", "")
    for fig in fig_list:
        p = Path(fig.get("path", ""))
        if not p.is_absolute() and workspace:
            fig["path"] = str(Path(workspace) / p)

    # Auto-detect image paths referenced in content
    # Matches: ![caption](path) or ![caption](/abs/path.png)
    import re
    img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    for match in img_pattern.finditer(content):
        caption = match.group(1)
        img_path = match.group(2)
        # Skip URLs (http/https)
        if img_path.startswith("http://") or img_path.startswith("https://"):
            continue
        # Resolve relative paths
        p = Path(img_path)
        if not p.is_absolute() and workspace:
            p = Path(workspace) / p
        # Add to fig_list if not already there
        if not any(fig.get("path") == str(p) for fig in fig_list):
            fig_list.append({"path": str(p), "caption": caption})

    # Append section heading
    lines.append(f"\n## {heading}\n")
    lines.append(content)
    lines.append("")

    # Copy figures and append references
    for fig in fig_list:
        fig_path = Path(fig["path"])
        caption = fig.get("caption", fig_path.stem)
        if fig_path.exists():
            dest = fig_dir / fig_path.name
            if not dest.exists():
                shutil.copy2(fig_path, dest)
            # Only append reference if not already in content
            ref = f"figures/{fig_path.name}"
            if ref not in content:
                lines.append(f"![{caption}]({ref})\n")
        else:
            logger.warning("write_report_section: figure not found: %s", fig["path"])

    # Append to file
    with open(report_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Count sections
    try:
        existing = report_path.read_text(encoding="utf-8")
        section_count = existing.count("\n## ")
    except Exception:
        section_count = -1

    return {
        "report_path": str(report_path),
        "sections": section_count,
        "is_new": is_new,
    }

# ── Export PDF ──────────────────────────────────────────────────────

@skill(
    name="export_report_pdf",
    display_name="Export Report as PDF",
    description=(
        "Convert a Markdown report to PDF. Requires 'mdpdf' or 'pandoc' "
        "to be installed. Falls back to Python markdown + weasyprint if "
        "neither is available.\n\n"
        "The PDF includes embedded figures and proper page formatting."
    ),
    category="report",
    group="report",
    params=[
        {"name": "md_path", "type": "string", "required": True,
         "description": "Path to the Markdown file to convert."},
        {"name": "output_path", "type": "string", "required": False,
         "description": "Output PDF path. Defaults to same name with .pdf extension."},
    ],
    returns="dict with pdf_path",
    needs_context=True,
)
def export_report_pdf(
    ctx: SkillContext,
    md_path: str,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Convert Markdown to PDF."""
    md = Path(md_path)

    # Resolve relative paths against the workspace directory.
    if not md.is_absolute():
        workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path", "")
        if workspace:
            md = Path(workspace) / md

    if not md.exists():
        raise FileNotFoundError(f"Markdown file not found: {md}")

    pdf = Path(output_path) if output_path else md.with_suffix(".pdf")
    if not pdf.is_absolute() and md.parent.exists():
        pdf = md.parent / pdf.name

    # Strategy 1: pandoc (best quality)
    if _cmd_exists("pandoc"):
        try:
            # Detect CJK font for xelatex
            cjk_font = _detect_cjk_font()
            pandoc_args = [
                "pandoc", str(md), "-o", str(pdf),
                "--pdf-engine=xelatex",
                "-V", "geometry:margin=1in",
                "-V", "fontsize=11pt",
                "--resource-path", str(md.parent),
            ]
            if cjk_font:
                pandoc_args += [
                    "-V", f"CJKmainfont={cjk_font}",
                    "-V", f"mainfont={cjk_font}",
                ]
            subprocess.run(
                pandoc_args,
                check=True, capture_output=True, text=True, timeout=120,
            )
            return {"pdf_path": str(pdf), "engine": "pandoc"}
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning("pandoc failed: %s, trying mdpdf", e)

    # Strategy 2: mdpdf (simpler, no LaTeX dependency)
    if _cmd_exists("mdpdf"):
        try:
            subprocess.run(
                ["mdpdf", "-o", str(pdf), str(md)],
                check=True, capture_output=True, text=True, timeout=60,
            )
            return {"pdf_path": str(pdf), "engine": "mdpdf"}
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning("mdpdf failed: %s, trying weasyprint", e)

    # Strategy 3: Python markdown + weasyprint
    try:
        import markdown
        from weasyprint import HTML

        md_content = md.read_text(encoding="utf-8")
        html_content = markdown.markdown(
            md_content,
            extensions=["tables", "fenced_code", "toc"],
        )

        # Resolve relative image paths
        md_dir = md.parent.resolve()
        html_content = html_content.replace(
            'src="figures/',
            f'src="file://{md_dir}/figures/',
        )

        cjk_font = _detect_cjk_font()
        font_family = f"'{cjk_font}', 'Times New Roman', serif" if cjk_font else "'Times New Roman', serif"

        full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: {font_family}; margin: 2cm; font-size: 11pt; line-height: 1.6; }}
h1 {{ font-size: 18pt; }}
h2 {{ font-size: 14pt; margin-top: 1.5em; }}
img {{ max-width: 100%; height: auto; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 6px; text-align: left; }}
</style></head><body>{html_content}</body></html>"""

        HTML(string=full_html, base_url=str(md_dir)).write_pdf(str(pdf))
        return {"pdf_path": str(pdf), "engine": "weasyprint"}
    except ImportError:
        raise RuntimeError(
            "No PDF engine available. Install one of: pandoc, mdpdf, or weasyprint.\n"
            "  pip install weasyprint  # recommended\n"
            "  brew install pandoc     # macOS"
        )


# ── Helpers ─────────────────────────────────────────────────────────

def _cmd_exists(cmd: str) -> bool:
    """Check if a command is available on PATH."""
    import shutil
    return shutil.which(cmd) is not None


def _detect_cjk_font() -> str:
    """Detect an available CJK font for PDF rendering.

    Tries common Chinese fonts in order. Returns the first available
    font name, or empty string if none found.
    """
    import subprocess
    candidates = [
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "SimHei",
        "Microsoft YaHei",
        "Heiti SC",
        "PingFang SC",
        "STSong",
        "Hiragino Sans GB",
    ]
    try:
        result = subprocess.run(
            ["fc-list", ":lang=zh", "family"],
            capture_output=True, text=True, timeout=5,
        )
        available = result.stdout
        for font in candidates:
            if font in available:
                return font
    except Exception:
        pass
    return ""
