"""Converts compiled presentation artifacts (PDF or PPTX) to PNG sequences
for visual validation by the agent.

PDF  → PNG  uses pymupdf  (pure Python, pip install pymupdf).
PPTX → PNG  uses aspose-slides (pip install aspose-slides).

Environment variables (all prefixed CREPE_):
  CREPE_ASPOSE_LICENSE_PATH — path to an Aspose .lic file.
      Without it, aspose-slides runs in evaluation mode and adds watermark
      overlays to output PNGs. Acceptable for layout validation; set the
      variable to suppress watermarks for final delivery.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _apply_aspose_license() -> bool:
    """Apply an Aspose license from CREPE_ASPOSE_LICENSE_PATH if set."""
    license_path = os.environ.get("CREPE_ASPOSE_LICENSE_PATH", "").strip()
    if not license_path or not os.path.isfile(license_path):
        return False
    try:
        import aspose.slides as slides
        lic = slides.License()
        lic.set_license(license_path)
        return True
    except Exception:
        return False


def render_pdf_to_pngs(
    pdf_path: str,
    output_dir: str,
    dpi: int = 150,
) -> list[str]:
    """Render every page of a PDF to a numbered PNG sequence via pymupdf."""
    try:
        import fitz  # pymupdf
    except ImportError:
        raise ImportError("pymupdf is not installed. Run: pip install pymupdf")

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0       # pymupdf base resolution is 72 dpi
    mat = fitz.Matrix(zoom, zoom)

    png_files: list[str] = []
    doc = fitz.open(pdf_path)
    try:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            out_path = os.path.join(output_dir, f"slide_{i + 1:03d}.png")
            pix.save(out_path)
            png_files.append(out_path)
    finally:
        doc.close()
    return png_files


def render_pptx_to_pngs(
    pptx_path: str,
    output_dir: str,
    dpi: int = 150,
) -> tuple[list[str], Optional[str]]:
    """Render every slide of a PPTX to a numbered PNG sequence via aspose-slides.

    Returns (png_files, warning).  warning is None when a valid license was
    applied; otherwise a human-readable string describes the evaluation limit.
    """
    try:
        import aspose.slides as slides
    except ImportError:
        raise ImportError("aspose-slides is not installed. Run: pip install aspose-slides")

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    licensed = _apply_aspose_license()
    warning: Optional[str] = (
        None if licensed else
        "Aspose is running in evaluation mode — output PNGs will contain watermarks. "
        "Set CREPE_ASPOSE_LICENSE_PATH to a valid .lic file to suppress watermarks."
    )

    scale = dpi / 96.0      # aspose-slides default thumbnail base is 96 dpi
    png_files: list[str] = []
    with slides.Presentation(pptx_path) as prs:
        for i, slide in enumerate(prs.slides):
            out_path = os.path.join(output_dir, f"slide_{i + 1:03d}.png")
            with slide.get_thumbnail(scale, scale) as image:
                image.save(out_path)
            png_files.append(out_path)
    return png_files, warning
