---
name: crepe
description: Create new slide decks (PDF, PPTX), A4 reports (PDF, DOCX), Excel workbooks and draw.io exports, or search papers and the web, with the CREPE extensions. Use for any new deck, report or workbook.
---

# crepe

CREPE tools are MCP extensions. They are disabled until you enable them.

## Steps

1. Pick the extension from the table below.
2. Enable it with `manage_extensions` (action `enable`, the extension name).
3. Read the matching section of `~/.agents/skills/crepe/references/guide.md`. Find it with `rg -n '^## ' <that file>`, then read only that line range.
4. Follow the tool order in the table.
5. Disable extensions you no longer need.

## Extensions

| Task | Extension | Tool order |
|------|-----------|------------|
| Slide deck | `crepe-presentations` | `create_presentation`, `set_slide` for each slide, `lint_presentation`, `compile_presentation`, `cleanup_presentation` |
| A4 report or paper | `crepe-documents` | `create_document`, `set_chapter` and `set_section`, `lint_document`, `compile_document`, `cleanup_document` |
| Papers, web, Wikipedia | `crepe-research` | `academic_search`, `arxiv_search`, `web_search`, `wikipedia_search` then `wikipedia_read`, `fetch_webpage` |
| Excel workbook | `crepe-spreadsheets` | `create_excel`, `update_excel_sheet`, `inspect_excel` |
| draw.io diagram | `crepe-diagrams` | `lint_drawio`, `inspect_drawio`, `export_drawio` |

## Rules

- Write slide and document content in Pandoc Markdown only. No raw LaTeX.
- Give images as absolute paths.
- Always run the `lint_*` tool before compiling. Fix every issue it reports.
- Look at rendered PNGs only if you can see images.
- Save compiled files where the user asked. Call the `cleanup_*` tool afterwards.
