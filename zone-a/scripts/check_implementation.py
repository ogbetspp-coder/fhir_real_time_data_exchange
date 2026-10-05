"""Report what changed between two CCDS versions and where each label stands on it.

    uv run --frozen python scripts/check_implementation.py OLD.docx NEW.docx LABELS.json \
        [--language en] [--wordings FILE] [--as-of YYYY-MM-DD] [--out FILE]
    uv run --frozen python scripts/check_implementation.py OLD.docx NEW.docx --template de,fr

OLD and NEW are the two CCDS versions (.docx), read with zone_a.certified. LABELS is a JSON list of
the labels to check, each ``{"file": "<path>", "language": "<code>"}`` with, optionally, its
``market`` and the date it must carry the change by (``due``, YYYY-MM-DD); a label is a .docx or
an ePI Bundle, and a relative path is taken from the list's own folder. ``--language`` names the
CCDS's language (default ``en``). ``--wordings`` gives each edit's wording in other languages
(zone_a.implementation.load_wordings); ``--as-of`` is the day lateness is counted on, required
when a label has a date. The report (zone_a.implementation.report) is written as canonical JSON
to FILE, or to standard output. ``--template`` writes, instead, a wording file to fill in for
the languages named. See docs/design/ccds-implementation-check.md.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

from zone_a.canonical_json import canonical_json
from zone_a.certified import read_docx
from zone_a.implementation import changes, edits, read_label, report, template


def main(argv: list[str] | None = None) -> int:
    """Writes the report, or with ``--template`` a wording file to fill in.

    Returns:
        The exit status: 0 when the report or the template was written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="the earlier CCDS version (.docx)")
    parser.add_argument("new", type=Path, help="the later CCDS version (.docx)")
    parser.add_argument("labels", type=Path, nargs="?", help="JSON list of labels")
    parser.add_argument("--language", default="en", help="the CCDS's language (default en)")
    parser.add_argument("--wordings", type=Path, help="each edit's wording in other languages")
    parser.add_argument("--as-of", type=datetime.date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--template", help="comma-separated languages: write a wording file")
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    arguments = parser.parse_args(argv)
    old_bytes, new_bytes = arguments.old.read_bytes(), arguments.new.read_bytes()
    old, new = read_docx(old_bytes), read_docx(new_bytes)
    if arguments.template is not None:
        languages = [code.strip() for code in arguments.template.split(",") if code.strip()]
        result: object = template(edits(old, new, changes(old, new)), arguments.language, languages)
    else:
        if arguments.labels is None:
            parser.error("give the labels to check, or --template")
        entries = json.loads(arguments.labels.read_text(encoding="utf-8"))
        labels = [
            read_label(
                entry["file"],
                entry["language"],
                (arguments.labels.parent / entry["file"]).read_bytes(),
                market=entry.get("market"),
                due=None if entry.get("due") is None else datetime.date.fromisoformat(entry["due"]),
            )
            for entry in entries
        ]
        wordings = (
            None
            if arguments.wordings is None
            else json.loads(arguments.wordings.read_text(encoding="utf-8"))
        )
        sources = {
            "old": hashlib.sha256(old_bytes).hexdigest(),
            "new": hashlib.sha256(new_bytes).hexdigest(),
        }
        try:
            result = report(
                old, new, arguments.language, labels, sources, wordings, arguments.as_of
            )
        except ValueError as problem:
            parser.error(str(problem))
    text = canonical_json(result) + "\n"
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
