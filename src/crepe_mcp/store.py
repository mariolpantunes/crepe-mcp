"""In-memory state for presentations being built by the CREPE MCP server.

Everything lives in the module-level PRESENTATIONS dict, keyed by
presentation_id. There is no persistence: state is lost on server restart,
and each presentation gets its own scratch directory on disk for pandoc
inputs/outputs.

Slides hold a title and their full Pandoc Markdown body (including any
fenced-div column syntax). Columns are NOT modelled as a separate concept —
callers embed pandoc column markup directly in the slide content string.
"""
from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Slide:
    id: str
    title: str
    # Full Pandoc Markdown body (bullets, code, math, images, speaker notes,
    # fenced-div column blocks — all raw Markdown).
    content: str = ""


@dataclass
class Metadata:
    title: str = ""
    subtitle: str = ""
    author: str = "Mário Antunes"
    institute: str = "Universidade de Aveiro"
    date: str = "2026"


@dataclass
class Presentation:
    id: str
    workdir: str
    metadata: Metadata = field(default_factory=Metadata)
    slides: List[Slide] = field(default_factory=list)
    # format -> absolute path on disk (populated after compile_presentation)
    artifacts: Dict[str, str] = field(default_factory=dict)


PRESENTATIONS: Dict[str, Presentation] = {}


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def new_presentation(
    title: str = "",
    subtitle: str = "",
    author: str = "Mário Antunes",
    institute: str = "Universidade de Aveiro",
    date: str = "2026",
) -> Presentation:
    presentation_id = uuid.uuid4().hex[:8]
    workdir = tempfile.mkdtemp(prefix=f"crepe_{presentation_id}_")
    metadata = Metadata(
        title=title, subtitle=subtitle,
        author=author, institute=institute, date=date,
    )
    presentation = Presentation(id=presentation_id, workdir=workdir, metadata=metadata)
    PRESENTATIONS[presentation_id] = presentation
    return presentation


def get_presentation(presentation_id: str) -> Presentation:
    presentation = PRESENTATIONS.get(presentation_id)
    if presentation is None:
        raise ValueError(f"Unknown presentation_id: {presentation_id!r}")
    return presentation


# ---------------------------------------------------------------------------
# Slide helpers
# ---------------------------------------------------------------------------

def upsert_slide(
    presentation: Presentation,
    index: int,
    title: str,
    content: str,
) -> tuple[Slide, str]:
    """Insert or replace a slide at *index*.

    * index < len(slides)  → replace in-place (id preserved).
    * index >= len(slides) → append.

    Returns (slide, action) where action is 'replaced' or 'appended'.
    """
    slide = Slide(id=uuid.uuid4().hex[:8], title=title, content=content)
    if index < len(presentation.slides):
        slide.id = presentation.slides[index].id
        presentation.slides[index] = slide
        return slide, "replaced"
    presentation.slides.append(slide)
    return slide, "appended"


def get_slide_by_index(presentation: Presentation, index: int) -> Slide:
    if index < 0 or index >= len(presentation.slides):
        raise IndexError(
            f"Slide index {index} out of range "
            f"(presentation has {len(presentation.slides)} slides)"
        )
    return presentation.slides[index]
