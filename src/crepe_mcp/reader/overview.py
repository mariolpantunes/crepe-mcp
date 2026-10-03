"""What an agent needs to plan its reading of a document: parts, outline, numbered items, in document page numbers."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from crepe_mcp.reader.common import ReaderError, page_span
from crepe_mcp.reader.heuristics import SAME_SIZE
from crepe_mcp.reader.store import DocumentStore

TITLE_BUDGET = 300
EVIDENCE_ITEMS = 4


def part_range(store: DocumentStore, part: str) -> tuple[int, int]:
    """First and last page of a part: 'all', 'manuscript' (the detected main copy), 'responses' or 'cover'."""
    if part == "all":
        return 1, store.page_count
    if part == "manuscript":
        pages = store.manuscript_pages()
        return int(pages["first"]), int(pages["last"])
    segments = [s for s in store.segments() if s["kind"] == part]
    if not segments:
        parts = ", ".join(f"{s['kind']} {page_span(s['first_page'], s['last_page'])}" for s in store.segments())
        raise ReaderError(f"This document has no {part} part (parts: {parts or 'none detected'}). Use part='all'.")
    return min(s["first_page"] for s in segments), max(s["last_page"] for s in segments)


def title(store: DocumentStore) -> str:
    """Document title: its metadata, else the first heading (formats with a text flow), else the first run of lines
    in the largest type of the first page (PDF)."""
    meta = store.meta()
    if meta.get("doc_title"):
        return meta["doc_title"][:TITLE_BUDGET]
    if meta.get("format"):
        headings = store.outline()
        return headings[0]["title"][:TITLE_BUDGET] if headings else ""
    first, _ = part_range(store, "manuscript")
    body_size = float(meta.get("body_size") or 0.0)
    lines = [line for line in store.lines(first, ("body",)) if re.search(r"[^\W\d_]{2}", line["text"])]
    if not lines:
        return ""
    tolerance = SAME_SIZE * body_size
    largest = max(line["size"] for line in lines)
    if largest - body_size <= tolerance:
        return ""  # nothing is set larger than the text
    selected: list[str] = []
    for line in lines:
        if largest - line["size"] <= tolerance:
            selected.append(line["text"].strip())
        elif selected:
            break
    return " ".join(selected)[:TITLE_BUDGET]


def overview(store: DocumentStore, path: Path) -> dict[str, Any]:
    meta = store.meta()
    segments = store.segments()
    reply: dict[str, Any] = {
        "file": path.name,
        "format": meta.get("format") or "pdf",
        "pages": store.page_count,
        "page_unit": "logical page (a reading chunk, not a printed page)" if meta.get("format") else "PDF page",
        "title": title(store),
    }
    for key in ("author", "date"):
        if meta.get(f"doc_{key}"):
            reply[key] = meta[f"doc_{key}"]
    if len(segments) > 1 or any(s["kind"] != "manuscript" for s in segments):
        first, last = part_range(store, "manuscript")
        chosen = store.manuscript_pages()
        reply["parts"] = [
            {"kind": s["kind"], "pages": page_span(s["first_page"], s["last_page"]), **({"label": s["label"]}
             if s["label"] else {}), "evidence": s["evidence"][:EVIDENCE_ITEMS]}
            for s in segments
        ]
        reply["manuscript"] = {"pages": page_span(first, last), "source": chosen["source"]}
    reply["outline"] = [
        f"{'  ' * (s['level'] - 1)}{s['number'] + ' ' if s['number'] else ''}{s['title']} (p{s['page']})"
        for s in store.outline()
    ]
    assets = store.assets()
    reply["numbered_items"] = dict(sorted(Counter(a["kind"] for a in assets).items()))
    reply["uncited_items"] = [a["id"] for a in assets if a["cited"] == 0 and a["kind"] != "reference"]
    unreadable = [p["page"] for p in store.pages() if p["source"] == "none"]
    if unreadable:
        reply["warnings"] = [f"pages {unreadable} have no text layer (e.g. scanned images); their text cannot be read"]
    return reply
