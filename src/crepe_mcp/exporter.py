"""Converts compiled presentation artifacts (PDF or PPTX) to PNG sequences
for visual validation by the agent.

PDF  → PNG  uses pymupdf  (pure Python, pip install pymupdf).
PPTX → PNG  uses LibreOffice headless (PPTX -> PDF -> pymupdf). LibreOffice
             is required on every platform, with no fallback.

Environment variables (all prefixed CREPE_):
  CREPE_LIBREOFFICE_PATH — path to a LibreOffice/soffice executable.
      Overrides auto-detection (PATH, macOS app bundle, Flatpak on Linux).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _find_libreoffice() -> list[str] | None:
    """Return the command prefix to invoke LibreOffice headless, or None.

    Checks (in order): CREPE_LIBREOFFICE_PATH override, `soffice`/`libreoffice`
    on PATH, the macOS app bundle, and — Linux only — a Flatpak install of
    org.libreoffice.LibreOffice. Mirrors setup.py's find_headless_browser().
    """
    override = os.environ.get("CREPE_LIBREOFFICE_PATH", "").strip()
    if override:
        if "org.libreoffice.LibreOffice" in override or "flatpak" in override:
            return ["flatpak", "run", "--filesystem=host", "--filesystem=/tmp", "org.libreoffice.LibreOffice"]
        if os.path.isfile(override) and os.access(override, os.X_OK):
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


def _render_office_to_pngs(
    cmd_prefix: list[str],
    pptx_path: str,
    output_dir: str,
    dpi: int,
    timeout: int = 120,
) -> list[str]:
    """Convert an Office file (PPTX or DOCX) → PDF via LibreOffice headless, then rasterize with pymupdf."""
    with tempfile.TemporaryDirectory(prefix="crepe_lo_") as profile_dir:
        # -env:UserInstallation isolates this invocation from any running LibreOffice instance or leftover lock
        cmd = cmd_prefix + [
            f"-env:UserInstallation=file://{profile_dir}",
            "--headless",
            "--convert-to", "pdf",
            "--outdir", output_dir,
            pptx_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"LibreOffice timed out converting {os.path.basename(pptx_path)!r} to PDF") from exc
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice failed to convert document to PDF:\n{result.stderr.strip()}")

    base = os.path.splitext(os.path.basename(pptx_path))[0]
    pdf_path = os.path.join(output_dir, base + ".pdf")
    if not os.path.isfile(pdf_path):
        raise RuntimeError(f"LibreOffice did not produce the expected PDF: {pdf_path!r}")

    try:
        return render_pdf_to_pngs(pdf_path, output_dir, dpi=dpi)
    finally:
        os.remove(pdf_path)


# Public aliases — use these in preference to private names.
render_via_libreoffice = _render_office_to_pngs
find_libreoffice = _find_libreoffice


RENDER_PARALLEL_MIN_PAGES = 4  # fewer pages are faster in one process than the workers' start-up
MAX_RENDER_WORKERS = 8
RENDER_WORKER_TIMEOUT = 600


def _render_in_workers(pdf_path: str, output_dir: str, dpi: int, count: int) -> bool:
    """Render pages 1..count with worker processes (one share of the pages each). True when every page was written;
    on any failure the caller renders the pages itself."""
    workers = min(count, os.cpu_count() or 1, MAX_RENDER_WORKERS)
    if count < RENDER_PARALLEL_MIN_PAGES or workers < 2:
        return False
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in sys.path if path)}
    running: list[subprocess.Popen[bytes]] = []
    ok = True
    try:
        for index in range(workers):
            pages = [str(number) for number in range(1 + index, count + 1, workers)]
            cmd = [sys.executable, "-m", "crepe_mcp.render_worker", pdf_path, output_dir, str(dpi), *pages]
            running.append(subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL))
        for process in running:
            try:
                ok = process.wait(timeout=RENDER_WORKER_TIMEOUT) == 0 and ok
            except subprocess.TimeoutExpired:
                ok = False
    except OSError:
        ok = False
    finally:
        for process in running:
            if process.poll() is None:
                process.kill()
            process.wait()
    return ok


def render_pdf_to_pngs(
    pdf_path: str,
    output_dir: str,
    dpi: int = 150,
) -> list[str]:
    """Render every page of a PDF to a numbered PNG sequence via pymupdf (long PDFs in worker processes)."""
    try:
        import pymupdf
    except ImportError as exc:
        raise ImportError("pymupdf is not installed. Run: pip install pymupdf") from exc

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as doc:
        count = len(doc)
    expected = [os.path.join(output_dir, f"slide_{number:03d}.png") for number in range(1, count + 1)]
    if _render_in_workers(pdf_path, output_dir, dpi, count) and all(os.path.isfile(path) for path in expected):
        return expected
    from crepe_mcp.render_worker import render_pages

    return render_pages(pdf_path, output_dir, dpi, list(range(1, count + 1)))


def render_pptx_to_pngs(
    pptx_path: str,
    output_dir: str,
    dpi: int = 150,
) -> tuple[list[str], str]:
    """Render every slide of a PPTX to a numbered PNG sequence.

    Uses LibreOffice headless (PPTX -> PDF -> pymupdf). LibreOffice is
    required on every platform, with no fallback.

    Returns (png_files, converter).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    cmd = _find_libreoffice()
    if cmd is None:
        raise RuntimeError(
            "LibreOffice is required to render PPTX slides (install via your "
            "package manager, e.g. libreoffice-impress, the macOS app bundle, or "
            "`flatpak install flathub org.libreoffice.LibreOffice`)."
        )
    png_files = _render_office_to_pngs(cmd, pptx_path, output_dir, dpi)
    return png_files, "libreoffice"
