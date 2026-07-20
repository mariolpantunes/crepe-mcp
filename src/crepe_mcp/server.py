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
    """Create a new, empty presentation and return its presentation_id.

    All title-slide metadata is set here. Every other Group-A tool requires
    the presentation_id returned by this call.

    Parameters
    ----------
    title     : Main title shown on the title slide.
    subtitle  : Optional subtitle.
    author    : Author name (default: Mário Antunes).
    institute : Institution name (default: Universidade de Aveiro).
    date      : Date string shown on the title slide (default: 2026).
    """
    pres = new_presentation(
        title=title, subtitle=subtitle, author=author,
        institute=institute, date=date,
    )
    return {"presentation_id": pres.id, "metadata": vars(pres.metadata)}


@mcp.tool
def get_presentation(presentation_id: str) -> dict:
    """Return the current state of a presentation.

    Returns metadata, an ordered slide list (index, id, title, 200-char
    content preview), and which compiled artifacts exist.
    """
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

    insert=False (default):
      index < current slide count  → replace the slide at that position.
      index >= current slide count → append a new slide at the end.

    insert=True:
      Inserts a new slide at index, shifting that slide and everything
      after it one position later. index >= current slide count still
      appends. Combine with delete_slide to move a slide to a new
      position (delete it, then insert it elsewhere).

    `content` is the raw Pandoc Markdown body for the slide. Supported syntax:

    Bullet list          : - item
    Incremental bullets  : > - item
    Math block           : $$ E = mc^2 $$
    Inline math          : $f(x)$
    Code block           : ```python\\ncode\\n```
    Image                : ![caption](/absolute/path/to/image.png)
    Speaker notes        : ::: notes\\ntext\\n:::
    Section divider (TOC): # Heading as the only content line

    Multi-column layout (embed fenced-div directly in content):

        :::: {.columns}
        ::: {.column width="50%"}
        Left content
        :::
        ::: {.column width="50%"}
        Right content
        :::
        ::::
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
    """Update title-slide metadata on an existing presentation.

    Only fields passed a value are changed; omitted (None) fields keep
    their current value. Use this instead of recreating a presentation
    just to fix a typo in the title after slides have already been added.
    """
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
    """List every presentation currently held in memory.

    Presentations stay in memory (and keep their on-disk scratch dir)
    until cleanup_presentation is called or the process exits. Use this
    to recover a presentation_id you've lost track of, or to find stale
    presentations worth cleaning up.
    """
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
    """Return the exact pandoc source this presentation compiles from.

    Presentations only live in memory -- state is lost on server restart or
    cleanup_presentation, with no other durable copy. This returns the same
    slides Markdown and config.yml metadata/theme block that
    compile_presentation actually feeds to pandoc (byte-for-byte, since it's
    built with the same functions), so it can be inspected, diffed, or saved
    outside the tool before that state disappears.

    Parameters
    ----------
    output_dir      : If given (absolute path), also writes slides.md and
                       config.yml there (directory created if needed).
    theme / highlight_style : Same meaning as compile_presentation. Not
                       stored on the presentation -- pass whatever you last
                       compiled with to get a matching config.yml.
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
    """Replace a presentation's slides by parsing pandoc slide Markdown.

    Reverses export_presentation_source: splits the given Markdown on '##'
    (slide) and bare '#' (section-divider) headings -- ignoring '#'
    characters inside fenced code blocks, so a comment like '# TODO' in a
    code sample is never mistaken for a heading -- and replaces every slide
    currently in the presentation with the parsed result. Use this to
    restore a deck exported earlier, or to bulk-load a hand-edited Markdown
    file in one call instead of many set_slide calls.

    presentation_id must already exist (call create_presentation first);
    this populates an existing presentation rather than creating a new one.
    Metadata (title/author/...) is untouched -- use
    update_presentation_metadata separately if needed.

    Exactly one of markdown or source_path must be given.

    markdown    : Inline Markdown content.
    source_path : Absolute path to a .md file to read instead (e.g. one
                  written by export_presentation_source, or a hand-edited
                  copy of one).
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
    """Compile the in-memory presentation to PDF or PPTX.

    Writes the output directly to `output_path`. Call render_slides_as_pngs
    afterwards to produce a PNG sequence for visual validation.

    Parameters
    ----------
    output_path     : Absolute destination path.
    format          : 'pdf' → Beamer/lualatex. 'pptx' → PowerPoint.
    theme           : Any Beamer theme name installed on this system (PDF
                      only) -- passed through to pandoc/LaTeX unvalidated,
                      not limited to a fixed list. Defaults to 'moloch'
                      (a Metropolis-inspired dark theme), which is already
                      the default and needs no special handling to use.
                      A few other commonly available names: 'default',
                      'metropolis', 'Madrid', 'Berlin', 'Warsaw',
                      'AnnArbor'. If a theme name isn't installed, pandoc's
                      LaTeX error will say so in this tool's error message
                      -- there's no need to hand-write Beamer LaTeX and
                      compile it outside this tool to use an unlisted
                      theme; just pass its name here.
    highlight_style : Code highlight style (PDF only, default 'tango').
    reference_doc   : Path to a .pptx template (PPTX only, optional).
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
    """Convert a compiled artifact to a PNG sequence for visual validation.

    The PNG output reflects the actual compiled format — Beamer PDF and PPTX
    look completely different and cannot substitute for each other.

    Parameters
    ----------
    format     : 'pdf' or 'pptx' — must match a previously compiled artifact.
    output_dir : Directory to write PNGs. Defaults to <artifact_path>.slides/
    dpi        : Render resolution (default 150).

    PDF  path : pymupdf — pure Python, no system deps.
    PPTX path : LibreOffice headless (required — see README for install
                instructions on your platform).
    """
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
    """Delete a presentation's in-memory state and its on-disk scratch directory.

    Call this once a deck's compiled artifacts (PDF/PPTX/PNGs) have been
    delivered to the user — the server keeps every presentation's workdir on
    disk until this is called or the process exits.
    """
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
    """Extract readable plain text from a URL.

    Uses the browser at CREPE_HEADLESS_BROWSER_PATH (--headless=new --dump-dom)
    when set. Falls back to urllib + HTML stripping with a warning if the
    variable is unset or points to a missing file.

    Example values for CREPE_HEADLESS_BROWSER_PATH:
      /usr/bin/chromium
      /usr/bin/google-chrome
      /Applications/Brave Browser.app/Contents/MacOS/Brave Browser
    """
    return research.fetch_webpage(url, max_chars=max_chars)


# ===========================================================================
# Entrypoint
# ===========================================================================

def main() -> None:
    """Console-script entrypoint — called by `crepe-mcp` after `uv tool install`."""
    mcp.run()


if __name__ == "__main__":
    main()
