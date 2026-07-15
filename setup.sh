#!/usr/bin/env bash
# CREPE MCP Server Setup Wrapper Script (`Option 1: Local Development`)
# Delegates to `setup.py` (which uses `argparse` for `--install`, `--uninstall`, `--tavily-key`, etc.)
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" &> /dev/null && pwd)

# Run setup.py via Python 3
if command -v python3 &>/dev/null; then
    exec python3 "$SCRIPT_DIR/setup.py" "$@"
elif command -v uv &>/dev/null; then
    exec uv run --isolated python "$SCRIPT_DIR/setup.py" "$@"
else
    echo "Error: Neither python3 nor uv could be found on your PATH." >&2
    exit 1
fi
