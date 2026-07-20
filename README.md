# CREPE — Compile, Research, Export, Presentation Engine

An MCP server for [Goose](https://block.github.io/goose/) that turns an AI
agent into a capable slide author: draft decks in Pandoc Markdown, compile to
Beamer PDF or PowerPoint, validate visually with PNG exports, and pull in
research from the web, Wikipedia and Semantic Scholar.

## Tools (15 total)

### Presentation (stateful)
| Tool | Purpose |
|------|---------|
| `create_presentation` | Start a new deck, set all metadata |
| `get_presentation` | Inspect metadata + slide list |
| `get_slide` | Read a single slide's full content |
| `set_slide` | Add, replace, or insert a slide (`insert=False` default: index < len → replace, else append; `insert=True` shifts later slides down) |
| `delete_slide` | Remove a slide by index; later slides shift down |
| `update_presentation_metadata` | Change title/subtitle/author/institute/date without rebuilding the deck |
| `list_presentations` | Enumerate presentation_ids currently held in memory |
| `compile_presentation` | Compile to PDF (Beamer/lualatex) or PPTX (pandoc) |
| `render_slides_as_pngs` | Render compiled artifact to PNG sequence for validation |
| `cleanup_presentation` | Delete a presentation's in-memory state and on-disk scratch dir |

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
git clone https://github.com/mariolpantunes/crepe-mcp
cd crepe-mcp
uv tool install .
```

## System requirements

- **pandoc** — slide compilation
- **lualatex** — PDF/Beamer output (`texlive-full` or MacTeX)
- **pymupdf** — PDF→PNG rendering (installed via pip, no system dep)
- **LibreOffice** — required on every platform for PPTX→PNG rendering
  (native `soffice`/`libreoffice` on PATH, the macOS app bundle at
  `/Applications/LibreOffice.app`, or — Linux only — a Flatpak install,
  `flatpak install flathub org.libreoffice.LibreOffice`, auto-detected at
  runtime, no config needed). There is no fallback: without LibreOffice,
  `render_slides_as_pngs` (`format="pptx"`) returns a clean error instead
  of a crash.

## Environment variables

All variables are prefixed with `CREPE_` to avoid collisions.

| Variable | Required | Purpose |
|----------|----------|---------|
| `CREPE_TAVILY_API_KEY` | No | Enables `web_search` via Tavily; graceful warning if absent |
| `CREPE_HEADLESS_BROWSER_PATH` | No | Path to Chromium-compatible browser for `fetch_webpage`. See [macOS Browser Paths](#macos--headless-browser-setup) below. Falls back to urllib if unset. |
| `CREPE_LIBREOFFICE_PATH` | No | Path to a `soffice`/`libreoffice` executable, overriding auto-detection. Only needed if auto-detection (PATH, macOS app bundle, Flatpak) fails. |

### macOS & Headless Browser Setup
To enable JavaScript-rendered webpage fetching via `fetch_webpage` on **macOS**, set `CREPE_HEADLESS_BROWSER_PATH` to any Chromium-based browser application path:
- **Google Chrome**: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
- **Brave Browser**: `/Applications/Brave Browser.app/Contents/MacOS/Brave Browser`
- **Microsoft Edge**: `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`

> **Note on Safari:** Apple's native **Safari (`safaridriver`) does not support CLI `--headless` mode** for DOM dumping (`--dump-dom`). If you only have Safari installed or choose not to set `CREPE_HEADLESS_BROWSER_PATH`, `fetch_webpage` automatically falls back to pure Python `urllib` + HTML stripping, which works instantaneously for all static articles, documentation, and Wikipedia pages.

## Goose integration

You can automate this entire configuration using our built-in interactive setup script:
```bash
# Auto-detect browsers/keys, export profile variables, and register in ~/.config/goose/config.yaml
./setup.py --install

# To remove and clean up later:
./setup.py --uninstall
```

Alternatively, you can add `crepe-mcp` manually to `~/.config/goose/config.yaml` using one of the options below.

**Use `envs` (literal values), not `env_keys`.** Goose resolves every `env_keys`
entry against its own keyring/secrets store — it does *not* fall back to a
plain shell environment variable of the same name. All of CREPE's env vars are
optional, and declaring one under `env_keys` without a matching Goose secret
makes the whole extension fail to start with `Failed to fetch secret ... not
found`. Only fill in the values you actually need (all are optional; omit any
you don't use):

### Option 1: Run directly from your local clone (Recommended for testing)
If you cloned the repository locally (e.g. to `~/git/crepe-mcp`), you can tell Goose to execute it directly via `uv run` without installing it globally:

```yaml
extensions:
  crepe:
    name: crepe
    type: stdio
    cmd: uv
    args: ["--directory", "/home/username/git/crepe-mcp", "run", "crepe-mcp"]
    envs:
      CREPE_TAVILY_API_KEY: ""
      CREPE_HEADLESS_BROWSER_PATH: ""
      CREPE_LIBREOFFICE_PATH: ""
```
*(Replace `/home/username/git/crepe-mcp` with your actual absolute path to the repository).*

### Option 2: Run as an installed tool (`uv tool install .`)
If you ran `uv tool install .` inside the project directory (or installed via pip), `crepe-mcp` is on your PATH:

```yaml
extensions:
  crepe:
    name: crepe
    type: stdio
    cmd: crepe-mcp
    args: []
    envs:
      CREPE_TAVILY_API_KEY: ""
      CREPE_HEADLESS_BROWSER_PATH: ""
      CREPE_LIBREOFFICE_PATH: ""
```

### Option 3: Run ephemerally via `uvx` (Once published to PyPI)
If running directly from PyPI without local installation:

```yaml
extensions:
  crepe:
    name: crepe
    type: stdio
    cmd: uvx
    args: ["crepe-mcp"]
    envs:
      CREPE_TAVILY_API_KEY: ""
      CREPE_HEADLESS_BROWSER_PATH: ""
      CREPE_LIBREOFFICE_PATH: ""
```

### Option 4: Add via Goose CLI (`goose configure`)
If you prefer Goose's interactive terminal setup instead of editing `config.yaml`:
1. Run `goose configure` in your terminal.
2. Select **`Add Extension`** → **`Command-line Extension (stdio)`**.
3. For **Name**, enter: `crepe`
4. For **Command**, enter either:
   - `crepe-mcp` (if installed globally)
   - OR `uv --directory /path/to/crepe-mcp run crepe-mcp` (for local repo)
5. When prompted for environment variables, add them as literal values
   (`CREPE_TAVILY_API_KEY`, etc.) — all are optional, so only add the ones you
   use. If `goose configure` only offers a secrets/keyring option rather than
   a plain value, prefer editing `config.yaml` directly (Options 1-3 above)
   to use `envs` instead.

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
| Two-column layout | `:::: {.columns}\n::: {.column width="50%"}\nLeft\n:::\n::: {.column width="50%"}\nRight\n:::\n::::` |

## License

MIT
