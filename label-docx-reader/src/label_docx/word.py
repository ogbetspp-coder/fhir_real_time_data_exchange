"""Ask Microsoft Word what a .docx shows, and hold the reader to it (macOS, Word installed).

    uv run --frozen python scripts/word_oracle.py record corpus/numbering-cases
    uv run --frozen python scripts/word_oracle.py compare path/to/label.docx [...]

``record`` writes Word's answers for a corpus set to its ``word.json``, which
``tests/test_word_oracle.py`` holds the reader to without Word. ``compare`` prints a verdict for
any files and writes nothing, so a confidential label can be checked and never enter the
repository. Verdicts name where the reader differs, never the text.

What Word is asked, each by a script on a copy of the document:

- **List labels:** Word's "convert numbers to text" writes each label into its paragraph; what
  each paragraph gained is the label and the tab or space after it. Word then saves the copy, and
  the font it gave each label's text, and the paragraph it is on, are read from its XML: a Symbol
  or Wingdings label is stored as codes (a bullet as U+F0B7), mapped through that font's table
  by ``label_as_drawn``; a code in no table is never agreement.
- **Text:** the body's text as Word shows it, paragraph by paragraph (``text_verdict`` maps
  Word's own codes for hyphens, breaks, note references and pictures).
- **Note marks:** markers around every note reference; Word saves the copy as text, and what it
  wrote between the markers is the mark.
- **Fields:** markers around every field; the text Word shows, then the text after it saves as
  PDF, when it recomputes SEQ, STYLEREF, REF and NOTEREF. A field must show what Word prints.
- **Print:** the whole text as shown and after saving as PDF, page numbers aside: any field that
  Word reprints differently is caught.
- **Emphasis:** whether each body paragraph is bold, italic, in capitals and struck through,
  over the text it shows (between its own fields, and each field's result), its white space's
  formatting aside. Word answers false for a paragraph that is partly so, so where the reader
  finds it partly so only Word's true is a difference. Paragraphs with a note reference or a page
  number, and those a hidden paragraph mark joins, are not held (Word's answer would count the
  mark or the number).
- **Headers, footers and comments:** each section's by type, and each comment's author and text.
- **Tracked changes:** Word's own Accept All and Reject All files (``word_views``).

Markers go in by scanning the XML, and each scan must find what Python's XML parser finds, or
Word is not asked. A story, note or field the reader reads that Word was not asked about is not
agreement (``judge``).

Word runs sandboxed: each file is copied into Word's container, where it opens without a
permission prompt, and removed after. One script runs at a time (``_osascript``).
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import itertools
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from label_docx.output import content, read
from label_docx.reader import (
    SYMBOL_FONT,
    WINGDINGS_BULLETS,
    DocxRefusedError,
    Paragraph,
    W,
    read_document,
    read_docx,
    tracked,
)

WORD = Path("/Applications/Microsoft Word.app")
# What Word is asked and how its answers are judged: a change to this file changes it
# (``scripts/lock.py``). A kept verdict or recorded answer of another version is not reused.
VERIFIER = "word-verifier/1.0.8"


class WordError(Exception):
    """Word could not be asked: it is not installed, failed, or did not answer in time."""


def find_word() -> Path | None:
    """Word, where it is installed (macOS), or None."""
    return WORD if sys.platform == "darwin" and WORD.exists() else None


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
    -- Saved, so the font Word gave each label can be read from its own XML.
    save document (item 2 of argv)
    close document (item 2 of argv) saving no
  end tell
  return stored & (character id 29) & drawn
end run
"""

# The document with every field updated, saved in place: Word's own results, in Word's XML.
UPDATE = """
on run argv
  set target to (POSIX file (item 1 of argv)) as string
  with timeout of 300 seconds
    tell application "Microsoft Word"
      open file name target
      repeat 600 times
        try
          if (name of every document) contains {item 2 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      repeat with f in (get fields of document (item 2 of argv))
        update field f
      end repeat
      save document (item 2 of argv)
      close document (item 2 of argv) saving no
    end tell
  end timeout
end run
"""

# The document with every tracked change accepted (item 3 "accept") or rejected, saved in place.
VIEW = """
on run argv
  set target to (POSIX file (item 1 of argv)) as string
  with timeout of 300 seconds
    tell application "Microsoft Word"
      open file name target
      repeat 600 times
        try
          if (name of every document) contains {item 2 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      if (item 3 of argv) is "accept" then
        accept all revisions document (item 2 of argv)
      else
        reject all revisions document (item 2 of argv)
      end if
      save document (item 2 of argv)
      close document (item 2 of argv) saving no
    end tell
  end timeout
end run
"""

# Every header and footer Word has for each section and type, unless linked to the previous
# section's, with the results of its page-number fields; whether each section shows a first page's
# and even pages' own; and every comment, author and text.
STORIES = """
on resultOf(g)
  tell application "Microsoft Word"
    set rr to result range of g
    return content of rr
  end tell
end resultOf

on pageResults(r)
  set pageNumbers to {}
  tell application "Microsoft Word"
    repeat with f in (get fields of r)
      set g to contents of f
      set fieldKind to field type of g
      if fieldKind is in {field page, field num pages, field section pages, field page ref} then ¬
        set end of pageNumbers to my resultOf(g)
    end repeat
  end tell
  set AppleScript's text item delimiters to (character id 29)
  set pagesJoined to pageNumbers as text
  set AppleScript's text item delimiters to ""
  return pagesJoined
end pageResults

on run argv
  set target to (POSIX file (item 1 of argv)) as string
  set out to ""
  -- Neither can be in Word's text: XML holds no such character, and neither is one of Word's own
  -- codes (U+001E and U+001F are its no-break and soft hyphens).
  set unit to (character id 28)
  set record_ to (character id 27)
  with timeout of 600 seconds
    tell application "Microsoft Word"
      open file name target
      repeat 600 times
        try
          if (name of every document) contains {item 2 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      set d to document (item 2 of argv)
      set kinds to {header footer primary, header footer first page, header footer even pages}
      set names to {"default", "first", "even"}
      repeat with s from 1 to (count of sections of d)
        set ps to page setup of section s of d
        set out to out & "setup" & unit & (s - 1) & unit ¬
          & (different first page header footer of ps) & unit ¬
          & (odd and even pages header footer of ps) & record_
        repeat with i from 1 to 3
          set h to get header (section s of d) index (item i of kinds)
          if (s is 1 or not (link to previous of h)) then
            set r to text object of h
            set out to out & "header" & unit & (s - 1) & unit & (item i of names) & unit ¬
              & (content of r) & unit & my pageResults(r) & record_
          end if
          set h to get footer (section s of d) index (item i of kinds)
          if (s is 1 or not (link to previous of h)) then
            set r to text object of h
            set out to out & "footer" & unit & (s - 1) & unit & (item i of names) & unit ¬
              & (content of r) & unit & my pageResults(r) & record_
          end if
        end repeat
      end repeat
      repeat with c in (get Word comments of d)
        set g to contents of c
        set out to out & "comment" & unit & (author of g) & unit ¬
          & (content of (comment text of g)) & record_
      end repeat
      close document (item 2 of argv) saving no
    end tell
  end timeout
  return out
end run
"""

# Each footnote's and endnote's text, in Word's order (the order the body refers to them).
NOTES = """
on run argv
  set target to (POSIX file (item 1 of argv)) as string
  set out to ""
  set unit to (character id 28)
  set record_ to (character id 27)
  with timeout of 600 seconds
    tell application "Microsoft Word"
      open file name target
      repeat 600 times
        try
          if (name of every document) contains {item 2 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      set d to document (item 2 of argv)
      -- Each note's whole paragraphs: its range alone starts after its mark's echo and a space.
      repeat with n in (get footnotes of d)
        set r to text object of (contents of n)
        set out to out & "footnote" & unit
        repeat with q in (get paragraphs of r)
          set out to out & (content of (text object of (contents of q)))
        end repeat
        set out to out & record_
      end repeat
      repeat with n in (get endnotes of d)
        set r to text object of (contents of n)
        set out to out & "endnote" & unit
        repeat with q in (get paragraphs of r)
          set out to out & (content of (text object of (contents of q)))
        end repeat
        set out to out & record_
      end repeat
      close document (item 2 of argv) saving no
    end tell
  end timeout
  return out
end run
"""

# What Word writes after a list label, by the reader's suffix: after a Word 6 level's, a tab
# (legacy-levels), where what Word draws is not known to be a space (``legacy``) or is (``tab``).
SUFFIXES = {"tab": "\t", "legacy": "\t", "space": " ", "nothing": ""}
_UNIT, _RECORD = "\x1c", "\x1b"

# The document saved as text.
EXPORT = """
on run argv
  set target to (POSIX file (item 1 of argv)) as string
  set output to (POSIX file (item 2 of argv)) as string
  tell application "Microsoft Word"
    open file name target
    repeat 600 times
      try
        if (name of every document) contains {item 3 of argv} then exit repeat
      end try
      delay 0.1
    end repeat
    save as document (item 3 of argv) file name output file format format Unicode text
    close document (item 4 of argv) saving no
  end tell
end run
"""

# The text Word shows, then the text after it saves the document as PDF.
PRINT = """
on run argv
  set target to (POSIX file (item 1 of argv)) as string
  set pdfOut to (POSIX file (item 2 of argv)) as string
  with timeout of 120 seconds
    tell application "Microsoft Word"
      open file name target
      repeat 600 times
        try
          if (name of every document) contains {item 3 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      set shown to content of text object of document (item 3 of argv)
      save as document (item 3 of argv) file name pdfOut file format format PDF
      set printed to content of text object of document (item 3 of argv)
      close document (item 3 of argv) saving no
    end tell
  end timeout
  return shown & (character id 29) & printed
end run
"""

# A run (not one that closes itself), and a simple field's start and end tags.
_ANY_RUN = re.compile(r"<w:r(?:\s[^>]*)?(?<!/)>(?:(?!</w:r>).)*?</w:r>", re.S)
_FIELD_TOKEN = re.compile(
    r"(?P<simple><w:fldSimple\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>)|(?P<close></w:fldSimple>)|"
    + _ANY_RUN.pattern,
    re.S,
)

# A run holding a note reference (in the body) or a note's echo of its mark (in a note).
_NOTE_RUN = {
    "reference": re.compile(
        r"<w:r(?:\s[^>]*)?>(?:(?!</w:r>).)*?<w:(?:footnote|endnote)Reference\b(?:(?!</w:r>).)*?</w:r>",
        re.S,
    ),
    "echo": re.compile(
        r"<w:r(?:\s[^>]*)?>(?:(?!</w:r>).)*?<w:(?:footnote|endnote)Ref\b(?:(?!</w:r>).)*?</w:r>",
        re.S,
    ),
}


def _osascript(command: list[str], script: str) -> subprocess.CompletedProcess[str]:
    """Run an AppleScript for Word, failing after 15 minutes rather than waiting on a hung Word.

    One script at a time, across processes (a lock file in Word's container): each finds its
    document by name, and two at once could find each other's. Word quits now and then in a
    long recording ("Connection is invalid", -609; "not running", -600): it is started again and
    the script run again, twice at most.
    """
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with (CONTAINER / ".label-docx-word.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_alone(command, script)


# How many documents open in Word have the name ``item 1 of argv``, compared in AppleScript, as
# Word's scripts find a document by name.
OPEN_NAMED = """
on run argv
  set found to 0
  tell application "Microsoft Word"
    repeat with d in (get documents)
      if (name of d) is (item 1 of argv) then set found to found + 1
    end repeat
  end tell
  return found
end run
"""


def _run_alone(command: list[str], script: str) -> subprocess.CompletedProcess[str]:
    # Word's scripts find the document by its name: one of that name already open would be the
    # one asked about.
    name = Path(command[2]).name
    # Word answers a question now and then with an error while it closes the last document:
    # asked again; a count of documents of that name, though, is final.
    for attempt in (1, 2, 3):
        try:
            opened = subprocess.run(
                ["osascript", "-", name],
                input=OPEN_NAMED,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired as hung:
            raise SystemExit("Word did not answer within 2 minutes") from hung
        if opened.returncode == 0 or attempt == 3:
            break
        if _quit(opened):
            _restart()
        else:
            time.sleep(2)
    if opened.returncode != 0 or opened.stdout.strip() != "0":
        answer = opened.stdout.strip() or opened.stderr.strip()
        raise SystemExit(f"a document named {name} may be open in Word ({answer})")
    for attempt in (1, 2, 3):
        try:
            done = subprocess.run(
                command,
                input=script,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=900,
            )
        except subprocess.TimeoutExpired as hung:
            raise SystemExit(f"Word did not answer within 15 minutes ({command[2]})") from hung
        if attempt == 3 or not _quit(done):
            return done
        _restart()
    raise AssertionError  # pragma: no cover


def _quit(done: subprocess.CompletedProcess[str]) -> bool:
    """Whether Word had quit ("Connection is invalid", -609; "not running", -600)."""
    return bool(re.search(r"\((-609|-600)\)", done.stderr or ""))


def _restart() -> None:
    """Start Word again and wait until it answers, up to two minutes."""
    subprocess.run(["open", "-g", "-a", str(WORD)], check=False)
    for _second in range(120):
        alive = subprocess.run(
            ["osascript", "-e", 'tell application "Microsoft Word" to name'],
            capture_output=True,
            text=True,
            check=False,
        )
        if alive.returncode == 0:
            break
        time.sleep(1)
    time.sleep(5)


def word_version() -> str:
    """Word's version, from its bundle."""
    with (WORD / "Contents/Info.plist").open("rb") as info:
        return f"Microsoft Word {plistlib.load(info)['CFBundleShortVersionString']} (macOS)"


def _ask_word(path: Path) -> tuple[str, bytes]:
    """Word's text before and after it writes the list labels in, and the copy it then saved."""
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        # Word's scripting fails now and then while it is still loading; one retry is enough.
        for attempt in (1, 2):
            shutil.copyfile(path, copy)  # afresh: a failed attempt may have saved the copy
            done = _osascript(["osascript", "-", str(copy), path.name], SCRIPT)
            if done.returncode == 0:
                return done.stdout, copy.read_bytes()
            if attempt == 2:
                raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    raise AssertionError  # pragma: no cover


def word_updated(path: Path) -> bytes:
    """``path`` as Word saves it after updating every field in it.

    Word writes the whole package again, in its own XML, with each field's result its own: the
    reader is then held to a document Word wrote, as a label is.
    """
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        shutil.copyfile(path, copy)
        done = _osascript(["osascript", "-", str(copy), path.name], UPDATE)
        if done.returncode != 0:
            raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
        return copy.read_bytes()


def word_views(path: Path) -> dict[str, bytes]:
    """``path`` as Word saves it with every tracked change accepted, and with every one rejected.

    Keyed as the reader's views are: ``accepted`` and ``original``.
    """
    CONTAINER.mkdir(parents=True, exist_ok=True)
    out: dict[str, bytes] = {}
    for view, verb in (("accepted", "accept"), ("original", "reject")):
        with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
            copy = Path(folder) / path.name
            shutil.copyfile(path, copy)
            done = _osascript(["osascript", "-", str(copy), path.name, verb], VIEW)
            if done.returncode != 0:
                raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
            out[view] = copy.read_bytes()
    return out


def tracked_verdict(path: Path, word: dict[str, bytes]) -> str:
    """Whether the reader reads each view of ``path`` as it reads Word's (``word_views``).

    The reader's view is held to Word's file whole: every paragraph, note, header, footer and
    comment, with its text, marks, list labels and note marks, and every table's grid, all but
    the names of the header and footer parts, which Word gives its own when it saves, and the
    grid columns' widths, which Word works out again when it lays a table out to save it (a
    one-column table stored 4000 twips wide, its text short, Word saved 398 to 526 wide,
    corpus/tracked-cases). A view the reader refuses is
    ``reader refuses``; the reader may refuse where Word goes on, never read otherwise.
    """
    try:
        accepted, original, _ = tracked(path.read_bytes())
        mine = {
            "accepted": _unnamed(content(read_document(accepted))),
            "original": _unnamed(content(read_document(original))),
        }
    except DocxRefusedError as refused:
        return f"reader refuses: {refused.code}"
    for view in ("accepted", "original"):
        try:
            theirs = _unnamed(content(read_document(word[view])))
        except DocxRefusedError as refused:
            return f"differs: the reader reads its {view} view, and refuses Word's ({refused.code})"
        if mine[view] != theirs:
            where = next(
                (key for key in sorted(theirs) if mine[view].get(key) != theirs[key]), "the result"
            )
            return f"differs: {view} view, {where}"
    return "agrees"


def _unnamed(value: dict[str, Any]) -> dict[str, Any]:
    """A document's text with its header and footer parts' names and its grids' widths left out."""
    return (
        value
        | {
            kind: [{k: v for k, v in story.items() if k != "part"} for story in value[kind]]
            for kind in ("headers", "footers")
        }
        | {
            "tables": [
                table
                | (
                    {}
                    if table["grid"] is None
                    else {"grid": {k: v for k, v in table["grid"].items() if k != "widths"}}
                )
                for table in value["tables"]
            ]
        }
    )


def judge_tracked(path: Path) -> str:
    """The verdict on a document with tracked changes, view by view.

    Each view the reader makes must be the one it makes of Word's file (``tracked_verdict``),
    and Word's file must be read as Word shows it (``judge``).
    """
    word = word_views(path)
    outcome = tracked_verdict(path, word)
    for view, data in word.items():
        if outcome != "agrees":
            break
        with tempfile.TemporaryDirectory() as folder:
            shown = Path(folder) / path.name
            shown.write_bytes(data)
            verdict = judge(shown, ask(shown))
        if verdict != "agrees":
            outcome = f"{verdict} (Word's {view} view)"
    return outcome


def is_tracked(path: Path) -> bool:
    """Whether the reader reads ``path`` as a document with tracked changes."""
    return "tracked" in json.loads(read(path.read_bytes())[0])


def word_text_and_labels(path: Path) -> tuple[list[str], list[str], list[str | None], list[int]]:
    """Word's body text, paragraph by paragraph, its list labels, each one's font and paragraph."""
    answer, saved = _ask_word(path)
    labels = _labels(path, answer)
    shown = _paragraphs_shown(answer.partition(SEPARATOR)[0])
    return shown, labels, *_label_fonts(path, saved, labels)


def _paragraphs_shown(stored: str) -> list[str]:
    """Word's text of the body or of a story as its paragraphs, empty ones left out.

    A paragraph ends at a paragraph mark or at a table cell's end mark (U+0007), which Word's
    text shows after the cell's last paragraph with or without a paragraph mark. U+000C is kept:
    Word's text shows both a section break, which ends a paragraph, and a page break, which does
    not, as it.
    """
    return [piece for piece in re.split(r"[\r\n\x07]", stored) if piece]


def _labels(path: Path, answer: str) -> list[str]:
    """The labels Word drew: what each paragraph gained when its numbers became text."""
    stored, _, drawn = answer.rstrip("\n").partition(SEPARATOR)
    # osascript turns Word's paragraph marks into line feeds; a manual line break stays U+000B.
    # The two texts can end in different numbers of empty lines, which are no paragraph's. Word
    # adds no paragraph, so the rest pair line for line; matching them by content instead goes
    # wrong where empty list items gain a label.
    # U+0007 is Word's end-of-cell and end-of-row mark, which follows a paragraph mark and would
    # otherwise stand at the start of the paragraph after a table, ahead of its label.
    # U+000C ends a paragraph that closes a section, in place of its paragraph mark.
    stored, drawn = stored.replace("\x07", ""), drawn.replace("\x07", "")
    before = re.split(r"[\n\x0c]", stored.rstrip("\n"))
    after = re.split(r"[\n\x0c]", drawn.rstrip("\n"))
    if len(before) != len(after) or not all(
        a.endswith(b) for b, a in zip(before, after, strict=True)
    ):
        raise SystemExit(f"{path.name}: Word changed a paragraph other than by a list label")
    return [a[: len(a) - len(b)] for b, a in zip(before, after, strict=True) if a != b]


# What a run's child adds to the paragraph's text, for comparing the document with Word's copy.
_PIECES = {"tab": "\t", "br": "\n", "cr": "\n", "noBreakHyphen": "\u2011", "softHyphen": "\u00ad"}
# Where the paragraphs are not the body's: text boxes, shapes and their fallbacks.
_ASIDE = {"drawing", "pict", "AlternateContent", "txbxContent", "object"}


def _body_paragraphs(package: bytes) -> list[list[tuple[str, str | None]]]:
    """Each body paragraph as (text, font) pieces, outside text boxes and shapes.

    A run's font is the one its ``w:rFonts`` names for both Latin slots: None for none, "mixed"
    where they differ.
    """
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(package)).read("word/document.xml"))
    out: list[list[tuple[str, str | None]]] = []

    def pieces(element: ET.Element, into: list[tuple[str, str | None]]) -> None:
        for child in element:
            local = child.tag.rsplit("}", 1)[-1]
            if local in _ASIDE:
                continue
            if child.tag == f"{{{W}}}r":
                fonts = child.find(f"{{{W}}}rPr/{{{W}}}rFonts")
                pair = (
                    (None, None)
                    if fonts is None
                    else (fonts.get(f"{{{W}}}ascii"), fonts.get(f"{{{W}}}hAnsi"))
                )
                font = pair[0] if pair[0] == pair[1] else "mixed"
                for item in child:
                    name = item.tag.rsplit("}", 1)[-1]
                    if name == "t":
                        into.append((item.text or "", font))
                    elif name in _PIECES:
                        into.append((_PIECES[name], font))
                    elif name == "sym":
                        into.append((chr(int(item.get(f"{{{W}}}char", "0"), 16)), font))
                continue
            pieces(child, into)

    def walk(element: ET.Element) -> None:
        for child in element:
            if child.tag.rsplit("}", 1)[-1] in _ASIDE:
                continue
            if child.tag == f"{{{W}}}p":
                paragraph: list[tuple[str, str | None]] = []
                pieces(child, paragraph)
                out.append(paragraph)
            else:
                walk(child)

    walk(root)
    return out


def _loose(label: str) -> str:
    """``label`` with each U+F0xx code as its low code, casefolded.

    Word's text of a label whose formatting comes from a character style on the paragraph mark
    shows it in capitals and its Symbol characters as U+F0xx; its saved copy holds it as written.
    """
    return "".join(
        chr(ord(c) - 0xF000) if 0xF000 <= ord(c) <= 0xF0FF else c for c in label
    ).casefold()


def _label_fonts(path: Path, saved: bytes, labels: list[str]) -> tuple[list[str | None], list[int]]:
    """The font Word gave each label when it wrote it in, and the body paragraph it is on.

    What a paragraph of Word's saved copy gained at its start, against the document, is its label;
    those must be ``labels``, in order (``_loose``ly: a label Word's text shows otherwise than
    its copy holds gets no font, so no symbol table vouches for it). A label's font is its runs'
    (``_body_paragraphs``), the tab or space after it aside: one name, "mixed", or None where
    Word named none. Paragraphs are counted as the reader counts the body's.
    """
    before, after = _body_paragraphs(path.read_bytes()), _body_paragraphs(saved)
    # Word ends a body that ends in a table with an empty paragraph.
    while len(after) > len(before) and not "".join(t for t, _ in after[-1]):
        after.pop()
    if len(before) != len(after):
        raise SystemExit(f"{path.name}: Word saved other paragraphs than the document's")
    found: list[str] = []
    fonts: list[str | None] = []
    at: list[int] = []
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        was, now = "".join(t for t, _ in old), "".join(t for t, _ in new)
        if now == was:
            continue
        at.append(index)
        if not now.endswith(was):
            raise SystemExit(f"{path.name}: Word changed a paragraph other than by a list label")
        found.append(now[: len(now) - len(was)])
        drawn = found[-1].rstrip("\t ")
        named: set[str | None] = set()
        for text, font in new:
            if not drawn:
                break
            if text:
                named.add(font)
                drawn = drawn[len(text) :]
        fonts.append(named.pop() if len(named) == 1 else ("mixed" if named else None))
    if len(found) != len(labels) or any(
        _loose(a) != _loose(b) for a, b in zip(found, labels, strict=False)
    ):
        raise SystemExit(f"{path.name}: Word's saved labels are not the ones it drew")
    # Word's text shows a Symbol label's space suffix as U+F020, and its copy as U+0020: one code.
    return [
        f if a == b or (b.endswith("\uf020") and a == b[:-1] + " ") else None
        for f, a, b in zip(fonts, found, labels, strict=True)
    ], at


def _marked(xml: str, kind: str, tag: str, count: itertools.count[int]) -> tuple[str, int]:
    """``xml`` with ``@@<tag><n>@@`` before and ``@@/@@`` after every ``kind`` run."""

    def wrap(run: re.Match[str]) -> str:
        before = f"<w:r><w:t>@@{tag}{next(count)}@@</w:t></w:r>"
        return before + run.group(0) + "<w:r><w:t>@@/@@</w:t></w:r>"

    return _NOTE_RUN[kind].subn(wrap, xml)


def _probe(data: bytes) -> bytes | None:
    """A copy of the document with markers around its note marks, or None if it has none."""
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        parts = [(info, source.read(info)) for info in source.infolist()]
    count = itertools.count()
    references = 0
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info, content in parts:
            written = content
            if info.filename == "word/document.xml":
                xml, references = _marked(content.decode("utf-8"), "reference", "B", count)
                written = xml.encode("utf-8")
            elif info.filename in ("word/footnotes.xml", "word/endnotes.xml"):
                xml, _ = _marked(content.decode("utf-8"), "echo", "N", count)
                written = xml.encode("utf-8")
            target.writestr(info.filename, written)
    return out.getvalue() if references else None


def _with_document(path: Path, rewrite: Callable[[str], str]) -> bytes:
    """``path``'s package with ``word/document.xml`` rewritten by ``rewrite``, the rest as is."""
    with zipfile.ZipFile(path) as source:
        parts = [(info, source.read(info)) for info in source.infolist()]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info, content in parts:
            written = content
            if info.filename == "word/document.xml":
                written = rewrite(content.decode("utf-8")).encode("utf-8")
            target.writestr(info.filename, written)
    return out.getvalue()


def word_fields(path: Path) -> dict[str, list[str]] | None:
    """The results Word shows and prints for the document's fields.

    None if the document has no field the reader reads (SEQ, STYLEREF, REF, NOTEREF,
    DOCPROPERTY, HYPERLINK).
    """
    if not _has_computed_fields(path):
        return None
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        copy.write_bytes(_with_document(path, _mark_fields))
        done = _osascript(
            ["osascript", "-", str(copy), str(copy.with_suffix(".pdf")), copy.name], PRINT
        )
    if done.returncode != 0:
        raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    shown, _, printed = done.stdout.rstrip("\n").partition(SEPARATOR)
    return {"shown": _between_markers(shown), "printed": _between_markers(printed)}


def _between_markers(text: str) -> list[str]:
    """Each field's text, in document order, without the markers.

    Markers nest where a field's text holds a copy of another's: a cross-reference to a caption
    prints the caption's text, markers and all. Only the outermost markers are a field's own.
    """
    values: list[str] = []
    depth = 0
    clean: list[str] = []
    start = 0
    for piece in re.split(r"(@@F@@|@@/@@)", text):
        if piece == "@@F@@":
            if depth == 0:
                start = len(clean)
            depth += 1
        elif piece == "@@/@@" and depth:
            depth -= 1
            if depth == 0:
                values.append("".join(clean[start:]))
        else:
            clean.append(piece)
    return values


def field_verdict(word: dict[str, list[str]], path: Path) -> str:
    """Whether the reader reads exactly when every field shows what Word prints."""
    try:
        read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        if refused.code == "stale-field" and word["shown"] != word["printed"]:
            return "agrees"
        return f"reader refuses: {refused.code}"
    if word["shown"] != word["printed"]:
        return "differs: the reader reads fields Word reprints"
    return "agrees"


_LAYOUT = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}


def _field_spans(xml: str) -> list[tuple[int, int, str, int]]:
    """Each field in ``xml``: where it starts and ends, its instruction and how deeply nested.

    A complex field runs from the run that begins it to the run that ends it, a simple one is
    its element (empty or not). Markers go between runs, so a run holding two field characters,
    or a field character in a run that holds other runs (a text box), cannot be marked; nor can
    fields the scan does not find as the XML parser does. Each is a ``SystemExit``.
    """
    spans: list[tuple[int, int, str, int]] = []
    complex_: list[tuple[int, list[str]]] = []
    simple: list[tuple[int, str]] = []
    characters = simples = 0
    for token in _FIELD_TOKEN.finditer(xml):
        if token.group("simple"):
            simples += 1
            tag = token.group("simple")
            instruction = re.search(r"\bw:instr=(?:\"([^\"]*)\"|'([^']*)')", tag)
            code = "" if instruction is None else instruction.group(1) or instruction.group(2) or ""
            if tag.endswith("/>"):
                spans.append((token.start(), token.end(), code, len(complex_) + len(simple)))
            else:
                simple.append((token.start(), code))
            continue
        if token.group("close"):
            if not simple:
                raise SystemExit("a simple field that ends and does not begin")
            start, code = simple.pop()
            spans.append((start, token.end(), code, len(complex_) + len(simple)))
            continue
        run = token.group(0)
        kinds = re.findall(r"<w:fldChar\b[^>]*?\bw:fldCharType=[\"'](\w+)[\"']", run)
        characters += len(kinds)
        if len(kinds) > 1 or (kinds and re.search(r"<w:r[\s>]", run[1:])):
            raise SystemExit("field characters that cannot be marked run by run")
        if kinds == ["begin"]:
            complex_.append((token.start(), []))
        for instruction in re.findall(r"<w:instrText[^>]*>([^<]*)</w:instrText>", run):
            if complex_:
                complex_[-1][1].append(instruction)
        if kinds == ["end"]:
            if not complex_:
                raise SystemExit("a field that ends and does not begin")
            start, parts = complex_.pop()
            spans.append((start, token.end(), "".join(parts), len(complex_) + len(simple)))
    root = ET.fromstring(xml)
    if (
        complex_
        or simple
        or characters != sum(1 for _ in root.iter(f"{{{W}}}fldChar"))
        or simples != sum(1 for _ in root.iter(f"{{{W}}}fldSimple"))
    ):
        raise SystemExit("fields the scan does not find as the XML parser does")
    return spans


def _around(xml: str, spans: list[tuple[int, int]], before: str, after: str) -> str:
    """``xml`` with a run of ``before`` and of ``after`` around each span; spans may nest."""
    marks = [(end, 0, -start, after) for start, end in spans]
    marks += [(start, 1, -end, before) for start, end in spans]
    out: list[str] = []
    done = 0
    for at, _, _, text in sorted(marks):
        out += [xml[done:at], f"<w:r><w:t>{text}</w:t></w:r>"]
        done = at
    return "".join(out) + xml[done:]


def _mark_fields(xml: str) -> str:
    """``xml`` with ``@@F@@`` and ``@@/@@`` around every field not inside another."""
    spans = [(start, end) for start, end, _, depth in _field_spans(xml) if depth == 0]
    return _around(xml, spans, "@@F@@", "@@/@@")


def _set_page_numbers_aside(xml: str) -> str:
    """``xml`` with ``@@P@@`` and ``@@/P@@`` around every page-number field."""
    spans = [
        (start, end)
        for start, end, code, _ in _field_spans(xml)
        if code.split()[:1] and code.split()[0].upper() in _LAYOUT
    ]
    return _around(xml, spans, "@@P@@", "@@/P@@")


def word_prints_what_it_shows(path: Path) -> bool:
    """Whether the text Word prints (saved as PDF) is the text it shows, page numbers aside."""
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        copy.write_bytes(_with_document(path, _set_page_numbers_aside))
        done = _osascript(
            ["osascript", "-", str(copy), str(copy.with_suffix(".pdf")), copy.name], PRINT
        )
    if done.returncode != 0:
        raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    shown, _, printed = done.stdout.rstrip("\n").partition(SEPARATOR)
    aside = re.compile(r"@@P@@.*?@@/P@@", re.S)
    # Word's answer ends in a line feed, stripped with the whole answer from the printed text.
    return aside.sub("", shown).rstrip("\n") == aside.sub("", printed).rstrip("\n")


def print_verdict(same: bool, path: Path) -> str:
    """Whether the reader reads only where Word prints what it shows."""
    try:
        read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        if refused.code == "stale-field" and not same:
            return "agrees"
        return f"reader refuses: {refused.code}"
    return "agrees" if same else "differs: the reader reads a document Word prints differently"


# Each paragraph marked ``@@Q<n>@@`` (n counting from 0): Word's toggles over the rest of its text.
EMPHASIS = """
on run argv
  set target to (POSIX file (item 1 of argv)) as string
  set out to ""
  with timeout of 3600 seconds
    tell application "Microsoft Word"
      open file name target
      repeat 600 times
        try
          if (name of every document) contains {item 2 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      set d to document (item 2 of argv)
      -- Every paragraph, however long the document: none is left unmeasured.
      set total to count of paragraphs of d
      repeat with i from 1 to total
        set r to text object of paragraph i of d
        set t to content of r
        if t starts with "@@Q" then
          set AppleScript's text item delimiters to "@@"
          set n to text item 2 of t
          set AppleScript's text item delimiters to ""
          set fromHere to (start of content of r) + (length of n) + 4
          set toHere to (end of content of r) - 1
          if toHere > fromHere then
            -- What the paragraph shows: the text between its fields and each field's result,
            -- never a field's code, which has formatting of its own.
            set pieces to {}
            set doneTo to fromHere
            repeat with f in (get fields of r)
              set g to contents of f
              set shown to result range of g
              set code to field code of g
              set codeStart to (start of content of code) - 1
              set shownStart to start of content of shown
              set shownEnd to end of content of shown
              -- Only fields that begin in this paragraph: Word also gives a paragraph in a table
              -- the fields before it, and one inside a longer field (a table of contents) that
              -- field, whose text is outside the paragraph or all of it.
              if codeStart >= fromHere and codeStart < toHere then
                if shownEnd > toHere then set shownEnd to toHere
                if codeStart > doneTo then set end of pieces to {doneTo, codeStart}
                if shownEnd > shownStart then set end of pieces to {shownStart, shownEnd}
                set doneTo to shownEnd + 1
              end if
            end repeat
            if toHere > doneTo then set end of pieces to {doneTo, toHere}
            set {isBold, isItalic, isCaps, isStruck} to {true, true, true, true}
            repeat with piece in pieces
              set f to font object of (create range d start (item 1 of piece) end (item 2 of piece))
              if (bold of f) is not true then set isBold to false
              if (italic of f) is not true then set isItalic to false
              if (all caps of f) is not true then set isCaps to false
              if (strike through of f) is not true then set isStruck to false
            end repeat
            if (count of pieces) > 0 then
              set out to out & (text 2 thru -1 of n) & "," & isBold & "," & isItalic ¬
                & "," & isCaps & "," & isStruck & linefeed
            end if
          end if
        end if
      end repeat
      close d saving no
    end tell
  end timeout
  return out
end run
"""
_PARAGRAPH_START = re.compile(
    r"<w:p(?:\s[^>]*)?/>|<w:p(?:\s[^>]*)?>(?:\s*<w:pPr\b(?:[^>]*/>|[^>]*>.*?</w:pPr>))?", re.S
)
TOGGLES = ("bold", "italic", "caps", "strike")


def _mark_paragraphs(xml: str) -> str:
    """``xml`` with ``@@Q<n>@@`` at the start of every body paragraph, after its properties.

    ``n`` is the reader's index: paragraphs in text boxes and shapes (``_ASIDE``) are not the
    body's and are left unmarked. Every paragraph the XML parser finds must be found by the scan,
    or the answers would be another's.
    """
    aside: list[bool] = []

    def walk(element: ET.Element, inside: bool) -> None:
        for child in element:
            if child.tag == f"{{{W}}}p":
                aside.append(inside)
            walk(child, inside or child.tag.rsplit("}", 1)[-1] in _ASIDE)

    walk(ET.fromstring(xml), False)
    found = itertools.count()
    body = itertools.count()

    def mark(start: re.Match[str]) -> str:
        tag = start.group(0)
        at = next(found)
        if at >= len(aside) or aside[at]:
            return tag
        marker = f"<w:r><w:t>@@Q{next(body)}@@</w:t></w:r>"
        if tag.endswith("/>") and "<w:pPr" not in tag:  # an empty paragraph
            return tag[:-2] + ">" + marker + "</w:p>"
        return tag + marker

    marked = _PARAGRAPH_START.sub(mark, xml)
    if next(found) != len(aside):
        raise SystemExit("paragraphs the scan does not find as the XML parser does")
    return marked


def word_emphasis(path: Path) -> dict[str, list[bool]]:
    """Word's bold, italic, caps and strike for each body paragraph, by the reader's index."""
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        copy.write_bytes(_with_document(path, _mark_paragraphs))
        done = _osascript(["osascript", "-", str(copy), copy.name], EMPHASIS)
    if done.returncode != 0:
        raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    answers: dict[str, list[bool]] = {}
    for line in done.stdout.splitlines():
        index, *values = line.split(",")
        answers[index] = [value == "true" for value in values]
    return answers


def emphasis_verdict(word: dict[str, list[bool]], path: Path) -> str:
    """Whether the reader's toggles are Word's, where the reader finds a paragraph wholly so."""
    try:
        paragraphs = read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        return f"reader refuses: {refused.code}"
    for index, paragraph in enumerate(paragraphs):
        if not paragraph.text.strip() or paragraph.notes or paragraph.pages:
            continue
        # Word joins a paragraph whose mark is hidden to the next, so neither is measured on its
        # own (their marks are held by the conservation check, R-35).
        if paragraph.mark_hidden or (index and paragraphs[index - 1].mark_hidden):
            continue
        answer = word.get(str(index))
        if answer is None:
            # Word measures every paragraph with text: one it did not is not judged as agreeing.
            return f"differs at paragraph {index + 1}: Word measured no emphasis there"
        # Word's answer for a range leaves out how its white space is formatted: a paragraph
        # whose letters are all bold is bold to it, with a space before them that is not.
        letters = [at for at, character in enumerate(paragraph.text) if not character.isspace()]
        for kind, shown in zip(TOGGLES, answer, strict=True):
            covered = [m for m in paragraph.marks if m.kind == kind]
            inside = [any(m.start <= at < m.end for m in covered) for at in letters]
            whole = all(inside)
            # Word's false is "not wholly so", which a part covered is; its true is "wholly so",
            # which no reading of a part covered is.
            if whole != shown and (whole or shown or not any(inside)):
                return f"differs at paragraph {index + 1}: Word {kind} {shown}, reader {whole}"
    return "agrees"


def word_note_marks(path: Path) -> dict[str, list[str]] | None:
    """The marks Word draws at the body's note references and in the notes, or None if none."""
    probe = _probe(path.read_bytes())
    if probe is None:
        return None
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        text = copy.with_suffix(".txt")
        copy.write_bytes(probe)
        done = _osascript(["osascript", "-", str(copy), str(text), copy.name, text.name], EXPORT)
        if done.returncode != 0:
            raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
        saved = text.read_bytes().decode("mac_roman")
    found = re.findall(r"@@([BN])\d+@@(.*?)@@/@@", saved, re.S)
    return {
        "body": [mark for where, mark in found if where == "B"],
        "notes": [mark for where, mark in found if where == "N"],
    }


def reader_note_marks(path: Path) -> dict[str, list[str]] | str:
    """The reader's marks at the body's note references and in the notes, or its refusal."""
    try:
        document = read_document(path.read_bytes())
    except DocxRefusedError as refused:
        return refused.code
    notes = [*document.footnotes, *document.endnotes]
    return {
        "body": [n.mark or "" for p in document.body for n in p.notes],
        "notes": [n.mark or "" for note in notes for p in note.paragraphs for n in p.notes],
    }


def note_verdict(word: dict[str, list[str]], reader: dict[str, list[str]] | str) -> str:
    """Whether the reader's note marks are Word's."""
    if isinstance(reader, str):
        return f"reader refuses: {reader}"
    for where in ("body", "notes"):
        if reader[where] != word[where]:
            return f"differs in the {where}' note marks: Word {word[where]}, reader {reader[where]}"
    return "agrees"


def reader_labels(path: Path) -> dict[int, str] | str:
    """What the reader draws before each of ``path``'s list items, by paragraph, or its refusal."""
    try:
        paragraphs = read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        return refused.code
    drawn = {
        index: (p.numbering.text or "") + SUFFIXES[p.numbering.suffix or "nothing"]
        for index, p in enumerate(paragraphs)
        if p.numbering is not None and p.numbering.num_id
    }
    return {index: item for index, item in drawn.items() if item}


def _as_shown(text: str) -> str:
    """Word's text with its own codes as the reader's characters.

    U+001E is a no-break hyphen (U+2011), U+001F a soft hyphen (U+00AD), U+000B a line break; text
    in the Symbol font shows as its stored code (U+F000 plus the code), as a bullet does, and
    the reader refuses that code anywhere else, so mapping it hides nothing.
    """
    return as_drawn(text.replace("\x1e", "\u2011").replace("\x1f", "\u00ad").replace("\x0b", "\n"))


def as_drawn(text: str) -> str:
    """Word's ``text`` with each stored Symbol code (U+F000 plus the code) as its character."""
    return "".join(
        SYMBOL_FONT.get(ord(c) - 0xF000, c) if 0xF000 <= ord(c) <= 0xF0FF else c for c in text
    )


_TABLES = {"Symbol": SYMBOL_FONT, "Wingdings": WINGDINGS_BULLETS}


def label_as_drawn(label: str, font: str | None) -> str | None:
    """Word's ``label`` as drawn in ``font``, the one Word gave it, or None if not known.

    In Symbol or Wingdings each code (stored as U+F000 plus the code, or as the code) goes through
    that font's table, the tab or space after the label aside; a code in no table is not known.
    In "mixed" fonts (Word named two) a label is not known. In another font, or none named, the
    label stays as stored.
    """
    body, suffix = (label[:-1], label[-1]) if label[-1:] in ("\t", " ") else (label, "")
    if font == "mixed":
        return None if body else label
    table = _TABLES.get(font or "")
    if table is None:
        return label
    out = []
    for c in body:
        code = ord(c) - 0xF000 if 0xF000 <= ord(c) <= 0xF0FF else ord(c)
        if code not in table:
            return None
        out.append(table[code])
    return "".join(out) + suffix


def verdict(
    word: list[str],
    reader: dict[int, str] | str,
    fonts: list[str | None],
    at: list[int] | None = None,
) -> str:
    """Whether the reader agrees with Word: agrees, refuses (code) or differs (where).

    ``at`` is the body paragraph each of Word's labels is on; a record without it (made before
    it was asked) holds the labels in order only.
    """
    if isinstance(reader, str):
        return f"reader refuses: {reader}"
    if len(reader) != len(word):
        return f"differs: Word has {len(word)} list items, the reader {len(reader)}"
    places = list(reader) if at is None else at
    for index, (place, theirs, font) in enumerate(zip(places, word, fonts, strict=True)):
        ours, drawn = reader.get(place), label_as_drawn(theirs, font)
        if drawn is None or ours != drawn:
            where = f"list item {index + 1} (paragraph {place + 1})"
            return f"differs at {where}: Word {theirs!r} in {font}, reader {ours!r}"
    return "agrees"


def _has_stories(path: Path) -> bool:
    """Whether the document names a header, footer or comments part."""
    with zipfile.ZipFile(path) as source:
        name = "word/_rels/document.xml.rels"
        if name not in source.namelist():
            return False
        rels = ET.fromstring(source.read(name))
    return any(
        rel.get("Type", "").rsplit("/", 1)[-1] in ("header", "footer", "comments") for rel in rels
    )


_COMPUTED_CODES = re.compile(r"\s*(?:SEQ|STYLEREF|REF|NOTEREF|DOCPROPERTY|HYPERLINK)\b", re.I)


def _has_computed_fields(path: Path) -> bool:
    """Whether the body has a field the reader computes or reads stored, by its parsed code.

    SEQ, STYLEREF, REF, NOTEREF, DOCPROPERTY or HYPERLINK.
    """
    with zipfile.ZipFile(path) as source:
        root = ET.fromstring(source.read("word/document.xml"))
    # Each instruction whole, as the reader joins it: its pieces may split the code.
    codes = [node.get(f"{{{W}}}instr", "") for node in root.iter(f"{{{W}}}fldSimple")]
    open_codes: list[list[str]] = []
    for node in root.iter():
        if node.tag not in (f"{{{W}}}fldChar", f"{{{W}}}instrText"):
            continue
        kind = node.get(f"{{{W}}}fldCharType")
        if kind == "begin":
            open_codes.append([])
        elif kind in ("separate", "end") and open_codes:
            codes.append("".join(open_codes.pop()))
        elif node.tag == f"{{{W}}}instrText" and open_codes:
            open_codes[-1].append(node.text or "")
    return any(_COMPUTED_CODES.match(code) for code in codes)


# How Word shows text in capitals (w:caps), as Word answered for each case: a character's one
# capital where it has one; but the micro sign and the small roman numerals stay as they are, a
# character whose capital is more than one character (ß, ŉ, ﬁ) stays, and Greek iota and upsilon
# with dialytika and tonos lose the tonos.
_CAPS_KEPT = frozenset("\u00b5" + "".join(chr(code) for code in range(0x2170, 0x2180)))
_CAPS_OWN = {"\u0390": "\u03aa", "\u03b0": "\u03ab"}


def _capitalised(paragraph: Paragraph) -> str:
    """The paragraph's text as Word shows it: its caps marks in Word's capitals.

    Word's text shows text in capitals as capitals, where the reader keeps the letters and marks
    them: so the marks are held to Word too.
    """
    text = paragraph.text
    for mark in paragraph.marks:
        if mark.kind == "caps":
            text = (
                text[: mark.start] + _word_capitals(text[mark.start : mark.end]) + text[mark.end :]
            )
    return text


def _word_capitals(text: str) -> str:
    """``text`` as Word shows it in capitals."""
    out: list[str] = []
    for character in text:
        capital = character.upper()
        if character in _CAPS_OWN:
            out.append(_CAPS_OWN[character])
        elif character in _CAPS_KEPT or len(capital) != 1:
            out.append(character)
        else:
            out.append(capital)
    return "".join(out)


def word_stories(path: Path) -> dict[str, list[list[Any]]] | None:
    """Word's headers, footers and comments; None if the document has none of them.

    Headers and footers are (kind, section, type, text, page-number results), comments (author,
    text). A header or footer is listed for each section and type unless Word links it to the
    previous section's; the first section's are always listed, shown or not. ``setups`` gives,
    for each section, whether it shows a first page's and even pages' own (Word's page setup).
    """
    if not _has_stories(path):
        return None
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        shutil.copyfile(path, copy)
        done = _osascript(["osascript", "-", str(copy), path.name], STORIES)
    if done.returncode != 0:
        raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    stories: list[list[Any]] = []
    comments: list[list[Any]] = []
    setups: list[list[Any]] = []
    for entry in done.stdout.rstrip("\n").split(_RECORD):
        if not entry:
            continue
        fields = entry.split(_UNIT)
        flags = ("true", "false")
        if fields[0] == "setup" and len(fields) == 4 and fields[1].isdigit():
            if fields[2] not in flags or fields[3] not in flags:
                raise SystemExit(f"{path.name}: Word's page setup does not parse")
            setups.append([int(fields[1]), fields[2] == "true", fields[3] == "true"])
        elif fields[0] == "comment" and len(fields) == 3:
            comments.append([fields[1], fields[2]])
        elif fields[0] in ("header", "footer") and len(fields) == 5 and fields[1].isdigit():
            pages = fields[4].split(SEPARATOR) if fields[4] else []
            stories.append([fields[0], int(fields[1]), fields[2], fields[3], pages])
        else:
            raise SystemExit(f"{path.name}: Word's headers, footers and comments do not parse")
    return {"stories": stories, "comments": comments, "setups": setups}


def word_note_texts(path: Path) -> dict[str, list[str]] | None:
    """Word's text of each footnote and endnote, in its order; None if the body refers to none."""
    try:
        document = read_document(path.read_bytes())
    except DocxRefusedError:
        return None
    if not (document.footnotes or document.endnotes):
        return None
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        shutil.copyfile(path, copy)
        done = _osascript(["osascript", "-", str(copy), path.name], NOTES)
    if done.returncode != 0:
        raise SystemExit(f"{path.name}: Word failed: {done.stderr.strip()}")
    texts: dict[str, list[str]] = {"footnote": [], "endnote": []}
    for entry in done.stdout.rstrip("\n").split(_RECORD):
        if not entry:
            continue
        kind, unit, text = entry.partition(_UNIT)
        if kind not in texts or not unit or _UNIT in text:
            raise SystemExit(f"{path.name}: Word's notes do not parse")
        texts[kind].append(text)
    return texts


def note_text_verdict(word: dict[str, list[str]], path: Path) -> str:
    """Whether each footnote's and endnote's text is Word's, paragraph by paragraph.

    Word's text shows a note mark (the note's echo of its own, or a reference) as U+0002, a
    picture as "/", capitals as capitals, and its own codes as ``_as_shown`` maps.
    """
    try:
        document = read_document(path.read_bytes())
    except DocxRefusedError as refused:
        return f"reader refuses: {refused.code}"
    for kind, notes in (("footnote", document.footnotes), ("endnote", document.endnotes)):
        theirs = word.get(kind, [])
        if len(theirs) != len(notes):
            return f"differs: Word has {len(theirs)} {kind}s, the reader {len(notes)}"
        for index, (note, text) in enumerate(zip(notes, theirs, strict=True)):
            mine: list[str] = []
            for paragraph in note.paragraphs:
                filled = _capitalised(paragraph).replace("\ufffc", "/")
                for offset in sorted((n.offset for n in paragraph.notes), reverse=True):
                    filled = filled[:offset] + "\x02" + filled[offset:]
                if filled:
                    mine.append(filled)
            shown = [_as_shown(t) for t in _paragraphs_shown(text)]
            if mine != shown or any(p.pages for p in note.paragraphs):
                return f"differs in {kind} {index + 1}"
    return "agrees"


def story_verdict(word: dict[str, list[list[Any]]], path: Path) -> str:
    """Whether the reader's headers, footers and comments are Word's.

    Each header or footer a section names must be, paragraph by paragraph, the one Word has for
    that section and type, with Word's page numbers where the reader sets them aside. A part the
    reader refuses on its own is not compared. Which are shown is held both ways to Word's page
    setup: one the reader reads must be of a type Word shows in that section, and one with text
    that Word shows must be one the reader reads or refuses on its own, never one it finds never
    shown. The comments must be Word's, author and text.
    """
    try:
        document = read_document(path.read_bytes())
    except DocxRefusedError as refused:
        return f"reader refuses: {refused.code}"
    shown = {
        (kind, section, type_): (text, pages)
        for kind, section, type_, text, pages in word["stories"]
    }
    if "setups" not in word:
        return "differs: Word was not asked which headers and footers it shows"
    setups = {section: (first, even) for section, first, even in word["setups"]}

    def word_shows(section: int, type_: str) -> bool:
        first, even = setups.get(section, (False, False))
        return type_ == "default" or (type_ == "first" and first) or (type_ == "even" and even)

    # What the reader reads, or refuses on its own (a part it finds never shown it does neither).
    accounted = {
        (story.kind, section, type_)
        for story in (*document.headers, *document.footers)
        if story.refusal is None or story.refusal[0] != "never-shown"
        for section, type_ in story.uses
    }
    for kind, section, type_, text, _ in word["stories"]:
        shown_here = word_shows(section, type_) and bool(_paragraphs_shown(text))
        if shown_here and (kind, section, type_) not in accounted:
            return f"differs: Word shows a {kind} {type_} in section {section + 1}"
    for story in (*document.headers, *document.footers):
        if story.refusal is not None:
            continue
        for section, type_ in story.uses:
            if not word_shows(section, type_):
                where = f"{story.kind} {type_} of section {section + 1}"
                return f"differs: Word never shows the {where}"
            answer = shown.get((story.kind, section, type_))
            if answer is None:
                return f"differs: Word has no {story.kind} {type_} in section {section + 1}"
            text, pages = answer
            mine: list[str] = []
            left = list(pages)
            for paragraph in story.paragraphs:
                filled = _capitalised(paragraph)
                # Word's text shows an inline picture as "/", where the reader writes one U+FFFC
                # (the conservation check holds each to a picture in the source).
                filled = filled.replace("\ufffc", "/")
                numbers = [left.pop(0) if left else "?" for _ in paragraph.pages]
                for offset, number in sorted(
                    zip(paragraph.pages, numbers, strict=True), reverse=True
                ):
                    filled = filled[:offset] + number + filled[offset:]
                if filled:
                    mine.append(filled)
            if mine != [_as_shown(t) for t in _paragraphs_shown(text)] or left:
                return f"differs in the {story.kind} {type_} of section {section + 1}"
    # Each comment read is one of Word's, author and paragraphs, each of Word's paired once;
    # Word's left over are as many as the comments the reader refuses on its own.
    theirs = [
        (author, [_as_shown(t) for t in _paragraphs_shown(text)])
        for author, text in word["comments"]
    ]
    unread = 0
    for comment in document.comments:
        if comment.refusal is not None:
            unread += 1
            continue
        texts = [_capitalised(p).replace("\ufffc", "/") for p in comment.paragraphs]
        mine_comment = (comment.author or "", [text for text in texts if text])
        if mine_comment not in theirs:
            return "differs in the comments: one the reader reads is none of Word's"
        theirs.remove(mine_comment)
    if len(theirs) != unread:
        return f"differs in the comments: Word has {len(word['comments'])}"
    return "agrees"


# What Word shows at a page place: a number in digits or roman numerals, or nothing (a hidden
# page field with no result). Words such as PAGEREF \p's "above" are not a page number.
_PAGE_NUMBER = "(?:[0-9]+|[ivxlcdm]+|[IVXLCDM]+)?"


def text_verdict(word: list[str], path: Path) -> str:
    """Whether the reader's body text is the text Word shows, paragraph by paragraph.

    Word's text has its own codes for some characters, mapped here: U+001E for a no-break
    hyphen (the reader's U+2011), U+001F for a soft hyphen (U+00AD), U+0002 where a note is
    referred to (the reader's notes), "/" for an inline picture (U+FFFC), and text in the Symbol
    font as its stored code (U+F000 plus the code, mapped by ``as_drawn``). Text in capitals shows
    as capitals, so the reader's caps marks are applied. A page number shows as Word draws it,
    where the reader sets it aside. A Symbol character (w:sym) shows as "(": there the reader's
    character must be one of the Symbol table's, and as many as the body has w:sym elements;
    which one it is, the conservation check holds to the table.
    """
    try:
        paragraphs = read_docx(path.read_bytes())
    except DocxRefusedError as refused:
        return f"reader refuses: {refused.code}"
    mine: list[str] = []
    for paragraph in paragraphs:
        text = _capitalised(paragraph)
        inserts = [(note.offset, "\x02") for note in paragraph.notes]
        inserts += [(offset, "\x00") for offset in paragraph.pages]
        for offset, code in sorted(inserts, reverse=True):
            text = text[:offset] + code + text[offset:]
        if text:
            mine.append(text.replace("\ufffc", "/"))
    # Word's pieces between U+000C, each with whether it may join the one before: within one
    # paragraph of Word's text, U+000C is a section break or a page break.
    parts: list[tuple[str, bool]] = []
    for piece in word:
        for number, part in enumerate(_as_shown(piece).split("\x0c")):
            if part:
                parts.append((part, number > 0))
    symbols = set(SYMBOL_FONT.values())
    stood_for = joined = at = 0
    for index, ours in enumerate(mine):
        # Each character the Symbol table could have given is a group: it or Word's "(".
        pattern = "".join(
            _PAGE_NUMBER
            if c == "\x00"
            else f"({re.escape(c)}|\\()"
            if c in symbols and c != "("
            else re.escape(c)
            for c in ours
        )
        found = None
        if at < len(parts):
            shown, count = parts[at][0], 1
            found = re.fullmatch(pattern, shown)
            # A page break the reader sets aside: the paragraph runs on in Word's next piece.
            while found is None and at + count < len(parts) and parts[at + count][1]:
                shown += parts[at + count][0]
                count += 1
                found = re.fullmatch(pattern, shown)
        if found is None:
            return f"differs in paragraph {index + 1} of those with text"
        stood_for += sum(1 for group in found.groups() if group == "(")
        joined += count - 1
        at += count
    if at != len(parts):
        return f"differs: Word shows {len(parts) - at} more paragraphs with text"
    with zipfile.ZipFile(path) as source:
        body = source.read("word/document.xml").decode("utf-8")
    # The body's Symbol characters that are not "(" itself: each must be one Word shows as "(".
    written = 0
    for code in re.findall(r"<w:sym\b[^>]*\bw:char=\"([0-9A-Fa-f]+)\"", body):
        value = int(code, 16)
        written += SYMBOL_FONT.get(value - 0xF000 if value >= 0xF000 else value) != "("
    if stood_for != written:
        return f"differs: Word shows {stood_for} characters as symbols, the body has {written}"
    # The page breaks inside a paragraph, with text before and after them in it: as many as the
    # places a paragraph of the reader's runs on in Word's next piece.
    breaks = 0
    for paragraph in re.findall(r"<w:p\b.*?</w:p>", body, re.DOTALL):
        pieces = re.split(r"<w:br\b[^>]*\bw:type=\"page\"[^>]*/>", paragraph)
        texts = [bool(re.search(r"<w:t(?:\s[^>]*)?>[^<]+</w:t>", piece)) for piece in pieces]
        breaks += sum(1 for k in range(1, len(pieces)) if any(texts[:k]) and any(texts[k:]))
    if joined != breaks:
        return f"differs: the reader runs on {joined} paragraphs Word ends; {breaks} page breaks"
    return "agrees"


def ask(path: Path) -> dict[str, Any]:
    """Word's answers for a .docx: labels, text, note marks, fields, print, emphasis, stories."""
    text, drawn, fonts, at = word_text_and_labels(path)
    return {
        "drawn": drawn,
        "fonts": fonts,
        "at": at,
        "text": text,
        "notes": word_note_marks(path),
        "fields": word_fields(path),
        "prints": word_prints_what_it_shows(path),
        "emphasis": word_emphasis(path),
        "stories": word_stories(path),
        "noteText": word_note_texts(path),
    }


def judge(path: Path, answers: dict[str, Any]) -> str:
    """The verdict on the reader's reading of ``path`` against Word's ``answers``.

    ``agrees``, ``differs: ...`` (where, never the text) or ``reader refuses: ...``: list
    labels first, then the text, note marks, emphasis, the print, the fields, and headers,
    footers and comments, each judged only where the ones before agree.
    """
    result = verdict(answers["drawn"], reader_labels(path), answers["fonts"], answers.get("at"))
    if answers.get("text") is not None and result == "agrees":
        result = text_verdict(answers["text"], path)
    if answers["notes"] is not None and result == "agrees":
        result = note_verdict(answers["notes"], reader_note_marks(path))
    if answers.get("noteText") is not None and result == "agrees":
        result = note_text_verdict(answers["noteText"], path)
    if result == "agrees":
        result = emphasis_verdict(answers["emphasis"], path)
    if result in ("agrees", "reader refuses: stale-field"):
        result = print_verdict(answers["prints"], path)
    if answers["fields"] is not None and result in ("agrees", "reader refuses: stale-field"):
        # A refusal for a stale field is the right answer for the labels too.
        result = field_verdict(answers["fields"], path)
    if answers.get("stories") is not None and result == "agrees":
        result = story_verdict(answers["stories"], path)
    if result == "agrees":
        result = _unasked(path, answers)
    return result


def _unasked(path: Path, answers: dict[str, Any]) -> str:
    """``differs`` where the document has what Word was not asked about, else ``agrees``.

    Whether Word is asked is decided from the document's bytes; whether something is there to be
    asked about is the reader's reading: a story, note or field read and never held to Word is
    not agreement.
    """
    try:
        document = read_document(path.read_bytes())
    except DocxRefusedError:
        return "agrees"  # a refusal for a stale field, which Word's answers bear out
    missing = [
        what
        for what, there, answer in (
            ("the text", True, "text"),
            ("note marks", any(p.notes for p in document.body), "notes"),
            ("note text", bool(document.footnotes or document.endnotes), "noteText"),
            ("fields", _has_computed_fields(path), "fields"),
            (
                "headers, footers and comments",
                bool(document.headers or document.footers or document.comments),
                "stories",
            ),
        )
        if there and answers.get(answer) is None
    ]
    return f"differs: Word was not asked about {', '.join(missing)}" if missing else "agrees"


def verify_docx(data: bytes, result: dict[str, Any]) -> dict[str, Any]:
    """Word's verdict on a .docx's reading, for the store (``Store(word=...)``).

    The reading judged is the reader's of these bytes, which is ``result`` (the store keeps a
    result only under the bytes' SHA-256 and the reader's version); a document with tracked
    changes is judged view by view (``judge_tracked``). ``differs`` lists where
    Word shows otherwise; a Word that fails is a ``WordError``, and nothing is recorded.
    """
    with tempfile.TemporaryDirectory() as folder:
        # Named for the document: Word finds each document it is asked about by its name.
        path = Path(folder) / f"{hashlib.sha256(data).hexdigest()[:16]}.docx"
        path.write_bytes(data)
        try:
            outcome = judge_tracked(path) if "tracked" in result else judge(path, ask(path))
            application = word_version()
        except SystemExit as failed:
            raise WordError(str(failed)) from failed
    return {
        "application": application,
        # Anything but agreement (a difference, or a reading the reader would refuse) differs.
        "differs": [] if outcome == "agrees" else [{"where": outcome}],
        "verdict": outcome,
    }
