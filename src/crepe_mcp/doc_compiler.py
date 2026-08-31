"""Compiles document projects to A4 PDF or DOCX using Pandoc."""
from __future__ import annotations

import os
import shutil
import subprocess

from crepe_mcp.doc_store import Document, build_doc_config_yaml, build_document_markdown

PANDOC = "pandoc"


class DocCompileError(RuntimeError):
    pass


__all__ = [
    "DocCompileError",
    "build_doc_config_yaml",
    "build_document_markdown",
    "compile_document_to_docx",
    "compile_document_to_pdf",
]


def compile_document_to_pdf(
    document: Document,
    output_path: str,
    timeout: int = 120,
) -> None:
    """Compile a Document to an A4 PDF using Pandoc and lualatex."""
    if not os.path.isabs(output_path):
        raise DocCompileError(f"output_path must be an absolute path, got {output_path!r}")
    if shutil.which(PANDOC) is None:
        raise DocCompileError("pandoc is not installed or not on PATH")
    if shutil.which("lualatex") is None:
        raise DocCompileError("lualatex is not installed or not on PATH (required for PDF document output)")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    doc_md_path = os.path.join(document.workdir, "document.md")
    with document.lock:
        markdown = build_document_markdown(document)
        toc = document.metadata.toc
        number_sections = document.metadata.number_sections
    with open(doc_md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    cmd = [
        PANDOC,
        "-s",
        "--pdf-engine=lualatex",
        doc_md_path,
        "-o", output_path,
    ]
    if toc:
        cmd.append("--toc")
    if number_sections:
        cmd.append("--number-sections")

    try:
        res = subprocess.run(
            cmd, cwd=document.workdir,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocCompileError(f"pandoc timed out after {timeout}s compiling PDF document") from exc


    if res.returncode != 0:
        raise DocCompileError(f"pandoc failed compiling PDF document:\n{res.stderr.strip()}")


def compile_document_to_docx(
    document: Document,
    output_path: str,
    reference_doc: str | None = None,
    timeout: int = 60,
) -> None:
    """Compile a Document to a Word .docx file using Pandoc and optional reference template doc."""
    if not os.path.isabs(output_path):
        raise DocCompileError(f"output_path must be an absolute path, got {output_path!r}")
    if shutil.which(PANDOC) is None:
        raise DocCompileError("pandoc is not installed or not on PATH")
    if reference_doc and not os.path.isfile(reference_doc):
        raise DocCompileError(f"reference_doc not found: {reference_doc!r}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    doc_md_path = os.path.join(document.workdir, "document.md")
    with document.lock:
        markdown = build_document_markdown(document)
        toc = document.metadata.toc
        number_sections = document.metadata.number_sections
    with open(doc_md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    cmd = [
        PANDOC,
        "-s",
        doc_md_path,
        "-o", output_path,
    ]
    if toc:
        cmd.append("--toc")
    if number_sections:
        cmd.append("--number-sections")
    if reference_doc:
        cmd.append(f"--reference-doc={reference_doc}")

    try:
        res = subprocess.run(
            cmd, cwd=document.workdir,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocCompileError(f"pandoc timed out after {timeout}s compiling DOCX document") from exc


    if res.returncode != 0:
        raise DocCompileError(f"pandoc failed compiling DOCX document:\n{res.stderr.strip()}")
