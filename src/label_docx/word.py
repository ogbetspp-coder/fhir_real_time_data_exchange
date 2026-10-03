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
  the font it gave each label's text is read from its XML: a Symbol or Wingdings label is stored
  as codes (a bullet as U+F0B7), mapped through that font's table by ``label_as_drawn``.
- **Text:** the body's text as Word shows it, paragraph by paragraph (``text_verdict`` maps
  Word's own codes for hyphens, breaks, note references and pictures).
- **Note marks:** markers around every note reference; Word saves the copy as text, and what it
  wrote between the markers is the mark.
- **Fields:** markers around every field; the text Word shows, then the text after it saves as
  PDF, when it recomputes SEQ, STYLEREF, REF and NOTEREF. A field must show what Word prints.
- **Print:** the whole text as shown and after saving as PDF, page numbers aside: any field that
  Word reprints differently is caught.
- **Emphasis:** whether each body paragraph is bold, italic, in capitals and struck through,
  over the text it shows (between its own fields, and each field's result). Word answers false
  for a paragraph that is partly so, so a paragraph is held only where the reader finds it
  wholly so or wholly not.
- **Headers, footers and comments:** each section's by type, and each comment's author and text.
- **Tracked changes:** Word's own Accept All and Reject All files (``word_views``).

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
# section's, with the results of its page-number fields; and every comment, author and text.
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
  set unit to (character id 31)
  set record_ to (character id 30)
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

SUFFIXES = {"tab": "\t", "legacy": "\t", "space": " ", "nothing": ""}

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

# A complex field (from the run that begins it to the run that ends it; fields here are not
# nested) or a simple one, which the markers go around.
_FIELD = re.compile(
    r"<w:r(?:\s[^>]*)?>(?:(?!</w:r>).)*?w:fldCharType=\"begin\".*?"
    r"w:fldCharType=\"end\"(?:(?!</w:r>).)*?</w:r>|<w:fldSimple\b.*?</w:fldSimple>",
    re.S,
)
_COMPUTED = re.compile(
    r"(?:instr=\"|<w:instrText[^>]*>)\s*(?:SEQ|STYLEREF|REF|NOTEREF|DOCPROPERTY|HYPERLINK)\b"
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


def _run_alone(command: list[str], script: str) -> subprocess.CompletedProcess[str]:
    # Word's scripts find the document by its name: one of that name already open would be the
    # one asked about.
    try:
        opened = subprocess.run(
            ["osascript", "-e", 'tell application "Microsoft Word" to get name of every document'],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as hung:
        raise SystemExit("Word did not answer within 2 minutes") from hung
    if Path(command[2]).name in opened.stdout.rstrip("\n").split(", "):
        raise SystemExit(f"a document named {Path(command[2]).name} is already open in Word")
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
        if attempt == 3 or not re.search(r"\((-609|-600)\)", done.stderr or ""):
            return done
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
    raise AssertionError  # pragma: no cover


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
    comment, with its text, marks, list labels and note marks, all but the names of the header
    and footer parts, which Word gives its own when it saves. A view the reader refuses is
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
    """A document's text with its header and footer parts' names left out."""
    return value | {
        kind: [{k: v for k, v in story.items() if k != "part"} for story in value[kind]]
        for kind in ("headers", "footers")
    }


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


def word_text_and_labels(path: Path) -> tuple[list[str], list[str], list[str | None]]:
    """Word's body text, paragraph by paragraph, its list labels, and the font of each label."""
    answer, saved = _ask_word(path)
    labels = _labels(path, answer)
    shown = _paragraphs_shown(answer.partition(SEPARATOR)[0])
    return shown, labels, _label_fonts(path, saved, labels)


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


def _label_fonts(path: Path, saved: bytes, labels: list[str]) -> list[str | None]:
    """The font Word gave each label when it wrote it in: Word's own XML, paragraph by paragraph.

    What a paragraph of Word's saved copy gained at its start, against the document, is its label;
    those must be ``labels``, in order. A label's font is its runs' (``_body_paragraphs``), the
    tab or space after it aside: one name, "mixed", or None where Word named none.
    """
    before, after = _body_paragraphs(path.read_bytes()), _body_paragraphs(saved)
    # Word ends a body that ends in a table with an empty paragraph.
    while len(after) > len(before) and not "".join(t for t, _ in after[-1]):
        after.pop()
    if len(before) != len(after):
        raise SystemExit(f"{path.name}: Word saved other paragraphs than the document's")
    found: list[str] = []
    fonts: list[str | None] = []
    for old, new in zip(before, after, strict=True):
        was, now = "".join(t for t, _ in old), "".join(t for t, _ in new)
        if now == was:
            continue
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
    if found != labels:
        raise SystemExit(f"{path.name}: Word's saved labels are not the ones it drew")
    return fonts


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
    with zipfile.ZipFile(path) as source:
        xml = source.read("word/document.xml").decode("utf-8")
    if not _COMPUTED.search(xml):
        return None
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        copy.write_bytes(
            _with_document(
                path,
                lambda xml: _FIELD.sub(
                    lambda f: f"<w:r><w:t>@@F@@</w:t></w:r>{f.group(0)}<w:r><w:t>@@/@@</w:t></w:r>",
                    xml,
                ),
            )
        )
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


_ANY_RUN = re.compile(r"<w:r(?:\s[^>]*)?>(?:(?!</w:r>).)*?</w:r>", re.S)
_LAYOUT = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}


def _set_page_numbers_aside(xml: str) -> str:
    """``xml`` with ``@@P@@`` and ``@@/P@@`` around every page-number field."""
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, list[str]]] = []
    for run in _ANY_RUN.finditer(xml):
        body = run.group(0)
        if 'fldCharType="begin"' in body:
            stack.append((run.start(), []))
        for instruction in re.findall(r"<w:instrText[^>]*>([^<]*)</w:instrText>", body):
            if stack:
                stack[-1][1].append(instruction)
        if 'fldCharType="end"' in body and stack:
            start, code = stack.pop()
            words = "".join(code).split()
            if words and words[0].upper() in _LAYOUT:
                spans.append((start, run.end()))
    for match in re.finditer(r"<w:fldSimple\b[^>]*w:instr=\"\s*(\w+).*?</w:fldSimple>", xml, re.S):
        if match.group(1).upper() in _LAYOUT:
            spans.append((match.start(), match.end()))
    for start, end in sorted(spans, reverse=True):
        xml = (
            xml[:start]
            + "<w:r><w:t>@@P@@</w:t></w:r>"
            + xml[start:end]
            + "<w:r><w:t>@@/P@@</w:t></w:r>"
            + xml[end:]
        )
    return xml


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
_PARAGRAPH_START = re.compile(r"<w:p(?:\s[^>]*)?/>|<w:p(?:\s[^>]*)?>(?:<w:pPr>.*?</w:pPr>)?", re.S)
TOGGLES = ("bold", "italic", "caps", "strike")


def word_emphasis(path: Path) -> dict[str, list[bool]]:
    """Word's bold, italic, caps and strike for each body paragraph, by the reader's index."""
    count = itertools.count()

    def mark(start: re.Match[str]) -> str:
        marker = f"<w:r><w:t>@@Q{next(count)}@@</w:t></w:r>"
        tag = start.group(0)
        if tag.endswith("/>"):
            return tag[:-2] + ">" + marker + "</w:p>"
        return tag + marker

    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        copy.write_bytes(_with_document(path, lambda xml: _PARAGRAPH_START.sub(mark, xml)))
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
        for kind, shown in zip(TOGGLES, answer, strict=True):
            covered = [m for m in paragraph.marks if m.kind == kind]
            whole = any(m.start == 0 and m.end == len(paragraph.text) for m in covered)
            if (whole or not covered) and whole != shown:
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


def as_drawn(text: str) -> str:
    """Word's ``text`` with each stored Symbol code (U+F000 plus the code) as its character."""
    return "".join(
        SYMBOL_FONT.get(ord(c) - 0xF000, c) if 0xF000 <= ord(c) <= 0xF0FF else c for c in text
    )


_TABLES = {"Symbol": SYMBOL_FONT, "Wingdings": WINGDINGS_BULLETS}


def label_as_drawn(label: str, font: str | None) -> str:
    """Word's ``label`` as drawn in ``font``, the one Word gave it.

    In Symbol or Wingdings each code (stored as U+F000 plus the code, or as the code) goes through
    that font's table; a code in no table, and any label in another font, stays as stored.
    """
    table = _TABLES.get(font or "")
    if table is None:
        return label
    out = []
    for c in label:
        code = ord(c) - 0xF000 if 0xF000 <= ord(c) <= 0xF0FF else ord(c)
        out.append(table.get(code, c))
    return "".join(out)


def verdict(word: list[str], reader: list[str] | str, fonts: list[str | None]) -> str:
    """Whether the reader agrees with Word: agrees, refuses (code) or differs (where)."""
    if isinstance(reader, str):
        return f"reader refuses: {reader}"
    if len(reader) != len(word):
        return f"differs: Word has {len(word)} list items, the reader {len(reader)}"
    for index, (ours, theirs, font) in enumerate(zip(reader, word, fonts, strict=True)):
        if ours != label_as_drawn(theirs, font):
            return f"differs at list item {index + 1}: Word {theirs!r} in {font}, reader {ours!r}"
    return "agrees"


def _has_stories(path: Path) -> bool:
    """Whether the document names a header, footer or comments part."""
    with zipfile.ZipFile(path) as source:
        names = source.namelist()
        rels = (
            source.read("word/_rels/document.xml.rels")
            if "word/_rels/document.xml.rels" in names
            else b""
        )
    return any(f'/{kind}"'.encode() in rels for kind in ("header", "footer", "comments"))


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
    previous section's; the first section's are always listed.
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
    for entry in done.stdout.rstrip("\n").split("\x1e"):
        if not entry:
            continue
        fields = entry.split("\x1f")
        if fields[0] == "comment":
            comments.append([fields[1], fields[2]])
        else:
            pages = fields[4].split(SEPARATOR) if fields[4] else []
            stories.append([fields[0], int(fields[1]), fields[2], fields[3], pages])
    return {"stories": stories, "comments": comments}


def story_verdict(word: dict[str, list[list[Any]]], path: Path) -> str:
    """Whether the reader's headers, footers and comments are Word's.

    Each header or footer a section names must be, paragraph by paragraph, the one Word has for
    that section and type, with Word's page numbers where the reader sets them aside. A part the
    reader refuses on its own is not compared. The comments must be Word's, author and text.
    """
    try:
        document = read_document(path.read_bytes())
    except DocxRefusedError as refused:
        return f"reader refuses: {refused.code}"
    shown = {
        (kind, section, type_): (text, pages)
        for kind, section, type_, text, pages in word["stories"]
    }
    for story in (*document.headers, *document.footers):
        if story.refusal is not None:
            continue
        for section, type_ in story.uses:
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
            if mine != _paragraphs_shown(text) or left:
                return f"differs in the {story.kind} {type_} of section {section + 1}"
    theirs = sorted((author, "".join(_paragraphs_shown(text))) for author, text in word["comments"])
    readers = sorted(
        (comment.author or "", "".join(p.text for p in comment.paragraphs))
        for comment in document.comments
        if comment.refusal is None
    )
    unread = any(comment.refusal is not None for comment in document.comments)
    if not unread and readers != theirs:
        return "differs in the comments"
    return "agrees"


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
        shown = piece.replace("\x1e", "\u2011").replace("\x1f", "\u00ad").replace("\x0b", "\n")
        # Text in the Symbol font shows as its stored code (U+F000 plus the code), as a bullet
        # does; the reader refuses that code anywhere else, so mapping it hides nothing.
        shown = as_drawn(shown)
        for number, part in enumerate(shown.split("\x0c")):
            if part:
                parts.append((part, number > 0))
    symbols = set(SYMBOL_FONT.values())
    stood_for = joined = at = 0
    for index, ours in enumerate(mine):
        # Each character the Symbol table could have given is a group: it or Word's "(".
        pattern = "".join(
            r"\w*"
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
    text, drawn, fonts = word_text_and_labels(path)
    return {
        "drawn": drawn,
        "fonts": fonts,
        "text": text,
        "notes": word_note_marks(path),
        "fields": word_fields(path),
        "prints": word_prints_what_it_shows(path),
        "emphasis": word_emphasis(path),
        "stories": word_stories(path),
    }


def judge(path: Path, answers: dict[str, Any]) -> str:
    """The verdict on the reader's reading of ``path`` against Word's ``answers``.

    ``agrees``, ``differs: ...`` (where, never the text) or ``reader refuses: ...``: list
    labels first, then the text, note marks, emphasis, the print, the fields, and headers,
    footers and comments, each judged only where the ones before agree.
    """
    result = verdict(answers["drawn"], reader_labels(path), answers["fonts"])
    if answers.get("text") is not None and result == "agrees":
        result = text_verdict(answers["text"], path)
    if answers["notes"] is not None and result == "agrees":
        result = note_verdict(answers["notes"], reader_note_marks(path))
    if result == "agrees":
        result = emphasis_verdict(answers["emphasis"], path)
    if result in ("agrees", "reader refuses: stale-field"):
        result = print_verdict(answers["prints"], path)
    if answers["fields"] is not None and result in ("agrees", "reader refuses: stale-field"):
        # A refusal for a stale field is the right answer for the labels too.
        result = field_verdict(answers["fields"], path)
    if answers.get("stories") is not None and result == "agrees":
        result = story_verdict(answers["stories"], path)
    return result


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
