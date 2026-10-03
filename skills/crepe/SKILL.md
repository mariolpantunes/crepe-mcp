---
name: crepe
description: Build slide decks (PDF, PPTX), A4 reports (PDF, DOCX), Excel workbooks and draw.io figures, search papers and the web, and read existing PDF/DOCX/Markdown files, with the CREPE MCP tools. Use for any deck, report, workbook, diagram, literature search or file to read.
license: MIT
compatibility: Needs the crepe-* MCP servers (run setup.py --install in the crepe-mcp repo). PDF output needs pandoc and lualatex; PPTX/DOCX previews need LibreOffice; reading non-PDF files needs pandoc; diagrams need draw.io; fetch_webpage uses Chromium.
metadata:
  author: Mario Antunes
  version: "0.3.0"
---

# crepe

CREPE is six MCP servers, one per domain. Paths below are relative to this skill's folder.

## Steps

1. Pick the task in the table and read only its reference file (it starts with a `Read when:` line).
2. Make sure the server's tools are available. If they are not listed, the server is disabled: enable it (Goose: `manage_extensions`, action `enable`, the server name) or search the tool by name if your host defers tool schemas. Still missing: read `references/troubleshooting.md`.
3. Follow the tool order. Call the `lint_*` tool before every compile and fix every issue it returns.
4. Write compiled files where the user asked (absolute path), then call the `cleanup_*` tool.

## Tasks

| Task | Server | Tool order | Reference |
|------|--------|------------|-----------|
| Slide deck | `crepe-presentations` | `create_presentation`, `set_slide` per slide, `lint_presentation`, `compile_presentation`, `render_slides_as_pngs`, `cleanup_presentation` | `references/presentations.md` |
| A4 report or paper | `crepe-documents` | `create_document`, `set_chapter` and `set_section`, `lint_document`, `compile_document`, `render_document_as_pngs`, `cleanup_document` | `references/documents.md` |
| draw.io figure | `crepe-diagrams` | write the `.drawio` (or `scripts/dg.py`), `lint_drawio`, `export_drawio` | `references/diagrams.md` |
| Papers, web, Wikipedia | `crepe-research` | `academic_search` or `arxiv_search`, `web_search`, `wikipedia_search` then `wikipedia_read`, `fetch_webpage` | `references/research.md` |
| Read, review or cite an existing file (PDF, DOCX, Markdown, ...) | `crepe-reader` | `open_document`, `read_section` / `search_document` / `read_pages`, `list_assets`, `get_asset`, `render_page_png`, `document_to_markdown` | `references/reader.md` |
| Excel workbook | `crepe-spreadsheets` | `create_excel`, `update_excel_sheet`, `inspect_excel`, `markdown_table_to_excel` | `references/spreadsheets.md` |

## Rules

- Slide and document text is Pandoc Markdown only. No raw LaTeX (`\includegraphics`, `\begin{center}`, `\textbf`): it vanishes in PPTX and DOCX.
- Every image and output path is absolute, and the image exists before you compile.
- Decks and documents live in the server's memory under an id. A restarted server forgets them: keep the source with `export_presentation_source` or `export_document_source` when the work is long.
- Look at rendered PNGs only if you can see images; otherwise trust a clean lint and a successful compile.
- API keys are never typed into a chat or a config. `web_search` needs `CREPE_TAVILY_API_KEY` in the user's `.env`; without it the tool answers with a `warning` field, so report that and use another source.

## Scripts

`scripts/dg.py` builds `.drawio` figures from a JSON spec or from Python (see `references/diagrams.md`).
