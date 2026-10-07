"""Load API keys from a dotenv file so secrets never live in agent host configs.

The file is CREPE_ENV_FILE (setup.py registers it with every host) or, failing that,
`.env` at the repository root of a source checkout. Lines are KEY=VALUE; `export `,
quotes, blank lines and `#` comments are allowed. Variables already set in the
environment win, so a host or shell can still override a key.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def parse_env_file(path: Path) -> dict[str, str]:
    """Return the KEY=VALUE pairs of a dotenv file (no interpolation)."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def env_file_path() -> Path:
    override = os.environ.get("CREPE_ENV_FILE", "").strip()
    return Path(override).expanduser() if override else REPO_ENV_FILE


def load_env_file() -> None:
    """Export the dotenv values that are not set yet; a missing or unreadable file is ignored."""
    path = env_file_path()
    try:
        values = parse_env_file(path)
    except OSError:
        return
    for key, value in values.items():
        os.environ.setdefault(key, value)
