"""CREPE — Presentations sub-server (Group A + lint_presentation, 15 tools).

Stateful slide deck builder. Manages in-memory presentation state with a fair
ticket lock per presentation and compiles to Beamer PDF or PowerPoint PPTX.

Can be run as a standalone MCP server:
    uv run crepe-presentations

Or imported and mounted in the full CREPE monolith (crepe-mcp), which exposes
all 40 tools through a single entry point.

Tools
-----
Group A (14):
  create_presentation, get_presentation, get_slide, set_slide, delete_slide,
  move_slide, duplicate_presentation, update_presentation_metadata,
  list_presentations, export_presentation_source, import_presentation_source,
  compile_presentation, render_slides_as_pngs, cleanup_presentation

Group F (1):
  lint_presentation

Environment variables (all CREPE_ prefixed):
  CREPE_LIBREOFFICE_PATH — path to soffice/libreoffice (for PPTX → PNG rendering)
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from crepe_mcp import linter as _linter
from crepe_mcp.compiler import CompileError, compile_to_pdf, compile_to_pptx
from crepe_mcp.exporter import render_pdf_to_pngs, render_pptx_to_pngs
from crepe_mcp.renderer import build_config_yaml, build_slides_markdown, parse_slides_markdown
from crepe_mcp.runner import run_server
from crepe_mcp.store import (
    delete_presentation as _delete_pres,
)
from crepe_mcp.store import (
    delete_slide as _delete_slide,
)
from crepe_mcp.store import (
    duplicate_presentation as _duplicate_pres,
)
from crepe_mcp.store import (
    get_presentation as _get_pres,
)
from crepe_mcp.store import (
    get_slide_by_index as _get_slide,
)
from crepe_mcp.store import (
    insert_slide as _insert_slide,
)
from crepe_mcp.store import (
    list_presentations as _list_pres,
)
from crepe_mcp.store import (
    move_slide as _move_slide,
)
from crepe_mcp.store import (
    new_presentation,
    upsert_slide,
)
from crepe_mcp.store import (
    replace_all_slides as _replace_all_slides,
)
from crepe_mcp.store import (
    update_metadata as _update_metadata,
)

PRESENTATION_INSTRUCTIONS = """\
CREPE Presentations Engine Guidelines:
1. All slide content must be strictly Pandoc Markdown (--slide-level=2).
2. Two-column layouts must use Pandoc fenced divs (:::: {.columns} ::: {.column width="50%"} ... ::: ::::).
3. Do NOT use raw LaTeX environments like \\begin{columns}, \\begin{center}, or \\includegraphics.
4. Images must use standard Markdown with absolute paths: ![caption](/absolute/path/to/image.png).
5. Always call lint_presentation() before compile_presentation() to ensure syntax correctness.
"""

mcp = FastMCP("crepe-presentations", instructions=PRESENTATION_INSTRUCTIONS)


@mcp.resource("presentation://{presentation_id}/source")
def get_presentation_markdown_resource(presentation_id: str) -> str:
    """Return the live Pandoc Markdown source for the specified presentation."""
    pres = _get_pres(presentation_id)
    with pres.lock:
        return build_slides_markdown(pres)


@mcp.resource("presentation://{presentation_id}/config")
def get_presentation_config_resource(presentation_id: str) -> str:
    """Return the live YAML configuration for the specified presentation."""
    pres = _get_pres(presentation_id)
    with pres.lock:
        return build_config_yaml(pres)


@mcp.prompt("academic_presentation")
def academic_presentation_prompt(
    topic: str,
    audience: str = "Academic / Conference",
    slide_count: int = 10,
) -> str:
    """Template for drafting an academic or technical presentation deck with CREPE."""
    return (
        f"You are creating a {slide_count}-slide academic presentation about '{topic}' for a {audience} audience.\n\n"
        "Instructions:\n"
        "1. Start by calling create_presentation() with appropriate metadata.\n"
        "2. Add slides with set_slide() using Pandoc Markdown, clear titles, bullets, and equations ($...$).\n"
        "3. Use two-column layouts (:::: {.columns} ... ::::) for comparing methods or showing text with figures.\n"
        "4. Always call lint_presentation() to verify syntax before compiling.\n"
        "5. Compile to PDF with compile_presentation(output_format='pdf') and verify with render_slides_as_pngs()."
    )

@mcp.tool
def create_presentation(
    title: str,
    subtitle: str = "",
    author: str = "Mário Antunes",
    institute: str = "Universidade de Aveiro",
    date: str = "2026",
) -> dict:
    """Create a new, empty presentation; returns presentation_id, required by
    every other Group-A tool. All title-slide metadata is set here: title,
    optional subtitle, author, institute, date."""
    pres = new_presentation(
        title=title, subtitle=subtitle, author=author,
        institute=institute, date=date,
    )
    return {"presentation_id": pres.id, "metadata": vars(pres.metadata)}


@mcp.tool
def get_presentation(presentation_id: str) -> dict:
    """Return a presentation's metadata, ordered slide list (index, id,
    title, 200-char content preview), and which compiled artifacts exist."""
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    with pres.lock:
        slides = [
            {
                "index": i,
                "id": s.id,
                "title": s.title,
                "content_preview": s.content[:200] + ("\u2026" if len(s.content) > 200 else ""),
            }
            for i, s in enumerate(pres.slides)
        ]
        artifact_keys = list(pres.artifacts.keys())
        meta = dict(vars(pres.metadata))
    return {
        "presentation_id": presentation_id,
        "metadata": meta,
        "slide_count": len(slides),
        "slides": slides,
        "artifacts": artifact_keys,
    }


@mcp.tool
def get_slide(presentation_id: str, slide_index: int) -> dict:
    """Return the full content of a single slide by its zero-based index."""
    try:
        pres = _get_pres(presentation_id)
        slide = _get_slide(pres, slide_index)
    except (ValueError, IndexError) as exc:
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "presentation_id": presentation_id,
        "index": slide_index,
        "id": slide.id,
        "title": slide.title,
        "content": slide.content,
    }


@mcp.tool
def set_slide(
    presentation_id: str,
    index: int,
    title: str,
    content: str,
    insert: bool = False,
    expected_slide_count: int | None = None,
) -> dict:
    """Add, replace, or insert a slide.

    insert=False (default): index < slide count -> replace in place;
    index >= slide count -> append.
    insert=True: inserts at index, shifting that slide and everything after
    it later (index >= slide count still appends). Combine with
    delete_slide to move a slide to a new position.

    `content` is raw Pandoc Markdown. Always use standard Markdown image tags
    `![](path)` instead of raw LaTeX `\\includegraphics` to guarantee compatibility
    with both PDF and PPTX targets.

    Exact syntax for Beamer/PPTX conventions:
      Incremental bullets : > - item
      Section divider      : content is ONLY "# Section Title", nothing else
      Two-column layout    :
        :::: {.columns}
        ::: {.column width="50%"}
        Left
        :::
        ::: {.column width="50%"}
        Right
        :::
        ::::

    expected_slide_count : optional. Stale-state check for concurrent calls.
    """

    try:
        pres = _get_pres(presentation_id)
        if insert:
            slide, actual_index, warnings = _insert_slide(pres, index, title, content, expected_slide_count)
            action = "inserted"
        else:
            slide, action, actual_index, warnings = upsert_slide(pres, index, title, content, expected_slide_count)
    except (ValueError, IndexError) as exc:
        return {"success": False, "error": str(exc)}

    with pres.lock:
        slide_count = len(pres.slides)
    res = {
        "success": True,
        "presentation_id": presentation_id,
        "action": action,
        "index": actual_index,
        "id": slide.id,
        "title": slide.title,
        "slide_count": slide_count,
    }
    if warnings:
        res["warnings"] = warnings
    return res


@mcp.tool
def delete_slide(
    presentation_id: str,
    index: int,
    expected_slide_count: int | None = None,
) -> dict:
    """Remove a slide by index. Slides after it shift down by one.

    expected_slide_count : optional, same stale-state guard as set_slide.
    """
    try:
        pres = _get_pres(presentation_id)
        slide = _delete_slide(pres, index, expected_slide_count)
    except (ValueError, IndexError) as exc:
        return {"success": False, "error": str(exc)}

    with pres.lock:
        slide_count = len(pres.slides)
    return {
        "success": True,
        "presentation_id": presentation_id,
        "deleted_id": slide.id,
        "deleted_title": slide.title,
        "slide_count": slide_count,
    }


@mcp.tool
def move_slide(
    presentation_id: str,
    from_index: int,
    to_index: int,
    expected_slide_count: int | None = None,
) -> dict:
    """Move a slide from from_index to to_index atomically."""
    try:
        pres = _get_pres(presentation_id)
        slide, actual_to = _move_slide(pres, from_index, to_index, expected_slide_count)
    except (ValueError, IndexError) as exc:
        return {"success": False, "error": str(exc)}

    with pres.lock:
        slide_count = len(pres.slides)
    return {
        "success": True,
        "presentation_id": presentation_id,
        "moved_id": slide.id,
        "from_index": from_index,
        "to_index": actual_to,
        "slide_count": slide_count,
    }


@mcp.tool
def duplicate_presentation(
    presentation_id: str,
    title_suffix: str = " (Copy)",
) -> dict:
    """Clone an existing presentation into a new presentation instance."""
    try:
        new_pres = _duplicate_pres(presentation_id, title_suffix=title_suffix)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "original_id": presentation_id,
        "new_presentation_id": new_pres.id,
        "metadata": vars(new_pres.metadata),
        "slide_count": len(new_pres.slides),
    }



@mcp.tool
def update_presentation_metadata(
    presentation_id: str,
    title: str | None = None,
    subtitle: str | None = None,
    author: str | None = None,
    institute: str | None = None,
    date: str | None = None,
) -> dict:
    """Update title-slide metadata (title/subtitle/author/institute/date)
    on an existing presentation. Only fields given a value are changed."""
    try:
        pres = _get_pres(presentation_id)
        metadata = _update_metadata(
            pres, title=title, subtitle=subtitle,
            author=author, institute=institute, date=date,
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "presentation_id": presentation_id,
        "metadata": vars(metadata),
    }


@mcp.tool
def list_presentations() -> dict:
    """List every presentation currently held in memory."""
    presentations = []
    for pres in _list_pres():
        with pres.lock:
            presentations.append({
                "presentation_id": pres.id,
                "title": pres.metadata.title,
                "slide_count": len(pres.slides),
                "artifacts": list(pres.artifacts.keys()),
            })
    return {"presentations": presentations}


@mcp.tool
def export_presentation_source(
    presentation_id: str,
    output_dir: str | None = None,
    theme: str = "moloch",
    highlight_style: str = "tango",
) -> dict:
    """Return the pandoc source (slides Markdown + config.yml) this
    presentation compiles from.

    output_dir : if given (absolute path), also writes slides.md/config.yml
    there. theme/highlight_style : same meaning as compile_presentation.
    """
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    with pres.lock:
        markdown = build_slides_markdown(pres)
        config_yaml = build_config_yaml(pres, theme=theme, highlight_style=highlight_style)

    result: dict = {
        "success": True,
        "presentation_id": presentation_id,
        "markdown": markdown,
        "config_yaml": config_yaml,
    }

    if output_dir is not None:
        if not os.path.isabs(output_dir):
            return {"success": False, "error": f"output_dir must be an absolute path, got {output_dir!r}"}
        os.makedirs(output_dir, exist_ok=True)
        slides_path = os.path.join(output_dir, "slides.md")
        config_path = os.path.join(output_dir, "config.yml")
        with open(slides_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_yaml)
        result["slides_path"] = slides_path
        result["config_path"] = config_path

    return result


@mcp.tool
def import_presentation_source(
    presentation_id: str,
    markdown: str | None = None,
    source_path: str | None = None,
) -> dict:
    """Replace a presentation's slides by parsing pandoc slide Markdown.
    Splits on '##' (slide) and bare '#' (section-divider) headings,
    ignoring '#' inside fenced code blocks.

    presentation_id must already exist. Every slide it currently has is
    replaced; metadata is untouched. Exactly one of markdown (inline
    content) or source_path (absolute path to a .md file) must be given.
    """
    if (markdown is None) == (source_path is None):
        return {"success": False, "error": "Pass exactly one of markdown or source_path."}
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    if source_path is not None:
        if not os.path.isabs(source_path):
            return {"success": False, "error": f"source_path must be an absolute path, got {source_path!r}"}
        if not os.path.isfile(source_path):
            return {"success": False, "error": f"source_path not found: {source_path!r}"}
        with open(source_path, encoding="utf-8") as f:
            markdown = f.read()
    if markdown is None:
        return {"success": False, "error": "Internal error: markdown is None after source check."}

    try:
        parsed = parse_slides_markdown(markdown)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    slide_count = _replace_all_slides(pres, parsed)

    return {
        "success": True,
        "presentation_id": presentation_id,
        "slide_count": slide_count,
    }


@mcp.tool
def compile_presentation(
    presentation_id: str,
    output_path: str,
    output_format: str,
    theme: str = "moloch",
    highlight_style: str = "tango",
    reference_doc: str | None = None,
) -> dict:
    """Compile the in-memory presentation to PDF or PPTX, writing directly
    to output_path (absolute). Call render_slides_as_pngs afterwards to
    validate visually.

    output_format : 'pdf' (Beamer/lualatex) or 'pptx' (PowerPoint).
    theme  : any Beamer theme name installed on this system (PDF only),
    default 'moloch'.
    highlight_style : code highlight style, default 'tango'.
    reference_doc   : path to a .pptx template (PPTX only, optional).
    """
    if output_format not in ("pdf", "pptx"):
        return {"success": False, "error": f"output_format must be 'pdf' or 'pptx', got {output_format!r}"}
    if not os.path.isabs(output_path):
        return {"success": False, "error": f"output_path must be an absolute path, got {output_path!r}"}
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    try:
        if output_format == "pdf":
            compile_to_pdf(pres, output_path, theme=theme, highlight_style=highlight_style)
        else:
            compile_to_pptx(pres, output_path, reference_doc=reference_doc,
                            theme=theme, highlight_style=highlight_style)
    except CompileError as exc:
        return {"success": False, "error": str(exc)}

    if not os.path.isfile(output_path):
        return {"success": False, "error": "Output file was not created by pandoc."}

    with pres.lock:
        pres.artifacts[output_format] = output_path
    return {
        "success": True,
        "presentation_id": presentation_id,
        "output_format": output_format,
        "output_path": output_path,
        "size_bytes": os.path.getsize(output_path),
    }


@mcp.tool
def render_slides_as_pngs(
    presentation_id: str,
    output_format: str,
    output_dir: str | None = None,
    dpi: int = 150,
) -> dict:
    """Convert a compiled artifact to a numbered PNG sequence for visual
    validation. output_format must match a previously compiled artifact."""
    if output_format not in ("pdf", "pptx"):
        return {"success": False, "error": f"output_format must be 'pdf' or 'pptx', got {output_format!r}"}
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    with pres.lock:
        artifact_path = pres.artifacts.get(output_format)
    if not artifact_path:
        return {
            "success": False,
            "error": (
                f"No '{output_format}' artifact found for {presentation_id!r}. "
                f"Call compile_presentation(output_format='{output_format}') first."
            ),
        }
    if not os.path.isfile(artifact_path):
        return {"success": False, "error": f"Artifact missing on disk: {artifact_path!r}"}

    if output_dir is None:
        output_dir = artifact_path + ".slides"

    try:
        if output_format == "pdf":
            png_files = render_pdf_to_pngs(artifact_path, output_dir, dpi=dpi)
            converter = "pymupdf"
        else:
            png_files, converter = render_pptx_to_pngs(artifact_path, output_dir, dpi=dpi)
    except ImportError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        return {"success": False, "error": f"Rendering failed: {exc}"}

    return {
        "success": True,
        "presentation_id": presentation_id,
        "output_format": output_format,
        "png_dir": output_dir,
        "png_files": png_files,
        "page_count": len(png_files),
        "dpi": dpi,
        "converter": converter,
    }


@mcp.tool
def cleanup_presentation(presentation_id: str) -> dict:
    """Delete a presentation's in-memory state and on-disk scratch dir."""
    try:
        _delete_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "presentation_id": presentation_id}
@mcp.tool
def lint_presentation(
    presentation_id: str,
    slide_index: int | None = None,
) -> dict:
    """Validate slide content without compiling. Call before compile_presentation.

    Checks for forbidden LaTeX, broken image paths, and pandoc parse errors.
    slide_index: if given, checks only that slide; otherwise checks all slides.
    Returns {valid, slide_count, issues: [{type, message, line, slide_index}]}.
    """
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    report = _linter.lint_presentation_content(pres, slide_index=slide_index)
    result = report.to_dict()
    result["presentation_id"] = presentation_id
    result["slide_count"] = len(pres.slides)
    return result

def main() -> None:
    """Console-script entrypoint for the standalone crepe-presentations server."""
    run_server(mcp)


if __name__ == "__main__":
    main()
