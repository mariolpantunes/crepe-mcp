"""tests/test_reader_server.py — the crepe-reader tools on PDF fixtures and on pandoc-read formats.

Run with: python -m unittest discover -s tests -p "test_reader_server.py" -v
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from pdf_fixtures import IsolatedTestCase, build_fixture

from crepe_mcp.exporter import find_libreoffice
from crepe_mcp.reader.store import close_all
from crepe_mcp.server_reader import mcp

SAMPLE = """\
---
title: Sample report
author: A. Writer
---

# 1 Introduction

This report studies sorting. See Table 1 and Figure 1 for the results, and Equation 1 for the bound.

## 1.1 Method

We sort lists of integers.

$$ T(n) = O(n \\log n) $$

Table: Table 1: Runtime in ms.

| size | time |
|------|------|
| 10   | 1    |
| 100  | 12   |

![Figure 1: Runtime plot](plot.png)

```python
def sort(x):
    return sorted(x)
```

# 2 Conclusion

- Sorting is fast
- Tables help
"""
PANDOC_FORMATS = ["md", "docx", "odt", "html", "tex", "epub"]


def call(tool: str, **args):
    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(tool, args)
            return result

    return asyncio.run(run())


def data(tool: str, **args):
    result = call(tool, **args)
    return result.data if result.data is not None else result.content[0].text


class ReaderTestCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)


class TestToolList(unittest.TestCase):
    def test_nine_tools(self):
        names = {t.name for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(
            names,
            {
                "open_document",
                "read_pages",
                "read_section",
                "search_document",
                "list_assets",
                "get_asset",
                "list_changes",
                "render_page_image",
                "document_to_markdown",
            },
        )


class TestPdf(ReaderTestCase):
    def setUp(self):
        super().setUp()
        self.pdf = str(build_fixture("ieee_single", self.tmp_path))

    def test_open_document_reports_parts_and_outline(self):
        info = data("open_document", path=self.pdf)
        self.assertEqual(info["format"], "pdf")
        self.assertEqual(info["pages"], 17)
        self.assertEqual([p["kind"] for p in info["parts"]], ["cover", "manuscript"])
        self.assertTrue(info["outline"])
        self.assertNotIn(str(self.tmp_path), str(info))

    def test_read_pages_follows_the_cursor_to_the_end(self):
        seen = []
        cursor = None
        for _ in range(40):
            text = data("read_pages", path=self.pdf, part="manuscript", **({"cursor": cursor} if cursor else {}))
            seen.append(text)
            line = text.splitlines()[0]
            cursor = line.split("next: ")[1].strip()
            if cursor.startswith("none"):
                break
            cursor = cursor.split()[0]
        self.assertTrue(cursor.startswith("none"))
        self.assertTrue(all(len(t) < 14_000 for t in seen))

    def test_search_assets_and_markdown(self):
        hits = data("search_document", path=self.pdf, query="Table")
        self.assertGreater(hits["total_hits"], 0)
        listing = data("list_assets", path=self.pdf)
        self.assertTrue(listing["items"])
        first = listing["items"][0]["id"]
        asset = data("get_asset", path=self.pdf, asset=first)
        self.assertIn(first, asset)
        out = self.tmp_path / "out" / "paper.md"
        written = data("document_to_markdown", path=self.pdf, output_path=str(out), part="manuscript")
        self.assertEqual(written["method"], "extracted text")
        self.assertIn("\n# ", "\n" + out.read_text())

    def test_render_page_image_png_is_the_default(self):
        out = self.tmp_path / "p.png"
        info = json.loads(data("render_page_image", path=self.pdf, page=2, output_path=str(out)))
        self.assertTrue(out.read_bytes().startswith(b"\x89PNG"))
        self.assertEqual((info["page"], info["format"]), (2, "png"))
        image = call("render_page_image", path=self.pdf, page=1)
        self.assertEqual(image.content[0].type, "image")
        self.assertEqual(image.content[0].mimeType, "image/png")
        with self.assertRaises(ToolError):
            call("render_page_image", path=self.pdf, page=99)

    def test_render_page_image_jpeg(self):
        out = self.tmp_path / "p.jpg"
        info = json.loads(data("render_page_image", path=self.pdf, page=1, format="jpeg", output_path=str(out)))
        self.assertTrue(out.read_bytes().startswith(b"\xff\xd8\xff"))
        self.assertEqual(info["format"], "jpeg")
        image = call("render_page_image", path=self.pdf, page=1, format="jpeg")
        self.assertEqual(image.content[0].mimeType, "image/jpeg")

    def test_render_page_image_output_suffix_must_match_the_format(self):
        for fmt, name in (("png", "p.jpg"), ("jpeg", "p.png"), ("png", "p")):
            with self.subTest(fmt=fmt, name=name), self.assertRaises(ToolError):
                call("render_page_image", path=self.pdf, page=1, format=fmt, output_path=str(self.tmp_path / name))
        with self.assertRaises(ToolError):
            call("render_page_image", path=self.pdf, page=1, format="gif")

    def test_second_open_is_served_from_the_store(self):
        data("open_document", path=self.pdf)
        with mock.patch("crepe_mcp.reader.store._build", side_effect=AssertionError("rebuilt")):
            close_all()
            data("open_document", path=self.pdf)


@unittest.skipUnless(shutil.which("pandoc"), "pandoc is required")
class TestPandocFormats(ReaderTestCase):
    def setUp(self):
        super().setUp()
        source = self.tmp_path / "sample.md"
        source.write_text(SAMPLE, encoding="utf-8")
        self.files = {"md": source}
        for fmt in PANDOC_FORMATS[1:]:
            target = self.tmp_path / f"sample.{fmt}"
            args = ["pandoc", str(source), "-o", str(target)] + (["-s"] if fmt in ("html", "tex") else [])
            subprocess.run(args, check=True, capture_output=True, cwd=self.tmp_path)
            self.files[fmt] = target

    def test_outline_title_and_search_in_every_format(self):
        for fmt, path in self.files.items():
            with self.subTest(fmt=fmt):
                info = data("open_document", path=str(path))
                # ODT keeps its title as a styled paragraph and HTML as a level-1 heading of its own
                self.assertIn(info["title"], ("Sample report", "Introduction"))
                self.assertEqual(info["format"], {"tex": "latex", "md": "markdown"}.get(fmt, fmt))
                self.assertEqual(len(info["outline"]), 4 if fmt == "html" else 3)
                self.assertTrue(any("1.1 Method" in line for line in info["outline"]))
                self.assertEqual(data("search_document", path=str(path), query="sorting")["total_hits"], 2)
                self.assertIn("integers", data("read_section", path=str(path), heading="Method"))

    def test_tables_equations_and_code_become_numbered_items(self):
        info = data("open_document", path=str(self.files["md"]))
        self.assertEqual(info["numbered_items"], {"equation": 1, "figure": 1, "listing": 1, "table": 1})
        table = data("get_asset", path=str(self.files["md"]), asset="table:1")
        self.assertIn("| 100 | 12 |", table)
        self.assertIn("Runtime in ms", table)
        self.assertIn('"cited_count": 1', table)
        self.assertIn("O(n", data("get_asset", path=str(self.files["md"]), asset="equation:1"))
        docx = data("list_assets", path=str(self.files["docx"]), kind="table")
        self.assertEqual([i["id"] for i in docx["items"]], ["table:1"])

    def reviewed_docx(self) -> Path:
        source = self.tmp_path / "reviewed.md"
        source.write_text(
            "# Intro\n\n"
            'The method is [very]{.insertion author="Alice" date="2026-10-01T10:00:00Z"} fast and '
            '[slow]{.deletion author="Bob" date="2026-10-02T09:30:00Z"} simple.\n\n'
            "# Results\n\n"
            'Check [Needs a citation.]{.comment-start id="0" author="Carol" date="2026-10-03T08:00:00Z"}'
            'this claim[]{.comment-end id="0"} please.\n',
            encoding="utf-8",
        )
        target = self.tmp_path / "reviewed.docx"
        subprocess.run(["pandoc", str(source), "-o", str(target)], check=True, capture_output=True, cwd=self.tmp_path)
        return target

    def test_list_changes_reports_tracked_changes_and_comments(self):
        path = str(self.reviewed_docx())
        found = data("list_changes", path=path)
        self.assertEqual(found["counts"], {"insertion": 1, "deletion": 1, "comment": 1})
        self.assertEqual(found["authors"], ["Alice", "Bob", "Carol"])
        by_kind = {item["kind"]: item for item in found["items"]}
        self.assertEqual((by_kind["insertion"]["text"], by_kind["insertion"]["author"]), ("very", "Alice"))
        self.assertEqual((by_kind["deletion"]["text"], by_kind["deletion"]["section"]), ("slow", "Intro"))
        comment = by_kind["comment"]
        self.assertEqual(
            (comment["text"], comment["anchor"], comment["section"]), ("Needs a citation.", "this claim", "Results")
        )
        self.assertEqual(comment["date"], "2026-10-03T08:00:00Z")
        only = data("list_changes", path=path, kind="comment", max_items=1)
        self.assertEqual((only["matching"], only["returned"]), (1, 1))
        self.assertEqual(only["counts"]["insertion"], 1)

    def test_accepted_view_keeps_insertions_and_drops_deletions_and_comments(self):
        text = data("read_pages", path=str(self.reviewed_docx()))
        self.assertIn("very", text)
        self.assertNotIn("slow", text)
        self.assertNotIn("Needs a citation", text)

    def test_list_changes_is_for_docx_only(self):
        with self.assertRaises(ToolError):
            call("list_changes", path=str(self.files["odt"]))

    def test_repeated_caption_numbers_keep_their_own_ids(self):
        path = self.tmp_path / "repeat.html"
        path.write_text(
            "<html><body><h1>T</h1>"
            "<table><caption>Table 1: first</caption><tr><th>a</th></tr><tr><td>1</td></tr></table>"
            "<table><caption>Table 1: again</caption><tr><th>b</th></tr><tr><td>2</td></tr></table>"
            "<table><caption>Table 1: third</caption><tr><th>c</th></tr><tr><td>3</td></tr></table>"
            "</body></html>",
            encoding="utf-8",
        )
        ids = [item["id"] for item in data("list_assets", path=str(path), kind="table")["items"]]
        self.assertEqual(ids, ["table:1", "table:1-2", "table:1-3"])
        self.assertIn("| b |", data("get_asset", path=str(path), asset="table:1-2"))

    def test_latex_includes_are_not_followed(self):
        secret = self.tmp_path / "secret.txt"
        secret.write_text("SECRET-LINE-12345\n", encoding="utf-8")
        path = self.tmp_path / "inc.tex"
        path.write_text(
            "\\documentclass{article}\\begin{document}\\section{Intro}Visible text."
            f"\\input{{{secret}}}\\end{{document}}\n",
            encoding="utf-8",
        )
        self.assertIn("Visible text", data("read_pages", path=str(path)))
        self.assertNotIn("SECRET-LINE", data("read_pages", path=str(path)))
        out = self.tmp_path / "inc.out.md"
        data("document_to_markdown", path=str(path), output_path=str(out))
        self.assertNotIn("SECRET-LINE", out.read_text(encoding="utf-8"))

    def test_document_to_markdown_never_overwrites_by_accident(self):
        source = self.files["md"]
        before = source.read_text(encoding="utf-8")
        with self.assertRaises(ToolError):
            call("document_to_markdown", path=str(source), output_path=str(source), overwrite=True)
        self.assertEqual(source.read_text(encoding="utf-8"), before)
        target = self.tmp_path / "copy.md"
        data("document_to_markdown", path=str(self.files["docx"]), output_path=str(target))
        with self.assertRaises(ToolError) as caught:
            call("document_to_markdown", path=str(self.files["docx"]), output_path=str(target))
        self.assertIn("overwrite", str(caught.exception))
        target.write_text("old", encoding="utf-8")
        data("document_to_markdown", path=str(self.files["docx"]), output_path=str(target), overwrite=True)
        self.assertNotEqual(target.read_text(encoding="utf-8"), "old")

    def test_read_section_continues_with_a_cursor_through_a_huge_paragraph(self):
        words = [f"w{n}" for n in range(6000)]
        small = [f"s{n}" for n in range(1500)]
        path = self.tmp_path / "huge.md"
        path.write_text(
            "# Big\n\n" + " ".join(small) + "\n\n" + " ".join(words) + "\n\n# Next\n\nOther.\n", encoding="utf-8"
        )
        body: list[str] = []
        cursor = None
        for _ in range(20):
            args = {"cursor": cursor} if cursor else {}
            reply = data("read_section", path=str(path), heading="Big", **args)
            status, _, text = reply.partition("\n\n")
            body.append(text)
            if "next: none" in status:
                break
            cursor = status.split("cursor='")[1].split("'")[0]
        else:
            self.fail("the section never ended")
        self.assertGreater(len(body), 2)
        tokens = " ".join(body).split()
        self.assertEqual(tokens, ["Big", *small, *words])
        with self.assertRaises(ToolError):
            call("read_section", path=str(path), heading="Big", cursor="s99")
        with self.assertRaises(ToolError):
            call("read_section", path=str(path), heading="Big", cursor="garbage")

    def test_unsupported_formats_have_no_page_image(self):
        with self.assertRaises(ToolError) as caught:
            call("render_page_image", path=str(self.files["md"]), page=1)
        self.assertIn("document_to_markdown", str(caught.exception))

    def test_office_pages_need_libreoffice(self):
        with mock.patch("crepe_mcp.reader.page_image.find_libreoffice", return_value=None):
            with self.assertRaises(ToolError) as caught:
                call("render_page_image", path=str(self.files["docx"]), page=1)
        self.assertIn("LibreOffice", str(caught.exception))

    @unittest.skipUnless(find_libreoffice(), "LibreOffice is required")
    def test_office_pages_render_through_libreoffice_and_are_cached(self):
        from crepe_mcp.reader import page_image

        calls = []
        real = page_image.office_to_pdf

        def counting(*args, **kwargs):
            calls.append(args[1])
            return real(*args, **kwargs)

        slides = self.tmp_path / "slides.md"
        slides.write_text("# Deck\n\n## First slide\n\n- one\n- two\n\n## Second slide\n\nText.\n", encoding="utf-8")
        deck = self.tmp_path / "sample.pptx"
        subprocess.run(["pandoc", str(slides), "-o", str(deck)], check=True, capture_output=True, cwd=self.tmp_path)
        self.files["pptx"] = deck
        for fmt in ("docx", "odt", "pptx"):
            with self.subTest(fmt=fmt):
                out = self.tmp_path / f"{fmt}.png"
                with mock.patch.object(page_image, "office_to_pdf", counting):
                    info = json.loads(
                        data("render_page_image", path=str(self.files[fmt]), page=1, output_path=str(out))
                    )
                    self.assertTrue(out.read_bytes().startswith(b"\x89PNG"))
                    self.assertGreaterEqual(info["pages"], 1)
                    jpeg = call("render_page_image", path=str(self.files[fmt]), page=1, format="jpeg")
                    self.assertEqual(jpeg.content[0].mimeType, "image/jpeg")
                    with self.assertRaises(ToolError):
                        call("render_page_image", path=str(self.files[fmt]), page=info["pages"] + 1)
        self.assertEqual(len(calls), 3, "each file is converted once, later pages come from the cache")

    def test_logical_pages_for_long_text(self):
        long_text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(40))
        path = self.tmp_path / "long.md"
        path.write_text("# Long\n\n" + long_text, encoding="utf-8")
        info = data("open_document", path=str(path))
        self.assertGreater(info["pages"], 3)
        self.assertIn("logical", info["page_unit"])
        first = data("read_pages", path=str(path))
        self.assertTrue(first.splitlines()[0].startswith("Pages 1"))
        self.assertLess(len(first), 14_000)

    def test_document_to_markdown_uses_pandoc(self):
        out = self.tmp_path / "out.md"
        written = data("document_to_markdown", path=str(self.files["docx"]), output_path=str(out))
        self.assertEqual(written["method"], "pandoc (gfm)")
        self.assertIn("| size", out.read_text())


class TestInputChecks(ReaderTestCase):
    def test_bad_paths_are_agent_errors(self):
        for path, fragment in (
            ("relative.pdf", "absolute"),
            ("/nowhere/x.pdf", "No such file"),
            (str(self.tmp_path / "x.exe"), "not supported"),
        ):
            with self.subTest(path=path), self.assertRaises(ToolError) as caught:
                call("open_document", path=path)
            self.assertIn(fragment, str(caught.exception))

    def test_reader_roots_limit_the_readable_folders(self):
        pdf = build_fixture("ieee_single", self.tmp_path)
        with mock.patch.dict(os.environ, {"CREPE_READER_ROOTS": str(self.tmp_path / "elsewhere")}):
            with self.assertRaises(ToolError) as caught:
                call("open_document", path=str(pdf))
        self.assertIn("outside the folders", str(caught.exception))
        with mock.patch.dict(os.environ, {"CREPE_READER_ROOTS": str(self.tmp_path)}):
            self.assertEqual(data("open_document", path=str(pdf))["pages"], 17)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is required")
    def test_unknown_item_and_heading(self):
        path = self.tmp_path / "s.md"
        path.write_text(SAMPLE, encoding="utf-8")
        with self.assertRaises(ToolError):
            call("get_asset", path=str(path), asset="figure:9")
        with self.assertRaises(ToolError) as caught:
            call("read_section", path=str(path), heading="Nonexistent")
        self.assertIn("Headings", str(caught.exception))


class TestStdio(ReaderTestCase):
    def test_stdout_carries_only_protocol_messages(self):
        pdf = build_fixture("ieee_single", self.tmp_path)
        src = str(Path(__file__).resolve().parent.parent / "src")
        env = {**os.environ, "PYTHONPATH": src, "CREPE_SCRATCH_BASE": str(self.tmp_path / "stdio-scratch")}
        code = "from crepe_mcp.server_reader import main; main()"
        transport = StdioTransport(command=sys.executable, args=["-c", code], env=env)

        async def run():
            async with Client(transport) as client:
                return await client.call_tool("open_document", {"path": str(pdf)})

        self.assertEqual(asyncio.run(run()).data["pages"], 17)


if __name__ == "__main__":
    unittest.main()
