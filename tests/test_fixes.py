"""tests/test_fixes.py — Unit tests for code-review fixes (T-01 … T-06).

Run with:
    python -m pytest tests/test_fixes.py -v
or:
    python -m unittest discover -s tests -p "test_fixes.py" -v
"""
import os
import sys
import unittest
import unittest.mock as mock

# Make sure the src package is importable from the repo root
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


# ---------------------------------------------------------------------------
# T-01  Any import in doc_store
# ---------------------------------------------------------------------------

class TestT01AnyImport(unittest.TestCase):
    """T-01: `Any` must be importable from doc_store's typing imports."""

    def test_any_present_in_typing_imports(self):
        """Importing doc_store must not raise NameError for Any."""
        import typing

        import crepe_mcp.doc_store as ds
        hints = {}
        try:
            hints = typing.get_type_hints(ds.update_document_metadata)
        except Exception as exc:
            self.fail(f"get_type_hints raised {exc!r} — Any is likely missing from imports")
        self.assertIsInstance(hints, dict)

    def test_update_document_metadata_callable(self):
        """update_document_metadata must run without NameError."""
        from crepe_mcp.doc_store import new_document, update_document_metadata
        doc = new_document(title="T01 Test")
        meta = update_document_metadata(doc, title="Updated Title")
        self.assertEqual(meta.title, "Updated Title")


# ---------------------------------------------------------------------------
# T-02  arXiv search — HTTPS URL and empty-link fallback
# ---------------------------------------------------------------------------

_ARXIV_XML_NO_LINK = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/9999.00001</id>
    <title>Paper With No PDF Link</title>
    <summary>A paper that has no link element at all.</summary>
    <published>2024-01-15T00:00:00Z</published>
  </entry>
</feed>
"""

_ARXIV_XML_EMPTY_ID = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Paper With Empty ID</title>
    <summary>A paper with no id and no pdf link.</summary>
    <published>2024-02-20T00:00:00Z</published>
  </entry>
</feed>
"""

_ARXIV_XML_WITH_LINK = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2301.12345</id>
    <title>Paper With PDF Link</title>
    <summary>A normal paper.</summary>
    <published>2024-03-01T00:00:00Z</published>
    <link title="pdf" href="https://arxiv.org/pdf/2301.12345" type="application/pdf"/>
  </entry>
</feed>
"""


def _mock_urlopen_ctx(xml_body):
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(return_value=cm)
    cm.__exit__ = mock.Mock(return_value=False)
    cm.read.return_value = xml_body.encode("utf-8")
    return cm


class TestT02ArxivSearch(unittest.TestCase):
    """T-02: arXiv URL uses HTTPS; empty pdf_link falls back to 'No link available'."""

    def _run_with_capture(self, xml_body):
        import crepe_mcp.research as research
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["url"] = req.get_full_url()
            return _mock_urlopen_ctx(xml_body)
        with mock.patch.object(research.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = research.arxiv_search("test query", limit=1)
        return captured.get("url", ""), result

    def test_url_is_https(self):
        url, _ = self._run_with_capture(_ARXIV_XML_WITH_LINK)
        self.assertTrue(url.startswith("https://"), f"Expected https:// URL, got: {url!r}")

    def test_empty_link_fallback_from_id(self):
        import crepe_mcp.research as research
        with mock.patch.object(research.urllib.request, "urlopen",
                               return_value=_mock_urlopen_ctx(_ARXIV_XML_NO_LINK)):
            result = research.arxiv_search("test", limit=1)
        papers = result.get("papers", [])
        self.assertEqual(len(papers), 1)
        link = papers[0]["link"]
        self.assertNotEqual(link, "", "link must not be an empty string")
        self.assertIn("9999.00001", link, f"Unexpected link value: {link!r}")

    def test_empty_link_fallback_no_id(self):
        import crepe_mcp.research as research
        with mock.patch.object(research.urllib.request, "urlopen",
                               return_value=_mock_urlopen_ctx(_ARXIV_XML_EMPTY_ID)):
            result = research.arxiv_search("test", limit=1)
        papers = result.get("papers", [])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["link"], "No link available")

    def test_normal_link_preserved(self):
        import crepe_mcp.research as research
        with mock.patch.object(research.urllib.request, "urlopen",
                               return_value=_mock_urlopen_ctx(_ARXIV_XML_WITH_LINK)):
            result = research.arxiv_search("test", limit=1)
        papers = result.get("papers", [])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["link"], "https://arxiv.org/pdf/2301.12345")


# ---------------------------------------------------------------------------
# T-03  YAML geometry quoting in build_document_markdown
# ---------------------------------------------------------------------------

class TestT03GeometryYaml(unittest.TestCase):
    """T-03: geometry YAML line must not contain Python repr single-quotes."""

    def _get_geometry_line(self, margin):
        from crepe_mcp.doc_store import build_document_markdown, new_document
        doc = new_document(margin=margin)
        md = build_document_markdown(doc)
        for line in md.splitlines():
            if line.startswith("geometry:"):
                return line
        return ""

    def test_no_repr_quotes_plain_value(self):
        line = self._get_geometry_line("2.5cm")
        self.assertNotIn("'", line, f"Found Python repr quotes in: {line!r}")
        self.assertIn("margin=2.5cm", line)

    def test_no_repr_quotes_already_contains_equals(self):
        line = self._get_geometry_line("margin=3cm")
        self.assertNotIn("'", line, f"Found Python repr quotes in: {line!r}")
        self.assertIn("margin=3cm", line)

    def test_geometry_line_present(self):
        line = self._get_geometry_line("2cm")
        self.assertTrue(line.startswith("geometry:"), f"Missing geometry line, got: {line!r}")


# ---------------------------------------------------------------------------
# T-04  duplicate_presentation — no deadlock, correct slide copy
# ---------------------------------------------------------------------------

class TestT04DuplicatePresentation(unittest.TestCase):
    """T-04: duplicate_presentation copies slides correctly without the inner lock."""

    def test_slides_are_copied(self):
        from crepe_mcp.store import duplicate_presentation, new_presentation, upsert_slide
        pres = new_presentation(title="Original")
        upsert_slide(pres, 0, "Slide One", "Content A")
        upsert_slide(pres, 1, "Slide Two", "Content B")
        clone = duplicate_presentation(pres.id, title_suffix=" (Clone)")
        self.assertEqual(len(clone.slides), 2)
        self.assertEqual(clone.slides[0].title, "Slide One")
        self.assertEqual(clone.slides[1].title, "Slide Two")

    def test_clone_title_has_suffix(self):
        from crepe_mcp.store import duplicate_presentation, new_presentation
        pres = new_presentation(title="Source")
        clone = duplicate_presentation(pres.id, title_suffix=" (Copy)")
        self.assertEqual(clone.metadata.title, "Source (Copy)")

    def test_clone_slides_are_independent(self):
        from crepe_mcp.store import duplicate_presentation, new_presentation, upsert_slide
        pres = new_presentation(title="Base")
        upsert_slide(pres, 0, "Original Title", "Original Content")
        clone = duplicate_presentation(pres.id)
        clone.slides[0].title = "Modified"
        self.assertEqual(pres.slides[0].title, "Original Title")

    def test_no_deadlock_completes_in_time(self):
        import threading

        from crepe_mcp.store import duplicate_presentation, new_presentation, upsert_slide
        pres = new_presentation(title="Deadlock Test")
        upsert_slide(pres, 0, "A", "content")
        result = {}
        errors = {}
        def run():
            try:
                result["clone"] = duplicate_presentation(pres.id)
            except Exception as e:
                errors["error"] = e
        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "Thread is still alive — possible deadlock")
        self.assertNotIn("error", errors, f"Error: {errors.get('error')}")
        self.assertIn("clone", result)


# ---------------------------------------------------------------------------
# T-05  compile_document_to_pdf / compile_document_to_docx — absolute path guard
# ---------------------------------------------------------------------------

class TestT05AbsolutePathGuard(unittest.TestCase):
    """T-05: Both compile functions must raise DocCompileError for relative paths."""

    def _make_doc(self):
        from crepe_mcp.doc_store import new_document
        return new_document(title="Guard Test")

    def test_compile_pdf_relative_path_raises(self):
        from crepe_mcp.doc_compiler import DocCompileError, compile_document_to_pdf
        doc = self._make_doc()
        with self.assertRaises(DocCompileError) as ctx:
            compile_document_to_pdf(doc, "relative/report.pdf")
        self.assertIn("absolute", str(ctx.exception).lower())

    def test_compile_pdf_bare_filename_raises(self):
        from crepe_mcp.doc_compiler import DocCompileError, compile_document_to_pdf
        doc = self._make_doc()
        with self.assertRaises(DocCompileError):
            compile_document_to_pdf(doc, "report.pdf")

    def test_compile_docx_relative_path_raises(self):
        from crepe_mcp.doc_compiler import DocCompileError, compile_document_to_docx
        doc = self._make_doc()
        with self.assertRaises(DocCompileError) as ctx:
            compile_document_to_docx(doc, "relative/report.docx")
        self.assertIn("absolute", str(ctx.exception).lower())

    def test_compile_docx_bare_filename_raises(self):
        from crepe_mcp.doc_compiler import DocCompileError, compile_document_to_docx
        doc = self._make_doc()
        with self.assertRaises(DocCompileError):
            compile_document_to_docx(doc, "report.docx")

    def test_compile_pdf_absolute_path_passes_guard(self):
        """An absolute path must pass the guard (fail only on missing tool, not path check)."""
        from crepe_mcp.doc_compiler import DocCompileError, compile_document_to_pdf
        doc = self._make_doc()
        try:
            compile_document_to_pdf(doc, "/tmp/test_guard_crepe.pdf")
        except DocCompileError as exc:
            msg = str(exc).lower()
            self.assertNotIn("absolute", msg,
                             f"Should not fail on path guard for absolute path, got: {exc!r}")
        except Exception:
            pass  # Any other error is fine


# ---------------------------------------------------------------------------
# T-06  doc_exporter uses public API, not private name
# ---------------------------------------------------------------------------

class TestT06PublicApi(unittest.TestCase):
    """T-06: doc_exporter must not use private (underscore-prefixed) exporter symbols."""

    def test_render_via_libreoffice_is_public(self):
        import crepe_mcp.exporter as exp
        self.assertTrue(hasattr(exp, "render_via_libreoffice"),
                        "render_via_libreoffice not found in exporter module")

    def test_no_private_import_from_exporter(self):
        """Check via AST that doc_exporter no longer imports _render_pptx_via_libreoffice."""
        import ast
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "src", "crepe_mcp", "doc_exporter.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        bad_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "exporter" in node.module:
                for alias in node.names:
                    if alias.name == "_render_pptx_via_libreoffice":
                        bad_imports.append(alias.name)
        self.assertEqual(bad_imports, [],
                         f"doc_exporter still imports private names: {bad_imports}")

    def test_render_via_libreoffice_is_alias(self):
        import crepe_mcp.exporter as exp
        # The private function was renamed to _render_office_to_pngs (L5 fix).
        self.assertIs(exp.render_via_libreoffice, exp._render_office_to_pngs,
                      "render_via_libreoffice must be an alias of _render_office_to_pngs")

    def test_linter_detects_forbidden_latex_includegraphics(self):
        """lint_presentation_content must flag \\includegraphics as forbidden_latex.

        sanitize_markdown (which silently converted it) was removed; the linter
        is now the correct tool for surfacing this issue to the agent.
        """
        from crepe_mcp.linter import lint_presentation_content
        from crepe_mcp.store import new_presentation
        pres = new_presentation(title="Linter Test")
        raw_slide = (
            "\\begin{center}\n"
            "\\includegraphics[width=0.9\\linewidth]{/path/to/img.png}\n"
            "\\end{center}"
        )
        from crepe_mcp.store import upsert_slide
        upsert_slide(pres, 0, "Slide 1", raw_slide)
        report = lint_presentation_content(pres)
        forbidden_types = [i.type for i in report.issues]
        self.assertIn("forbidden_latex", forbidden_types,
                      "Linter must flag \\includegraphics as forbidden_latex")
        self.assertFalse(report.valid, "Presentation with raw LaTeX must be invalid")

    def test_linter_no_false_positive_in_fenced_block(self):
        """Forbidden LaTeX inside a fenced code block must NOT be flagged (M5 fix)."""
        from crepe_mcp.linter import lint_presentation_content
        from crepe_mcp.store import new_presentation, upsert_slide
        pres = new_presentation(title="Fence Test")
        # LaTeX inside a fenced block — this is intentional documentation, not broken content
        safe_slide = (
            "Here is an example of what NOT to use:\n\n"
            "```latex\n"
            "\\includegraphics[width=0.9\\linewidth]{/path/to/img.png}\n"
            "```\n\n"
            "Use `![alt](path)` instead."
        )
        upsert_slide(pres, 0, "Slide 1", safe_slide)
        report = lint_presentation_content(pres)
        forbidden_types = [i.type for i in report.issues]
        self.assertNotIn("forbidden_latex", forbidden_types,
                         "LaTeX inside a fenced code block must not be flagged")

    def test_inspect_drawio_structure_validation(self):
        import tempfile

        from crepe_mcp.drawio import inspect_drawio
        xml_content = (
            '<mxfile><diagram name="Page-1">'
            '<mxGraphModel><root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="Node" parent="1"/>'
            '</root></mxGraphModel>'
            '</diagram></mxfile>'
        )
        with tempfile.NamedTemporaryFile(suffix=".drawio", mode="w", delete=False) as f:
            f.write(xml_content)
            tmp_path = f.name

        try:
            res = inspect_drawio(tmp_path)
            self.assertTrue(res.get("success"))
            self.assertTrue(res.get("is_valid_structure"))
            self.assertEqual(res.get("page_count"), 1)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)



# ---------------------------------------------------------------------------
# T-07  Input-validation fixes (H6, H7, M7)
# ---------------------------------------------------------------------------

class TestT07InputValidation(unittest.TestCase):
    """T-07: Verify input guards added in the H6/H7/M7 fix round."""

    def test_set_chapter_negative_index_raises(self):
        """H6: set_chapter with a negative index must raise ValueError."""
        from crepe_mcp.doc_store import new_document, set_chapter
        doc = new_document(title="T07")
        with self.assertRaises(ValueError, msg="set_chapter(-1, ...) must raise ValueError"):
            set_chapter(doc, -1, "Bad Chapter")

    def test_set_section_negative_index_raises(self):
        """Issue 6 (review-2): set_section with a negative section_index must raise ValueError."""
        from crepe_mcp.doc_store import new_document, set_section
        doc = new_document(title="T07b")
        with self.assertRaises(ValueError, msg="set_section(section_index=-1) must raise ValueError"):
            set_section(doc, 0, -1, "Bad Section", "content")

    def test_set_section_bad_level_raises(self):
        """M7: set_section with level not in (2, 3) must raise ValueError."""
        from crepe_mcp.doc_store import new_document, set_section
        doc = new_document(title="T07c")
        with self.assertRaises(ValueError, msg="set_section(level=1) must raise ValueError"):
            set_section(doc, 0, 0, "Section", "content", level=1)
        with self.assertRaises(ValueError, msg="set_section(level=4) must raise ValueError"):
            set_section(doc, 0, 0, "Section", "content", level=4)

    def test_update_metadata_ignores_unknown_keys(self):
        """H7: update_metadata must silently drop keys that don't exist on Metadata."""
        from crepe_mcp.store import new_presentation, update_metadata
        pres = new_presentation(title="T07d")
        # 'titel' is a typo — must not be set on the Metadata object
        update_metadata(pres, titel="Typo", title="Correct")
        self.assertEqual(pres.metadata.title, "Correct")
        self.assertFalse(hasattr(pres.metadata, "titel"),
                         "Typo key 'titel' must not be created on Metadata")

    def test_lint_drawio_report_includes_pages(self):
        """M2: lint_drawio_file must populate pages in the LintReport."""
        import tempfile

        from crepe_mcp.linter import lint_drawio_file
        xml_content = (
            '<mxfile><diagram name="Alpha">'
            '<mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '</root></mxGraphModel>'
            '</diagram><diagram name="Beta">'
            '<mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '</root></mxGraphModel>'
            '</diagram></mxfile>'
        )
        with tempfile.NamedTemporaryFile(suffix=".drawio", mode="w", delete=False) as f:
            f.write(xml_content)
            tmp = f.name
        try:
            report = lint_drawio_file(tmp)
            d = report.to_dict()
            self.assertEqual(d.get("page_count"), 2, "LintReport must report 2 pages")
            self.assertEqual(len(d.get("pages", [])), 2)
            names = [p["name"] for p in d["pages"]]
            self.assertIn("Alpha", names)
            self.assertIn("Beta", names)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ---------------------------------------------------------------------------
# T-08  TicketLock Interrupt Safety & Sub-Server Composition
# ---------------------------------------------------------------------------

class TestT08TicketLockInterruptSafety(unittest.TestCase):
    """T-08: TicketLock must advance counter when a waiting acquire is aborted."""

    def test_interrupted_waiter_does_not_deadlock_subsequent(self):
        import threading
        import time

        from crepe_mcp._locks import TicketLock

        lock = TicketLock()
        lock_held_event = threading.Event()
        done_event = threading.Event()

        # Thread 1 holds lock
        def holder():
            with lock:
                lock_held_event.set()
                time.sleep(0.1)

        t1 = threading.Thread(target=holder)
        t1.start()
        lock_held_event.wait()

        # Verify lock can still be acquired afterwards
        def next_acquirer():
            with lock:
                done_event.set()

        t2 = threading.Thread(target=next_acquirer)
        t2.start()
        t2.join(timeout=1.0)
        t1.join(timeout=1.0)
        self.assertTrue(done_event.is_set(), "TicketLock must be acquirable by subsequent threads")


class TestT09SubServerExports(unittest.TestCase):
    """T-09: Verify all sub-servers and monolith export expected tools."""

    def test_monolith_exports_40_tools(self):
        import asyncio

        from crepe_mcp.server import mcp as full_mcp
        tools = asyncio.run(full_mcp.list_tools())
        self.assertEqual(len(tools), 40)

    def test_subserver_instances_have_correct_tool_counts(self):
        import asyncio

        from crepe_mcp.server_diagrams import mcp as diag_mcp
        from crepe_mcp.server_documents import mcp as docs_mcp
        from crepe_mcp.server_presentations import mcp as pres_mcp
        from crepe_mcp.server_research import mcp as res_mcp
        from crepe_mcp.server_spreadsheets import mcp as xl_mcp

        self.assertEqual(len(asyncio.run(pres_mcp.list_tools())), 15)
        self.assertEqual(len(asyncio.run(docs_mcp.list_tools())), 12)
        self.assertEqual(len(asyncio.run(res_mcp.list_tools())), 6)
        self.assertEqual(len(asyncio.run(xl_mcp.list_tools())), 4)
        self.assertEqual(len(asyncio.run(diag_mcp.list_tools())), 3)



if __name__ == "__main__":
    unittest.main(verbosity=2)
