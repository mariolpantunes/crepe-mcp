"""tests/test_reader_legacy.py — legacy .doc, .xls and .ppt are converted by LibreOffice, then read through pandoc.

LibreOffice is mocked: the fake converter writes the OOXML twin that pandoc builds from a Markdown sample.

Run with: python -m unittest discover -s tests -p "test_reader_legacy.py" -v
"""

import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from fastmcp.exceptions import ToolError
from test_reader_server import ReaderTestCase, call, data

from crepe_mcp.exporter import find_libreoffice, office_convert

SAMPLE = "---\ntitle: Old report\n---\n\n# Intro\n\nLegacy sorting text.\n\n## Method\n\nWe sort things.\n"
SLIDES = "# Deck\n\n## First slide\n\n- one\n- two\n\n## Second slide\n\nText.\n"


@unittest.skipUnless(shutil.which("pandoc"), "pandoc is required")
class TestLegacyOffice(ReaderTestCase):
    def setUp(self):
        super().setUp()
        self.twins = {}
        for suffix, fmt, text in ((".doc", "docx", SAMPLE), (".ppt", "pptx", SLIDES)):
            source = self.tmp_path / f"twin{fmt}.md"
            source.write_text(text, encoding="utf-8")
            twin = self.tmp_path / f"twin.{fmt}"
            subprocess.run(["pandoc", str(source), "-o", str(twin)], check=True, capture_output=True)
            self.twins[suffix] = twin
            (self.tmp_path / f"old{suffix}").write_bytes(b"\xd0\xcf\x11\xe0 not a real binary file")
        self.calls = []

        def fake_convert(command, source, output_dir, target, timeout=120):
            self.calls.append((Path(source).suffix, target))
            out = Path(output_dir) / f"{Path(source).stem}.{target}"
            shutil.copy(self.twins[Path(source).suffix], out)
            return str(out)

        for name, value in (
            ("crepe_mcp.reader.legacy.find_libreoffice", lambda: ["soffice"]),
            ("crepe_mcp.reader.legacy.office_convert", fake_convert),
        ):
            patched = mock.patch(name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def test_doc_is_read_as_docx_and_says_so(self):
        path = str(self.tmp_path / "old.doc")
        info = data("open_document", path=path)
        self.assertEqual((info["file"], info["format"], info["converted_from"]), ("old.doc", "docx", ".doc"))
        self.assertEqual(info["title"], "Old report")
        self.assertIn("We sort things", data("read_section", path=path, heading="Method"))
        self.assertEqual(self.calls, [(".doc", "docx")], "converted once, then served from the store")

    def test_ppt_is_read_as_pptx(self):
        info = data("open_document", path=str(self.tmp_path / "old.ppt"))
        self.assertEqual((info["format"], info["converted_from"]), ("pptx", ".ppt"))
        self.assertIn("First slide", " ".join(info["outline"]))

    def test_without_libreoffice_the_error_says_how_to_fix_it(self):
        with mock.patch("crepe_mcp.reader.legacy.find_libreoffice", return_value=None):
            with self.assertRaises(ToolError) as caught:
                call("open_document", path=str(self.tmp_path / "old.doc"))
        self.assertIn("LibreOffice", str(caught.exception))
        self.assertIn("docx", str(caught.exception))


@unittest.skipUnless(shutil.which("pandoc") and find_libreoffice(), "pandoc and LibreOffice are required")
class TestRealLibreOffice(ReaderTestCase):
    def test_a_real_doc_and_ppt_are_read(self):
        for suffix, fmt, text in ((".doc", "docx", SAMPLE), (".ppt", "pptx", SLIDES)):
            source = self.tmp_path / f"real{fmt}.md"
            source.write_text(text, encoding="utf-8")
            modern = self.tmp_path / f"real.{fmt}"
            subprocess.run(["pandoc", str(source), "-o", str(modern)], check=True, capture_output=True)
            legacy = Path(office_convert(find_libreoffice(), str(modern), str(self.tmp_path), suffix[1:], timeout=300))
            with self.subTest(suffix=suffix):
                info = data("open_document", path=str(legacy))
                self.assertEqual((info["format"], info["converted_from"]), (fmt, suffix))
                self.assertTrue(any(word in " ".join(info["outline"]) for word in ("Method", "First slide")))


if __name__ == "__main__":
    unittest.main()
