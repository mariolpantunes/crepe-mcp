# CREPE — Agent Usage Guide

> **Read this file once before using any CREPE tool.** It contains the
> syntax rules, workflows, and constraints that determine whether your
> compiled output (PDF, PPTX, DOCX) will look correct.

---

## 1. Tool Groups at a Glance

| Group | Tools | Sub-server | Purpose |
|-------|-------|------------|----------|
| **A** | `create_presentation` · `get/set/delete/move_slide` · `duplicate/update/list_presentations` · `export/import_presentation_source` · `compile_presentation` · `render_slides_as_pngs` · `cleanup_presentation` | `crepe-presentations` | Stateful slide deck builder |
| **B** | `academic_search` · `arxiv_search` · `web_search` · `wikipedia_search/read` · `fetch_webpage` | `crepe-research` | Research & web |
| **C** | `export_drawio` · `inspect_drawio` | `crepe-diagrams` | Draw.io diagram export |
| **D** | `create_document` · `get/set_chapter` · `set_section` · `delete_chapter` · `update/list_documents` · `export_document_source` · `compile_document` · `render_document_as_pngs` · `cleanup_document` | `crepe-documents` | A4 report/article builder |
| **E** | `create_excel` · `inspect_excel` · `update_excel_sheet` · `markdown_table_to_excel` | `crepe-spreadsheets` | Excel workbook |
| **F** | `lint_presentation` · `lint_document` · `lint_drawio` | (with their group) | Pre-compile content validation |
| **All** | 40 tools | `crepe-mcp` | Full monolith (all groups) |

## 1b. Server Entry Points

Each sub-server can be registered independently in your agent host:

| Command | Tools | Use when |
|---------|-------|----------|
| `uv run crepe-mcp` | 40 (all) | General-purpose agents that need everything |
| `uv run crepe-presentations` | 15 | Slide deck workflows only |
| `uv run crepe-documents` | 12 | Report/paper writing only |
| `uv run crepe-research` | 6 | Research and web browsing only |
| `uv run crepe-spreadsheets` | 4 | Spreadsheet tasks only |
| `uv run crepe-diagrams` | 3 | Diagram export/validation only |

The sub-servers registered by `setup.py --install` are disabled by default
(`enabled: false`) in the Goose config. Enable only the ones you need to keep
your agent's context window lean.

---

## 2. Recommended Workflow

### Presentations (Group A + F)

```
create_presentation()
  → set_slide() × N          # build slides
  → lint_presentation()       # ← CHECK BEFORE COMPILING
  → compile_presentation()    # pdf or pptx
  → render_slides_as_pngs()  # visual check
  → cleanup_presentation()
```

### A4 Documents (Group D + F)

```
create_document()
  → set_chapter() / set_section() × N
  → lint_document()           # ← CHECK BEFORE COMPILING
  → compile_document()        # pdf or docx
  → render_document_as_pngs()
  → cleanup_document()
```

### Draw.io Diagrams (Group C + F)

```
# (create .drawio file via tool or write raw XML)
lint_drawio(input_path)       # ← CHECK STRUCTURE FIRST
  → export_drawio()           # png / svg / pdf
```

**Always call the matching `lint_*` tool before compiling.** Fix all
`issues` it returns. Compilation errors from pandoc/lualatex are harder
to debug than lint warnings.

---

## 3. Markdown Syntax for Slides

All `content` fields in `set_slide` are **Pandoc Markdown**.
The markdown is passed directly to pandoc with `--slide-level=2`.

### ✅ Supported constructs

```markdown
## Slide Title

Bullet list:
- item one
- item two

Incremental bullets (reveal one at a time):
> - first
> - second

Inline code: `x = 42`

Code block:
```python
def hello():
    return "world"
```

Math (inline): $E = mc^2$

Math (display):
$$ \nabla \cdot \mathbf{E} = \frac{\rho}{\varepsilon_0} $$

Image (standard Markdown):
![Alt text](/absolute/path/to/image.png)

Image with size (Pandoc attribute):
![Caption](/path/to/img.png){width=80%}

Two-column layout:
:::: {.columns}
::: {.column width="50%"}
Left column content
:::
::: {.column width="50%"}
Right column content
:::
::::

Section divider (creates a section frame, no ## wrapper):
# Section Title

Speaker notes (PDF only):
::: notes
Notes visible only to presenter.
:::
```

### ❌ Forbidden in slide content

| Forbidden | Reason | Use instead |
|-----------|--------|-------------|
| `\includegraphics{...}` | Silently dropped by PPTX writer | `![](/path)` |
| `\begin{center}...\end{center}` | Breaks PPTX column layouts | Pandoc div fences `:::` |
| `\begin{columns}...\end{columns}` | LaTeX-only, ignored by PPTX | `:::: {.columns}` |
| `\textbf{...}` | Raw LaTeX | `**bold**` |
| `\textit{...}` | Raw LaTeX | `*italic*` |

> **Rule**: Raw LaTeX environments work only for PDF/Beamer. If you
> ever compile to PPTX, they silently disappear. Always use standard
> Pandoc Markdown — it renders correctly for **both** targets.

---

## 4. Markdown Syntax for Documents (A4)

`intro` (in `set_chapter`) and `content` (in `set_section`) are also
**Pandoc Markdown**, compiled via lualatex (PDF) or pandoc (DOCX).

### ✅ Supported

```markdown
Paragraphs, bullet lists, numbered lists, bold/italic/code.

Tables (GitHub Flavored Markdown):
| Col A | Col B |
|-------|-------|
| 1     | 2     |

Figures:
![Caption text](/absolute/path/to/figure.png){width=80%}

Math:
$$ \int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2} $$

Citations (if using pandoc-citeproc — not enabled by default):
[@smith2023]
```

### ❌ Forbidden

Same rules as slides: no raw `\includegraphics`, no `\begin{center}`,
no raw LaTeX environments. These break DOCX output and generate
lualatex errors in edge cases.

---

## 5. Image Paths

- **Always use absolute paths** in `![](...)` tags.
- The compiled artifact is written to an arbitrary `output_path`; the
  workdir is a temp directory. Relative paths will break.
- Images are embedded from disk at compile time. They must exist
  **before** you call `compile_presentation` / `compile_document`.
- `lint_presentation` and `lint_document` verify that all referenced
  image paths exist on disk — fix those warnings before compiling.

---

## 6. Draw.io Diagrams

### Recommended settings

```python
export_drawio(
    input_path="/abs/path/diagram.drawio",
    output_path="/abs/path/diagram.png",
    output_format="png",        # or "svg" for vector
    transparent=True,           # default — no white background
    scale=2.0,                  # 2× for high-DPI slides
    border=4,                   # small padding around diagram
)
```

`transparent=True` is the default. **Do not override to False** unless
you specifically need a white-background diagram — transparent PNGs
embed cleanly into slides without a white box.

### Validation before export

```python
result = inspect_drawio("/abs/path/diagram.drawio")
# or the richer:
result = lint_drawio("/abs/path/diagram.drawio")
```

`lint_drawio` also decodes compressed diagram XML and validates the
cell hierarchy. Fix any `issues` before calling `export_drawio`.

### Structure rules for hand-crafted `.drawio` files

Every valid `.drawio` file requires exactly these base cells:

```xml
<mxGraphModel>
  <root>
    <mxCell id="0"/>                        <!-- document root -->
    <mxCell id="1" parent="0"/>             <!-- default layer -->
    <!-- your content cells here, parent="1" -->
  </root>
</mxGraphModel>
```

Missing either base cell causes rendering artefacts or empty exports.

---

## 7. Concurrency & `expected_slide_count`

`set_slide`, `delete_slide`, and `move_slide` all accept an optional
`expected_slide_count` parameter. Use it when making parallel batched
calls:

```python
# Safe: each call declares what it expects to see
set_slide(pres_id, index=0, title="...", content="...", expected_slide_count=0)
set_slide(pres_id, index=1, title="...", content="...", expected_slide_count=1)
```

If the actual slide count differs from `expected_slide_count`, the call
returns `{"success": False, "error": "stale state ..."}`. This guards
against index collisions when multiple slides are written concurrently.

The server uses a **fair ticket lock** per presentation: calls are
processed in arrival order, so concurrent writes are safe without
`expected_slide_count` — but providing it gives you an explicit
correctness guarantee.

---

## 8. Lint Tools Reference

### `lint_presentation(presentation_id, slide_index=None)`

Validates the in-memory slide content for a presentation.

Checks:
- Pandoc dry-run parse (`pandoc --from markdown --to native`)
- Forbidden LaTeX patterns (`\includegraphics`, `\begin{center}`, etc.)
- All `![](path)` image references exist on disk
- Slide structure (title present, no content before first heading)

Returns:
```json
{
  "valid": true,
  "slide_count": 5,
  "issues": []
}
```
or with issues:
```json
{
  "valid": false,
  "issues": [
    {"slide_index": 2, "type": "forbidden_latex", "line": 4, "message": "\\includegraphics detected — use ![](path) instead"},
    {"slide_index": 3, "type": "missing_image", "line": 1, "message": "Image not found on disk: /tmp/missing.png"}
  ]
}
```

### `lint_document(document_id, chapter_index=None)`

Same checks as `lint_presentation`, applied to all chapters/sections (or one).

### `lint_drawio(input_path)`

Validates a `.drawio` file without invoking draw.io.

Checks:
- Valid XML structure
- Compressed diagram content decoded and validated
- Base cells (id=0 and id=1) present in every page
- No duplicate cell IDs
- Layer hierarchy integrity

Returns:
```json
{
  "valid": true,
  "page_count": 2,
  "pages": [{"index": 1, "name": "Architecture"}],
  "issues": []
}
```

---

## 9. Compile Targets Reference

| Tool | `output_format` | Engine | Notes |
|------|----------------|--------|-------|
| `compile_presentation` | `pdf` | pandoc + lualatex + Beamer | Supports full LaTeX in content |
| `compile_presentation` | `pptx` | pandoc PPTX writer | **No raw LaTeX** — use Markdown only |
| `compile_document` | `pdf` | pandoc + lualatex | Full LaTeX support |
| `compile_document` | `docx` | pandoc DOCX writer | **No raw LaTeX** in content |

Use `theme` (e.g. `"moloch"`, `"metropolis"`, `"Madrid"`) for PDF only.
Use `reference_doc` (a `.pptx` or `.docx` template) for PPTX/DOCX styling.

---

## 10. Environment Variables

| Variable | Purpose | Set by |
|----------|---------|--------|
| `CREPE_TAVILY_API_KEY` | Tavily web search | `setup.py --install` |
| `CREPE_HEADLESS_BROWSER_PATH` | Chromium for `fetch_webpage` | `setup.py --install` |
| `CREPE_LIBREOFFICE_PATH` | LibreOffice for PPTX→PNG | `setup.py --install` |
| `CREPE_DRAWIO_PATH` | draw.io binary (optional override) | `setup.py --install` |

All variables are auto-detected by `setup.py`. Run `python setup.py --install`
to reconfigure.
