"""tests/test_perf.py — the parallel and indexed paths give the same results as the plain ones.

Run with: python -m unittest discover -s tests -p "test_perf.py" -v
"""
import hashlib
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf
from pdf_fixtures import IsolatedTestCase, build_fixture

from crepe_mcp import doc_store, exporter, linter, store
from crepe_mcp.reader import assets as assets_module
from crepe_mcp.reader.document import Line
from crepe_mcp.reader.indexes import _centre, _RowIndex
from crepe_mcp.reader.store import DocumentStore, close_all
from crepe_mcp.render_worker import render_pages

TABLES = ("pages", "lines", "paragraphs", "sections", "segments", "structure", "assets", "mentions")


def dump(doc_store_: DocumentStore) -> dict[str, str]:
    return {
        table: hashlib.sha256(
            repr([tuple(row) for row in doc_store_.con.execute(f"SELECT * FROM {table} ORDER BY rowid")]).encode()
        ).hexdigest()
        for table in TABLES
    }


def make_line(i: int, column: int, y0: float, height: float) -> Line:
    return Line(id=i, page=1, seq=i, region="body", column=column, x0=0.0, y0=y0, x1=10.0, y1=y0 + height,
                text="x", font="f", size=10.0, bold=False, italic=False, mono=False, color=0)


class TestRowIndex(unittest.TestCase):
    def test_matches_the_scan_it_replaces(self):
        rng = random.Random(7)
        lines = [make_line(i, rng.randint(0, 2), round(rng.uniform(0, 400), 1), rng.choice([8.0, 10.0, 12.0]))
                 for i in range(300)]
        index = _RowIndex(lines)
        for tolerance in (0.0, 2.5, 5.0, 17.0):
            for line in lines:
                expected = {other.id for other in lines if other.id != line.id and other.column == line.column
                            and abs(_centre(other) - _centre(line)) <= tolerance}
                self.assertEqual({m.id for m in index.mates(line, tolerance)}, expected)

    def test_boundary_values_are_inclusive_like_the_scan(self):
        a, b = make_line(1, 0, 0.0, 10.0), make_line(2, 0, 3.0, 10.0)  # centres 5.0 and 8.0
        self.assertEqual([m.id for m in _RowIndex([a, b]).mates(a, 3.0)], [2])
        self.assertEqual(_RowIndex([a, b]).mates(a, 2.999), [])


class TestParallelGeometry(IsolatedTestCase):
    def build_dump(self, name: str, threshold: int) -> tuple[dict[str, str], int]:
        close_all()
        path = build_fixture(name, self.tmp_path / name)
        scratch = {"CREPE_SCRATCH_BASE": str(self.tmp_path / f"scratch-{name}-{threshold}")}
        real_popen = assets_module.subprocess.Popen
        with mock.patch.dict(os.environ, scratch), \
                mock.patch.object(assets_module, "PARALLEL_MIN_TABLE_PAGES", threshold), \
                mock.patch.object(assets_module.subprocess, "Popen", wraps=real_popen) as popen:
            result = dump(DocumentStore.open(path))
        close_all()
        return result, popen.call_count

    def test_workers_give_the_same_store(self):
        for name in ("em_long_review", "scholarone_two_copies"):
            plain, plain_workers = self.build_dump(name, 10**6)
            parallel, workers = self.build_dump(name, 2)
            self.assertEqual(plain_workers, 0)
            self.assertGreaterEqual(workers, 2, name)
            self.assertEqual(parallel, plain, name)

    def test_a_failing_worker_falls_back_to_the_parent(self):
        plain, _ = self.build_dump("em_long_review", 10**6)
        with mock.patch.object(assets_module, "_geometries_in_workers", return_value={}):
            fallback, _ = self.build_dump("em_long_review", 2)
        self.assertEqual(fallback, plain)


class TestParallelLint(unittest.TestCase):
    BODIES = [
        "ok slide",
        "\\begin{center}x\\end{center}",
        "![](/nope/missing.png)",
        "fine",
        "\\textbf{bold} and ![](/also/missing.png)",
        "",
        "last",
    ]

    def test_presentation_issues_keep_slide_order(self):
        pres = store.new_presentation(title="T")
        for i, body in enumerate(self.BODIES):
            store.upsert_slide(pres, i, "" if i == 3 else f"S{i}", body)
        report = linter.lint_presentation_content(pres)
        expected = []
        for i, body in enumerate(self.BODIES):
            if i == 3:
                expected.append(("missing_title", i))
            expected += [(x.type, x.slide_index) for x in linter._check_markdown(body, workdir=pres.workdir,
                                                                                  slide_index=i)]
        self.assertEqual([(x.type, x.slide_index) for x in report.issues], expected)
        self.assertGreaterEqual(len(expected), 4)
        store.delete_presentation(pres.id)

    def test_document_issues_keep_chapter_and_section_order(self):
        doc = doc_store.new_document(title="D")
        doc_store.set_chapter(doc, 0, "One", "\\begin{center}intro\\end{center}")
        for si, body in enumerate(self.BODIES[:4]):
            doc_store.set_section(doc, 0, si, "" if si == 3 else f"S{si}", body)
        doc_store.set_chapter(doc, 1, "Two", "")
        doc_store.set_section(doc, 1, 0, "A", self.BODIES[4])
        report = linter.lint_document_content(doc)
        with mock.patch.object(linter, "LINT_PARALLEL_MIN", 10**6):
            serial = linter.lint_document_content(doc)
        key = [(x.type, x.chapter_index, x.section_index, x.line) for x in report.issues]
        self.assertEqual(key, [(x.type, x.chapter_index, x.section_index, x.line) for x in serial.issues])
        self.assertGreaterEqual(len(key), 5)
        doc_store.delete_document(doc.id)


class TestParallelRender(unittest.TestCase):
    def test_workers_write_the_same_pngs_as_one_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "six.pdf"
            doc = pymupdf.open()
            for n in range(1, 8):
                page = doc.new_page(width=300, height=200)
                page.insert_text((30, 100), f"Slide number {n}", fontsize=18)
            doc.save(str(pdf))
            doc.close()
            real_popen = exporter.subprocess.Popen
            with mock.patch.object(exporter.subprocess, "Popen", wraps=real_popen) as popen:
                names = exporter.render_pdf_to_pngs(str(pdf), str(Path(tmp) / "par"), dpi=60)
            serial = render_pages(str(pdf), str(Path(tmp) / "ser"), 60, list(range(1, 8)))
            self.assertGreaterEqual(popen.call_count, 2)
            self.assertEqual([Path(n).name for n in names], [f"slide_{i:03d}.png" for i in range(1, 8)])
            for parallel, plain in zip(names, serial, strict=True):
                self.assertEqual(Path(parallel).read_bytes(), Path(plain).read_bytes())

    def test_falls_back_when_workers_cannot_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "five.pdf"
            doc = pymupdf.open()
            for _ in range(5):
                doc.new_page(width=200, height=100)
            doc.save(str(pdf))
            doc.close()
            with mock.patch.object(exporter.subprocess, "Popen", side_effect=OSError("no processes")):
                names = exporter.render_pdf_to_pngs(str(pdf), str(Path(tmp) / "out"), dpi=40)
            self.assertEqual(len(names), 5)
            self.assertTrue(all(Path(n).is_file() for n in names))


if __name__ == "__main__":
    unittest.main()
