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
    def test_eight_tools(self):
        names = {t.name for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(names, {"open_document", "read_pages", "read_section", "search_document", "list_assets",
                                 "get_asset", "render_page_png", "document_to_markdown"})


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

    def test_render_page_png(self):
        out = self.tmp_path / "p.png"
        info = json.loads(data("render_page_png", path=self.pdf, page=2, output_path=str(out)))
        self.assertTrue(out.read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(info["page"], 2)
        image = call("render_page_png", path=self.pdf, page=1)
        self.assertEqual(image.content[0].type, "image")
        with self.assertRaises(ToolError):
            call("render_page_png", path=self.pdf, page=99)

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
        for path, fragment in (("relative.pdf", "absolute"), ("/nowhere/x.pdf", "No such file"),
                               (str(self.tmp_path / "x.exe"), "not supported")):
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
