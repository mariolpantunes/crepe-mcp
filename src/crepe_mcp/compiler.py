"""Invokes pandoc to compile a presentation to PDF (Beamer) or PPTX."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from crepe_mcp.renderer import build_config_yaml, build_slides_markdown
from crepe_mcp.store import Presentation

PANDOC = "pandoc"


class CompileError(RuntimeError):
    pass


def _write_sources(
    presentation: Presentation,
    theme: str = "moloch",
    highlight_style: str = "tango",
) -> tuple[str, str]:
    config_path = os.path.join(presentation.workdir, "config.yml")
    slides_path = os.path.join(presentation.workdir, "slides.md")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(build_config_yaml(presentation, theme=theme, highlight_style=highlight_style))
    with open(slides_path, "w", encoding="utf-8") as f:
        f.write(build_slides_markdown(presentation))
    return config_path, slides_path


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

    config_path, slides_path = _write_sources(
        presentation, theme=theme, highlight_style=highlight_style
    )
    cmd = [
        PANDOC, "-s",
        f"--metadata-file={config_path}",
        "--slide-level=2",
        "-t", "beamer",
        "--pdf-engine=lualatex",
        "--toc", "--toc-depth=1",
        "-o", output_path,
        slides_path,
    ]
    try:
        result = subprocess.run(
            cmd, cwd=presentation.workdir,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise CompileError(f"pandoc timed out after {timeout}s building PDF")
    if result.returncode != 0:
        raise CompileError(f"pandoc failed building PDF:\n{result.stderr.strip()}")


def compile_to_pptx(
    presentation: Presentation,
    output_path: str,
    reference_doc: Optional[str] = None,
    timeout: int = 60,
) -> None:
    """Compile presentation to a PowerPoint (.pptx) file."""
    if shutil.which(PANDOC) is None:
        raise CompileError("pandoc is not installed or not on PATH")
    if reference_doc and not os.path.isfile(reference_doc):
        raise CompileError(f"reference_doc not found: {reference_doc!r}")

    # build_config_yaml always emits Beamer-specific keys (theme, header-includes)
    # regardless of target; pandoc simply ignores the ones it doesn't use for PPTX.
    config_path, slides_path = _write_sources(presentation)
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
    except subprocess.TimeoutExpired:
        raise CompileError(f"pandoc timed out after {timeout}s building PPTX")
    if result.returncode != 0:
        raise CompileError(f"pandoc failed building PPTX:\n{result.stderr.strip()}")
