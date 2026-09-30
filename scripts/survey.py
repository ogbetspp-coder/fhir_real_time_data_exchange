"""Read every .docx under a folder and tally what the reader does with them.

    uv run --frozen python scripts/survey.py path/to/folder

For a folder of labels of your own: it prints how many documents the reader reads, how many it
refuses and for which reasons, and how many carry counted list labels, and writes nothing. It
never prints a document's text, only file names, refusal codes and the reader's refusal details
(which name elements, fonts and codes). Add ``--files`` to list each file's outcome.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

from label_docx.reader import READER_VERSION, DocxRefusedError, read_docx


def outcome(path: Path) -> tuple[str, str, bool]:
    """The refusal code and detail, or "read" and whether any list label shows a number."""
    try:
        paragraphs = read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        return refused.code, refused.detail, False
    counted = any(
        p.numbering is not None and p.numbering.text and any(c.isalnum() for c in p.numbering.text)
        for p in paragraphs
    )
    return "read", "", counted


def main() -> int:
    """Print the tally; 0 whatever the outcomes."""
    parser = argparse.ArgumentParser(description="Tally the reader's outcomes over a folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--files", action="store_true", help="list each file's outcome")
    args = parser.parse_args()
    paths = sorted(args.folder.rglob("*.docx"))
    results = {path: outcome(path) for path in paths}
    codes = collections.Counter(code for code, _, _ in results.values())
    out = sys.stdout
    out.write(f"{READER_VERSION}: {len(paths)} documents\n")
    for code, count in codes.most_common():
        out.write(f"  {count:5}  {code}\n")
    out.write(f"  {sum(c for _, _, c in results.values()):5}  read, with numbered list labels\n")
    reasons = collections.Counter((c, d) for c, d, _ in results.values() if c != "read")
    if reasons:
        out.write("refusals:\n")
        for (code, detail), count in reasons.most_common():
            out.write(f"  {count:5}  {code}: {detail}\n")
    if args.files:
        for path, (code, detail, _) in results.items():
            out.write(f"{path.relative_to(args.folder)}: {code} {detail}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
