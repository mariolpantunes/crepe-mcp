"""Documents other than PDF (DOCX, ODT, EPUB, HTML, LaTeX, Markdown, PPTX, XLSX ...) read through pandoc's AST.

Pandoc converts the file to its JSON AST; this module turns the blocks into the same tables the PDF extractor
fills (pages, paragraphs, sections, numbered items, mentions), so every reader tool works on any format.
Formats without pages are cut into logical pages of about PAGE_CHARS characters at block boundaries; a page is
a reading chunk, not a printed page. Deterministic, no LLM involved.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from crepe_mcp.reader.common import ReaderError
from crepe_mcp.reader.schema import SCHEMA

PAGE_CHARS = 3500  # target size of a logical page
PANDOC_TIMEOUT = 120
NUMBERED_TITLE_RE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(\S.*)$")
CAPTION_LABEL_RE = re.compile(r"^(table|figure|fig\.?)\s*(\d+)\s*[.:)\-–—]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
MENTION_RES = {
    "figure": re.compile(r"\b(?:figure|fig\.)\s*(\d+)", re.IGNORECASE),
    "table": re.compile(r"\btables?\s*(\d+)", re.IGNORECASE),
    "equation": re.compile(r"\b(?:equation|eq\.)\s*\(?(\d+)", re.IGNORECASE),
    "listing": re.compile(r"\blisting\s*(\d+)", re.IGNORECASE),
}
LABELS = {"figure": "Figure", "table": "Table", "equation": "Equation", "listing": "Listing"}


@cache
def pandoc_version() -> str:
    try:
        out = subprocess.run(["pandoc", "--version"], capture_output=True, text=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ReaderError("pandoc is not installed or not on PATH; it is needed to read this file type.") from error
    return out.splitlines()[0].strip() if out else "unknown"


def parse(path: Path, fmt: str) -> dict[str, Any]:
    """The pandoc JSON AST of a file."""
    pandoc_version()
    cmd = ["pandoc", "-f", fmt, "-t", "json", "--wrap=none", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PANDOC_TIMEOUT)
    except subprocess.TimeoutExpired as error:
        raise ReaderError(f"pandoc needed more than {PANDOC_TIMEOUT}s to read {path.name!r}.") from error
    if result.returncode != 0:
        raise ReaderError(f"pandoc could not read {path.name!r} as {fmt}: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout)


# ---- inlines -------------------------------------------------------------------------------------------------------


def text_of(inlines: list[dict[str, Any]]) -> str:
    """Plain text of a list of inlines; math keeps its TeX between dollars, notes are left out."""
    out: list[str] = []
    for node in inlines:
        kind: str = node["t"]
        content: Any = node.get("c")
        if kind == "Str":
            out.append(content)
        elif kind in ("Space", "SoftBreak"):
            out.append(" ")
        elif kind == "LineBreak":
            out.append("\n")
        elif kind in ("Emph", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps", "Underline"):
            out.append(text_of(content))
        elif kind == "Quoted":
            quote = "'" if content[0]["t"] == "SingleQuote" else '"'
            out.append(f"{quote}{text_of(content[1])}{quote}")
        elif kind == "Cite":
            out.append(text_of(content[1]))
        elif kind == "Code":
            out.append(content[1])
        elif kind == "Math":
            tex = content[1]
            out.append(f"$${tex}$$" if content[0]["t"] == "DisplayMath" else f"${tex}$")
        elif kind in ("Link", "Span"):
            out.append(text_of(content[1]))
        elif kind == "Image":
            out.append(text_of(content[1]))
    return "".join(out)


def blocks_text(blocks: list[dict[str, Any]]) -> str:
    """Plain text of blocks, one line per paragraph, used for table cells and captions."""
    parts: list[str] = []
    for block in blocks:
        kind: str = block["t"]
        content: Any = block.get("c")
        if kind in ("Para", "Plain"):
            parts.append(text_of(content))
        elif kind == "CodeBlock":
            parts.append(content[1])
        elif kind in ("BlockQuote", "Div"):
            parts.append(blocks_text(content if kind == "BlockQuote" else content[1]))
        elif kind in ("BulletList", "OrderedList"):
            items = content if kind == "BulletList" else content[1]
            parts.extend(blocks_text(item) for item in items)
        elif kind == "Header":
            parts.append(text_of(content[2]))
    return " ".join(part for part in (p.strip() for p in parts) if part)


def _meta_text(value: dict[str, Any]) -> str:
    kind: str = value["t"]
    content: Any = value.get("c")
    if kind == "MetaString":
        return str(content)
    if kind == "MetaInlines":
        return text_of(content)
    if kind == "MetaBlocks":
        return blocks_text(content)
    if kind == "MetaList":
        return ", ".join(part for part in (_meta_text(item) for item in content) if part)
    return ""


def metadata(ast: dict[str, Any]) -> dict[str, str]:
    found = {key: _meta_text(value) for key, value in ast.get("meta", {}).items() if key in ("title", "author", "date")}
    return {key: value.strip() for key, value in found.items() if value.strip()}


# ---- tables --------------------------------------------------------------------------------------------------------


def _cell(cell: list[Any]) -> str:
    return blocks_text(cell[4]).replace("|", "\\|").replace("\n", " ")


def _rows(section: list[Any]) -> list[list[str]]:
    return [[_cell(cell) for cell in row[1]] for row in section]


def table_markdown(table: list[Any]) -> str:
    """A pandoc Table as a GitHub pipe table; a table without a header row gets an empty one."""
    head = _rows(table[3][1])
    body = [row for tbody in table[4] for row in _rows(tbody[2] + tbody[3])] + _rows(table[5][1])
    width = max((len(row) for row in [*head, *body]), default=0)
    if width == 0:
        return ""
    pad = [[*row, *[""] * (width - len(row))] for row in (head[-1:] or [[""] * width])]
    header = pad[0]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join([*row, *[""] * (width - len(row))]) + " |" for row in body]
    return "\n".join(lines)


# ---- builder -------------------------------------------------------------------------------------------------------


@dataclass
class _Para:
    id: int
    kind: str
    section: int
    text: str
    level: int = 0  # headings only
    page: int = 1


@dataclass
class _Asset:
    id: str
    kind: str
    number: str
    caption: str
    content: str
    content_format: str
    paragraph: int
    page: int = 1


@dataclass
class _Walker:
    paragraphs: list[_Para] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    assets: list[_Asset] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    stack: list[tuple[int, int]] = field(default_factory=list)  # (level, section id)

    def add(self, kind: str, text: str, level: int = 0) -> _Para:
        section = self.stack[-1][1] if self.stack else 0
        if kind == "heading":
            section = len(self.sections) + 1
        para = _Para(len(self.paragraphs) + 1, kind, section, text, level)
        self.paragraphs.append(para)
        return para

    def next_number(self, kind: str, explicit: str | None = None) -> str:
        if explicit:
            self.counts[kind] = max(self.counts.get(kind, 0), int(explicit))
            return explicit
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return str(self.counts[kind])

    def asset(self, kind: str, caption: str, content: str, content_format: str, paragraph: int, explicit: str | None
              ) -> None:
        number = self.next_number(kind, explicit)
        self.assets.append(_Asset(f"{kind}:{number}", kind, number, caption, content, content_format, paragraph))

    def heading(self, level: int, inlines: list[dict[str, Any]]) -> None:
        text = text_of(inlines).strip()
        if not text:
            return
        match = NUMBERED_TITLE_RE.match(text)
        number, title = (match.group(1), match.group(2)) if match else ("", text)
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        parent = self.stack[-1][1] if self.stack else 0
        para = self.add("heading", text, level)
        self.sections.append(
            {"id": para.section, "parent": parent, "level": level, "number": number, "title": title,
             "paragraph": para.id}
        )
        self.stack.append((level, para.section))

    def caption_parts(self, caption: list[Any], kind: str) -> tuple[str, str | None]:
        """(caption text without its leading label, explicit number) of a pandoc Caption."""
        text = blocks_text(caption[1]).strip()
        match = CAPTION_LABEL_RE.match(text)
        if match and ("table" in match.group(1).lower()) == (kind == "table"):
            return match.group(3).strip(), match.group(2)
        return text, None

    def block(self, node: dict[str, Any]) -> None:
        kind: str = node["t"]
        content: Any = node.get("c")
        if kind == "Header":
            self.heading(content[0], content[2])
        elif kind in ("Para", "Plain"):
            self.paragraph(content)
        elif kind == "LineBlock":
            self.add("text", "\n".join(text_of(line) for line in content))
        elif kind == "CodeBlock":
            para = self.add("text", content[1])
            self.asset("listing", "", content[1], "text", para.id, None)
        elif kind == "BlockQuote":
            self.blocks(content)
        elif kind == "Div":
            self.blocks(content[1])
        elif kind in ("BulletList", "OrderedList"):
            for item in content if kind == "BulletList" else content[1]:
                self.list_item(item)
        elif kind == "DefinitionList":
            for term, definitions in content:
                self.add("text", text_of(term) + ": " + " ".join(blocks_text(d) for d in definitions))
        elif kind == "Table":
            self.table(content)
        elif kind == "Figure":
            self.figure(content)

    def blocks(self, nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            self.block(node)

    def list_item(self, item: list[dict[str, Any]]) -> None:
        text = blocks_text(item)
        if text:
            self.add("text", "- " + text)
        for node in item:
            if node["t"] in ("BulletList", "OrderedList", "Table", "Figure", "CodeBlock"):
                self.block(node)

    def paragraph(self, inlines: list[dict[str, Any]]) -> None:
        images = [node for node in inlines if node["t"] == "Image"]
        text = text_of(inlines).strip()
        if len(images) == 1 and len([n for n in inlines if n["t"] not in ("Space", "SoftBreak")]) == 1:
            alt = text_of(images[0]["c"][1]).strip()
            self.image(alt, images[0]["c"][2][0], alt)
            return
        if not text:
            return
        para = self.add("text", text)
        for node in inlines:
            if node["t"] == "Math" and node["c"][0]["t"] == "DisplayMath":
                self.asset("equation", "", node["c"][1], "latex", para.id, None)

    def image(self, caption: str, url: str, alt: str) -> None:
        label = CAPTION_LABEL_RE.match(caption)
        explicit = label.group(2) if label and "table" not in label.group(1).lower() else None
        stripped = label.group(3).strip() if label and explicit else caption
        number = self.next_number("figure", explicit)
        para = self.add("caption", f"Figure {number}. {stripped or alt or url}".strip())
        self.assets.append(_Asset(f"figure:{number}", "figure", number, stripped, f"![{alt}]({url})", "text", para.id))

    def figure(self, content: list[Any]) -> None:
        caption, explicit = self.caption_parts(content[1], "figure")
        url = next((n["c"][2][0] for b in content[2] for n in (b.get("c") or []) if isinstance(n, dict)
                    and n.get("t") == "Image"), "")
        number = self.next_number("figure", explicit)
        para = self.add("caption", f"Figure {number}. {caption or url}".strip())
        self.assets.append(_Asset(f"figure:{number}", "figure", number, caption, f"![{caption}]({url})", "text",
                                  para.id))

    def table(self, content: list[Any]) -> None:
        label = CAPTION_LABEL_RE.match(blocks_text(content[1][1]).strip())
        if label and label.group(1).lower().startswith("fig"):
            # pandoc's DOCX writer lays a captioned picture out as a one-cell table: it is a figure
            self.image(f"Figure {label.group(2)}. {label.group(3).strip()}", "", label.group(3).strip())
            return
        caption, explicit = self.caption_parts(content[1], "table")
        markdown = table_markdown(content)
        number = self.next_number("table", explicit)
        self.add("caption", f"Table {number}. {caption}".strip())
        para = self.add("text", markdown)
        self.assets.append(_Asset(f"table:{number}", "table", number, caption, markdown, "markdown", para.id))


def _paginate(paragraphs: list[_Para]) -> int:
    """Assign logical pages: whole blocks, a heading stays with the block after it. Returns the page count."""
    page, used = 1, 0
    previous: _Para | None = None
    for para in paragraphs:
        size = len(para.text) + 2
        if used and used + size > PAGE_CHARS:
            page += 1
            used = 0
            if previous is not None and previous.kind == "heading" and previous.page == page - 1:
                previous.page = page
                used = len(previous.text) + 2
        para.page = page
        used += size
        previous = para
    return page


def _unit_text(para: _Para) -> str:
    return f"{'#' * min(para.level, 6)} {para.text}" if para.kind == "heading" else para.text


def build(path: Path, fmt: str, db: Path, meta: dict[str, str]) -> None:
    """Write the document store for a non-PDF file (same tables as the PDF builder)."""
    ast = parse(path, fmt)
    walker = _Walker()
    walker.blocks(ast["blocks"])
    if not walker.paragraphs:
        raise ReaderError(f"{path.name!r} has no readable text.")
    pages = _paginate(walker.paragraphs)
    by_id = {p.id: p for p in walker.paragraphs}
    for asset in walker.assets:
        asset.page = by_id[asset.paragraph].page
    ids = {a.id: a for a in walker.assets}
    mentions: list[tuple[str, int, int, str]] = []
    for para in walker.paragraphs:
        if para.kind != "text":
            continue
        for kind, pattern in MENTION_RES.items():
            for match in pattern.finditer(para.text):
                key = f"{kind}:{match.group(1)}"
                if key in ids and ids[key].paragraph != para.id:
                    mentions.append((key, para.id, para.page, match.group(0)))
    properties = metadata(ast)
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(
            {**meta, **{f"doc_{k}": v for k, v in properties.items()}, "pages": str(pages), "file_name": path.name,
             "format": fmt, "pandoc_version": pandoc_version()}.items()))
        for number in range(1, pages + 1):
            mine = [p for p in walker.paragraphs if p.page == number]
            text = "\n\n".join(_unit_text(p) for p in mine)
            figures = sum(1 for a in walker.assets if a.kind == "figure" and a.page == number)
            con.execute(
                "INSERT INTO pages (page, width, height, label, source, images, drawings, markup_annots, chars, text) "
                "VALUES (?, 0, 0, ?, 'text', ?, 0, 0, ?, ?)", (number, str(number), figures, len(text), text))
        con.executemany(
            "INSERT INTO paragraphs (id, page, last_page, kind, section, first_line, last_line, text) "
            "VALUES (?, ?, ?, ?, ?, 0, 0, ?)", [(p.id, p.page, p.page, p.kind, p.section, p.text)
                                                 for p in walker.paragraphs])
        con.executemany(
            "INSERT INTO sections (id, parent, level, number, title, page, paragraph) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(s["id"], s["parent"], s["level"], s["number"], s["title"], by_id[s["paragraph"]].page, s["paragraph"])
             for s in walker.sections])
        con.execute("INSERT INTO segments (id, kind, first_page, last_page, label, evidence) "
                    "VALUES (1, 'manuscript', 1, ?, '', '[]')", (pages,))
        con.executemany("INSERT INTO structure (key, value) VALUES (?, ?)", [
            ("current_segment", "1"), ("current_confidence", '"high"'), ("current_evidence", "[]"), ("round", '""'),
            ("round_label", '""'), ("round_confidence", '""'), ("round_evidence", "[]")])
        con.executemany(
            "INSERT INTO assets (segment, id, kind, number, label, page, last_page, x0, y0, x1, y1, caption, content, "
            "content_format, method, confidence, paragraph, seq) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, 'pandoc-ast', 'high', ?, ?)",
            [(a.id, a.kind, a.number, f"{LABELS[a.kind]} {a.number}", a.page, a.page, a.caption, a.content,
              a.content_format, a.paragraph, seq) for seq, a in enumerate(walker.assets, 1)])
        con.executemany("INSERT INTO mentions (segment, asset, paragraph, page, text, strength) "
                        "VALUES (1, ?, ?, ?, ?, 'strong')", mentions)
        con.execute("INSERT INTO paragraph_search (paragraph_search) VALUES ('rebuild')")
        con.commit()
    finally:
        con.close()
