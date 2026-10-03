Read when: a CREPE tool is missing, a compile fails, a call returns an error, or the user asks how CREPE is installed.

# Troubleshooting

## The tools are not there

CREPE is five MCP servers: `crepe-presentations`, `crepe-documents`, `crepe-research`, `crepe-spreadsheets`, `crepe-diagrams`. Each runs from `<crepe-mcp repo>/venv/bin/<name>`.

- Goose gates them: only `crepe-research` is on by default, the rest are `enabled: false` with a description. Turn one on with `manage_extensions` (action `enable`).
- Hosts that run Goose through an ACP provider (`claude-acp`, `gemini-cli`, `cursor-agent`, `codex`) cannot reach `manage_extensions`, so disabled servers can never be switched on. The user must install with `./setup.py --install --target goose --enable-all`. Those hosts defer tool schemas behind their own tool search, so search by tool name (for example `compile_presentation`).
- Not registered at all: the user runs `./setup.py --install` in the crepe-mcp repo. Then restart the host.
- The server list is fixed: there is no all-in-one `crepe-mcp` server any more.

## Errors

| Symptom | Cause and fix |
|---------|---------------|
| `stale state ...` from `set_slide`, `delete_slide`, `move_slide` | `expected_slide_count` did not match. Call `get_presentation`, then retry with the real count. |
| Unknown `presentation_id` / `document_id` | The server restarted and lost its memory. Rebuild from the exported source with `import_presentation_source`, or create again. |
| `output_path must be an absolute path` | Give an absolute path. |
| `pandoc is not installed` / `lualatex is not installed` | PDF needs pandoc and TeX Live (lualatex). PPTX and DOCX need only pandoc. Tell the user; do not try to install. |
| Lint `forbidden_latex` | Replace the LaTeX with Markdown (see the table in `references/presentations.md`). |
| Lint `missing_image` | The path is wrong or relative. Create the file first, use an absolute path. |
| Lint `parse_error` with a line number | The line is relative to that slide or section. Check unbalanced fences (``` or `:::`) and unescaped `$`. |
| lualatex fails but lint passed | Read the first `!` line of the error: a missing package or font, or an unsupported character in the text. Remove the construct rather than adding LaTeX. |
| `render_*_as_pngs` fails | The matching compile did not run first, or PPTX/DOCX rendering has no LibreOffice. PDF rendering needs nothing extra. |
| `export_drawio` fails | draw.io is not installed or `CREPE_DRAWIO_PATH` is wrong; or the file failed `lint_drawio`. |
| `web_search` returns `warning` | `CREPE_TAVILY_API_KEY` is missing from the `.env`. Report it; use other tools. |
| `fetch_webpage` warns about a browser | No Chromium at `CREPE_HEADLESS_BROWSER_PATH`: the text may lack JavaScript content. |

## Environment

Set by `./setup.py --install`; keys are never written to client configs.

| Variable | Used by | Where it comes from |
|----------|---------|---------------------|
| `CREPE_TAVILY_API_KEY` | `web_search` | `.env` file: `$CREPE_ENV_FILE`, else `~/.config/crepe-mcp/.env`, else `./.env` |
| `CREPE_SEMANTIC_SCHOLAR_API_KEY` | `academic_search` (optional) | same `.env` |
| `CREPE_HEADLESS_BROWSER_PATH` | `fetch_webpage` | auto-detected Chromium |
| `CREPE_LIBREOFFICE_PATH` | PPTX/DOCX to PNG | auto-detected LibreOffice |
| `CREPE_DRAWIO_PATH` | `export_drawio` | auto-detected draw.io |
