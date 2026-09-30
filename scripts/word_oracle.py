"""Ask Microsoft Word for the list labels it draws, and hold the reader to them (macOS only).

    uv run --frozen python scripts/word_oracle.py record corpus/numbering-cases
    uv run --frozen python scripts/word_oracle.py compare path/to/label.docx [...]

Word is the reference for list labels. For each document it reads the text, runs its own
"convert numbers to text" (which writes every list label into the paragraph, followed by the tab
or space after it), reads the text again, and closes the document without saving. What each list
item gained is what Word draws before it; three requests a document, where asking paragraph by
paragraph took Word over a minute for a long template.

``record`` asks Word about every .docx in a corpus set and writes the answers to the set's
``word.json``, which ``tests/test_word_oracle.py`` holds the reader to without Word. ``compare``
prints the verdict for any files and writes nothing, so a confidential label can be checked on one
machine and never enter the repository. Both print, for each file, whether the reader agrees with
Word, differs (at which list item), or refuses; never the paragraphs' text.

The reader's side is its label followed by the character its suffix names: a tab for ``tab`` and
for ``legacy`` (Word converts a Word 6 level's gap to a tab), a space for ``space``, nothing for
``nothing``. A list item whose label and suffix are both empty gains nothing in Word and cannot be
seen, so it is left out on both sides. Word writes a Symbol-font character as the code it stores
(a bullet as U+F0B7); ``as_drawn`` maps it through the reader's Symbol table before comparing. A
private-use code in any other font is one the reader refuses, so the mapping cannot hide a
difference.

Word runs sandboxed: each file is copied into Word's container, where it opens without a
permission prompt, and removed after.
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
SEPARATOR = "\x1d"

# The document's text before and after Word writes its list labels into it.
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
    set stored to content of text object of d
    convert numbers to text d
    set drawn to content of text object of d
    close d saving no
  end tell
  return stored & (character id 29) & drawn
end run
"""

SUFFIXES = {"tab": "\t", "legacy": "\t", "space": " ", "nothing": ""}


def word_version() -> str:
    """Word's version, from its bundle."""
    with (WORD / "Contents/Info.plist").open("rb") as info:
        return f"Microsoft Word {plistlib.load(info)['CFBundleShortVersionString']} (macOS)"


def _ask_word(path: Path) -> str:
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
                return done.stdout
            if attempt == 2:
                raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    raise AssertionError  # pragma: no cover


def word_labels(path: Path) -> list[str]:
    """What Word draws before each of ``path``'s list items, in document order."""
    stored, _, drawn = _ask_word(path).rstrip("\n").partition(SEPARATOR)
    # osascript turns Word's paragraph marks into line feeds; a manual line break stays U+000B.
    # The two texts can end in different numbers of empty lines, which are no paragraph's. Word
    # adds no paragraph, so the rest pair line for line; matching them by content instead goes
    # wrong where empty list items gain a label.
    # U+0007 is Word's end-of-cell and end-of-row mark, which follows a paragraph mark and would
    # otherwise stand at the start of the paragraph after a table, ahead of its label.
    stored, drawn = stored.replace("\x07", ""), drawn.replace("\x07", "")
    before, after = stored.rstrip("\n").split("\n"), drawn.rstrip("\n").split("\n")
    if len(before) != len(after) or not all(
        a.endswith(b) for b, a in zip(before, after, strict=True)
    ):
        raise SystemExit(f"{path.name}: Word changed a paragraph other than by a list label")
    return [a[: len(a) - len(b)] for b, a in zip(before, after, strict=True) if a != b]


def reader_labels(path: Path) -> list[str] | str:
    """What the reader draws before each of ``path``'s list items, or its refusal code."""
    try:
        paragraphs = read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        return refused.code
    drawn = [
        (p.numbering.text or "") + SUFFIXES[p.numbering.suffix or "nothing"]
        for p in paragraphs
        if p.numbering is not None and p.numbering.num_id
    ]
    return [item for item in drawn if item]


def as_drawn(label: str) -> str:
    """Word's ``label`` with each stored Symbol code (U+F000 plus the code) as its character."""
    return "".join(
        SYMBOL_FONT.get(ord(c) - 0xF000, c) if 0xF000 <= ord(c) <= 0xF0FF else c for c in label
    )


def verdict(word: list[str], reader: list[str] | str) -> str:
    """Whether the reader agrees with Word: agrees, refuses (code) or differs (where)."""
    if isinstance(reader, str):
        return f"reader refuses: {reader}"
    if len(reader) != len(word):
        return f"differs: Word has {len(word)} list items, the reader {len(reader)}"
    for index, (ours, theirs) in enumerate(zip(reader, word, strict=True)):
        if ours != as_drawn(theirs):
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
        if not path.read_bytes().startswith(b"PK\x03\x04"):
            # A .doc under a .docx name makes Word convert it, and can leave a dialog open.
            if args.command == "record":
                raise SystemExit(f"{path.name}: not a .docx")
            sys.stdout.write(f"{path.name}: not a .docx; not sent to Word\n")
            continue
        try:
            word = word_labels(path)
        except SystemExit as failed:
            if args.command == "record":
                raise
            # One file Word cannot open or answer for does not stop a comparison of many.
            sys.stdout.write(f"{failed}\n")
            continue
        answers[path.name] = word
        result = verdict(word, reader_labels(path))
        differs = differs or result.startswith("differs")
        sys.stdout.write(f"{path.name}: {result}\n")
        if result.startswith("reader refuses"):
            sys.stdout.write(f"  Word draws: {json.dumps(word)}\n")
        sys.stdout.flush()
    if args.command == "record":
        record = {
            "application": word_version(),
            "method": "convert numbers to text; what each list item gained",
            "recorded": datetime.date.today().isoformat(),
            "drawn": answers,
        }
        target = args.folder / "word.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {target}\n")
    return 1 if differs else 0


if __name__ == "__main__":
    raise SystemExit(main())
