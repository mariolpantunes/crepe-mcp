"""Per-paper SQLite store: extraction results are persisted once and queried per request.

The store lives in `<scratch>/store/<sha256[:16]>/paper.sqlite`, keyed by the PDF content, so a
replaced PDF with the same name gets a fresh store and two different PDFs never share one. It is
rebuilt when EXTRACTOR_VERSION or the PyMuPDF version changes. Builds write to a temporary file
that is renamed into place, so an interrupted build never leaves a half-filled store. Queries
fetch only what a request needs; nothing large is kept in memory between calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf as fitz

from crepe_mcp.reader.assets import build_assets
from crepe_mcp.reader.config import scratch_base
from crepe_mcp.reader.document import extract, measure
from crepe_mcp.reader.heuristics import DIGEST as HEURISTICS_DIGEST
from crepe_mcp.reader.indexes import build_indexes
from crepe_mcp.reader.structure import build_structure

EXTRACTOR_VERSION = "32"
MAX_OPEN_STORES = 8

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE pages (
    page INTEGER PRIMARY KEY,
    width REAL NOT NULL,
    height REAL NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    images INTEGER NOT NULL,
    drawings INTEGER NOT NULL,
    markup_annots INTEGER NOT NULL,
    chars INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE lines (
    id INTEGER PRIMARY KEY,
    page INTEGER NOT NULL REFERENCES pages (page),
    seq INTEGER NOT NULL,
    region TEXT NOT NULL,
    col INTEGER NOT NULL,
    x0 REAL NOT NULL,
    y0 REAL NOT NULL,
    x1 REAL NOT NULL,
    y1 REAL NOT NULL,
    text TEXT NOT NULL,
    font TEXT NOT NULL,
    size REAL NOT NULL,
    bold INTEGER NOT NULL,
    italic INTEGER NOT NULL,
    mono INTEGER NOT NULL,
    color INTEGER NOT NULL
);
CREATE INDEX lines_by_page ON lines (page, seq);
CREATE INDEX lines_by_region ON lines (region, page);
CREATE TABLE paragraphs (
    id INTEGER PRIMARY KEY,
    page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    kind TEXT NOT NULL,
    section INTEGER NOT NULL,
    first_line INTEGER NOT NULL,
    last_line INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX paragraphs_by_page ON paragraphs (page, id);
CREATE TABLE sections (
    id INTEGER PRIMARY KEY,
    parent INTEGER NOT NULL,
    level INTEGER NOT NULL,
    number TEXT NOT NULL,
    title TEXT NOT NULL,
    page INTEGER NOT NULL,
    paragraph INTEGER NOT NULL
);
CREATE VIRTUAL TABLE paragraph_search USING fts5 (
    text, content = 'paragraphs', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE segments (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    first_page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    label TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE TABLE structure (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE assets (
    segment INTEGER NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    number TEXT NOT NULL,
    label TEXT NOT NULL,
    page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    x0 REAL,
    y0 REAL,
    x1 REAL,
    y1 REAL,
    caption TEXT NOT NULL,
    content TEXT NOT NULL,
    content_format TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence TEXT NOT NULL,
    paragraph INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (segment, id)
);
CREATE INDEX assets_by_kind ON assets (kind, seq);
CREATE TABLE mentions (
    id INTEGER PRIMARY KEY,
    segment INTEGER NOT NULL,
    asset TEXT NOT NULL,
    paragraph INTEGER NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    strength TEXT NOT NULL
);
CREATE INDEX mentions_by_asset ON mentions (segment, asset, paragraph);
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_FINGERPRINTS: dict[tuple[str, int, int], str] = {}
_OPEN: OrderedDict[str, DocumentStore] = OrderedDict()
_LOCK = threading.RLock()


def fingerprint(pdf_path: Path) -> str:
    """SHA-256 of the PDF bytes, computed once per (path, size, mtime)."""
    st = pdf_path.stat()
    key = (str(pdf_path), st.st_size, st.st_mtime_ns)
    if key not in _FINGERPRINTS:
        digest = hashlib.sha256()
        with open(pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        _FINGERPRINTS[key] = digest.hexdigest()
    return _FINGERPRINTS[key]


def _expected_meta(pdf: Path) -> dict[str, str]:
    return {
        "fingerprint": fingerprint(pdf),
        "extractor_version": EXTRACTOR_VERSION,
        "pymupdf_version": str(fitz.VersionBind),
        "heuristics": HEURISTICS_DIGEST,
    }


def _build(pdf: Path, db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(db.parent, 0o700)
    tmp = db.with_name(f".{db.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.unlink(missing_ok=True)
    pages = extract(pdf)
    next_id = 1
    for page in pages:
        for line in sorted(page.lines, key=lambda line: line.seq):
            line.id = next_id
            next_id += 1
    metrics = measure(pages)
    paragraphs, sections = build_indexes(pages, metrics)
    structure = build_structure(pdf, pages, sections, metrics)
    segment_of_page = {
        page_no: segment.id
        for segment in structure.segments
        for page_no in range(segment.first_page, segment.last_page + 1)
    }
    assets, mentions = build_assets(pdf, pages, paragraphs, sections, segment_of_page, metrics)
    structure_values = {
        "current_segment": structure.current,
        "current_confidence": structure.current_confidence,
        "current_evidence": structure.current_evidence,
        "round": structure.round,
        "round_label": structure.round_label,
        "round_confidence": structure.round_confidence,
        "round_evidence": structure.round_evidence,
    }
    meta = dict(
        _expected_meta(pdf),
        file_name=pdf.name,
        pages=str(len(pages)),
        body_font=metrics.body_font,
        body_size=str(metrics.body_size),
        line_height=str(metrics.line_height),
        line_gap=str(metrics.line_gap),
    )
    con = sqlite3.connect(str(tmp))
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(meta.items()))
        con.executemany(
            "INSERT INTO pages (page, width, height, label, source, images, drawings, markup_annots, chars, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (p.number, p.width, p.height, p.label, p.source, p.images, p.drawings, p.markup_annots, len(p.text),
                 p.text)
                for p in pages
            ],
        )
        con.executemany(
            "INSERT INTO lines (id, page, seq, region, col, x0, y0, x1, y1, text, font, size, bold, italic, mono, "
            "color) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (line.id, line.page, line.seq, line.region, line.column, line.x0, line.y0, line.x1, line.y1,
                 line.text, line.font, line.size, int(line.bold), int(line.italic), int(line.mono), line.color)
                for page in pages
                for line in sorted(page.lines, key=lambda line: line.seq)
            ],
        )
        con.executemany(
            "INSERT INTO paragraphs (id, page, last_page, kind, section, first_line, last_line, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(p.id, p.page, p.last_page, p.kind, p.section, p.first_line, p.last_line, p.text) for p in paragraphs],
        )
        con.executemany(
            "INSERT INTO sections (id, parent, level, number, title, page, paragraph) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(s.id, s.parent, s.level, s.number, s.title, s.page, s.paragraph) for s in sections],
        )
        con.executemany(
            "INSERT INTO segments (id, kind, first_page, last_page, label, evidence) VALUES (?, ?, ?, ?, ?, ?)",
            [(s.id, s.kind, s.first_page, s.last_page, s.label, json.dumps(s.evidence)) for s in structure.segments],
        )
        con.executemany(
            "INSERT INTO structure (key, value) VALUES (?, ?)",
            sorted((key, json.dumps(value)) for key, value in structure_values.items()),
        )
        con.executemany(
            "INSERT INTO assets (segment, id, kind, number, label, page, last_page, x0, y0, x1, y1, caption, "
            "content, content_format, method, confidence, paragraph, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (a.segment, a.id, a.kind, a.number, a.label, a.page, a.last_page or a.page,
                 *(a.bbox or (None, None, None, None)), a.caption,
                 a.content, a.content_format, a.method, a.confidence, a.paragraph, a.seq)
                for a in assets
            ],
        )
        con.executemany(
            "INSERT INTO mentions (segment, asset, paragraph, page, text, strength) VALUES (?, ?, ?, ?, ?, ?)",
            [(m.segment, m.asset, m.paragraph, m.page, m.text, m.strength) for m in mentions],
        )
        con.execute("INSERT INTO paragraph_search (paragraph_search) VALUES ('rebuild')")
        con.commit()
    finally:
        con.close()
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    os.replace(tmp, db)


class DocumentStore:
    """Read access to one paper's extraction, plus a small key/value state table."""

    def __init__(self, path: Path, expected: dict[str, str]) -> None:
        self.path = path
        self.expected = expected
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    @classmethod
    def open(cls, pdf_path: str | Path) -> DocumentStore:
        """Open the store for a PDF, building or rebuilding it when missing or stale."""
        pdf = Path(pdf_path).resolve()
        if not pdf.is_file():
            raise FileNotFoundError(f"PDF not found: {pdf.name}")
        with _LOCK:
            expected = _expected_meta(pdf)
            db = scratch_base() / "store" / expected["fingerprint"][:16] / "paper.sqlite"
            key = str(db)
            cached = _OPEN.pop(key, None)
            if cached is not None:
                if cached.meta_matches(expected):
                    _OPEN[key] = cached
                    return cached
                cached.close()
            if not (db.is_file() and cls._file_matches(db, expected)):
                _build(pdf, db)
            store = cls(db, expected)
            _OPEN[key] = store
            while len(_OPEN) > MAX_OPEN_STORES:
                _, oldest = _OPEN.popitem(last=False)
                oldest.close()
            return store

    @staticmethod
    def _file_matches(db: Path, expected: dict[str, str]) -> bool:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                rows = dict(con.execute("SELECT key, value FROM meta").fetchall())
            finally:
                con.close()
        except sqlite3.Error:
            return False
        return all(rows.get(k) == v for k, v in expected.items())

    def meta_matches(self, expected: dict[str, str]) -> bool:
        return self.expected == expected and all(self.meta().get(k) == v for k, v in expected.items())

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with _LOCK:
            return self.con.execute(sql, params).fetchall()

    def meta(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._query("SELECT key, value FROM meta")}

    @property
    def page_count(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM pages")[0]["n"])

    def pages(self, first: int = 1, last: int | None = None) -> list[dict[str, Any]]:
        last = self.page_count if last is None else last
        rows = self._query("SELECT * FROM pages WHERE page BETWEEN ? AND ? ORDER BY page", (first, last))
        return [dict(row) for row in rows]

    def reading_pages(self, first: int, last: int, omission: str = "") -> list[dict[str, Any]]:
        """Pages first..last; with an omission line, the entries of the reference list are left out of the text and
        replaced by that line once per page."""
        pages = self.pages(first, last)
        sql = "SELECT first_line, last_line FROM paragraphs WHERE kind = 'reference' AND last_page >= ? AND page <= ?"
        spans = [(row["first_line"], row["last_line"]) for row in self._query(sql, (first, last))] if omission else []
        if not spans:
            return pages
        for page in pages:
            kept: list[str] = []
            for line in self.lines(page["page"], ("body",)):
                if not any(low <= line["id"] <= high for low, high in spans):
                    kept.append(line["text"])
                elif omission not in kept:
                    kept.append(omission)
            if omission in kept:
                page["text"] = "\n".join(kept)
        return pages

    def lines(self, page: int, regions: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        if regions:
            marks = ",".join("?" for _ in regions)
            sql = f"SELECT * FROM lines WHERE page = ? AND region IN ({marks}) ORDER BY seq"
            rows = self._query(sql, (page, *regions))
        else:
            rows = self._query("SELECT * FROM lines WHERE page = ? ORDER BY seq", (page,))
        return [dict(row) for row in rows]

    def outline(self) -> list[dict[str, Any]]:
        rows = self._query("SELECT id, parent, level, number, title, page FROM sections ORDER BY id")
        return [dict(row) for row in rows]

    def paragraphs(self, first: int = 1, last: int | None = None) -> list[dict[str, Any]]:
        last = self.page_count if last is None else last
        rows = self._query("SELECT * FROM paragraphs WHERE page BETWEEN ? AND ? ORDER BY id", (first, last))
        return [dict(row) for row in rows]

    def section_paragraphs(self, section_id: int) -> list[dict[str, Any]]:
        """Paragraphs from a section heading up to the next heading of the same or a higher level."""
        rows = self._query("SELECT level, paragraph FROM sections WHERE id = ?", (section_id,))
        if not rows:
            return []
        level, start = rows[0]["level"], rows[0]["paragraph"]
        following = self._query(
            "SELECT paragraph FROM sections WHERE id > ? AND level <= ? ORDER BY id LIMIT 1", (section_id, level)
        )
        if following:
            sql, params = "SELECT * FROM paragraphs WHERE id >= ? AND id < ? ORDER BY id", (start, following[0][0])
        else:
            sql, params = "SELECT * FROM paragraphs WHERE id >= ? ORDER BY id", (start,)
        return [dict(row) for row in self._query(sql, params)]

    def search(
        self, query: str, limit: int = 15, first: int = 1, last: int | None = None
    ) -> tuple[int, list[dict[str, Any]]]:
        """Case-insensitive search over paragraphs, in document order: (total hits, first hits).

        The query is matched as a phrase whose last word is a prefix, like grep on words: 'Fig' finds
        'Figure', 'Table I' finds 'Table I' and 'Table II'.
        """
        if not re.search(r"\w", query):
            return 0, []
        phrase = '"' + query.replace('"', '""') + '" *'
        last = self.page_count if last is None else last
        joined = (
            "FROM paragraph_search JOIN paragraphs p ON p.id = paragraph_search.rowid "
            "LEFT JOIN sections s ON s.id = p.section "
            "WHERE paragraph_search MATCH ? AND p.page BETWEEN ? AND ?"
        )
        total = self._query(f"SELECT COUNT(*) {joined}", (phrase, first, last))[0][0]
        rows = self._query(
            "SELECT p.id, p.page, p.kind, p.section, COALESCE(s.title, '') AS section_title, "
            f"snippet(paragraph_search, 0, '[', ']', '…', 16) AS snippet {joined} ORDER BY p.id LIMIT ?",
            (phrase, first, last, limit),
        )
        return int(total), [dict(row) for row in rows]

    def assets(self, kind: str | None = None, first: int = 1, last: int | None = None) -> list[dict[str, Any]]:
        """Assets on pages first..last in document order, with the number of paragraphs citing each ('cited')."""
        last = self.page_count if last is None else last
        cited = "(SELECT COUNT(*) FROM mentions m WHERE m.segment = a.segment AND m.asset = a.id) AS cited"
        where = "WHERE a.page BETWEEN ? AND ?" + (" AND a.kind = ?" if kind else "")
        params: tuple[Any, ...] = (first, last, kind) if kind else (first, last)
        return [dict(row) for row in self._query(f"SELECT a.*, {cited} FROM assets a {where} ORDER BY a.seq", params)]

    def asset(self, asset_id: str, first: int = 1, last: int | None = None) -> dict[str, Any] | None:
        """The first asset with this id on pages first..last, with its mentions (paragraph, page, text, strength)."""
        last = self.page_count if last is None else last
        rows = self._query(
            "SELECT * FROM assets WHERE id = ? AND page BETWEEN ? AND ? ORDER BY seq LIMIT 1", (asset_id, first, last)
        )
        if not rows:
            return None
        found = dict(rows[0])
        mentions = self._query(
            "SELECT paragraph, page, text, strength FROM mentions WHERE segment = ? AND asset = ? ORDER BY paragraph",
            (found["segment"], asset_id),
        )
        found["mentions"] = [dict(row) for row in mentions]
        return found

    def segments(self) -> list[dict[str, Any]]:
        """Parts of the submission (cover, manuscript, letter, responses, other) with their evidence."""
        rows = [dict(row) for row in self._query("SELECT * FROM segments ORDER BY id")]
        for row in rows:
            row["evidence"] = json.loads(row["evidence"])
        return rows

    def structure(self) -> dict[str, Any]:
        """Current manuscript choice and review round, each with confidence and evidence."""
        return {row["key"]: json.loads(row["value"]) for row in self._query("SELECT key, value FROM structure")}

    def manuscript_pages(self) -> dict[str, Any]:
        """Pages of the manuscript under review: a manual override, the detected copy, or the whole document."""
        override = self.get_state("manuscript_pages")
        if override:
            return dict(json.loads(override), source="override")
        current = self.structure().get("current_segment", 0)
        rows = self._query("SELECT first_page, last_page FROM segments WHERE id = ?", (current,))
        if rows:
            return {"first": rows[0][0], "last": rows[0][1], "source": "detected"}
        return {"first": 1, "last": self.page_count, "source": "whole-document"}

    def set_manuscript_pages(self, first: int, last: int, reason: str) -> None:
        """Record a manual manuscript page range (kept until the store is rebuilt)."""
        if not 1 <= first <= last <= self.page_count:
            raise ValueError(f"Pages {first}-{last} are not a valid range within 1-{self.page_count}")
        self.set_state("manuscript_pages", json.dumps({"first": first, "last": last, "reason": reason}))

    def get_state(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM state WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_state(self, key: str, value: str) -> None:
        with _LOCK:
            self.con.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))
            self.con.commit()

    def update_state(self, key: str, change: Callable[[str | None], str]) -> str:
        """Replace a state value by change(current value) in one locked step, so parallel tool calls lose no update."""
        with _LOCK:
            rows = self.con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchall()
            value = change(rows[0][0] if rows else None)
            self.con.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))
            self.con.commit()
            return value

    def close(self) -> None:
        with _LOCK:
            self.con.close()


def close_all() -> None:
    """Close every open store (tests and shutdown)."""
    with _LOCK:
        while _OPEN:
            _, store = _OPEN.popitem()
            store.close()
