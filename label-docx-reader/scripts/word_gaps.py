"""Record where Word draws each list label and its text in legacy-drawn (macOS with Word).

    uv run --frozen python scripts/word_gaps.py record

Word saves corpus/numbering-cases/legacy-drawn.docx as PDF (``label_docx.word``: one script at a
time, never with a document of that name open, on a copy in Word's sandbox, closed by name), and
``scripts/ink_bands.swift`` draws each page and finds, in each row's band, the red ink (the label)
and the blue ink (the text). Word's PDF is read for ink, not for its text: where a PDF places a
character is not where Word drew it. ``word-gaps.json`` beside the case keeps, row by row (as
``numbering_cases.drawn_rows`` lists them), where the label's ink starts and ends and the text's
starts and ends, in points from the page's left edge; ``tests/test_word_gaps.py`` holds the
reader to them without Word.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numbering_cases
from label_docx import word

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "corpus" / "numbering-cases" / "legacy-drawn.docx"
RECORD = CASE.with_name("word-gaps.json")
TOOL = ROOT / "scripts" / "ink_bands.swift"
# Pixels a point: an ink edge is found to a sixth of a point.
SCALE = 6
TOP = 36.0  # the page's top margin, 720 twips
LINE = numbering_cases.DRAWN_LINE / 20


def bands(pdf: Path) -> list[list[float]]:
    """Each band's red start and end and blue start and end, page by page, -1 where none."""
    with tempfile.TemporaryDirectory() as folder:
        tool = Path(folder) / "ink_bands"
        subprocess.run(["swiftc", "-O", str(TOOL), "-o", str(tool)], check=True)
        done = subprocess.run(
            [str(tool), str(pdf), str(TOP), str(LINE), str(numbering_cases.DRAWN_ROWS), str(SCALE)],
            check=True,
            capture_output=True,
            text=True,
        )
    return [[float(x) for x in line.split("\t")[2:]] for line in done.stdout.splitlines()]


def drawn(case: Path) -> list[list[float]]:
    """Word's PDF of ``case``, as ink: one [label start, label end, text start, text end] a row."""
    word.CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=word.CONTAINER) as folder:
        copy, pdf = Path(folder) / case.name, Path(folder) / f"{case.stem}.pdf"
        shutil.copyfile(case, copy)
        done = word._osascript(["osascript", "-", str(copy), str(pdf), case.name], word.PRINT)
        if done.returncode != 0 or not pdf.exists():
            raise SystemExit(f"{case.name}: Word failed: {done.stderr.strip()}")
        found = bands(pdf)
    rows = len(numbering_cases.drawn_rows())
    if any(band != [-1.0] * 4 for band in found[rows:]):
        raise SystemExit(f"{case.name}: ink below the last row")
    if len(found) < rows or any(-1.0 in band for band in found[:rows]):
        raise SystemExit(f"{case.name}: a row without its label's or its text's ink")
    return found[:rows]


def main() -> int:
    """Ask Word and write the record."""
    parser = argparse.ArgumentParser(description="Record Word's drawn list labels.")
    parser.add_subparsers(dest="command", required=True).add_parser("record")
    parser.parse_args()
    rows = drawn(CASE)
    record = {
        "application": word.word_version(),
        "method": (
            "Word saved the case as PDF; each page drawn at the scale's pixels a point; in each "
            "row's band (its exact line), where the red ink (the label) and the blue ink (the "
            "text) start and end, in points from the page's left edge"
        ),
        "recorded": datetime.date.today().isoformat(),
        "rows": rows,
        "scale": SCALE,
        "sha256": hashlib.sha256(CASE.read_bytes()).hexdigest(),
    }
    RECORD.write_text(json.dumps(record, indent=1) + "\n", "utf-8")
    sys.stdout.write(f"wrote {RECORD.relative_to(ROOT)}: {len(rows)} rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
