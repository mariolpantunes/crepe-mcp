<p align="center">
  <img src="assets/logo.svg" width="128" height="128" alt="CREPE MCP Logo" />
</p>

# CREPE — Compile, Research, Export, Presentation Engine

An MCP server for [Goose](https://block.github.io/goose/), [Claude Desktop](https://claude.ai), [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview), and [Antigravity (AGY CLI)](https://github.com/google-deepmind) that turns AI agents into capable document and presentation authors: draft slide decks in Pandoc Markdown, compile to Beamer PDF or PowerPoint, build A4 reports and articles, manage styled Excel workbooks, export Draw.io architecture diagrams, and pull in research from Semantic Scholar, arXiv, Wikipedia, and the live web.

---

## Architecture & Sub-Servers

Built natively on **FastMCP 3.X**, CREPE is structured as five focused sub-servers that can be used independently or unified via the monolith entry point:

| Sub-server | Entry point | Tools | Domain |
|:-----------|:------------|:-----:|:-------|
| **Presentations** | `uv run crepe-presentations` | 15 | Slide decks (Beamer PDF, PPTX) & `lint_presentation` |
| **Documents** | `uv run crepe-documents` | 12 | A4 reports & articles (PDF, DOCX) & `lint_document` |
| **Research** | `uv run crepe-research` | 6 | Semantic Scholar, arXiv, Tavily web, Wikipedia |
| **Spreadsheets** | `uv run crepe-spreadsheets` | 4 | Styled Excel workbooks (.xlsx) |
| **Diagrams** | `uv run crepe-diagrams` | 3 | draw.io export & structure validation (`lint_drawio`) |
| **Monolith** | `uv run crepe-mcp` | **40** | All 40 tools aggregated in a single endpoint |

> **Context Efficiency**: Use the sub-servers when running targeted workflows to keep your LLM context window lean, or the monolith (`crepe-mcp`) for general-purpose pair programming.

---

## FastMCP 3.X Native Capabilities

- **Automatic Agent Instructions**: Every server passes structured formatting guidelines during initialization so connected LLMs strictly follow Pandoc Markdown and validation rules without needing separate prompt engineering.
- **Dynamic MCP Resources**:
  - `presentation://{presentation_id}/source` — Live Markdown source of a presentation.
  - `presentation://{presentation_id}/config` — Live YAML configuration.
  - `document://{document_id}/source` — Live structured document Markdown.
  - `document://{document_id}/config` — Live YAML document metadata.
- **Standard MCP Prompt Templates**:
  - `academic_presentation`: Scaffolds multi-slide conference/academic slide decks.
  - `technical_report`: Scaffolds engineering and research documents.
  - `drawio_diagram`: Scaffolds multi-layer draw.io architecture diagrams.
  - `spreadsheet_model`: Scaffolds multi-period formatted Excel workbooks.

---

## Tools Summary (40 Total)

### Group A — Presentation (14 tools + 1 linter)
`create_presentation`, `get_presentation`, `get_slide`, `set_slide`, `move_slide`, `duplicate_presentation`, `delete_slide`, `update_presentation_metadata`, `list_presentations`, `export_presentation_source`, `import_presentation_source`, `compile_presentation`, `render_slides_as_pngs`, `cleanup_presentation`, `lint_presentation`.

### Group B — Research & Utilities (6 tools)
`academic_search`, `arxiv_search`, `web_search`, `wikipedia_search`, `wikipedia_read`, `fetch_webpage`.

### Group C — Diagram Tools (2 tools + 1 linter)
`inspect_drawio`, `export_drawio`, `lint_drawio`.

### Group D — A4 Document Engine (11 tools + 1 linter)
`create_document`, `get_document`, `set_chapter`, `set_section`, `delete_chapter`, `update_document_metadata`, `export_document_source`, `list_documents`, `compile_document`, `render_document_as_pngs`, `cleanup_document`, `lint_document`.

### Group E — Excel Engine (4 tools)
`create_excel`, `inspect_excel`, `update_excel_sheet`, `markdown_table_to_excel`.

---

## Quick Setup & Client Integration

Use the automated POSIX installer to configure your environment and register CREPE across your installed AI clients:

```bash
# Auto-detect installed clients (Goose, Claude, AGY CLI) and configure
./setup.py --install

# Target specific clients
./setup.py --install --target goose claude agy

# Install monolith mode (single all-in-one server) instead of sub-servers
./setup.py --install --legacy

# Uninstall and clean up
./setup.py --uninstall
```

### Manual Configuration

#### 1. Goose (`~/.config/goose/config.yaml`)
```yaml
extensions:
  crepe-presentations:
    name: crepe-presentations
    type: stdio
    cmd: uv
    args: ["--directory", "/path/to/crepe-mcp", "run", "crepe-presentations"]
    envs:
      CREPE_LIBREOFFICE_PATH: ""
```

#### 2. Antigravity CLI (`~/.gemini/config/mcp_config.json`)
```json
{
  "mcpServers": {
    "crepe": {
      "command": "uv",
      "args": ["--directory", "/path/to/crepe-mcp", "run", "crepe-mcp"],
      "env": {}
    }
  }
}
```

#### 3. Claude Desktop (`claude_desktop_config.json`)
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "crepe": {
      "command": "uv",
      "args": ["--directory", "/path/to/crepe-mcp", "run", "crepe-mcp"],
      "env": {}
    }
  }
}
```

---

## System Requirements

- **Python**: `>=3.12`
- **pandoc**: Required for compiling slides and documents to PDF/PPTX/DOCX.
- **lualatex**: Required for PDF rendering (`texlive-full` on Linux or MacTeX on macOS).
- **LibreOffice**: Optional / recommended for rasterizing PPTX slides to PNG sequences.
- **draw.io**: Optional / recommended for headlessly exporting `.drawio` diagrams to PNG/SVG.

---

## Testing & Quality Gates

Run all quality checks locally matching the GitHub Actions CI pipeline:

```bash
# Run unit tests via Python's native unittest
python3 -m unittest discover -s tests -p "test_*.py" -v

# Run linting
ruff check .

# Run type checking
basedpyright

# Run dead code detection
vulture
```

---

## License

MIT © Mário Antunes
