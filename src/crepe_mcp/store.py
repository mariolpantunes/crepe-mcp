"""In-memory state for presentations being built by the CREPE MCP server.

Everything lives in the module-level PRESENTATIONS dict, keyed by
presentation_id. There is no persistence: state is lost on server restart,
and each presentation gets its own scratch directory on disk for pandoc
inputs/outputs.

Slides hold a title and their full Pandoc Markdown body (including any
fenced-div column syntax). Columns are NOT modelled as a separate concept —
callers embed pandoc column markup directly in the slide content string.

Concurrency: an MCP host can dispatch multiple tool calls from a single
agent turn concurrently (confirmed empirically -- batched set_slide calls
against the same presentation raced and silently dropped a slide). Every
function that reads-then-mutates a Presentation's slides/metadata holds
that presentation's own lock; new_presentation/delete_presentation/
list_presentations hold _REGISTRY_LOCK, which guards the PRESENTATIONS
dict itself.

A plain threading.Lock stops data corruption but not surprising ordering:
whichever of several waiting threads the OS scheduler wakes up first gets
to go first, which need not match the order the calls actually arrived in.
Presentation.lock is a _TicketLock instead, so waiters are served strictly
in arrival order -- callers that batch several positional set_slide calls
in one turn get a well-defined execution order to reason about.

That alone is still not sufficient: the reordering that matters happens
before any of this module's code runs at all (in OS thread scheduling,
before a thread's first bytecode instruction), so even perfectly fair
in-store ordering can't undo it -- measured empirically at ~14% of trials
still landing a slide at an unintended position under realistic concurrent
load. expected_slide_count (on upsert_slide/insert_slide/delete_slide) is
the actual fix: an optional optimistic-concurrency check, made atomically
under the same lock as the mutation, that fails loudly with a clear error
the moment a call's assumption about the current slide count is stale --
instead of silently landing somewhere the caller didn't intend.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field

from crepe_mcp._locks import TicketLock


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
    slides: list[Slide] = field(default_factory=list)
    # format -> absolute path on disk (populated after compile_presentation)
    artifacts: dict[str, str] = field(default_factory=dict)
    lock: TicketLock = field(default_factory=TicketLock, repr=False, compare=False)


PRESENTATIONS: dict[str, Presentation] = {}
_REGISTRY_LOCK = threading.Lock()


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
    with _REGISTRY_LOCK:
        PRESENTATIONS[presentation_id] = presentation
    return presentation


def get_presentation(presentation_id: str) -> Presentation:
    with _REGISTRY_LOCK:
        presentation = PRESENTATIONS.get(presentation_id)
    if presentation is None:
        raise ValueError(f"Unknown presentation_id: {presentation_id!r}")
    return presentation


def delete_presentation(presentation_id: str) -> None:
    """Drop a presentation from memory and remove its on-disk workdir."""
    with _REGISTRY_LOCK:
        presentation = PRESENTATIONS.pop(presentation_id, None)
    if presentation is None:
        raise ValueError(f"Unknown presentation_id: {presentation_id!r}")
    shutil.rmtree(presentation.workdir, ignore_errors=True)


def _cleanup_all_workdirs() -> None:
    with _REGISTRY_LOCK:
        workdirs = [p.workdir for p in PRESENTATIONS.values()]
    for d in workdirs:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_all_workdirs)


# ---------------------------------------------------------------------------
# Slide helpers
# ---------------------------------------------------------------------------

def _check_expected_count(presentation: Presentation, expected_slide_count: int | None) -> None:
    """Raise ValueError if expected_slide_count is given and stale.

    Must be called while already holding presentation.lock, so the check
    and the mutation that follows it are atomic together -- otherwise a
    second call could change the count in the gap between the check and
    the actual mutation, defeating the whole point.
    """
    actual = len(presentation.slides)
    if expected_slide_count is not None and expected_slide_count != actual:
        raise ValueError(
            f"expected_slide_count={expected_slide_count} but the presentation "
            f"actually has {actual} slide(s) now -- a concurrent call already "
            "changed it. Call get_presentation to see the current state, then retry."
        )


def upsert_slide(
    presentation: Presentation,
    index: int,
    title: str,
    content: str,
    expected_slide_count: int | None = None,
) -> tuple[Slide, str, int, list[str]]:
    """Insert or replace a slide at *index*.

    * index < len(slides)  → replace in-place (id preserved).
    * index >= len(slides) → append.

    expected_slide_count, if given, must match the current count or this
    raises ValueError instead of guessing (see _check_expected_count).

    Returns (slide, action, actual_index, warnings) where action is 'replaced' or
    'appended'. actual_index is computed under the same lock as the
    mutation -- looking it up afterwards via slides.index(slide) would
    itself be a race if another call mutates the list in between.
    """
    if index < 0:
        raise ValueError(f"Slide index must be >= 0, got {index}")
    with presentation.lock:
        _check_expected_count(presentation, expected_slide_count)
        if index < len(presentation.slides):
            # Preserve the existing slide's id so the caller can detect a replace.
            slide = Slide(
                id=presentation.slides[index].id,
                title=title,
                content=content,
            )
            presentation.slides[index] = slide
            return slide, "replaced", index, []
        slide = Slide(id=uuid.uuid4().hex[:8], title=title, content=content)
        presentation.slides.append(slide)
        return slide, "appended", len(presentation.slides) - 1, []



def get_slide_by_index(presentation: Presentation, index: int) -> Slide:
    with presentation.lock:
        if index < 0 or index >= len(presentation.slides):
            raise IndexError(
                f"Slide index {index} out of range "
                f"(presentation has {len(presentation.slides)} slides)"
            )
        return presentation.slides[index]


def delete_slide(
    presentation: Presentation,
    index: int,
    expected_slide_count: int | None = None,
) -> Slide:
    """Remove and return the slide at *index*; later slides shift down.

    expected_slide_count, if given, must match the current count or this
    raises ValueError instead of guessing (see _check_expected_count).
    """
    with presentation.lock:
        _check_expected_count(presentation, expected_slide_count)
        if index < 0 or index >= len(presentation.slides):
            raise IndexError(
                f"Slide index {index} out of range "
                f"(presentation has {len(presentation.slides)} slides)"
            )
        return presentation.slides.pop(index)


def insert_slide(
    presentation: Presentation,
    index: int,
    title: str,
    content: str,
    expected_slide_count: int | None = None,
) -> tuple[Slide, int, list[str]]:
    """Insert a new slide at *index*, shifting slides at/after it later.

    index >= len(slides) inserts at the end, consistent with
    upsert_slide's append-on-overflow behavior. expected_slide_count, if
    given, must match the current count or this raises ValueError instead
    of guessing (see _check_expected_count).

    Returns (slide, actual_index, warnings), computed under the same lock as the
    mutation for the same reason upsert_slide does.
    """
    if index < 0:
        raise ValueError(f"Slide index must be >= 0, got {index}")
    with presentation.lock:
        _check_expected_count(presentation, expected_slide_count)
        actual_index = min(index, len(presentation.slides))
        slide = Slide(id=uuid.uuid4().hex[:8], title=title, content=content)
        presentation.slides.insert(actual_index, slide)
    return slide, actual_index, []


def replace_all_slides(
    presentation: Presentation,
    parsed: list[tuple[str, str]],
) -> int:
    """Atomically replace every slide in *presentation* with *parsed* slides.

    Builds the new Slide objects outside the lock (pure construction, no shared
    state), then holds the lock for a single clear()+extend() — one atomic
    operation with no intermediate empty-list state visible to other threads.

    parsed : list of (title, content) pairs from parse_slides_markdown.
    Returns the new slide count, read inside the lock so callers get an
    accurate value without a second lock acquisition.
    """
    new_slides = [
        Slide(id=uuid.uuid4().hex[:8], title=title, content=content)
        for title, content in parsed
    ]
    with presentation.lock:
        presentation.slides.clear()
        presentation.slides.extend(new_slides)
        return len(presentation.slides)


def list_presentations() -> list[Presentation]:
    with _REGISTRY_LOCK:
        return list(PRESENTATIONS.values())


def update_metadata(presentation: Presentation, **fields: str | None) -> Metadata:
    """Update only the metadata fields passed with a non-None value."""
    with presentation.lock:
        for key, value in fields.items():
            if value is not None and hasattr(presentation.metadata, key):
                setattr(presentation.metadata, key, value)
        return presentation.metadata


def move_slide(
    presentation: Presentation,
    from_index: int,
    to_index: int,
    expected_slide_count: int | None = None,
) -> tuple[Slide, int]:
    """Move a slide from from_index to to_index atomically under presentation lock.

    Returns (slide, final_to_index).
    """
    with presentation.lock:
        _check_expected_count(presentation, expected_slide_count)
        num_slides = len(presentation.slides)
        if from_index < 0 or from_index >= num_slides:
            raise IndexError(f"from_index {from_index} out of range (slide count: {num_slides})")
        if to_index < 0:
            raise ValueError(f"to_index must be >= 0, got {to_index}")

        slide = presentation.slides.pop(from_index)
        target_index = min(to_index, len(presentation.slides))
        presentation.slides.insert(target_index, slide)
        return slide, target_index


def duplicate_presentation(presentation_id: str, title_suffix: str = " (Copy)") -> Presentation:
    """Clone an existing presentation into a new presentation instance with its own workdir.

    Acquires src.lock only to snapshot metadata and slides; releases it before
    calling new_presentation (which acquires _REGISTRY_LOCK internally). This
    avoids establishing a pres.lock → _REGISTRY_LOCK ordering that could
    deadlock if any future code path takes them in the opposite order.
    """
    src = get_presentation(presentation_id)
    with src.lock:
        meta_dict = vars(src.metadata).copy()
        meta_dict["title"] = meta_dict.get("title", "") + title_suffix
        slide_snapshot = [(s.title, s.content) for s in src.slides]
    # Create new presentation *outside* src.lock — new_presentation acquires
    # _REGISTRY_LOCK internally and must not do so while we hold src.lock.
    new_pres = new_presentation(**meta_dict)
    with new_pres.lock:
        for title, content in slide_snapshot:
            new_pres.slides.append(Slide(id=uuid.uuid4().hex[:8], title=title, content=content))
    return new_pres
