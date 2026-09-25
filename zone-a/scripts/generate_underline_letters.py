"""Write, or check, the letters an underline cannot change, for the TypeScript port.

    uv run --frozen python scripts/generate_underline_letters.py          # write
    uv run --frozen python scripts/generate_underline_letters.py --check  # fail on drift

zone_a.underline counts a code point as a letter when it is alphabetic and its Unicode name
starts with LATIN, GREEK or CYRILLIC. JavaScript has no character names, so the authority
importer's port (src/authority/underline.ts) reads the same set from
src/authority/data/underline-letters.json, as code point ranges computed here from Python's
Unicode database. tests/test_underline.py checks the file is current.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from zone_a.underline import is_underline_letter

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "src" / "authority" / "data" / "underline-letters.json"


def ranges() -> list[list[int]]:
    """Inclusive code point ranges of the letters, in order."""
    found: list[list[int]] = []
    for code_point in range(0x110000):
        if not is_underline_letter(chr(code_point)):
            continue
        if found and found[-1][1] == code_point - 1:
            found[-1][1] = code_point
        else:
            found.append([code_point, code_point])
    return found


def render() -> str:
    """The file's content."""
    payload = {"unicodeVersion": unicodedata.unidata_version, "ranges": ranges()}
    return json.dumps(payload, separators=(",", ":")) + "\n"


def main() -> int:
    """Write the file, or with --check report whether it is current."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    content = render()
    if check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != content:
            sys.stderr.write(f"{TARGET} is out of date; run this script\n")
            return 1
        return 0
    TARGET.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
