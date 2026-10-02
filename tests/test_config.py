"""tests/test_config.py — `.env` loading for API keys.

Run with: python -m unittest tests.test_config -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crepe_mcp import config


class TestParseEnv(unittest.TestCase):
    def test_forms(self):
        text = "# c\n\nA=1\nexport B='two words'\nC=\"3\"\nD=4 # note\nbad line\n=x\n"
        self.assertEqual(config.parse_env(text), {"A": "1", "B": "two words", "C": "3", "D": "4"})


class TestLoadEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _write(self, name: str, text: str, mode: int = 0o600) -> Path:
        path = self.tmp / name
        path.write_text(text)
        path.chmod(mode)
        return path

    def test_loads_without_overriding(self):
        path = self._write("a.env", "CREPE_T_NEW=fromfile\nCREPE_T_SET=fromfile\n")
        env = {config.ENV_FILE_VAR: str(path), "CREPE_T_SET": "fromenv"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("CREPE_T_NEW", None)
            self.assertEqual(config.load_env(), path)
            self.assertEqual(os.environ["CREPE_T_NEW"], "fromfile")
            self.assertEqual(os.environ["CREPE_T_SET"], "fromenv")

    def test_missing_file_is_silent(self):
        env = {config.ENV_FILE_VAR: str(self.tmp / "none.env"), "XDG_CONFIG_HOME": str(self.tmp)}
        with mock.patch.dict(os.environ, env), mock.patch("pathlib.Path.cwd", return_value=self.tmp):
            self.assertIsNone(config.load_env())

    def test_loose_permissions_warn_without_value(self):
        path = self._write("b.env", "CREPE_T_SECRET=hunter2\n", mode=0o644)
        with mock.patch.dict(os.environ, {config.ENV_FILE_VAR: str(path)}):
            with mock.patch("sys.stderr") as err:
                config.load_env()
            printed = "".join(str(c.args[0]) for c in err.write.call_args_list)
            self.assertIn("chmod 600", printed)
            self.assertNotIn("hunter2", printed)
            os.environ.pop("CREPE_T_SECRET", None)


if __name__ == "__main__":
    unittest.main()
