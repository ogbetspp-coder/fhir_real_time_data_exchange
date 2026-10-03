"""Have Microsoft Word author documents for corpus/word-authored (macOS, Word installed).

    uv run --frozen python scripts/word_authored.py corpus/word-authored

The synthetic cases (scripts/numbering_cases.py) are written by a script; these are written by
Word itself, so they hold exactly what Word writes when an author inserts a table of
contents: its field codes, styles, bookmarks and the page numbers it computed. Each document
starts as a synthetic body (built here), is opened in Word, given what an author would add, and
saved by Word. Word's files are not the same bytes from run to run (it stamps them with session
ids), so the corpus keeps the files Word wrote and their hashes (``sources.json``); running this
again writes new ones, to be reviewed and committed in their place.

Some documents are then edited the way a document goes stale (a heading renamed after the table
of contents was built, its page numbers out of date), so the reader and Word's print can be held
to what Word prints for them.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from label_docx.word import CONTAINER, WORD
from numbering_cases import HEADINGS, OUTLINE_NUMBERING, Case, heading, package, para, words

# Insert a table of contents, with page numbers, at the first paragraph; save as .docx.
TABLE_OF_CONTENTS = """
on run argv
  set target to (POSIX file (item 1 of argv)) as string
  set output to (POSIX file (item 2 of argv)) as string
  with timeout of 120 seconds
    tell application "Microsoft Word"
      open file name target
      repeat 600 times
        try
          if (name of every document) contains {item 3 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      set d to document (item 3 of argv)
      make new table of contents at d with properties ¬
        {text object:(text object of paragraph 1 of d), upper heading level:1, ¬
        lower heading level:2, use heading styles:true, include page numbers:true, ¬
        right align page numbers:true}
      save as d file name output file format format document
      close document (item 4 of argv) saving no
    end tell
  end timeout
end run
"""

BODY = (
    para(words("CONTENTS"))
    + heading(1, "Description and composition")
    + para(words("Film-coated tablets of 10 mg."))
    + heading(2, "Composition")
    + para(words("Each tablet contains 10 mg of the active substance."))
    + heading(1, "Stability")
    + para(words("Store below 25 C."))
)


def _word(base: bytes, name: str) -> bytes:
    """``base`` given a table of contents by Word, as Word saves it."""
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        source = Path(folder) / f"{name}-base.docx"
        output = Path(folder) / f"{name}.docx"
        source.write_bytes(base)
        done = subprocess.run(
            ["osascript", "-", str(source), str(output), source.name, output.name],
            input=TABLE_OF_CONTENTS,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise SystemExit(f"{name}: Word failed: {done.stderr.strip()}")
        return output.read_bytes()


def _stale(data: bytes) -> bytes:
    """The table of contents gone stale: its first entry renamed, its page numbers wrong."""
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        parts = [(info, source.read(info)) for info in source.infolist()]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info, content in parts:
            written = content
            if info.filename == "word/document.xml":
                xml = content.decode("utf-8")
                # Each PAGEREF's stored page number becomes 9.
                xml = re.sub(
                    r'(PAGEREF[^<]*</w:instrText>.*?fldCharType="separate"/>.*?<w:t>)\d+(</w:t>)',
                    r"\g<1>9\2",
                    xml,
                    flags=re.S,
                )
                xml = xml.replace(
                    "<w:t>Description and composition</w:t>", "<w:t>Description</w:t>", 1
                )
                written = xml.encode("utf-8")
            target.writestr(info, written)
    return out.getvalue()


def main() -> int:
    """Write the documents and their sources.json."""
    parser = argparse.ArgumentParser(description="Have Word author corpus/word-authored.")
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    base = package(Case("", OUTLINE_NUMBERING, BODY, HEADINGS))
    authored = _word(base, "table-of-contents")
    files = {
        "table-of-contents.docx": (
            "A table of contents with page numbers, inserted by Word.",
            authored,
        ),
        "table-of-contents-stale.docx": (
            "The same after a heading was renamed and pages moved: entry and page numbers stale.",
            _stale(authored),
        ),
    }
    with (WORD / "Contents/Info.plist").open("rb") as info:
        version = plistlib.load(info)["CFBundleShortVersionString"]
    sources = []
    for name, (what, data) in files.items():
        (args.folder / name).write_bytes(data)
        sources.append(
            {
                "name": what,
                "file": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    manifest = {
        "schemaVersion": "1.0.0",
        "note": (
            f"Authored by Microsoft Word {version} (macOS) through scripts/word_authored.py; the "
            "stale variants are Word's files edited as the script says."
        ),
        "sources": sources,
    }
    (args.folder / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    shutil.rmtree(args.folder / "__pycache__", ignore_errors=True)
    sys.stdout.write(f"wrote {len(files)} documents to {args.folder}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
