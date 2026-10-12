"""tests/test_page_image.py — the cache of LibreOffice-converted PDFs behind render_page_image.

LibreOffice itself is replaced by a function that writes a one-page PDF, so these run anywhere.
Run with: python -m unittest discover -s tests -p "test_page_image.py" -v
"""

import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

from crepe_mcp.reader import page_image


def fake_office_to_pdf(command, source, output_dir, timeout=120):
    target = Path(output_dir) / (Path(source).stem + ".pdf")
    doc = pymupdf.open()
    doc.new_page(width=200, height=100).insert_text((20, 50), "converted", fontsize=12)
    doc.save(str(target))
    doc.close()
    return str(target)


class TestConvertedPdfCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.scratch = self.tmp / "scratch"
        self.source = self.tmp / "report.docx"
        self.source.write_bytes(b"not really a docx")
        patches = [
            mock.patch.dict(os.environ, {"CREPE_SCRATCH_BASE": str(self.scratch)}),
            mock.patch.object(page_image, "find_libreoffice", return_value=["soffice"]),
            mock.patch.object(page_image, "office_to_pdf", side_effect=fake_office_to_pdf),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.base = self.scratch / "office-pdf"

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_cached_files_are_private(self):
        data, count = page_image.render_page(self.source, 1, 50, "png")
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertEqual(count, 1)
        folders = [p for p in self.base.iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 1)
        self.assertEqual(self.mode(self.base), 0o700)
        self.assertEqual(self.mode(folders[0]), 0o700)
        self.assertEqual(self.mode(next(folders[0].glob("*.pdf"))), 0o600)

    def test_second_page_reuses_the_conversion(self):
        page_image.render_page(self.source, 1, 50, "png")
        page_image.render_page(self.source, 1, 50, "jpeg")
        self.assertEqual(page_image.office_to_pdf.call_count, 1)

    def test_old_entries_are_removed_and_recent_ones_kept(self):
        old, recent, stale_work = self.base / ("a" * 24), self.base / ("b" * 24), self.base / "convert-xyz"
        for folder in (old, recent, stale_work):
            folder.mkdir(parents=True)
            (folder / "x.pdf").write_bytes(b"%PDF-")
        long_ago = time.time() - (page_image.CACHE_MAX_AGE + 3600)
        os.utime(old, (long_ago, long_ago))
        os.utime(stale_work, (long_ago, long_ago))
        page_image.render_page(self.source, 1, 50, "png")
        self.assertFalse(old.exists())
        self.assertFalse(stale_work.exists())
        self.assertTrue(recent.exists())

    def test_reuse_refreshes_the_age_of_an_entry(self):
        page_image.render_page(self.source, 1, 50, "png")
        folder = next(p for p in self.base.iterdir() if p.is_dir())
        long_ago = time.time() - page_image.CACHE_MAX_AGE / 2
        os.utime(folder, (long_ago, long_ago))
        page_image.render_page(self.source, 1, 50, "png")
        self.assertGreater(folder.stat().st_mtime, long_ago + 60)


if __name__ == "__main__":
    unittest.main()
