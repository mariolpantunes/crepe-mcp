import os
import unittest
from pathlib import Path

import pymupdf as fitz
from pdf_fixtures import IsolatedTestCase, build_fixture

from crepe_mcp.reader.store import DocumentStore, close_all

REAL_WORKSPACE = os.environ.get("CREPE_READER_CORPUS", "")
ACCESS = Path(REAL_WORKSPACE) / "papers" / "Access-2026-41373_Proof_hi.pdf"


def paragraph_pdf(path: Path) -> Path:
    doc = fitz.open()
    rows = [
        (1, 72, "1 Introduction", 11, "tibo"),
        (1, 72, "The first paragraph starts here and it contin-", 10, "tiro"),
        (1, 72, "ues on the next line of the same paragraph.", 10, "tiro"),
        (1, 84, "Second paragraph begins after a first-line indent.", 10, "tiro"),
        (1, 72, "It also has a second line without an indent.", 10, "tiro"),
        (1, 72, "Figure 1: A caption line in small type.", 8, "tiro"),
        (1, 84, "This sentence goes on", 10, "tiro"),
        (2, 72, "across the page break and ends here.", 10, "tiro"),
    ]
    for page_no in (1, 2):
        # PyMuPDF invalidates page objects when another page is added, so each page is written completely first.
        page = doc.new_page(width=595, height=842)
        y = 100.0
        for row_page, x, text, size, font in rows:
            if row_page != page_no:
                continue
            if text.startswith(("Figure", "This sentence")):
                y += 14
            page.insert_text((x, y), text, fontsize=size, fontname=font)
            y += size * 1.3
    doc.save(str(path))
    doc.close()
    return path


def outline_rows(store: DocumentStore) -> list[tuple]:
    return [(s["level"], s["number"], s["title"], s["page"]) for s in store.outline()]


class TestParagraphsAndSections(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)

    def test_paragraph_rules(self):
        store = DocumentStore.open(paragraph_pdf(self.tmp_path / "paragraphs.pdf"))
        rows = [(p["kind"], p["text"], p["page"], p["last_page"]) for p in store.paragraphs()]
        self.assertEqual(rows, [
            ("heading", "1 Introduction", 1, 1),
            ("text", "The first paragraph starts here and it continues on the next line of the same paragraph.", 1, 1),
            ("text", "Second paragraph begins after a first-line indent. It also has a second line without an indent.",
             1, 1),
            ("caption", "Figure 1: A caption line in small type.", 1, 1),
            ("text", "This sentence goes on across the page break and ends here.", 1, 2),
        ])
        self.assertEqual(outline_rows(store), [(1, "1", "Introduction", 1)])

    def test_ieee_outline(self):
        store = DocumentStore.open(build_fixture("ieee_single", self.tmp_path))
        self.assertEqual(outline_rows(store), [
            (1, "", "Abstract", 4),
            (1, "I", "INTRODUCTION", 4),
            (1, "II", "RELATED WORK", 7),
            (1, "III", "PROPOSED METHOD", 9),
            (1, "IV", "EXPERIMENTS", 12),
            (1, "V", "CONCLUSION", 14),
            (1, "", "REFERENCES", 16),
        ])

    def test_elsevier_outline_includes_cover_abstract(self):
        store = DocumentStore.open(build_fixture("em_revision", self.tmp_path))
        self.assertEqual(outline_rows(store), [
            (1, "", "Abstract", 1),
            (1, "", "Abstract", 2),
            (1, "1", "Introduction", 2),
            (1, "2", "Related Work", 9),
            (1, "3", "Proposed Method", 15),
            (1, "4", "Experiments", 21),
            (1, "5", "Conclusion", 27),
            (1, "", "References", 33),
        ])

    def test_section_paragraphs(self):
        store = DocumentStore.open(build_fixture("ieee_single", self.tmp_path))
        section = next(s for s in store.outline() if s["number"] == "III")
        paragraphs = store.section_paragraphs(section["id"])
        self.assertEqual(paragraphs[0]["kind"], "heading")
        self.assertEqual({p["page"] for p in paragraphs}, {9, 10, 11})
        self.assertEqual(store.section_paragraphs(9999), [])

    def test_search(self):
        store = DocumentStore.open(build_fixture("ieee_single", self.tmp_path))
        total, hits = store.search("ablation experiments")
        self.assertGreater(total, 0)
        self.assertTrue(all(4 <= hit["page"] <= 17 for hit in hits))
        self.assertTrue(all(hit["section_title"] for hit in hits))
        self.assertIn("[", hits[0]["snippet"])
        total, hits = store.search("Table I")
        self.assertGreaterEqual(total, 2)
        self.assertEqual({hit["page"] for hit in hits}, {7})
        total, hits = store.search("the", limit=3)
        self.assertEqual(len(hits), 3)
        self.assertGreater(total, 3)
        self.assertEqual(store.search('"; DROP TABLE lines; --')[0], 0)
        self.assertEqual(store.search("..."), (0, []))
        self.assertEqual(store.search("ablation", first=1, last=3), (0, []))


@unittest.skipUnless(REAL_WORKSPACE and ACCESS.is_file(), "set CREPE_READER_CORPUS to a folder with papers/")
class TestRealAccessIndexes(IsolatedTestCase):
    def test_outline_structure(self):
        self.addCleanup(close_all)
        store = DocumentStore.open(ACCESS)
        outline = store.outline()
        levels = [(s["level"], bool(s["number"])) for s in outline]
        self.assertEqual(levels.count((1, True)), 6, outline)
        self.assertEqual(levels.count((2, True)), 9, outline)
        self.assertEqual(levels.count((3, True)), 5, outline)
        self.assertEqual(levels.count((1, False)), 2, outline)
        self.assertEqual([s["number"] for s in outline if s["level"] == 1 and s["number"]],
                         ["I", "II", "III", "IV", "V", "VI"])
        self.assertTrue(all(4 <= s["page"] <= 16 for s in outline))
        self.assertGreater(len(store.paragraphs()), 100)
        self.assertGreater(store.search("Fig")[0], 0)


if __name__ == "__main__":
    unittest.main()
