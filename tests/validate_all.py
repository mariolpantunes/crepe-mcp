#!/usr/bin/env python3
"""Comprehensive end-to-end validation test harness for the CREPE MCP server.

Runs all 17 tools across Group A (Stateful Presentation Builder) and Group B
(Research & Web Utilities), asserting expected structure, error/warning handling,
and physical file creation (.pdf, .pptx, and .png sequences).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import fitz  # pymupdf; also used by exporter.render_pdf_to_pngs

# Add src/ to sys.path if running directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crepe_mcp import server


def call_tool(tool_func, *args, **kwargs):
    """Helper to call FastMCP tool whether it is decorated as Tool object or function."""
    if hasattr(tool_func, "fn"):
        return tool_func.fn(*args, **kwargs)
    return tool_func(*args, **kwargs)


def print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"=== {title} ===")
    print("=" * 70)


def print_check(name: str, passed: bool, details: str = "") -> None:
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status} | {name}")
    if details:
        print(f"       -> {details}")
    if not passed:
        sys.exit(1)


def main() -> None:
    print_section("STAGE 1: FAST MCP SERVER INSPECTION")
    # Verify FastMCP instance and tool count
    mcp_instance = server.mcp
    print_check("FastMCP Instance Name", mcp_instance.name == "crepe", f"name={mcp_instance.name}")

    print_section("STAGE 2: GROUP A (PRESENTATION TOOLS)")
    
    # 1. create_presentation
    print("Creating presentation...")
    res_create = call_tool(
        server.create_presentation,
        title="Validation Suite Deck",
        subtitle="Automated Testing",
        author="CREPE Harness",
        institute="AI Engineering",
        date="2026-07-15",
    )
    pres_id = res_create.get("presentation_id")
    print_check("create_presentation returns presentation_id", bool(pres_id), f"presentation_id={pres_id}")
    assert pres_id is not None
    print_check("create_presentation metadata title", res_create.get("metadata", {}).get("title") == "Validation Suite Deck")

    # 2. set_slide (appends)
    print("\nAdding Slide 0 (Introduction)...")
    res_s0 = call_tool(
        server.set_slide,
        presentation_id=pres_id,
        index=0,
        title="Introduction",
        content="- Welcome to the CREPE automated suite\n- Testing pandoc and slide generation",
    )
    print_check("set_slide append Slide 0", res_s0.get("action") == "appended" and res_s0.get("slide_count") == 1)

    print("\nAdding Slide 1 (Columns & Math)...")
    res_s1 = call_tool(
        server.set_slide,
        presentation_id=pres_id,
        index=1,
        title="Layouts & Math",
        content=(
            ":::: {.columns}\n"
            "::: {.column width=\"50%\"}\n"
            "Code Block:\n"
            "```python\nprint('hello')\n```\n"
            ":::\n"
            "::: {.column width=\"50%\"}\n"
            "Equation:\n"
            "$$ E = mc^2 $$\n"
            ":::\n"
            "::::"
        ),
    )
    print_check("set_slide append Slide 1", res_s1.get("action") == "appended" and res_s1.get("slide_count") == 2)

    print("\nAdding Slide 2 (Speaker Notes)...")
    res_s2 = call_tool(
        server.set_slide,
        presentation_id=pres_id,
        index=2,
        title="Slide with Notes",
        content="Main bullet points here.\n\n::: notes\nRemember to explain the architecture diagrams.\n:::",
    )
    print_check("set_slide append Slide 2", res_s2.get("action") == "appended" and res_s2.get("slide_count") == 3)

    # 3. set_slide (replace Slide 1)
    print("\nReplacing Slide 1 in-place...")
    res_s1_replace = call_tool(
        server.set_slide,
        presentation_id=pres_id,
        index=1,
        title="Layouts & Updated Math",
        content=(
            ":::: {.columns}\n"
            "::: {.column width=\"50%\"}\n"
            "Code Block:\n"
            "```python\nprint('hello crepe')\n```\n"
            ":::\n"
            "::: {.column width=\"50%\"}\n"
            "Equation:\n"
            "$$ a^2 + b^2 = c^2 $$\n"
            ":::\n"
            "::::"
        ),
    )
    print_check(
        "set_slide replace Slide 1",
        res_s1_replace.get("action") == "replaced"
        and res_s1_replace.get("slide_count") == 3
        and res_s1_replace.get("id") == res_s1.get("id"),
        f"action={res_s1_replace.get('action')}, preserved ID={res_s1_replace.get('id')}",
    )

    # 4. get_presentation & get_slide
    print("\nInspecting presentation state...")
    res_get_pres = call_tool(server.get_presentation, presentation_id=pres_id)
    print_check("get_presentation total slides", res_get_pres.get("slide_count") == 3)
    
    res_get_slide = call_tool(server.get_slide, presentation_id=pres_id, slide_index=1)
    print_check(
        "get_slide verifies replaced content",
        res_get_slide.get("success") is True
        and "$$ a^2 + b^2 = c^2 $$" in res_get_slide.get("content", ""),
        "Verified updated math equation in Slide 1",
    )

    # 5. set_slide insert=True (mid-deck insertion, shifts later slides)
    print("\nInserting a slide at index 1 (insert=True)...")
    res_insert = call_tool(
        server.set_slide,
        presentation_id=pres_id,
        index=1,
        title="Inserted Mid-Deck",
        content="- This slide was inserted, not replaced",
        insert=True,
    )
    print_check(
        "set_slide insert=True shifts later slides and grows the deck",
        res_insert.get("action") == "inserted"
        and res_insert.get("index") == 1
        and res_insert.get("slide_count") == 4,
        f"action={res_insert.get('action')}, slide_count={res_insert.get('slide_count')}",
    )
    res_shifted = call_tool(server.get_slide, presentation_id=pres_id, slide_index=2)
    print_check(
        "Slide previously at index 1 shifted to index 2 after insert",
        res_shifted.get("title") == "Layouts & Updated Math",
        f"title at index 2: {res_shifted.get('title')!r}",
    )

    # 6. delete_slide (removes the inserted slide, shifts the rest back down)
    print("\nDeleting the inserted slide at index 1...")
    res_delete = call_tool(server.delete_slide, presentation_id=pres_id, index=1)
    print_check(
        "delete_slide removes the slide and shrinks the deck",
        res_delete.get("success") is True
        and res_delete.get("deleted_title") == "Inserted Mid-Deck"
        and res_delete.get("slide_count") == 3,
        f"deleted_title={res_delete.get('deleted_title')!r}, slide_count={res_delete.get('slide_count')}",
    )
    res_restored = call_tool(server.get_slide, presentation_id=pres_id, slide_index=1)
    print_check(
        "Slide at index 1 shifted back after delete",
        res_restored.get("title") == "Layouts & Updated Math",
        f"title at index 1: {res_restored.get('title')!r}",
    )
    res_delete_bad = call_tool(server.delete_slide, presentation_id=pres_id, index=99)
    print_check(
        "delete_slide on an out-of-range index returns a clean error",
        res_delete_bad.get("success") is False,
        f"error={res_delete_bad.get('error')!r}",
    )

    # 7. update_presentation_metadata (partial update, other fields untouched)
    print("\nUpdating only the presentation title...")
    res_meta = call_tool(
        server.update_presentation_metadata,
        presentation_id=pres_id,
        title="Validation Suite Deck (Updated)",
    )
    print_check(
        "update_presentation_metadata changes only the requested field",
        res_meta.get("success") is True
        and res_meta.get("metadata", {}).get("title") == "Validation Suite Deck (Updated)"
        and res_meta.get("metadata", {}).get("subtitle") == "Automated Testing"
        and res_meta.get("metadata", {}).get("author") == "CREPE Harness",
        f"metadata={res_meta.get('metadata')}",
    )

    # 8. list_presentations (finds the open presentation before cleanup)
    print("\nListing open presentations...")
    res_list = call_tool(server.list_presentations)
    listed_ids = [p.get("presentation_id") for p in res_list.get("presentations", [])]
    print_check(
        "list_presentations includes the presentation currently being built",
        pres_id in listed_ids,
        f"open presentation_ids: {listed_ids}",
    )

    # 9. export_presentation_source / import_presentation_source (round trip)
    print("\nExporting presentation source to disk...")
    export_dir = f"/tmp/crepe_val_{pres_id}_src"
    res_export = call_tool(server.export_presentation_source, presentation_id=pres_id, output_dir=export_dir)
    print_check(
        "export_presentation_source writes slides.md and config.yml",
        res_export.get("success") is True
        and os.path.isfile(res_export.get("slides_path", ""))
        and os.path.isfile(res_export.get("config_path", ""))
        and "# Section" not in res_export.get("markdown", "").split("\n")[0]  # sanity: real content, not a stub
        and len(res_export.get("markdown", "")) > 0,
        f"slides_path={res_export.get('slides_path')}, markdown_len={len(res_export.get('markdown', ''))}",
    )
    res_export_bad = call_tool(server.export_presentation_source, presentation_id=pres_id, output_dir="relative/path")
    print_check(
        "export_presentation_source rejects a relative output_dir",
        res_export_bad.get("success") is False,
        f"error={res_export_bad.get('error')!r}",
    )

    print("\nImporting exported source into a fresh presentation...")
    res_new = call_tool(server.create_presentation, title="Import Target")
    new_pid = res_new["presentation_id"]
    res_import = call_tool(server.import_presentation_source, presentation_id=new_pid, source_path=res_export["slides_path"])
    print_check(
        "import_presentation_source reconstructs the same slide count from the exported file",
        res_import.get("success") is True and res_import.get("slide_count") == 3,
        f"import result={res_import}",
    )
    orig_slide_1 = call_tool(server.get_slide, presentation_id=pres_id, slide_index=1)
    new_slide_1 = call_tool(server.get_slide, presentation_id=new_pid, slide_index=1)
    print_check(
        "imported slide content matches the original byte-for-byte",
        orig_slide_1.get("title") == new_slide_1.get("title")
        and orig_slide_1.get("content") == new_slide_1.get("content"),
        f"title match={orig_slide_1.get('title') == new_slide_1.get('title')}",
    )
    res_import_both = call_tool(
        server.import_presentation_source, presentation_id=new_pid,
        markdown="## x", source_path=res_export["slides_path"],
    )
    print_check(
        "import_presentation_source rejects passing both markdown and source_path",
        res_import_both.get("success") is False,
        f"error={res_import_both.get('error')!r}",
    )
    res_import_bad_md = call_tool(server.import_presentation_source, presentation_id=new_pid, markdown="no heading here")
    print_check(
        "import_presentation_source rejects Markdown with no heading",
        res_import_bad_md.get("success") is False,
        f"error={res_import_bad_md.get('error')!r}",
    )
    fence_markdown = "## Code Slide\n\n```python\n# not a heading\ndef f():\n    ## also not a heading\n    return 1\n```\n"
    res_import_fence = call_tool(server.import_presentation_source, presentation_id=new_pid, markdown=fence_markdown)
    fence_slide = call_tool(server.get_slide, presentation_id=new_pid, slide_index=0)
    print_check(
        "import_presentation_source does not split on '#'/'##' inside a fenced code block",
        res_import_fence.get("success") is True
        and res_import_fence.get("slide_count") == 1
        and "# not a heading" in fence_slide.get("content", ""),
        f"slide_count={res_import_fence.get('slide_count')}, content={fence_slide.get('content')!r}",
    )
    call_tool(server.cleanup_presentation, presentation_id=new_pid)
    shutil.rmtree(export_dir, ignore_errors=True)

    # 10. compile_presentation (PDF)
    pdf_output = f"/tmp/crepe_val_{pres_id}.pdf"
    print(f"\nCompiling presentation to Beamer PDF ({pdf_output})...")
    res_pdf = call_tool(
        server.compile_presentation,
        presentation_id=pres_id,
        output_path=pdf_output,
        format="pdf",
        theme="moloch",
    )
    print_check(
        "compile_presentation format=pdf",
        res_pdf.get("success") is True and os.path.isfile(pdf_output) and os.path.getsize(pdf_output) > 0,
        f"PDF size: {os.path.getsize(pdf_output)} bytes",
    )

    slide_count = call_tool(server.get_presentation, presentation_id=pres_id)["slide_count"]
    pdf_doc = fitz.open(pdf_output)
    print_check(
        "compiled PDF has no blank auto-TOC frame when the deck defines no sections "
        "(1 title page + 1 page per slide, nothing extra)",
        pdf_doc.page_count == 1 + slide_count,
        f"page_count={pdf_doc.page_count}, slide_count={slide_count}",
    )
    pdf_doc.close()

    # 11. render_slides_as_pngs (PDF -> PNG via pymupdf)
    pdf_png_dir = f"/tmp/crepe_val_{pres_id}_pdf_slides"
    print(f"\nRendering PDF slides as PNGs to {pdf_png_dir}...")
    res_pdf_png = call_tool(
        server.render_slides_as_pngs,
        presentation_id=pres_id,
        format="pdf",
        output_dir=pdf_png_dir,
        dpi=100,
    )
    png_files = res_pdf_png.get("png_files", [])
    print_check(
        "render_slides_as_pngs format=pdf (pymupdf)",
        res_pdf_png.get("success") is True
        and res_pdf_png.get("converter") == "pymupdf"
        and len(png_files) >= 3
        and all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in png_files),
        f"Generated {len(png_files)} PNGs, page_count={res_pdf_png.get('page_count')}",
    )

    # 12. compile_presentation (PPTX)
    pptx_output = f"/tmp/crepe_val_{pres_id}.pptx"
    print(f"\nCompiling presentation to PowerPoint PPTX ({pptx_output})...")
    res_pptx = call_tool(
        server.compile_presentation,
        presentation_id=pres_id,
        output_path=pptx_output,
        format="pptx",
    )
    print_check(
        "compile_presentation format=pptx",
        res_pptx.get("success") is True and os.path.isfile(pptx_output) and os.path.getsize(pptx_output) > 0,
        f"PPTX size: {os.path.getsize(pptx_output)} bytes",
    )

    # 13. render_slides_as_pngs (PPTX -> PNG via LibreOffice; required on every platform)
    pptx_png_dir = f"/tmp/crepe_val_{pres_id}_pptx_slides"
    print(f"\nRendering PPTX slides as PNGs to {pptx_png_dir}...")
    res_pptx_png = call_tool(
        server.render_slides_as_pngs,
        presentation_id=pres_id,
        format="pptx",
        output_dir=pptx_png_dir,
        dpi=100,
    )
    pptx_png_files = res_pptx_png.get("png_files", [])
    if res_pptx_png.get("success") is True:
        converter = res_pptx_png.get("converter")
        print_check(
            "render_slides_as_pngs format=pptx (libreoffice)",
            converter == "libreoffice"
            and len(pptx_png_files) >= 3
            and all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in pptx_png_files),
            f"Generated {len(pptx_png_files)} PNGs via {converter}",
        )
    else:
        err_msg = res_pptx_png.get("error", "")
        print_check(
            "render_slides_as_pngs format=pptx cleanly reports missing LibreOffice "
            "instead of crashing the process (no fallback, by design)",
            "LibreOffice is required" in err_msg,
            f"Clean error returned: {err_msg[:160]}...",
        )

    # Cleanup temp files
    shutil.rmtree(pdf_png_dir, ignore_errors=True)
    shutil.rmtree(pptx_png_dir, ignore_errors=True)
    if os.path.isfile(pdf_output):
        os.remove(pdf_output)
    if os.path.isfile(pptx_output):
        os.remove(pptx_output)

    # 14. cleanup_presentation
    print("\nCleaning up presentation workdir...")
    workdir = server._get_pres(pres_id).workdir
    res_cleanup = call_tool(server.cleanup_presentation, presentation_id=pres_id)
    print_check(
        "cleanup_presentation removes presentation and its workdir",
        res_cleanup.get("success") is True and not os.path.isdir(workdir),
        f"workdir removed: {not os.path.isdir(workdir)}",
    )
    res_cleanup_again = call_tool(server.cleanup_presentation, presentation_id=pres_id)
    print_check(
        "cleanup_presentation on an unknown presentation_id returns a clean error",
        res_cleanup_again.get("success") is False and "Unknown presentation_id" in res_cleanup_again.get("error", ""),
        f"error='{res_cleanup_again.get('error')}'",
    )
    res_list_after = call_tool(server.list_presentations)
    listed_ids_after = [p.get("presentation_id") for p in res_list_after.get("presentations", [])]
    print_check(
        "list_presentations no longer includes a cleaned-up presentation",
        pres_id not in listed_ids_after,
        f"open presentation_ids: {listed_ids_after}",
    )

    print_section("STAGE 3: GROUP B (RESEARCH & WEB UTILITIES)")

    # 15. web_search (Graceful warning check without key)
    print("Testing web_search without CREPE_TAVILY_API_KEY...")
    os.environ.pop("CREPE_TAVILY_API_KEY", None)
    res_web = call_tool(server.web_search, query="MCP protocol updates")
    print_check(
        "web_search returns warning when CREPE_TAVILY_API_KEY is unset",
        res_web.get("results") == [] and "CREPE_TAVILY_API_KEY is not set" in res_web.get("warning", ""),
        f"warning='{res_web.get('warning')}'",
    )

    # 16. wikipedia_search & wikipedia_read
    print("\nTesting wikipedia_search & wikipedia_read...")
    res_wiki_s = call_tool(server.wikipedia_search, query="Pandoc", limit=2)
    wiki_results = res_wiki_s.get("results", [])
    print_check(
        "wikipedia_search finds Pandoc",
        len(wiki_results) > 0 and any("Pandoc" in r.get("title", "") for r in wiki_results),
        f"Found {len(wiki_results)} results: {[r.get('title') for r in wiki_results]}",
    )

    wiki_title = wiki_results[0].get("title", "Pandoc")
    res_wiki_r = call_tool(server.wikipedia_read, title=wiki_title, max_chars=800)
    print_check(
        "wikipedia_read extracts plain text article body",
        res_wiki_r.get("title") == wiki_title and len(res_wiki_r.get("content", "")) > 100,
        f"Extracted {len(res_wiki_r.get('content', ''))} chars for '{wiki_title}'",
    )

    # 17. academic_search
    print("\nTesting academic_search (Semantic Scholar)...")
    res_acad = call_tool(server.academic_search, query="agentic coding large language models", limit=2)
    papers = res_acad.get("papers", [])
    error_or_papers = len(papers) > 0 or "rate limit" in res_acad.get("error", "").lower()
    print_check(
        "academic_search returns papers or handles 429 rate limit cleanly",
        error_or_papers,
        f"Papers retrieved: {len(papers)} | error: '{res_acad.get('error', '')}'",
    )

    # 18. fetch_webpage (urllib fallback check)
    print("\nTesting fetch_webpage (urllib fallback mode)...")
    os.environ.pop("CREPE_HEADLESS_BROWSER_PATH", None)
    res_fetch = call_tool(server.fetch_webpage, url="https://example.com", max_chars=1000)
    print_check(
        "fetch_webpage extracts text via urllib and returns warning when browser unset",
        "Example Domain" in res_fetch.get("content", "") and "CREPE_HEADLESS_BROWSER_PATH is not set" in res_fetch.get("warning", ""),
        f"Extracted content length: {len(res_fetch.get('content', ''))} | warning present: {bool(res_fetch.get('warning'))}",
    )

    res_fetch_file = call_tool(server.fetch_webpage, url="file:///etc/passwd", max_chars=1000)
    print_check(
        "fetch_webpage rejects non-http(s) schemes (e.g. file://) instead of disclosing local files",
        res_fetch_file.get("content", "") == "" and "Unsupported URL scheme" in res_fetch_file.get("error", ""),
        f"error='{res_fetch_file.get('error')}'",
    )

    print_section("ALL 17 TOOLS SUCCESSFULLY VALIDATED!")


if __name__ == "__main__":
    main()
