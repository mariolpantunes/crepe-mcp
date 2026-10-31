"""Figure files of a non-PDF document: where a relative image path points and the image itself.

A Markdown deck names its figures relative to the folder pandoc runs in (`plots/07/e1.pdf`) and lists the folders to
search in `resource-path` of a `defaults.yml` next to it. Only relative paths to PDF, PNG, JPEG and SVG files that
lie in the document folder or in one of those folders are followed, and inside CREPE_READER_ROOTS when it is set.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pymupdf
import yaml

from crepe_mcp.reader.common import ROOTS_VAR, ReaderError

IMAGE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".svg")
RASTER_SUFFIXES = (".png", ".jpg", ".jpeg")
DEFAULTS_NAMES = ("defaults.yml", "defaults.yaml")
URL_RE = re.compile(r"\]\(([^)]*)\)$")


def figure_url(content: str) -> str:
    """The image path inside a figure asset's `![alt](url)` content, or an empty string."""
    match = URL_RE.search(content.strip())
    return match.group(1).strip().split(maxsplit=1)[0] if match and match.group(1).strip() else ""


def _roots_allow(path: Path) -> bool:
    roots = [Path(r).expanduser().resolve() for r in os.environ.get(ROOTS_VAR, "").split(os.pathsep) if r.strip()]
    return not roots or any(path.is_relative_to(root) for root in roots)


def search_roots(document: Path) -> list[Path]:
    """The folders a relative figure path is tried in: the document folder, then its defaults file's resource-path."""
    folder = document.parent
    roots = [folder]
    for name in DEFAULTS_NAMES:
        defaults = folder / name
        if not defaults.is_file():
            continue
        try:
            loaded = yaml.safe_load(defaults.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            break
        listed = loaded.get("resource-path", []) if isinstance(loaded, dict) else []
        if isinstance(listed, str):
            listed = [listed]
        roots += [(folder / str(item)).resolve() for item in listed if isinstance(item, str)]
        break
    return roots


def resolve_figure(document: Path, url: str) -> tuple[Path | None, str]:
    """(file, note): the figure file the url names, or None with the reason in the note."""
    if not url:
        return None, "no image path"
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):
        return None, f"remote image, not fetched: {url[:120]}"
    if Path(url).is_absolute():
        return None, f"absolute image paths are not followed: {url[:120]}"
    if Path(url).suffix.lower() not in IMAGE_SUFFIXES:
        return None, f"not a PDF, PNG, JPEG or SVG image: {url[:120]}"
    roots = search_roots(document)
    for root in roots:
        candidate = (root / url).resolve()
        if candidate.is_file() and _roots_allow(candidate):
            return candidate, "resolved"
    where = ", ".join(root.name or str(root) for root in roots)
    return None, f"missing: {url[:120]} not found in {where}"


def image_png(path: Path, max_side: int) -> bytes:
    """PNG of a figure file (first page of a PDF), its longest side at most max_side pixels."""
    try:
        with pymupdf.open(str(path)) as doc:
            page = doc[0]
            longest = max(page.rect.width, page.rect.height)
            zoom = max_side / longest
            if path.suffix.lower() in RASTER_SUFFIXES:
                zoom = min(zoom, 1.0)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return pixmap.tobytes("png")
    except Exception as error:  # noqa: BLE001 - pymupdf raises its own error classes for broken files
        raise ReaderError(f"Cannot read the image {path.name!r}: {error}") from error
