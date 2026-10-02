"""Secrets and settings loaded from a `.env` file instead of client configs.

Lookup order (first existing file wins): `$CREPE_ENV_FILE`, `~/.config/crepe-mcp/.env`, `./.env`.
Variables already present in the process environment are never overridden.
Values are never logged or returned.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

ENV_FILE_VAR = "CREPE_ENV_FILE"


def candidate_files() -> list[Path]:
    """Return the `.env` locations in lookup order."""
    explicit = os.environ.get(ENV_FILE_VAR, "").strip()
    files = [Path(explicit).expanduser()] if explicit else []
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", "") or Path.home() / ".config")
    files += [config_home / "crepe-mcp" / ".env", Path.cwd() / ".env"]
    return files


def parse_env(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` lines: `#` comments, optional `export `, optional single or double quotes."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key.strip():
            values[key.strip()] = value
    return values


def load_env() -> Path | None:
    """Load the first existing `.env` into `os.environ` without overriding; return the file used."""
    for path in candidate_files():
        if not path.is_file():
            continue
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"crepe-mcp: {path} is readable by others; run chmod 600", file=sys.stderr)
        for key, value in parse_env(path.read_text(encoding="utf-8")).items():
            os.environ.setdefault(key, value)
        return path
    return None
