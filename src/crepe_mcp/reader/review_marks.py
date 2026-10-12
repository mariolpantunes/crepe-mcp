"""Tracked changes and comments of a DOCX, read through pandoc's `--track-changes=all`.

The text tools show the accepted view of a DOCX (insertions kept, deletions dropped, comments left out). This
module lists what a reviewer marked: each insertion and deletion with its author and date, and each comment with
the text it is attached to, in document order and with the heading of the section it sits in.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Literal

from crepe_mcp.reader.common import ReaderError
from crepe_mcp.reader.pandoc_reader import PANDOC_TIMEOUT, pandoc_version, text_of

MarkKind = Literal["insertion", "deletion", "comment"]
TEXT_LIMIT = 400  # characters kept per mark


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


def _span(node: Any) -> tuple[str, dict[str, str], list[Any]] | None:
    """(class, attributes, inlines) of a pandoc Span node, or None for any other node."""
    if not isinstance(node, dict) or node.get("t") != "Span":
        return None
    (_, classes, pairs), inlines = node["c"]
    return (classes[0] if classes else ""), dict(pairs), inlines


LEAVES = ("Str", "Space", "SoftBreak", "LineBreak", "Code", "Math")
BLOCKS = ("Para", "Plain", "Header", "CodeBlock", "LineBlock")


def collect(ast: dict[str, Any]) -> list[dict[str, str]]:
    """Every insertion, deletion and comment of a pandoc AST (read with track changes kept), in document order.

    A comment's anchor is the document text between its start and end marker, across inline and paragraph
    boundaries; the text of a comment inside it belongs to that comment only.
    """
    marks: list[dict[str, str]] = []
    heading = [""]
    open_comments: dict[str, tuple[dict[str, str], list[str]]] = {}

    def emit(text: str) -> None:
        for _, parts in open_comments.values():
            parts.append(text)

    def finish(ident: str) -> None:
        mark, parts = open_comments.pop(ident)
        mark["anchor"] = _clip("".join(parts))

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        kind, content = node.get("t"), node.get("c")
        if kind in BLOCKS:
            emit(" ")
        if kind == "Header" and content:
            heading[0] = _clip(text_of(content[2]))
        if kind in LEAVES:
            emit(text_of([node]))
            return
        span = _span(node)
        if span is not None:
            name, attrs, inlines = span
            if name == "comment-start":
                mark = {
                    "kind": "comment",
                    "author": attrs.get("author", ""),
                    "date": attrs.get("date", ""),
                    "text": _clip(text_of(inlines)),
                    "section": heading[0],
                    "anchor": "",
                }
                marks.append(mark)
                open_comments[attrs.get("id", "")] = (mark, [])
                return
            if name == "comment-end":
                if attrs.get("id", "") in open_comments:
                    finish(attrs.get("id", ""))
                return
            if name in ("insertion", "deletion"):
                marks.append(
                    {
                        "kind": name,
                        "author": attrs.get("author", ""),
                        "date": attrs.get("date", ""),
                        "text": _clip(text_of(inlines)),
                        "section": heading[0],
                    }
                )
        if content is not None:
            walk(content)

    walk(ast.get("blocks", []))
    for ident in list(open_comments):
        finish(ident)
    return marks


def read_marks(path: Path) -> list[dict[str, str]]:
    """The marks of a DOCX file; raises ReaderError when pandoc is missing or cannot read it."""
    pandoc_version()
    cmd = ["pandoc", "--sandbox", "-f", "docx", "--track-changes=all", "-t", "json", "--wrap=none", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PANDOC_TIMEOUT)
    except subprocess.TimeoutExpired as error:
        raise ReaderError(f"pandoc needed more than {PANDOC_TIMEOUT}s to read {path.name!r}.") from error
    if result.returncode != 0:
        raise ReaderError(f"pandoc could not read {path.name!r} as docx: {result.stderr.strip()[:300]}")
    return collect(json.loads(result.stdout))


def summary(marks: list[dict[str, str]], kind: str, limit: int) -> dict[str, Any]:
    """The tool reply: counts per kind over all marks, and up to `limit` marks of the requested kind."""
    counts = {name: sum(1 for mark in marks if mark["kind"] == name) for name in ("insertion", "deletion", "comment")}
    chosen = [mark for mark in marks if kind == "all" or mark["kind"] == kind]
    authors = sorted({mark["author"] for mark in marks if mark["author"]})
    return {
        "counts": counts,
        "authors": authors,
        "returned": min(len(chosen), limit),
        "matching": len(chosen),
        "items": chosen[:limit],
    }
