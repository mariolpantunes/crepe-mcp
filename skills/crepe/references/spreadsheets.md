Read when: creating, reading or editing an Excel workbook (server `crepe-spreadsheets`, 4 tools).

# Spreadsheets

All paths are absolute.

| Tool | Use |
|------|-----|
| `create_excel(output_path, sheets, overwrite=true)` | New workbook with styled header rows. |
| `inspect_excel(input_path, max_rows=100)` | Sheet names, dimensions, cell values and formulas of an existing file. |
| `update_excel_sheet(input_path, sheet_name, append_rows?, update_cells?)` | Append rows or set cells. The sheet must exist. |
| `markdown_table_to_excel(markdown_table, output_path, sheet_name="Data")` | One GitHub pipe table to a styled workbook. |

## Shapes

`sheets` is a list of objects; a cell that is a string starting with `=` becomes a formula:

```json
[{"name": "Metrics", "headers": ["Region", "Output"],
  "rows": [["North", 120], ["South", 95], ["Total", "=SUM(B2:B3)"]]}]
```

`update_excel_sheet`: `append_rows` is `[["East", 77]]`; `update_cells` maps A1 coordinates, `{"B2": 42, "C2": "=SUM(A1:B1)"}`.

## Rules

- Inspect before updating: read the real sheet names and the last used row.
- `create_excel` overwrites an existing file unless `overwrite=false`.
- Formulas are stored, not calculated: `inspect_excel` returns the formula text, never its result, and a file you just wrote has no computed values until a spreadsheet application opens it.
