"""Worker process for the page geometry of table pages: ``python -m crepe_mcp.reader.geometry_worker PDF TASKS OUT``.

TASKS is a pickle written by the parent in a private temporary folder: a list of (page number, Page, Metrics, text
block). The worker computes the geometry of each page and pickles {number: geometry} to OUT. It is started as a plain
subprocess, not through multiprocessing, so that it never re-imports the program that started the server.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pymupdf as fitz

from crepe_mcp.reader.assets import _geometry


def main(argv: list[str]) -> int:
    pdf_path, tasks_path, out_path = argv[1:4]
    tasks = pickle.loads(Path(tasks_path).read_bytes())
    results = {}
    with fitz.open(pdf_path) as doc:
        for number, page, metrics, text_block in tasks:
            results[number] = _geometry(doc[number - 1], page, True, metrics, text_block)
    Path(out_path).write_bytes(pickle.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
