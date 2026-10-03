"""Deterministic PDF extraction into page and line records.

One PyMuPDF pass per page with fixed flags; no LLM is involved. Every text line is classified into a
region (body, header, footer, line number, rotated); body lines get a column and a reading-order
position, so page text excludes running heads, page numbers and proof line numbers. Tolerances are
relative to the document's own body font size and line height (see ``heuristics``):
- a justified line that MuPDF split at its stretched word spaces (equal gaps below an em) is joined again;
- headers/footers are peeled row by row from the top and the bottom of each page while every line of
  the row is a running element, a page counter or a "Page n of m" label. A running element is the same
  text at about the same height (``running_shift``) on a chain of at least ``running_min_pages`` pages,
  each at most ``running_page_distance`` pages from the next; a page counter is a number whose value
  changes with the page along such a chain;
- proof line numbers are increasing digit-only lines outside the horizontal extent of the text;
- a page has two columns when more text lies entirely on each side of the middle than crosses it, or when
  it has lines side by side across the middle and most pages of the same size are two-column (a first
  page whose title and abstract span both columns).
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pymupdf as fitz

from crepe_mcp.reader.heuristics import (
    COLUMN_OVERLAP,
    LINE_NUMBER_SEQUENCE,
    RUNNING_MIN_PAGES,
    RUNNING_PAGE_DISTANCE,
    RUNNING_SHIFT,
    SAME_ROW,
    SAME_SIZE,
    Metrics,
)

EXTRACT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_MEDIABOX_CLIP
PAGE_LABEL_RE = re.compile(r"^page\s+#(?:\s+of\s+#)?$|^-\s*#\s*-$", re.IGNORECASE)
LINE_NUMBER_RE = re.compile(r"^\d{1,4}$")
# Text mark-up annotations used to show revisions (looked up by name: PyMuPDF's type stubs omit them).
MARKUP_ANNOT_TYPES = tuple(
    getattr(fitz, name)
    for name in ("PDF_ANNOT_HIGHLIGHT", "PDF_ANNOT_UNDERLINE", "PDF_ANNOT_SQUIGGLY", "PDF_ANNOT_STRIKE_OUT")
)


@dataclass
class Line:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    font: str
    size: float
    bold: bool
    italic: bool
    mono: bool
    color: int
    region: str = "body"
    column: int = 0
    seq: int = 0
    id: int = 0
    lead_size: float = 0.0  # size of the first span: a smaller lead is a superscript mark such as an affiliation


@dataclass
class Page:
    number: int
    width: float
    height: float
    label: str
    source: str
    images: int
    drawings: int
    markup_annots: int = 0
    lines: list[Line] = field(default_factory=list)

    def body_lines(self) -> list[Line]:
        return sorted((line for line in self.lines if line.region == "body"), key=lambda line: line.seq)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.body_lines())


def _pattern(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.casefold().split()))


def _line_record(page_no: int, line: dict[str, Any]) -> Line | None:
    spans = [span for span in line.get("spans", []) if span.get("text")]
    text = " ".join("".join(span["text"] for span in spans).split())
    if not text:
        return None
    main = max(spans, key=lambda span: len(span["text"].strip()))
    font = str(main.get("font", ""))
    lowered = font.lower()
    flags = int(main.get("flags", 0))
    x0, y0, x1, y1 = (round(float(v), 2) for v in line["bbox"])
    record = Line(
        page=page_no,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        text=text,
        font=font,
        size=round(float(main.get("size", 0.0)), 2),
        bold=bool(flags & 16) or "bold" in lowered,
        italic=bool(flags & 2) or "italic" in lowered or font.endswith("It"),
        mono=bool(flags & 8) or any(key in lowered for key in ("mono", "courier", "cour")),
        color=int(main.get("color", 0)),
        lead_size=round(float(spans[0].get("size", 0.0)), 2),
    )
    if tuple(round(float(v)) for v in line.get("dir", (1, 0))) != (1, 0):
        record.region = "rotated"
    return record


def _justified(pieces: list[Line]) -> bool:
    """Pieces of one justified text line that MuPDF split at stretched word spaces: three or more consecutive pieces
    in one style on one baseline, separated by equal gaps narrower than an em (table cells are spaced unevenly)."""
    first = pieces[0]
    gaps = [b.x0 - a.x1 for a, b in zip(pieces, pieces[1:], strict=False)]
    return (
        len(pieces) >= 3
        and all(p.font == first.font and abs(p.size - first.size) <= SAME_SIZE * first.size
                and abs(p.y1 - first.y1) <= SAME_SIZE * first.size for p in pieces)
        and 0 < min(gaps) and max(gaps) <= first.size and max(gaps) - min(gaps) <= SAME_SIZE * first.size
    )


def _lines_from(page_no: int, data: Any) -> list[Line]:
    records = (_line_record(page_no, line) for block in data.get("blocks", []) for line in block.get("lines", []))
    lines = [record for record in records if record is not None]
    out: list[Line] = []
    index = 0
    while index < len(lines):
        end = index + 1
        while end < len(lines) and abs(lines[end].y1 - lines[index].y1) <= SAME_SIZE * lines[index].size \
                and lines[end].x0 > lines[end - 1].x1:
            end += 1
        run = lines[index:end]
        if _justified(run):
            out.append(replace(run[0], x1=run[-1].x1, y0=min(p.y0 for p in run), y1=max(p.y1 for p in run),
                               text=" ".join(p.text for p in run)))
        else:
            out.extend(run)
        index = end
    return out


def _read_page(page: Any, number: int) -> Page:
    data: Any = page.get_text("dict", flags=EXTRACT_FLAGS)
    lines = _lines_from(number, data)
    images = len(page.get_images(full=False))
    # A page without text is either blank or has no text layer (e.g. scanned); no OCR is attempted.
    source = "text" if lines else ("none" if images else "blank")
    return Page(
        number=number,
        width=round(float(page.rect.width), 2),
        height=round(float(page.rect.height), 2),
        label=str(page.get_label() or ""),
        source=source,
        images=images,
        drawings=len(page.get_cdrawings()),
        markup_annots=sum(1 for _ in page.annots(types=MARKUP_ANNOT_TYPES) or []),
        lines=lines,
    )


def measure(pages: list[Page]) -> Metrics:
    """Body font and size (most letters, so digit-only proof line numbers and page counters never count),
    median body line height and median gap within a column."""
    counter: Counter[tuple[str, float]] = Counter()
    for page in pages:
        for line in page.lines:
            if line.region == "body":
                counter[(line.font, round(line.size, 1))] += sum(1 for char in line.text if char.isalpha())
    if not counter:
        return Metrics("", 0.0, 0.0, 0.0)
    (font, size), _ = counter.most_common(1)[0]

    def is_body(line: Line) -> bool:
        return line.region == "body" and line.font == font and round(line.size, 1) == size

    heights = [line.y1 - line.y0 for page in pages for line in page.lines if is_body(line)]
    gaps: list[float] = []
    for page in pages:
        lines = sorted((line for line in page.lines if is_body(line)), key=lambda line: (line.column, line.y0))
        gaps += [b.y0 - a.y1 for a, b in zip(lines, lines[1:], strict=False) if a.column == b.column and b.y0 >= a.y1]
    line_height = round(statistics.median(heights), 2) if heights else size
    return Metrics(font, size, line_height, round(statistics.median(gaps), 2) if gaps else 0.0)


def _rows(lines: list[Line], tolerance: float) -> list[list[Line]]:
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda line: (line.y0 + line.y1) / 2):
        centre = (line.y0 + line.y1) / 2
        if rows and centre - (rows[-1][0].y0 + rows[-1][0].y1) / 2 <= tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    return rows


def _classify_margins(pages: list[Page], metrics: Metrics) -> None:
    tolerance = SAME_ROW * metrics.line_height
    seen: dict[str, list[tuple[int, float, str]]] = {}
    for page in pages:
        for line in page.lines:
            if line.region == "body":
                seen.setdefault(_pattern(line.text), []).append((page.number, line.y0, line.text))

    shift = RUNNING_SHIFT * metrics.line_height

    def marginal(page_no: int, line: Line) -> bool:
        key = _pattern(line.text)
        if PAGE_LABEL_RE.match(key):
            return True
        counter = LINE_NUMBER_RE.match(line.text) is not None
        if not counter and re.search(r"[a-z]", key) is None:
            return False
        # each number of a running line is constant ('VOLUME 11') or moves with the page ('Page 3 of 17'), and a bare
        # page counter moves with the page; numbers that change otherwise belong to page content ('TABLE S.3', 'TABLE
        # S.7' at the top of successive pages), and a repeated bare number is a proof line number
        numbers = [int(value) for value in re.findall(r"\d+", line.text)]

        def follows(text: str, other_page: int) -> bool:
            others = [int(value) for value in re.findall(r"\d+", text)]
            shift_by = other_page - page_no
            return len(others) == len(numbers) and all(
                a - b == shift_by if counter else a in (b, b + shift_by) for a, b in zip(others, numbers, strict=True)
            )

        found = sorted({page_no} | {
            other_page for other_page, y, text in seen.get(key, [])
            if abs(y - line.y0) <= shift and follows(text, other_page)
        })
        first = last = found.index(page_no)
        while first > 0 and found[first] - found[first - 1] <= RUNNING_PAGE_DISTANCE:
            first -= 1
        while last + 1 < len(found) and found[last + 1] - found[last] <= RUNNING_PAGE_DISTANCE:
            last += 1
        return last - first + 1 >= RUNNING_MIN_PAGES

    for page in pages:
        rows = _rows([line for line in page.lines if line.region == "body"], tolerance)
        for ordered, region in ((rows, "header"), (rows[::-1], "footer")):
            for row in ordered:
                if any(line.region != "body" for line in row) or not all(marginal(page.number, line) for line in row):
                    break
                for line in row:
                    line.region = region


def _classify_line_numbers(page: Page) -> None:
    body = [line for line in page.lines if line.region == "body"]
    words = [line for line in body if not LINE_NUMBER_RE.match(line.text)]
    if not words:
        return
    left = min(line.x0 for line in words)
    right = max(line.x1 for line in words)
    numbers = [line for line in body if LINE_NUMBER_RE.match(line.text)]
    for side in ([line for line in numbers if line.x1 <= left], [line for line in numbers if line.x0 >= right]):
        run = sorted(side, key=lambda line: line.y0)
        values = [int(line.text) for line in run]
        if len(run) >= LINE_NUMBER_SEQUENCE and all(b > a for a, b in zip(values, values[1:], strict=False)):
            for line in run:
                line.region = "linenumber"


def _columns(page: Page, metrics: Metrics) -> tuple[bool, bool]:
    """Assign each body line to the left (0) or right (1) half or to both (-1); return whether the page is
    two-column on its own and whether it has left and right lines side by side on one row."""
    body = [line for line in page.lines if line.region == "body"]
    mid = page.width / 2
    tolerance = COLUMN_OVERLAP * metrics.body_size
    for line in body:
        if line.x1 <= mid + tolerance:
            line.column = 0
        elif line.x0 >= mid - tolerance:
            line.column = 1
        else:
            line.column = -1
    # characters, not lines: the short cells of a table beside the middle must not outweigh prose
    sides = [sum(len(line.text) for line in body if line.column == side) for side in (0, 1, -1)]
    row = SAME_ROW * metrics.line_height
    left = [(line.y0 + line.y1) / 2 for line in body if line.column == 0]
    right = [(line.y0 + line.y1) / 2 for line in body if line.column == 1]
    side_by_side = any(abs(a - b) <= row for a in left for b in right)
    return min(sides[0], sides[1]) > sides[2], side_by_side


def _order(page: Page, two_columns: bool, row: float) -> None:
    """Reading order: bands separated by full-width lines, left column then right; within a column top to bottom, and
    lines whose vertical centres lie within a row of each other (a text line split at inline math) left to right."""
    body = [line for line in page.lines if line.region == "body"]

    def position(line: Line) -> tuple[float, float]:
        return (line.y0, line.x0)

    def rows_of(lines: list[Line]) -> list[Line]:
        ordered_rows: list[Line] = []
        current: list[Line] = []
        for line in sorted(lines, key=lambda line: (line.y0 + line.y1) / 2):
            if current and (line.y0 + line.y1) / 2 - (current[0].y0 + current[0].y1) / 2 > row:
                ordered_rows += sorted(current, key=lambda piece: piece.x0)
                current = []
            current.append(line)
        return ordered_rows + sorted(current, key=lambda piece: piece.x0)

    if not two_columns:
        for line in body:
            line.column = 0
        ordered = rows_of(body)
    else:
        spanning = sorted((line for line in body if line.column == -1), key=position)
        ordered = []
        top = float("-inf")
        for boundary in [*spanning, None]:
            limit = boundary.y0 if boundary is not None else float("inf")
            band = [line for line in body if line.column != -1 and top <= line.y0 < limit]
            ordered += rows_of([line for line in band if line.column == 0])
            ordered += rows_of([line for line in band if line.column == 1])
            if boundary is not None:
                ordered.append(boundary)
                top = boundary.y0

    rest = sorted(
        (line for line in page.lines if line.region != "body"), key=lambda line: (line.region, line.y0, line.x0)
    )
    for seq, line in enumerate([*ordered, *rest]):
        line.seq = seq


def extract(pdf_path: str | Path) -> list[Page]:
    """Extract every page of a PDF into classified, ordered line records."""
    with fitz.open(str(pdf_path)) as doc:
        pages = [_read_page(doc[i], i + 1) for i in range(len(doc))]
    metrics = measure(pages)
    _classify_margins(pages, metrics)
    layouts: dict[int, tuple[bool, bool]] = {}
    votes: dict[tuple[float, float], Counter[bool]] = {}
    for page in pages:
        _classify_line_numbers(page)
        layouts[page.number] = _columns(page, metrics)
        if any(line.region == "body" for line in page.lines):
            votes.setdefault((page.width, page.height), Counter())[layouts[page.number][0]] += 1
    for page in pages:
        own, side_by_side = layouts[page.number]
        size_votes = votes.get((page.width, page.height), Counter())
        _order(page, own or (side_by_side and size_votes[True] > size_votes[False]), SAME_ROW * metrics.line_height)
    return pages
