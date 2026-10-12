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


def _anchors(siblings: list[Any]) -> dict[str, str]:
    """For each comment-start among sibling inlines, the text up to the matching comment-end."""
    starts: dict[str, int] = {}
    found: dict[str, str] = {}
    for index, node in enumerate(siblings):
        span = _span(node)
        if span is None:
            continue
        name, attrs, _ = span
        if name == "comment-start":
            starts[attrs.get("id", "")] = index
        elif name == "comment-end" and attrs.get("id", "") in starts:
            begin = starts.pop(attrs.get("id", ""))
            found[attrs.get("id", "")] = text_of(siblings[begin + 1 : index])
    return found


def collect(ast: dict[str, Any]) -> list[dict[str, str]]:
    """Every insertion, deletion and comment of a pandoc AST (read with track changes kept), in document order."""
    marks: list[dict[str, str]] = []
    heading = [""]
    anchors: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, list):
            anchors.update(_anchors(node))
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        kind, content = node.get("t"), node.get("c")
        if kind == "Header" and content:
            heading[0] = _clip(text_of(content[2]))
        span = _span(node)
        if span is not None and span[0] in ("insertion", "deletion", "comment-start"):
            name, attrs, inlines = span
            mark = {
                "kind": "comment" if name == "comment-start" else name,
                "author": attrs.get("author", ""),
                "date": attrs.get("date", ""),
                "text": _clip(text_of(inlines)),
                "section": heading[0],
            }
            if name == "comment-start":
                mark["anchor"] = _clip(anchors.get(attrs.get("id", ""), ""))
            marks.append(mark)
        if content is not None:
            walk(content)

    walk(ast.get("blocks", []))
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
