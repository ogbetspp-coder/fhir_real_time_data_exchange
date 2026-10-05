"""Report what changed between two CCDS versions and where each label stands on it.

    uv run --frozen python scripts/check_implementation.py OLD.docx NEW.docx LABELS.json \
        [--language en] [--out FILE]

OLD and NEW are the two CCDS versions (.docx), read with zone_a.certified. LABELS is a JSON list of
the labels to check, each ``{"file": "<path>", "language": "<code>"}`` (a .docx or an ePI Bundle; a
relative path is taken from the list's own folder), and ``--language`` names the CCDS's language
(default ``en``). The report (zone_a.implementation.report) is written as canonical JSON to FILE, or
to standard output. See docs/design/ccds-implementation-check.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from zone_a.canonical_json import canonical_json
from zone_a.certified import read_docx
from zone_a.implementation import read_label, report


def main(argv: list[str] | None = None) -> int:
    """Writes the report.

    Returns:
        The exit status: 0 when the report was written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="the earlier CCDS version (.docx)")
    parser.add_argument("new", type=Path, help="the later CCDS version (.docx)")
    parser.add_argument("labels", type=Path, help="JSON list of {file, language}")
    parser.add_argument("--language", default="en", help="the CCDS's language (default en)")
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    arguments = parser.parse_args(argv)
    old_bytes, new_bytes = arguments.old.read_bytes(), arguments.new.read_bytes()
    entries = json.loads(arguments.labels.read_text(encoding="utf-8"))
    labels = [
        read_label(
            entry["file"], entry["language"], (arguments.labels.parent / entry["file"]).read_bytes()
        )
        for entry in entries
    ]
    sources = {
        "old": hashlib.sha256(old_bytes).hexdigest(),
        "new": hashlib.sha256(new_bytes).hexdigest(),
    }
    result = report(read_docx(old_bytes), read_docx(new_bytes), arguments.language, labels, sources)
    text = canonical_json(result) + "\n"
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
