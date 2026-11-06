"""tests/test_reader_paths.py — the reader and fetch_webpage name the right tool for the wrong input."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from crepe_mcp import research
from crepe_mcp.reader.common import ReaderError, resolve_path


class TestReaderPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name, "a b.md").resolve()
        self.file.write_text("# t\n")

    def test_plain_and_file_url(self):
        self.assertEqual(resolve_path(str(self.file)), self.file)
        self.assertEqual(resolve_path(self.file.as_uri()), self.file)
        self.assertEqual(resolve_path("file://localhost" + self.file.as_uri()[len("file://") :]), self.file)

    def test_remote_file_host_refused(self):
        with self.assertRaisesRegex(ReaderError, "local file"):
            resolve_path("file://example.org/x.pdf")

    def test_web_url_names_other_tools(self):
        with self.assertRaisesRegex(ReaderError, "fetch_webpage"):
            resolve_path("https://example.org/x.pdf")

    def test_fetch_webpage_points_file_to_reader(self):
        out = asyncio.run(research.fetch_webpage(self.file.as_uri()))
        self.assertIn("open_document", out["error"])

    def test_fetch_webpage_points_bare_path_to_reader(self):
        out = asyncio.run(research.fetch_webpage(str(self.file)))
        self.assertIn("open_document", out["error"])


if __name__ == "__main__":
    unittest.main()
