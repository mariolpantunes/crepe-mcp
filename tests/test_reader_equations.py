"""Equations rebuilt from character geometry: scripts, fractions, limits and accents."""

import unittest

import pymupdf as fitz

from crepe_mcp.reader.equations import equation_text


def page_with(parts, number="(7)", size=10.0):
    """A page with one display equation; parts are (text, x, baseline offset, size factor[, font]) with italic Times
    by default and upright Times for function names."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    baseline = 100.0
    for text, x, offset, factor, *font in parts:
        page.insert_text((x, baseline + offset), text, fontsize=size * factor, fontname=font[0] if font else "tiit")
    page.insert_text((360, baseline), number, fontsize=size, fontname="tiro")
    number_width = fitz.get_text_length(number, fontname="tiro", fontsize=size)
    return doc, page, (360, baseline - size, 360 + number_width, baseline + 0.3 * size)


class TestEquations(unittest.TestCase):
    def rebuild(self, parts):
        doc, page, number_box = page_with(parts)
        content, confidence = equation_text(page, (40, 60, 350, 140), number_box)
        doc.close()
        text, markup = content.split("\n")
        return text, markup, confidence

    def test_fraction_scripts_and_limits(self):
        parts = [
            ("y", 60, 0, 1), ("=", 72, 0, 1),
            ("max", 90, 0, 1, "tiro"), ("i", 100, 8, 0.7),  # limit below max
            ("a", 125, -7, 1), ("b", 130.5, -7, 1),         # numerator above the row
            ("c", 129, 7, 1),                               # denominator below the row
            ("x", 150, 0, 1), ("2", 156, -4, 0.7),          # superscript
            ("z", 170, 0, 1), ("k", 176, 2, 0.7),           # subscript
        ]
        text, markup, confidence = self.rebuild(parts)
        self.assertEqual(text, "y = max_i (ab)/(c) x^2 z_k")
        self.assertIn("<munder><mi>max</mi><mi>i</mi></munder>", markup)
        self.assertIn("<mfrac><mrow><mi>a</mi><mi>b</mi></mrow><mi>c</mi></mfrac>", markup)
        self.assertIn("<msup><mi>x</mi><mn>2</mn></msup>", markup)
        self.assertIn("<msub><mi>z</mi><mi>k</mi></msub>", markup)
        self.assertEqual(confidence, "medium")

    def test_scale_invariance(self):
        small = self.rebuild([("x", 60, 0, 1), ("2", 66, -4, 0.7), ("=", 80, 0, 1), ("y", 95, 0, 1)])
        doc, page, number_box = page_with([("x", 60, 0, 1.0), ("2", 69, -6, 0.7), ("=", 90, 0, 1.0),
                                           ("y", 110, 0, 1.0)], size=15.0)
        big, _ = equation_text(page, (40, 60, 350, 140), number_box)
        doc.close()
        self.assertEqual(small[0], "x^2 = y")
        self.assertEqual(big.split("\n")[0], "x^2 = y")


if __name__ == "__main__":
    unittest.main()
