"""Submission structure: cover pages, manuscript copies, letters, author responses, and the review round.

Deterministic page signals, calibrated on the verified layouts (PLAN.md §2.3), with no numeric thresholds:
- cover: submission-system fields in the page body (Manuscript Number, Submission ID/Version, Article
  Type, --Manuscript Draft--), read together with the value printed on the same row; pages until the next
  part continue the cover;
- item labels: the upload type Editorial Manager prints as the first line of each item (Letter,
  Highlights, Response to reviewers, Revised manuscript ...); content wins when it disagrees;
- manuscript start: an Abstract (heading or run-in) or Keywords/Index Terms line, or an Introduction
  heading with no such line on that page or the two before, on a page with text in the document's body
  font (submission-system cover sheets print "Keywords" fields in their own typeface);
- responses: a response heading (Response to the Reviewers, Point-by-point, Review Response Letter), or a
  reviewer/comment line together with an author reply line (Response:, Reply:, Answer:) or a
  "Reviewer n" line; numbered "Remark 2." or "Question 1." lines alone are theorem-like environments of a
  manuscript; letter: a salutation without them;
- trailing blank pages form their own part; other blank pages continue the current part.
The current manuscript is the only copy, else the copy labelled unmarked, else the copy with the fewest
mark-up annotations and then the least coloured text. The review round comes from response letters,
several copies, revised-manuscript labels and R<n> markers in the file name or cover fields; "Initial
Submission" in a cover field marks a first submission. Body prose is never used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from crepe_mcp.reader.document import Line, Page
from crepe_mcp.reader.heuristics import Metrics
from crepe_mcp.reader.indexes import Section, table_content

# "Corresponding author" is deliberately absent: IEEE manuscripts print it as a first-page footnote.
COVER_FIELD_RE = re.compile(
    r"^(?:manuscript\s+(?:number|id)|submission\s+(?:id|version|number)|article\s+type)\b"
    r"|--\s*manuscript\s+draft\s*--",
    re.IGNORECASE,
)
ITEM_LABEL_RE = re.compile(
    r"^(?:cover\s+letter|letter|highlights|responses?\s+to\s+(?:the\s+)?reviewers?(?:['’]\s*comments)?"
    r"|revised\s+manuscript.*|manuscript(?:\s+file)?|title\s+page|declaration\s+of.*|author\s+agreement"
    r"|credit\s+author\s+statement|supplementary\s+(?:material|files?)|figures?|tables?)$",
    re.IGNORECASE,
)
RESPONSE_HEADING_RE = re.compile(
    r"^(?:review\s+response\s+letter|response\s+letter|(?:responses?|reply|replies|answers?)\s+to\s+(?:the\s+)?"
    r"(?:reviewers?|referees?|editors?)|point[- ]by[- ]point)",
    re.IGNORECASE,
)
REVIEWER_RE = re.compile(r"^(?:response\s+to\s+)?(?:reviewer|referee)\s*#?\s*\d+", re.IGNORECASE)
COMMENT_LINE_RE = re.compile(r"^(?:comment|remark|point|question|concern)\s*#?\s*\d+\s*[.:)]", re.IGNORECASE)
REPLY_LINE_RE = re.compile(r"^(?:(?:authors?['’]?s?\s+)?(?:response|reply|answer))\s*[:.]", re.IGNORECASE)
LETTER_RE = re.compile(r"^dear\s+(?:editor|editors|editorial|prof|professor|dr|sir|madam|reviewers?)\b", re.IGNORECASE)
START_RE = re.compile(r"^(?:abstract|keywords|key\s+words|index\s+terms)\b", re.IGNORECASE)
INTRODUCTION_RE = re.compile(r"^introduction$", re.IGNORECASE)
ROUND_RE = re.compile(r"(?:[._\s-]|\d)R(\d)(?![A-Za-z0-9])", re.IGNORECASE)
INITIAL_RE = re.compile(r"\binitial\s+submission\b", re.IGNORECASE)
UNMARKED_RE = re.compile(r"\bunmarked\b|without\s+changes|\bclean\b", re.IGNORECASE)


@dataclass
class Segment:
    id: int
    kind: str
    first_page: int
    last_page: int
    label: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class Structure:
    segments: list[Segment]
    current: int
    current_confidence: str
    current_evidence: list[str]
    round: str
    round_label: str
    round_confidence: str
    round_evidence: list[str]


@dataclass
class _Signals:
    blank: bool
    label: str
    cover_lines: list[str]
    responses: str
    letter: str
    start: str
    coloured_chars: int
    markup_annots: int


def _coloured(line: Line) -> bool:
    red, green, blue = (line.color >> 16) & 255, (line.color >> 8) & 255, line.color & 255
    return len({red, green, blue}) > 1


def _cover_lines(page: Page) -> list[str]:
    """Cover fields with their values; forms print a label and its value side by side on one row."""
    body = page.body_lines()
    fields: list[str] = []
    for line in body:
        text = line.text.strip()
        match = COVER_FIELD_RE.search(text)
        if not match:
            continue
        if match.start() == 0 and not text[match.end():].strip(" :\t-"):
            row = [other for other in body if other is not line and other.x0 >= line.x1
                   and line.y0 <= (other.y0 + other.y1) / 2 <= line.y1]
            text = " ".join([text, *(other.text.strip() for other in sorted(row, key=lambda other: other.x0))])
        fields.append(text)
    return fields


def _signals(page: Page, metrics: Metrics, cells: set[int]) -> _Signals:
    body = page.body_lines()
    texts = [line.text.strip() for line in body]
    label = texts[0] if texts and ITEM_LABEL_RE.match(texts[0]) else ""
    rest = texts[1:] if label else texts
    # a cell of a table is not a manuscript start: a wrapped cell can read 'abstract and' or 'keywords'
    rest_lines = [line for line in (body[1:] if label else body) if line.id not in cells]
    heading = next((t for t in rest if RESPONSE_HEADING_RE.match(t)), "")
    reviewer = next((t for t in rest if REVIEWER_RE.match(t)), "")
    comment = next((t for t in rest if COMMENT_LINE_RE.match(t)), "")
    reply = next((t for t in rest if REPLY_LINE_RE.match(t)), "")
    exchange = (reviewer or comment) if (reviewer and (comment or reply)) or (comment and reply) else ""
    typeset = any(line.font == metrics.body_font for line in body)
    return _Signals(
        blank=page.source == "blank",
        label=label,
        cover_lines=_cover_lines(page),
        responses=heading or exchange,
        letter=next((t for t in rest if LETTER_RE.match(t)), ""),
        start=next((line.text.strip() for line in rest_lines
                    if START_RE.match(line.text.strip())), "") if typeset else "",
        coloured_chars=sum(len(line.text) for line in page.body_lines() if _coloured(line)),
        markup_annots=page.markup_annots,
    )


def _label_kind(label: str) -> str:
    lowered = label.lower()
    if lowered.startswith(("revised manuscript", "manuscript")):
        return "manuscript"
    if lowered.startswith(("letter", "cover letter")):
        return "letter"
    if lowered.startswith("response"):
        return "responses"
    return "other"


def _quote(text: str) -> str:
    return f"'{text[:60]}'"


def build_structure(pdf_path: Path, pages: list[Page], sections: list[Section], metrics: Metrics) -> Structure:
    """Split the PDF into parts, choose the manuscript under review, and decide the review round."""
    cells = table_content(pages, metrics)
    signals = {page.number: _signals(page, metrics, cells[page.number]) for page in pages}
    intro_pages = {s.page for s in sections if s.level == 1 and INTRODUCTION_RE.match(s.title)}
    last_content = max((page.number for page in pages if not signals[page.number].blank), default=0)
    segments: list[Segment] = []

    def start(kind: str, page_no: int, evidence: str, label: str = "") -> None:
        segments.append(Segment(len(segments) + 1, kind, page_no, page_no, label, [evidence] if evidence else []))

    for page in pages:
        number, found = page.number, signals[page.number]
        current = segments[-1] if segments else None
        if found.blank:
            trailing = number > last_content
            if current is not None and not (trailing and current.label != "blank"):
                current.last_page = number
            else:
                start("other", number, f"p{number}: blank page", "blank")
            continue

        near_start = any(signals[m].start for m in range(number - 2, number + 1) if m in signals)
        manuscript_signal = found.start or (_quote("Introduction") if number in intro_pages and not near_start else "")
        recent_manuscript = current is not None and current.kind == "manuscript" and number - current.first_page <= 2
        label_note = f" (item label {_quote(found.label)})" if found.label else ""

        if found.cover_lines and (current is None or current.kind == "cover"):
            if current is None:
                start("cover", number, f"p{number}: submission field {_quote(found.cover_lines[0])}")
            else:
                current.last_page = number
        elif manuscript_signal and not found.responses:
            if recent_manuscript and current is not None:
                current.last_page = number
            else:
                evidence = f"p{number}: {_quote(found.start or 'Introduction')}{label_note}"
                start("manuscript", number, evidence, found.label)
        elif found.responses:
            if current is not None and current.kind == "responses":
                current.last_page = number
            else:
                start("responses", number, f"p{number}: {_quote(found.responses)}{label_note}", found.label)
        elif found.letter:
            if current is not None and current.kind in ("letter", "responses"):
                current.last_page = number
            else:
                start("letter", number, f"p{number}: {_quote(found.letter)}{label_note}", found.label)
        elif found.label:
            start(_label_kind(found.label), number, f"p{number}: item label {_quote(found.label)}", found.label)
        elif current is not None:
            current.last_page = number
        else:
            start("other", number, "")

    current_id, current_confidence, current_evidence = _current_manuscript(segments, signals)
    round_status, round_label, round_confidence, round_evidence = _review_round(
        pdf_path, pages, segments, signals, current_id
    )
    return Structure(segments, current_id, current_confidence, current_evidence,
                     round_status, round_label, round_confidence, round_evidence)


def _current_manuscript(segments: list[Segment], signals: dict[int, _Signals]) -> tuple[int, str, list[str]]:
    manuscripts = [s for s in segments if s.kind == "manuscript"]
    if not manuscripts:
        return 0, "", ["no manuscript part detected"]
    if len(manuscripts) == 1:
        only = manuscripts[0]
        return only.id, "high", [f"only manuscript part: pages {only.first_page}-{only.last_page}"]
    unmarked = [s for s in manuscripts if UNMARKED_RE.search(s.label)]
    if len(unmarked) == 1:
        chosen = unmarked[0]
        return chosen.id, "high", [f"pages {chosen.first_page}-{chosen.last_page} labelled {_quote(chosen.label)}"]

    marks: dict[int, tuple[int, int]] = {}
    for segment in manuscripts:
        span = range(segment.first_page, segment.last_page + 1)
        marks[segment.id] = (sum(signals[n].markup_annots for n in span), sum(signals[n].coloured_chars for n in span))
    chosen = min(manuscripts, key=lambda s: (marks[s.id], s.first_page))
    clean = marks[chosen.id] == (0, 0)
    others_marked = all(marks[s.id] != (0, 0) for s in manuscripts if s is not chosen)
    evidence = [
        f"pages {s.first_page}-{s.last_page}: {marks[s.id][1]} coloured characters, "
        f"{marks[s.id][0]} mark-up annotations"
        for s in manuscripts
    ]
    return chosen.id, "high" if clean and others_marked else "low", evidence


def _review_round(
    pdf_path: Path, pages: list[Page], segments: list[Segment], signals: dict[int, _Signals], current_id: int
) -> tuple[str, str, str, list[str]]:
    revision: list[str] = []
    first: list[str] = []
    for segment in segments:
        if segment.kind == "responses":
            revision.append(f"author responses on pages {segment.first_page}-{segment.last_page}")
        if re.match(r"revised\s+manuscript", segment.label, re.IGNORECASE):
            revision.append(f"item label {_quote(segment.label)} on page {segment.first_page}")
    manuscripts = [s for s in segments if s.kind == "manuscript"]
    if len(manuscripts) >= 2:
        revision.append(f"{len(manuscripts)} manuscript copies")

    round_label = ""
    candidates = [("file name", Path(pdf_path).stem)]
    candidates += [(f"page {n} cover field", text) for n in sorted(signals) for text in signals[n].cover_lines]
    for source, text in candidates:
        match = ROUND_RE.search(text)
        if match:
            round_label = f"R{match.group(1)}"
            revision.append(f"{source} {_quote(text)} marks round {round_label}")
            break
    for n in sorted(signals):
        first += [f"page {n} cover field {_quote(text)}" for text in signals[n].cover_lines if INITIAL_RE.search(text)]

    if revision:
        current = next((s for s in segments if s.id == current_id), None)
        if current is not None:
            size = current.last_page - current.first_page + 1
            if len(pages) > size:
                revision.append(f"the PDF has {len(pages)} pages for a {size}-page manuscript")
        return "revision", round_label, "high" if len(revision) >= 2 else "medium", revision
    if first:
        return "first", "", "high", first
    return "", "", "", ["no response letter, manuscript copies, revision labels or round markers found"]
