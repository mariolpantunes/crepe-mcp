"""Configuration for the reader: scratch location, packaged layout factors and their overrides."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

PACKAGED_CONFIG = Path(__file__).with_name("config.json")


def scratch_base() -> Path:
    """Base directory for per-document extraction stores.

    Read from CREPE_SCRATCH_BASE on every call; defaults to <system temp>/crepe-reader.
    """
    return Path(os.environ.get("CREPE_SCRATCH_BASE") or Path(tempfile.gettempdir()) / "crepe-reader")


def load_section(section: str) -> dict[str, Any]:
    """Values of one section of the packaged config.json, overridden by the CREPE_READER_CONFIG file when set."""
    entries: dict[str, Any] = json.loads(PACKAGED_CONFIG.read_text(encoding="utf-8"))[section]
    values = {name: entry["value"] for name, entry in entries.items()}
    override_path = os.environ.get("CREPE_READER_CONFIG")
    if override_path:
        overrides: dict[str, Any] = json.loads(Path(override_path).read_text(encoding="utf-8")).get(section, {})
        unknown = sorted(set(overrides) - set(values))
        if unknown:
            raise ValueError(f"{override_path}: unknown {section} settings {unknown}; known: {sorted(values)}")
        for name, entry in overrides.items():
            values[name] = entry["value"] if isinstance(entry, dict) else entry
    return values
