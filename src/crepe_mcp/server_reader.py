"""CREPE — Reader sub-server (Group G, 8 tools).

Deterministic decomposition and analysis of existing documents: PDF, and DOCX, ODT, RTF, EPUB, HTML, LaTeX,
reStructuredText, Markdown, Org, Jupyter, PPTX, XLSX, CSV through pandoc. No LLM is involved: a file is read
once into a store of pages, sections, paragraphs and numbered items, and the tools return small pieces of it.

Can be run as a standalone MCP server:
    venv/bin/crepe-reader

Tools
-----
Group G (8):
  open_document, read_pages, read_section, search_document, list_assets, get_asset,
  render_page_png, document_to_markdown
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal

import pymupdf
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image
from mcp.types import TextContent
from pydantic import Field

from crepe_mcp.reader import crops, reading
from crepe_mcp.reader.common import ReaderError, page_span, resolve_path
from crepe_mcp.reader.markdown_export import export_markdown
from crepe_mcp.reader.overview import overview, part_range
from crepe_mcp.reader.store import DocumentStore
from crepe_mcp.runner import run_server

READER_INSTRUCTIONS = """\
CREPE Reader Engine Guidelines:
1. Every tool takes 'path', the absolute path of the document. Page numbers are the document's own (PDF pages;
   for formats without pages, logical pages of about 3,500 characters).
2. Call open_document first: title, parts, outline with pages, numbered-item counts.
3. read_pages reads whole pages in order (about 12,000 characters per call): continue with the next cursor until it
   is none. read_section reads one section by its heading; search_document finds where a term is mentioned.
4. list_assets lists numbered figures, tables, equations, algorithms, listings and references; get_asset returns one
   as text (tables as Markdown, equations as TeX) with the sentences citing it.
5. render_page_png shows a PDF page as an image; document_to_markdown writes the whole document to a .md file.
"""

# Reply budgets, not layout heuristics.
MENTION_ITEMS = 12
MENTION_CONTEXT = 100
CAPTION_PREVIEW = 140

mcp = FastMCP("crepe-reader", instructions=READER_INSTRUCTIONS)

DocPath = Annotated[
    str,
    Field(max_length=1024, description="Absolute path of the document (PDF, DOCX, ODT, EPUB, HTML, LaTeX, MD, ...)."),
]
Part = Annotated[
    Literal["all", "manuscript", "responses", "cover"],
    Field(description="Pages to use: all (default), or a part listed by open_document (manuscript, responses, cover)."),
]
AssetKind = Literal["figure", "table", "equation", "algorithm", "listing", "statement", "reference"]


def _agent_errors[F: Callable[..., Any]](func: F) -> F:
    """Report problems the agent can fix as tool errors whose message says what to do."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (ReaderError, FileNotFoundError, ValueError) as error:
            raise ToolError(str(error)) from error

    return wrapper  # type: ignore[return-value]


def _open(path: str) -> tuple[Path, DocumentStore]:
    resolved = resolve_path(path)
    return resolved, DocumentStore.open(resolved)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
@_agent_errors
def open_document(path: DocPath) -> dict[str, Any]:
    """Call first. Returns the title, format, page count, parts of a PDF bundle (cover, manuscript, responses) with
    page ranges, the outline with pages, numbered-item counts (figures, tables, equations, ...) and warnings such as
    pages without a text layer. The first call on a file reads it (seconds for a long PDF); later calls are instant."""
    resolved, store = _open(path)
    return overview(store, resolved)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
@_agent_errors
def read_pages(
    path: DocPath,
    first_page: Annotated[int | None, Field(ge=1, description="First page; omit for the part's start.")] = None,
    last_page: Annotated[int | None, Field(ge=1, description="Last page; omit for the part's end.")] = None,
    part: Part = "all",
    cursor: Annotated[
        str | None, Field(max_length=20, description="next cursor of the previous reply; overrides the pages.")
    ] = None,
    references: Annotated[bool, Field(description="Include the reference list entries (omitted by default).")] = False,
) -> str:
    """Read whole pages in reading order (about 12,000 characters per call). The first line gives the pages returned
    and the next cursor: pass it as cursor until it is none. Reference list entries are left out unless
    references=true (list_assets kind='reference' lists them)."""
    _, store = _open(path)
    part_first, part_last = part_range(store, part)
    scope = f"{part} = pages {page_span(part_first, part_last)}"
    offset = 0
    if cursor:
        first, offset = reading.parse_cursor(cursor)
        last = max(part_last, first)
    else:
        first = first_page or part_first
        last = last_page or (part_last if first <= part_last else first)
    last = min(last, store.page_count)
    omission = "" if references else reading.REFERENCES_OMITTED
    text, _ = reading.read_pages(store, first, last, offset, scope, omission)
    return text


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
@_agent_errors
def read_section(
    path: DocPath,
    heading: Annotated[
        str,
        Field(min_length=1, max_length=120,
              description="Outline heading, e.g. 'Introduction', 'IV. Experiments', '3.2'; case ignored."),
    ],
    part: Part = "all",
) -> str:
    """Read one section by its outline heading, up to the next heading of the same or higher level. If nothing
    matches, the error lists the headings."""
    _, store = _open(path)
    first, last = part_range(store, part)
    return reading.read_section(store, heading, first, last)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
@_agent_errors
def search_document(
    path: DocPath,
    query: Annotated[
        str,
        Field(min_length=2, max_length=120,
              description="Words matched from their start, case-insensitive: a term, dataset, 'Table 3', a number."),
    ],
    part: Part = "all",
    max_hits: Annotated[int, Field(ge=1, le=40, description="Maximum hits returned.")] = 15,
) -> dict[str, Any]:
    """Find where something is mentioned: the number of matching paragraphs and, per hit, page, section and
    snippet."""
    _, store = _open(path)
    first, last = part_range(store, part)
    return reading.search(store, query, first, last, max_hits)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
@_agent_errors
def list_assets(
    path: DocPath,
    kind: Annotated[AssetKind | None, Field(description="Only this kind; omit for all kinds but references.")] = None,
    part: Part = "all",
) -> dict[str, Any]:
    """List the numbered items: id, label, page, caption start and how many paragraphs cite it; never-cited ids
    under 'uncited'. Choose what to inspect with get_asset."""
    _, store = _open(path)
    first, last = part_range(store, part)
    everything = store.assets(first=first, last=last)
    shown = [a for a in everything if (a["kind"] == kind if kind else a["kind"] != "reference")]
    items = []
    for item in shown:
        entry: dict[str, Any] = {"id": item["id"], "label": item["label"],
                                 "page": page_span(item["page"], item["last_page"]), "cited": item["cited"]}
        preview = " ".join((item["caption"] or item["content"]).split())
        if preview:
            entry["caption"] = preview[:CAPTION_PREVIEW]
        if item["confidence"] == "low":
            entry["region"] = "not located (caption only)"
        items.append(entry)
    reply: dict[str, Any] = {
        "pages": page_span(first, last),
        "counts": dict(sorted(Counter(a["kind"] for a in everything).items())),
        "items": items,
        "uncited": [a["id"] for a in shown if a["cited"] == 0],
    }
    if not kind and any(a["kind"] == "reference" for a in everything):
        reply["hint"] = "References are listed with kind='reference'."
    return reply


@mcp.tool(annotations={"readOnlyHint": True})
@_agent_errors
def get_asset(
    path: DocPath,
    asset: Annotated[
        str,
        Field(pattern=r"^[a-z]+:[A-Za-z0-9.]+$",
              description="Item id from list_assets, e.g. 'figure:3', 'table:II', 'reference:12'."),
    ],
    include_image: Annotated[
        bool, Field(description="Attach a cropped image of a PDF item, if images are enabled on this server.")
    ] = False,
) -> list[Any]:
    """Inspect one numbered item: caption and content as text (table cells as Markdown, equations as linear text and
    MathML or TeX, algorithm lines, listings, theorem-like statements with their proof, reference entry), how it was
    located, and the sentences citing it."""
    resolved, store = _open(path)
    found = store.asset(asset)
    if found is None:
        raise ReaderError(f"No item {asset!r} in this document. Call list_assets for valid ids.")
    mentions = found["mentions"]
    paragraphs: dict[int, str] = {}
    for page_no in sorted({m["page"] for m in mentions[:MENTION_ITEMS]}):
        paragraphs.update({p["id"]: p["text"] for p in store.paragraphs(page_no, page_no)})
    cited_by = []
    for mention in mentions[:MENTION_ITEMS]:
        text = paragraphs.get(mention["paragraph"], "")
        at = max(text.find(mention["text"]), 0)
        context = text[max(0, at - MENTION_CONTEXT): at + len(mention["text"]) + MENTION_CONTEXT]
        entry = {"page": mention["page"], "text": " ".join(context.split())}
        if mention["strength"] == "weak":
            entry["certainty"] = "weak (bare number)"
        cited_by.append(entry)
    detail: dict[str, Any] = {
        "id": found["id"],
        "label": found["label"],
        "page": page_span(found["page"], found["last_page"]),
        "caption": found["caption"],
        "content": found["content"],
        "format": found["content_format"],
        "located_by": f"{found['method']} ({found['confidence']} confidence)",
        "cited_count": len(mentions),
        "cited_by": cited_by,
    }
    image: Image | None = None
    images = crops.settings()
    if not include_image:
        detail["image"] = "not requested"
    elif resolved.suffix.lower() != ".pdf":
        detail["image"] = "not attached: images are cropped from PDF files only"
    elif not images["enabled"]:
        detail["image"] = "not attached: images are disabled on this server; rely on the caption and content"
    elif found["x0"] is None:
        detail["image"] = "not attached: no region was located for this item"
    else:
        bbox = (found["x0"], found["y0"], found["x1"], found["y1"])
        image = Image(data=crops.crop_png(resolved, found["page"], bbox, int(images["max_side"])), format="png")
        detail["image"] = "attached"
    text = TextContent(type="text", text=json.dumps(detail, ensure_ascii=False))
    return [text, image] if image is not None else [text]


@mcp.tool(annotations={"readOnlyHint": True})
@_agent_errors
def render_page_png(
    path: DocPath,
    page: Annotated[int, Field(ge=1, description="PDF page number.")],
    dpi: Annotated[int, Field(ge=36, le=300, description="Resolution; 100 is readable and small.")] = 100,
    output_path: Annotated[
        str | None, Field(max_length=1024, description="Absolute .png path to save to; omit to get the image back.")
    ] = None,
) -> list[Any]:
    """Show one page of a PDF as an image (for layout, figures and scans that text cannot describe). With
    output_path the PNG is saved there (e.g. to embed it in a deck) and its size is returned."""
    resolved, store = _open(path)
    if resolved.suffix.lower() != ".pdf":
        raise ReaderError("render_page_png works on PDF files; convert the document to PDF first.")
    if page > store.page_count:
        raise ReaderError(f"Page {page} is outside this PDF (1-{store.page_count}).")
    with pymupdf.open(str(resolved)) as doc:
        data = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False).tobytes("png")
    if output_path is None:
        return [Image(data=data, format="png")]
    target = Path(output_path)
    if not target.is_absolute() or target.suffix.lower() != ".png":
        raise ReaderError("output_path must be an absolute path ending in .png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    saved = {"output_path": str(target), "bytes": len(data), "page": page, "dpi": dpi}
    return [TextContent(type="text", text=json.dumps(saved))]


@mcp.tool(annotations={"destructiveHint": False, "idempotentHint": True})
@_agent_errors
def document_to_markdown(
    path: DocPath,
    output_path: Annotated[str, Field(max_length=1024, description="Absolute path of the .md file to write.")],
    part: Part = "all",
) -> dict[str, Any]:
    """Write the document as one Markdown file (pandoc GFM for non-PDF formats, extracted text with headings and
    tables for PDF) that can be edited and used in a CREPE deck or document. Returns the file size, not its text."""
    resolved, store = _open(path)
    target = Path(output_path)
    if not target.is_absolute():
        raise ReaderError("output_path must be an absolute path")
    return export_markdown(store, resolved, target, part)


def main() -> None:
    """Console-script entrypoint for the standalone crepe-reader server."""
    run_server(mcp)


if __name__ == "__main__":
    main()
