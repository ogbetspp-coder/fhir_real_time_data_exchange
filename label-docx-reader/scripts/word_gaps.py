"""Record where Word draws each list label and its text in the legacy-drawn cases (macOS, Word).

    uv run --frozen python scripts/word_gaps.py record

Word saves each corpus/numbering-cases/legacy-drawn*.docx as PDF (``label_docx.word``: one script at
a time, never with a document of that name open, on a copy in Word's sandbox, closed by name), and
``scripts/ink_bands.swift`` draws each page and finds, in each row's band, the red ink (the label)
and the blue ink (the text; a second line's is green, and left out). Word's PDF is read for ink, not
for its text: where a PDF places a character is not where Word drew it. ``word-gaps.json`` beside
the cases keeps, case by case and row by row (as ``numbering_cases.ROWS`` lists them), where the
label's ink starts and ends and the text's starts and ends, in points from the page's left edge;
``tests/test_word_gaps.py`` holds the reader to them without Word.
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
FOLDER = ROOT / "corpus" / "numbering-cases"
RECORD = FOLDER / "word-gaps.json"
TOOL = ROOT / "scripts" / "ink_bands.swift"
# Pixels a point: an ink edge is found to a sixth of a point.
SCALE = 6
TOP = 36.0  # the page's top margin, 720 twips
LINE = numbering_cases.DRAWN_LINE / 20


def bands(
    pdf: Path, line: float = LINE, rows: int = numbering_cases.DRAWN_ROWS
) -> list[list[float]]:
    """Each band's red and blue ink across, then down, page by page, -1 where none."""
    with tempfile.TemporaryDirectory() as folder:
        tool = Path(folder) / "ink_bands"
        subprocess.run(["swiftc", "-O", str(TOOL), "-o", str(tool)], check=True)
        done = subprocess.run(
            [str(tool), str(pdf), str(TOP), str(line), str(rows), str(SCALE)],
            check=True,
            capture_output=True,
            text=True,
        )
    return [[float(x) for x in line.split("\t")[2:]] for line in done.stdout.splitlines()]


def drawn(case: Path) -> list[list[float]]:
    """Word's PDF of ``case``, as ink, a row each.

    Its label's and text's start and end across, then down (``inside``).
    """
    word.CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=word.CONTAINER) as folder:
        copy, pdf = Path(folder) / case.name, Path(folder) / f"{case.stem}.pdf"
        shutil.copyfile(case, copy)
        done = word._osascript(["osascript", "-", str(copy), str(pdf), case.name], word.PRINT)
        if done.returncode != 0 or not pdf.exists():
            raise SystemExit(f"{case.name}: Word failed: {done.stderr.strip()}")
        height, per_page = numbering_cases.GEOMETRY[case.stem]
        found = bands(pdf, height / 20, per_page)
    rows = len(numbering_cases.ROWS[case.stem]())
    if any(band != [-1.0] * 8 for band in found[rows:]):
        raise SystemExit(f"{case.name}: ink below the last row")
    if len(found) < rows or any(-1.0 in band for band in found[:rows]):
        raise SystemExit(f"{case.name}: a row without its label's or its text's ink")
    for index, band in enumerate(found[:rows]):
        if not inside(case.stem, index, band):
            raise SystemExit(f"{case.name}: row {index + 1}'s ink reaches its band's edge")
    return found[:rows]


# How far from its band's edges a row's ink must stay, in points: a row whose ink comes nearer
# may have ink in its neighbour's band.
CLEARANCE = 0.5


def inside(case: str, index: int, band: list[float]) -> bool:
    """Whether a row's ink, down the page, keeps clear of its band's top and bottom edges."""
    height, per_page = numbering_cases.GEOMETRY[case]
    top = TOP + (index % per_page) * height / 20
    return all(top + CLEARANCE <= y <= top + height / 20 - CLEARANCE for y in band[4:])


def main() -> int:
    """Ask Word and write the record."""
    parser = argparse.ArgumentParser(description="Record Word's drawn list labels.")
    parser.add_subparsers(dest="command", required=True).add_parser("record")
    parser.parse_args()
    cases = {
        path.stem: {"rows": drawn(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (FOLDER / f"{name}.docx" for name in numbering_cases.ROWS)
    }
    record = {
        "application": word.word_version(),
        "cases": cases,
        "method": (
            "Word saved the case as PDF; each page drawn at the scale's pixels a point; in each "
            "row's band (its exact line), where the red ink (the label) and the blue ink (the "
            "text) start and end, in points from the page's left edge"
        ),
        "recorded": datetime.date.today().isoformat(),
        "scale": SCALE,
    }
    RECORD.write_text(json.dumps(record, indent=1) + "\n", "utf-8")
    total = sum(len(case["rows"]) for case in cases.values())
    sys.stdout.write(f"wrote {RECORD.relative_to(ROOT)}: {len(cases)} cases, {total} rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
