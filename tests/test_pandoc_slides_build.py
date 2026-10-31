"""tests/test_pandoc_slides_build.py — a slide deck store and the figure helpers from a hand-made pandoc AST.

Same idea as test_pandoc_build.py: the AST stands in for `pandoc -t json`, so the deck logic is covered on a runner
that has no pandoc.

Run with: python -m unittest discover -s tests -p "test_pandoc_slides_build.py" -v
"""

import json
import os
import unittest
from unittest import mock

import pymupdf
from fastmcp.exceptions import ToolError
from pdf_fixtures import IsolatedTestCase
from test_pandoc_build import ATTR, header, para, words
from test_reader_server import call, data

from crepe_mcp.reader import figures, pandoc_reader
from crepe_mcp.reader.common import ReaderError
from crepe_mcp.reader.store import DocumentStore, close_all

TIKZ = (
    "\\begin{tikzpicture}\n"
    "  \\node[fillbox=cblue, text width=2cm] (a) {\\textbf{linear, probabilistic}\\\\ Classes 03--04};\n"
    "  \\node[right=8mm of a] (b) {tree};\n"
    "\\end{tikzpicture}"
)


def image(url: str, alt: str = "") -> dict:
    return {"t": "Para", "c": [{"t": "Image", "c": [ATTR, words(alt), [url, ""]]}]}


def notes(text: str) -> dict:
    return {"t": "Div", "c": [["", ["notes"], []], [para(text)]]}


def frames(count: int) -> list[dict]:
    return [block for n in range(3, count + 3) for block in (header(2, f"Filler {n}"), para(f"Short text {n}."))]


DECK = {
    "meta": {
        "title": {"t": "MetaInlines", "c": words("Course")},
        "subtitle": {"t": "MetaInlines", "c": words("Class 07")},
    },
    "blocks": [
        header(1, "Section A"),
        header(2, "Diagram frame"),
        {"t": "RawBlock", "c": ["latex", TIKZ]},
        {"t": "RawBlock", "c": ["latex", "\\vspace{1cm} some leftover text"]},
        {"t": "RawBlock", "c": ["html", "<!-- defines: x -->"]},
        header(2, "Figure frame"),
        image("plots/a.pdf"),
        notes("Remember the overfit."),
        header(1, "Section B"),
        header(2, "Last frame"),
        para("Closing words."),
        *frames(2),
    ],
}
PROSE = {"meta": {}, "blocks": [header(1, "A"), header(2, "B"), para("x"), header(2, "C"), para("y")]}


class TestSlideLevel(unittest.TestCase):
    def test_a_short_marked_markdown_file_is_a_deck(self):
        self.assertEqual(pandoc_reader.slide_level(DECK, "markdown"), 2)

    def test_other_formats_few_frames_and_unmarked_files_are_not(self):
        self.assertEqual(pandoc_reader.slide_level(DECK, "docx"), 0)
        self.assertEqual(pandoc_reader.slide_level(PROSE, "markdown"), 0)
        plain = {
            "meta": {},
            "blocks": [b for b in DECK["blocks"] if b["t"] in ("Header", "Para") and "Image" not in str(b)],
        }
        self.assertEqual(pandoc_reader.slide_level(plain, "markdown"), 0, "no figure, notes, columns or raw LaTeX")

    def test_long_frames_make_prose(self):
        long = {"meta": DECK["meta"], "blocks": [*DECK["blocks"], para("word " * 9000)]}
        self.assertEqual(pandoc_reader.slide_level(long, "markdown"), 0)

    def test_slide_level_in_the_metadata_decides(self):
        meta = {"slide-level": {"t": "MetaString", "c": "1"}}
        self.assertEqual(pandoc_reader.slide_level({"meta": meta, "blocks": PROSE["blocks"]}, "markdown"), 1)


class TestTikz(unittest.TestCase):
    def test_latex_text_drops_environments_spacing_and_commands(self):
        source = (
            "\\begin{center}\n\\vspace{8mm}\n{\\Huge\\bfseries DEMO R1:}\\\\[5mm]\n"
            "{\\ttfamily 01\\_x.ipynb}\n\\end{center}"
        )
        self.assertEqual(pandoc_reader.latex_text(source), "DEMO R1:\n01_x.ipynb")

    def test_labels_skip_options_and_tex_commands(self):
        self.assertEqual(pandoc_reader.tikz_labels(TIKZ), ["linear, probabilistic / Classes 03--04", "tree"])

    def test_an_unclosed_node_is_skipped(self):
        self.assertEqual(pandoc_reader.tikz_labels("\\node (a) {broken"), [])


class TestDeckStore(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)
        self.root = self.tmp_path / "deck"
        (self.root / "assets" / "plots").mkdir(parents=True)
        (self.root / "defaults.yml").write_text("resource-path:\n  - assets\n", encoding="utf-8")
        pdf = pymupdf.open()
        pdf.new_page(width=100, height=60)
        pdf.save(self.root / "assets" / "plots" / "a.pdf")
        self.deck = self.root / "deck.md"
        self.deck.write_text("placeholder\n", encoding="utf-8")
        config = self.tmp_path / "reader.json"
        config.write_text(json.dumps({"images": {"enabled": True}}), encoding="utf-8")
        for patcher in (
            mock.patch.object(pandoc_reader, "parse", return_value=DECK),
            mock.patch.object(pandoc_reader, "pandoc_version", return_value="pandoc 3.1"),
            mock.patch.dict(os.environ, {"CREPE_READER_CONFIG": str(config)}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = DocumentStore.open(self.deck)

    def test_pages_are_frames_and_a_section_heading_joins_its_first_frame(self):
        self.assertEqual(self.store.page_count, 5)
        pages = {p["page"]: p["text"] for p in self.store.pages()}
        self.assertTrue(pages[3].startswith("# Section B\n\n## Last frame"))

    def test_diagram_is_an_item_with_its_labels_and_stray_raw_latex_is_a_warning(self):
        info = data("open_document", path=str(self.deck))
        self.assertEqual((info["page_unit"], info["subtitle"]), ("slide", "Class 07"))
        self.assertEqual(info["numbered_items"]["diagram"], 1)
        self.assertNotIn("uncited_items", info)
        self.assertNotIn("warnings", info)
        self.assertIn("some leftover text", data("read_pages", path=str(self.deck), first_page=1, last_page=1))
        detail = data("get_asset", path=str(self.deck), asset="diagram:1")
        self.assertIn('"frame": "Diagram frame"', detail)
        self.assertIn("tree", detail)
        listed = data("list_assets", path=str(self.deck))
        self.assertNotIn("uncited", listed)
        self.assertEqual({i["frame"] for i in listed["items"]}, {"Diagram frame", "Figure frame"})

    def test_figure_is_captioned_by_its_frame_and_attached(self):
        result = call("get_asset", path=str(self.deck), asset="figure:1", include_image=True)
        text = json.loads(result.content[0].text)
        self.assertEqual((text["caption"], text["image"]), ("Figure frame", "attached"))
        self.assertTrue(text["resolved_path"].endswith("assets/plots/a.pdf"))
        self.assertEqual(sum(b.type == "image" for b in result.content), 1)

    def test_notes_are_read_only_on_request(self):
        plain = data("read_pages", path=str(self.deck), first_page=2, last_page=2)
        self.assertNotIn("overfit", plain)
        self.assertIn(
            "[speaker notes]\nRemember the overfit.",
            data("read_pages", path=str(self.deck), first_page=2, last_page=2, notes=True),
        )
        self.assertEqual(self.store.notes(1, 1), {})

    def test_a_section_read_keeps_frame_headings_and_skips_notes(self):
        text = data("read_section", path=str(self.deck), heading="Section A")
        self.assertIn("## Figure frame", text)
        self.assertNotIn("overfit", text)

    def test_search_gives_the_slide(self):
        hit = data("search_document", path=str(self.deck), query="Closing")["hits"][0]
        self.assertEqual((hit["page"], hit["section"]), (3, "Last frame"))

    def test_render_page_image_still_refuses_the_deck_itself(self):
        with self.assertRaises(ToolError):
            call("render_page_image", path=str(self.deck), page=1)


class TestFigureFiles(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.doc = self.tmp_path / "d" / "deck.md"
        (self.tmp_path / "d" / "img").mkdir(parents=True)
        self.doc.write_text("x", encoding="utf-8")
        (self.tmp_path / "d" / "img" / "a.png").write_bytes(b"not an image")

    def test_url_of_a_figure_content(self):
        self.assertEqual(figures.figure_url("![alt (1)](plots/a.pdf)"), "plots/a.pdf")
        self.assertEqual(figures.figure_url('![alt](plots/a.pdf "title")'), "plots/a.pdf")
        self.assertEqual(figures.figure_url("![alt]()"), "")

    def test_only_relative_local_images_inside_the_roots_are_followed(self):
        found, note = figures.resolve_figure(self.doc, "img/a.png")
        self.assertEqual((found, note), ((self.tmp_path / "d" / "img" / "a.png").resolve(), "resolved"))
        for url, word in (
            ("", "no image"),
            ("https://x.org/a.png", "remote"),
            ("/etc/a.png", "absolute"),
            ("a.txt", "not a"),
            ("img/b.png", "missing"),
        ):
            with self.subTest(url=url):
                self.assertIn(word, figures.resolve_figure(self.doc, url)[1])
        with mock.patch.dict(os.environ, {"CREPE_READER_ROOTS": str(self.tmp_path / "other")}):
            self.assertIn("missing", figures.resolve_figure(self.doc, "img/a.png")[1])

    def test_defaults_file_resource_path_forms(self):
        (self.tmp_path / "d" / "defaults.yml").write_text("resource-path: img\n", encoding="utf-8")
        self.assertEqual(figures.resolve_figure(self.doc, "a.png")[1], "resolved")
        (self.tmp_path / "d" / "defaults.yml").write_text("resource-path: [unclosed\n", encoding="utf-8")
        self.assertEqual(figures.search_roots(self.doc), [self.tmp_path / "d"])

    def test_unreadable_image_is_an_agent_error(self):
        with self.assertRaises(ReaderError):
            figures.image_png(self.tmp_path / "d" / "img" / "a.png", 100)


if __name__ == "__main__":
    unittest.main()
