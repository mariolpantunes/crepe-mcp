import os
import unittest
from pathlib import Path

from pdf_fixtures import FIXTURES, IsolatedTestCase, build_fixture

from crepe_mcp.reader.store import DocumentStore, close_all

REAL_WORKSPACE = os.environ.get("CREPE_READER_CORPUS", "")
ACCESS = Path(REAL_WORKSPACE) / "papers" / "Access-2026-41373_Proof_hi.pdf"


def parts(store: DocumentStore) -> list[tuple[str, int, int]]:
    return [(s["kind"], s["first_page"], s["last_page"]) for s in store.segments()]


class TestStructure(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)

    def test_fixture_parts_round_and_manuscript(self):
        for name, spec in FIXTURES.items():
            store = DocumentStore.open(build_fixture(name, self.tmp_path))
            self.assertEqual(parts(store), list(spec.segments), name)
            self.assertEqual(store.structure()["round"], spec.round, name)
            pages = store.manuscript_pages()
            if spec.current_manuscript:
                self.assertEqual((pages["first"], pages["last"], pages["source"]),
                                 (*spec.current_manuscript, "detected"), name)
            else:
                self.assertEqual((pages["first"], pages["last"], pages["source"]), (1, spec.pages, "whole-document"))

    def test_content_wins_over_item_label(self):
        store = DocumentStore.open(build_fixture("em_long_review", self.tmp_path))
        manuscript = next(s for s in store.segments() if s["kind"] == "manuscript")
        self.assertEqual(manuscript["label"], "Response to reviewers")
        self.assertIn("item label 'Response to reviewers'", manuscript["evidence"][0])

    def test_current_copy_chosen_by_markup(self):
        store = DocumentStore.open(build_fixture("scholarone_two_copies", self.tmp_path))
        structure = store.structure()
        self.assertEqual(structure["current_confidence"], "high")
        self.assertEqual(len(structure["current_evidence"]), 2)
        self.assertIn("0 coloured characters, 0 mark-up annotations", structure["current_evidence"][0])
        self.assertNotIn(" 0 mark-up annotations", structure["current_evidence"][1])

    def test_round_evidence(self):
        revision = DocumentStore.open(build_fixture("em_revision", self.tmp_path)).structure()
        self.assertEqual((revision["round"], revision["round_label"], revision["round_confidence"]),
                         ("revision", "R2", "high"))
        self.assertTrue(any("author responses on pages 38-58" in e for e in revision["round_evidence"]))
        first = DocumentStore.open(build_fixture("ieee_single", self.tmp_path)).structure()
        self.assertEqual((first["round"], first["round_confidence"]), ("first", "high"))
        self.assertIn("Initial Submission", first["round_evidence"][0])

    def test_manual_manuscript_override(self):
        store = DocumentStore.open(build_fixture("scholarone_two_copies", self.tmp_path))
        store.set_manuscript_pages(24, 43, "the clean copy is the second one")
        pages = store.manuscript_pages()
        self.assertEqual((pages["first"], pages["last"], pages["source"]), (24, 43, "override"))
        self.assertEqual(pages["reason"], "the clean copy is the second one")
        for first, last in ((0, 5), (10, 5), (1, 99)):
            with self.assertRaises(ValueError):
                store.set_manuscript_pages(first, last, "bad range")

    def test_outline_continues_after_an_earlier_reference_list(self):
        long_review = DocumentStore.open(build_fixture("em_long_review", self.tmp_path)).outline()
        rows = [(s["level"], s["number"], s["title"], s["page"]) for s in long_review]
        self.assertIn((1, "1", "Introduction", 15), rows)
        copies = DocumentStore.open(build_fixture("scholarone_two_copies", self.tmp_path)).outline()
        self.assertEqual([s["page"] for s in copies if s["title"] == "INTRODUCTION"], [4, 24])

    def test_assets_are_kept_per_manuscript_copy(self):
        store = DocumentStore.open(build_fixture("scholarone_two_copies", self.tmp_path))
        self.assertEqual([a["page"] for a in store.assets("figure", 4, 23)], [5, 9, 10])
        self.assertEqual([a["page"] for a in store.assets("figure", 24, 43)], [25, 29, 30])
        second = store.asset("figure:1", 24, 43)
        assert second is not None
        self.assertEqual(second["page"], 25)
        self.assertTrue(all(24 <= m["page"] <= 43 for m in second["mentions"]))


@unittest.skipUnless(REAL_WORKSPACE and ACCESS.is_file(), "set CREPE_READER_CORPUS to a folder with papers/")
class TestRealAccessStructure(IsolatedTestCase):
    def test_parts_and_round(self):
        self.addCleanup(close_all)
        store = DocumentStore.open(ACCESS)
        self.assertEqual(parts(store), [("cover", 1, 3), ("manuscript", 4, 17)])
        structure = store.structure()
        self.assertEqual((structure["round"], structure["round_confidence"]), ("first", "high"))
        pages = store.manuscript_pages()
        self.assertEqual((pages["first"], pages["last"], pages["source"]), (4, 17, "detected"))


if __name__ == "__main__":
    unittest.main()
