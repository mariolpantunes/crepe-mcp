import os
import unittest
from pathlib import Path

from pdf_fixtures import IsolatedTestCase, PaperBuilder, build_fixture, filler

from crepe_mcp.reader.store import DocumentStore, close_all

REAL_WORKSPACE = os.environ.get("CREPE_READER_CORPUS", "")
ACCESS = Path(REAL_WORKSPACE) / "papers" / "Access-2026-41373_Proof_hi.pdf"


def ids(store: DocumentStore, kind: str) -> list[str]:
    return [asset["id"] for asset in store.assets(kind)]


def numbered(values: list[str]) -> list[str]:
    return sorted(values, key=lambda value: int(value.split(":")[1]))


class TestAssets(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)

    def test_ieee_fixture_assets(self):
        store = DocumentStore.open(build_fixture("ieee_single", self.tmp_path))
        figures = {asset["id"]: asset for asset in store.assets("figure")}
        self.assertEqual(ids(store, "figure"), ["figure:1", "figure:2", "figure:3"])
        self.assertEqual([figures[i]["page"] for i in ids(store, "figure")], [5, 9, 10])
        self.assertTrue(all(a["method"] == "caption+image" and a["confidence"] == "high" for a in figures.values()))
        self.assertIn("Accuracy (%)", figures["figure:1"]["content"])
        self.assertTrue(figures["figure:1"]["caption"].startswith("Fig. 1."))
        self.assertGreaterEqual(figures["figure:1"]["cited"], 1)
        self.assertGreaterEqual(figures["figure:2"]["cited"], 1)
        self.assertEqual(figures["figure:3"]["cited"], 0)

        table = store.asset("table:I")
        self.assertIsNotNone(table)
        assert table is not None
        self.assertEqual((table["page"], table["method"], table["content_format"]), (7, "caption+table", "markdown"))
        self.assertIn("| Dataset | Items | Features |\n|---|---|---|\n| A | 1200 | 16 |", table["content"])
        self.assertIn(7, {m["page"] for m in table["mentions"]})

        self.assertEqual(ids(store, "equation"), ["equation:1", "equation:2"])
        equation = store.asset("equation:1")
        assert equation is not None
        self.assertEqual(equation["page"], 6)
        self.assertEqual(equation["content_format"], "text+mathml")
        text, markup = equation["content"].split("\n")
        self.assertTrue(text.startswith("f(x) = ") and text.endswith("w_ix_i^2"), text)
        self.assertIn("<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>", markup)
        self.assertIn("Eq. (1)", {m["text"] for m in equation["mentions"]})

        algorithm = store.asset("algorithm:1")
        assert algorithm is not None
        self.assertEqual((algorithm["page"], algorithm["method"]), (8, "caption+rules"))
        self.assertIn("1: R <- X", algorithm["content"])
        self.assertIn("4: end while", algorithm["content"])
        self.assertIn(8, {m["page"] for m in algorithm["mentions"]})

        self.assertEqual(numbered(ids(store, "reference")), [f"reference:{k}" for k in range(1, 15)])
        reference = store.asset("reference:1")
        assert reference is not None
        self.assertIn(4, {m["page"] for m in reference["mentions"]})
        self.assertIsNone(store.asset("figure:99"))

    def test_caption_paragraphs_need_small_type(self):
        store = DocumentStore.open(build_fixture("ieee_single", self.tmp_path))
        captions = [p["text"] for p in store.paragraphs() if p["kind"] == "caption"]
        self.assertFalse(any(text.startswith(("Table I lists", "Algorithm 1 details")) for text in captions))
        self.assertTrue(any(text.startswith("TABLE I") for text in captions))
        references = [p for p in store.paragraphs() if p["kind"] == "reference"]
        self.assertEqual(len(references), 14)
        self.assertTrue(all(p["text"].endswith("2024.") for p in references))

    def test_elsevier_fixture_assets(self):
        store = DocumentStore.open(build_fixture("em_revision", self.tmp_path))
        self.assertEqual(ids(store, "figure"), ["figure:1", "figure:2", "figure:3"])
        self.assertEqual([a["page"] for a in store.assets("figure")], [3, 7, 8])
        self.assertEqual(ids(store, "table"), ["table:1"])
        table = store.asset("table:1")
        assert table is not None
        self.assertEqual(table["method"], "caption+table")
        self.assertGreaterEqual(len(table["mentions"]), 1)
        self.assertEqual(numbered(ids(store, "reference")), [f"reference:{k}" for k in range(1, 36)])

    def test_statements_proofs_and_citation_lists(self):
        builder = PaperBuilder()
        builder.new_page()
        builder.heading("2. Analysis")
        builder.paragraph(filler(3, 2))
        builder.paragraph("Theorem 1 (Bound). For every input the error is at most one half.")
        builder.paragraph("Proof. The claim follows from the triangle inequality.")
        builder.paragraph("Lemma 2. The loss is convex on the feasible set.")
        builder.paragraph("Theorems 1 and 2 are illustrated in Figs. 1-3 and discussed in Tables I-III.")
        store = DocumentStore.open(builder.save(self.tmp_path / "statements.pdf"))
        self.assertEqual(ids(store, "statement"), ["theorem:1", "lemma:2"])
        theorem = store.asset("theorem:1")
        assert theorem is not None
        self.assertEqual(theorem["label"], "Theorem 1 (Bound)")
        self.assertIn("Proof. The claim follows", theorem["content"])
        self.assertEqual(len(theorem["mentions"]), 1)
        lemma = store.asset("lemma:2")
        assert lemma is not None
        self.assertNotIn("Proof", lemma["content"])

    def test_booktabs_table_with_body_type_caption(self):
        builder = PaperBuilder()
        builder.new_page()
        builder.heading("1. Results")
        builder.paragraph(filler(1, 3))
        builder.paragraph("Table S2 compares the three methods on both datasets.")
        builder.booktabs("TABLE S.2:", "Accuracy of the compared methods.",
                         [["Method", "Dataset A", "Dataset B"], ["Baseline", "71.2", "64.0"], ["Ours", "78.9", "70.3"]])
        builder.paragraph(filler(2, 3))
        store = DocumentStore.open(builder.save(self.tmp_path / "booktabs.pdf"))
        table = store.asset("table:S2")
        assert table is not None
        self.assertEqual((table["method"], table["confidence"], table["content_format"]),
                         ("caption+rules", "medium", "markdown"))
        self.assertEqual(table["content"].splitlines()[0], "| Method | Dataset A | Dataset B |")
        self.assertIn("| Ours | 78.9 | 70.3 |", table["content"])
        self.assertEqual(len(table["mentions"]), 1)
        captions = [p["text"] for p in store.paragraphs() if p["kind"] == "caption"]
        self.assertEqual(captions, ["TABLE S.2: Accuracy of the compared methods."])


@unittest.skipUnless(REAL_WORKSPACE and ACCESS.is_file(), "set CREPE_READER_CORPUS to a folder with papers/")
class TestRealAccessAssets(IsolatedTestCase):
    def test_asset_counts_and_regions(self):
        self.addCleanup(close_all)
        store = DocumentStore.open(ACCESS)
        self.assertEqual(numbered(ids(store, "figure")), [f"figure:{k}" for k in range(1, 9)])
        self.assertEqual(numbered(ids(store, "table")), [f"table:{k}" for k in range(1, 8)])
        self.assertEqual(numbered(ids(store, "equation")), [f"equation:{k}" for k in range(1, 7)])
        self.assertEqual(ids(store, "algorithm"), ["algorithm:1", "algorithm:2"])
        self.assertEqual(numbered(ids(store, "reference")), [f"reference:{k}" for k in range(1, 21)])
        figures = store.assets("figure")
        self.assertGreaterEqual(sum(1 for a in figures if a["method"] == "caption+image"), 7)
        tables = store.assets("table")
        self.assertTrue(all(a["method"] == "caption+table" and a["content"].startswith("| ") for a in tables))
        first = store.asset("table:1")
        assert first is not None
        self.assertEqual(first["content"].splitlines()[:3], ["| λ | SR | Comput. time (h) |", "|---|---|---|",
                                                              "| 2 | -21775 | 6.47 |"])
        for algorithm_id in ("algorithm:1", "algorithm:2"):
            algorithm = store.asset(algorithm_id)
            assert algorithm is not None
            self.assertGreaterEqual(algorithm["content"].count("\n"), 5)
        self.assertGreaterEqual(sum(1 for a in tables if a["cited"]), 5)
        self.assertGreaterEqual(sum(1 for a in store.assets("reference") if a["cited"]), 15)


if __name__ == "__main__":
    unittest.main()
