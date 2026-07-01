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


# ── Map Snapshot ────────────────────────────────────────────────────

@skill(
    name="export_map_snapshot",
    display_name="Export Map Snapshot",
    description=(
        "Export the current map view as a PNG/JPG image. Automatically switches "
        "to the map tab before exporting, so the map is always visible. "
        "Supports switching to a specific basemap and controlling layer visibility. "
        "The original map state is automatically restored after export.\n\n"
        "Use cases:\n"
        "- Academic report figures: use basemap='carto-light-nolabels' for clean maps\n"
        "- Multi-layer comparison: export multiple times with different visible_layers\n"
        "- Presentation slides: use hide_basemap=True for white background\n\n"
        "Available basemap IDs: 'osm-streets', 'carto-dark', 'carto-dark-nolabels', "
        "'carto-light', 'carto-light-nolabels', 'carto-voyager', 'carto-voyager-nolabels'.\n\n"
        "Note: This skill blocks until the file is written. If the map has no data, "
        "the export will be a blank basemap."
    ),
    category="report",
    group="report",
    params=[
        {"name": "save_path", "type": "string", "required": True,
         "description": "Absolute path where the image will be saved (e.g. '/workspace/figures/map1.png')."},
        {"name": "basemap_id", "type": "string", "required": False,
         "description": "Basemap to use. Default: 'carto-light-nolabels' (clean, light, no labels — ideal for academic figures)."},
        {"name": "visible_layers", "type": "string", "required": False,
         "description": "JSON array of layer IDs to show. Other layers will be hidden. Omit to keep current visibility."},
        {"name": "hide_basemap", "type": "boolean", "required": False,
         "description": "If true, hide the basemap entirely (white background). Default: false."},
        {"name": "dpi_scale", "type": "number", "required": False,
         "description": "DPI multiplier for high-res export (1=screen, 2=2x, 3=3x). Default: 2 for print quality."},
    ],
    returns="dict with saved_to, width, height, format",
    needs_context=True,
)
def export_map_snapshot(
    ctx: SkillContext,
    save_path: str,
    basemap_id: str = "carto-light-nolabels",
    visible_layers: str | None = None,
    hide_basemap: bool = False,
    dpi_scale: float = 2.0,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Export map snapshot via frontend RPC.

    Blocks until the frontend confirms the file has been written.
    Polls save_path every 0.5s up to `timeout` seconds.
    """
    import time

    payload: dict[str, Any] = {
        "save_path": save_path,
        "basemap_id": basemap_id,
        "dpi_scale": dpi_scale,
        "hide_basemap": hide_basemap,
    }

    if visible_layers:
        try:
            layers = json.loads(visible_layers) if isinstance(visible_layers, str) else visible_layers
            if isinstance(layers, list):
                payload["visible_layers"] = layers
        except json.JSONDecodeError:
            pass

    run_async_from_sync(ctx.notify("rpc.ui.map.export_map", payload))

    # Wait for the frontend to write the file.
    target = Path(save_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if target.exists() and target.stat().st_size > 0:
            size_kb = target.stat().st_size / 1024
            return {
                "saved_to": save_path,
                "basemap": basemap_id,
                "dpi_scale": dpi_scale,
                "size_kb": round(size_kb, 1),
            }
        time.sleep(0.5)

    raise TimeoutError(
        f"Map export timed out after {timeout}s. "
        f"File not found: {save_path}. "
        "Check that the frontend map is visible and rendering."
    )


# ── Write Report ────────────────────────────────────────────────────

@skill(
    name="write_report",
    display_name="Write Academic Report",
    description=(
        "Generate a structured academic report in Markdown format with "
        "embedded figure references. Creates a report.md file and a figures/ "
        "directory for images.\n\n"
        "The report follows standard academic structure: Title, Abstract, "
        "Introduction, Methods, Results, Discussion, Conclusion, References.\n\n"
        "Use export_map_snapshot first to generate figure PNGs, then reference "
        "them in the report via their filenames."
    ),
    category="report",
    group="report",
    params=[
        {"name": "output_dir", "type": "string", "required": True,
         "description": "Directory where report.md and figures/ will be created."},
        {"name": "title", "type": "string", "required": True,
         "description": "Report title."},
        {"name": "authors", "type": "string", "required": False,
         "description": "Author names. Default: 'OpenGIS Analysis'."},
        {"name": "sections", "type": "string", "required": True,
         "description": "JSON array of section objects: [{\"heading\": \"...\", \"content\": \"...\"}]. Content supports Markdown."},
        {"name": "figures", "type": "string", "required": False,
         "description": "JSON array of figure objects: [{\"path\": \"/abs/path/to/image.png\", \"caption\": \"...\", \"label\": \"fig-1\"}]. Paths are copied to figures/ dir."},
        {"name": "references", "type": "string", "required": False,
         "description": "JSON array of reference strings: [\"Author (Year). Title. Journal.\"]."},
    ],
    returns="dict with report_path and figures_dir",
    needs_context=True,
)
def write_report(
    ctx: SkillContext,
    output_dir: str,
    title: str,
    sections: str,
    authors: str = "OpenGIS Analysis",
    figures: str | None = None,
    references: str | None = None,
) -> dict[str, Any]:
    """Generate a structured academic report in Markdown."""
    out = Path(output_dir)

    # Resolve relative paths against the workspace directory.
    if not out.is_absolute():
        workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path", "")
        if workspace:
            out = Path(workspace) / out

    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    # Parse sections
    section_list = json.loads(sections) if isinstance(sections, str) else sections

    # Parse and copy figures
    fig_list = []
    if figures:
        fig_list = json.loads(figures) if isinstance(figures, str) else figures

    # Build Markdown
    lines: list[str] = []
    lines.append(f"# {title}\n")
    lines.append(f"**Authors:** {authors}\n")
    lines.append(f"**Date:** {_today()}\n")
    lines.append("---\n")

    fig_counter = 0
    for sec in section_list:
        heading = sec.get("heading", "")
        content = sec.get("content", "")
        lines.append(f"## {heading}\n")
        lines.append(content)
        lines.append("")

        # Insert figures after the section that references them
        for fig in fig_list:
            if fig.get("after_section") == heading:
                fig_counter += 1
                fig_path = Path(fig["path"])
                fig_name = fig.get("label", f"fig-{fig_counter}")
                caption = fig.get("caption", fig_path.stem)

                # Copy figure to figures/ directory
                dest = fig_dir / fig_path.name
                if fig_path.exists() and fig_path.resolve() != dest.resolve():
                    import shutil
                    shutil.copy2(fig_path, dest)

                lines.append(f"![{caption}](figures/{fig_path.name})")
                lines.append(f"*Figure {fig_counter}: {caption}*\n")

    # Append remaining figures not tied to a section
    remaining = [f for f in fig_list if not f.get("after_section")]
    if remaining:
        lines.append("## Figures\n")
        for fig in remaining:
            fig_counter += 1
            fig_path = Path(fig["path"])
            caption = fig.get("caption", fig_path.stem)
            dest = fig_dir / fig_path.name
            if fig_path.exists() and fig_path.resolve() != dest.resolve():
                import shutil
                shutil.copy2(fig_path, dest)
            lines.append(f"![{caption}](figures/{fig_path.name})")
            lines.append(f"*Figure {fig_counter}: {caption}*\n")

    # References
    ref_list = []
    if references:
        ref_list = json.loads(references) if isinstance(references, str) else references
    if ref_list:
        lines.append("## References\n")
        for i, ref in enumerate(ref_list, 1):
            lines.append(f"[{i}] {ref}")
        lines.append("")

    report_path = out / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "report_path": str(report_path),
        "figures_dir": str(fig_dir),
        "sections": len(section_list),
        "figures": fig_counter,
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

def _today() -> str:
    from datetime import date
    return date.today().isoformat()


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
