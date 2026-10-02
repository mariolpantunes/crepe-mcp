"""tests/test_skill.py — the crepe agent skill and the resilience of setup.py.

Run with: python -m unittest tests.test_skill -v
"""
import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "crepe" / "SKILL.md"

CONFIG = """\
# my goose config
GOOSE_MODE: auto  # keep me
extensions:
  developer:
    enabled: true
    type: builtin
  crepe-research:
    enabled: false
    cmd: /old/path
  # a comment between blocks
  other:
    enabled: true
    args:
    - -y
active_provider: x
# trailing comment
"""


def load_setup():
    spec = importlib.util.spec_from_file_location("crepe_setup", ROOT / "setup.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crepe_setup"] = mod
    spec.loader.exec_module(mod)
    assert mod.load_yaml() is not None, "PyYAML is required for these tests"
    return mod


class SkillTests(unittest.TestCase):
    def test_frontmatter(self):
        m = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", SKILL.read_text())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "crepe")
        self.assertLess(len(m.group(2)), 300)

    def test_lists_every_sub_server(self):
        setup = load_setup()
        text = SKILL.read_text()
        for sub in setup.SUB_SERVERS:
            self.assertIn(f"`{sub['name']}`", text)

    def test_install_and_remove(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            setup.SKILL_DST = Path(tmp) / "skills" / "crepe"
            setup.AGENTS_MD_DST = Path(tmp) / "CREPE_AGENTS.md"
            setup.AGENTS_MD_DST.write_text("legacy")
            self.assertTrue(setup.install_skill())
            self.assertTrue((setup.SKILL_DST / "SKILL.md").is_file())
            self.assertTrue((setup.SKILL_DST / "references" / "guide.md").is_file())
            self.assertFalse(Path(str(setup.SKILL_DST) + ".tmp").exists())
            setup.remove_skill()
            self.assertFalse(setup.SKILL_DST.exists())
            self.assertFalse(setup.AGENTS_MD_DST.exists())

    def test_install_keeps_old_copy_when_source_missing(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            setup.SKILL_DST = Path(tmp) / "crepe"
            setup.SKILL_DST.mkdir()
            (setup.SKILL_DST / "SKILL.md").write_text("old")
            setup.SKILL_SRC = Path(tmp) / "missing"
            self.assertFalse(setup.install_skill())
            self.assertEqual((setup.SKILL_DST / "SKILL.md").read_text(), "old")


class GooseConfigTests(unittest.TestCase):
    def setUp(self):
        self.setup = load_setup()
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.setup.GOOSE_CONFIG_DIR = d
        self.setup.GOOSE_CONFIG_PATH = d / "config.yaml"
        self.setup.GOOSE_CONFIG_PATH.write_text(CONFIG)
        self.yaml = self.setup.yaml

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return self.yaml.safe_load(self.setup.GOOSE_CONFIG_PATH.read_text())

    def test_update_preserves_comments_and_other_settings(self):
        self.assertTrue(self.setup.update_goose_config({"K": "v"}))
        text = self.setup.GOOSE_CONFIG_PATH.read_text()
        for keep in ("# my goose config", "# keep me", "# a comment between blocks", "# trailing comment"):
            self.assertIn(keep, text)
        cfg = self.load()
        self.assertEqual(cfg["active_provider"], "x")
        self.assertEqual(cfg["extensions"]["other"]["args"], ["-y"])
        self.assertEqual(cfg["extensions"]["developer"]["type"], "builtin")
        for sub in self.setup.SUB_SERVERS:
            self.assertEqual(cfg["extensions"][sub["name"]]["envs"], {"K": "v"})
            self.assertTrue(cfg["extensions"][sub["name"]]["description"])
        self.assertNotIn("/old/path", text)

    def test_update_keeps_user_enabled_state_and_is_idempotent(self):
        self.setup.update_goose_config({})
        self.assertFalse(self.load()["extensions"]["crepe-research"]["enabled"])
        self.setup.update_goose_config({}, enable_all=True)
        self.assertTrue(self.load()["extensions"]["crepe-presentations"]["enabled"])
        self.setup.update_goose_config({})
        self.assertTrue(self.load()["extensions"]["crepe-presentations"]["enabled"])
        before = self.setup.GOOSE_CONFIG_PATH.read_text()
        self.setup.update_goose_config({})
        self.assertEqual(self.setup.GOOSE_CONFIG_PATH.read_text(), before)

    def test_reinstall_keeps_previous_env_values(self):
        self.setup.update_goose_config({"CREPE_TAVILY_API_KEY": "secret", "CREPE_DRAWIO_PATH": "/a"})
        self.setup.update_goose_config({"CREPE_DRAWIO_PATH": "/b"})
        envs = self.load()["extensions"]["crepe-research"]["envs"]
        self.assertEqual(envs, {"CREPE_TAVILY_API_KEY": "secret", "CREPE_DRAWIO_PATH": "/b"})

    def test_backups_are_timestamped_and_pruned(self):
        for _ in range(8):
            self.setup.backup_file(self.setup.GOOSE_CONFIG_PATH, keep=3)
            import time
            time.sleep(1.01)
        self.assertEqual(len(list(Path(self.tmp.name).glob("config.yaml.bak-*"))), 3)

    def test_remove_only_touches_crepe(self):
        self.setup.update_goose_config({})
        self.setup.remove_from_goose_config()
        text = self.setup.GOOSE_CONFIG_PATH.read_text()
        cfg = self.load()
        self.assertEqual(set(cfg["extensions"]), {"developer", "other"})
        self.assertIn("# a comment between blocks", text)
        self.assertIn("# trailing comment", text)

    def test_flow_style_extensions_falls_back_to_full_rewrite(self):
        self.setup.GOOSE_CONFIG_PATH.write_text("extensions: {}\nGOOSE_MODE: auto\n")
        self.assertTrue(self.setup.update_goose_config({}))
        cfg = self.load()
        self.assertEqual(cfg["GOOSE_MODE"], "auto")
        self.assertIn("crepe-presentations", cfg["extensions"])

    def test_stale_monolith_entry_is_dropped_on_install(self):
        self.setup.GOOSE_CONFIG_PATH.write_text("extensions:\n  crepe:\n    enabled: true\n    cmd: /old/crepe-mcp\n")
        self.assertTrue(self.setup.update_goose_config({}))
        cfg = self.load()
        self.assertNotIn("crepe", cfg["extensions"])
        self.assertIn("crepe-presentations", cfg["extensions"])

    def test_stale_monolith_entry_is_dropped_from_json_config(self):
        import json

        path = Path(self.tmp.name) / "mcp.json"
        path.write_text(json.dumps({"mcpServers": {"crepe": {"command": "/old/crepe-mcp"}, "other": {"command": "x"}}}))
        self.assertTrue(self.setup.update_json_mcp_config(path, "Test", {}))
        servers = json.loads(path.read_text())["mcpServers"]
        self.assertNotIn("crepe", servers)
        self.assertEqual(servers["other"], {"command": "x"})
        self.assertEqual({s["name"] for s in self.setup.SUB_SERVERS} - set(servers), set())

    def test_unparsable_config_is_left_alone(self):
        self.setup.GOOSE_CONFIG_PATH.write_text("extensions: [unclosed\n")
        self.assertFalse(self.setup.update_goose_config({}))
        self.assertEqual(self.setup.GOOSE_CONFIG_PATH.read_text(), "extensions: [unclosed\n")


if __name__ == "__main__":
    unittest.main()
