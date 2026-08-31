"""Converts compiled document artifacts (PDF or DOCX) to PNG sequences for visual validation."""
from __future__ import annotations

import os

from crepe_mcp.exporter import _find_libreoffice, render_pdf_to_pngs, render_via_libreoffice


def render_document_as_pngs(
    artifact_path: str,
    output_dir: str,
    dpi: int = 150,
) -> tuple[list[str], str]:
    """Render every page of a PDF or DOCX document to a PNG sequence.

    Returns (png_files, converter_used).
    """
    if not os.path.isfile(artifact_path):
        raise FileNotFoundError(f"Artifact not found: {artifact_path!r}")

    ext = os.path.splitext(artifact_path)[1].lower()
    if ext == ".pdf":
        png_files = render_pdf_to_pngs(artifact_path, output_dir, dpi=dpi)
        return png_files, "pymupdf"
    elif ext in (".docx", ".doc"):
        cmd = _find_libreoffice()
        if cmd is None:
            raise ImportError("LibreOffice is required to render DOCX documents to PNGs.")
        png_files = render_via_libreoffice(cmd, artifact_path, output_dir, dpi=dpi)
        return png_files, "libreoffice"
    else:
        raise ValueError(f"Unsupported document artifact extension {ext!r}; expected .pdf or .docx")
