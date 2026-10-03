"""Layout heuristics, relative to each document's own measurements.

Lengths are multiples of the document's body font size (``em``) or body line height, both measured per
document by ``document.measure``; nothing is expressed in absolute points or page fractions, so the same
rules apply to 8 pt and 12 pt papers on A4 or Letter.

The factors are read from ``config.json`` next to this module (each with its unit and rationale). A file
named by the ``CREPE_READER_CONFIG`` environment variable, with the same structure, overrides individual
values; unknown names are rejected. The effective values are part of every paper store's cache key, so
changing them rebuilds the stores. They are typographic conventions, not values fitted to one paper, but
they are still provisional: see "Heuristic factors" in TODO.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from crepe_mcp.reader.config import load_section


def load_heuristics() -> dict[str, float]:
    """Packaged heuristic factors, overridden by the CREPE_READER_CONFIG file when set."""
    return {name: float(value) for name, value in load_section("heuristics").items()}


VALUES = load_heuristics()
DIGEST = hashlib.sha256(json.dumps(VALUES, sort_keys=True).encode()).hexdigest()[:16]

SAME_SIZE = VALUES["same_size"]
SAME_ROW = VALUES["same_row"]
COLUMN_OVERLAP = VALUES["column_overlap"]
RUNNING_PAGE_DISTANCE = int(VALUES["running_page_distance"])
RUNNING_MIN_PAGES = int(VALUES["running_min_pages"])
RUNNING_SHIFT = VALUES["running_shift"]
LINE_NUMBER_SEQUENCE = int(VALUES["line_number_sequence"])
NUMBERING_RUN = int(VALUES["numbering_run"])
INDENT = VALUES["indent"]
PARAGRAPH_SPACE = VALUES["paragraph_space"]
CAPTION_REGION = VALUES["caption_region"]
FIGURE_MIN_HEIGHT = VALUES["figure_min_height"]
FIGURE_MIN_WIDTH = VALUES["figure_min_width"]
RULE_MAX_THICKNESS = VALUES["rule_max_thickness"]
RULE_MIN_LENGTH = VALUES["rule_min_length"]
RULE_ALIGNMENT = VALUES["rule_alignment"]


@dataclass(frozen=True)
class Metrics:
    """Typographic measurements of one document."""

    body_font: str
    body_size: float
    line_height: float
    line_gap: float = 0.0

    def same_size(self, a: float, b: float) -> bool:
        return abs(a - b) <= SAME_SIZE * self.body_size

    def smaller(self, size: float) -> bool:
        return size < self.body_size and not self.same_size(size, self.body_size)

    def larger(self, size: float) -> bool:
        return size > self.body_size and not self.same_size(size, self.body_size)
