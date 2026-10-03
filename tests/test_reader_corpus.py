"""Structure facts of the real validation papers in ``$CREPE_READER_CORPUS/papers``.

Each paper is skipped when it is not in the workspace. Only facts checked against the PDFs are recorded: the
submission parts, the review round, the top-level section numbering of each manuscript copy, caption counts
where every caption was read, and the header and one row of tables read cell by cell. Record a new layout here
before changing a factor in ``config.json``.
"""

import os
import re
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from pdf_fixtures import IsolatedTestCase

from crepe_mcp.reader.store import DocumentStore, close_all

REAL_WORKSPACE = os.environ.get("CREPE_READER_CORPUS", "")
ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")


@dataclass(frozen=True)
class Expected:
    parts: tuple[tuple[str, int, int], ...]
    round: str
    top_level: tuple[str, ...]  # numbered level-1 headings in reading order (roman or decimal)
    captions: dict[str, int] = field(default_factory=dict)
    tables: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = field(default_factory=dict)  # id: (header, a row)


def decimal(last: int) -> tuple[str, ...]:
    return tuple(str(n) for n in range(1, last + 1))


def table_rows(content: str) -> list[tuple[str, ...]]:
    """Markdown table rows as squeezed cells."""
    return [
        squeezed(tuple(cell.replace("\\|", "|") for cell in line.strip().strip("|").split(" | ")))
        for line in content.splitlines()
        if line.startswith("|") and not line.startswith("|---")
    ]


def squeezed(cells: tuple[str, ...]) -> tuple[str, ...]:
    """Cells without spaces and with one minus sign: word spacing and the minus glyph are not facts of the table."""
    return tuple(re.sub(r"\s+", "", cell).replace("−", "-") for cell in cells)


CORPUS = {
    "Access-2026-40102_Proof_hi.pdf": Expected(
        (("cover", 1, 3), ("manuscript", 4, 16)), "first", ROMAN[:5], {"figure": 3, "table": 4},
        {
            "table:1": (("Dataset", "Metric", "Random", "Saliency", "GB", "GI", "DeepLIFT", "DeepSHAP", "A-Last",
                         "Rollout", "EXI{T}2"),
                        ("", "AF(↑)", "0.1348", "0.2354", "0.2141", "0.2170", "0.2158", "0.2155", "0.2014", "0.2256",
                         "0.2526")),
            "table:4": (("Dataset", "Origin", "Random", "Saliency", "GB", "GI", "DeepLIFT", "DeepSHAP", "A-Last",
                         "Rollout", "EXI{T}2"),
                        ("Benzen Concentration", "1.6428", "2.6177", "8.1451", "8.0705", "7.4212", "7.4356", "7.3790",
                         "5.9304", "6.0895", "8.0815")),
        },
    ),
    "Access-2026-40875_Proof_hi.pdf": Expected(
        (("cover", 1, 3), ("manuscript", 4, 16)), "first", ROMAN[:6],
        tables={"table:II": (("No.", "Check", "Failure condition", "Release evidence"),
                             ("3", "Exact duplicates", "Repeated serialized record", "Duplicate group identifiers"))},
    ),
    "Access-2026-41373_Proof_hi.pdf": Expected(
        (("cover", 1, 3), ("manuscript", 4, 17)), "first", ROMAN[:6], {"figure": 8, "table": 7},
        {
            "table:1": (("λ", "SR", "Comput. time (h)"), ("2", "−21775", "6.47")),
            "table:7": (("Algo.", "m", "SR", "Comput. time (h)", "Speed-up"), ("FP", "50", "−10807", "3.38", "480×")),
        },
    ),
    "IoT-70548-2026_Proof_hi.pdf": Expected(
        (("cover", 1, 3), ("manuscript", 4, 22)), "first", ROMAN[:7], {"figure": 11, "table": 11},
        {"table:I": (("Feature", "CoAP", "MQTT", "AMQP", "MQTT-SN"),
                     ("Transport Protocol", "UDP", "TCP", "TCP", "UDP"))},
    ),
    "NEUNET-D-26-04237.pdf": Expected((("cover", 1, 1), ("manuscript", 2, 23)), "", decimal(5), {"figure": 13}),
    "TDSC-2025-09-1631.R1_Proof_hi.pdf": Expected(
        (("cover", 1, 3), ("manuscript", 4, 21), ("manuscript", 22, 49), ("letter", 50, 50), ("responses", 51, 68)),
        "revision",
        ROMAN[:7] * 2,
        tables={
            "table:I": (("Symbol", "Meaning"), ("St", "Clients selected in round t.")),
            "table:II": (("Threat", "Coverage in QFL-Guard"),
                         ("Singleton isolation", "Server-facing AS padding; no DP credit.")),
            "table:VI": (("Dataset", "Bal. Acc. (%)", "kNN/PCA MI (bits)", "Latency (s)", "Energy (mJ)"),
                         ("Edge-IIoT▷", "80.9±1.2", "0.34±0.03", "2.33±0.12", "95")),
            "table:X": (("Dataset", "Config.", "Balanced Acc. (%) Overall", "Balanced Acc. (%) Cls 0",
                         "Balanced Acc. (%) Cls 1", "kNN/PCA MI (bits)"),
                        ("", "IID baseline", "93.51", "93.98", "93.04", "0.33")),
        },
    ),
    "TPDS-2026-08-0833_Proof_hi.pdf": Expected(
        (("cover", 1, 3), ("manuscript", 4, 14)), "first", decimal(11), {"figure": 2, "table": 5},
        {"table:2": (("Scenario", "Oracle", "Sessions", "Ctot", "Cavg", "Time (s)"),
                     ("", "ARBITRARY", "7.97 ± 0.07", "5.41 ± 0.16", "0.776 ± 0.021", "0.010"))},
    ),
    "oral-4560066-peer-review-v1.pdf": Expected(
        (("manuscript", 1, 19),), "", decimal(5), {"figure": 11, "table": 6},
        {"table:2": (tuple(f"Training Set {label}" for label in ("Epoch", "Time", "train/box_loss", "train/seg_loss",
                                                                 "train/cls_loss", "train/dfl_loss")),
                     ("1", "75.324", "1.570", "2.579", "3.822", "1.273"))},
    ),
}


@unittest.skipUnless(REAL_WORKSPACE and (Path(REAL_WORKSPACE) / "papers").is_dir(),
                     "set CREPE_READER_CORPUS to a workspace with the validation papers")
class TestValidationCorpus(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(close_all)

    def test_structure_facts(self):
        for name, expected in CORPUS.items():
            pdf = Path(REAL_WORKSPACE) / "papers" / name
            with self.subTest(paper=name):
                if not pdf.is_file():
                    self.skipTest(f"{name} is not in the workspace")
                store = DocumentStore.open(pdf)
                parts = tuple((s["kind"], s["first_page"], s["last_page"]) for s in store.segments())
                self.assertEqual(parts, expected.parts)
                self.assertEqual(store.structure()["round"], expected.round)
                scheme = r"[IVX]+" if expected.top_level[0] == "I" else r"\d+"
                top = tuple(
                    s["number"] for s in store.outline() if s["level"] == 1 and re.fullmatch(scheme, s["number"])
                )
                self.assertEqual(top, expected.top_level)
                for kind, count in expected.captions.items():
                    self.assertEqual(len(store.assets(kind)), count, kind)
                for asset_id, (header, row) in expected.tables.items():
                    asset = store.asset(asset_id)
                    self.assertIsNotNone(asset, asset_id)
                    rows = table_rows(asset["content"] if asset else "")
                    self.assertEqual(rows[0], squeezed(header), asset_id)
                    self.assertIn(squeezed(row), rows, asset_id)


if __name__ == "__main__":
    unittest.main()
