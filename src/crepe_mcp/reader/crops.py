"""Cropped images of numbered items for models that can see images (rendered on request, never written to disk).

Whether images are attached, how many per paper and their size are the `images` settings of config.json.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

from crepe_mcp.reader.config import load_section


def settings() -> dict[str, int | bool]:
    """The images settings: enabled, budget (images per paper) and max_side (pixels), read on every call."""
    values = load_section("images")
    return {"enabled": bool(values["enabled"]), "budget": int(values["budget"]), "max_side": int(values["max_side"])}


def crop_png(pdf: Path, page: int, bbox: tuple[float, float, float, float], max_side: int) -> bytes:
    """PNG of a page region, scaled so that its longest side is max_side pixels."""
    with fitz.open(str(pdf)) as doc:
        pdf_page = doc[page - 1]
        rect = fitz.Rect(bbox) & pdf_page.rect
        if rect.is_empty:
            raise ValueError(f"The region of this item lies outside page {page}.")
        zoom = max_side / max(rect.width, rect.height)
        pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
        return pixmap.tobytes("png")
