"""One page of a document as an image: PDF directly, Office formats through LibreOffice.

A DOCX, ODT, RTF or PPTX is laid out by LibreOffice into a PDF, which is kept in the reader's scratch folder
(keyed by the file's path, size and modification time) so that the next page of the same file is instant.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Literal

import pymupdf

from crepe_mcp.exporter import find_libreoffice, office_to_pdf
from crepe_mcp.reader.common import ReaderError
from crepe_mcp.reader.config import scratch_base

ImageFormat = Literal["png", "jpeg"]
#: Output file suffixes accepted for each format.
SUFFIXES: dict[str, tuple[str, ...]] = {"png": (".png",), "jpeg": (".jpg", ".jpeg")}
#: Formats that LibreOffice can lay out into pages.
OFFICE_SUFFIXES = (".docx", ".odt", ".rtf", ".pptx")
JPEG_QUALITY = 85
CONVERT_TIMEOUT = 180
CACHE_MAX_AGE = 7 * 24 * 3600  # seconds a converted PDF is kept after its last use
WORK_MAX_AGE = 3600  # seconds after which an interrupted conversion's folder is removed


def check_output_path(output_path: str, fmt: ImageFormat) -> Path:
    """The absolute target file for the image, with a suffix that matches the format."""
    target = Path(output_path)
    if not target.is_absolute() or target.suffix.lower() not in SUFFIXES[fmt]:
        expected = " or ".join(SUFFIXES[fmt])
        raise ReaderError(f"output_path must be an absolute path ending in {expected} for format {fmt!r}.")
    return target


def _evict(base: Path) -> None:
    """Remove converted PDFs not used for CACHE_MAX_AGE seconds and conversion folders an interrupted run left."""
    now = time.time()
    for entry in base.iterdir():
        limit = WORK_MAX_AGE if entry.name.startswith("convert-") else CACHE_MAX_AGE
        try:
            if entry.is_dir() and now - entry.stat().st_mtime > limit:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _converted_pdf(source: Path) -> Path:
    """The PDF LibreOffice makes of an Office file, converted once and reused while the file is unchanged.

    The cache lives in the reader's scratch folder with owner-only permissions (the PDF holds the document's
    text) and entries unused for CACHE_MAX_AGE seconds are removed when a new conversion is made.
    """
    stat = source.stat()
    key = f"{source}|{stat.st_size}|{stat.st_mtime_ns}"
    base = scratch_base() / "office-pdf"
    folder = base / hashlib.sha256(key.encode()).hexdigest()[:24]
    cached = folder / f"{source.stem}.pdf"
    if cached.is_file():
        os.utime(folder)  # a document still in use does not age out
        return cached
    command = find_libreoffice()
    if command is None:
        raise ReaderError(
            f"Rendering a {source.suffix} page needs LibreOffice, which was not found. Install it or set "
            "CREPE_LIBREOFFICE_PATH; or save the document as PDF first."
        )
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    _evict(base)
    work = tempfile.mkdtemp(prefix="convert-", dir=base)  # mkdtemp folders are 0700
    try:
        try:
            pdf = office_to_pdf(command, str(source), work, timeout=CONVERT_TIMEOUT)
        except RuntimeError as error:
            raise ReaderError(str(error)) from error
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        os.chmod(pdf, 0o600)
        os.replace(pdf, cached)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return cached


def render_page(source: Path, page: int, dpi: int, fmt: ImageFormat) -> tuple[bytes, int]:
    """The image bytes of one page and the page count of the rendered PDF.

    For an Office file the page is the page of the document as LibreOffice lays it out (the slide number of a
    deck), which differs from the logical pages that read_pages uses.
    """
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        pdf = source
    elif suffix in OFFICE_SUFFIXES:
        pdf = _converted_pdf(source)
    else:
        raise ReaderError(
            f"Pages can be shown for PDF and {', '.join(OFFICE_SUFFIXES)} files, not {suffix}. "
            "Use document_to_markdown to get the content, or save the document as PDF."
        )
    with pymupdf.open(str(pdf)) as doc:
        count = len(doc)
        if page > count:
            raise ReaderError(f"Page {page} is outside this document (1-{count}).")
        pixmap = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
        if fmt == "jpeg":
            return pixmap.tobytes("jpeg", jpg_quality=JPEG_QUALITY), count
        return pixmap.tobytes("png"), count
