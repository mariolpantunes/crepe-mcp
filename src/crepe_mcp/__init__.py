"""CREPE — Compile, Research, Export, Presentation Engine."""

from crepe_mcp._env import load_env_file

__version__ = "0.2.3"

# API keys come from the .env file (CREPE_ENV_FILE), loaded before any sub-server reads them.
load_env_file()
