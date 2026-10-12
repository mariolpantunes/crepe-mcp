"""tests/test_review_marks.py — tracked changes and comments read from a pandoc AST (no pandoc needed).

Run with: python -m unittest discover -s tests -p "test_review_marks.py" -v
"""

import unittest

from crepe_mcp.reader.review_marks import collect


def word(text: str) -> dict:
    return {"t": "Str", "c": text}


SPACE = {"t": "Space"}


def words(text: str) -> list[dict]:
    out: list[dict] = []
    for index, part in enumerate(text.split()):
        out += ([SPACE] if index else []) + [word(part)]
    return out


def span(name: str, attrs: dict[str, str], inlines: list[dict]) -> dict:
    return {"t": "Span", "c": [["", [name], [[k, v] for k, v in attrs.items()]], inlines]}


def start(ident: str, body: str, author: str = "Carol") -> dict:
    return span("comment-start", {"id": ident, "author": author, "date": "2026-10-03"}, words(body))


def end(ident: str) -> dict:
    return span("comment-end", {"id": ident}, [])


def para(inlines: list[dict]) -> dict:
    return {"t": "Para", "c": inlines}


def header(text: str) -> dict:
    return {"t": "Header", "c": [1, ["", [], []], words(text)]}


class TestCollect(unittest.TestCase):
    def comments(self, blocks: list[dict]) -> dict[str, dict[str, str]]:
        return {m["text"]: m for m in collect({"blocks": blocks}) if m["kind"] == "comment"}

    def test_anchor_is_the_text_between_start_and_end(self):
        blocks = [para([word("Check")] + [SPACE, start("0", "Needs a citation.")] + words("this claim") + [end("0")])]
        found = self.comments(blocks)
        self.assertEqual(found["Needs a citation."]["anchor"], "this claim")

    def test_nested_comment_text_does_not_enter_the_outer_anchor(self):
        inlines = (
            [start("0", "Outer note")]
            + words("claim")
            + [SPACE, start("1", "Inner note")]
            + words("here")
            + [end("1"), SPACE, word("ok"), end("0")]
        )
        found = self.comments([para(inlines)])
        self.assertEqual(found["Outer note"]["anchor"], "claim here ok")
        self.assertEqual(found["Inner note"]["anchor"], "here")

    def test_comment_spanning_paragraphs_keeps_its_anchor(self):
        blocks = [
            para([start("0", "Rework this")] + words("first para")),
            para(words("second para") + [end("0"), SPACE, word("tail")]),
        ]
        self.assertEqual(self.comments(blocks)["Rework this"]["anchor"], "first para second para")

    def test_unclosed_comment_keeps_what_follows_it_in_its_paragraph(self):
        found = self.comments([para([start("0", "Open")] + words("dangling text"))])
        self.assertEqual(found["Open"]["anchor"], "dangling text")

    def test_changes_and_sections_in_document_order(self):
        blocks = [
            header("Intro"),
            para(
                words("The method is")
                + [SPACE, span("insertion", {"author": "Alice", "date": "d1"}, words("very")), SPACE]
                + [span("deletion", {"author": "Bob", "date": "d2"}, words("slow"))]
            ),
            header("Results"),
            para([start("0", "Cite", "Dana")] + words("claim") + [end("0")]),
        ]
        marks = collect({"blocks": blocks})
        self.assertEqual(
            [(m["kind"], m["text"], m["author"], m["section"]) for m in marks],
            [
                ("insertion", "very", "Alice", "Intro"),
                ("deletion", "slow", "Bob", "Intro"),
                ("comment", "Cite", "Dana", "Results"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
