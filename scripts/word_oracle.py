"""Ask Microsoft Word for the list labels it draws, and hold the reader to them (macOS only).

    uv run --frozen python scripts/word_oracle.py record corpus/numbering-cases
    uv run --frozen python scripts/word_oracle.py compare path/to/label.docx [...]

Word is the reference for list labels: a paragraph's ``listString`` is the label Word draws
before it. ``record`` asks Word for every .docx in a corpus set and writes the answers to the
set's ``word.json``, which ``tests/test_word_oracle.py`` holds the reader to without Word.
``compare`` prints Word's labels beside the reader's for any files and writes nothing, so a
confidential label can be checked on one machine and never enter the repository. Both print, for
each file, whether the reader agrees with Word, differs, or refuses; never the paragraphs' text.

Word runs sandboxed: each file is copied into Word's container, where it opens without a
permission prompt, and removed after. Word reports a Symbol-font character as the code it stores
(a bullet as U+F0B7); ``as_drawn`` maps it through the reader's Symbol table before comparing. A
private-use code in any other font is one the reader refuses, so the mapping cannot hide a
difference.
"""

from __future__ import annotations

import argparse
import datetime
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from label_docx.reader import SYMBOL_FONT, DocxRefusedError, read_docx

WORD = Path("/Applications/Microsoft Word.app")
CONTAINER = Path.home() / "Library/Containers/com.microsoft.Word/Data"
RECORD, UNIT = "\x1e", "\x1f"

# The label of every paragraph Word counts as a list item, in document order, and their number.
SCRIPT = """
on run argv
  -- Resolved here: inside the tell block, Word would be asked to make the file reference.
  set target to (POSIX file (item 1 of argv)) as string
  tell application "Microsoft Word"
    open file name target
    -- Open returns before the document is loaded; wait for it by name, up to a minute.
    repeat 600 times
      try
        if (name of every document) contains {item 2 of argv} then exit repeat
      end try
      delay 0.1
    end repeat
    set d to document (item 2 of argv)
    set out to {}
    repeat with i from 1 to (count of paragraphs of d)
      set f to list format of (text object of paragraph i of d)
      if list type of f is not list no numbering then set end of out to (list string of f)
    end repeat
    close d saving no
  end tell
  set AppleScript's text item delimiters to (character id 30)
  return ((count of out) as text) & (character id 31) & (out as text)
end run
"""


def word_version() -> str:
    """Word's version, from its bundle."""
    with (WORD / "Contents/Info.plist").open("rb") as info:
        return f"Microsoft Word {plistlib.load(info)['CFBundleShortVersionString']} (macOS)"


def word_labels(path: Path) -> list[str]:
    """The labels Word draws for ``path``'s list items, in document order."""
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        shutil.copyfile(path, copy)
        # Word's scripting fails now and then while it is still loading; one retry is enough.
        for attempt in (1, 2):
            done = subprocess.run(
                ["osascript", "-", str(copy), path.name],
                input=SCRIPT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            if done.returncode == 0:
                break
            if attempt == 2:
                raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    count, _, joined = done.stdout.rstrip("\n").partition(UNIT)
    labels = joined.split(RECORD) if int(count) else []
    if len(labels) != int(count):
        raise SystemExit(f"{path.name}: Word's answer did not parse")
    return labels


def reader_labels(path: Path) -> list[str | None] | str:
    """The reader's labels for ``path``'s list items, or its refusal code."""
    try:
        paragraphs = read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        return refused.code
    return [p.numbering.text for p in paragraphs if p.numbering is not None and p.numbering.num_id]


def as_drawn(label: str) -> str:
    """Word's ``label`` with each stored Symbol code (U+F000 plus the code) as its character."""
    return "".join(
        SYMBOL_FONT.get(ord(c) - 0xF000, c) if 0xF000 <= ord(c) <= 0xF0FF else c for c in label
    )


def verdict(word: list[str], reader: list[str | None] | str) -> str:
    """Whether the reader agrees with Word: agrees, refuses (code) or differs (where)."""
    if isinstance(reader, str):
        return f"reader refuses: {reader}"
    if len(reader) != len(word):
        return f"differs: Word has {len(word)} list items, the reader {len(reader)}"
    for index, (ours, theirs) in enumerate(zip(reader, word, strict=True)):
        if ours is None or ours != as_drawn(theirs):
            return f"differs at list item {index + 1}: Word {theirs!r}, reader {ours!r}"
    return "agrees"


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
    differs = False
    for path in paths:
        word = word_labels(path)
        answers[path.name] = word
        result = verdict(word, reader_labels(path))
        differs = differs or result.startswith("differs")
        sys.stdout.write(f"{path.name}: {result}\n")
        if result.startswith("reader refuses"):
            sys.stdout.write(f"  Word draws: {json.dumps(word)}\n")
    if args.command == "record":
        record = {
            "application": word_version(),
            "recorded": datetime.date.today().isoformat(),
            "labels": answers,
        }
        target = args.folder / "word.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {target}\n")
    return 1 if differs else 0


if __name__ == "__main__":
    raise SystemExit(main())
