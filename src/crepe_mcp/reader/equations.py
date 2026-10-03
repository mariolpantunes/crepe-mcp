"""Display equations rebuilt from character geometry into linear text and MathML (deterministic, PLAN D7).

The characters of an equation region come from PyMuPDF with their font, size and baseline. Distances are fractions
of the equation's main type size, so the rules hold at any scale:
- the main row is the baseline of the equation number; other rows of full-size characters well above or below it
  are further rows of the display;
- full-size characters stacked above and below the main row, overlapping horizontally, are a fraction (numerator
  and denominator are rebuilt with the same rules on their own baselines);
- small characters directly above or below a large operator (∑, ∏, ∫, or max, min, sup, inf, lim) are its limits;
- other small characters are a subscript (baseline lower than the row) or a superscript (higher) of the item before;
- accents printed over a letter (¯ ˆ ˜ ˙) decorate that letter.
TeX math-extension fonts encode large operators as letters ('X' for ∑); they are mapped back. The result has
confidence 'low' when characters are left over, 'medium' otherwise.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

import pymupdf as fitz

# Offsets and distances as fractions of the main type size.
BAND = 0.35  # a character whose baseline is this close to the row's baseline sits on the row
SMALL = 0.9  # characters smaller than this share of the main size are scripts or limits
STACK = 1.6  # fraction parts and limits lie within this distance of the row
SEGMENT_GAP = 1.2  # a horizontal gap wider than this separates two fraction parts on one side
SPACE = 0.2  # a horizontal gap wider than this between two items is a space

# TeX math-extension fonts (cmex) put each glyph at a fixed slot and PDFs expose the slot as the character: delimiters
# in several sizes (slots 0x00-0x2F), large operators in text and display size (0x46-0x61).
_SMALL = ["(", ")", "[", "]", "⌊", "⌋", "⌈", "⌉", "{", "}", "⟨", "⟩", "|", "‖", "/", "\\"]
_BIG = ["(", ")", "(", ")", "[", "]", "⌊", "⌋", "⌈", "⌉", "{", "}", "⟨", "⟩", "/", "\\"]
_BIGGER = ["(", ")", "[", "]", "⌊", "⌋", "⌈", "⌉", "{", "}", "⟨", "⟩", "/", "\\", "/", "\\"]
_OPERATORS = ["⨆", "⨆", "∮", "∮", "⨀", "⨀", "⨁", "⨁", "⨂", "⨂", "∑", "∏", "∫", "⋃", "⋂", "⨄", "⋀", "⋁",
              "∑", "∏", "∫", "⋃", "⋂", "⨄", "⋀", "⋁", "∐", "∐"]
TEX_EXTENSION = {
    **{chr(slot): glyph for slot, glyph in enumerate(_SMALL)},
    **{chr(0x10 + slot): glyph for slot, glyph in enumerate(_BIG)},
    **{chr(0x20 + slot): glyph for slot, glyph in enumerate(_BIGGER)},
    **{chr(0x46 + slot): glyph for slot, glyph in enumerate(_OPERATORS)},
    **{chr(slot): "√" for slot in range(0x70, 0x75)},
}
# Capitals of TeX's symbol fonts are calligraphic, those of the AMS blackboard fonts double-struck.
SCRIPT_CAPITALS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵", strict=True))
DOUBLE_STRUCK = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ", strict=True))
LARGE_OPERATORS = {"∑", "∏", "∫", "∮", "⋃", "⋂", "⨆", "⨀", "⨁", "⨂", "⨄", "⋀", "⋁", "∐"}
LIMIT_WORDS = {"max", "min", "sup", "inf", "lim", "argmax", "argmin"}
ACCENTS = {"¯": "̄", "ˆ": "̂", "^": "̂", "˜": "̃", "~": "̃", "˙": "̇", "¨": "̈"}


@dataclass
class Glyph:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    base: float
    size: float
    italic: bool
    large: bool = False  # a glyph of a TeX math-extension font (large operators and delimiters)


@dataclass
class Node:
    """kind: token (text, role mi|mn|mo), sub, sup, subsup, frac, limits, accent."""

    kind: str
    text: str = ""
    role: str = "mi"
    children: list[list[Node]] = field(default_factory=list)
    x0: float = 0.0
    x1: float = 0.0
    italic: bool = False
    spaced: bool = False  # printed with a space before it


def glyphs_in(pdf_page: Any, rect: tuple[float, float, float, float], exclude: list[tuple[float, float, float, float]]
              ) -> list[Glyph]:
    """Characters whose centre lies in rect and in none of the excluded boxes (e.g. the equation number)."""
    found: list[Glyph] = []
    data: Any = pdf_page.get_text("rawdict", clip=fitz.Rect(rect))
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font = str(span.get("font", ""))
                italic = bool(span.get("flags", 0) & 2) or font.upper().startswith("CMMI") or "Italic" in font
                for char in span.get("chars", []):
                    text = str(char["c"])
                    x0, y0, x1, y1 = char["bbox"]
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    outside = not (rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3])
                    if not text.strip() or outside or any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in exclude):
                        continue
                    family = font.upper().split("+")[-1]
                    large = family.startswith("CMEX")
                    if large:
                        text = TEX_EXTENSION.get(text, text)
                    elif family.startswith("CMSY"):
                        text = SCRIPT_CAPITALS.get(text, text)
                    elif family.startswith(("MSBM", "BBOLD", "DSROM")):
                        text = DOUBLE_STRUCK.get(text, text)
                    if not text.isprintable():
                        continue
                    found.append(Glyph(text, x0, y0, x1, y1, float(char["origin"][1]), float(span["size"]), italic,
                                       large))
    return found


def _main_size(glyphs: list[Glyph]) -> float:
    sizes: dict[float, int] = {}
    for glyph in glyphs:
        sizes[round(glyph.size, 1)] = sizes.get(round(glyph.size, 1), 0) + 1
    return max(sizes, key=lambda size: (sizes[size], size))


def _segments(glyphs: list[Glyph], gap: float) -> list[list[Glyph]]:
    segments: list[list[Glyph]] = []
    for glyph in sorted(glyphs, key=lambda g: g.x0):
        if segments and glyph.x0 - max(g.x1 for g in segments[-1]) <= gap:
            segments[-1].append(glyph)
        else:
            segments.append([glyph])
    return segments


def _role(text: str, italic: bool) -> str:
    if text[0].isdigit() or (text[0] == "." and len(text) > 1):
        return "mn"
    if text[0].isalpha() and (italic or len(text) > 1 or unicodedata.category(text[0]) in ("Ll", "Lu")):
        return "mi"
    return "mo"


def _tokens(glyphs: list[Glyph], size: float) -> list[Node]:
    """Row characters as tokens: numbers, upright words (function names such as max) and single italic letters."""
    nodes: list[Node] = []
    for glyph in sorted(glyphs, key=lambda g: g.x0):
        last = nodes[-1] if nodes else None
        adjacent = last is not None and last.kind == "token" and glyph.x0 - last.x1 <= 0.15 * size
        number = last is not None and last.role == "mn" and (glyph.text.isdigit() or glyph.text == ".")
        word = (last is not None and last.role == "mi" and not last.italic and not glyph.italic
                and last.text.isalpha() and glyph.text.isalpha())
        if last is not None and adjacent and (number or word):
            last.text += glyph.text
            last.x1 = glyph.x1
            continue
        nodes.append(Node("token", glyph.text, _role(glyph.text, glyph.italic), x0=glyph.x0, x1=glyph.x1,
                          italic=glyph.italic))
    return nodes


def build_row(glyphs: list[Glyph], baseline: float, size: float) -> tuple[list[Node], list[Glyph]]:
    """The items of one row on the given baseline, and the characters that belong to no item of it."""
    band = [
        g for g in glyphs
        if g.size >= SMALL * size and (abs(g.base - baseline) <= BAND * size or (g.large and g.y0 <= baseline
                                                                               and g.y1 >= baseline - size))
    ]
    small = [g for g in glyphs if g.size < SMALL * size]
    stacked = [g for g in glyphs if g.size >= SMALL * size and g not in band and abs(g.base - baseline) <= STACK * size]
    claimed: set[int] = {id(g) for g in band}
    items: list[Node] = []

    # large operators and limit words take the small characters directly above and below them
    row_tokens = _tokens(band, size)
    for token in row_tokens:
        if token.text in LARGE_OPERATORS or token.text in LIMIT_WORDS:
            reach = (token.x0 - 0.5 * size, token.x1 + 0.5 * size)
            top = min(g.y0 for g in band if token.x0 <= (g.x0 + g.x1) / 2 <= token.x1)
            bottom = max(g.y1 for g in band if token.x0 <= (g.x0 + g.x1) / 2 <= token.x1)
            below = [g for g in small if id(g) not in claimed and g.y0 >= bottom - 0.2 * size
                     and g.y0 - bottom <= STACK * size]
            above = [g for g in small if id(g) not in claimed and g.y1 <= top + 0.2 * size
                     and top - g.y1 <= STACK * size]
            under = [g for part in _segments(below, 0.3 * size) if min(g.x0 for g in part) <= reach[1]
                     and max(g.x1 for g in part) >= reach[0] for g in part]
            over = [g for part in _segments(above, 0.3 * size) if min(g.x0 for g in part) <= reach[1]
                    and max(g.x1 for g in part) >= reach[0] for g in part]
            if under or over:
                parts = []
                for part in (under, over):
                    claimed.update(id(g) for g in part)
                    parts.append(build_row(part, _baseline(part), _main_size(part))[0] if part else [])
                role = "mo" if token.text in LARGE_OPERATORS else "mi"
                operator = Node("token", token.text, role, x0=token.x0, x1=token.x1)
                token.kind, token.children = "limits", [[operator], *parts]
                token.x0 = min([token.x0] + [g.x0 for g in under + over])
                token.x1 = max([token.x1] + [g.x1 for g in under + over])

    # fractions: full-size parts above and below the row that overlap horizontally
    above = _segments([g for g in stacked if g.base < baseline], SEGMENT_GAP * size)
    below = _segments([g for g in stacked if g.base > baseline], SEGMENT_GAP * size)
    for top_part in above:
        left, right = min(g.x0 for g in top_part), max(g.x1 for g in top_part)
        match = next((part for part in below if min(g.x0 for g in part) < right and max(g.x1 for g in part) > left
                      and not any(id(g) in claimed for g in part)), None)
        if match is None or any(id(g) in claimed for g in top_part):
            continue
        extent = (min(left, min(g.x0 for g in match)), max(right, max(g.x1 for g in match)))
        inside = [g for g in small if id(g) not in claimed and extent[0] - 0.2 * size <= g.x0 <= extent[1] + 0.2 * size
                  and abs(g.base - baseline) <= STACK * size]
        numerator = top_part + [g for g in inside if g.base < baseline - BAND * size]
        denominator = match + [g for g in inside if g.base > baseline + BAND * size]
        claimed.update(id(g) for g in numerator + denominator)
        num_nodes, _ = build_row(numerator, _baseline(top_part), size)
        den_nodes, _ = build_row(denominator, _baseline(match), size)
        items.append(Node("frac", children=[num_nodes, den_nodes], x0=extent[0], x1=extent[1]))

    items.extend(row_tokens)
    items.sort(key=lambda node: node.x0)

    # accents decorate the letter they overlap
    for accent in [node for node in items if node.kind == "token" and node.text in ACCENTS]:
        centre = (accent.x0 + accent.x1) / 2
        target = next((node for node in items if node is not accent and node.x0 - 0.1 * size <= centre
                       <= node.x1 + 0.1 * size), None)
        items.remove(accent)
        if target is not None:
            items[items.index(target)] = Node("accent", ACCENTS[accent.text], children=[[target]], x0=target.x0,
                                              x1=target.x1)

    # scripts: remaining small characters attach to the item before them
    scripts = [g for g in small if id(g) not in claimed and abs(g.base - baseline) <= STACK * size]
    for group in _segments(scripts, 0.15 * size):
        start = min(g.x0 for g in group)
        target = next((node for node in reversed(items) if node.x0 < start), None)
        if target is None:
            continue
        claimed.update(id(g) for g in group)
        rows: dict[str, list[Glyph]] = {"sub": [], "sup": []}
        for glyph in group:
            rows["sup" if glyph.base < baseline - 0.1 * size else "sub"].append(glyph)
        index = items.index(target)
        base_node = Node(target.kind, target.text, target.role, target.children, target.x0, target.x1)
        subs = build_row(rows["sub"], _baseline(rows["sub"]), _main_size(rows["sub"]))[0] if rows["sub"] else []
        sups = build_row(rows["sup"], _baseline(rows["sup"]), _main_size(rows["sup"]))[0] if rows["sup"] else []
        kind = "subsup" if subs and sups else ("sub" if subs else "sup")
        children = [[base_node], subs, sups] if kind == "subsup" else [[base_node], subs or sups]
        items[index] = Node(kind, children=children, x0=target.x0, x1=max(g.x1 for g in group))

    for previous, node in zip(items, items[1:], strict=False):
        node.spaced = node.x0 - previous.x1 > SPACE * size
    leftover = [g for g in glyphs if id(g) not in claimed and g not in band]
    return items, leftover


def _baseline(glyphs: list[Glyph]) -> float:
    size = _main_size(glyphs)
    main = [g.base for g in glyphs if abs(g.size - size) < 0.05 * size] or [g.base for g in glyphs]
    return sorted(main)[len(main) // 2]


def rebuild(glyphs: list[Glyph], baseline: float, size: float) -> tuple[list[list[Node]], bool]:
    """Rows of the display (main row on the equation number's baseline, main size the number's size) and whether
    every character was placed."""
    if not glyphs:
        return [], False
    rows: list[tuple[float, list[Node]]] = []
    remaining = glyphs
    current = baseline
    while remaining:
        items, leftover = build_row(remaining, current, size)
        if items:
            rows.append((current, items))
        if len(leftover) == len(remaining):
            break
        remaining = leftover
        full = [g for g in remaining if g.size >= SMALL * size]
        if not full:
            break
        current = min((g.base for g in full), key=lambda base: abs(base - baseline))
    rows.sort(key=lambda row: row[0])
    return [items for _, items in rows], not remaining


def _wrap(text: str) -> str:
    return text if len(text) == 1 else "{" + text + "}"


def linear(nodes: list[Node]) -> str:
    parts: list[str] = []
    for node in nodes:
        if node.spaced:
            parts.append(" ")
        if node.kind == "token":
            spacer = " " if node.role == "mo" and node.text in "=<>≤≥≈∈⊂+−-×·→" else ""
            parts.append(f"{spacer}{node.text}{spacer}")
        elif node.kind == "accent":
            parts.append(unicodedata.normalize("NFC", linear(node.children[0]) + node.text))
        elif node.kind == "sub":
            parts.append(f"{linear(node.children[0])}_{_wrap(linear(node.children[1]))}")
        elif node.kind == "sup":
            parts.append(f"{linear(node.children[0])}^{_wrap(linear(node.children[1]))}")
        elif node.kind == "subsup":
            base, sub, sup = (linear(child) for child in node.children)
            parts.append(f"{base}_{_wrap(sub)}^{_wrap(sup)}")
        elif node.kind == "frac":
            num, den = (linear(child) for child in node.children)
            parts.append(f"({num})/({den})")
        elif node.kind == "limits":
            op, under, over = (linear(child) for child in node.children)
            parts.append(op + (f"_{_wrap(under)}" if under else "") + (f"^{_wrap(over)}" if over else "") + " ")
    return " ".join("".join(parts).split())


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _row(nodes: list[Node]) -> str:
    inner = "".join(mathml(node) for node in nodes)
    return inner if len(nodes) == 1 else f"<mrow>{inner}</mrow>"


def mathml(node: Node) -> str:
    if node.kind == "token":
        return f"<{node.role}>{_escape(node.text)}</{node.role}>"
    if node.kind == "accent":
        return f"<mover>{_row(node.children[0])}<mo>{_escape(unicodedata.normalize('NFC', node.text))}</mo></mover>"
    if node.kind in ("sub", "sup"):
        tag = "msub" if node.kind == "sub" else "msup"
        return f"<{tag}>{_row(node.children[0])}{_row(node.children[1])}</{tag}>"
    if node.kind == "subsup":
        return f"<msubsup>{''.join(_row(child) for child in node.children)}</msubsup>"
    if node.kind == "frac":
        return f"<mfrac>{_row(node.children[0])}{_row(node.children[1])}</mfrac>"
    op, under, over = node.children
    if under and over:
        return f"<munderover>{_row(op)}{_row(under)}{_row(over)}</munderover>"
    return f"<munder>{_row(op)}{_row(under)}</munder>" if under else f"<mover>{_row(op)}{_row(over)}</mover>"


def equation_text(pdf_page: Any, rect: tuple[float, float, float, float], number_box: tuple[float, float, float, float]
                  ) -> tuple[str, str]:
    """(content, confidence): linear text on the first line(s) and MathML on the last line."""
    glyphs = glyphs_in(pdf_page, rect, [number_box])
    number = [g for g in glyphs_in(pdf_page, number_box, []) if g.text.isdigit()]
    if not glyphs or not number:
        return "", "low"
    rows, complete = rebuild(glyphs, number[0].base, number[0].size)
    if not rows:
        return "", "low"
    text = "\n".join(linear(row) for row in rows) if len(rows) > 1 else linear(rows[0])
    if len(rows) == 1:
        markup = f"<math>{_row(rows[0])}</math>"
    else:
        markup = "<math><mtable>" + "".join(f"<mtr><mtd>{_row(row)}</mtd></mtr>" for row in rows) + "</mtable></math>"
    return f"{text}\n{markup}", "medium" if complete and len(rows) <= 2 else "low"
