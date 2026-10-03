Read when: building, editing or compiling a slide deck (server `crepe-presentations`, 15 tools).

# Presentations

## Workflow

```
create_presentation(title, subtitle?, author?, institute?, date?)   -> presentation_id
set_slide(presentation_id, index, title, content) x N
lint_presentation(presentation_id)                                  -> fix every issue
compile_presentation(presentation_id, output_path, output_format)   -> 'pdf' or 'pptx'
render_slides_as_pngs(presentation_id, output_format)               -> optional visual check
cleanup_presentation(presentation_id)
```

Other tools: `get_presentation`, `get_slide`, `list_presentations`, `delete_slide`, `move_slide`, `duplicate_presentation`, `update_presentation_metadata`.
`author` and `institute` default to the user's own values; pass them explicitly for anyone else.

## Slide content (Pandoc Markdown, `--slide-level=2`)

`set_slide` stores one slide: `title` becomes the `##` heading, `content` is the body. `index` replaces a slide, or appends when it is past the end; `insert=true` inserts and shifts the rest.

````markdown
- bullet                       (plain list)
> - first                      (incremental: reveals one at a time)
> - second
Inline code `x = 42`, math $E = mc^2$, display math:
$$ \nabla \cdot \mathbf{E} = \frac{\rho}{\varepsilon_0} $$

```python
def hello():
    return "world"
```

![Caption](/absolute/path/image.png){width=80%}

:::: {.columns}
::: {.column width="50%"}
Left
:::
::: {.column width="50%"}
Right
:::
::::

::: notes
Speaker notes. PDF only.
:::
````

- A section divider is a slide whose `content` is only `# Section Title`. Do not wrap it in `##`.
- Tables are GitHub-style pipe tables.

### Forbidden (raw LaTeX works only for PDF and disappears in PPTX)

| Do not write | Write instead |
|--------------|---------------|
| `\includegraphics{...}` | `![](/abs/path.png)` |
| `\begin{center}...\end{center}` | a fenced div `::: ... :::` |
| `\begin{columns}...\end{columns}` | `:::: {.columns}` |
| `\textbf{...}`, `\textit{...}` | `**bold**`, `*italic*` |

## Concurrency

`set_slide`, `delete_slide` and `move_slide` accept `expected_slide_count`. When you send several calls at once, give each the count it expects to see; a mismatch returns `{"success": false, "error": "stale state ..."}` instead of writing to the wrong index. The server serializes calls per deck in arrival order, so the guard is optional.

## Compile

| `output_format` | Engine | Notes |
|-----------------|--------|-------|
| `pdf` | pandoc + lualatex + Beamer | `theme` (default `moloch`; any installed Beamer theme, e.g. `metropolis`, `Madrid`) and `highlight_style` (`tango`) apply here only. Takes several seconds. |
| `pptx` | pandoc PPTX writer | No raw LaTeX. `reference_doc` is an optional `.pptx` template for styling. |

`output_path` must be absolute. `render_slides_as_pngs(output_format=...)` needs a previous compile in that format (`dpi` 150 by default, `output_dir` optional); PPTX rendering needs LibreOffice.

## Source round trip

`export_presentation_source` returns the slides Markdown and `config.yml` (and writes them to `output_dir` if given). `import_presentation_source(presentation_id, markdown | source_path)` replaces all slides by splitting on `##` and bare `#` headings; metadata stays. Use it to edit a deck in a text file or to restart after the server forgot an id.

## Lint

`lint_presentation(presentation_id, slide_index?)` runs a pandoc parse per slide and checks forbidden LaTeX, that every `![](path)` exists on disk, and the slide structure (a title, nothing before the first heading).

```json
{"valid": false, "slide_count": 5, "issues": [
  {"slide_index": 2, "type": "forbidden_latex", "line": 4, "message": "\\includegraphics detected - use ![](path) instead"},
  {"slide_index": 3, "type": "missing_image", "line": 1, "message": "Image not found on disk: /tmp/missing.png"}]}
```

Fix the slide with `set_slide`, then lint again. Compile errors from lualatex are harder to read than lint messages, so never skip the lint.
