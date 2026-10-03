"""Shared pieces of the reader tools: errors, page spans and the checked path of a document."""

from __future__ import annotations

import os
from pathlib import Path

#: Formats read through pandoc's AST; PDF has its own extractor.
PANDOC_FORMATS: dict[str, str] = {
    ".docx": "docx", ".odt": "odt", ".rtf": "rtf", ".epub": "epub", ".html": "html", ".htm": "html",
    ".tex": "latex", ".rst": "rst", ".md": "markdown", ".markdown": "markdown", ".txt": "markdown",
    ".org": "org", ".ipynb": "ipynb", ".pptx": "pptx", ".xlsx": "xlsx", ".csv": "csv", ".tsv": "tsv",
    ".fb2": "fb2", ".xml": "jats",
}
SUPPORTED = (".pdf", *PANDOC_FORMATS)
ROOTS_VAR = "CREPE_READER_ROOTS"


class ReaderError(ValueError):
    """A request the agent can correct; the message says how."""


def page_span(first: int, last: int) -> str:
    return str(first) if first == last else f"{first}-{last}"


def resolve_path(path: str) -> Path:
    """The document named by the agent: an absolute path to a supported, readable file.

    When CREPE_READER_ROOTS lists folders (separated like PATH), the file must be inside one of them.
    """
    given = Path(path.strip()).expanduser()
    if not given.is_absolute():
        raise ReaderError(f"Pass the absolute path of the document, got {path[:120]!r}.")
    resolved = given.resolve()
    if resolved.suffix.lower() not in SUPPORTED:
        raise ReaderError(f"{resolved.suffix or 'This file type'} is not supported. Supported: {', '.join(SUPPORTED)}.")
    roots = [Path(r).expanduser().resolve() for r in os.environ.get(ROOTS_VAR, "").split(os.pathsep) if r.strip()]
    if roots and not any(resolved.is_relative_to(root) for root in roots):
        raise ReaderError(f"{resolved.name!r} is outside the folders this server may read.")
    if not resolved.is_file():
        raise ReaderError(f"No such file: {resolved.name!r} (looked for {str(given)[:200]!r}).")
    return resolved
