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
from typing import Dict, List


class _TicketLock:
    """A mutex that grants entry in strict arrival (FIFO) order.

    threading.Lock makes no fairness guarantee -- under contention, the OS
    scheduler picks which waiter wakes next, which can and does reorder
    logically-sequential operations. This hands out a ticket number on
    entry and only proceeds once it's that ticket's turn, so N threads
    calling __enter__ in some order T0, T1, ..., Tn-1 (wall-clock arrival)
    are guaranteed to execute in that same order.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._next_ticket = 0
        self._now_serving = 0

    def __enter__(self) -> "_TicketLock":
        self._lock.acquire()
        ticket = self._next_ticket
        self._next_ticket += 1
        while self._now_serving != ticket:
            self._cond.wait()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._now_serving += 1
        self._cond.notify_all()
        self._lock.release()


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
    lock: "_TicketLock" = field(default_factory=_TicketLock, repr=False, compare=False)


PRESENTATIONS: Dict[str, Presentation] = {}
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
    for presentation in PRESENTATIONS.values():
        shutil.rmtree(presentation.workdir, ignore_errors=True)


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
) -> tuple[Slide, str, int]:
    """Insert or replace a slide at *index*.

    * index < len(slides)  → replace in-place (id preserved).
    * index >= len(slides) → append.

    expected_slide_count, if given, must match the current count or this
    raises ValueError instead of guessing (see _check_expected_count).

    Returns (slide, action, actual_index) where action is 'replaced' or
    'appended'. actual_index is computed under the same lock as the
    mutation -- looking it up afterwards via slides.index(slide) would
    itself be a race if another call mutates the list in between.
    """
    if index < 0:
        raise ValueError(f"Slide index must be >= 0, got {index}")
    slide = Slide(id=uuid.uuid4().hex[:8], title=title, content=content)
    with presentation.lock:
        _check_expected_count(presentation, expected_slide_count)
        if index < len(presentation.slides):
            slide.id = presentation.slides[index].id
            presentation.slides[index] = slide
            return slide, "replaced", index
        presentation.slides.append(slide)
        return slide, "appended", len(presentation.slides) - 1


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
) -> tuple[Slide, int]:
    """Insert a new slide at *index*, shifting slides at/after it later.

    index >= len(slides) inserts at the end, consistent with
    upsert_slide's append-on-overflow behavior. expected_slide_count, if
    given, must match the current count or this raises ValueError instead
    of guessing (see _check_expected_count).

    Returns (slide, actual_index), computed under the same lock as the
    mutation for the same reason upsert_slide does.
    """
    if index < 0:
        raise ValueError(f"Slide index must be >= 0, got {index}")
    slide = Slide(id=uuid.uuid4().hex[:8], title=title, content=content)
    with presentation.lock:
        _check_expected_count(presentation, expected_slide_count)
        actual_index = min(index, len(presentation.slides))
        presentation.slides.insert(actual_index, slide)
    return slide, actual_index


def list_presentations() -> List[Presentation]:
    with _REGISTRY_LOCK:
        return list(PRESENTATIONS.values())


def update_metadata(presentation: Presentation, **fields: str | None) -> Metadata:
    """Update only the metadata fields passed with a non-None value."""
    with presentation.lock:
        for key, value in fields.items():
            if value is not None:
                setattr(presentation.metadata, key, value)
        return presentation.metadata
