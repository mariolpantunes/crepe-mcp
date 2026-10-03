"""Scale invariance: the same layout at another body size and paper size yields the same structure."""

import unittest

from pdf_fixtures import IsolatedTestCase, build_fixture

from crepe_mcp.reader.store import DocumentStore, close_all

VARIANTS = (
    ("ieee_single", "ieee_single_8pt_letter"),
    ("ieee_single", "ieee_single_12pt"),
    ("em_revision", "em_revision_12pt"),
)


def summary(store: DocumentStore) -> dict[str, object]:
    pages = range(1, store.page_count + 1)
    return {
        "parts": [(s["kind"], s["first_page"], s["last_page"]) for s in store.segments()],
        "round": store.structure()["round"],
        "outline": [(s["level"], s["number"], s["title"], s["page"]) for s in store.outline()],
        "assets": [(a["id"], a["page"], a["method"]) for a in store.assets()],
        "cited": sorted((a["id"], a["cited"]) for a in store.assets()),
        "header_lines": [len(store.lines(p, ("header",))) for p in pages],
        "footer_lines": [len(store.lines(p, ("footer",))) for p in pages],
        "numbered_pages": [bool(store.lines(p, ("linenumber",))) for p in pages],
        "text": " ".join(" ".join(p["text"].split()) for p in store.paragraphs()),
    }


class TestScaleInvariance(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)

    def test_variants_match_the_base_layout(self):
        for base_name, variant_name in VARIANTS:
            base = DocumentStore.open(build_fixture(base_name, self.tmp_path))
            variant = DocumentStore.open(build_fixture(variant_name, self.tmp_path))
            self.assertNotEqual(base.meta()["body_size"], variant.meta()["body_size"], variant_name)
            expected, actual = summary(base), summary(variant)
            for key, value in expected.items():
                self.assertEqual(actual[key], value, f"{variant_name}: {key}")


if __name__ == "__main__":
    unittest.main()
