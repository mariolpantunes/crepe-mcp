"""In-memory state for structured A4 documents (reports, articles, papers).

Documents hold metadata (paper_size, margins, font_size, toc, etc.) and a hierarchical
tree of Chapters and Sections.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import yaml

from crepe_mcp._locks import TicketLock


@dataclass
class Section:
    id: str
    title: str
    content: str = ""
    level: int = 2  # 2 for ## Section, 3 for ### Subsection


@dataclass
class Chapter:
    id: str
    title: str
    intro: str = ""
    sections: list[Section] = field(default_factory=list)


@dataclass
class DocumentMetadata:
    title: str = "Untitled Document"
    subtitle: str = ""
    author: str = "Mário Antunes"
    institute: str = "Universidade de Aveiro"
    date: str = "2026"
    abstract: str = ""
    paper_size: str = "a4paper"
    margin: str = "2.5cm"
    font_size: str = "11pt"
    toc: bool = True
    number_sections: bool = True


@dataclass
class Document:
    id: str
    workdir: str
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    chapters: list[Chapter] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    lock: TicketLock = field(default_factory=TicketLock, repr=False, compare=False)


DOCUMENTS: dict[str, Document] = {}
_DOC_REGISTRY_LOCK = threading.Lock()


def new_document(
    title: str = "Untitled Document",
    subtitle: str = "",
    author: str = "Mário Antunes",
    institute: str = "Universidade de Aveiro",
    date: str = "2026",
    abstract: str = "",
    paper_size: str = "a4paper",
    margin: str = "2.5cm",
    font_size: str = "11pt",
    toc: bool = True,
    number_sections: bool = True,
) -> Document:
    doc_id = uuid.uuid4().hex[:8]
    workdir = tempfile.mkdtemp(prefix=f"crepe_doc_{doc_id}_")
    metadata = DocumentMetadata(
        title=title, subtitle=subtitle, author=author,
        institute=institute, date=date, abstract=abstract,
        paper_size=paper_size, margin=margin, font_size=font_size,
        toc=toc, number_sections=number_sections,
    )
    doc = Document(id=doc_id, workdir=workdir, metadata=metadata)
    with _DOC_REGISTRY_LOCK:
        DOCUMENTS[doc_id] = doc
    return doc


def get_document(doc_id: str) -> Document:
    with _DOC_REGISTRY_LOCK:
        doc = DOCUMENTS.get(doc_id)
    if doc is None:
        raise ValueError(f"Unknown document_id: {doc_id!r}")
    return doc


def delete_document(doc_id: str) -> None:
    with _DOC_REGISTRY_LOCK:
        doc = DOCUMENTS.pop(doc_id, None)
    if doc is None:
        raise ValueError(f"Unknown document_id: {doc_id!r}")
    shutil.rmtree(doc.workdir, ignore_errors=True)


def list_documents() -> list[Document]:
    with _DOC_REGISTRY_LOCK:
        return list(DOCUMENTS.values())


def _cleanup_all_doc_workdirs() -> None:
    with _DOC_REGISTRY_LOCK:
        dirs = [d.workdir for d in DOCUMENTS.values()]
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_all_doc_workdirs)


# ---------------------------------------------------------------------------
# Chapter and Section manipulation
# ---------------------------------------------------------------------------

def set_chapter(
    doc: Document,
    chapter_index: int,
    title: str,
    intro: str = "",
) -> tuple[Chapter, str, int, list[str]]:
    """Add or update a top-level chapter (# Chapter Title)."""
    if chapter_index < 0:
        raise ValueError(f"chapter_index must be >= 0, got {chapter_index}")
    with doc.lock:
        if chapter_index < len(doc.chapters):
            ch = doc.chapters[chapter_index]
            ch.title = title
            ch.intro = intro
            return ch, "replaced", chapter_index, []
        else:
            ch = Chapter(id=uuid.uuid4().hex[:8], title=title, intro=intro)
            doc.chapters.append(ch)
            return ch, "appended", len(doc.chapters) - 1, []


def set_section(
    doc: Document,
    chapter_index: int,
    section_index: int,
    title: str,
    content: str,
    level: int = 2,
) -> tuple[Section, str, int, list[str]]:
    """Add or update a section within a chapter."""
    if level not in (2, 3):
        raise ValueError(f"level must be 2 or 3, got {level}")
    if section_index < 0:
        raise ValueError(f"section_index must be >= 0, got {section_index}")
    with doc.lock:
        if chapter_index < 0 or chapter_index >= len(doc.chapters):
            raise IndexError(f"chapter_index {chapter_index} out of range (chapters: {len(doc.chapters)})")
        ch = doc.chapters[chapter_index]

        if section_index < len(ch.sections):
            sec = ch.sections[section_index]
            sec.title = title
            sec.content = content
            sec.level = level
            return sec, "replaced", section_index, []
        else:
            sec = Section(id=uuid.uuid4().hex[:8], title=title, content=content, level=level)
            ch.sections.append(sec)
            return sec, "appended", len(ch.sections) - 1, []


def delete_chapter(doc: Document, chapter_index: int) -> Chapter:
    with doc.lock:
        if chapter_index < 0 or chapter_index >= len(doc.chapters):
            raise IndexError(f"chapter_index {chapter_index} out of range (chapters: {len(doc.chapters)})")
        return doc.chapters.pop(chapter_index)


def update_document_metadata(doc: Document, **fields: Any) -> DocumentMetadata:
    with doc.lock:
        for k, v in fields.items():
            if v is not None and hasattr(doc.metadata, k):
                setattr(doc.metadata, k, v)
        return doc.metadata


def build_doc_config_yaml(doc: Document) -> str:
    """Build YAML configuration string for a document's metadata."""
    front: dict = {"title": doc.metadata.title}
    if doc.metadata.subtitle:
        front["subtitle"] = doc.metadata.subtitle
    front["author"] = doc.metadata.author
    if doc.metadata.institute:
        front["institute"] = doc.metadata.institute
    front["date"] = str(doc.metadata.date)
    if doc.metadata.abstract:
        front["abstract"] = doc.metadata.abstract
    front["papersize"] = doc.metadata.paper_size
    margin_val = doc.metadata.margin
    front["geometry"] = margin_val if "margin=" in margin_val else f"margin={margin_val}"
    front["fontsize"] = doc.metadata.font_size
    if doc.metadata.toc:
        front["toc"] = True
    if doc.metadata.number_sections:
        front["numbersections"] = True

    return yaml.safe_dump(
        front, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()


def build_document_markdown(doc: Document) -> str:
    """Assemble all chapters and sections into a single Markdown document string."""
    parts: list[str] = []

    # YAML front-matter — use yaml.safe_dump to handle special characters
    # (colons, quotes, backslashes, Unicode) that Python repr() misquotes.
    yaml_text = build_doc_config_yaml(doc)
    parts.append(f"---\n{yaml_text}\n---\n")

    for ch in doc.chapters:
        parts.append(f"# {ch.title}")
        parts.append("")
        if ch.intro.strip():
            parts.append(ch.intro.strip())
            parts.append("")
        for sec in ch.sections:
            hashes = "#" * max(2, min(sec.level, 6))
            parts.append(f"{hashes} {sec.title}")
            parts.append("")
            if sec.content.strip():
                parts.append(sec.content.strip())
                parts.append("")

    return "\n".join(parts)
