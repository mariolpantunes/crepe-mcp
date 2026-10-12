Read when: the user gives you an existing file to read, summarise, review, cite from or reuse (server `crepe-reader`, 9 tools).

# Reader

Reads PDF, DOCX, ODT, RTF, EPUB, HTML, LaTeX, reStructuredText, Markdown, Org, Jupyter, PPTX, XLSX and CSV without any model: the file is read once into pages, sections, paragraphs and numbered items, and each tool returns a small piece. Every tool takes `path`, the **absolute** path of the file.

```
open_document(path)                         -> title, format, pages, parts, outline, numbered-item counts, warnings
read_pages(path, first_page?, last_page?, part?, cursor?, references?)
read_section(path, heading, part?, cursor?) -> one section by outline heading (about 12,000 characters per call)
search_document(path, query, part?, max_hits?)
list_assets(path, kind?, part?)             -> numbered figures, tables, equations, algorithms, listings, references
get_asset(path, asset, include_image?)      -> one item as text, with the sentences that cite it
list_changes(path, kind?, max_items?)      -> tracked insertions, deletions and comments of a DOCX
render_page_image(path, page, dpi?, format?, output_path?)  -> a page as an image (png default, or jpeg)
document_to_markdown(path, output_path, part?, overwrite?)   -> whole document as a .md file
```

## How to read

1. `open_document` first. The outline tells you which sections exist; `numbered_items` and `uncited_items` tell you what figures and tables there are.
2. Prefer `read_section` and `search_document` over reading everything. `read_pages` returns about 12,000 characters per call: continue with the `next` cursor of the reply until it says `none`. `read_section` works the same way: a long section ends with a `next` cursor (`s<paragraph>@<offset>`); pass it as `cursor` with the same `heading`.
3. Reference list entries are left out of `read_pages` unless `references=true`; `list_assets(kind="reference")` lists them.
4. Quote numbers from `get_asset` (tables arrive as Markdown, equations as TeX or text), not from the caption alone.
5. `render_page_image` is for what text cannot show (a scanned page, a layout, a chart). Without `output_path` it returns the image; with an absolute `.png` (or `.jpg` for `format="jpeg"`) path it saves it so a deck or document can embed it with `![](/abs/path.png)`. For DOCX, ODT, RTF and PPTX the page is the page (slide) as LibreOffice lays it out, not the reading chunk of `read_pages`; the first call converts the file (seconds), later pages are cached.
6. A DOCX is read in its accepted view (insertions kept, deletions dropped, comments left out). Call `list_changes` when the user asks what reviewers changed or commented: it gives author, date, text, the text a comment is attached to, and the section of each mark.
7. `document_to_markdown` gives an editable copy to reuse in `crepe-documents` (`set_section`) or `crepe-presentations` (`import_presentation_source`). It writes a file and returns its size, not the text. It never replaces the source document, and refuses an existing file unless `overwrite=true`.

## Pages

- PDF: the real page numbers. A bundle (cover sheet, manuscript, response letter) shows its `parts`; use `part="manuscript"` to stay inside the main copy.
- Other formats have no pages: a "page" is a reading chunk of about 3,500 characters cut between blocks. Page numbers are stable for a given file but are not printed pages.
- `open_document` warns about PDF pages without a text layer (scans). They cannot be read as text: use `render_page_image`.

## Limits

- Tables, equations and figures are located by rules, not by a model; `list_assets` flags items it could not locate. Check important numbers against the page image.
- Images are attached to `get_asset` only for PDFs and only if the server enables them.
- `CREPE_READER_ROOTS` (folders separated like PATH) can restrict the readable folders; a path outside them is refused.
- Extraction is cached under the system temp folder (`CREPE_SCRATCH_BASE`); a changed file is read again.
