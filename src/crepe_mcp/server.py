"""CREPE — Compile, Research, Export, Presentation Engine.

FastMCP server exposing 17 tools across two groups:

GROUP A — Stateful presentation builder
  1.  create_presentation
  2.  get_presentation
  3.  get_slide
  4.  set_slide
  5.  delete_slide
  6.  update_presentation_metadata
  7.  list_presentations
  8.  export_presentation_source
  9.  import_presentation_source
  10. compile_presentation
  11. render_slides_as_pngs
  12. cleanup_presentation

GROUP B — Research & web utilities
  13. academic_search
  14. web_search
  15. wikipedia_search
  16. wikipedia_read
  17. fetch_webpage

Environment variables (all CREPE_ prefixed):
  CREPE_TAVILY_API_KEY        — Tavily API key for web_search
  CREPE_HEADLESS_BROWSER_PATH — path to Chromium-compatible browser
"""
from __future__ import annotations

import os
from typing import Optional

from fastmcp import FastMCP

from crepe_mcp.compiler import CompileError, compile_to_pdf, compile_to_pptx
from crepe_mcp.exporter import render_pdf_to_pngs, render_pptx_to_pngs
from crepe_mcp.renderer import build_config_yaml, build_slides_markdown, parse_slides_markdown
from crepe_mcp import research
from crepe_mcp.store import (
    delete_presentation as _delete_pres,
    delete_slide as _delete_slide,
    get_presentation as _get_pres,
    get_slide_by_index as _get_slide,
    insert_slide as _insert_slide,
    list_presentations as _list_pres,
    new_presentation,
    update_metadata as _update_metadata,
    upsert_slide,
)

mcp = FastMCP("crepe")


# ===========================================================================
# GROUP A — Stateful presentation builder
# ===========================================================================

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

    slides = [
        {
            "index": i,
            "id": s.id,
            "title": s.title,
            "content_preview": s.content[:200] + ("…" if len(s.content) > 200 else ""),
        }
        for i, s in enumerate(pres.slides)
    ]
    return {
        "presentation_id": presentation_id,
        "metadata": vars(pres.metadata),
        "slide_count": len(slides),
        "slides": slides,
        "artifacts": list(pres.artifacts.keys()),
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
) -> dict:
    """Add, replace, or insert a slide.

    insert=False (default): index < slide count -> replace in place;
    index >= slide count -> append.
    insert=True: inserts at index, shifting that slide and everything after
    it later (index >= slide count still appends). Combine with
    delete_slide to move a slide to a new position.

    `content` is raw Pandoc Markdown -- standard bullets/code/images/math
    all work as expected. Less-obvious Pandoc/Beamer conventions:
      Incremental bullets : > - item
      Speaker notes        : ::: notes\\ntext\\n:::
      Section divider      : content is ONLY "# Section Title", nothing else
      Two-column layout    : :::: {.columns}\\n::: {.column width="50%"}\\nLeft\\n:::\\n::: {.column width="50%"}\\nRight\\n:::\\n::::
    """
    try:
        pres = _get_pres(presentation_id)
        if insert:
            slide = _insert_slide(pres, index, title, content)
            action = "inserted"
        else:
            slide, action = upsert_slide(pres, index, title, content)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    actual_index = pres.slides.index(slide)
    return {
        "success": True,
        "presentation_id": presentation_id,
        "action": action,
        "index": actual_index,
        "id": slide.id,
        "title": slide.title,
        "slide_count": len(pres.slides),
    }


@mcp.tool
def delete_slide(presentation_id: str, index: int) -> dict:
    """Remove a slide by index. Slides after it shift down by one."""
    try:
        pres = _get_pres(presentation_id)
        slide = _delete_slide(pres, index)
    except (ValueError, IndexError) as exc:
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "presentation_id": presentation_id,
        "deleted_id": slide.id,
        "deleted_title": slide.title,
        "slide_count": len(pres.slides),
    }


@mcp.tool
def update_presentation_metadata(
    presentation_id: str,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    author: Optional[str] = None,
    institute: Optional[str] = None,
    date: Optional[str] = None,
) -> dict:
    """Update title-slide metadata on an existing presentation. Only fields
    given a value are changed; others keep their current value -- use this
    instead of recreating a presentation to fix a title typo after slides
    already exist."""
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
    """List every presentation currently held in memory (state and its
    on-disk scratch dir persist until cleanup_presentation or process
    exit). Use this to recover a lost presentation_id or find stale
    presentations to clean up."""
    presentations = [
        {
            "presentation_id": pres.id,
            "title": pres.metadata.title,
            "slide_count": len(pres.slides),
            "artifacts": list(pres.artifacts.keys()),
        }
        for pres in _list_pres()
    ]
    return {"presentations": presentations}


@mcp.tool
def export_presentation_source(
    presentation_id: str,
    output_dir: Optional[str] = None,
    theme: str = "moloch",
    highlight_style: str = "tango",
) -> dict:
    """Return the exact pandoc source (slides Markdown + config.yml) this
    presentation compiles from -- built with the same functions
    compile_presentation uses, so it's byte-identical. Presentations only
    live in memory (lost on restart or cleanup_presentation) with no other
    durable copy, so this is how to save or inspect the source before that
    happens.

    output_dir : if given (absolute path), also writes slides.md/config.yml
    there. theme/highlight_style : same meaning as compile_presentation;
    not stored on the presentation, so pass whatever you compiled with to
    get a matching config.yml.
    """
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

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
    markdown: Optional[str] = None,
    source_path: Optional[str] = None,
) -> dict:
    """Replace a presentation's slides by parsing pandoc slide Markdown --
    the inverse of export_presentation_source. Splits on '##' (slide) and
    bare '#' (section-divider) headings, ignoring '#' inside fenced code
    blocks so a code comment is never mistaken for a heading. Use this to
    restore an exported deck or bulk-load a hand-edited Markdown file in
    one call instead of many set_slide calls.

    presentation_id must already exist (create_presentation first) --
    every slide it currently has is replaced; metadata is untouched.
    Exactly one of markdown (inline content) or source_path (absolute path
    to a .md file, e.g. one written by export_presentation_source) must be
    given.
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
        with open(source_path, "r", encoding="utf-8") as f:
            markdown = f.read()
    assert markdown is not None  # guaranteed by the exactly-one-of check above

    try:
        parsed = parse_slides_markdown(markdown)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    pres.slides.clear()
    for title, content in parsed:
        upsert_slide(pres, len(pres.slides), title, content)

    return {
        "success": True,
        "presentation_id": presentation_id,
        "slide_count": len(pres.slides),
    }


@mcp.tool
def compile_presentation(
    presentation_id: str,
    output_path: str,
    format: str,
    theme: str = "moloch",
    highlight_style: str = "tango",
    reference_doc: Optional[str] = None,
) -> dict:
    """Compile the in-memory presentation to PDF or PPTX, writing directly
    to output_path (absolute). Call render_slides_as_pngs afterwards to
    validate visually.

    format : 'pdf' (Beamer/lualatex) or 'pptx' (PowerPoint).
    theme  : any Beamer theme installed on this system (PDF only) --
    passed through to pandoc/LaTeX unvalidated, not a fixed list. Default
    'moloch' already works with no special handling; other options include
    'metropolis', 'Madrid', 'Berlin', 'default', 'Warsaw'. An invalid name
    surfaces as a LaTeX error in this tool's response -- no need to
    hand-write/compile Beamer LaTeX outside this tool for an unlisted theme.
    highlight_style : code highlight style, default 'tango'.
    reference_doc   : path to a .pptx template (PPTX only, optional).
    """
    if format not in ("pdf", "pptx"):
        return {"success": False, "error": f"format must be 'pdf' or 'pptx', got {format!r}"}
    if not os.path.isabs(output_path):
        return {"success": False, "error": f"output_path must be an absolute path, got {output_path!r}"}
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    try:
        if format == "pdf":
            compile_to_pdf(pres, output_path, theme=theme, highlight_style=highlight_style)
        else:
            compile_to_pptx(pres, output_path, reference_doc=reference_doc)
    except CompileError as exc:
        return {"success": False, "error": str(exc)}

    if not os.path.isfile(output_path):
        return {"success": False, "error": "Output file was not created by pandoc."}

    pres.artifacts[format] = output_path
    return {
        "success": True,
        "presentation_id": presentation_id,
        "format": format,
        "output_path": output_path,
        "size_bytes": os.path.getsize(output_path),
    }


@mcp.tool
def render_slides_as_pngs(
    presentation_id: str,
    format: str,
    output_dir: Optional[str] = None,
    dpi: int = 150,
) -> dict:
    """Convert a compiled artifact to a numbered PNG sequence for visual
    validation. format must match a previously compiled artifact -- PDF
    (pymupdf) and PPTX (LibreOffice headless, required, no fallback) render
    differently and can't substitute for each other. output_dir defaults
    to <artifact_path>.slides/; dpi default 150."""
    if format not in ("pdf", "pptx"):
        return {"success": False, "error": f"format must be 'pdf' or 'pptx', got {format!r}"}
    try:
        pres = _get_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    artifact_path = pres.artifacts.get(format)
    if not artifact_path:
        return {
            "success": False,
            "error": (
                f"No '{format}' artifact found for {presentation_id!r}. "
                f"Call compile_presentation(format='{format}') first."
            ),
        }
    if not os.path.isfile(artifact_path):
        return {"success": False, "error": f"Artifact missing on disk: {artifact_path!r}"}

    if output_dir is None:
        output_dir = artifact_path + ".slides"

    try:
        if format == "pdf":
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
        "format": format,
        "png_dir": output_dir,
        "png_files": png_files,
        "page_count": len(png_files),
        "dpi": dpi,
        "converter": converter,
    }


@mcp.tool
def cleanup_presentation(presentation_id: str) -> dict:
    """Delete a presentation's in-memory state and on-disk scratch dir.
    Call once its compiled artifacts (PDF/PPTX/PNGs) have been delivered --
    otherwise the workdir persists until this is called or the process
    exits."""
    try:
        _delete_pres(presentation_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "presentation_id": presentation_id}


# ===========================================================================
# GROUP B — Research & web utilities
# ===========================================================================

@mcp.tool
def academic_search(query: str, limit: int = 5) -> dict:
    """Search Semantic Scholar for academic papers. No API key required.

    Returns up to `limit` results with title, link (open-access PDF preferred),
    and truncated abstract (400 chars).
    """
    return research.academic_search(query, limit=limit)


@mcp.tool
def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web via the Tavily API.

    Requires CREPE_TAVILY_API_KEY in the environment. Returns an empty result
    with a warning field (not an error) if the key is absent, so the agent
    can surface the message gracefully.
    """
    return research.web_search(query, max_results=max_results)


@mcp.tool
def wikipedia_search(query: str, limit: int = 3) -> dict:
    """Search Wikipedia and return matching article titles, URLs, and excerpts.

    Pass the returned title to wikipedia_read to fetch the full article.
    """
    return research.wikipedia_search(query, limit=limit)


@mcp.tool
def wikipedia_read(title: str, max_chars: int = 15000) -> dict:
    """Fetch the full plain-text body of a Wikipedia article.

    `title` should be the exact article title from wikipedia_search.
    Content is truncated to `max_chars` characters (default 15 000).
    """
    return research.wikipedia_read(title, max_chars=max_chars)


@mcp.tool
def fetch_webpage(url: str, max_chars: int = 15000) -> dict:
    """Extract readable plain text from a URL (http/https only). Uses the
    browser at CREPE_HEADLESS_BROWSER_PATH (--headless=new --dump-dom) if
    set, else falls back to urllib + HTML stripping with a warning."""
    return research.fetch_webpage(url, max_chars=max_chars)


# ===========================================================================
# Entrypoint
# ===========================================================================

def main() -> None:
    """Console-script entrypoint — called by `crepe-mcp` after `uv tool install`."""
    mcp.run()


if __name__ == "__main__":
    main()
