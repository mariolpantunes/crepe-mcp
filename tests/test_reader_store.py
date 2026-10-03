import os
import re
import shutil
import stat
import unittest
from pathlib import Path
from unittest import mock

import pymupdf as fitz
from pdf_fixtures import IsolatedTestCase, build_fixture

from crepe_mcp.reader import store as store_module
from crepe_mcp.reader.config import scratch_base
from crepe_mcp.reader.document import extract
from crepe_mcp.reader.heuristics import LINE_NUMBER_SEQUENCE
from crepe_mcp.reader.store import DocumentStore, close_all

REAL_WORKSPACE = os.environ.get("CREPE_READER_CORPUS", "")


def two_column_pdf(path: Path, line_numbers: bool = False) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 120), "A Spanning Title Across Both Columns Used To Check Reading Order", fontsize=12)
    for i in range(12):
        page.insert_text((60, 160 + 14 * i), f"Left column line {i + 1}", fontsize=10)
        page.insert_text((320, 160 + 14 * i), f"Right column line {i + 1}", fontsize=10)
    page.insert_text((60, 360), "Full width caption line that crosses the middle of the page layout area", fontsize=9)
    for i in range(4):
        page.insert_text((60, 390 + 14 * i), f"Left column line {i + 13}", fontsize=10)
        page.insert_text((320, 390 + 14 * i), f"Right column line {i + 13}", fontsize=10)
    if line_numbers:
        for k in range(40):
            page.insert_text((16, 110 + 14 * k), str(k + 1), fontsize=8)
            page.insert_text((570, 110 + 14 * k), str(k + 1), fontsize=8)
    doc.save(str(path))
    doc.close()
    return path


def dump(store: DocumentStore) -> list[list[tuple]]:
    tables = ("pages", "lines", "paragraphs", "sections", "segments", "structure", "assets", "mentions")
    return [[tuple(row) for row in store.con.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in tables]


class TestDocumentExtraction(IsolatedTestCase):
    def test_two_column_reading_order(self):
        page = extract(two_column_pdf(self.tmp_path / "columns.pdf"))[0]
        expected = (
            ["A Spanning Title Across Both Columns Used To Check Reading Order"]
            + [f"Left column line {i}" for i in range(1, 13)]
            + [f"Right column line {i}" for i in range(1, 13)]
            + ["Full width caption line that crosses the middle of the page layout area"]
            + [f"Left column line {i}" for i in range(13, 17)]
            + [f"Right column line {i}" for i in range(13, 17)]
        )
        self.assertEqual(page.text.splitlines(), expected)

    def test_line_numbers_are_excluded(self):
        page = extract(two_column_pdf(self.tmp_path / "numbered.pdf", line_numbers=True))[0]
        self.assertEqual(sum(1 for line in page.lines if line.region == "linenumber"), 80)
        self.assertFalse(any(re.fullmatch(r"\d+", text) for text in page.text.splitlines()))
        self.assertIn("Right column line 12", page.text)


class TestDocumentStore(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)

    def test_margins_removed_from_page_text(self):
        store = DocumentStore.open(build_fixture("ieee_single", self.tmp_path))
        self.assertEqual(store.page_count, 17)
        for page in store.pages():
            self.assertNotIn(f"Page {page['page']} of 17", page["text"])
            self.assertNotIn("VOLUME 11, 2023", page["text"])
        self.assertEqual(len(store.lines(2, ("header",))), 2)
        self.assertEqual([line["text"] for line in store.lines(5, ("footer",))], ["VOLUME 11, 2023"])
        self.assertGreaterEqual(len(store.lines(5, ("linenumber",))), LINE_NUMBER_SEQUENCE)
        self.assertIn("I. INTRODUCTION", store.pages(4, 4)[0]["text"])

    def test_store_location_permissions_and_cache(self):
        pdf = build_fixture("ieee_single", self.tmp_path)
        store = DocumentStore.open(pdf)
        self.assertIs(DocumentStore.open(pdf), store)
        self.assertTrue(str(store.path).startswith(str(scratch_base() / "store")))
        self.assertEqual(stat.S_IMODE(store.path.parent.stat().st_mode), 0o700)
        self.assertEqual(store.meta()["extractor_version"], store_module.EXTRACTOR_VERSION)
        store.set_state("manuscript", "4-17")
        self.assertEqual(DocumentStore.open(pdf).get_state("manuscript"), "4-17")

    def test_same_name_different_content_gets_its_own_store(self):
        first = self.tmp_path / "a" / "paper.pdf"
        second = self.tmp_path / "b" / "paper.pdf"
        first.parent.mkdir()
        second.parent.mkdir()
        for path, words in ((first, "First version of the manuscript"), (second, "Second version of the manuscript")):
            doc = fitz.open()
            doc.new_page().insert_text((72, 300), words)
            doc.save(str(path))
            doc.close()
        one, two = DocumentStore.open(first), DocumentStore.open(second)
        self.assertNotEqual(one.path, two.path)
        self.assertIn("First version", one.pages()[0]["text"])
        self.assertIn("Second version", two.pages()[0]["text"])
        shutil.copy(second, first)
        self.assertIn("Second version", DocumentStore.open(first).pages()[0]["text"])

    def test_rebuild_is_deterministic(self):
        pdf = build_fixture("scholarone_two_copies", self.tmp_path)
        store = DocumentStore.open(pdf)
        before = dump(store)
        path = store.path
        close_all()
        shutil.rmtree(path.parent)
        after = dump(DocumentStore.open(pdf))
        self.assertEqual(before, after)

    def test_extractor_version_change_rebuilds(self):
        pdf = build_fixture("em_revision", self.tmp_path)
        DocumentStore.open(pdf).set_state("marker", "old build")
        with mock.patch.object(store_module, "EXTRACTOR_VERSION", "test-bump"):
            rebuilt = DocumentStore.open(pdf)
            self.assertEqual(rebuilt.meta()["extractor_version"], "test-bump")
            self.assertIsNone(rebuilt.get_state("marker"))

    def test_pages_without_text(self):
        scanned = DocumentStore.open(build_fixture("scanned", self.tmp_path)).pages()[0]
        self.assertEqual((scanned["source"], scanned["text"]), ("none", ""))
        blank = DocumentStore.open(build_fixture("em_long_review", self.tmp_path)).pages(93, 93)[0]
        self.assertEqual((blank["source"], blank["text"]), ("blank", ""))

    def test_missing_pdf(self):
        with self.assertRaises(FileNotFoundError):
            DocumentStore.open(self.tmp_path / "missing.pdf")


@unittest.skipUnless(REAL_WORKSPACE and (Path(REAL_WORKSPACE) / "papers" / "Access-2026-41373_Proof_hi.pdf").is_file(),
                     "set CREPE_READER_CORPUS to a folder with papers/")
class TestRealAccessProof(IsolatedTestCase):
    def test_structure_facts(self):
        self.addCleanup(close_all)
        store = DocumentStore.open(Path(REAL_WORKSPACE) / "papers" / "Access-2026-41373_Proof_hi.pdf")
        pages = {page["page"]: page for page in store.pages()}
        self.assertEqual(len(pages), 17)
        self.assertGreater(pages[1]["width"], pages[4]["width"])
        self.assertGreaterEqual(len(store.lines(5, ("linenumber",))), 50)
        self.assertTrue(any("et al." in line["text"] for line in store.lines(5, ("header",))))
        body = store.lines(5, ("body",))
        self.assertTrue(body)
        self.assertFalse(any(re.fullmatch(r"\d{1,4}", line["text"]) and line["x1"] < 34 for line in body))
        self.assertEqual({line["col"] for line in body} - {-1}, {0, 1})


if __name__ == "__main__":
    unittest.main()
