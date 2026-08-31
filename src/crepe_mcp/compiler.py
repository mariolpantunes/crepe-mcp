"""Invokes pandoc to compile a presentation to PDF (Beamer) or PPTX."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from crepe_mcp.renderer import build_config_yaml, build_slides_markdown, has_sections
from crepe_mcp.store import Presentation

PANDOC = "pandoc"


class CompileError(RuntimeError):
    pass


def _write_sources(
    presentation: Presentation,
    theme: str = "moloch",
    highlight_style: str = "tango",
) -> tuple[str, str, str]:
    """Snapshot presentation state under its lock, then write source files to disk.

    Holds presentation.lock only for the markdown/config build (microseconds),
    not for the file writes — so concurrent set_slide calls are not blocked
    during disk I/O.
    """
    config_path = os.path.join(presentation.workdir, "config.yml")
    slides_path = os.path.join(presentation.workdir, "slides.md")
    with presentation.lock:
        markdown = build_slides_markdown(presentation)
        config_yaml = build_config_yaml(presentation, theme=theme, highlight_style=highlight_style)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config_yaml)
    with open(slides_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return config_path, slides_path, markdown


def compile_to_pdf(
    presentation: Presentation,
    output_path: str,
    theme: str = "moloch",
    highlight_style: str = "tango",
    timeout: int = 120,
) -> None:
    """Compile presentation to a Beamer PDF using lualatex."""
    if shutil.which(PANDOC) is None:
        raise CompileError("pandoc is not installed or not on PATH")
    if shutil.which("lualatex") is None:
        raise CompileError("lualatex is not installed or not on PATH (required for PDF output)")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    config_path, slides_path, markdown = _write_sources(
        presentation, theme=theme, highlight_style=highlight_style
    )
    # Only ask pandoc for a table of contents if the deck actually defines
    # section headings ("# Section Title" slides); otherwise Beamer's
    # \tableofcontents renders a blank frame with nothing to list.
    toc_flags = ["--toc", "--toc-depth=1"] if has_sections(markdown) else []
    cmd = [
        PANDOC, "-s",
        f"--metadata-file={config_path}",
        "--slide-level=2",
        "-t", "beamer",
        "--pdf-engine=lualatex",
        *toc_flags,
        "-o", output_path,
        slides_path,
    ]
    try:
        result = subprocess.run(
            cmd, cwd=presentation.workdir,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompileError(f"pandoc timed out after {timeout}s building PDF") from exc
    if result.returncode != 0:
        raise CompileError(f"pandoc failed building PDF:\n{result.stderr.strip()}")



def compile_to_pptx(
    presentation: Presentation,
    output_path: str,
    reference_doc: str | None = None,
    theme: str = "moloch",
    highlight_style: str = "tango",
    timeout: int = 60,
) -> None:
    """Compile presentation to a PowerPoint (.pptx) file.

    theme and highlight_style are accepted for API symmetry with compile_to_pdf
    but are ignored by the pandoc PPTX writer — a warning is emitted to stderr
    if non-default values are passed so the caller is not silently misled.
    Use reference_doc (a .pptx template) to control PPTX styling instead.
    """
    if shutil.which(PANDOC) is None:
        raise CompileError("pandoc is not installed or not on PATH")
    if reference_doc and not os.path.isfile(reference_doc):
        raise CompileError(f"reference_doc not found: {reference_doc!r}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if theme != "moloch":
        print(
            f"compile_to_pptx: 'theme' ({theme!r}) is ignored for PPTX output — "
            "use reference_doc to apply a custom template.",
            file=sys.stderr,
        )
    if highlight_style != "tango":
        print(
            f"compile_to_pptx: 'highlight_style' ({highlight_style!r}) is ignored "
            "for PPTX output.",
            file=sys.stderr,
        )

    # build_config_yaml always emits Beamer-specific keys (theme, header-includes)
    # regardless of target; pandoc simply ignores the ones it doesn't use for PPTX.
    config_path, slides_path, _markdown = _write_sources(presentation)
    cmd = [
        PANDOC, "-s",
        f"--metadata-file={config_path}",
        "--slide-level=2",
        "-o", output_path,
        slides_path,
    ]
    if reference_doc:
        cmd.append(f"--reference-doc={reference_doc}")
    try:
        result = subprocess.run(
            cmd, cwd=presentation.workdir,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompileError(f"pandoc timed out after {timeout}s building PPTX") from exc
    if result.returncode != 0:
        raise CompileError(f"pandoc failed building PPTX:\n{result.stderr.strip()}")
