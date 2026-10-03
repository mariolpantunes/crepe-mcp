"""Paragraph and section indexes built from classified page lines (deterministic, no LLM).

Tolerances are relative to the document's measured body size and line height (see ``heuristics``):
- a heading candidate is a line that is not a cell of a table, set apart from
  body text (another font, bold, another size, or all capitals), that carries a section number (``I.``,
  ``A.``, ``1)``, ``1.2``) or is a standard unnumbered title, and is shorter than the longest body line of
  its column on the page (full prose lines reach it) unless it fills a single gap in an accepted numbering
  run. A section number printed as its own text line next to the title is joined to it, and a title that
  wraps (hyphen or full line) or continues on its row carries on in the same typography. Lines outside the
  column's usual text extent (margin notes) and digit-only lines (stray line numbers, equation numbers) do
  not make a cell, so a line-numbered manuscript still has headings; the later lines of a deep cell, which
  stand alone on their row, are cells of the table they continue.
  A number set smaller than its title (a superscript affiliation mark), math fonts other than the body
  font, math symbols, captions and pages without body-font text (cover sheets) are rejected; after
  References only appendices, acknowledgments, a new Abstract or a restart of the top-level numbering
  (a further manuscript copy) count;
- numbered candidates must form the document's own numbering: runs of consecutive numbers in one scheme
  and typeface (II after I, B after A, 2.3 after 2.2) are accepted when they start at the first number and
  continue, when they are at least ``numbering_run`` long, or when they resume an accepted run after a
  missed heading; a lone first number is accepted when its title is a standard section title or it is set
  like an accepted run of the same scheme. Author initials ("A. Author"), affiliation marks and footnotes
  therefore never become sections, without any font-size threshold;
- a caption is a label (full name or short name, ``Fig. 1.``, ``Figure 3 -``, ``Table 4:``), a separator
  and the caption text. A space is not a separator, so ``Table 5 compares ...`` is a sentence. A label
  alone on its line is a caption whose text is the line below (``Table 1`` / ``Summary of ...``). The
  letter of a part belongs to the number, so ``Fig. 5(a)`` and ``Fig. 5(b)`` are the items ``5a`` and
  ``5b``;
- a paragraph starts at a heading, a caption, a first-line indent, a font-size change, extra vertical
  space beyond the document's usual line gap, or a column/page change after a sentence end; hyphenated
  line breaks are joined; a caption holds no paragraph break, so the next label ends it, and a caption
  never continues into lines that share their row (table cells); inside References, entries start at
  ``[k]`` and continue across hanging indents.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from crepe_mcp.reader.document import Line, Page
from crepe_mcp.reader.heuristics import INDENT, NUMBERING_RUN, PARAGRAPH_SPACE, SAME_ROW, Metrics

MATH_FONT_RE = re.compile(r"^(?:CM(?:MI|SY|EX|R|BX|TI|SS)\d+|MSBM|MSAM|Symbol|STIX|Euclid|.*Math)", re.IGNORECASE)
MATH_CHAR_RE = re.compile(r"[=±×÷∈∉⊂⊆∑∏∫√∞≤≥≈→←↦∀∃θλμσπ]")
NUMBERED_RE = re.compile(
    r"^(?:(?P<roman>[IVX]{1,6})\.|(?P<letter>[A-Z])\.|(?P<paren>\d{1,2})\)|(?P<decimal>\d{1,2}(?:\.\d{1,2}){0,3})\.?)"
    r"\s+(?P<title>\S.*)$"
)
SECTION_NUMBER_RE = re.compile(r"^(?:[IVX]{1,6}\.?|[A-Z]\.|\d{1,2}\)|\d{1,2}(?:\.\d{1,2}){0,3}\.?)$")
UNNUMBERED_RE = re.compile(
    r"^(?:abstract|index terms|keywords|acknowledg(?:e)?ments?|references|bibliography|appendix(?:\s+\w+)?"
    r"|conclusions?|introduction|nomenclature)[.:]?$",
    re.IGNORECASE,
)
AFTER_REFERENCES_RE = re.compile(r"^(?:appendix(?:\s+\w+)?|acknowledg(?:e)?ments?|abstract)[.:]?$", re.IGNORECASE)
REFERENCES_RE = re.compile(r"^(?:references|bibliography)$", re.IGNORECASE)
# Printed item numbers: 3, IV, 3.2 (section-based), S3 / S.3 / A1 (supplementary or appendix), and the parts of a
# numbered item, 5a / 5(a) / 5.(b) -- the letter belongs to the number, so 5a and 5b are two items, not one.
LABEL_NUMBER = r"(?:[A-Z]\.?\s?\d+(?:\.\d+)?|\d+(?:\.\d+)?|[IVX]+)(?:\.?\s?\([a-z]\)|[a-z])?"
LABEL_END = r"(?![A-Za-z0-9])"  # a number may end in a letter or ')', where \b does not hold
CAPTION_KIND = r"(?:figure|fig|table|tab|algorithm|alg|listing)\.?"  # full name or short name, with or without '.'
CAPTION_RE = re.compile(rf"^{CAPTION_KIND}\s*{LABEL_NUMBER}{LABEL_END}", re.IGNORECASE)
LABEL_ONLY_RE = re.compile(rf"^{CAPTION_KIND}\s*{LABEL_NUMBER}\s*[.:]?$", re.IGNORECASE)
# What separates a caption's label from its text. A space does not: 'Table 1 briefly describes ...' is a sentence.
CAPTION_SEPARATOR_RE = re.compile(r"\s*[.:]")
# Theorem-like statements open with a punctuated label: 'Theorem 2 (Title).', 'Remark 1.'
STATEMENT_KINDS = ("theorem", "lemma", "proposition", "corollary", "definition", "remark", "assumption", "conjecture")
STATEMENT_RE = re.compile(
    rf"^(?P<kind>{'|'.join(STATEMENT_KINDS)})\s+(?P<number>{LABEL_NUMBER})\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*[.:]",
    re.IGNORECASE,
)
PROOF_RE = re.compile(r"^proof\b", re.IGNORECASE)

REFERENCE_START_RE = re.compile(r"^\[\d{1,4}\]")
SENTENCE_END_RE = re.compile(r"[.:?!]\s*$")
EQUATION_TAG_RE = re.compile(r"^\(\d{1,3}\)$")  # a line holding only an equation number ends a display
ROMAN_DIGITS = {"I": 1, "V": 5, "X": 10}

Style = tuple[str, bool, bool]


@dataclass
class Paragraph:
    id: int
    page: int
    last_page: int
    kind: str
    section: int
    first_line: int
    last_line: int
    text: str


@dataclass
class Section:
    id: int
    parent: int
    level: int
    number: str
    title: str
    page: int
    paragraph: int


@dataclass(frozen=True)
class _Reading:
    """One interpretation of a section number: 'I.' is roman one or the ninth letter."""

    level: int
    number: str
    scheme: tuple[str, str]  # (kind, parent prefix for decimals)
    value: int


@dataclass
class _Candidate:
    lines: list[Line]  # in reading order: optional number line, then the title lines
    text: str
    title: str
    style: Style
    readings: list[_Reading] = field(default_factory=list)
    reading: _Reading | None = None
    fills: bool = False  # reaches the column's usual right edge like justified prose
    plain: bool = False  # set in body typography: only a numbered sub-level ('3.3.1') may be such a heading
    page: int = 0
    first_index: int = 0  # positions of its first and last line in the page's body reading order
    last_index: int = 0


@dataclass
class _Sequence:
    """A run of consecutive section numbers in one scheme and typeface."""

    scheme: tuple[str, str]
    style: tuple[str, bool] | None
    first: int
    last: int
    members: list[_Candidate] = field(default_factory=list)


def _family(font: str) -> str:
    """Font family without subset tag and style suffix ('ABCDEF+TimesLTStd-Italic' -> 'timesltstd')."""
    return re.split(r"[-,]", re.sub(r"^[A-Z]{6}\+", "", font), maxsplit=1)[0].lower()


def _style(line: Line) -> Style:
    return (_family(line.font), line.bold, line.italic)


def _roman(value: str) -> int:
    total = 0
    for index, digit in enumerate(value):
        amount = ROMAN_DIGITS[digit]
        following = ROMAN_DIGITS[value[index + 1]] if index + 1 < len(value) else 0
        total += -amount if following > amount else amount
    return total


def _centre(line: Line) -> float:
    return (line.y0 + line.y1) / 2


def _crowded(lines: list[Line], tolerance: float, spacing: float) -> set[int]:
    """Ids of lines sharing their row with another line of the same column across a space wider than `spacing`
    (table cells, equation parts); the pieces of one text line split at a font change (inline math) abut."""
    crowded: set[int] = set()
    by_column: dict[int, list[Line]] = {}
    for line in lines:
        by_column.setdefault(line.column, []).append(line)
    for column_lines in by_column.values():
        ordered = sorted(column_lines, key=_centre)
        for index, line in enumerate(ordered):
            low, high = index, index + 1
            while low > 0 and _centre(line) - _centre(ordered[low - 1]) <= tolerance:
                low -= 1
            while high < len(ordered) and _centre(ordered[high]) - _centre(line) <= tolerance:
                high += 1
            pieces = sorted(ordered[low:high], key=lambda piece: piece.x0)
            if any(b.x0 - a.x1 > spacing for a, b in zip(pieces, pieces[1:], strict=False)):
                crowded.add(line.id)
    return crowded


def is_caption(line: Line, metrics: Metrics, opens_paragraph: bool = False,
               in_caption: bool = False, in_table: bool = False) -> bool:
    """A caption is a figure/table/algorithm label, a separator and the caption text: 'Fig. 1. Evolution ...',
    'Table 4: Benchmark datasets'. A label alone on its line is a caption too -- its text is the line below
    ('Table 1' / 'Summary of ...') -- when it opens a paragraph or breaks the caption it follows, since a caption
    holds no paragraph break. A space is not a separator, so 'Table 5 compares ...' is a sentence, not a caption.
    A label in type smaller than body text is a caption whatever follows it, unless it is a cell of a table."""
    match = CAPTION_RE.match(line.text)
    if not match:
        return False
    rest = line.text[match.end():]
    if not rest.strip():
        return opens_paragraph or in_caption
    if CAPTION_SEPARATOR_RE.match(rest):
        return True
    return metrics.smaller(line.size) and not in_table


def _readings(match: re.Match[str]) -> list[_Reading]:
    roman, letter, paren, decimal = (match.group(name) for name in ("roman", "letter", "paren", "decimal"))
    if roman:
        readings = [_Reading(1, roman, ("roman", ""), _roman(roman))]
        if len(roman) == 1:
            readings.append(_Reading(2, roman, ("letter", ""), ord(roman) - ord("A") + 1))
        return readings
    if letter:
        return [_Reading(2, letter, ("letter", ""), ord(letter) - ord("A") + 1)]
    if paren:
        return [_Reading(3, paren, ("paren", ""), int(paren))]
    parts = decimal.split(".")
    return [_Reading(len(parts), decimal, ("decimal", ".".join(parts[:-1])), int(parts[-1]))]


def _candidate(
    number: Line | None, titles: list[Line], metrics: Metrics, column_edge: float, after_references: bool
) -> _Candidate | None:
    """A heading candidate (numbering not yet validated), else None."""
    title_text = ""
    for line in titles:
        title_text = _join(title_text, line.text.strip()) if title_text else line.text.strip()
    text = f"{number.text.strip()} {title_text}" if number is not None else title_text
    first, last = titles[0], titles[-1]
    if last.text.rstrip().endswith("-") or CAPTION_RE.match(text) or MATH_CHAR_RE.search(text):
        return None
    if MATH_FONT_RE.match(first.font) and _family(first.font) != _family(metrics.body_font):
        return None
    set_apart = (first.font != metrics.body_font or first.bold or not metrics.same_size(first.size, metrics.body_size)
                 or title_text.isupper())
    fills = last.x1 >= column_edge - INDENT * metrics.body_size
    numbered = NUMBERED_RE.match(text)
    sub_level = numbered is not None and "." in (numbered.group("decimal") or "")
    if not set_apart and (after_references or not sub_level or fills):
        return None
    lines = [number, *titles] if number is not None else list(titles)
    if after_references:
        if AFTER_REFERENCES_RE.match(text) and not fills:
            return _Candidate(lines, text, text.rstrip(".:").strip(), _style(first))
        return None
    match = NUMBERED_RE.match(text)
    if match:
        title = match.group("title").rstrip(".").strip()
        if not re.search(r"[A-Za-z]{3}", title) or ("," in title and re.search(r"\d", title)):
            return None
        number_size = number.size if number is not None else first.lead_size
        if number_size and number_size < first.size and not metrics.same_size(number_size, first.size):
            return None  # a superscript mark, e.g. "¹ Lincoln University College"
        return _Candidate(lines, text, title, _style(first), _readings(match), fills=fills, plain=not set_apart)
    if UNNUMBERED_RE.match(text) and not fills and set_apart:
        return _Candidate(lines, text, text.rstrip(".:").strip(), _style(first))
    return None


def _column_lefts(page: Page, metrics: Metrics) -> dict[int, float]:
    counters: dict[int, Counter[int]] = {}
    for line in page.body_lines():
        if metrics.same_size(line.size, metrics.body_size):
            counters.setdefault(line.column, Counter())[round(line.x0)] += 1
    return {column: float(counter.most_common(1)[0][0]) for column, counter in counters.items()}


def _column_starts(pages: list[Page], metrics: Metrics) -> dict[int, float]:
    """Usual left edge of each column across the document (the most common left end of body lines)."""
    counters: dict[int, Counter[int]] = {}
    for page in pages:
        for line in page.body_lines():
            if _prose(line, metrics):
                counters.setdefault(line.column, Counter())[round(line.x0)] += 1
    return {column: float(counter.most_common(1)[0][0]) for column, counter in counters.items()}


def _prose(line: Line, metrics: Metrics) -> bool:
    """A body-size line with letters (digit-only lines are line numbers, counters or table figures)."""
    return metrics.same_size(line.size, metrics.body_size) and any(char.isalpha() for char in line.text)


def _page_edges(page: Page, metrics: Metrics) -> dict[int, float]:
    """Right end of the longest body-size line of each column on the page: full prose lines reach it,
    whether the text is justified or ragged."""
    edges: dict[int, float] = {}
    for line in page.body_lines():
        if _prose(line, metrics):
            edges[line.column] = max(edges.get(line.column, 0.0), line.x1)
    return edges


def _restarts_numbering(text: str) -> bool:
    match = NUMBERED_RE.match(text)
    return match is not None and any(r.level == 1 and r.value == 1 for r in _readings(match))


def _page_cells(page: Page, metrics: Metrics, starts: dict[int, float]) -> set[int]:
    """Ids of the lines that are cells of a table on this page: a line sharing its row with lettered text of
    another cell within the column's text extent, plus the lines continuing such a cell below it (the last lines
    of a deep cell stand alone on their row and would otherwise read as running text, a heading or an abstract).
    Digit-only neighbours are line or equation numbers, not cells."""
    body = page.body_lines()
    row = SAME_ROW * metrics.line_height
    margin = INDENT * metrics.body_size
    gap_limit = metrics.line_gap + PARAGRAPH_SPACE * metrics.body_size
    edges = _page_edges(page, metrics)
    crowded = _crowded(body, row, metrics.body_size)
    cells: set[int] = set()
    by_column: dict[int, list[Line]] = {}
    for line in body:
        by_column.setdefault(line.column, []).append(line)
        if line.id not in crowded:
            continue
        low, high = starts.get(line.column, 0.0) - margin, edges.get(line.column, page.width) + margin
        if any(low <= mate.x0 and mate.x1 <= high and re.search(r"[^\W\d_]", mate.text)
               for mate in _row_mates(line, body, row)):
            cells.add(line.id)
    for column_lines in by_column.values():
        ordered = sorted(column_lines, key=lambda line: (line.y0, line.x0))
        for index, line in enumerate(ordered):
            if line.id in cells:
                continue
            for above in reversed(ordered[:index]):
                if above.y1 < line.y0 - gap_limit:
                    break
                if (above.id in cells and metrics.same_size(above.size, line.size)
                        and line.x0 < above.x1 and above.x0 < line.x1 and above.y0 < line.y0):
                    cells.add(line.id)
                    break
    return cells


def table_content(pages: list[Page], metrics: Metrics) -> dict[int, set[int]]:
    """Ids of the table-cell lines of each page, by page number."""
    starts = _column_starts(pages, metrics)
    return {page.number: _page_cells(page, metrics, starts) for page in pages}


def _candidates(pages: list[Page], metrics: Metrics) -> list[_Candidate]:
    starts = _column_starts(pages, metrics)
    row = SAME_ROW * metrics.line_height
    gap_limit = metrics.line_gap + PARAGRAPH_SPACE * metrics.body_size
    candidates: list[_Candidate] = []
    after_references = False
    for page in pages:
        body = page.body_lines()
        if not any(line.font == metrics.body_font for line in body):
            continue  # cover sheet generated by the submission system
        cells = _page_cells(page, metrics, starts)
        edges = _page_edges(page, metrics)
        consumed: set[int] = set()
        for index, line in enumerate(body):
            text = line.text.strip()
            if line.id in consumed or not (
                SECTION_NUMBER_RE.match(text) or NUMBERED_RE.match(text) or UNNUMBERED_RE.match(text)
            ):
                continue
            edge = edges.get(line.column, page.width)
            options: list[tuple[Line | None, int]] = []
            following = body[index + 1] if index + 1 < len(body) else None
            if (SECTION_NUMBER_RE.match(text) and following is not None and following.column == line.column
                    and following.x0 >= line.x1 and abs(_centre(following) - _centre(line)) <= row):
                options.append((line, index + 1))
            options.append((None, index))
            for number, start in options:
                titles = [body[start]]
                while start + len(titles) < len(body):
                    # a title continues in the same typography: more text on its row, or a wrapped next line
                    last, after = titles[-1], body[start + len(titles)]
                    if not (after.column == last.column and _style(after) == _style(last)
                            and metrics.same_size(after.size, last.size)):
                        break
                    same_row = abs(_centre(after) - _centre(last)) <= row and after.x0 >= last.x1
                    wraps = last.text.rstrip().endswith("-") or last.x1 >= edge - INDENT * metrics.body_size
                    if not (same_row or (wraps and 0 <= after.y0 - last.y1 <= gap_limit)):
                        break
                    titles.append(after)
                group = [number, *titles] if number is not None else titles
                ids = {member.id for member in group}
                if any(member.id in cells for member in group):
                    continue  # a table cell, not a heading
                joined_text = " ".join(member.text.strip() for member in group)
                restart = after_references and _restarts_numbering(joined_text)
                candidate = _candidate(number, titles, metrics, edge, after_references and not restart)
                if candidate is None:
                    continue
                candidate.page, candidate.first_index = page.number, index
                candidate.last_index = start + len(titles) - 1
                consumed.update(ids)
                candidates.append(candidate)
                if restart:
                    after_references = False
                if not candidate.readings:
                    if REFERENCES_RE.match(candidate.title):
                        after_references = True
                    elif candidate.title.lower() == "abstract":
                        after_references = False
                break
    return candidates


def _row_mates(line: Line, body: list[Line], tolerance: float) -> list[Line]:
    return [other for other in body
            if other.id != line.id and other.column == line.column and abs(_centre(other) - _centre(line)) <= tolerance]


def _headings(candidates: list[_Candidate]) -> list[_Candidate]:
    """Unnumbered candidates plus the numbered ones that belong to the document's numbering sequences.

    Runs of consecutive numbers are formed per scheme and typeface. A run is accepted when it starts at the
    first number and continues, when it is at least ``numbering_run`` long (its first heading was missed),
    or when it resumes after an accepted run of the same scheme (a heading in between was missed). A lone
    first number is accepted when its title is a standard section title or it is set like an accepted run.
    Numbered lines of one scheme directly below one another in one typeface (list items, table rows) are dropped;
    a line as wide as justified prose is accepted only as the missing number between two accepted ones.
    """
    listed: set[int] = set()
    for before, after in zip(candidates, candidates[1:], strict=False):
        if (before.readings and after.readings and before.page == after.page
                and after.first_index == before.last_index + 1 and before.style == after.style
                and {r.scheme for r in before.readings} & {r.scheme for r in after.readings}):
            listed.update((id(before), id(after)))
    numbered = [c for c in candidates if c.readings and id(c) not in listed]

    def run_key(reading: _Reading, style: Style) -> tuple[tuple[str, str], tuple[str, bool] | None]:
        # numbers under a parent ('3.3.') belong together whatever their typeface; top-level schemes also need the
        # typeface, italic aside (one heading of a run may be set in italic)
        return (reading.scheme, None if reading.scheme[1] else style[:2])

    runs: list[_Sequence] = []
    latest: dict[tuple[tuple[str, str], tuple[str, bool] | None], _Sequence] = {}
    for candidate in numbered:
        if candidate.fills:
            continue
        reading, run = next(
            ((r, latest[run_key(r, candidate.style)]) for r in candidate.readings
             if run_key(r, candidate.style) in latest and latest[run_key(r, candidate.style)].last + 1 == r.value),
            (next((r for r in candidate.readings if r.value == 1), candidate.readings[0]), None),
        )
        if run is None:
            run = _Sequence(reading.scheme, run_key(reading, candidate.style)[1], reading.value, reading.value)
            runs.append(run)
            latest[run_key(reading, candidate.style)] = run
        run.last = reading.value
        run.members.append(candidate)
        candidate.reading = reading

    order = {id(c): position for position, c in enumerate(candidates)}
    accepted_runs: list[_Sequence] = []
    resumable: dict[tuple[tuple[str, str], tuple[str, bool] | None], _Sequence] = {}
    for run in runs:
        key = (run.scheme, run.style)
        before = resumable.get(key)
        if any(member.plain for member in run.members):
            valid = run.first == 1 and len(run.members) >= 2  # headings in body type need a full run of their own
        else:
            valid = ((run.first == 1 and len(run.members) >= 2) or len(run.members) >= NUMBERING_RUN
                     or (run.first > 1 and before is not None and run.first > before.last))
        if valid:
            accepted_runs.append(run)
            resumable[key] = run

    # the document's top-level numbering is its first accepted top-level run; a run of another top-level scheme
    # lying entirely inside it (numbered table rows or list items between sections IV and V) is not an outline
    top_level = [run for run in accepted_runs if run.members[0].reading and run.members[0].reading.level == 1]
    if top_level:
        main = min(top_level, key=lambda run: order[id(run.members[0])])
        low, high = order[id(main.members[0])], order[id(main.members[-1])]
        accepted_runs = [
            run for run in accepted_runs
            if run is main or run not in top_level or run.scheme[0] == main.scheme[0]
            or not all(low < order[id(member)] < high for member in run.members)
        ]
    accepted = {id(member) for run in accepted_runs for member in run.members}
    for run in runs:
        lone = run.members[0]
        if len(run.members) == 1 and run.first == 1 and not lone.plain and (
            UNNUMBERED_RE.match(lone.title)
            or any(a.scheme[0] == run.scheme[0] and a.members[0].style == lone.style for a in accepted_runs)
        ):  # a lone heading is set exactly like an accepted run, italic included
            accepted.add(id(lone))

    for candidate in numbered:
        if not candidate.fills:
            continue
        for reading in candidate.readings:
            members = sorted((m for a in accepted_runs
                              if a.scheme == reading.scheme and a.style == run_key(reading, candidate.style)[1]
                              for m in a.members), key=lambda m: order[id(m)])
            before = [m for m in members if order[id(m)] < order[id(candidate)]]
            after = [m for m in members if order[id(m)] > order[id(candidate)]]
            if (before and after and before[-1].reading is not None and after[0].reading is not None
                    and before[-1].reading.value + 1 == reading.value == after[0].reading.value - 1):
                candidate.reading = reading
                accepted.add(id(candidate))
                break
    return [c for c in candidates if not c.readings or id(c) in accepted]


def _join(text: str, addition: str) -> str:
    if text.endswith("-") and addition[:1].islower():
        return text[:-1] + addition
    return f"{text} {addition}"


def build_indexes(pages: list[Page], metrics: Metrics) -> tuple[list[Paragraph], list[Section]]:
    """Paragraphs in reading order and the section tree; lines must already carry their ids."""
    gap_limit = metrics.line_gap + PARAGRAPH_SPACE * metrics.body_size
    overlap_limit = -SAME_ROW * metrics.line_height
    column_starts = _column_starts(pages, metrics)
    headings: dict[int, _Candidate] = {}
    for candidate in _headings(_candidates(pages, metrics)):
        candidate.lines.sort(key=lambda line: line.id)
        headings[candidate.lines[0].id] = candidate
    title_lines = {line.id for c in headings.values() for line in c.lines[1:]}
    paragraphs: list[Paragraph] = []
    sections: list[Section] = []
    stack: list[Section] = []
    current: Paragraph | None = None
    previous: Line | None = None
    after_references = False

    for page in pages:
        lefts = _column_lefts(page, metrics)
        crowded = _crowded(page.body_lines(), SAME_ROW * metrics.line_height, metrics.body_size)
        cells = _page_cells(page, metrics, column_starts)
        for line in page.body_lines():
            if line.id in title_lines:
                continue
            heading = headings.get(line.id)
            if heading is not None:
                reading = heading.reading
                level, number = (reading.level, reading.number) if reading else (1, "")
                while stack and stack[-1].level >= level:
                    stack.pop()
                section_id = len(sections) + 1
                last = heading.lines[-1]
                paragraph = Paragraph(len(paragraphs) + 1, line.page, last.page, "heading", section_id,
                                      line.id, last.id, heading.text)
                section = Section(section_id, stack[-1].id if stack else 0, level, number, heading.title, line.page,
                                  paragraph.id)
                paragraphs.append(paragraph)
                sections.append(section)
                stack.append(section)
                if REFERENCES_RE.match(heading.title):
                    after_references = True
                elif heading.title.lower() == "abstract" or level == 1 and reading is not None and reading.value == 1:
                    after_references = False
                current, previous = None, last
                continue

            reference_start = after_references and REFERENCE_START_RE.match(line.text) is not None
            opens = (
                previous is None or line.page != previous.page or line.column != previous.column
                or SENTENCE_END_RE.search(previous.text) is not None or line.y0 - previous.y1 > gap_limit
                or EQUATION_TAG_RE.match(previous.text) is not None
            )
            # numbered items carry on past the References: floats set at the end, and appendices
            caption = is_caption(
                line, metrics, opens, current is not None and current.kind == "caption", line.id in cells)
            if current is not None and previous is not None and not reference_start:
                same_flow = line.page == previous.page and line.column == previous.column
                gap = line.y0 - previous.y1 if same_flow else 0.0
                same_type = metrics.same_size(line.size, previous.size) and _family(line.font) == _family(previous.font)
                # small capitals and inline math give the lines of a caption in small type different sizes and fonts
                small_caption = (
                    current.kind == "caption" and metrics.smaller(line.size) and metrics.smaller(previous.size)
                )
                if current.kind == "reference" and same_type:
                    joined = True  # hanging-indent continuation of a reference entry
                elif opens and (STATEMENT_RE.match(line.text) or PROOF_RE.match(line.text)):
                    joined = False  # 'Theorem 2 (Title).' or 'Proof.' after a finished sentence opens a paragraph
                elif caption:
                    joined = False  # a caption holds no paragraph break, so a label always starts a new one
                elif current.kind == "caption" and line.id in crowded:
                    joined = False  # table cells below a table caption
                elif current.kind == "caption" and (LABEL_ONLY_RE.match(current.text) or small_caption):
                    # a label printed on its own line ('TABLE V') takes the title below it, and a caption set in small
                    # type continues through its centred lines
                    joined = same_flow and gap <= gap_limit
                else:
                    indented = line.x0 >= lefts.get(line.column, line.x0) + INDENT * metrics.body_size
                    resized = not metrics.same_size(line.size, previous.size)
                    ended = SENTENCE_END_RE.search(previous.text) is not None
                    flow_break = not same_flow and (ended or current.kind == "caption")
                    joined = not (indented or resized or gap > gap_limit or gap < overlap_limit or flow_break)
                if joined:
                    current.text = _join(current.text, line.text)
                    current.last_line, current.last_page = line.id, line.page
                    previous = line
                    continue

            kind = "reference" if reference_start else ("caption" if caption else "text")
            current = Paragraph(len(paragraphs) + 1, line.page, line.page, kind,
                                stack[-1].id if stack else 0, line.id, line.id, line.text)
            paragraphs.append(current)
            previous = line
    return paragraphs, sections
