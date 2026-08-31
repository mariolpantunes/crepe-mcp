"""CREPE — Diagrams sub-server (Group C + lint_drawio, 3 tools).

draw.io diagram export and validation.

Can be run as a standalone MCP server:
    uv run crepe-diagrams

Or imported and mounted in the full CREPE monolith (crepe-mcp).

Tools
-----
Group C (2):
  export_drawio, inspect_drawio

Group F (1):
  lint_drawio

Environment variables:
  CREPE_DRAWIO_PATH — path to draw.io executable (auto-detected if absent)
"""
from __future__ import annotations

from fastmcp import FastMCP

from crepe_mcp import drawio as _drawio
from crepe_mcp import linter as _linter
from crepe_mcp.runner import run_server

DIAGRAMS_INSTRUCTIONS = """\
CREPE Diagrams Engine Guidelines:
1. Valid .drawio XML must include base root cells (<mxCell id="0"/> and <mxCell id="1" parent="0"/>).
2. Call inspect_drawio() or lint_drawio() to validate XML structure and retrieve page names before export.
3. Use export_drawio() to rasterize diagrams to PNG (transparent=True recommended for clean slide embedding) or SVG.
"""

mcp = FastMCP("crepe-diagrams", instructions=DIAGRAMS_INSTRUCTIONS)


@mcp.prompt("drawio_diagram")
def drawio_diagram_prompt(
    system_name: str,
    diagram_type: str = "architecture",
) -> str:
    """Template for designing and exporting a Draw.io architecture or flow diagram."""
    return (
        f"You are creating a Draw.io {diagram_type} diagram for '{system_name}'.\n\n"
        "Guidelines:\n"
        "1. Structure XML with <mxfile><diagram name='Page-1'><mxGraphModel>"
        "<root><mxCell id='0'/><mxCell id='1' parent='0'/>...\n"
        "2. Add nodes with clear labels, shapes, and connection edges.\n"
        "3. Validate diagram with lint_drawio(input_path=...).\n"
        "4. Export to PNG for slides using export_drawio(transparent=True, scale=2.0)."
    )

@mcp.tool
def export_drawio(
    input_path: str,
    output_path: str,
    output_format: str = "png",
    page_index: int = 1,
    all_pages: bool = False,
    transparent: bool = True,
    scale: float = 1.0,
    border: int = 0,
    embed_diagram: bool = False,
) -> dict:
    """Export a .drawio file to an image or document.

    input_path   : absolute path to the .drawio / .xml source file.
    output_path  : absolute path for the output file.
    output_format: 'png' (default), 'pdf', 'svg', 'jpg', 'html', or 'xml'.
    page_index   : 1-based page to export (ignored when all_pages=True).
    all_pages    : export all pages (pdf and html only).
    transparent  : transparent background (default True for png/svg; ensures clean embedding without white backgrounds).
    scale        : scale factor (e.g. 2.0 for 2x resolution).
    border       : border width in pixels around the diagram.
    embed_diagram: embed diagram source in output (png, svg, pdf only).
    """
    return _drawio.export_drawio(
        input_path=input_path,
        output_path=output_path,
        output_format=output_format,
        page_index=page_index,
        all_pages=all_pages,
        transparent=transparent,
        scale=scale,
        border=border,
        embed_diagram=embed_diagram,
    )


@mcp.tool
def inspect_drawio(input_path: str) -> dict:
    """Validate a .drawio file and return its pages.

    Parses the XML without invoking draw.io — works offline.
    Use this before export_drawio to confirm the file is valid
    and to discover available page names and indices.
    input_path : absolute path to the .drawio / .xml file.
    """
    return _drawio.inspect_drawio(input_path)
@mcp.tool
def lint_drawio(input_path: str) -> dict:
    """Validate a .drawio file structure without invoking draw.io. Call before export_drawio.

    Checks XML validity, compressed content, base cells (id=0, id=1), and layer hierarchy.
    Returns {valid, page_count, pages, issues: [{type, message, page_name}]}.
    """
    report = _linter.lint_drawio_file(input_path)
    return report.to_dict()

def main() -> None:
    """Console-script entrypoint for the standalone crepe-diagrams server."""
    run_server(mcp)


if __name__ == "__main__":
    main()
