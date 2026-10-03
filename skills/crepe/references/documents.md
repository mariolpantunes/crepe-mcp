Read when: writing or compiling an A4 report, article or paper (server `crepe-documents`, 12 tools).

# A4 documents

## Workflow

```
create_document(title, subtitle?, author?, institute?, date?, abstract?, paper_size?, margin?, font_size?, toc?, number_sections?)  -> document_id
set_chapter(document_id, chapter_index, title, intro?)
set_section(document_id, chapter_index, section_index, title, content, level?)    level 2 = ##, level 3 = ###
lint_document(document_id)                                                        -> fix every issue
compile_document(document_id, output_path, output_format)                         -> 'pdf' or 'docx'
render_document_as_pngs(document_id, output_format)                               -> optional visual check
cleanup_document(document_id)
```

Defaults: `a4paper`, `2.5cm` margin, `11pt`, table of contents and numbered sections on. Other tools: `get_document`, `list_documents`, `delete_chapter`, `update_document_metadata`, `export_document_source`.
`author` and `institute` default to the user's own values; pass them explicitly for anyone else.

## Structure

Chapters are `#`, sections `##`, subsections `###`. `intro` (chapter) and `content` (section) are Pandoc Markdown. Indexes are zero-based; an index past the end appends. `set_section` needs its chapter to exist first (otherwise it fails with an index error).

## Content

````markdown
Paragraphs, lists, **bold**, *italic*, `code`.

| Col A | Col B |
|-------|-------|
| 1     | 2     |

![Caption text](/absolute/path/figure.png){width=80%}

$$ \int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2} $$
````

Citations (`[@key]`) need pandoc-citeproc and are not enabled by default.

Forbidden, as for slides: `\includegraphics`, `\begin{center}` and any raw LaTeX environment. They break DOCX and sometimes lualatex. Use Markdown figures and tables.

## Compile

| `output_format` | Engine | Notes |
|-----------------|--------|-------|
| `pdf` | pandoc + lualatex | Full LaTeX available, but keep to Markdown. Takes several seconds. |
| `docx` | pandoc DOCX writer | No raw LaTeX. `reference_doc` is an optional `.docx` template for styling. |

`output_path` must be absolute; images must exist on disk at that moment. `render_document_as_pngs` needs a previous compile in that format.

## Lint

`lint_document(document_id, chapter_index?)` makes the same checks as the slide lint (pandoc parse, forbidden LaTeX, missing images) over every chapter and section, or one chapter. Issues carry `chapter_index` and `section_index`. Fix with `set_section`, lint again.
