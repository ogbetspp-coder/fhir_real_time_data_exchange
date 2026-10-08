"""Record what Word draws for each drawing case (macOS, Word).

    uv run --frozen python scripts/word_drawn.py record

Word saves each corpus/drawing-cases/*.docx as PDF (``label_docx.word``: one script at a time,
never with a document of that name open, on a copy in Word's sandbox, closed by name), and
``scripts/ink_drawn.swift`` draws its pages. ``word-drawn.json`` beside the cases keeps, case by
case: for a picture case, the box of the picture's red and blue pixels, a digest of them and the
ink around and elsewhere; for a row case, row by row (as ``drawing_cases`` lays them out), where
the red and the blue ink start and end, the ink between them, and the colours most of the row is
painted. ``tests/test_word_drawn.py`` holds the reader to them without Word.
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

import drawing_cases
from label_docx import word

ROOT = Path(__file__).resolve().parents[1]
FOLDER = drawing_cases.FOLDER
RECORD = FOLDER / "word-drawn.json"
TOOL = ROOT / "scripts" / "ink_drawn.swift"
# Pixels a point.
SCALE = 4


def measured(tool: Path, pdf: Path, case: drawing_cases.Case) -> list[list[object]]:
    """What the tool finds in Word's PDF of ``case``: one entry a row, or the picture's."""
    if case.rows:
        arguments = ["rows", str(pdf), str(drawing_cases.TOP), str(drawing_cases.LINE)]
        arguments += [str(drawing_cases.PAGE_ROWS), str(SCALE)]
    else:
        arguments = ["picture", str(pdf), str(SCALE)]
    done = subprocess.run([str(tool), *arguments], check=True, capture_output=True, text=True)
    lines = [line.split("\t") for line in done.stdout.splitlines()]
    if not case.rows:
        x, y, width, height, digest, ring, elsewhere = lines[0]
        return [[int(x), int(y), int(width), int(height), digest, int(ring), int(elsewhere)]]
    out: list[list[object]] = []
    for _page, _band, *edges, between, colours in lines:
        out.append([*(float(e) for e in edges), int(between), colours])
    if any(entry[4] != -1 or entry[5] for entry in out[case.rows :]):
        raise SystemExit("ink below the last row")
    return out[: case.rows]


def drawn(tool: Path, name: str, case: drawing_cases.Case) -> list[list[object]]:
    """Word's PDF of the case, measured."""
    word.CONTAINER.mkdir(parents=True, exist_ok=True)
    path = FOLDER / f"{name}.docx"
    with tempfile.TemporaryDirectory(dir=word.CONTAINER) as folder:
        copy, pdf = Path(folder) / path.name, Path(folder) / f"{name}.pdf"
        shutil.copyfile(path, copy)
        done = word._osascript(["osascript", "-", str(copy), str(pdf), path.name], word.PRINT)
        if done.returncode != 0 or not pdf.exists():
            raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
        return measured(tool, pdf, case)


def main() -> int:
    """Ask Word and write the record."""
    parser = argparse.ArgumentParser(description="Record what Word draws for the drawing cases.")
    parser.add_subparsers(dest="command", required=True).add_parser("record")
    parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        tool = Path(folder) / "ink_drawn"
        subprocess.run(["swiftc", "-O", str(TOOL), "-o", str(tool)], check=True)
        cases = {}
        for name, case in drawing_cases.CASES.items():
            data = (FOLDER / f"{name}.docx").read_bytes()
            cases[name] = {
                "drawn": drawn(tool, name, case),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            sys.stdout.write(f"{name}: drawn\n")
            sys.stdout.flush()
    record = {
        "application": word.word_version(),
        "cases": cases,
        "method": (
            "Word saved the case as PDF; each page drawn at the scale's pixels a point. A picture "
            "case: the box of the clearly red or blue pixels (left, top, width, height in "
            "pixels), the first 16 hex digits of the SHA-256 of its pixels, the pixels not white "
            "within 12 of it and the coloured pixels elsewhere. A row case, each row's band (its "
            "exact line): where the red and the blue ink start and end (points from the page's "
            "left edge, -1 for none), the pixels not white between them (-1 where either is "
            "missing), and the band's three commonest colours but white (RRGGBB:pixels)"
        ),
        "recorded": datetime.date.today().isoformat(),
        "scale": SCALE,
    }
    RECORD.write_text(json.dumps(record, indent=1) + "\n", "utf-8")
    sys.stdout.write(f"wrote {RECORD.relative_to(ROOT)}: {len(cases)} cases\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
