"""Content linter for CREPE MCP presentations, documents, and draw.io diagrams.

Provides three public functions called by the Group-F MCP tools:
  lint_presentation_content(pres)    → LintReport
  lint_document_content(doc)         → LintReport
  lint_drawio_file(input_path)       → LintReport

Each function returns a LintReport dataclass with a `valid` flag and an
`issues` list. Issues carry slide/chapter/section indices, line numbers,
issue types, and human-readable messages.

No content is mutated here. This module is purely analytical.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from crepe_mcp.drawio import read_drawio_bytes as _read_drawio_bytes
from crepe_mcp.drawio import try_decode_compressed as _try_decode_compressed

if TYPE_CHECKING:
    from crepe_mcp.doc_store import Document
    from crepe_mcp.store import Presentation


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    type: str           # e.g. "forbidden_latex", "missing_image", "parse_error"
    message: str
    line: int | None = None
    # Location fields — set only when applicable
    slide_index: int | None = None
    chapter_index: int | None = None
    section_index: int | None = None
    page_name: str | None = None   # for drawio


@dataclass
class LintReport:
    valid: bool
    issues: list[Issue] = field(default_factory=list)
    # Populated by lint_drawio_file only; empty for presentation/document reports.
    pages: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        result: dict = {"valid": self.valid, "issues": []}
        for iss in self.issues:
            d: dict = {"type": iss.type, "message": iss.message}
            if iss.line is not None:
                d["line"] = iss.line
            if iss.slide_index is not None:
                d["slide_index"] = iss.slide_index
            if iss.chapter_index is not None:
                d["chapter_index"] = iss.chapter_index
            if iss.section_index is not None:
                d["section_index"] = iss.section_index
            if iss.page_name is not None:
                d["page_name"] = iss.page_name
            result["issues"].append(d)
        if self.pages:
            result["page_count"] = len(self.pages)
            result["pages"] = self.pages
        return result


# ---------------------------------------------------------------------------
# Markdown checks (shared between slides and documents)
# ---------------------------------------------------------------------------

# Patterns that are forbidden for cross-format (PDF + PPTX/DOCX) compatibility
_FORBIDDEN = [
    (
        re.compile(r"\\includegraphics(?:\[.*?\])?\{([^}]+)\}", re.DOTALL),
        "forbidden_latex",
        r"Raw LaTeX \includegraphics{{{src}}} found — use ![alt](/path) instead.",
    ),
    (
        re.compile(r"^\\begin\{center\}", re.MULTILINE),
        "forbidden_latex",
        r"Raw LaTeX \begin{{center}} found — use Pandoc fenced divs ::: instead.",
    ),
    (
        re.compile(r"^\\end\{center\}", re.MULTILINE),
        "forbidden_latex",
        r"Raw LaTeX \end{{center}} found — use Pandoc fenced divs ::: instead.",
    ),
    (
        re.compile(r"\\begin\{columns\}", re.DOTALL),
        "forbidden_latex",
        r"Raw LaTeX \begin{{columns}} — use :::: {{.columns}} ::: {{.column}} instead.",
    ),
    (
        re.compile(r"\\textbf\{"),
        "forbidden_latex",
        r"Raw LaTeX \textbf{{}} — use **bold** instead.",
    ),
    (
        re.compile(r"\\textit\{"),
        "forbidden_latex",
        r"Raw LaTeX \textit{{}} — use *italic* instead.",
    ),
]

# Standard Markdown image: ![alt](path) optionally followed by {attrs}
_IMAGE_RE = re.compile(r"!\[.*?\]\(([^)]+)\)")


def _strip_fenced_content(markdown: str) -> str:
    """Return markdown with fenced code block *contents* blanked out.

    Preserves line count so that forbidden-pattern line numbers remain
    accurate. The opening and closing fence delimiter lines are kept;
    only the lines between them are replaced with empty strings.
    """
    result: list[str] = []
    fence_char: str = ""
    fence_min_len: int = 0
    in_fence = False

    for line in markdown.splitlines():
        if not in_fence:
            m = re.match(r'^(`{3,}|~{3,})', line)
            if m:
                in_fence = True
                fence_char = m.group(1)[0]
                fence_min_len = len(m.group(1))
                result.append(line)  # keep opening fence
            else:
                result.append(line)
        else:
            # Closing fence: same char, >= same run length, only whitespace after
            m = re.match(r'^([`~]{' + str(fence_min_len) + r',})\s*$', line)
            if m and m.group(1)[0] == fence_char:
                in_fence = False
                result.append(line)  # keep closing fence
            else:
                result.append("")   # blank content — preserves line numbers
    return "\n".join(result)


def _check_markdown(
    markdown: str,
    workdir: str | None = None,
    *,
    slide_index: int | None = None,
    chapter_index: int | None = None,
    section_index: int | None = None,
) -> list[Issue]:
    """Run all checks on a single markdown string. Returns a list of Issues."""
    issues: list[Issue] = []
    lines = markdown.splitlines()
    loc = dict(
        slide_index=slide_index,
        chapter_index=chapter_index,
        section_index=section_index,
    )

    # 1. Forbidden pattern scan (on fence-stripped text to avoid false positives
    #    from LaTeX syntax shown in code examples inside fenced blocks).
    scan_text = _strip_fenced_content(markdown)
    for pattern, issue_type, msg_template in _FORBIDDEN:
        for m in pattern.finditer(scan_text):
            line_no = scan_text[: m.start()].count("\n") + 1
            src = m.group(1) if m.lastindex else ""
            message = msg_template.format(src=src)
            issues.append(Issue(type=issue_type, message=message, line=line_no, **loc))  # type: ignore[arg-type]

    # 2. Image path existence check (also fence-stripped to ignore code examples)
    for m in _IMAGE_RE.finditer(scan_text):
        path = m.group(1).strip()
        line_no = scan_text[: m.start()].count("\n") + 1
        # Skip URLs and data URIs
        if path.startswith(("http://", "https://", "data:")):
            continue
        # Resolve relative paths against the workdir if given
        if not os.path.isabs(path) and workdir:
            resolved = os.path.join(workdir, path)
        else:
            resolved = path
        if not os.path.isfile(resolved):
            issues.append(Issue(
                type="missing_image",
                message=f"Image not found on disk: {path!r}",
                line=line_no,
                **loc,  # type: ignore[arg-type]
            ))

    # 3. Pandoc dry-run parse (only if pandoc is available)
    if shutil.which("pandoc") and markdown.strip():
        issues.extend(_pandoc_dry_run(markdown, lines, **loc))  # type: ignore[arg-type]

    return issues


def _pandoc_dry_run(
    markdown: str,
    lines: list[str],
    *,
    slide_index: int | None = None,
    chapter_index: int | None = None,
    section_index: int | None = None,
) -> list[Issue]:
    """Run pandoc --from markdown --to native as a parse-only check."""
    issues: list[Issue] = []
    loc = dict(
        slide_index=slide_index,
        chapter_index=chapter_index,
        section_index=section_index,
    )
    tmp_path: str | None = None  # H3: must be initialised before try so finally can guard it
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(markdown)
            tmp_path = tf.name
        result = subprocess.run(
            ["pandoc", "--from", "markdown", "--to", "native", tmp_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            # Parse pandoc's stderr for line numbers
            for err_line in result.stderr.splitlines():
                line_no = _extract_pandoc_line(err_line)
                issues.append(Issue(
                    type="parse_error",
                    message=f"Pandoc parse error: {err_line.strip()}",
                    line=line_no,
                    **loc,  # type: ignore[arg-type]
                ))
    except subprocess.TimeoutExpired:
        issues.append(Issue(
            type="parse_timeout",
            message="Pandoc parse timed out (15 s) — check for runaway fenced blocks.",
            **loc,  # type: ignore[arg-type]
        ))
    except Exception as exc:
        issues.append(Issue(
            type="parse_error",
            message=f"Could not run pandoc dry-run: {exc}",
            **loc,  # type: ignore[arg-type]
        ))
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return issues


_PANDOC_LINE_RE = re.compile(r":(\d+):")


def _extract_pandoc_line(err_line: str) -> int | None:
    m = _PANDOC_LINE_RE.search(err_line)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Presentation lint
# ---------------------------------------------------------------------------

def lint_presentation_content(
    pres: Presentation,
    slide_index: int | None = None,
) -> LintReport:
    """Lint all slides (or one by index) of an in-memory Presentation.

    Takes a consistent snapshot of pres.slides under pres.lock before
    iterating — a concurrent set_slide cannot corrupt the iteration.
    """
    issues: list[Issue] = []

    # H2: snapshot under lock — do not iterate the live list
    with pres.lock:
        slides = [(s.title, s.content) for s in pres.slides]
    workdir = pres.workdir

    if slide_index is not None:
        if slide_index < 0 or slide_index >= len(slides):
            issues.append(Issue(
                type="slide_not_found",
                message=f"slide_index {slide_index} out of range (0-{len(slides)-1}).",
                slide_index=slide_index,
            ))
            return LintReport(valid=False, issues=issues)
        target_range = range(slide_index, slide_index + 1)
    else:
        target_range = range(len(slides))

    for i in target_range:
        title, content = slides[i]
        if not title.strip():
            issues.append(Issue(
                type="missing_title",
                message="Slide has no title.",
                slide_index=i,
            ))
        issues.extend(
            _check_markdown(content, workdir=workdir, slide_index=i)
        )

    return LintReport(valid=len(issues) == 0, issues=issues)


# ---------------------------------------------------------------------------
# Document lint
# ---------------------------------------------------------------------------

def lint_document_content(
    doc: Document,
    chapter_index: int | None = None,
) -> LintReport:
    """Lint all chapters/sections (or one chapter) of an in-memory Document.

    Takes a consistent snapshot of doc.chapters under doc.lock before
    iterating — concurrent set_chapter/set_section calls cannot corrupt it.
    """
    issues: list[Issue] = []

    # H2: snapshot under lock
    with doc.lock:
        chapters = [
            (ch.title, ch.intro, [(s.title, s.content) for s in ch.sections])
            for ch in doc.chapters
        ]
    workdir = doc.workdir

    if chapter_index is not None:
        if chapter_index < 0 or chapter_index >= len(chapters):
            issues.append(Issue(
                type="chapter_not_found",
                message=f"chapter_index {chapter_index} out of range (0-{len(chapters)-1}).",
                chapter_index=chapter_index,
            ))
            return LintReport(valid=False, issues=issues)
        target_range = range(chapter_index, chapter_index + 1)
    else:
        target_range = range(len(chapters))

    for ci in target_range:
        ch_title, ch_intro, sections = chapters[ci]
        if not ch_title.strip():
            issues.append(Issue(
                type="missing_title",
                message="Chapter has no title.",
                chapter_index=ci,
            ))
        if ch_intro:
            issues.extend(
                _check_markdown(ch_intro, workdir=workdir, chapter_index=ci)
            )
        for si, (sec_title, sec_content) in enumerate(sections):
            if not sec_title.strip():
                issues.append(Issue(
                    type="missing_title",
                    message="Section has no title.",
                    chapter_index=ci,
                    section_index=si,
                ))
            issues.extend(
                _check_markdown(
                    sec_content,
                    workdir=workdir,
                    chapter_index=ci,
                    section_index=si,
                )
            )

    return LintReport(valid=len(issues) == 0, issues=issues)


# ---------------------------------------------------------------------------
# Draw.io lint
# ---------------------------------------------------------------------------

def lint_drawio_file(input_path: str) -> LintReport:
    """Validate a .drawio file: XML structure, compressed content, cell hierarchy.

    The returned LintReport includes a `pages` list (and `page_count` in
    to_dict()) so agents can see which pages were inspected.
    """
    issues: list[Issue] = []
    pages: list[dict] = []

    if not os.path.isabs(input_path):
        return LintReport(
            valid=False,
            issues=[Issue(type="invalid_path", message=f"input_path must be absolute, got {input_path!r}")],
        )
    if not os.path.isfile(input_path):
        return LintReport(
            valid=False,
            issues=[Issue(type="file_not_found", message=f"File not found: {input_path!r}")],
        )

    # Read bytes (plain XML or zip-compressed)
    try:
        raw_bytes = _read_drawio_bytes(input_path)
    except Exception as exc:
        return LintReport(
            valid=False,
            issues=[Issue(type="read_error", message=f"Could not read file: {exc}")],
        )

    # XML parse
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        return LintReport(
            valid=False,
            issues=[Issue(type="xml_parse_error", message=f"XML parse error: {exc}")],
        )

    if root.tag == "mxfile":
        diagrams = root.findall("diagram")
        if not diagrams:
            issues.append(Issue(
                type="no_pages",
                message="<mxfile> contains no <diagram> children.",
            ))
        for i, diagram in enumerate(diagrams, start=1):
            name = diagram.get("name", f"Page-{i}")
            pages.append({"index": i, "name": name})
            issues.extend(_check_diagram_element(diagram, i, name))
    elif root.tag == "mxGraphModel":
        pages.append({"index": 1, "name": "Page-1"})
        issues.extend(_check_graph_model_elem(root, page_name="Page-1"))
    else:
        issues.append(Issue(
            type="unexpected_root",
            message=f"Unexpected root element <{root.tag}>; expected <mxfile> or <mxGraphModel>.",
        ))

    return LintReport(valid=len(issues) == 0, issues=issues, pages=pages)


def _check_diagram_element(
    diagram: ET.Element,
    index: int,
    name: str,
) -> list[Issue]:
    """Validate one <diagram> element, handling both inline and compressed content."""
    issues: list[Issue] = []

    if len(diagram):
        # Inline (uncompressed) <mxGraphModel> child
        graph_model = diagram.find("mxGraphModel")
        if graph_model is not None:
            issues.extend(_check_graph_model_elem(graph_model, page_name=name))
        else:
            issues.append(Issue(
                type="missing_graph_model",
                message=f"Page {index} ({name!r}): <diagram> has children but no <mxGraphModel>.",
                page_name=name,
            ))
    elif diagram.text and diagram.text.strip():
        # Text content — could be inline XML or base64+deflate compressed XML
        text = diagram.text.strip()
        decoded = _try_decode_compressed(text)
        if decoded is not None:
            text = decoded
        if text.startswith("<"):
            try:
                inner = ET.fromstring(text)
                if inner.tag == "mxGraphModel":
                    issues.extend(_check_graph_model_elem(inner, page_name=name))
            except ET.ParseError as exc:
                issues.append(Issue(
                    type="xml_parse_error",
                    message=f"Page {index} ({name!r}): inner XML parse error: {exc}",
                    page_name=name,
                ))
        else:
            issues.append(Issue(
                type="unreadable_content",
                message=f"Page {index} ({name!r}): diagram content could not be decoded or parsed.",
                page_name=name,
            ))
    else:
        issues.append(Issue(
            type="empty_page",
            message=f"Page {index} ({name!r}): <diagram> element is empty.",
            page_name=name,
        ))

    return issues


def _check_graph_model_elem(model: ET.Element, page_name: str) -> list[Issue]:
    """Validate a <mxGraphModel> element's cell hierarchy."""
    issues: list[Issue] = []
    root_elem = model.find("root")
    if root_elem is None:
        issues.append(Issue(
            type="missing_root",
            message=f"Page {page_name!r}: <mxGraphModel> has no <root> child.",
            page_name=page_name,
        ))
        return issues

    cells = root_elem.findall("mxCell")
    cell_ids: set[str] = set()
    has_root_0 = False
    has_root_1 = False

    for cell in cells:
        cid = cell.get("id")
        if cid is None:
            issues.append(Issue(
                type="missing_cell_id",
                message=f"Page {page_name!r}: <mxCell> missing 'id' attribute.",
                page_name=page_name,
            ))
            continue
        if cid in cell_ids:
            issues.append(Issue(
                type="duplicate_cell_id",
                message=f"Page {page_name!r}: duplicate mxCell id={cid!r}.",
                page_name=page_name,
            ))
        cell_ids.add(cid)
        if cid == "0":
            has_root_0 = True
        elif cid == "1":
            has_root_1 = True

    if not has_root_0:
        issues.append(Issue(
            type="missing_base_cell",
            message=f"Page {page_name!r}: missing base root cell <mxCell id=\"0\"/>.",
            page_name=page_name,
        ))
    if not has_root_1:
        issues.append(Issue(
            type="missing_layer_cell",
            message=f"Page {page_name!r}: missing default layer <mxCell id=\"1\" parent=\"0\"/>.",
            page_name=page_name,
        ))

    return issues


# _try_decode_compressed and _read_drawio_bytes are now imported from
# crepe_mcp.drawio (their canonical home) at the top of this module.
