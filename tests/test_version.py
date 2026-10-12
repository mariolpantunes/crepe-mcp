"""tests/test_version.py — one version string everywhere.

Run with: python -m unittest tests.test_version -v
"""

import re
import tomllib
import unittest
from pathlib import Path

import crepe_mcp

ROOT = Path(__file__).resolve().parent.parent


class TestVersion(unittest.TestCase):
    def test_package_version_matches_the_project_metadata(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(crepe_mcp.__version__, project["version"])

    def test_citation_and_skill_carry_the_same_version(self):
        citation = re.search(r"^version:\s*(\S+)", (ROOT / "CITATION.cff").read_text(encoding="utf-8"), re.M)
        skill = re.search(
            r'^\s+version:\s*"([^"]+)"', (ROOT / "skills/crepe/SKILL.md").read_text(encoding="utf-8"), re.M
        )
        self.assertIsNotNone(citation)
        self.assertIsNotNone(skill)
        self.assertEqual(citation.group(1), crepe_mcp.__version__)
        self.assertEqual(skill.group(1), crepe_mcp.__version__)


if __name__ == "__main__":
    unittest.main()
