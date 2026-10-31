"""tests/test_reader_slides.py — crepe-reader on a pandoc/beamer Markdown deck (issues I1-I10 of the slides note).

The deck is tests/fixtures/slides/deck.md: 8 frames in 2 sections, a TikZ block, PDF, PNG and SVG figures (one missing),
speaker notes, an HTML comment, columns, a display equation and a table. Figures are written into a copy of the
fixture folder, so no binary is committed.

Run with: python -m unittest discover -s tests -p "test_reader_slides.py" -v
"""

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

import pymupdf
from fastmcp.exceptions import ToolError
from test_reader_server import ReaderTestCase, call, data

FIXTURE = Path(__file__).parent / "fixtures" / "slides"
FRAMES = 8


def write_figures(root: Path) -> None:
    plots = root / "assets" / "plots" / "07"
    plots.mkdir(parents=True)
    pdf = pymupdf.open()
    pdf.new_page(width=200, height=120).insert_text((20, 60), "scores")
    pdf.save(plots / "e1_scores.pdf")
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    pixmap.clear_with(200)
    pixmap.save(plots / "e3_bagging.png")
    (plots / "e4_boost.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"><rect width="40" height="30"/></svg>',
        encoding="utf-8",
    )


def image_blocks(result) -> list:
    return [block for block in result.content if block.type == "image"]


@unittest.skipUnless(shutil.which("pandoc"), "pandoc is required")
class TestSlideDeck(ReaderTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp_path / "slides"
        shutil.copytree(FIXTURE, self.root)
        write_figures(self.root)
        self.deck = str(self.root / "deck.md")
        config = self.tmp_path / "reader.json"
        config.write_text(json.dumps({"images": {"enabled": True}}), encoding="utf-8")
        patched = mock.patch.dict(os.environ, {"CREPE_READER_CONFIG": str(config)})
        patched.start()
        self.addCleanup(patched.stop)

    # I1 diagrams are kept, not dropped
    def test_tikz_block_is_a_diagram_item_with_its_labels(self):
        listed = data("list_assets", path=self.deck, kind="diagram")
        self.assertEqual([item["id"] for item in listed["items"]], ["diagram:1"])
        detail = data("get_asset", path=self.deck, asset="diagram:1")
        self.assertIn("linear, probabilistic", detail)
        self.assertIn("Class 06", detail)
        self.assertIn("[diagram 1]", data("read_section", path=self.deck, heading="Where We Left Off"))

    # I2 figure paths are resolved through the resource path and the image is attached
    def test_figures_resolve_and_attach_for_pdf_png_and_svg(self):
        for number, name in ((1, "e1_scores.pdf"), (2, "e3_bagging.png"), (3, "e4_boost.svg")):
            with self.subTest(name=name):
                result = call("get_asset", path=self.deck, asset=f"figure:{number}", include_image=True)
                self.assertEqual(len(image_blocks(result)), 1)
                self.assertIn(name, result.content[0].text)
                self.assertIn('"resolved_path"', result.content[0].text)

    def test_a_missing_figure_is_reported_not_silent(self):
        result = call("get_asset", path=self.deck, asset="figure:4", include_image=True)
        self.assertEqual(image_blocks(result), [])
        self.assertIn("missing", result.content[0].text)

    # I3 a figure file can be rendered directly
    def test_render_page_image_accepts_a_figure_file(self):
        for name in ("e1_scores.pdf", "e3_bagging.png"):
            with self.subTest(name=name):
                result = call("render_page_image", path=str(self.root / "assets" / "plots" / "07" / name), page=1)
                self.assertEqual(len(image_blocks(result)), 1)

    def test_render_page_image_still_refuses_a_deck_without_render_slides(self):
        with self.assertRaises(ToolError):
            call("render_page_image", path=self.deck, page=1)

    # I4 one frame is one page
    def test_pages_are_frames(self):
        info = data("open_document", path=self.deck)
        self.assertEqual(info["page_unit"], "slide")
        self.assertEqual(info["pages"], FRAMES)
        second = data("read_pages", path=self.deck, first_page=2, last_page=2)
        self.assertIn("Score Curves", second)
        self.assertNotIn("Where We Left Off", second)
        self.assertNotIn("Bagging in Pictures", second)

    # I5 a level-1 section keeps its frame headings
    def test_section_read_keeps_frame_headings(self):
        text = data("read_section", path=self.deck, heading="Combining Models")
        for frame in ("Two Columns", "Voting Rule", "Error Table"):
            self.assertIn(f"## {frame}", text)

    # I6 captions and uncited noise
    def test_deck_figures_have_captions_and_nothing_is_uncited(self):
        listed = data("list_assets", path=self.deck, kind="figure")
        captions = {item["id"]: item.get("caption", "") for item in listed["items"]}
        self.assertTrue(captions["figure:1"].startswith("Scores of one model"))
        self.assertTrue(captions["figure:2"].startswith("Bagging in Pictures"), "no alt text: the frame title")
        self.assertNotIn("uncited", listed)

    # I7 title block
    def test_title_block_is_reported(self):
        info = data("open_document", path=self.deck)
        self.assertEqual(info["title"], "Sample course")
        self.assertEqual(info["subtitle"], "Class 07 — Ensemble Models")
        self.assertEqual(info["author"], "A. Teacher")
        self.assertEqual(str(info["date"]), "2026-10-31")

    # I8 every asset knows its frame
    def test_assets_carry_their_frame(self):
        detail = data("get_asset", path=self.deck, asset="equation:1")
        self.assertIn("mode", detail)
        self.assertIn('"frame": "Voting Rule"', detail)
        table = data("get_asset", path=self.deck, asset="table:1")
        self.assertIn('"frame": "Error Table"', table)

    # I9 notes, comments and columns
    def test_notes_comments_and_columns(self):
        plain = data("read_pages", path=self.deck, first_page=2, last_page=2)
        self.assertNotIn("one model overfits", plain)
        with_notes = data("read_pages", path=self.deck, first_page=2, last_page=2, notes=True)
        self.assertIn("one model overfits", with_notes)
        everything = data("read_pages", path=self.deck, first_page=1, last_page=FRAMES)
        self.assertNotIn("defines:", everything)
        self.assertIn("left bullet", everything)
        self.assertIn("right bullet", everything)

    # I10 search gives the frame
    def test_search_hits_name_the_slide(self):
        hits = data("search_document", path=self.deck, query="majority vote")
        self.assertEqual(hits["total_hits"], 1)
        hit = hits["hits"][0]
        self.assertEqual(hit["page"], 7)
        self.assertIn("Voting Rule", str(hit))


if __name__ == "__main__":
    unittest.main()
