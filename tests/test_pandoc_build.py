"""tests/test_pandoc_build.py — a document store built from a hand-made pandoc AST (no pandoc needed).

The AST stands in for `pandoc -t json`, so the store builder, the reading tools and the search run on a CI runner
that has no pandoc.

Run with: python -m unittest discover -s tests -p "test_pandoc_build.py" -v
"""

import unittest
from pathlib import Path
from unittest import mock

from pdf_fixtures import IsolatedTestCase

from crepe_mcp.reader import pandoc_reader
from crepe_mcp.reader.common import ReaderError
from crepe_mcp.reader.reading import READ_BUDGET, read_pages, read_section, search
from crepe_mcp.reader.store import DocumentStore, close_all

ATTR = ["", [], []]


def words(text: str) -> list[dict]:
    inlines: list[dict] = []
    for index, word in enumerate(text.split(" ")):
        if index:
            inlines.append({"t": "Space"})
        inlines.append({"t": "Str", "c": word})
    return inlines


def para(text: str) -> dict:
    return {"t": "Para", "c": words(text)}


def header(level: int, text: str) -> dict:
    return {"t": "Header", "c": [level, ATTR, words(text)]}


def cell(text: str) -> list:
    return [ATTR, {"t": "AlignDefault"}, 1, 1, [{"t": "Plain", "c": words(text)}]]


def table(caption: str, head: list[str], body: list[list[str]]) -> dict:
    def row(values: list[str]) -> list:
        return [ATTR, [cell(v) for v in values]]

    short = [{"t": "Plain", "c": words(caption)}]
    return {
        "t": "Table",
        "c": [
            ATTR,
            [None, short],
            [],
            [ATTR, [row(head)]],
            [[ATTR, 0, [], [row(v) for v in body]]],
            [ATTR, []],
        ],
    }


def bullets(*items: list[dict]) -> dict:
    return {"t": "BulletList", "c": [list(item) for item in items]}


# a sentence of 12 words, about 70 characters, repeated to reach any length
SENTENCE = "The proposed method is evaluated against three baselines on two public datasets."


def sentences(count: int) -> str:
    return " ".join([SENTENCE] * count)


AST = {
    "meta": {"title": {"t": "MetaInlines", "c": words("A Study of Baselines")}},
    "blocks": [
        header(1, "Introduction"),
        para(sentences(3)),
        header(1, "Method"),
        para("We describe the pipeline. Table 1 lists the scores."),
        bullets(
            [
                {"t": "Plain", "c": words("first step")},
                bullets([{"t": "Plain", "c": words("nested detail")}]),
            ],
            [{"t": "Plain", "c": words("second step")}],
        ),
        {"t": "CodeBlock", "c": [ATTR, "def run():\n    return 1"]},
        table("Table 1. Scores per system", ["System", "Score"], [["alpha", "90"], ["beta", "80"]]),
        {"t": "LineBlock", "c": [words("line one"), words("line two")]},
        {"t": "BlockQuote", "c": [para("quoted wisdom")]},
        {"t": "DefinitionList", "c": [[words("term"), [[para("its definition")]]]]},
        header(2, "Details"),
        para(sentences(40)),
        header(1, "Results"),
        para("A paragraph " + "without a break " * 900),
    ],
}


class PandocBuildTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(close_all)
        self.source = self.tmp_path / "study.md"
        self.source.write_text("placeholder\n", encoding="utf-8")
        for target, value in (
            (pandoc_reader, "parse"),
            (pandoc_reader, "pandoc_version"),
        ):
            patcher = mock.patch.object(target, value, return_value=AST if value == "parse" else "pandoc 3.1")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = DocumentStore.open(self.source)

    def test_store_has_pages_sections_and_metadata(self):
        self.assertGreaterEqual(self.store.page_count, 2)
        titles = [s["title"] for s in self.store.outline()]
        self.assertEqual(titles, ["Introduction", "Method", "Details", "Results"])
        self.assertEqual(self.store.meta()["doc_title"], "A Study of Baselines")
        self.assertEqual(self.store.meta()["format"], "markdown")

    def test_assets_are_numbered_and_mentioned(self):
        kinds = {a["id"]: a for a in self.store.assets()}
        self.assertIn("table:1", kinds)
        self.assertIn("listing:1", kinds)
        self.assertIn("| alpha | 90 |", kinds["table:1"]["content"])
        self.assertTrue(self.store.asset("table:1"))

    def test_nested_list_text_is_stored_once(self):
        texts = [p["text"] for p in self.store.paragraphs()]
        self.assertEqual(sum(1 for t in texts if "nested detail" in t), 1)
        self.assertIn("- first step", texts)

    def test_line_block_quote_and_definition_are_text(self):
        text = "\n".join(p["text"] for p in self.store.paragraphs())
        for expected in ("line one\nline two", "quoted wisdom", "term: its definition"):
            self.assertIn(expected, text)

    def test_read_pages_returns_text_under_a_status_line(self):
        reply, _ = read_pages(self.store, 1, 1)
        self.assertIn("Introduction", reply)
        with self.assertRaises(ReaderError):
            read_pages(self.store, 1, self.store.page_count + 1)

    def test_read_section_by_heading_and_by_unknown_heading(self):
        reply = read_section(self.store, "Method", 1, self.store.page_count)
        self.assertIn("Section", reply)
        self.assertIn("We describe the pipeline", reply)
        self.assertIn("next: none", reply)
        with self.assertRaises(ReaderError) as caught:
            read_section(self.store, "Nowhere", 1, self.store.page_count)
        self.assertIn("Headings:", str(caught.exception))

    def test_read_section_splits_a_huge_paragraph_with_a_cursor(self):
        pages = self.store.page_count
        collected = ""
        cursor = None
        for _ in range(10):
            reply = read_section(self.store, "Results", 1, pages, cursor)
            status, _, body = reply.partition("\n\n")
            collected += body
            if "next: none" in status:
                break
            cursor = status.split("cursor='")[1].split("'")[0]
            self.assertLessEqual(len(body), READ_BUDGET)
        else:
            self.fail("the section never ended")
        self.assertGreater(len(collected), READ_BUDGET)
        with self.assertRaises(ReaderError):
            read_section(self.store, "Results", 1, pages, "s99")

    def test_search_finds_a_word_prefix_and_reports_none(self):
        hit = search(self.store, "baselin", 1, self.store.page_count, 5)
        self.assertGreater(hit["total_hits"], 0)
        self.assertIn("section", hit["hits"][0])
        miss = search(self.store, "zzzz", 1, self.store.page_count, 5)
        self.assertEqual(miss["total_hits"], 0)
        self.assertIn("hint", miss)

    def test_empty_document_is_refused(self):
        empty = self.tmp_path / "empty.md"
        empty.write_text("nothing readable in this one\n", encoding="utf-8")
        with mock.patch.object(pandoc_reader, "parse", return_value={"meta": {}, "blocks": []}):
            with self.assertRaises(ReaderError):
                DocumentStore.open(empty)

    def test_a_source_that_is_not_there_is_not_opened(self):
        with self.assertRaises((ReaderError, OSError)):
            DocumentStore.open(Path(self.tmp_path / "missing.md"))


if __name__ == "__main__":
    unittest.main()
