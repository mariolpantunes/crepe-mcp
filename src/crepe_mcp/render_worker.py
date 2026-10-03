"""Worker process for PNG rendering: ``python -m crepe_mcp.render_worker PDF OUTDIR DPI PAGE [PAGE ...]``.

Renders the given 1-based pages to ``slide_NNN.png`` in OUTDIR. Started as a plain subprocess by
``exporter.render_pdf_to_pngs``, so pages render side by side without sharing any pymupdf state.
"""
from __future__ import annotations

import os
import sys

import pymupdf


def render_pages(pdf_path: str, output_dir: str, dpi: int, pages: list[int]) -> list[str]:
    """Render the 1-based pages of a PDF; returns the PNG paths in the order of `pages`."""
    os.makedirs(output_dir, exist_ok=True)
    zoom = dpi / 72.0  # pymupdf base resolution is 72 dpi
    matrix = pymupdf.Matrix(zoom, zoom)
    written: list[str] = []
    with pymupdf.open(pdf_path) as doc:
        for number in pages:
            path = os.path.join(output_dir, f"slide_{number:03d}.png")
            doc[number - 1].get_pixmap(matrix=matrix).save(path)
            written.append(path)
    return written


def main(argv: list[str]) -> int:
    render_pages(argv[1], argv[2], int(argv[3]), [int(page) for page in argv[4:]])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
