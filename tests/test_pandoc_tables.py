"""tests/test_pandoc_tables.py — pandoc tables as Markdown: several header rows, merged cells (no pandoc needed).

Run with: python -m unittest discover -s tests -p "test_pandoc_tables.py" -v
"""

import unittest

from crepe_mcp.reader.pandoc_reader import table_markdown

ATTR = ["", [], []]


def cell(text: str, rowspan: int = 1, colspan: int = 1) -> list:
    blocks = [{"t": "Plain", "c": [{"t": "Str", "c": text}]}] if text else []
    return [ATTR, {"t": "AlignDefault"}, rowspan, colspan, blocks]


def row(*cells: list) -> list:
    return [ATTR, list(cells)]


def table(head: list[list], body: list[list]) -> list:
    return [ATTR, [None, []], [], [ATTR, head], [[ATTR, 0, [], body]], [ATTR, []]]


class TestTableMarkdown(unittest.TestCase):
    def test_single_header_row_is_unchanged(self):
        md = table_markdown(table([row(cell("size"), cell("time"))], [row(cell("10"), cell("1"))]))
        self.assertEqual(md.splitlines(), ["| size | time |", "|---|---|", "| 10 | 1 |"])

    def test_every_header_row_is_kept_and_group_labels_cover_their_columns(self):
        head = [
            row(cell("Name", rowspan=2), cell("Score", colspan=2)),
            row(cell("Math"), cell("Art")),
        ]
        md = table_markdown(table(head, [row(cell("Ann"), cell("90"), cell("80"))]))
        self.assertEqual(
            md.splitlines(),
            ["| Name | Score / Math | Score / Art |", "|---|---|---|", "| Ann | 90 | 80 |"],
        )

    def test_a_row_span_in_the_body_fills_the_rows_below(self):
        body = [
            row(cell("A", rowspan=2), cell("1"), cell("2")),
            row(cell("3"), cell("4")),
            row(cell("B"), cell("5"), cell("6")),
        ]
        md = table_markdown(table([row(cell("k"), cell("x"), cell("y"))], body))
        self.assertEqual(md.splitlines()[2:], ["| A | 1 | 2 |", "| A | 3 | 4 |", "| B | 5 | 6 |"])

    def test_a_merged_body_cell_keeps_the_columns_aligned(self):
        body = [row(cell("1"), cell("2"), cell("3")), row(cell("Total", colspan=3))]
        md = table_markdown(table([row(cell("a"), cell("b"), cell("c"))], body))
        self.assertEqual(md.splitlines()[2:], ["| 1 | 2 | 3 |", "| Total |  |  |"])

    def test_a_table_without_a_header_gets_an_empty_one(self):
        md = table_markdown(table([], [row(cell("1"), cell("2"))]))
        self.assertEqual(md.splitlines(), ["|  |  |", "|---|---|", "| 1 | 2 |"])

    def test_pipes_in_cells_are_escaped(self):
        md = table_markdown(table([row(cell("a|b"))], [row(cell("c|d"))]))
        self.assertIn("a\\|b", md)
        self.assertIn("c\\|d", md)


if __name__ == "__main__":
    unittest.main()
