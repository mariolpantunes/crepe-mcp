"""Turns in-memory Presentation state into the two pandoc inputs:
  - config.yml  (passed as --metadata-file)
  - slides.md   (the document body, one ## block per slide)
"""
from __future__ import annotations

import re
from typing import Optional

import yaml

from crepe_mcp.store import Presentation, Slide

# A slide using the documented "Section divider" syntax: content is a single
# top-level (#) heading and nothing else.
_SECTION_DIVIDER_RE = re.compile(r"^#\s+\S.*$")
# Any top-level (#) heading anywhere in the built markdown, but not a ## (or
# deeper) slide-frame heading.
_TOP_LEVEL_HEADING_RE = re.compile(r"^#\s+\S", re.MULTILINE)
# A slide/section heading line: '#' or '##' followed by a space (not '###+').
_HEADING_RE = re.compile(r"^(#{1,2})\s+(.*)$")
# A fenced code block delimiter (``` or ~~~, 3+ chars, optional info string).
_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")

# LaTeX preamble included in every Beamer/lualatex deck.
HEADER_INCLUDES = [
    r"\usepackage{booktabs}",
    r"\usepackage{etoolbox}",
    r"\usepackage{caption}",
    r"\usepackage{amsmath}",
    r"\usepackage{amssymb}",
    r"\usepackage{tikz}",
    r"\usetikzlibrary{positioning,shapes,arrows,calc,fit}",
    r"\usepackage{fancyvrb}",
    r"\captionsetup[figure]{labelformat=empty}",
    r"\AtBeginEnvironment{longtable}{\scriptsize}",
    r"\AtBeginEnvironment{verbatim}{\scriptsize}",
    r"\AtBeginEnvironment{Highlighting}{\scriptsize}",
]

# Beamer theme options (PDF output only). These are Metropolis-family
# features (moloch is a Metropolis fork) -- unconditionally passing them to
# a classic theme (e.g. 'default', 'Warsaw') raises a LaTeX "Option clash"
# error, so they're only applied for themes that actually support them.
METROPOLIS_FAMILY_THEMES = {"moloch", "metropolis"}
DEFAULT_THEME_OPTIONS = [
    "sectionpage=progressbar",
    "numbering=fraction",
    "progressbar=frametitle",
]


def build_config_yaml(
    presentation: Presentation,
    theme: str = "moloch",
    highlight_style: str = "tango",
) -> str:
    """Build the YAML metadata block passed to pandoc via --metadata-file."""
    meta = presentation.metadata
    config = {
        "title": meta.title,
        "subtitle": meta.subtitle,
        "author": meta.author,
        "institute": meta.institute,
        "date": meta.date,
        "theme": theme,
        "themeoptions": DEFAULT_THEME_OPTIONS if theme in METROPOLIS_FAMILY_THEMES else [],
        "highlight-style": highlight_style,
        "colorlinks": True,
        "mainfont": "Noto Sans",
        "sansfont": "Noto Sans",
        "monofont": "Noto Sans Mono",
        "header-includes": HEADER_INCLUDES,
    }
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def _render_slide(slide: Slide) -> str:
    content = slide.content.strip()
    if "\n" not in content and _SECTION_DIVIDER_RE.match(content):
        # Section-divider slide (README syntax: "# Section Title" as the
        # only content). Emit the bare heading with no enclosing "## " frame:
        # wrapping it in an empty frame produces a redundant blank page
        # alongside the Metropolis-family theme's own auto section frame
        # (moloch/metropolis's sectionpage=progressbar already renders one).
        return content
    return f"## {slide.title}\n\n{content}"


def build_slides_markdown(presentation: Presentation) -> str:
    """Assemble every slide in insertion order into one Markdown document."""
    return "\n\n".join(_render_slide(s) for s in presentation.slides) + "\n"


def has_sections(markdown: str) -> bool:
    """True if the built markdown defines any top-level (#) section heading.

    Used to decide whether to pass --toc to pandoc: with zero sections,
    Beamer's \\tableofcontents renders a blank frame with nothing to list.
    """
    return bool(_TOP_LEVEL_HEADING_RE.search(markdown))


def _fence_run(line: str) -> Optional[tuple[str, int]]:
    """(char, length) if the stripped line opens a fence (``` or ~~~), else None."""
    match = _FENCE_OPEN_RE.match(line.strip())
    if not match:
        return None
    run = match.group(1)
    return run[0], len(run)


def _is_fence_close(line: str, fence_char: str, fence_len: int) -> bool:
    stripped = line.strip()
    return len(stripped) >= fence_len and set(stripped) == {fence_char}


def parse_slides_markdown(markdown: str) -> list[tuple[str, str]]:
    """Split pandoc slide Markdown into (title, content) pairs -- the inverse
    of build_slides_markdown(). Splits on '##' (slide) and bare '#'
    (section-divider) headings, ignoring '#' characters inside fenced code
    blocks (``` or ~~~) so a comment like '# TODO' in a code sample is never
    mistaken for a heading.

    A bare '# Section' line round-trips to ('', '# Section'), matching what
    set_slide(title='', content='# Section') / _render_slide() produce.

    Raises ValueError if there's no heading at all, or content appears before
    the first heading (nothing to attach it to).
    """
    fence: Optional[tuple[str, int]] = None
    blocks: list[tuple[str, str, list[str]]] = []
    preamble: list[str] = []

    for line in markdown.splitlines():
        if fence is not None:
            if _is_fence_close(line, *fence):
                fence = None
            (blocks[-1][2] if blocks else preamble).append(line)
            continue

        run = _fence_run(line)
        if run is not None:
            fence = run
            (blocks[-1][2] if blocks else preamble).append(line)
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            blocks.append((heading_match.group(1), heading_match.group(2).strip(), []))
            continue

        (blocks[-1][2] if blocks else preamble).append(line)

    if any(line.strip() for line in preamble):
        raise ValueError(
            "Markdown has content before the first '#'/'##' heading -- "
            "every slide must start with a heading line."
        )
    if not blocks:
        raise ValueError("No '#' or '##' headings found in the given Markdown -- nothing to import.")

    slides: list[tuple[str, str]] = []
    for hashes, title, body_lines in blocks:
        if len(hashes) == 1:
            slides.append(("", f"# {title}"))
        else:
            slides.append((title, "\n".join(body_lines).strip()))
    return slides
