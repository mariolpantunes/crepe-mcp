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

import sys

def _ensure_linux_dotnet_compat() -> None:
    """Ensure embedded .NET Core runtime in aspose-slides has required ICU and OpenSSL 1.1 compat on Linux."""
    if not sys.platform.startswith("linux"):
        return
    os.environ.setdefault("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT", "1")
    # If libssl.so.1.1 is already in standard paths, no override needed
    import ctypes
    import ctypes.util
    if ctypes.util.find_library("ssl") and "1.1" in (ctypes.util.find_library("ssl") or ""):
        return
    candidate_dirs = [
        "/usr/lib64", "/usr/lib", "/lib64", "/lib",
        "/usr/local/lib", "/opt/lib",
        "/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu",
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/SteamLinuxRuntime_sniper/sniper_platform_3.0.20260608.242788/files/lib/x86_64-linux-gnu"),
        os.path.expanduser("~/.local/share/Steam/steamapps/common/SteamLinuxRuntime_sniper/sniper_platform_3.0.20260608.242788/files/lib/x86_64-linux-gnu"),
        os.path.expanduser("~/anaconda3/lib"),
        os.path.expanduser("~/miniconda3/lib"),
    ]
    import glob
    try:
        candidate_dirs.extend(glob.glob(os.path.expanduser("~/.var/app/*/files/lib/*-linux-gnu")))
        candidate_dirs.extend(glob.glob("/usr/lib/*-linux-gnu"))
    except Exception:
        pass

    for d in candidate_dirs:
        ssl_p = os.path.join(d, "libssl.so.1.1")
        crypto_p = os.path.join(d, "libcrypto.so.1.1")
        if os.path.isfile(ssl_p) and os.path.isfile(crypto_p):
            try:
                ctypes.CDLL(crypto_p, mode=ctypes.RTLD_GLOBAL)
                ctypes.CDLL(ssl_p, mode=ctypes.RTLD_GLOBAL)
                ld_path = os.environ.get("LD_LIBRARY_PATH", "")
                if d not in ld_path.split(":"):
                    os.environ["LD_LIBRARY_PATH"] = f"{d}:{ld_path}" if ld_path else d
                break
            except Exception:
                continue

_ensure_linux_dotnet_compat()


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
