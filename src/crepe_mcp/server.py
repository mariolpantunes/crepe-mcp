"""CREPE — Compile, Research, Export, Presentation Engine (full monolith).

This is the all-in-one entry point that mounts all five CREPE sub-servers
and exposes their combined 40 tools through a single MCP stdio endpoint.

Sub-servers (importable and runnable independently):
  crepe-presentations  — Group A (14) + lint_presentation (1) = 15 tools
  crepe-documents      — Group D (11) + lint_document (1)      = 12 tools
  crepe-research       — Group B (6)                            = 6 tools
  crepe-spreadsheets   — Group E (4)                            = 4 tools
  crepe-diagrams       — Group C (2) + lint_drawio (1)          = 3 tools

All tool names are unchanged — no namespace prefix is applied, so existing
agent workflows that used the monolith continue to work without modification.

Environment variables (all CREPE_ prefixed):
  CREPE_TAVILY_API_KEY            — Tavily API key for web_search
  CREPE_SEMANTIC_SCHOLAR_API_KEY  — optional; avoids 429 rate limits
  CREPE_HEADLESS_BROWSER_PATH     — Chromium path for fetch_webpage
  CREPE_DRAWIO_PATH               — draw.io executable
  CREPE_LIBREOFFICE_PATH          — soffice/libreoffice executable
"""
from __future__ import annotations

from fastmcp import FastMCP

from crepe_mcp import doc_store as _doc_store
from crepe_mcp import research
from crepe_mcp.runner import run_server
from crepe_mcp.server_diagrams import (
    export_drawio,
    inspect_drawio,
    lint_drawio,
)
from crepe_mcp.server_diagrams import (
    mcp as _diagrams_mcp,
)
from crepe_mcp.server_documents import (
    cleanup_document,
    compile_document,
    create_document,
    delete_chapter,
    export_document_source,
    get_document,
    lint_document,
    list_documents,
    render_document_as_pngs,
    set_chapter,
    set_section,
    update_document_metadata,
)
from crepe_mcp.server_documents import (
    mcp as _documents_mcp,
)
from crepe_mcp.server_presentations import (
    cleanup_presentation,
    compile_presentation,
    create_presentation,
    delete_slide,
    duplicate_presentation,
    export_presentation_source,
    get_presentation,
    get_slide,
    import_presentation_source,
    lint_presentation,
    list_presentations,
    move_slide,
    render_slides_as_pngs,
    set_slide,
    update_presentation_metadata,
)
from crepe_mcp.server_presentations import (
    mcp as _presentations_mcp,
)
from crepe_mcp.server_research import (
    academic_search,
    arxiv_search,
    fetch_webpage,
    web_search,
    wikipedia_read,
    wikipedia_search,
)
from crepe_mcp.server_research import (
    mcp as _research_mcp,
)
from crepe_mcp.server_spreadsheets import (
    create_excel,
    inspect_excel,
    markdown_table_to_excel,
    update_excel_sheet,
)
from crepe_mcp.server_spreadsheets import (
    mcp as _spreadsheets_mcp,
)
from crepe_mcp.store import get_presentation as _get_pres

MONOLITH_INSTRUCTIONS = """\
CREPE — Compile, Research, Export, Presentation Engine:
1. Presentations (Group A): build slide decks in Pandoc Markdown, validate with lint_presentation, compile to PDF/PPTX.
2. Documents (Group D): build A4 reports/papers, validate with lint_document, compile to PDF/DOCX.
3. Research (Group B): search Semantic Scholar, arXiv, web (Tavily), Wikipedia, and fetch webpages.
4. Spreadsheets (Group E): create, inspect, and update styled Excel workbooks (.xlsx).
5. Diagrams (Group C): validate with lint_drawio and export draw.io diagrams to PNG/SVG/PDF.
Always run the matching lint_* tool before compiling artifacts.
"""

mcp = FastMCP("crepe", instructions=MONOLITH_INSTRUCTIONS, lifespan=research.browser_lifespan)

# Mount all sub-servers without a namespace prefix so tool names remain
# identical to the previous monolith (backward-compatible).
mcp.mount(_presentations_mcp)
mcp.mount(_documents_mcp)
mcp.mount(_research_mcp)
mcp.mount(_spreadsheets_mcp)
mcp.mount(_diagrams_mcp)


__all__ = [
    "_doc_store",
    "_get_pres",
    "academic_search",
    "arxiv_search",
    "cleanup_document",

    "cleanup_presentation",
    "compile_document",
    "compile_presentation",
    "create_document",
    "create_excel",
    "create_presentation",
    "delete_chapter",
    "delete_slide",
    "duplicate_presentation",
    "export_document_source",
    "export_drawio",
    "export_presentation_source",
    "fetch_webpage",
    "get_document",
    "get_presentation",
    "get_slide",
    "import_presentation_source",
    "inspect_excel",
    "inspect_drawio",
    "lint_document",
    "lint_drawio",
    "lint_presentation",
    "list_documents",
    "list_presentations",
    "main",
    "markdown_table_to_excel",
    "mcp",
    "move_slide",
    "render_document_as_pngs",
    "render_slides_as_pngs",
    "set_chapter",
    "set_section",
    "set_slide",
    "update_document_metadata",
    "update_excel_sheet",
    "update_presentation_metadata",
    "web_search",
    "wikipedia_read",
    "wikipedia_search",
]


def main() -> None:
    """Console-script entrypoint — called by `crepe-mcp` after `uv tool install`."""
    run_server(mcp)


if __name__ == "__main__":
    main()
