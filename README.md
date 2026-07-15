# CREPE — Compile, Research, Export, Presentation Engine

An MCP server for [Goose](https://block.github.io/goose/) that turns an AI
agent into a capable slide author: draft decks in Pandoc Markdown, compile to
Beamer PDF or PowerPoint, validate visually with PNG exports, and pull in
research from the web, Wikipedia and Semantic Scholar.

## Tools (11 total)

### Presentation (stateful)
| Tool | Purpose |
|------|---------|
| `create_presentation` | Start a new deck, set all metadata |
| `get_presentation` | Inspect metadata + slide list |
| `get_slide` | Read a single slide's full content |
| `set_slide` | Add or replace a slide (index < len → replace, else append) |
| `compile_presentation` | Compile to PDF (Beamer/lualatex) or PPTX (pandoc) |
| `render_slides_as_pngs` | Render compiled artifact to PNG sequence for validation |

### Research & utilities
| Tool | Purpose |
|------|---------|
| `academic_search` | Semantic Scholar (no key needed) |
| `web_search` | Tavily API web search |
| `wikipedia_search` | Wikipedia article search |
| `wikipedia_read` | Full Wikipedia article text |
| `fetch_webpage` | Extract text from any URL (headless browser or urllib) |

## Installation

```bash
# From PyPI (once published)
uv tool install crepe-mcp

# From source
git clone https://github.com/yourname/crepe-mcp
cd crepe-mcp
uv tool install .
```

## System requirements

- **pandoc** — slide compilation
- **lualatex** — PDF/Beamer output (`texlive-full` or MacTeX)
- **aspose-slides** — PPTX→PNG rendering (installed via pip, no system dep)
- **pymupdf** — PDF→PNG rendering (installed via pip, no system dep)

## Environment variables

All variables are prefixed with `CREPE_` to avoid collisions.

| Variable | Required | Purpose |
|----------|----------|---------|
| `CREPE_TAVILY_API_KEY` | No | Enables `web_search` via Tavily; graceful warning if absent |
| `CREPE_HEADLESS_BROWSER_PATH` | No | Path to Chromium-compatible browser for `fetch_webpage` (e.g. `/usr/bin/chromium`). Falls back to urllib if unset. |
| `CREPE_ASPOSE_LICENSE_PATH` | No | Path to Aspose `.lic` file for watermark-free PPTX→PNG export. Evaluation mode (with watermarks) is used if unset. |

## Goose integration

Add CREPE to Goose by editing `~/.config/goose/config.yaml`:

```yaml
extensions:
  crepe:
    type: stdio
    cmd: crepe-mcp
    args: []
    env_keys:
      - CREPE_TAVILY_API_KEY
      - CREPE_HEADLESS_BROWSER_PATH
      - CREPE_ASPOSE_LICENSE_PATH
```

Or run ephemerally with `uvx` (no install needed):

```yaml
extensions:
  crepe:
    type: stdio
    cmd: uvx
    args: ["crepe-mcp"]
    env_keys:
      - CREPE_TAVILY_API_KEY
```

## Typical agent workflow

```
# Build a deck
create_presentation(title="My Talk", author="Ada Lovelace")
set_slide(id, 0, "Introduction", "- Point one\n- Point two")
set_slide(id, 1, "Results", "$$E=mc^2$$")

# Validate PDF layout
compile_presentation(id, "/tmp/deck.pdf", format="pdf")
render_slides_as_pngs(id, format="pdf")   # → slide_001.png …

# Deliver as PPTX
compile_presentation(id, "/tmp/deck.pptx", format="pptx")
render_slides_as_pngs(id, format="pptx")  # validate PPTX layout
```

## Pandoc slide syntax quick reference

| Element | Syntax |
|---------|--------|
| Section divider | `# Section Title` as slide content |
| Bullet | `- item` |
| Incremental bullet | `> - item` |
| Math block | `$$ E=mc^2 $$` |
| Code | ` ```python\ncode\n``` ` |
| Image | `![caption](/path/to/image.png)` |
| Speaker notes | `::: notes\ntext\n:::` |
| Two-column layout | pandoc fenced-div (see docs) |

## License

MIT
