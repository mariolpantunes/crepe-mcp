#!/usr/bin/env python3
"""Comprehensive end-to-end validation test harness for the CREPE MCP server.

Runs all 12 tools across Group A (Stateful Presentation Builder) and Group B
(Research & Web Utilities), asserting expected structure, error/warning handling,
and physical file creation (.pdf, .pptx, and .png sequences).
"""
from __future__ import annotations

import os
os.environ.setdefault("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT", "1")
import shutil
import sys
from pathlib import Path

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

    # 5. compile_presentation (PDF)
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

    # 6. render_slides_as_pngs (PDF -> PNG via pymupdf)
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

    # 7. compile_presentation (PPTX)
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

    # 8. render_slides_as_pngs (PPTX -> PNG via LibreOffice, or aspose-slides fallback)
    pptx_png_dir = f"/tmp/crepe_val_{pres_id}_pptx_slides"
    print(f"\nRendering PPTX slides as PNGs to {pptx_png_dir}...")
    # Make sure ASPOSE license is not set to verify warning return on the aspose fallback path
    os.environ.pop("CREPE_ASPOSE_LICENSE_PATH", None)
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
            "render_slides_as_pngs format=pptx (libreoffice or aspose-slides)",
            converter in ("libreoffice", "aspose-slides")
            and len(pptx_png_files) >= 3
            and all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in pptx_png_files),
            f"Generated {len(pptx_png_files)} PNGs via {converter}",
        )
        if converter == "aspose-slides":
            print_check(
                "Aspose evaluation warning returned when CREPE_ASPOSE_LICENSE_PATH is unset",
                "Aspose is running in evaluation mode" in res_pptx_png.get("warning", ""),
                f"warning='{res_pptx_png.get('warning')}'",
            )
        else:
            print_check(
                "LibreOffice path returns no evaluation/watermark warning",
                res_pptx_png.get("warning") is None,
                f"warning={res_pptx_png.get('warning')!r}",
            )
    else:
        err_msg = res_pptx_png.get("error", "")
        print_check(
            "render_slides_as_pngs format=pptx cleanly reports missing LibreOffice on Linux "
            "instead of crashing the process (no aspose fallback on Linux by design)",
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

    # 9. cleanup_presentation
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

    print_section("STAGE 3: GROUP B (RESEARCH & WEB UTILITIES)")

    # 10. web_search (Graceful warning check without key)
    print("Testing web_search without CREPE_TAVILY_API_KEY...")
    os.environ.pop("CREPE_TAVILY_API_KEY", None)
    res_web = call_tool(server.web_search, query="MCP protocol updates")
    print_check(
        "web_search returns warning when CREPE_TAVILY_API_KEY is unset",
        res_web.get("results") == [] and "CREPE_TAVILY_API_KEY is not set" in res_web.get("warning", ""),
        f"warning='{res_web.get('warning')}'",
    )

    # 11. wikipedia_search & wikipedia_read
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

    # 12. academic_search
    print("\nTesting academic_search (Semantic Scholar)...")
    res_acad = call_tool(server.academic_search, query="agentic coding large language models", limit=2)
    papers = res_acad.get("papers", [])
    error_or_papers = len(papers) > 0 or "rate limit" in res_acad.get("error", "").lower()
    print_check(
        "academic_search returns papers or handles 429 rate limit cleanly",
        error_or_papers,
        f"Papers retrieved: {len(papers)} | error: '{res_acad.get('error', '')}'",
    )

    # 13. fetch_webpage (urllib fallback check)
    print("\nTesting fetch_webpage (urllib fallback mode)...")
    os.environ.pop("CREPE_HEADLESS_BROWSER_PATH", None)
    res_fetch = call_tool(server.fetch_webpage, url="https://example.com", max_chars=1000)
    print_check(
        "fetch_webpage extracts text via urllib and returns warning when browser unset",
        "Example Domain" in res_fetch.get("content", "") and "CREPE_HEADLESS_BROWSER_PATH is not set" in res_fetch.get("warning", ""),
        f"Extracted content length: {len(res_fetch.get('content', ''))} | warning present: {bool(res_fetch.get('warning'))}",
    )

    print_section("ALL 12 TOOLS SUCCESSFULLY VALIDATED!")


if __name__ == "__main__":
    main()
