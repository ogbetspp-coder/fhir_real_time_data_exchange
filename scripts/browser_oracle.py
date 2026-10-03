"""Ask a browser (headless Chrome) what each ePI section shows, and hold the ePI reader to it.

    uv run --frozen python scripts/browser_oracle.py record corpus/ema-epi
    uv run --frozen python scripts/browser_oracle.py compare path/to/epi.json [...]

A browser is the reference for an ePI's text, as Word is for a .docx. For each document the
oracle writes one page holding every section's div, which Chrome parses as HTML (each div into
its own element, as a viewer inserts it), lays out with its default style sheet, and reads back:

- the text a reader of the page would copy: the section selected whole, as Chrome serialises a
  selection (whitespace collapsed by the layout, a line break between blocks, a tab between
  table cells), with each picture as U+FFFC, the reader's character for one;
- for each text node, where its text lands in that serialisation (the node selected alone) and
  what the browser computed for it: font weight and style, colour, font size, the background
  painted under it, the decorations (underline, line-through) of every element it inherits them
  from, the vertical alignment and relative shift of each inline element around it, and the
  borders of those inline elements.

The comparison is line by line: both sides are split into lines at every line break, tab and
paragraph end (the browser's separators, the reader's paragraphs and its U+000A), empty lines
dropped. Each line must have the same characters, and each character the same marks: the
browser's facts turned into the reader's kinds by the reader's own thresholds (bold at weight
600, faint under a contrast of 1.33:1 or under 2pt, a colour nearly black as none...). The
thresholds are definitions; the facts they are applied to are the browser's.

``record`` writes ``browser.json`` for a corpus set: for each document and each section (in the
order ``walk`` gives), a SHA-256 of the browser's lines and of their marks, which
``tests/test_browser_oracle.py`` holds the reader to without a browser. ``compare`` prints the
verdict for any files and writes nothing, never the text itself.

Each list item's marker ("1.", "\u2022"...) is read from Chrome's accessibility tree, the
marker it draws, and compared with the reader's. Not compared: the layout the reader refuses
or states as a residual.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from label_docx import epi
from label_docx.browser import (
    browser_lines,
    browser_markers,
    browser_sections,
    chrome_version,
    digest,
    first_difference,
    markers_digest,
    reader_lines,
    reader_markers,
)
from lock import MANIFESTS


def sections(path: Path) -> list[epi.Section]:
    """The document's sections that hold a div, in ``walk`` order."""
    document = epi.read_epi(path.read_bytes())
    return [s for s in epi.walk(document.sections) if s.paragraphs or s.refusal is not None]


def divs(path: Path) -> list[str]:
    """The sections' divs, in the order ``sections`` gives them."""
    out: list[str] = []

    def visit(raw: dict[str, Any]) -> None:
        div = raw.get("text", {}).get("div")
        paragraphs, refusal, _ = epi.read_div(div) if isinstance(div, str) else ((), None, ())
        if paragraphs or refusal is not None:
            out.append(div)
        for child in raw.get("section", []):
            visit(child)

    bundle = json.loads(path.read_bytes())
    (composition,) = [
        e["resource"] for e in bundle.get("entry", []) if "section" in e.get("resource", {})
    ]
    for raw in composition["section"]:
        visit(raw)
    return out


def check(path: Path) -> tuple[list[dict[str, str] | None], list[str]]:
    """The browser's digest of each section, and a verdict for each."""
    read = sections(path)
    shown = browser_sections(divs(path))
    drawn = browser_markers(divs(path))
    digests: list[dict[str, str] | None] = []
    verdicts: list[str] = []
    for section, browser, markers in zip(read, shown, drawn, strict=True):
        if browser["error"] is not None:
            digests.append(None)
            verdicts.append(f"browser text not placed: {browser['error']}")
            continue
        lines = browser_lines(browser)
        digests.append({**digest(lines), "markers": markers_digest(markers)})
        if section.refusal is not None:
            verdicts.append(f"reader refuses: {section.refusal.code}")
            continue
        mine = reader_lines(section.paragraphs)
        if mine != lines:
            verdicts.append("differs: " + first_difference(lines, mine))
        elif reader_markers(section.paragraphs) != markers:
            verdicts.append("differs: a list marker")
        else:
            verdicts.append("agrees")
    return digests, verdicts


def main() -> int:
    """Record a corpus set's answers, or compare files; 1 if the reader differs from Chrome."""
    parser = argparse.ArgumentParser(description="Hold the ePI reader to a browser.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("record", help="write browser.json for a corpus set").add_argument(
        "folder", type=Path
    )
    commands.add_parser("compare", help="compare files, writing nothing").add_argument(
        "files", type=Path, nargs="+"
    )
    args = parser.parse_args()
    paths = (
        sorted(p for p in args.folder.glob("*.json") if p.stem not in MANIFESTS)
        if args.command == "record"
        else args.files
    )
    answers: dict[str, list[dict[str, str] | None]] = {}
    differs = False
    for path in paths:
        try:
            digests, verdicts = check(path)
        except epi.EpiRefusedError as refused:
            sys.stdout.write(f"{path.name}: reader refuses the document: {refused}\n")
            continue
        answers[path.name] = digests
        counts = collections.Counter(verdict.split(":")[0] for verdict in verdicts)
        sys.stdout.write(f"{path.name}: {json.dumps(counts, sort_keys=True)}\n")
        for index, verdict in enumerate(verdicts):
            if not verdict.startswith(("agrees", "reader refuses")):
                differs = True
                sys.stdout.write(f"  section {index + 1}: {verdict}\n")
        sys.stdout.flush()
    if args.command == "record":
        record = {
            "application": chrome_version(),
            "method": (
                "each section's div parsed as HTML into its own element of one page, laid out "
                "with the browser's default style sheet; the text of the section selected "
                "whole, and each text node's place in it and computed style"
            ),
            "recorded": datetime.date.today().isoformat(),
            "sections": answers,
        }
        target = args.folder / "browser.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {target}\n")
    return 1 if differs else 0


if __name__ == "__main__":
    raise SystemExit(main())
