"""Shared pieces of the reader tools: errors, page spans and the checked path of a document."""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from pathlib import Path

#: Formats read through pandoc's AST; PDF has its own extractor.
PANDOC_FORMATS: dict[str, str] = {
    ".docx": "docx",
    ".odt": "odt",
    ".rtf": "rtf",
    ".epub": "epub",
    ".html": "html",
    ".htm": "html",
    ".tex": "latex",
    ".rst": "rst",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "markdown",
    ".org": "org",
    ".ipynb": "ipynb",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".tsv": "tsv",
    ".fb2": "fb2",
    ".xml": "jats",
}
#: Legacy binary Office formats, converted by LibreOffice to the OOXML format that pandoc reads.
LEGACY_OFFICE: dict[str, str] = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx"}
SUPPORTED = (".pdf", *PANDOC_FORMATS, *LEGACY_OFFICE)
ROOTS_VAR = "CREPE_READER_ROOTS"


class ReaderError(ValueError):
    """A request the agent can correct; the message says how."""


def page_span(first: int, last: int) -> str:
    return str(first) if first == last else f"{first}-{last}"


def _local_path(text: str) -> str:
    """A plain path, from a plain path or a file:// URL; other URLs are refused with the tool to use instead."""
    parts = urllib.parse.urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme == "file":
        if parts.netloc not in ("", "localhost"):
            raise ReaderError(f"Only local file:// URLs are read, got host {parts.netloc!r}.")
        return urllib.request.url2pathname(parts.path)
    if scheme in ("http", "https"):
        raise ReaderError(
            "crepe-reader reads local files only, not web URLs. Download the file first (for example "
            f"`curl -L -o /tmp/doc.pdf URL`) and pass its absolute path; for the text of an HTML page use "
            f"crepe-research fetch_webpage. Got {text[:120]!r}."
        )
    return text


def resolve_path(path: str, extra: tuple[str, ...] = ()) -> Path:
    """The document named by the agent: an absolute path (or file:// URL) to a supported, readable file.

    When CREPE_READER_ROOTS lists folders (separated like PATH), the file must be inside one of them. `extra` adds
    file suffixes that are accepted besides the document formats (figure files for render_page_image).
    """
    given = Path(_local_path(path.strip())).expanduser()
    if not given.is_absolute():
        raise ReaderError(f"Pass the absolute path of the document, got {path[:120]!r}.")
    resolved = given.resolve()
    if resolved.suffix.lower() not in (*SUPPORTED, *extra):
        raise ReaderError(f"{resolved.suffix or 'This file type'} is not supported. Supported: {', '.join(SUPPORTED)}.")
    roots = [Path(r).expanduser().resolve() for r in os.environ.get(ROOTS_VAR, "").split(os.pathsep) if r.strip()]
    if roots and not any(resolved.is_relative_to(root) for root in roots):
        raise ReaderError(f"{resolved.name!r} is outside the folders this server may read.")
    if not resolved.is_file():
        raise ReaderError(f"No such file: {resolved.name!r} (looked for {str(given)[:200]!r}).")
    return resolved
