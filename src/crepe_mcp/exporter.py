"""Converts compiled presentation artifacts (PDF or PPTX) to PNG sequences
for visual validation by the agent.

PDF  → PNG  uses pymupdf  (pure Python, pip install pymupdf).
PPTX → PNG  prefers LibreOffice headless (PPTX -> PDF -> pymupdf), falling
             back to aspose-slides where LibreOffice isn't available.
             On Linux, LibreOffice is required with no fallback: its
             embedded .NET runtime needs libssl.so.1.1, which distros that
             ship only OpenSSL 3 (Slackware, recent Arch, etc.) don't have.

Environment variables (all prefixed CREPE_):
  CREPE_LIBREOFFICE_PATH    — path to a LibreOffice/soffice executable.
      Overrides auto-detection (PATH, macOS app bundle, Flatpak on Linux).
  CREPE_ASPOSE_LICENSE_PATH — path to an Aspose .lic file (fallback path only).
      Without it, aspose-slides runs in evaluation mode and adds watermark
      overlays to output PNGs. Acceptable for layout validation; set the
      variable to suppress watermarks for final delivery.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _find_libreoffice() -> Optional[list[str]]:
    """Return the command prefix to invoke LibreOffice headless, or None.

    Checks (in order): CREPE_LIBREOFFICE_PATH override, `soffice`/`libreoffice`
    on PATH, the macOS app bundle, and — Linux only — a Flatpak install of
    org.libreoffice.LibreOffice. Mirrors setup.py's find_headless_browser().
    """
    override = os.environ.get("CREPE_LIBREOFFICE_PATH", "").strip()
    if override and os.path.isfile(override):
        return [override]

    for binary in ("soffice", "libreoffice"):
        found = shutil.which(binary)
        if found:
            return [found]

    macos_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.isfile(macos_path) and os.access(macos_path, os.X_OK):
        return [macos_path]

    if sys.platform.startswith("linux") and shutil.which("flatpak"):
        try:
            result = subprocess.run(
                ["flatpak", "info", "org.libreoffice.LibreOffice"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                # Flatpak gives every app a private tmpfs for /tmp regardless of
                # --filesystem=host (host does not imply /tmp) -- both flags are
                # needed since presentation workdirs live under the system tempdir
                # but output_path/output_dir can point anywhere on the host.
                return [
                    "flatpak", "run", "--filesystem=host", "--filesystem=/tmp",
                    "org.libreoffice.LibreOffice",
                ]
        except Exception:
            pass

    return None


def _render_pptx_via_libreoffice(
    cmd_prefix: list[str],
    pptx_path: str,
    output_dir: str,
    dpi: int,
    timeout: int = 120,
) -> list[str]:
    """Convert PPTX -> PDF via LibreOffice headless, then rasterize with pymupdf."""
    cmd = cmd_prefix + ["--headless", "--convert-to", "pdf", "--outdir", output_dir, pptx_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice failed to convert PPTX to PDF:\n{result.stderr.strip()}")

    base = os.path.splitext(os.path.basename(pptx_path))[0]
    pdf_path = os.path.join(output_dir, base + ".pdf")
    if not os.path.isfile(pdf_path):
        raise RuntimeError(f"LibreOffice did not produce the expected PDF: {pdf_path!r}")

    try:
        return render_pdf_to_pngs(pdf_path, output_dir, dpi=dpi)
    finally:
        os.remove(pdf_path)


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


def _render_pptx_via_aspose(
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
            if hasattr(slide, "get_image"):
                image = slide.get_image(scale, scale)
            else:
                image = slide.get_thumbnail(scale, scale)
            try:
                if hasattr(image, "save"):
                    try:
                        image.save(out_path, slides.ImageFormat.PNG)
                    except TypeError:
                        image.save(out_path)
            finally:
                if hasattr(image, "dispose"):
                    image.dispose()
            png_files.append(out_path)
    return png_files, warning


def render_pptx_to_pngs(
    pptx_path: str,
    output_dir: str,
    dpi: int = 150,
) -> tuple[list[str], Optional[str], str]:
    """Render every slide of a PPTX to a numbered PNG sequence.

    Prefers LibreOffice headless (PPTX -> PDF -> pymupdf), falling back to
    aspose-slides where LibreOffice isn't available. On Linux, LibreOffice is
    required with no fallback — see the module docstring for why.

    Returns (png_files, warning, converter). warning is only ever set on the
    aspose-slides fallback path (evaluation-mode watermark notice).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    cmd = _find_libreoffice()

    if sys.platform.startswith("linux"):
        if cmd is None:
            raise ImportError(
                "LibreOffice is required to render PPTX slides on Linux (install via "
                "your package manager, e.g. libreoffice-impress, or "
                "`flatpak install flathub org.libreoffice.LibreOffice`)."
            )
        png_files = _render_pptx_via_libreoffice(cmd, pptx_path, output_dir, dpi)
        return png_files, None, "libreoffice"

    if cmd is not None:
        png_files = _render_pptx_via_libreoffice(cmd, pptx_path, output_dir, dpi)
        return png_files, None, "libreoffice"

    png_files, warning = _render_pptx_via_aspose(pptx_path, output_dir, dpi)
    return png_files, warning, "aspose-slides"
