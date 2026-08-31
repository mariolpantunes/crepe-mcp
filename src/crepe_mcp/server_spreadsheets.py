"""CREPE — Spreadsheets sub-server (Group E, 4 tools).

Excel workbook creation, inspection, and manipulation.

Can be run as a standalone MCP server:
    uv run crepe-spreadsheets

Or imported and mounted in the full CREPE monolith (crepe-mcp).

Tools
-----
Group E (4):
  create_excel, inspect_excel, update_excel_sheet, markdown_table_to_excel
"""
from __future__ import annotations

from fastmcp import FastMCP

from crepe_mcp import excel as _excel
from crepe_mcp.runner import run_server

SPREADSHEETS_INSTRUCTIONS = """\
CREPE Spreadsheets Engine Guidelines:
1. Use create_excel() to build multi-sheet workbooks with styled header formatting.
2. Use inspect_excel() to inspect existing workbooks, dimensions, cell contents, and formulas.
3. Use update_excel_sheet() to append rows or modify specific cells/formulas.
4. Use markdown_table_to_excel() to rapidly convert Markdown tables to .xlsx files.
"""

mcp = FastMCP("crepe-spreadsheets", instructions=SPREADSHEETS_INSTRUCTIONS)


@mcp.prompt("spreadsheet_model")
def spreadsheet_model_prompt(
    model_name: str,
    periods: int = 4,
) -> str:
    """Template for building a multi-period financial or metrics spreadsheet with CREPE."""
    return (
        f"You are creating a spreadsheet model '{model_name}' covering {periods} periods.\n\n"
        "Guidelines:\n"
        "1. Define headers with column names (e.g. ['Metric', 'Q1', 'Q2', 'Q3', 'Q4', 'Total']).\n"
        "2. Structure rows with initial data and formula strings (e.g. '=SUM(B2:E2)').\n"
        "3. Call create_excel(output_path=..., sheets=[...]) to generate the workbook.\n"
        "4. Validate workbook layout with inspect_excel()."
    )

@mcp.tool
def create_excel(
    output_path: str,
    sheets: list[dict] | None = None,
    overwrite: bool = True,
) -> dict:
    """Create a new Excel (.xlsx) workbook with styled header rows and data rows."""
    return _excel.create_excel(output_path=output_path, sheets=sheets, overwrite=overwrite)


@mcp.tool
def inspect_excel(input_path: str, max_rows: int = 100) -> dict:
    """Inspect an existing .xlsx file and return sheet names, dimensions, cell values, and formulas."""
    return _excel.inspect_excel(input_path=input_path, max_rows=max_rows)


@mcp.tool
def update_excel_sheet(
    input_path: str,
    sheet_name: str,
    append_rows: list[list] | None = None,
    update_cells: dict | None = None,
) -> dict:
    """Update an existing .xlsx sheet by appending rows or setting cell values and formulas."""
    return _excel.update_excel_sheet(
        input_path=input_path, sheet_name=sheet_name, append_rows=append_rows, update_cells=update_cells
    )


@mcp.tool
def markdown_table_to_excel(
    markdown_table: str,
    output_path: str,
    sheet_name: str = "Data",
) -> dict:
    """Convert a GitHub Flavored Markdown table string into a styled Excel workbook."""
    return _excel.markdown_table_to_excel(markdown_table=markdown_table, output_path=output_path, sheet_name=sheet_name)

def main() -> None:
    """Console-script entrypoint for the standalone crepe-spreadsheets server."""
    run_server(mcp)


if __name__ == "__main__":
    main()
