"""Reading over the paper store: whole pages with a continuation cursor, sections by heading, and search."""

from __future__ import annotations

import re
from typing import Any

from crepe_mcp.reader.common import ReaderError, page_span
from crepe_mcp.reader.store import DocumentStore

READ_BUDGET = 12_000  # characters per reply
REFERENCES_OMITTED = "[reference list entries omitted: list_assets(kind='reference') lists them]"
CURSOR_RE = re.compile(r"^p?(\d{1,5})(?:@(\d{1,7}))?$")
SECTION_NUMBER_RE = re.compile(r"^\s*(?:[IVX]+|[A-Z]|\d+(?:\.\d+)*)[.)]?(?:\s+(?=\S)|\s*$)", re.IGNORECASE)


def parse_cursor(cursor: str) -> tuple[int, int]:
    """(page, character offset) of a 'next' value such as '8' or 'p6@4000'."""
    match = CURSOR_RE.match(cursor.strip())
    if not match:
        raise ReaderError(
            f"Cursor {cursor[:40]!r} is not valid: pass the 'next' value of the previous reply unchanged."
        )
    return int(match.group(1)), int(match.group(2) or 0)


def read_pages(
    store: DocumentStore, first: int, last: int, offset: int = 0, scope: str = "", omission: str = ""
) -> tuple[str, list[int]]:
    """Whole pages first..last up to the budget, as plain text under a status line, and the pages returned whole. A
    page that does not fit is left for the next cursor; only a single page larger than the budget is split, at a line
    break, with a 'p<page>@<offset>' cursor. With an omission line the reference list entries are left out."""
    total = store.page_count
    for value in (first, last):
        if not 1 <= value <= total:
            where = f"{total} pages; {scope}" if scope else f"{total} pages"
            raise ReaderError(f"Page {value} is outside this PDF ({where}). Use pages between 1 and {total}.")
    if first > last:
        raise ReaderError(f"first_page {first} is after last_page {last}.")
    chunks: list[str] = []
    returned: list[int] = []
    used = 0
    read_last = first
    next_cursor: str | None = None
    for page in store.reading_pages(first, last, omission):
        number = page["page"]
        start = offset if number == first else 0
        text = page["text"][start:]
        if not page["text"]:
            text = "[no text layer on this page: scanned image]" if page["source"] == "none" else "[blank page]"
        header = f"=== Page {number}{' (continued)' if start else ''} ==="
        block = f"{header}\n{text}"
        if used + len(block) > READ_BUDGET:
            if chunks:
                next_cursor = str(number)
                break
            room = max(READ_BUDGET - len(header) - 1, 1)
            cut = text.rfind("\n", 0, room)
            cut = cut if cut > 0 else room
            chunks.append(f"{header}\n{text[:cut]}")
            read_last = number
            next_cursor = f"p{number}@{start + cut + (1 if text[cut:cut + 1] == chr(10) else 0)}"
            break
        chunks.append(block)
        returned.append(number)
        used += len(block) + 2
        read_last = number
    status = f"Pages {page_span(first, read_last)}{f' ({scope})' if scope else ''}. next: "
    status += f"{next_cursor}" if next_cursor else "none (range complete)"
    footer = f"\n\n[continue with read_pages cursor='{next_cursor}']" if next_cursor else ""
    return f"{status}\n\n" + "\n\n".join(chunks) + footer, returned


def _heading_key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", SECTION_NUMBER_RE.sub("", text.strip())).casefold().split())


def section_label(section: dict[str, Any]) -> str:
    return f"{section['number']} {section['title']}".strip()


def read_section(store: DocumentStore, heading: str, first: int, last: int) -> str:
    """One outline section of pages first..last, up to the next heading of the same or a higher level, as plain text
    under a status line."""
    sections = [s for s in store.outline() if first <= s["page"] <= last]
    wanted = _heading_key(heading)
    number = heading.strip().rstrip(".)").upper()
    match = (
        next((s for s in sections if wanted and _heading_key(s["title"]) == wanted), None)
        or next((s for s in sections if wanted and _heading_key(s["title"]).startswith(wanted)), None)
        or next((s for s in sections if not wanted and s["number"] and s["number"].upper() == number), None)
    )
    if match is None:
        labels = [section_label(s) for s in sections][:40]
        raise ReaderError(
            f"No heading matches {heading[:80]!r}. Headings: {labels}. "
            "Pass one of them, or use search_document."
        )
    parts: list[str] = []
    used = 0
    end_page = match["page"]
    next_page: int | None = None
    for paragraph in store.section_paragraphs(match["id"]):
        if paragraph["page"] > last:
            break
        if parts and used + len(paragraph["text"]) > READ_BUDGET:
            next_page = paragraph["page"]
            break
        parts.append(paragraph["text"][:READ_BUDGET])
        used += len(parts[-1]) + 2
        end_page = paragraph["last_page"]
    span = page_span(match["page"], end_page)
    status = f"Section {section_label(match)} (level {match['level']}, page{'s' if '-' in span else ''} {span}). "
    status += f"next: page {next_page}, continue with read_pages first_page={next_page}" if next_page else "next: none"
    return f"{status}\n\n" + "\n\n".join(parts)


def search(store: DocumentStore, query: str, first: int, last: int, max_hits: int) -> dict[str, Any]:
    """Paragraph hits in document order with page, section and snippet."""
    total, hits = store.search(query, max_hits, first, last)
    reply: dict[str, Any] = {
        "query": query,
        "pages": page_span(first, last),
        "total_hits": total,
        "hits": [{"page": h["page"], "section": h["section_title"], "snippet": h["snippet"]} for h in hits],
    }
    if total == 0:
        reply["hint"] = "No match. Words match from their start ('Fig' finds 'Figure'); try a shorter or other term."
    elif total > len(hits):
        reply["hint"] = f"Showing the first {len(hits)} of {total} hits; refine the query or raise max_hits."
    return reply
