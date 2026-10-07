"""Tests for the .env loader that keeps API keys out of agent host configs."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crepe_mcp._env import parse_env_file

SRC = str(Path(__file__).resolve().parent.parent / "src")


class TestEnvFile(unittest.TestCase):
    def write(self, text: str) -> Path:
        fd, name = tempfile.mkstemp(suffix=".env")
        os.close(fd)
        path = Path(name)
        path.write_text(text, encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    def test_parse_formats(self):
        path = self.write('# comment\n\nA=1\nexport B="two words"\nC=\'x=y\'\nnot a pair\n')
        self.assertEqual(parse_env_file(path), {"A": "1", "B": "two words", "C": "x=y"})

    def test_import_loads_file_without_overriding(self):
        path = self.write("CREPE_TEST_KEY=from-file\nCREPE_TEST_SET=from-file\n")
        env = {**os.environ, "CREPE_ENV_FILE": str(path), "CREPE_TEST_SET": "from-env", "PYTHONPATH": SRC}
        code = "import os, crepe_mcp; print(os.environ['CREPE_TEST_KEY'], os.environ['CREPE_TEST_SET'])"
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split(), ["from-file", "from-env"])

    def test_missing_file_is_ignored(self):
        env = {**os.environ, "CREPE_ENV_FILE": "/nonexistent/.env", "PYTHONPATH": SRC}
        subprocess.run([sys.executable, "-c", "import crepe_mcp"], env=env, check=True)


if __name__ == "__main__":
    unittest.main()
