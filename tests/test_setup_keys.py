"""tests/test_setup_keys.py — setup.py keeps API keys in the .env and out of client configs.

Run with: python -m unittest tests.test_setup_keys -v
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_skill import load_setup


class TestEnvFile(unittest.TestCase):
    def setUp(self):
        self.setup = load_setup()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_file = Path(self.tmp.name) / "conf" / ".env"
        patcher = mock.patch.dict(os.environ, {"CREPE_ENV_FILE": str(self.env_file)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, _ in self.setup.API_KEY_VARS.values():
            os.environ.pop(name, None)

    def test_store_creates_private_file_and_keeps_other_lines(self):
        self.setup.store_env_value("CREPE_A", "1")
        self.env_file.write_text(self.env_file.read_text() + "# note\nOTHER=x\n")
        self.setup.store_env_value("CREPE_A", "2")
        self.setup.store_env_value("CREPE_B", "3")
        self.assertEqual(self.env_file.read_text(), "CREPE_A=2\n# note\nOTHER=x\nCREPE_B=3\n")
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)

    def test_has(self):
        self.assertFalse(self.setup.env_file_has("CREPE_A"))
        self.setup.store_env_value("CREPE_A", "")
        self.assertFalse(self.setup.env_file_has("CREPE_A"))
        self.setup.store_env_value("CREPE_A", "v")
        self.assertTrue(self.setup.env_file_has("CREPE_A"))

    def test_configure_takes_process_env_and_never_prints_the_value(self):
        os.environ["CREPE_TAVILY_API_KEY"] = "s3cret-value"
        with mock.patch("builtins.print") as out:
            self.setup.configure_api_keys(non_interactive=True)
        self.assertIn("CREPE_TAVILY_API_KEY=s3cret-value", self.env_file.read_text())
        self.assertNotIn("s3cret-value", " ".join(str(c) for c in out.call_args_list))

    def test_configure_without_key_writes_nothing_when_non_interactive(self):
        self.setup.configure_api_keys(non_interactive=True)
        self.assertFalse(self.env_file.exists())

    def test_client_config_gets_no_keys(self):
        os.environ["CREPE_TAVILY_API_KEY"] = "s3cret-value"
        self.setup.configure_api_keys(non_interactive=True)
        cfg = Path(self.tmp.name) / "mcp.json"
        self.setup.update_json_mcp_config(cfg, "Test", {"CREPE_DRAWIO_PATH": "/x"})
        self.assertNotIn("s3cret-value", cfg.read_text())
        self.assertNotIn("API_KEY", json.dumps(json.loads(cfg.read_text())))


if __name__ == "__main__":
    unittest.main()
