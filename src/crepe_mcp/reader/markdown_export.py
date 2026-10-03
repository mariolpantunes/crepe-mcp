"""A whole document as one Markdown file: pandoc for the formats it reads, the extracted text for PDF."""

from __future__ import annotations

import subprocess
from pathlib import Path

from crepe_mcp.reader.common import PANDOC_FORMATS, ReaderError
from crepe_mcp.reader.overview import part_range
from crepe_mcp.reader.store import DocumentStore

EXPORT_TIMEOUT = 120


def pdf_markdown(store: DocumentStore, first: int, last: int) -> str:
    """Pages first..last as Markdown: headings from the outline, paragraphs, and tables and equations from the
    numbered items placed after their captions or paragraphs."""
    levels = {s["id"]: s["level"] for s in store.outline()}
    extras: dict[int, list[str]] = {}
    for asset in store.assets(first=first, last=last):
        if asset["kind"] in ("table", "equation", "algorithm", "listing") and asset["content"]:
            body = asset["content"] if asset["content_format"] == "markdown" else f"```\n{asset['content']}\n```"
            extras.setdefault(asset["paragraph"], []).append(body)
    out: list[str] = []
    for para in store.paragraphs(first, last):
        if para["kind"] == "heading":
            out.append(f"{'#' * min(levels.get(para['section'], 1), 6)} {para['text']}")
        else:
            out.append(para["text"])
        out.extend(extras.get(para["id"], []))
    return "\n\n".join(out) + "\n"


def export_markdown(store: DocumentStore, path: Path, output: Path, part: str) -> dict[str, object]:
    """Write the document to `output` (absolute, .md) and say what was written."""
    if output.suffix.lower() not in (".md", ".markdown"):
        raise ReaderError("output_path must end in .md")
    output.parent.mkdir(parents=True, exist_ok=True)
    first, last = part_range(store, part)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        output.write_text(pdf_markdown(store, first, last), encoding="utf-8")
        method = "extracted text"
    else:
        cmd = ["pandoc", "-f", PANDOC_FORMATS[suffix], "-t", "gfm", "--wrap=none", "-o", str(output), str(path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=EXPORT_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            raise ReaderError(f"pandoc needed more than {EXPORT_TIMEOUT}s to convert {path.name!r}.") from error
        if result.returncode != 0:
            raise ReaderError(f"pandoc could not convert {path.name!r}: {result.stderr.strip()[:300]}")
        method = "pandoc (gfm)"
    return {"output_path": str(output), "bytes": output.stat().st_size, "method": method}
