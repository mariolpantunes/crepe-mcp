"""Turns in-memory Presentation state into the two pandoc inputs:
  - config.yml  (passed as --metadata-file)
  - slides.md   (the document body, one ## block per slide)
"""
from __future__ import annotations

import yaml

from crepe_mcp.store import Presentation, Slide

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

# Default Beamer theme options (PDF output only).
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
        "themeoptions": DEFAULT_THEME_OPTIONS,
        "highlight-style": highlight_style,
        "colorlinks": True,
        "mainfont": "Noto Sans",
        "sansfont": "Noto Sans",
        "monofont": "Noto Sans Mono",
        "header-includes": HEADER_INCLUDES,
    }
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def _render_slide(slide: Slide) -> str:
    return f"## {slide.title}\n\n{slide.content.strip()}"


def build_slides_markdown(presentation: Presentation) -> str:
    """Assemble every slide in insertion order into one Markdown document."""
    return "\n\n".join(_render_slide(s) for s in presentation.slides) + "\n"
