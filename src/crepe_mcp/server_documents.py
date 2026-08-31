"""CREPE — Documents sub-server (Group D + lint_document, 12 tools).

A4 report/article/paper builder. Manages in-memory document state and compiles
to PDF (via Pandoc/lualatex) or Word DOCX.

Can be run as a standalone MCP server:
    uv run crepe-documents

Or imported and mounted in the full CREPE monolith (crepe-mcp).

Tools
-----
Group D (11):
  create_document, get_document, set_chapter, set_section, delete_chapter,
  update_document_metadata, export_document_source, list_documents,
  compile_document, render_document_as_pngs, cleanup_document

Group F (1):
  lint_document
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from crepe_mcp import doc_compiler as _doc_compiler
from crepe_mcp import doc_exporter as _doc_exporter
from crepe_mcp import doc_store as _doc_store
from crepe_mcp import linter as _linter
from crepe_mcp.runner import run_server

DOCUMENT_INSTRUCTIONS = """\
CREPE Documents Engine Guidelines:
1. Document content (intro in set_chapter, content in set_section) must be strictly Pandoc Markdown.
2. Structure: Chapters are level 1 headings (#), Sections are level 2 (##), Subsections are level 3 (###).
3. Do NOT use raw LaTeX environments (\\begin{center}, \\includegraphics, etc.). Use Markdown figures and tables.
4. Images must use absolute paths: ![caption](/absolute/path/to/image.png).
5. Always call lint_document() before compile_document() to verify syntax and image paths.
"""

mcp = FastMCP("crepe-documents", instructions=DOCUMENT_INSTRUCTIONS)


@mcp.resource("document://{document_id}/source")
def get_document_markdown_resource(document_id: str) -> str:
    """Return the live Pandoc Markdown source for the specified document."""
    doc = _doc_store.get_document(document_id)
    with doc.lock:
        return _doc_compiler.build_document_markdown(doc)


@mcp.resource("document://{document_id}/config")
def get_document_config_resource(document_id: str) -> str:
    """Return the live YAML configuration for the specified document."""
    doc = _doc_store.get_document(document_id)
    with doc.lock:
        return _doc_compiler.build_doc_config_yaml(doc)


@mcp.prompt("technical_report")
def technical_report_prompt(
    title: str,
    topic: str,
    chapter_count: int = 4,
) -> str:
    """Template for drafting a structured technical report or article with CREPE."""
    return (
        f"You are writing a technical report titled '{title}' on the topic '{topic}' with {chapter_count} chapters.\n\n"
        "Instructions:\n"
        "1. Create the document using create_document(title=...).\n"
        "2. Add chapters with set_chapter() and detailed subsections with set_section().\n"
        "3. Format text using Pandoc Markdown, GFM tables, math equations ($$...$$), and figures.\n"
        "4. Validate content with lint_document().\n"
        "5. Compile with compile_document(output_format='pdf') and render PNGs with render_document_as_pngs()."
    )

@mcp.tool
def create_document(
    title: str = "Untitled Document",
    subtitle: str = "",
    author: str = "Mário Antunes",
    institute: str = "Universidade de Aveiro",
    date: str = "2026",
    abstract: str = "",
    paper_size: str = "a4paper",
    margin: str = "2.5cm",
    font_size: str = "11pt",
    toc: bool = True,
    number_sections: bool = True,
) -> dict:
    """Create a new A4 document project (report/article/paper)."""
    doc = _doc_store.new_document(
        title=title, subtitle=subtitle, author=author, institute=institute,
        date=date, abstract=abstract, paper_size=paper_size, margin=margin,
        font_size=font_size, toc=toc, number_sections=number_sections,
    )
    return {"document_id": doc.id, "metadata": vars(doc.metadata)}


@mcp.tool
def get_document(document_id: str) -> dict:
    """Get metadata, chapter/section hierarchy, and artifact paths of a document."""
    try:
        doc = _doc_store.get_document(document_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    with doc.lock:
        chapters_summary = [
            {
                "index": i,
                "id": ch.id,
                "title": ch.title,
                "section_count": len(ch.sections),
                "sections": [{"index": sj, "title": s.title, "level": s.level} for sj, s in enumerate(ch.sections)],
            }
            for i, ch in enumerate(doc.chapters)
        ]
        artifact_keys = list(doc.artifacts.keys())
        meta = dict(vars(doc.metadata))
        chapter_count = len(doc.chapters)
    return {
        "document_id": document_id,
        "metadata": meta,
        "chapter_count": chapter_count,
        "chapters": chapters_summary,
        "artifacts": artifact_keys,
    }


@mcp.tool
def set_chapter(
    document_id: str,
    chapter_index: int,
    title: str,
    intro: str = "",
) -> dict:
    """Add or replace a top-level Chapter (# Chapter Title) in an A4 document.

    `intro` is Pandoc Markdown. Always use standard Markdown image tags `![](path)`
    instead of raw LaTeX `\\includegraphics` to guarantee compatibility across PDF and DOCX outputs.
    """
    try:
        doc = _doc_store.get_document(document_id)
        ch, action, actual_idx, warnings = _doc_store.set_chapter(doc, chapter_index, title, intro)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    res = {
        "success": True,
        "document_id": document_id,
        "action": action,
        "chapter_index": actual_idx,
        "chapter_id": ch.id,
        "title": ch.title,
    }
    if warnings:
        res["warnings"] = warnings
    return res


@mcp.tool
def set_section(
    document_id: str,
    chapter_index: int,
    section_index: int,
    title: str,
    content: str,
    level: int = 2,
) -> dict:
    """Add or replace a section (level 2: ## or level 3: ###) within a document chapter.

    `content` is Pandoc Markdown. Always use standard Markdown image tags `![](path)`
    instead of raw LaTeX `\\includegraphics` to guarantee compatibility across PDF and DOCX outputs.
    """
    try:
        doc = _doc_store.get_document(document_id)
        sec, action, actual_idx, warnings = _doc_store.set_section(
            doc, chapter_index, section_index, title, content, level=level
        )
    except (ValueError, IndexError) as exc:
        return {"success": False, "error": str(exc)}


    res = {
        "success": True,
        "document_id": document_id,
        "chapter_index": chapter_index,
        "action": action,
        "section_index": actual_idx,
        "section_id": sec.id,
        "title": sec.title,
    }
    if warnings:
        res["warnings"] = warnings
    return res


@mcp.tool
def delete_chapter(document_id: str, chapter_index: int) -> dict:
    """Remove a chapter and its sections from an A4 document."""
    try:
        doc = _doc_store.get_document(document_id)
        ch = _doc_store.delete_chapter(doc, chapter_index)
    except (ValueError, IndexError) as exc:
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "document_id": document_id,
        "deleted_chapter_id": ch.id,
        "deleted_title": ch.title,
        "chapter_count": len(doc.chapters),
    }


@mcp.tool
def update_document_metadata(
    document_id: str,
    title: str | None = None,
    subtitle: str | None = None,
    author: str | None = None,
    institute: str | None = None,
    date: str | None = None,
    abstract: str | None = None,
    paper_size: str | None = None,
    margin: str | None = None,
    font_size: str | None = None,
    toc: bool | None = None,
    number_sections: bool | None = None,
) -> dict:
    """Update metadata settings on an existing document."""
    try:
        doc = _doc_store.get_document(document_id)
        meta = _doc_store.update_document_metadata(
            doc, title=title, subtitle=subtitle, author=author, institute=institute,
            date=date, abstract=abstract, paper_size=paper_size, margin=margin,
            font_size=font_size, toc=toc, number_sections=number_sections,
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    return {"success": True, "document_id": document_id, "metadata": vars(meta)}


@mcp.tool
def export_document_source(document_id: str, output_path: str | None = None) -> dict:
    """Export the assembled document Markdown source string."""
    try:
        doc = _doc_store.get_document(document_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    with doc.lock:
        markdown = _doc_store.build_document_markdown(doc)
    res = {"success": True, "document_id": document_id, "markdown": markdown}
    if output_path:
        if not os.path.isabs(output_path):
            return {"success": False, "error": f"output_path must be absolute, got {output_path!r}"}
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        res["output_path"] = output_path
    return res


@mcp.tool
def list_documents() -> dict:
    """List every document currently in memory."""
    docs = []
    for d in _doc_store.list_documents():
        with d.lock:
            docs.append({
                "document_id": d.id,
                "title": d.metadata.title,
                "chapter_count": len(d.chapters),
                "artifacts": list(d.artifacts.keys()),
            })
    return {"documents": docs}


@mcp.tool
def compile_document(
    document_id: str,
    output_path: str,
    output_format: str,
    reference_doc: str | None = None,
) -> dict:
    """Compile an A4 document project to PDF (via Pandoc/lualatex) or Word .docx
    (with optional reference_doc template).
    """
    if output_format not in ("pdf", "docx"):
        return {"success": False, "error": f"output_format must be 'pdf' or 'docx', got {output_format!r}"}
    if not os.path.isabs(output_path):
        return {"success": False, "error": f"output_path must be absolute, got {output_path!r}"}

    try:
        doc = _doc_store.get_document(document_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    try:
        if output_format == "pdf":
            _doc_compiler.compile_document_to_pdf(doc, output_path)
        else:
            _doc_compiler.compile_document_to_docx(doc, output_path, reference_doc=reference_doc)
    except _doc_compiler.DocCompileError as exc:
        return {"success": False, "error": str(exc)}

    if not os.path.isfile(output_path):
        return {"success": False, "error": "Output document was not created by Pandoc."}

    with doc.lock:
        doc.artifacts[output_format] = output_path
    return {
        "success": True,
        "document_id": document_id,
        "output_format": output_format,
        "output_path": output_path,
        "size_bytes": os.path.getsize(output_path),
    }


@mcp.tool
def render_document_as_pngs(
    document_id: str,
    output_format: str,
    output_dir: str | None = None,
    dpi: int = 150,
) -> dict:
    """Render every page of a compiled document artifact (pdf or docx) to a numbered PNG sequence for visual check."""
    if output_format not in ("pdf", "docx"):
        return {"success": False, "error": f"output_format must be 'pdf' or 'docx', got {output_format!r}"}

    try:
        doc = _doc_store.get_document(document_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    with doc.lock:
        artifact_path = doc.artifacts.get(output_format)
    if not artifact_path or not os.path.isfile(artifact_path):
        return {
            "success": False,
            "error": (
                f"No compiled '{output_format}' artifact found for {document_id!r}. "
                "Call compile_document first."
            ),
        }

    if output_dir is None:
        output_dir = artifact_path + ".pages"

    try:
        png_files, converter = _doc_exporter.render_document_as_pngs(artifact_path, output_dir, dpi=dpi)
    except Exception as exc:
        return {"success": False, "error": f"Document page rendering failed: {exc}"}

    return {
        "success": True,
        "document_id": document_id,
        "output_format": output_format,
        "png_dir": output_dir,
        "png_files": png_files,
        "page_count": len(png_files),
        "converter": converter,
    }


@mcp.tool
def cleanup_document(document_id: str) -> dict:
    """Purge document state and remove workdir from disk."""
    try:
        _doc_store.delete_document(document_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "document_id": document_id}


@mcp.tool
def lint_document(
    document_id: str,
    chapter_index: int | None = None,
) -> dict:
    """Validate document chapter/section content without compiling. Call before compile_document.

    Checks for forbidden LaTeX, broken image paths, and pandoc parse errors.
    chapter_index: if given, checks only that chapter; otherwise checks all.
    Returns {valid, chapter_count, issues: [{type, message, line, chapter_index, section_index}]}.
    """
    try:
        doc = _doc_store.get_document(document_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    report = _linter.lint_document_content(doc, chapter_index=chapter_index)
    result = report.to_dict()
    result["document_id"] = document_id
    result["chapter_count"] = len(doc.chapters)
    return result


def main() -> None:
    """Console-script entrypoint for the standalone crepe-documents server."""
    run_server(mcp)


if __name__ == "__main__":
    main()
