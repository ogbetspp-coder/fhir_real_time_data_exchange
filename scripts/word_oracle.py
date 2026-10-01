"""Ask Microsoft Word for what it shows, and hold the reader to it (macOS only).

    uv run --frozen python scripts/word_oracle.py record corpus/numbering-cases
    uv run --frozen python scripts/word_oracle.py compare path/to/label.docx [...]

The questions asked and how each answer is judged are ``label_docx.word``. ``record`` asks Word
about every .docx in a corpus set and writes the answers to the set's ``word.json``, which
``tests/test_word_oracle.py`` holds the reader to without Word. ``compare`` prints the verdict
for any files and writes nothing, so a confidential label can be checked on one machine and
never enter the repository. Both print, for each file, whether the reader agrees with Word,
differs (where), or refuses; never the paragraphs' text.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from label_docx.word import ask, judge, word_version


def main() -> int:
    """Record a corpus set's answers, or compare files; 1 if the reader differs from Word."""
    parser = argparse.ArgumentParser(description="Hold the reader's list labels to Word's.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("record", help="write word.json for a corpus set").add_argument(
        "folder", type=Path
    )
    commands.add_parser("compare", help="compare files, writing nothing").add_argument(
        "files", type=Path, nargs="+"
    )
    args = parser.parse_args()
    paths = sorted(args.folder.glob("*.docx")) if args.command == "record" else args.files
    answers: dict[str, list[str]] = {}
    note_answers: dict[str, dict[str, list[str]]] = {}
    field_answers: dict[str, dict[str, list[str]]] = {}
    print_answers: dict[str, bool] = {}
    emphasis_answers: dict[str, dict[str, list[bool]]] = {}
    # A recording keeps each file's answers as it goes, so one stopped part way resumes there.
    progress = args.folder / ".word-progress.json" if args.command == "record" else None
    recorded: dict[str, dict[str, Any]] = (
        json.loads(progress.read_text("utf-8")) if progress and progress.exists() else {}
    )
    differs = False
    for path in paths:
        if not path.read_bytes().startswith(b"PK\x03\x04"):
            # A .doc under a .docx name makes Word convert it, and can leave a dialog open.
            if args.command == "record":
                raise SystemExit(f"{path.name}: not a .docx")
            sys.stdout.write(f"{path.name}: not a .docx; not sent to Word\n")
            continue
        try:
            kept = recorded[path.name] if path.name in recorded else ask(path)
            if progress:
                recorded[path.name] = kept
                progress.write_text(json.dumps(recorded), "utf-8")
        except SystemExit as failed:
            if args.command == "record":
                raise
            # One file Word cannot open or answer for does not stop a comparison of many.
            sys.stdout.write(f"{failed}\n")
            continue
        word = kept["drawn"]
        answers[path.name] = word
        if kept["notes"] is not None:
            note_answers[path.name] = kept["notes"]
        print_answers[path.name] = kept["prints"]
        emphasis_answers[path.name] = kept["emphasis"]
        if kept["fields"] is not None:
            field_answers[path.name] = kept["fields"]
        result = judge(path, kept)
        differs = differs or result.startswith("differs")
        sys.stdout.write(f"{path.name}: {result}\n")
        if result.startswith("reader refuses"):
            sys.stdout.write(f"  Word draws: {json.dumps(word)}\n")
        sys.stdout.flush()
    if args.command == "record":
        record = {
            "application": word_version(),
            "method": (
                "list labels: convert numbers to text, what each list item gained; note marks: "
                "saved as text, what Word wrote between markers around each mark; fields: the "
                "text between markers around each, as shown and after saving as PDF; prints: the "
                "whole text as shown and after saving as PDF, page numbers aside; emphasis: bold, "
                "italic, caps and strike of each body paragraph's text"
            ),
            "recorded": datetime.date.today().isoformat(),
            "drawn": answers,
            "notes": note_answers,
            "fields": field_answers,
            "prints": print_answers,
            "emphasis": emphasis_answers,
        }
        target = args.folder / "word.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {target}\n")
        if progress:
            progress.unlink(missing_ok=True)
    return 1 if differs else 0


if __name__ == "__main__":
    raise SystemExit(main())
