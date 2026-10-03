"""Synthetic submission PDFs mirroring layouts verified on real papers (PLAN.md §2.3).

The PDFs are generated with PyMuPDF when a test needs them; no binary PDF is committed and no text from
a real submission is used. Each fixture reproduces the structure that matters to the reviewer: page
ranges of cover sheets, item labels, letters, response letters and manuscript copies; running headers and
footers; cover fields; numbered assets (figures, tables, equations, algorithms, references); mark-up in a
revised copy; blank pages and pages without a text layer. Layouts can be built at another scale and paper
size, so the extraction rules are tested for scale invariance. Text varies from page to page, as in real
papers, so that only running heads repeat at the same height on nearby pages.
"""

from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from unittest import mock

import pymupdf as fitz

PAPERS = {"a4": (595.0, 842.0), "letter": (612.0, 792.0)}
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
RED = (0.85, 0.1, 0.1)
BLACK = (0.0, 0.0, 0.0)

SENTENCES = [
    "The proposed method is evaluated against three baselines on two public datasets.",
    "Results are averaged over five random seeds and reported with standard deviations.",
    "The cost of each step grows linearly with the number of candidate items.",
    "Ablation experiments isolate the contribution of each component of the pipeline.",
    "Limitations include the size of the evaluation corpus and the tuning budget.",
]
AUTHORS = ["A. Author and B. Author", "C. Writer", "D. Scholar, E. Analyst and F. Critic", "G. Tester and H. Maker",
           "I. Reader"]
TITLES = ["Representative selection at scale", "Learned similarity for page layouts",
          "Clustering under costly distances", "Coverage sampling heuristics", "Evaluation of medoid methods",
          "Graph matching networks"]
VENUES = ["Journal of Examples", "Transactions on Samples", "Letters in Fixtures", "Proceedings of Tests"]


def filler(seed: int, count: int = 2) -> str:
    return " ".join(SENTENCES[(seed + i) % len(SENTENCES)] for i in range(count))


def reference_entry(k: int) -> str:
    return (f'[{k}] {AUTHORS[k % len(AUTHORS)]}, "{TITLES[k % len(TITLES)]}," {VENUES[k % len(VENUES)]}, '
            f"vol. {k}, pp. 1-10, 2024.")


@dataclass(frozen=True)
class FixtureSpec:
    """Structure facts a fixture must produce: the expectations for structure and venue tests."""

    name: str
    filename: str
    pages: int
    venue_id: str
    round: str
    segments: tuple[tuple[str, int, int], ...]
    current_manuscript: tuple[int, int] | None
    reviewers: tuple[int, ...] = ()


class PaperBuilder:
    """Writes pages explicitly; content that would overflow a page raises instead of paginating.

    Every size and distance given to the builder is in points at scale 1 and multiplied by ``scale``.
    """

    def __init__(self, scale: float = 1.0, paper: str = "a4") -> None:
        self.doc = fitz.open()
        self.scale = scale
        self.width, self.height = PAPERS[paper]
        self.margin_x = 0.09 * self.width
        self.gutter = 0.03 * self.width
        self.body_top = 0.095 * self.height
        self.body_bottom = 0.915 * self.height
        self.headers: tuple[str, ...] = ()
        self.footers: tuple[str, ...] = ()
        self.marks: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.numbered: list[bool] = []
        self.page_columns: list[int] = []
        self.line_numbers = False
        self.columns = 1
        self.col = 0
        self.y = self.body_top
        self._page: fitz.Page | None = None

    def set_marks(self, headers: tuple[str, ...] = (), footers: tuple[str, ...] = ()) -> None:
        """Running headers/footers for the pages created from now on ({page} and {pages} are filled in)."""
        self.headers, self.footers = headers, footers

    def set_line_numbers(self, enabled: bool) -> None:
        """Proof line numbers in the side margins of the pages created from now on."""
        self.line_numbers = enabled

    def new_page(self, columns: int = 1) -> fitz.Page:
        self._page = self.doc.new_page(width=self.width, height=self.height)
        self.marks.append((self.headers, self.footers))
        self.numbered.append(self.line_numbers)
        self.page_columns.append(columns)
        self.columns, self.col, self.y = columns, 0, self.body_top
        return self._page

    @property
    def page(self) -> fitz.Page:
        return self._page if self._page is not None else self.new_page()

    def column_box(self) -> tuple[float, float]:
        width = (self.width - 2 * self.margin_x - self.gutter * (self.columns - 1)) / self.columns
        return self.margin_x + self.col * (width + self.gutter), width

    def _room(self, height: float) -> None:
        if self.y + height <= self.body_bottom:
            return
        if self.col + 1 >= self.columns:
            raise ValueError(f"fixture content overflows page {len(self.doc)}; make the page content shorter")
        self.col += 1
        self.y = self.body_top

    def line(self, text: str, size: float = 10, font: str = "tiro", color: tuple[float, ...] = BLACK) -> fitz.Rect:
        size *= self.scale
        self._room(size * 1.35)
        x0, _ = self.column_box()
        self.page.insert_text((x0, self.y + size), text, fontsize=size, fontname=font, color=color)
        right = x0 + fitz.get_text_length(text, fontname=font, fontsize=size)
        rect = fitz.Rect(x0, self.y, right, self.y + size * 1.35)
        self.y += size * 1.35
        return rect

    def paragraph(
        self,
        text: str,
        size: float = 10,
        font: str = "tiro",
        color: tuple[float, ...] = BLACK,
        highlight: bool = False,
    ) -> None:
        _, width = self.column_box()
        for chunk in textwrap.wrap(text, max(20, int(width / (size * self.scale * 0.52)))):
            rect = self.line(chunk, size=size, font=font, color=color)
            if highlight:
                self.page.add_highlight_annot(rect)
        self.y += size * self.scale * 0.8

    def heading(self, text: str, size: float = 11) -> None:
        self.y += 4 * self.scale
        self.line(text, size=size, font="tibo")
        self.y += 2 * self.scale

    def figure(self, label: str, caption: str, inner_text: str = "", height: float = 100) -> None:
        height *= self.scale
        self._room(height + 30 * self.scale)
        x0, width = self.column_box()
        rect = fitz.Rect(x0 + 8 * self.scale, self.y, x0 + width - 8 * self.scale, self.y + height)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
        pix.set_rect(pix.irect, (90, 140, 200))
        self.page.insert_image(rect, pixmap=pix, keep_proportion=False)
        if inner_text:
            self.page.insert_text((rect.x0 + 4 * self.scale, rect.y1 - 4 * self.scale), inner_text,
                                  fontsize=7 * self.scale, fontname="helv")
        self.y += height + 6 * self.scale
        self.paragraph(f"{label} {caption}", size=8)

    def table(self, label: str, caption: str, rows: list[list[str]]) -> None:
        row_h = 14.0 * self.scale
        self._room(row_h * len(rows) + 30 * self.scale)
        self.paragraph(f"{label} {caption}", size=8)
        x0, width = self.column_box()
        top, col_w = self.y, width / len(rows[0])
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                position = (x0 + c * col_w + 3 * self.scale, top + r * row_h + 10 * self.scale)
                self.page.insert_text(position, cell, fontsize=8 * self.scale, fontname="tiro")
        for r in range(len(rows) + 1):
            self.page.draw_line((x0, top + r * row_h), (x0 + width, top + r * row_h), width=0.6)
        for c in range(len(rows[0]) + 1):
            self.page.draw_line((x0 + c * col_w, top), (x0 + c * col_w, top + len(rows) * row_h), width=0.6)
        self.y = top + len(rows) * row_h + 8 * self.scale

    def booktabs(self, label: str, caption: str, rows: list[list[str]], caption_size: float = 10) -> None:
        """Table with horizontal rules only (top, below the header, bottom) and its caption above."""
        row_h = 12.0 * self.scale
        self._room(row_h * len(rows) + 30 * self.scale)
        self.paragraph(f"{label} {caption}", size=caption_size)
        x0, width = self.column_box()
        top, col_w = self.y, width / len(rows[0])
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                position = (x0 + c * col_w + 3 * self.scale, top + r * row_h + 9 * self.scale)
                self.page.insert_text(position, cell, fontsize=9 * self.scale, fontname="tiro")
        for y in (top, top + row_h, top + len(rows) * row_h):
            self.page.draw_line((x0, y), (x0 + width, y), width=0.6)
        self.y = top + len(rows) * row_h + 8 * self.scale

    def equation(self, parts: list[tuple[str, str]], number: int) -> None:
        """Display equation; parts are (text, style) with style normal|italic|sub|sup|symbol."""
        size = 10.0 * self.scale
        styles = {
            "normal": ("tiro", size, 0.0),
            "italic": ("tiit", size, 0.0),
            "sub": ("tiit", size * 0.7, 3.0 * self.scale),
            "sup": ("tiit", size * 0.7, -4.0 * self.scale),
            "symbol": ("symb", size, 0.0),
        }
        self._room(size * 2.4)
        x0, width = self.column_box()
        x, baseline = x0 + 16 * self.scale, self.y + size * 1.5
        for text, style in parts:
            font, fsize, dy = styles[style]
            self.page.insert_text((x, baseline + dy), text, fontsize=fsize, fontname=font)
            x += fitz.get_text_length(text, fontname=font, fontsize=fsize) + 0.5 * self.scale
        tag = f"({number})"
        tag_x = x0 + width - fitz.get_text_length(tag, fontname="tiro", fontsize=size)
        self.page.insert_text((tag_x, baseline), tag, fontsize=size, fontname="tiro")
        self.y += size * 2.4

    def algorithm(self, label: str, caption: str, steps: list[str]) -> None:
        self._room(11 * self.scale * (len(steps) + 2) + 12 * self.scale)
        x0, width = self.column_box()
        self.page.draw_line((x0, self.y), (x0 + width, self.y), width=0.8)
        self.y += 3 * self.scale
        self.line(f"{label} {caption}", size=9, font="tibo")
        for i, step in enumerate(steps, start=1):
            self.line(f"{i}: {step}", size=8, font="cour")
        self.page.draw_line((x0, self.y + 2 * self.scale), (x0 + width, self.y + 2 * self.scale), width=0.8)
        self.y += 8 * self.scale

    def save(self, path: Path, total_pages: int | None = None) -> Path:
        total = total_pages or len(self.doc)
        step = 12 * self.scale
        for index, (headers, footers) in enumerate(self.marks):
            page = self.doc[index]
            for k, text in enumerate(headers):
                label = text.format(page=index + 1, pages=total)
                y = 0.031 * self.height + 11 * self.scale * k
                page.insert_text((self.margin_x, y), label, fontsize=8 * self.scale, fontname="helv")
            for k, text in enumerate(footers):
                label = text.format(page=index + 1, pages=total)
                y = self.height - 0.026 * self.height - 11 * self.scale * k
                page.insert_text((self.margin_x, y), label, fontsize=8 * self.scale, fontname="helv")
            if self.numbered[index]:
                for k in range(int((self.body_bottom - self.body_top) // step)):
                    y = self.body_top + 10 * self.scale + step * k
                    page.insert_text((0.027 * self.width, y), str(k + 1), fontsize=8 * self.scale, fontname="tiro")
                    if self.page_columns[index] == 2:
                        page.insert_text((self.width - 0.04 * self.width, y), str(k + 1), fontsize=8 * self.scale,
                                         fontname="tiro")
        self.doc.set_metadata({"producer": "reviewer-mcp fixture", "creator": "reviewer-mcp fixture"})
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        self.doc.close()
        return path


@dataclass(frozen=True)
class Style:
    """Label conventions of a publisher template."""

    columns: int
    section: Callable[[int, str], str]
    figure: Callable[[int], str]
    figure_ref: Callable[[int], str]
    table: Callable[[int], str]
    table_ref: Callable[[int], str]
    keywords: str
    references: str


IEEE = Style(
    columns=2,
    section=lambda n, title: f"{ROMAN[n - 1]}. {title.upper()}",
    figure=lambda n: f"Fig. {n}.",
    figure_ref=lambda n: f"Fig. {n}",
    table=lambda n: f"TABLE {ROMAN[n - 1]}",
    table_ref=lambda n: f"Table {ROMAN[n - 1]}",
    keywords="Index Terms: representative selection, clustering, evaluation",
    references="REFERENCES",
)
ELSEVIER = Style(
    columns=1,
    section=lambda n, title: f"{n} {title}",
    figure=lambda n: f"Figure {n}:",
    figure_ref=lambda n: f"Figure {n}",
    table=lambda n: f"Table {n}:",
    table_ref=lambda n: f"Table {n}",
    keywords="Keywords: scheduled services; network design; literature review",
    references="References",
)
SECTIONS = ["Introduction", "Related Work", "Proposed Method", "Experiments", "Conclusion"]
EQUATION_1 = [
    ("f", "italic"), ("(", "normal"), ("x", "italic"), (") = ", "normal"), ("Σ", "symbol"),
    ("i", "sub"), ("w", "italic"), ("i", "sub"), ("x", "italic"), ("i", "sub"), ("2", "sup"),
]
EQUATION_2 = [("r", "italic"), ("(", "normal"), ("θ", "symbol"), (") = 1 - ", "normal"), ("n", "italic"),
              ("θ", "sub"), (" / ", "normal"), ("N", "italic")]


def write_manuscript(
    b: PaperBuilder,
    pages: int,
    style: Style,
    title: str,
    *,
    reference_pages: int = 2,
    item_label: str = "",
    marked: bool = False,
    references_table_page: int | None = None,
    template_footnote: bool = False,
) -> None:
    """Manuscript pages: title page, sections, numbered assets on fixed pages, references at the end."""
    body_pages = pages - reference_pages
    heading_pages = {1 + round(k * (body_pages - 1) / len(SECTIONS)): k + 1 for k in range(1, len(SECTIONS))}
    color = RED if marked else BLACK
    for i in range(pages):
        b.new_page(columns=style.columns)
        if i == 0:
            if item_label:
                b.line(item_label, size=9, font="helv")
            b.paragraph(title, size=14, font="tibo")
            b.line("A. Author, B. Author and C. Author", size=9)
            if template_footnote:
                b.paragraph("Received XX Month, XXXX; revised XX Month, XXXX; accepted XX Month, XXXX.", size=7)
            b.heading("Abstract")
            b.paragraph(filler(1, 3), size=9)
            b.paragraph(style.keywords, size=8)
            b.heading(style.section(1, SECTIONS[0]))
            b.paragraph(f"{filler(2)} {style.figure_ref(1)} summarises the approach [1].", color=color)
            continue
        if i >= body_pages:
            if i == body_pages:
                b.heading(style.references)
            first = (i - body_pages) * 7 + 1
            for k in range(first, first + 7):
                b.paragraph(reference_entry(k), size=8)
            continue
        if i in heading_pages:
            number = heading_pages[i]
            b.heading(style.section(number, SECTIONS[number - 1]))
        b.paragraph(filler(i), color=color, highlight=marked and i % 2 == 0)
        if i == 1:
            b.figure(style.figure(1), "Overview of the proposed pipeline.", inner_text="Accuracy (%)")
        elif i == 2:
            b.paragraph("Eq. (1) defines the objective and Eq. (2) the coverage ratio [2].", color=color)
            b.equation(EQUATION_1, 1)
            b.equation(EQUATION_2, 2)
        elif i == 3:
            b.paragraph(f"{style.table_ref(1)} lists the datasets used in the evaluation [3].", color=color)
            b.table(style.table(1), "Datasets and parameters.",
                    [["Dataset", "Items", "Features"], ["A", "1200", "16"], ["B", "5400", "32"]])
        elif i == 4:
            b.paragraph("Algorithm 1 details the incremental procedure [4].", color=color)
            b.algorithm("Algorithm 1", "Incremental coverage",
                        ["R <- X", "while |R| > m do", "select representatives", "end while"])
        elif i == 5:
            b.paragraph(f"{style.figure_ref(2)} reports the runtime of all methods [5].", color=color)
            b.figure(style.figure(2), "Runtime versus dataset size.", inner_text="Time (s)")
        elif i == 6:
            b.figure(style.figure(3), "Example figure that the text never cites.")
        if references_table_page is not None and i == references_table_page:
            b.table(style.table(2), "Classification of the reviewed studies.",
                    [["Modeling", "Solution", "References"],
                     ["MILP", "Heuristic", "[3], [4]"],
                     ["Stochastic", "Exact", "[5]"]])


def write_letter(b: PaperBuilder, pages: int, item_label: str = "Letter") -> None:
    for i in range(pages):
        b.new_page()
        if i == 0:
            b.line(item_label, size=9, font="helv")
            b.paragraph("Dear Editorial Team,")
        b.paragraph(f"{SENTENCES[i % len(SENTENCES)]} We submit the revised manuscript with our answers.")


def write_responses(
    b: PaperBuilder,
    pages: int,
    reviewers: tuple[int, ...],
    *,
    heading: str,
    item_label: str = "",
    reference_page: bool = False,
) -> None:
    per = max(1, pages // len(reviewers))
    for i in range(pages):
        b.new_page()
        if i == 0:
            if item_label:
                b.line(item_label, size=9, font="helv")
            b.heading(heading)
            b.paragraph("Dear Editor, we thank the reviewers for their comments and describe every change below.")
        index = min(len(reviewers) - 1, i // per)
        if i % per == 0:
            b.heading(f"Response to Reviewer {reviewers[index]}")
        for c in (1, 2):
            b.paragraph(f"Comment {c}. {SENTENCES[(i + c) % len(SENTENCES)]}")
            b.paragraph(f"Response: {SENTENCES[(i + c + 2) % len(SENTENCES)]}")
        if reference_page and i == pages - 1:
            b.heading("References")
            b.paragraph(reference_entry(1), size=8)


def em_cover(b: PaperBuilder, journal: str, title: str, number: str, article_type: str) -> None:
    b.new_page()
    b.line(journal, size=11)
    b.paragraph(title, size=11, font="tibo")
    b.line("--Manuscript Draft--", size=10)
    b.line(f"Manuscript Number: {number}", size=9)
    b.line(f"Article Type: {article_type}", size=9)
    b.heading("Abstract")
    b.paragraph(filler(3, 3), size=9)


def build_ieee_single(path: Path, scale: float = 1.0, paper: str = "a4") -> Path:
    """Access-2026-41373: ScholarOne cover pages 1-3, two-column manuscript 4-17, first submission."""
    b = PaperBuilder(scale, paper)
    b.set_marks(headers=("For consideration in IEEE Access", "Page {page} of {pages}"))
    b.new_page()
    b.line("Regular Manuscript", size=12, font="tibo")
    b.paragraph("Scalable Selection of Representative Items in Learned Similarity Spaces", size=13, font="tibo")
    b.line("Submission ID 00000000-0000-4000-8000-000000000001", size=9)
    b.line("Submission Version Initial Submission", size=9)
    b.new_page()
    b.line("Layout", size=10)
    b.line("Select a Manuscript Type: Research Article", size=10)
    b.new_page()
    b.line("Files for peer review", size=10)
    b.paragraph("All files submitted by the author for peer review are listed below.")
    b.set_marks(headers=("Page {page} of {pages}",), footers=("VOLUME 11, 2023",))
    b.set_line_numbers(True)
    write_manuscript(b, 14, IEEE, "Scalable Selection of Representative Items in Learned Similarity Spaces",
                     template_footnote=True)
    return b.save(path)


def build_em_long_review(path: Path, scale: float = 1.0, paper: str = "a4") -> Path:
    """JII-00489: EM cover 1, highlights 2, letter 3-5, responses 6-14, manuscript 15-92, blank 93."""
    title = "Scheduled Service Network Design: A Review of Models and Methods"
    b = PaperBuilder(scale, paper)
    em_cover(b, "Journal of Industrial Information Integration", title, "JII-D-26-00001R2", "Review Article")
    b.new_page()
    b.line("Highlights", size=9, font="helv")
    b.paragraph("Unified formulation of scheduled service network design. Classification of 106 studies.")
    write_letter(b, 3)
    write_responses(b, 9, (1, 2), heading="Review Response Letter", item_label="Response to reviewers",
                    reference_page=True)
    write_manuscript(b, 78, ELSEVIER, title, reference_pages=16, item_label="Response to reviewers",
                     references_table_page=29)
    b.new_page()
    return b.save(path)


def build_em_revision(path: Path, scale: float = 1.0, paper: str = "a4") -> Path:
    """JII-00697: EM cover 1, unmarked manuscript 2-37, responses to reviewers 1, 6, 7, 8 on 38-58, blank 59."""
    title = "Mechanism-Guided Framework for Multi-Fault Diagnosis of Battery Systems"
    b = PaperBuilder(scale, paper)
    em_cover(b, "Journal of Industrial Information Integration", title, "JII-D-26-00002R2", "Research Paper")
    write_manuscript(b, 36, ELSEVIER, title, reference_pages=5,
                     item_label="Revised manuscript without author details (unmarked)")
    write_responses(b, 21, (1, 6, 7, 8), heading="Response to Reviewers' Comments", item_label="Response to reviewers")
    b.new_page()
    return b.save(path)


def build_scholarone_two_copies(path: Path, scale: float = 1.0, paper: str = "a4") -> Path:
    """TMLCN-03-26-0073: cover 1-3, clean copy 4-23, highlighted copy 24-43, responses 44-55, unstamped 56."""
    title = "Physics-Guided Diffusion for Synthetic Wireless Traffic Generation"
    journal = "IEEE Transactions on Machine Learning in Communications and Networking"
    b = PaperBuilder(scale, paper)
    b.set_marks(headers=(f"For consideration in {journal}   Page {{page}} of {{pages}}",))
    b.new_page()
    b.line("Transactions Paper Submissions", size=11, font="tibo")
    b.paragraph(title, size=12, font="tibo")
    b.line("Submission ID TMLCN-03-26-0001.R2", size=9)
    b.new_page()
    b.line("Supplementary files are listed on the next page.", size=9)
    b.new_page()
    write_manuscript(b, 20, IEEE, title, template_footnote=True)
    write_manuscript(b, 20, IEEE, title, marked=True, template_footnote=True)
    write_responses(b, 12, (2, 3, 4), heading="Response to the Reviewers' Comments")
    b.set_marks()
    b.new_page()
    return b.save(path, total_pages=55)


def build_scanned(path: Path) -> Path:
    """A one-page submission without a text layer (scanned)."""
    width, height = PAPERS["a4"]
    source = fitz.open()
    source.new_page(width=width, height=height).insert_text((72, 120), "Scanned manuscript page", fontsize=24)
    pix = source[0].get_pixmap(dpi=72)
    source.close()
    doc = fitz.open()
    doc.new_page(width=width, height=height).insert_image(fitz.Rect(0, 0, width, height), pixmap=pix)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


IEEE_PARTS = (("cover", 1, 3), ("manuscript", 4, 17))
EM_REVISION_PARTS = (("cover", 1, 1), ("manuscript", 2, 37), ("responses", 38, 58), ("other", 59, 59))

FIXTURES: dict[str, FixtureSpec] = {
    "ieee_single": FixtureSpec(
        "ieee_single", "Access-2026-00001_Proof_hi.pdf", 17, "ieee_access", "first", IEEE_PARTS, (4, 17),
    ),
    "ieee_single_8pt_letter": FixtureSpec(
        "ieee_single_8pt_letter", "Access-2026-00002_Proof_hi.pdf", 17, "ieee_access", "first", IEEE_PARTS, (4, 17),
    ),
    "ieee_single_12pt": FixtureSpec(
        "ieee_single_12pt", "Access-2026-00003_Proof_hi.pdf", 17, "ieee_access", "first", IEEE_PARTS, (4, 17),
    ),
    "em_long_review": FixtureSpec(
        "em_long_review", "JII-D-26-00001_R2_reviewer.pdf", 93, "elsevier_jii", "revision",
        (("cover", 1, 1), ("other", 2, 2), ("letter", 3, 5), ("responses", 6, 14), ("manuscript", 15, 92),
         ("other", 93, 93)),
        (15, 92), (1, 2),
    ),
    "em_revision": FixtureSpec(
        "em_revision", "JII-D-26-00002_R2_reviewer.pdf", 59, "elsevier_jii", "revision", EM_REVISION_PARTS,
        (2, 37), (1, 6, 7, 8),
    ),
    "em_revision_12pt": FixtureSpec(
        "em_revision_12pt", "JII-D-26-00003_R2_reviewer.pdf", 59, "elsevier_jii", "revision", EM_REVISION_PARTS,
        (2, 37), (1, 6, 7, 8),
    ),
    "scholarone_two_copies": FixtureSpec(
        "scholarone_two_copies", "TMLCN-03-26-0001.R2_Proof_hi.pdf", 56, "ieee_tmlcn", "revision",
        (("cover", 1, 3), ("manuscript", 4, 23), ("manuscript", 24, 43), ("responses", 44, 55), ("other", 56, 56)),
        (4, 23), (2, 3, 4),
    ),
    "scanned": FixtureSpec("scanned", "scanned_submission.pdf", 1, "", "", (("other", 1, 1),), None),
}

BUILDERS: dict[str, Callable[[Path], Path]] = {
    "ieee_single": build_ieee_single,
    "ieee_single_8pt_letter": partial(build_ieee_single, scale=0.8, paper="letter"),
    "ieee_single_12pt": partial(build_ieee_single, scale=1.2),
    "em_long_review": build_em_long_review,
    "em_revision": build_em_revision,
    "em_revision_12pt": partial(build_em_revision, scale=1.2),
    "scholarone_two_copies": build_scholarone_two_copies,
    "scanned": build_scanned,
}

_BUILT: dict[str, bytes] = {}


def build_fixture(name: str, directory: Path) -> Path:
    """Write fixture `name` into `directory` under its submission file name (built once per process)."""
    spec = FIXTURES[name]
    if name not in _BUILT:
        with tempfile.TemporaryDirectory() as tmp:
            _BUILT[name] = BUILDERS[name](Path(tmp) / spec.filename).read_bytes()
    path = directory / spec.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_BUILT[name])
    return path


class IsolatedTestCase(unittest.TestCase):
    """Runs each test with its own scratch directory, so nothing is written elsewhere."""

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"CREPE_SCRATCH_BASE": str(self.tmp_path / "scratch")})
        env.start()
        self.addCleanup(env.stop)
