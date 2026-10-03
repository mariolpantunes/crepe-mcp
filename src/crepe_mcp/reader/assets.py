"""Numbered assets and their mentions, built after paragraphs and sections (deterministic, no LLM).

Kinds covered: figure, table, equation, algorithm, listing and reference. Tolerances are relative to the
document's body size and line height (see ``heuristics``):
- figure/table captions are caption paragraphs starting with a label (``FIGURE 3.``, ``Fig. 1.``,
  ``TABLE I``, ``Table 1:``, ``Fig. 5(a).``); the region is the nearest raster image, vector-drawing cluster or table
  found by PyMuPDF ``find_tables`` above or below the caption whose centre lies in the caption's column. A table
  is limited by its rules, not by the page: it ends at its last rule, so when no further rule of its own follows
  and the next page opens with rules with the same ends, it carries on there (``last_page``);
- equations are lines holding only ``(n)`` that are the rightmost item of their row, in the right half
  of the column; the equation text is the rest of that row;
- algorithms/listings start at a label line with a column-wide rule at its top and end at the next
  column-wide rule below it;
- references are the numbered ``[k]`` entries of the References section;
- mentions are label citations in text and captions (``Fig. 3``, ``Table II``, ``Eq. (5)``,
  ``Algorithm 1``, ``[12]``, ``[3]–[5]``), resolved within the same submission part; where only the parts of
  an item are numbered, a citation of the whole (``Fig. 5``) names every part of it (``5a``, ``5b``); a citation range
  never extends past the part's highest reference number; a bare ``(n)`` matching an equation number is a
  weak mention.
"""

from __future__ import annotations

import os
import pickle
import re
import statistics
import subprocess
import sys
import tempfile
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf as fitz

from crepe_mcp.reader.document import Line, Page
from crepe_mcp.reader.equations import equation_text
from crepe_mcp.reader.heuristics import (
    CAPTION_REGION,
    FIGURE_MIN_HEIGHT,
    FIGURE_MIN_WIDTH,
    INDENT,
    RULE_ALIGNMENT,
    RULE_MAX_THICKNESS,
    RULE_MIN_LENGTH,
    SAME_ROW,
    Metrics,
)
from crepe_mcp.reader.indexes import (
    CAPTION_RE,
    LABEL_END,
    LABEL_NUMBER,
    LABEL_ONLY_RE,
    PROOF_RE,
    REFERENCES_RE,
    SENTENCE_END_RE,
    STATEMENT_KINDS,
    STATEMENT_RE,
    Paragraph,
    Section,
)

Rect = tuple[float, float, float, float]

FIGURE_TABLE_RE = re.compile(
    rf"^(?P<kind>figure|fig|table|tab)\.?\s*(?P<number>{LABEL_NUMBER}){LABEL_END}", re.IGNORECASE)
BLOCK_RE = re.compile(r"^(?P<kind>algorithm|listing)\s+(?P<number>\d+)\b", re.IGNORECASE)
EQUATION_NUMBER_RE = re.compile(r"^\((?P<number>\d{1,3})\)$")
REFERENCE_RE = re.compile(r"^\[(?P<number>\d{1,4})\]")
STATEMENT_BUDGET = 3000  # characters of a statement and its proof (reply budget)
# A citation names one item or a list of them: 'Fig. 3', 'Figs. 3 and 4', 'Tables 1-3', 'Eqs. (5)-(7)'
_ITEM = rf"\(?{LABEL_NUMBER}\)?"
_LIST = rf"({_ITEM}(?:\s*(?:,|and|&|or|–|-|to)\s*{_ITEM})*)"
MENTION_RES = {
    "figure": re.compile(rf"\bFig(?:ure)?s?\.?\s*{_LIST}", re.IGNORECASE),
    "table": re.compile(rf"\bTables?\s+{_LIST}", re.IGNORECASE),
    "algorithm": re.compile(rf"\bAlgorithms?\s+{_LIST}", re.IGNORECASE),
    "listing": re.compile(rf"\bListings?\s+{_LIST}", re.IGNORECASE),
    "equation": re.compile(
        r"\b(?:Eqs?\.|Equations?)\s*(\(\d+\)(?:\s*(?:,|and|&|or|–|-|to)\s*\(\d+\))*)", re.IGNORECASE
    ),
    **{kind: re.compile(rf"\b{kind}s?\s+{_LIST}", re.IGNORECASE) for kind in STATEMENT_KINDS},
}
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10}
BARE_EQUATION_RE = re.compile(r"\((\d{1,3})\)")
PROSE_WORD_RE = re.compile(r"\b(?!(?:max|min|sup|inf|lim|log|exp|argmax|argmin|if|otherwise)\b)[A-Za-z]{3,}\b")
CITATION_RANGE_RE = re.compile(r"\[(\d{1,4})\]\s*[-–]\s*\[(\d{1,4})\]")
CITATION_RE = re.compile(r"\[(\d{1,4}(?:\s*[-–,]\s*\d{1,4})*)\]")


@dataclass
class Asset:
    id: str
    kind: str
    number: str
    label: str
    page: int
    bbox: Rect | None
    caption: str
    content: str
    content_format: str
    method: str
    confidence: str
    paragraph: int
    column: int = 0
    y: float = 0.0
    seq: int = 0
    segment: int = 0
    last_page: int = 0  # a table ends at its last rule, which may be on a later page


@dataclass
class Mention:
    asset: str
    paragraph: int
    page: int
    text: str
    strength: str
    segment: int = 0


@dataclass
class _Geometry:
    images: list[Rect] = field(default_factory=list)
    shapes: list[Rect] = field(default_factory=list)
    rules: list[Rect] = field(default_factory=list)
    tables: list[Rect] = field(default_factory=list)
    joined: list[Rect] = field(default_factory=list)  # rule pieces of one row joined, top to bottom
    ruled: list[Rect] = field(default_factory=list)  # table regions bounded by aligned horizontal rules


def _rect(values: Any) -> Rect:
    x0, y0, x1, y1 = (round(float(v), 2) for v in values)
    return (x0, y0, x1, y1)


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _near(a: Rect, b: Rect, pad: float) -> bool:
    return a[0] - pad <= b[2] and b[0] - pad <= a[2] and a[1] - pad <= b[3] and b[1] - pad <= a[3]


def _centre_inside(inner: Rect, outer: Rect) -> bool:
    x, y = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def _cluster(rects: list[Rect], pad: float) -> list[Rect]:
    clusters: list[Rect] = []
    for rect in sorted(rects):
        merged = rect
        changed = True
        while changed:
            changed = False
            for other in list(clusters):
                if _near(merged, other, pad):
                    clusters.remove(other)
                    merged = _union(merged, other)
                    changed = True
        clusters.append(merged)
    return sorted(clusters)


def _text_blocks(pages: list[Page]) -> dict[tuple[float, float], tuple[float, float]]:
    """Usual top and bottom of body text for each page size (median over the pages of that size)."""
    extents: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for page in pages:
        body = page.body_lines()
        if body:
            extents.setdefault((page.width, page.height), []).append(
                (min(line.y0 for line in body), max(line.y1 for line in body))
            )
    return {
        size: (statistics.median(top for top, _ in values), statistics.median(bottom for _, bottom in values))
        for size, values in extents.items()
    }


def _geometry(
    pdf_page: Any, page: Page, with_tables: bool, metrics: Metrics, text_block: tuple[float, float],
    drawings: list[dict[str, Any]] | None = None,
) -> _Geometry:
    em, line_height = metrics.body_size, metrics.line_height
    top, bottom = text_block[0] - SAME_ROW * line_height, text_block[1] + SAME_ROW * line_height

    def beyond_text(rect: Rect) -> bool:
        # outside the page size's usual text block, e.g. a journal logo in the top margin; floats at the top
        # of a page lie inside it even when no text line precedes them on that page
        return rect[3] <= top or rect[1] >= bottom

    geometry = _Geometry()
    # image blocks are all that is needed here: the "blocks" form lists them with their boxes and, unlike "dict",
    # does not copy the image data (same boxes, several times faster)
    for block in pdf_page.get_text("blocks", flags=fitz.TEXTFLAGS_DICT):
        if block[6] == 1:
            rect = _rect(block[:4])
            if not beyond_text(rect):
                geometry.images.append(rect)
    pieces: list[Rect] = []
    paths: list[dict[str, Any]] = pdf_page.get_drawings() if drawings is None else drawings
    for drawing in paths:
        rect = _rect(drawing["rect"])
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if beyond_text(rect):
            continue
        if height <= RULE_MAX_THICKNESS * em and width >= RULE_MIN_LENGTH * em:
            geometry.rules.append(rect)
        elif height > RULE_MAX_THICKNESS * em and width > RULE_MAX_THICKNESS * em:
            pieces.append(rect)
    if with_tables:
        geometry.tables = [_rect(table.bbox) for table in pdf_page.find_tables(paths=paths).tables]
    geometry.shapes = [
        _with_labels(cluster, page, metrics)
        for cluster in _cluster(pieces, SAME_ROW * line_height)
        if cluster[3] - cluster[1] >= FIGURE_MIN_HEIGHT * line_height
        and cluster[2] - cluster[0] >= FIGURE_MIN_WIDTH * em
    ]
    geometry.images = sorted(set(geometry.images))
    geometry.rules.sort()
    if with_tables:
        em = metrics.body_size
        geometry.joined = sorted(_join_rules(geometry.rules, RULE_MAX_THICKNESS * em, RULE_ALIGNMENT * em),
                                 key=lambda rule: rule[1])
        geometry.ruled = _ruled_regions(geometry.joined, page, metrics)
    return geometry


def _with_labels(region: Rect, page: Page, metrics: Metrics) -> Rect:
    """A drawing extended by the lines in smaller type next to it (axis labels, legends, sub-captions): each added line
    lies on the page, within a line height of the region and inside its width, and no caption label lies between
    it and the region (the region never grows into a caption)."""
    captions = [line for line in page.body_lines() if CAPTION_RE.match(line.text)]
    labels = [
        line for line in page.body_lines()
        if metrics.smaller(line.size) and 0 <= line.y0 and line.y1 <= page.height and line not in captions
    ]
    grown = region
    changed = True
    while changed:
        changed = False
        for line in labels:
            box = (line.x0, line.y0, line.x1, line.y1)
            if not grown[0] <= (line.x0 + line.x1) / 2 <= grown[2] or _centre_inside(box, grown):
                continue
            low, high = min(grown[3], box[1]), max(grown[1], box[3])
            blocked = any(
                grown[0] <= (c.x0 + c.x1) / 2 <= grown[2]
                and (grown[3] <= c.y0 <= box[3] or box[1] <= c.y1 <= grown[1] or low <= c.y0 <= high)
                for c in captions
            )
            if not blocked and _near(grown, box, metrics.line_height):
                grown = _union(grown, box)
                changed = True
    return grown


def _ruled_regions(joined: list[Rect], page: Page, metrics: Metrics) -> list[Rect]:
    """Regions between aligned horizontal rules (booktabs tables): rules with the same ends are grouped from top to
    bottom, and a group is split where a caption lies between two rules."""
    align = RULE_ALIGNMENT * metrics.body_size
    captions = [line for line in page.body_lines() if CAPTION_RE.match(line.text)]
    groups: list[list[Rect]] = []
    for rule in sorted(joined, key=lambda r: r[1]):
        for group in groups:
            last = group[-1]
            if abs(rule[0] - last[0]) <= align and abs(rule[2] - last[2]) <= align and not any(
                last[3] < line.y0 and line.y1 < rule[1] and last[0] - align <= line.x0 <= last[2] for line in captions
            ):
                group.append(rule)
                break
        else:
            groups.append([rule])
    return [
        (min(r[0] for r in group), group[0][1], max(r[2] for r in group), group[-1][3])
        for group in groups
        if len(group) >= 2
    ]


def _table_run(page: int, region: Rect, rules: Callable[[int], list[Rect]], captions: Callable[[int], list[Line]],
               block: Callable[[int], tuple[float, float]], last: int, align: float) -> list[tuple[int, Rect]]:
    """The pages and regions one table covers. Its rules are its limit, not the page: the table ends at its last
    rule, and when no further rule of its own follows on the page, it carries on wherever the next page opens with
    rules with the same ends and no caption stands between the two. Booktabs' three rules and a table ruled on
    every row behave alike, since both simply end at their last rule. A page the table carries on past holds rows
    below its last rule, so that part reaches the foot of the page's text."""

    def mine(rule: Rect) -> bool:
        return abs(rule[0] - region[0]) <= align and abs(rule[2] - region[2]) <= align

    parts = [(page, region)]
    while page < last:
        if any(rule[1] > region[3] + align and mine(rule) for rule in rules(page)):
            break  # the table closed with a further rule of its own on this page
        following = rules(page + 1)
        if not following or not mine(following[0]):
            break  # the next page does not open with this table's rules
        if (any(line.y0 > region[3] for line in captions(page))
                or any(line.y1 < following[0][3] for line in captions(page + 1))):
            break  # a caption between the two starts another item
        run = [following[0]]
        for rule in following[1:]:
            if not mine(rule) or any(run[-1][3] < line.y0 and line.y1 < rule[1] for line in captions(page + 1)):
                break
            run.append(rule)
        parts[-1] = (page, (region[0], region[1], region[2], block(page)[1]))
        page += 1
        region = (min(rule[0] for rule in run), block(page)[0], max(rule[2] for rule in run), run[-1][3])
        parts.append((page, region))
        if len(run) < len(following):
            break  # rules of another item follow on this page, so the table closed on it
    return parts


def _merge_tables(parts: list[tuple[str, str]]) -> tuple[str, str]:
    """One table out of the parts of a run. A continuation carries no heading, so the rule under its first row is
    dropped and every row of it follows the rows of the part before."""
    texts = [(text, fmt) for text, fmt in parts if text.strip()]
    if not texts:
        return "", "text"
    merged = [texts[0][0]]
    for text, _ in texts[1:]:
        lines = text.split("\n")
        if len(lines) >= 2 and lines[0].startswith("|") and set(lines[1].strip()) <= set("|-"):
            lines = [lines[0], *lines[2:]]
        merged.append("\n".join(lines))
    return "\n".join(merged), "markdown" if all(fmt == "markdown" for _, fmt in texts) else "text"


def _bands(words: list[Any], same_row: float) -> list[list[Any]]:
    """Words grouped into text lines by their vertical centre."""
    bands: list[list[Any]] = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        centre = (w[1] + w[3]) / 2
        if bands and centre - (bands[-1][0][1] + bands[-1][0][3]) / 2 <= same_row:
            bands[-1].append(w)
        else:
            bands.append([w])
    return bands


def _table_grid(table: Any, words: list[Any], touch: float, same_row: float) -> list[list[str]]:
    """Cell texts of a detected table, rebuilt from the page words. A column boundary is dropped when it cuts through
    words in more rows than it separates words (it splits one column). A detected row is split into one row per text
    line when most of its filled columns hold one same number of lines (rows set without rules between them); cells
    wrapped over different numbers of lines stay whole. A row whose first cell is empty and whose text continues a
    sentence (lower case) belongs to the row above."""
    edges = sorted({round(x, 1) for row in table.rows for cell in row.cells if cell for x in (cell[0], cell[2])})
    if len(edges) < 3:
        return []
    detected = [[w for w in words if row.bbox[1] <= (w[1] + w[3]) / 2 <= row.bbox[3]] for row in table.rows]

    def keeps(x: float) -> bool:
        cut = sum(1 for line in rows if any(w[0] + touch < x < w[2] - touch for w in line))
        split = sum(
            1 for line in rows
            if not any(w[0] + touch < x < w[2] - touch for w in line)
            and any(w[2] <= x for w in line) and any(w[0] >= x for w in line)
        )
        return cut <= split

    rows = detected
    bounds = [edges[0], *(x for x in edges[1:-1] if keeps(x)), edges[-1]]

    def column_of(w: Any) -> int:
        centre = (w[0] + w[2]) / 2
        return next((i for i in range(len(bounds) - 1) if centre <= bounds[i + 1]), len(bounds) - 2)

    rows = []
    for line in detected:
        bands = _bands(line, same_row)
        columns = {column_of(w) for w in line}
        lines_in = [sum(1 for band in bands if any(column_of(w) == column for w in band)) for column in columns]
        several = [count for count in lines_in if count > 1]
        stacked = max((several.count(count) for count in several), default=0)
        rows.extend(bands if stacked * 2 > len(columns) else [line])
    grid: list[list[str]] = []
    for line in rows:
        cells: list[list[str]] = [[] for _ in range(len(bounds) - 1)]
        for w in sorted(line, key=lambda w: (w[5], w[6], w[0])):
            cells[column_of(w)].append(str(w[4]))
        texts = [" ".join(cell) for cell in cells]
        if not any(texts):
            continue
        continues = not texts[0] and all(not cell or cell[0].islower() for cell in texts[1:])
        if grid and continues:
            grid[-1] = [" ".join(part for part in (old, new) if part) for old, new in zip(grid[-1], texts, strict=True)]
        else:
            grid.append(texts)
    keep = [i for i in range(len(bounds) - 1) if any(row[i] for row in grid)]
    return [[row[i] for i in keep] for row in grid]


def _markdown(grid: list[list[str]]) -> str:
    rows = [[cell.replace("|", "\\|") for cell in row] for row in grid]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows[1:]])


def _word_spacing(words: list[Any], region: Rect, em: float) -> float:
    """Word spacing of the region's type: the median space between consecutive words of one text line outside the
    region, scaled by the ratio of the median word heights inside and outside it (spacing grows with the type size)."""
    lines: dict[tuple[int, int], list[Any]] = {}
    inside: list[float] = []
    outside: list[float] = []
    for w in words:
        if _centre_inside((w[0], w[1], w[2], w[3]), region):
            inside.append(w[3] - w[1])
        else:
            lines.setdefault((w[5], w[6]), []).append(w)
            outside.append(w[3] - w[1])
    gaps = [
        b[0] - a[2]
        for line in lines.values()
        for a, b in zip(sorted(line, key=lambda w: w[0]), sorted(line, key=lambda w: w[0])[1:], strict=False)
        if b[0] > a[2]
    ]
    spacing = statistics.median(gaps) if gaps else 0.25 * em
    return spacing * statistics.median(inside) / statistics.median(outside) if inside and outside else spacing


def _join_rules(rules: list[Rect], thickness: float, gap: float) -> list[Rect]:
    """Rules drawn in pieces joined: pieces on one line (tops within the rule thickness) that follow each other with at
    most `gap` between them. Parallel lines closer than a text row (plot grids) stay apart."""
    lines: list[list[Rect]] = []
    for rule in sorted(rules, key=lambda r: r[1]):
        if lines and rule[1] - lines[-1][0][1] <= thickness:
            lines[-1].append(rule)
        else:
            lines.append([rule])
    joined: list[Rect] = []
    for line in lines:
        start = len(joined)
        for piece in sorted(line):
            if len(joined) > start and piece[0] <= joined[-1][2] + gap:
                joined[-1] = _union(joined[-1], piece)
            else:
                joined.append(piece)
    return joined


def _horizontal_rules(drawings: list[Any], thickness: float) -> list[tuple[float, float, float]]:
    """(y, x0, x1) of the thin horizontal rules, with rules drawn as touching segments on one line joined."""
    pieces = [_rect(r) for r in drawings if r.height <= thickness]
    return [(r[1], r[0], r[2]) for r in _join_rules(pieces, thickness, thickness)]


_Row = tuple[float, list[tuple[float, float, str]]]  # vertical centre and the (x0, x1, text) phrases of a text row


def _numeric(text: str) -> bool:
    """A cell of numbers: digits with decimal marks, signs, ±, ×, % and brackets only."""
    return any(ch.isdigit() for ch in text) and all(ch.isdigit() or ch in ".,±×%−-+()[] " for ch in text)


def _phrase_panels(words: list[Any], region: Rect, rules: list[tuple[float, float, float]], joint: float,
                   same_row: float, align: float, touch: float) -> list[tuple[str, list[list[str]]]]:
    """Table cells from phrases (words closer than `joint`, split at column edges: where most rows start a word and
    some row leaves a wider space before it), per panel: a centred row of one phrase with a rule above
    and below it titles the panel that follows when the next row is a header (no numeric cell), and each panel gets
    its own columns and header; before data rows it is a section label of the same grid."""
    inside = [w for w in words if _centre_inside((w[0], w[1], w[2], w[3]), region)]
    ordered = [sorted(band, key=lambda w: w[0]) for band in _bands(inside, same_row)]
    starts = sorted((w[0], index, w[2]) for index, band in enumerate(ordered) for w in band)
    lefts = [x for x, _, _ in starts]
    opened = {(w[0], index) for index, band in enumerate(ordered)
              for before, w in zip([None, *band], band, strict=False) if before is None or w[0] - before[2] > joint}

    def column_edge(x: float) -> bool:
        # the left edge of a column of left-aligned text: most rows start a word there, most of those words open a
        # phrase (after a wide space or at the row start), and most opening words end at different places (ragged
        # text, unlike a column of equal-width numbers)
        there = starts[bisect_left(lefts, x - touch):bisect_right(lefts, x + touch)]
        rows_there = {index for _, index, _ in there}
        opening = [(index, x1) for x0, index, x1 in there if (x0, index) in opened]
        ends = sorted(x1 for _, x1 in opening)
        distinct_ends = 1 + sum(1 for a, b in zip(ends, ends[1:], strict=False) if b - a > touch)
        return (len(rows_there) * 2 > len(ordered) and len({index for index, _ in opening}) * 2 > len(rows_there)
                and distinct_ends * 2 > len(ends))

    rows: list[tuple[float, list[tuple[float, float, str]]]] = []
    for band in ordered:
        groups: list[list[Any]] = []
        for w in band:
            gap = w[0] - groups[-1][-1][2] if groups else 0.0
            if groups and gap <= joint and not (gap > 0 and column_edge(w[0])):
                groups[-1].append(w)
            else:
                groups.append([w])
        centre = statistics.median((w[1] + w[3]) / 2 for w in band)
        rows.append((centre, [(g[0][0], max(w[2] for w in g), " ".join(str(w[4]) for w in g)) for g in groups]))
    centre_x = (region[0] + region[2]) / 2
    panels: list[tuple[str, list[_Row]]] = [("", [])]
    for index, (centre, phrases) in enumerate(rows):
        x0, x1, text = phrases[0]
        titles = (
            len(phrases) == 1 and 0 < index < len(rows) - 1 and abs((x0 + x1) / 2 - centre_x) <= align
            and any(rows[index - 1][0] < y < centre for y, _, _ in rules)
            and any(centre < y < rows[index + 1][0] for y, _, _ in rules)
            and not any(_numeric(cell) for _, _, cell in rows[index + 1][1])
        )
        if titles:
            panels.append((text, []))
        else:
            panels[-1][1].append((centre, phrases))
    return [(title, _panel_grid(panel, [rule for rule in rules if panel[0][0] < rule[0] < panel[-1][0]], align))
            for title, panel in panels if panel]


def _panel_grid(rows: list[_Row], rules: list[tuple[float, float, float]], align: float) -> list[list[str]]:
    """Columns are the merged horizontal extents of the phrases of the body rows (a row of one phrase votes unless it
    spans columns); the header ends at the first rule across the table (the next one when the rows above hold only
    group labels) and its rows collapse into one. A phrase of the last header row heads the column it overlaps most;
    a phrase above it heads every column it overlaps, or every column under the rule drawn beneath it."""
    if len(rows) < 2:
        return []
    widest = max((x1 - x0 for _, x0, x1 in rules), default=0.0)
    top_rows = 1
    for y, x0, x1 in rules:
        if x1 - x0 < widest - align:
            continue
        top_rows = max(1, sum(1 for centre, _ in rows if centre < y))
        if any(len(phrases) > 1 for _, phrases in rows[:top_rows]):
            break
    else:
        top_rows = 1
    header, body = rows[:top_rows], rows[top_rows:]
    def merged(phrases: list[tuple[float, float, str]]) -> list[list[float]]:
        extents: list[list[float]] = []
        for x0, x1, _ in sorted(phrases):
            if extents and x0 <= extents[-1][1]:
                extents[-1][1] = max(extents[-1][1], x1)
            else:
                extents.append([x0, x1])
        return extents

    full = [phrase for _, phrases in body if len(phrases) > 1 for phrase in phrases]
    lone = [phrases[0] for _, phrases in body if len(phrases) == 1]
    spans = merged(full)
    spans = merged(full + [p for p in lone if sum(1 for s0, s1 in spans if min(p[1], s1) > max(p[0], s0)) <= 1])
    if len(spans) < 2 or not body:
        return []

    def overlapped(x0: float, x1: float) -> list[int]:
        return [i for i, (s0, s1) in enumerate(spans) if min(x1, s1) - max(x0, s0) > 0]

    def nearest(x0: float, x1: float) -> int:
        centre = (x0 + x1) / 2
        return min(range(len(spans)), key=lambda i: 0 if spans[i][0] <= centre <= spans[i][1]
                   else min(abs(centre - spans[i][0]), abs(centre - spans[i][1])))

    def under_rule(level: int, x0: float, x1: float) -> tuple[float, float]:
        if level + 1 >= len(header):
            return x0, x1
        centre, below = header[level][0], header[level + 1][0]
        for y, r0, r1 in rules:
            alone = [p for p in header[level][1] if r0 <= (p[0] + p[1]) / 2 <= r1]
            if centre < y < below and len(alone) == 1 and r0 <= (x0 + x1) / 2 <= r1:
                return r0, r1
        return x0, x1

    def widest_overlap(x0: float, x1: float) -> list[int]:
        columns = overlapped(x0, x1)
        if not columns:
            return [nearest(x0, x1)]
        return [max(columns, key=lambda i: min(x1, spans[i][1]) - max(x0, spans[i][0]))]

    head = [""] * len(spans)
    for level, (_, phrases) in enumerate(header):
        labels = level == len(header) - 1
        for phrase_x0, phrase_x1, text in phrases:
            x0, x1 = under_rule(level, phrase_x0, phrase_x1)
            for column in widest_overlap(x0, x1) if labels else overlapped(x0, x1) or [nearest(x0, x1)]:
                head[column] = f"{head[column]} {text}".strip()
    grid = [head]
    for _, phrases in body:
        cells = [""] * len(spans)
        for x0, x1, text in phrases:
            column = nearest(x0, x1)
            cells[column] = f"{cells[column]} {text}".strip()
        grid.append(cells)
    return _repair_rows(grid)


def _repair_rows(grid: list[list[str]]) -> list[list[str]]:
    """Join rows that belong together: a wrapped first cell joins the row below it, a row of text continuing the
    unfinished cells above it (empty first cell), and a sparse row filling only cells empty in the row above (a label
    set between rows) joins that row."""
    out = [grid[0]]
    body = grid[1:]
    index = 0
    while index < len(body):
        row = body[index]
        filled = [i for i, cell in enumerate(row) if cell]
        after = body[index + 1] if index + 1 < len(body) else None
        if filled == [0] and after is not None and after[0] and sum(1 for cell in after if cell) > 1:
            body[index + 1] = [f"{row[0]} {after[0]}", *after[1:]]
            index += 1
            continue
        previous = out[-1] if len(out) > 1 else None
        if previous is not None and not row[0] and filled:
            continues = all(
                previous[i] and previous[i][-1].isalpha() and not previous[i].endswith((".", ":", ";", "?", "!"))
                for i in filled
            )
            complementary = len(filled) * 2 <= len(row) and all(not previous[i] for i in filled)
            if continues or complementary:
                out[-1] = [f"{old} {new}".strip() for old, new in zip(previous, row, strict=True)]
                index += 1
                continue
        out.append(row)
        index += 1
    keep = [i for i in range(len(out[0])) if any(r[i] for r in out)]
    return [[r[i] for i in keep] for r in out]


def _table_text(
    pdf_page: Any, region: Rect, page: Page, metrics: Metrics, page_drawings: list[dict[str, Any]] | None = None
) -> tuple[str, str, str]:
    """(content, format, strategy) of a table region as plain Markdown. Cells come from phrases of the region's
    words (spacing measured on the page); a table drawn with vertical rules or cell boxes keeps PyMuPDF's ruling-line
    cells when its rules separate at least as many columns as the phrases do."""
    em, row = metrics.body_size, SAME_ROW * metrics.line_height
    thickness = RULE_MAX_THICKNESS * em
    align = RULE_ALIGNMENT * em
    clip = fitz.Rect(region[0] - align, region[1] - row, region[2] + align, region[3] + row)
    words = pdf_page.get_text("words")
    inside_region = (clip.x0, clip.y0, clip.x1, clip.y1)
    all_paths: list[dict[str, Any]] = pdf_page.get_drawings() if page_drawings is None else page_drawings
    drawings = [d["rect"] for d in all_paths if _near(_rect(d["rect"]), inside_region, 0)]
    vertical = [r for r in drawings if r.width <= thickness and r.height >= row * 2]
    boxes = [r for r in drawings if r.width > thickness and r.height > thickness]
    lines_grid: list[list[str]] = []
    if len(vertical) >= 3 or len(boxes) >= 3:
        best: tuple[int, list[list[str]]] | None = None
        for table in pdf_page.find_tables(clip=clip, strategy="lines", paths=all_paths).tables:
            grid = _table_grid(table, [w for w in words if _centre_inside((w[0], w[1], w[2], w[3]), inside_region)],
                               thickness, row)
            filled = sum(1 for line in grid for cell in line if cell)
            if len(grid) >= 2 and len(grid[0]) >= 2 and (best is None or filled > best[0]):
                best = (filled, grid)
        if best is not None:
            lines_grid = _repair_rows(best[1])
    inner = [rule for rule in _horizontal_rules(drawings, thickness)
             if rule[2] - rule[1] >= RULE_MIN_LENGTH * em and region[1] + row < rule[0] < region[3] - row]
    joint = 2 * _word_spacing(words, inside_region, em)
    panels = _phrase_panels(words, inside_region, inner, joint, row, align, thickness)
    columns = max((len(grid[0]) for _, grid in panels if len(grid) >= 2), default=0)
    if lines_grid and len(lines_grid[0]) >= columns:
        return _markdown(lines_grid), "markdown", "lines"
    if columns >= 2:
        parts = []
        for title, grid in panels:
            table = _markdown(grid) if len(grid) >= 2 and len(grid[0]) >= 2 else "\n".join(
                " ".join(cell for cell in line if cell) for line in grid)
            parts.append("\n".join(part for part in (title, table) if part))
        return "\n\n".join(part for part in parts if part), "markdown", "phrases"
    inside = [line for line in page.body_lines() if _centre_inside((line.x0, line.y0, line.x1, line.y1), region)]
    return "\n".join(_rows(inside, row)), "text", "rows"


def _display_block(
    number: Line, band: list[Line], body: list[Line], numbers: list[Line], metrics: Metrics
) -> Rect:
    """The display equation around a numbered row: the row plus adjacent lines within a line height that are set
    apart from prose (indented from the column's usual left edge, or in smaller type: limits, scripts, fraction
    parts), up to the number's left edge. Growth stops at prose (three or more words), at pieces sharing a row with
    prose (inline fractions and scripts) and at other numbered rows."""
    em, line_height = metrics.body_size, metrics.line_height
    column = [line for line in body if line.column == number.column]
    lefts = [round(line.x0) for line in column] or [round(number.x0)]
    left = max(set(lefts), key=lefts.count)
    block = (float(left), number.y0, number.x0, number.y1)
    for line in band:
        block = _union(block, (line.x0, line.y0, min(line.x1, number.x0), line.y1))
    own = (number.y0 + number.y1) / 2
    others = [(other.y0 + other.y1) / 2 for other in numbers if other is not number]
    def in_prose_row(line: Line) -> bool:
        # the text row around the line (pieces split at inline math) starts at the column's left edge and holds prose
        centre = (line.y0 + line.y1) / 2
        row = [other for other in column if abs((other.y0 + other.y1) / 2 - centre) <= SAME_ROW * line_height]
        return (min(other.x0 for other in row) < left + INDENT * em
                and sum(len(PROSE_WORD_RE.findall(other.text)) for other in row) >= 3)

    def displayed(line: Line) -> bool:
        centre = (line.y0 + line.y1) / 2
        return (
            line is not number and line not in band and line.x1 <= number.x0 + em and line.x0 >= left - em
            and (line.x0 >= left + INDENT * em or metrics.smaller(line.size))
            and len(PROSE_WORD_RE.findall(line.text)) < 3 and not CAPTION_RE.match(line.text)
            and not any(abs(centre - other) < abs(centre - own) for other in others)
            and not in_prose_row(line)
        )

    grown = True
    while grown:
        grown = False
        for line in body:
            box = (line.x0, line.y0, line.x1, line.y1)
            near = line.y1 >= block[1] - line_height and line.y0 <= block[3] + line_height
            if near and not _centre_inside(box, block) and displayed(line):
                block = _union(block, box)
                grown = True
    return block


def _column_range(page: Page, column: int) -> tuple[float, float]:
    lines = page.body_lines()
    selected = [line for line in lines if column == -1 or line.column == column] or lines
    if not selected:
        return (0.0, page.width)
    return (min(line.x0 for line in selected), max(line.x1 for line in selected))


def _nearest(
    caption: Rect, options: list[tuple[str, str, Rect]], x_range: tuple[float, float], metrics: Metrics
) -> tuple[str, str, Rect] | None:
    """The region closest above or below the caption, in the caption's column or spanning the caption (a page-wide
    table under a caption set in one column)."""
    lowest, highest = -SAME_ROW * metrics.line_height, CAPTION_REGION * metrics.line_height
    align = RULE_ALIGNMENT * metrics.body_size
    best: tuple[float, int, tuple[str, str, Rect]] | None = None
    for option in options:
        rect = option[2]
        crosses_column = rect[0] < x_range[1] - align < x_range[1] + align < rect[2] or (
            rect[0] < x_range[0] - align < x_range[0] + align < rect[2])
        if not (x_range[0] <= (rect[0] + rect[2]) / 2 <= x_range[1] or crosses_column):
            continue
        for distance, preference in ((caption[1] - rect[3], 0), (rect[1] - caption[3], 1)):
            if lowest <= distance <= highest and (best is None or (distance, preference) < (best[0], best[1])):
                best = (distance, preference, option)
    return best[2] if best else None


def _rows(lines: list[Line], tolerance: float) -> list[str]:
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda line: ((line.y0 + line.y1) / 2, line.x0)):
        centre = (line.y0 + line.y1) / 2
        if rows and abs(centre - (rows[-1][0].y0 + rows[-1][0].y1) / 2) <= tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [" ".join(line.text for line in sorted(row, key=lambda line: line.x0)) for row in rows]


def _number(value: str) -> str:
    """Canonical item number: 'iv' -> 'IV', '03' -> '3', '3.2' -> '3.2', 'S.3' / 's 3' -> 'S3', and the letter of
    a part written any of the printed ways, '5(a)' / '5.(b)' / '5A' -> '5a'."""
    value = value.strip()
    suffix = ""
    part = re.search(r"\.?\s?\(?([A-Za-z])\)?$", value)
    if part and not re.fullmatch(r"[IVXivx]+", value):
        suffix = part.group(1).lower()
        value = value[:part.start()].strip()
    if re.fullmatch(r"[IVXivx]+", value):
        return value.upper() + suffix
    prefix = re.match(r"^([A-Za-z])\.?\s?(?=\d)", value)
    digits = value[prefix.end():] if prefix else value
    return ((prefix.group(1).upper() if prefix else "")
            + ".".join(str(int(part)) for part in digits.split(".")) + suffix)


def _roman_value(value: str) -> int:
    total = 0
    for char, following in zip(value, [*value[1:], ""], strict=True):
        worth = ROMAN_VALUES[char]
        total += -worth if following and ROMAN_VALUES[following] > worth else worth
    return total


def _roman(number: int) -> str:
    out = ""
    for worth, symbol in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while number >= worth:
            out, number = out + symbol, number - worth
    return out


def _item_numbers(listing: str) -> list[str]:
    """Numbers named by a citation list: '3 and 4' -> 3, 4; '1–3' -> 1, 2, 3; 'I-III' -> I, II, III; 'S.1–S.3' ->
    S1, S2, S3; '6(a) and 6(b)' -> 6a, 6b. Ranges longer than 20 items are not expanded."""
    tokens = re.findall(rf"{LABEL_NUMBER}|–|-|to", listing)
    numbers: list[str] = []
    in_range = False
    for token in tokens:
        if token in ("–", "-", "to"):
            in_range = bool(numbers)
            continue
        number = _number(token)
        if in_range:
            numbers.extend(_between(numbers[-1], number))
            in_range = False
        numbers.append(number)
    return list(dict.fromkeys(numbers))


def _between(start: str, end: str) -> list[str]:
    """Item numbers strictly between two numbers of the same form (roman, or an optional letter and an integer)."""
    if re.fullmatch(r"[IVX]+", start) and re.fullmatch(r"[IVX]+", end):
        low, high = _roman_value(start), _roman_value(end)
        return [_roman(n) for n in range(low + 1, high)] if 0 < high - low <= 20 else []
    first, last = re.fullmatch(r"([A-Z]?)(\d+)", start), re.fullmatch(r"([A-Z]?)(\d+)", end)
    if not first or not last or first.group(1) != last.group(1):
        return []
    low, high = int(first.group(2)), int(last.group(2))
    return [f"{first.group(1)}{n}" for n in range(low + 1, high)] if 0 < high - low <= 20 else []


def _expand(spec: str, highest: int) -> list[str]:
    """Numbers of a citation such as '3, 5-7'; a range is expanded only when it stays within the reference list."""
    numbers: list[int] = []
    for part in re.split(r"\s*,\s*", spec):
        bounds = [int(bound) for bound in re.split(r"\s*[-–]\s*", part) if bound.isdigit()]
        if len(bounds) == 2 and bounds[0] < bounds[1] <= highest:
            numbers.extend(range(bounds[0], bounds[1] + 1))
        else:
            numbers.extend(bounds)
    return [str(n) for n in numbers]


PARALLEL_MIN_TABLE_PAGES = 3  # fewer table pages are faster in one process than the workers' start-up
MAX_GEOMETRY_WORKERS = 8
WORKER_TIMEOUT = 600  # seconds a worker may take before its pages are computed here instead


def _geometries_in_workers(
    pdf_path: Path, numbers: list[int], pages: dict[int, Page], metrics: Metrics,
    blocks: dict[int, tuple[float, float]],
) -> dict[int, _Geometry]:
    """Geometry (with tables) of the given pages computed by worker processes; pages a worker did not deliver are
    simply missing from the result. Each page is independent, so the result equals computing them one by one."""
    workers = min(len(numbers), os.cpu_count() or 1, MAX_GEOMETRY_WORKERS)
    if workers < 2:
        return {}
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in sys.path if path)}
    found: dict[int, _Geometry] = {}
    with tempfile.TemporaryDirectory(prefix="crepe-geometry-") as tmp:
        running: list[tuple[subprocess.Popen[bytes], Path]] = []
        try:
            for index in range(workers):
                share = numbers[index::workers]
                tasks, out = Path(tmp) / f"tasks{index}.pkl", Path(tmp) / f"out{index}.pkl"
                tasks.write_bytes(pickle.dumps([(n, pages[n], metrics, blocks[n]) for n in share]))
                cmd = [sys.executable, "-m", "crepe_mcp.reader.geometry_worker", str(pdf_path), str(tasks), str(out)]
                running.append((subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL), out))
            for process, out in running:
                try:
                    if process.wait(timeout=WORKER_TIMEOUT) == 0 and out.is_file():
                        found.update(pickle.loads(out.read_bytes()))
                except subprocess.TimeoutExpired:
                    process.kill()
        except OSError:
            pass
        finally:
            for process, _ in running:
                if process.poll() is None:
                    process.kill()
                process.wait()
    return found


def build_assets(
    pdf_path: Path,
    pages: list[Page],
    paragraphs: list[Paragraph],
    sections: list[Section],
    segment_of_page: dict[int, int],
    metrics: Metrics,
) -> tuple[list[Asset], list[Mention]]:
    """Detect numbered assets and the paragraphs citing them, per submission part (e.g. manuscript copy)."""
    em, tolerance = metrics.body_size, SAME_ROW * metrics.line_height
    lines_by_id = {line.id: line for page in pages for line in page.lines}
    page_by_number = {page.number: page for page in pages}
    paragraph_of_line = {
        line_id: paragraph.id
        for paragraph in paragraphs
        for line_id in range(paragraph.first_line, paragraph.last_line + 1)
    }

    captions: list[tuple[Paragraph, str, str, str]] = []
    for paragraph in paragraphs:
        match = FIGURE_TABLE_RE.match(paragraph.text) if paragraph.kind == "caption" else None
        if match:
            kind = "figure" if match.group("kind").lower().startswith("fig") else "table"
            captions.append((paragraph, kind, _number(match.group("number")), match.group(0)))
    blocks = [(line, match) for page in pages for line in page.body_lines() if (match := BLOCK_RE.match(line.text))]

    caption_pages = {lines_by_id[p.first_line].page for p, _, _, _ in captions}
    geometry_pages = sorted(caption_pages | {line.page for line, _ in blocks})
    table_pages = {lines_by_id[p.first_line].page for p, kind, _, _ in captions if kind == "table"}
    geometry: dict[int, _Geometry] = {}
    text_blocks = _text_blocks(pages)
    last_page = max(page.number for page in pages)
    doc = fitz.open(str(pdf_path))
    drawings_cache: dict[int, list[dict[str, Any]]] = {}

    def drawings_of(number: int) -> list[dict[str, Any]]:
        """The vector graphics of a page, read once and shared by the geometry and the table text."""
        if number not in drawings_cache:
            drawings_cache[number] = doc[number - 1].get_drawings()
        return drawings_cache[number]

    def geometry_of(number: int, with_tables: bool = True) -> _Geometry:
        found = geometry.get(number)
        if found is None:
            page = page_by_number[number]
            text_block = text_blocks.get((page.width, page.height), (0.0, page.height))
            found = _geometry(doc[number - 1], page, with_tables, metrics, text_block, drawings_of(number))
            geometry[number] = found
        return found

    # find_tables dominates the build and every page is independent: the table pages go to worker processes
    # (the parent meanwhile does the cheap pages); the results are those of computing them one after another.
    if len(table_pages) >= PARALLEL_MIN_TABLE_PAGES:
        table_blocks = {n: text_blocks.get((page_by_number[n].width, page_by_number[n].height),
                                           (0.0, page_by_number[n].height)) for n in table_pages}
        geometry.update(_geometries_in_workers(pdf_path, sorted(table_pages), page_by_number, metrics, table_blocks))
    for number in geometry_pages:
        if number not in geometry:
            geometry_of(number, number in table_pages)

    def text_block(number: int) -> tuple[float, float]:
        page = page_by_number[number]
        return text_blocks.get((page.width, page.height), (0.0, page.height))

    def caption_lines(number: int) -> list[Line]:
        return [line for line in page_by_number[number].body_lines() if CAPTION_RE.match(line.text)]

    assets: dict[tuple[int, str], Asset] = {}

    def add(asset: Asset) -> None:
        asset.segment = segment_of_page.get(asset.page, 0)
        key = (asset.segment, asset.id)
        kept = assets.get(key)
        # a label printed without its text (a stray 'Fig. 1.' above the figure) loses to the caption carrying it
        if kept is None or (LABEL_ONLY_RE.match(kept.caption.strip())
                            and not LABEL_ONLY_RE.match(asset.caption.strip())):
            assets[key] = asset

    for paragraph, kind, number, label in captions:
        first = lines_by_id[paragraph.first_line]
        span = [lines_by_id[i] for i in range(paragraph.first_line, paragraph.last_line + 1)
                if i in lines_by_id and lines_by_id[i].page == first.page]
        caption_rect = (min(ln.x0 for ln in span), first.y0, max(ln.x1 for ln in span), max(ln.y1 for ln in span))
        page = page_by_number[first.page]
        found = geometry[first.page]
        if kind == "figure":
            options = [("caption+image", "high", r) for r in found.images]
            options += [("caption+drawing", "medium", r) for r in found.shapes]
        else:
            options = [("caption+table", "high", r) for r in found.tables]
            options += [("caption+rules", "medium", r) for r in found.ruled]
        choice = _nearest(caption_rect, options, _column_range(page, first.column), metrics)
        method, confidence, bbox = choice if choice else ("caption", "low", None)
        content, content_format = "", "text"
        ends = first.page
        if bbox is not None and kind == "table":
            run = ([(first.page, bbox)] if method == "caption+table" else _table_run(
                first.page, bbox, lambda number: geometry_of(number).joined, caption_lines, text_block,
                last_page, RULE_ALIGNMENT * em))
            texts = [_table_text(doc[number - 1], part, page_by_number[number], metrics, drawings_of(number))
                     for number, part in run]
            content, content_format = _merge_tables([(text, fmt) for text, fmt, _ in texts])
            ends = run[-1][0]
            confidence = "high" if texts[0][2] == "lines" and method == "caption+table" else "medium"
        elif bbox is not None:
            region = bbox
            span_ids = {s.id for s in span}
            inner = [ln for ln in page.body_lines() if ln.id not in span_ids
                     and _centre_inside((ln.x0, ln.y0, ln.x1, ln.y1), region)]
            content = "\n".join(ln.text for ln in inner)
        add(Asset(f"{kind}:{number}", kind, number, label, first.page, bbox, paragraph.text, content,
                  content_format, method, confidence, paragraph.id, first.column, first.y0, last_page=ends))

    for line, match in blocks:
        page = page_by_number[line.page]
        left, right = _column_range(page, line.column)
        rules = [r for r in geometry[line.page].rules if r[0] <= left + em and r[2] >= right - em]
        top = [r for r in rules if abs(r[1] - line.y0) <= tolerance]
        if not top:
            continue  # a sentence such as "Algorithm 1 details ..." rather than an algorithm block
        closing = [r[1] for r in rules if r[1] > line.y1 + tolerance]
        end = min(closing) if closing else max(ln.y1 for ln in page.body_lines())
        inside = [ln for ln in page.body_lines() if ln.id != line.id and ln.y0 >= line.y1 - tolerance
                  and ln.y1 <= end + tolerance and ln.x0 >= left - em and ln.x1 <= right + em]
        kind, number = match.group("kind").lower(), _number(match.group("number"))
        add(Asset(f"{kind}:{number}", kind, number, match.group(0), line.page, (left, top[0][1], right, end),
                  line.text, "\n".join(_rows(inside, tolerance)), "text", "caption+rules", "high",
                  paragraph_of_line.get(line.id, 0), line.column, line.y0))

    for page in pages:
        body = page.body_lines()
        number_lines = [line for line in body if EQUATION_NUMBER_RE.match(line.text)]
        for line in body:
            match = EQUATION_NUMBER_RE.match(line.text)
            if not match:
                continue
            left, right = _column_range(page, line.column)
            centre = (line.y0 + line.y1) / 2
            row = [ln for ln in body if ln.id != line.id
                   and (ln.column == line.column or -1 in (ln.column, line.column))
                   and abs((ln.y0 + ln.y1) / 2 - centre) <= tolerance]
            if line.x0 < (left + right) / 2 or any(ln.x0 >= line.x1 for ln in row):
                continue  # an equation number is the rightmost item of its row, in the right half of the column
            band = [ln for ln in row if not EQUATION_NUMBER_RE.match(ln.text)]
            block = _display_block(line, band, body, number_lines, metrics)
            number_box = (line.x0, line.y0, line.x1, line.y1)
            content, confidence = equation_text(doc[page.number - 1], block, number_box)
            content_format = "text+mathml"
            if not content:
                inside = [ln for ln in body
                          if ln is not line and _centre_inside((ln.x0, ln.y0, ln.x1, ln.y1), block)]
                content, content_format, confidence = "\n".join(_rows(inside, tolerance)), "text", "low"
            number = _number(match.group("number"))
            add(Asset(f"equation:{number}", "equation", number, line.text, page.number, _union(block, number_box),
                      "", content, content_format, "number-line", confidence, paragraph_of_line.get(line.id, 0),
                      line.column, line.y0))

    displays: dict[int, list[Asset]] = {}
    for asset in assets.values():
        if asset.kind == "equation" and asset.bbox is not None:
            displays.setdefault(asset.page, []).append(asset)

    def display_of(paragraph: Paragraph) -> Asset | None:
        first = lines_by_id[paragraph.first_line]
        box = (first.x0, first.y0, first.x1, first.y1)
        return next((a for a in displays.get(first.page, []) if a.bbox and _centre_inside(box, a.bbox)), None)

    def statement_text(paragraph: Paragraph, shown: set[str]) -> str:
        """A paragraph of a statement, or the rebuilt equation it belongs to (once)."""
        display = display_of(paragraph)
        if display is None:
            return paragraph.text
        if display.id in shown:
            return ""
        shown.add(display.id)
        return f"{display.content.split(chr(10))[0]}   ({display.number})"

    for index, paragraph in enumerate(paragraphs):
        match = STATEMENT_RE.match(paragraph.text) if paragraph.kind == "text" else None
        if not match:
            continue
        parts = [paragraph.text]
        shown: set[str] = set()
        in_proof = False
        for following in paragraphs[index + 1:]:
            stop = following.kind == "heading" or following.section != paragraph.section
            if stop or STATEMENT_RE.match(following.text):
                break
            if PROOF_RE.match(following.text):
                in_proof = True
            elif not in_proof and SENTENCE_END_RE.search(parts[-1]):
                break  # a statement runs until a paragraph ends a sentence (displays may interrupt it), then its proof
            text = statement_text(following, shown)
            if text:
                parts.append(text)
            if sum(len(part) for part in parts) > STATEMENT_BUDGET or following.text.rstrip().endswith(("∎", "□")):
                break
        kind, number = match.group("kind").lower(), _number(match.group("number"))
        first = lines_by_id[paragraph.first_line]
        add(Asset(f"{kind}:{number}", "statement", number, match.group(0).rstrip(".: "), paragraph.page, None, "",
                  "\n\n".join(parts)[:STATEMENT_BUDGET], "text", "label-paragraph", "medium", paragraph.id,
                  first.column, first.y0))

    for section in sections:
        if not REFERENCES_RE.match(section.title):
            continue
        following = [s.paragraph for s in sections if s.id > section.id and s.level <= section.level]
        end = min(following) if following else len(paragraphs) + 1
        for paragraph in paragraphs:
            match = REFERENCE_RE.match(paragraph.text) if paragraph.kind == "reference" else None
            if match and section.paragraph < paragraph.id < end:
                number = _number(match.group("number"))
                first = lines_by_id[paragraph.first_line]
                add(Asset(f"reference:{number}", "reference", number, match.group(0), paragraph.page, None, "",
                          paragraph.text, "text", "numbered-entry", "high", paragraph.id, first.column, first.y0))

    doc.close()
    ordered = sorted(assets.values(), key=lambda a: (a.page, a.column, a.y, a.kind, a.id))
    for seq, asset in enumerate(ordered):
        asset.seq = seq
    return ordered, _mentions(paragraphs, assets, segment_of_page)


def _cited(assets: dict[tuple[int, str], Asset], segment: int, asset_id: str) -> list[str]:
    """The asset an id names; when only the parts of an item are numbered, a citation of the whole item names
    every part of it ('Fig. 5' -> 'figure:5a' and 'figure:5b')."""
    if (segment, asset_id) in assets:
        return [asset_id]
    part = re.compile(rf"^{re.escape(asset_id)}[a-z]$")
    return sorted(key[1] for key in assets if key[0] == segment and part.match(key[1]))


def _mentions(
    paragraphs: list[Paragraph], assets: dict[tuple[int, str], Asset], segment_of_page: dict[int, int]
) -> list[Mention]:
    """Citations resolved within the paragraph's own submission part."""
    owned: dict[int, set[str]] = {}
    highest: dict[int, int] = {}
    for (segment, _), asset in assets.items():
        owned.setdefault(asset.paragraph, set()).add(asset.id)
        if asset.kind == "reference":
            highest[segment] = max(highest.get(segment, 0), int(asset.number))
    mentions: list[Mention] = []
    seen: set[tuple[int, str, int]] = set()
    for paragraph in paragraphs:
        if paragraph.kind not in ("text", "caption"):
            continue
        mine = owned.get(paragraph.id, set())
        segment = segment_of_page.get(paragraph.page, 0)
        equation_numbers = {a.number for (s, _), a in assets.items() if s == segment and a.kind == "equation"}

        def record(asset_id: str, text: str, strength: str, paragraph: Paragraph = paragraph,
                   mine: set[str] = mine, segment: int = segment) -> None:
            for cited in _cited(assets, segment, asset_id):
                if cited not in mine and (segment, cited, paragraph.id) not in seen:
                    seen.add((segment, cited, paragraph.id))
                    mentions.append(Mention(cited, paragraph.id, paragraph.page, text, strength, segment))

        for kind, pattern in MENTION_RES.items():
            for match in pattern.finditer(paragraph.text):
                for number in _item_numbers(match.group(1)):
                    record(f"{kind}:{number}", match.group(0), "strong")
        for match in CITATION_RANGE_RE.finditer(paragraph.text):
            for number in _expand(f"{match.group(1)}-{match.group(2)}", highest.get(segment, 0)):
                record(f"reference:{number}", match.group(0), "strong")
        for match in CITATION_RE.finditer(paragraph.text):
            for number in _expand(match.group(1), highest.get(segment, 0)):
                record(f"reference:{number}", match.group(0), "strong")
        for match in BARE_EQUATION_RE.finditer(paragraph.text):
            number = _number(match.group(1))
            if number in equation_numbers:
                record(f"equation:{number}", match.group(0), "weak")
    return mentions
