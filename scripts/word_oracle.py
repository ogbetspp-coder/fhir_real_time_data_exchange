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

Footnote and endnote marks are the second reference. Word does not report the mark it draws,
so the oracle writes a copy of the document with ``@@n@@`` before and ``@@/@@`` after every note
reference's run (and every note's echo of its mark), has Word save the copy as text, and reads
what Word wrote between them. Text runs take no part in note numbering, so the marks are those
of the document itself. Word saves text in Mac OS Roman, which holds every mark the reader draws
(digits, letters, roman numerals, *, †, ‡, §).

Computed fields (SEQ captions, STYLEREF) are the third. Word shows a field's stored result on
screen and recomputes SEQ and STYLEREF when it prints or saves as PDF, so the oracle writes a copy
with markers around every field, reads the text Word shows, saves it as PDF, and reads the text
again. The reader must read a document only where every field shows what Word prints, and refuse
one (``stale-field``) where any does not.

The fourth is the whole document. For every document, the oracle reads the text Word shows and
the text after it saves as PDF, with the page numbers (PAGEREF, PAGE...) set aside, since Word
sets those from the layout. The reader may read a document only where the two are the same: any
field, known to the reader or not, that Word reprints differently is caught here.

Word runs sandboxed: each file is copied into Word's container, where it opens without a
permission prompt, and removed after.
"""

from __future__ import annotations

import argparse
import datetime
import io
import itertools
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from label_docx.reader import SYMBOL_FONT, DocxRefusedError, read_document, read_docx

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


def word_fields(path: Path) -> dict[str, list[str]] | None:
    """The results Word shows and prints for the document's fields.

    None if the document has no field the reader reads (SEQ, STYLEREF, REF, NOTEREF,
    DOCPROPERTY, HYPERLINK).
    """
    with zipfile.ZipFile(path) as source:
        parts = [(info, source.read(info)) for info in source.infolist()]
    xml = {info.filename: content for info, content in parts}["word/document.xml"]
    if not _COMPUTED.search(xml.decode("utf-8")):
        return None
    marked = _FIELD.sub(
        lambda f: f"<w:r><w:t>@@F@@</w:t></w:r>{f.group(0)}<w:r><w:t>@@/@@</w:t></w:r>",
        xml.decode("utf-8"),
    )
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        with zipfile.ZipFile(copy, "w", zipfile.ZIP_DEFLATED) as target:
            for info, content in parts:
                target.writestr(
                    info.filename,
                    marked.encode("utf-8") if info.filename == "word/document.xml" else content,
                )
        done = subprocess.run(
            ["osascript", "-", str(copy), str(copy.with_suffix(".pdf")), copy.name],
            input=PRINT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
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
    with zipfile.ZipFile(path) as source:
        parts = [(info, source.read(info)) for info in source.infolist()]
    CONTAINER.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
        copy = Path(folder) / path.name
        with zipfile.ZipFile(copy, "w", zipfile.ZIP_DEFLATED) as target:
            for info, content in parts:
                written = content
                if info.filename == "word/document.xml":
                    written = _set_page_numbers_aside(content.decode("utf-8")).encode("utf-8")
                target.writestr(info.filename, written)
        done = subprocess.run(
            ["osascript", "-", str(copy), str(copy.with_suffix(".pdf")), copy.name],
            input=PRINT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
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
        done = subprocess.run(
            ["osascript", "-", str(copy), str(text), copy.name, text.name],
            input=EXPORT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
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
    note_answers: dict[str, dict[str, list[str]]] = {}
    field_answers: dict[str, dict[str, list[str]]] = {}
    print_answers: dict[str, bool] = {}
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
            marks = word_note_marks(path)
            fields = word_fields(path)
            same = word_prints_what_it_shows(path)
        except SystemExit as failed:
            if args.command == "record":
                raise
            # One file Word cannot open or answer for does not stop a comparison of many.
            sys.stdout.write(f"{failed}\n")
            continue
        answers[path.name] = word
        result = verdict(word, reader_labels(path))
        if marks is not None:
            note_answers[path.name] = marks
            if result == "agrees":
                result = note_verdict(marks, reader_note_marks(path))
        print_answers[path.name] = same
        if result in ("agrees", "reader refuses: stale-field"):
            result = print_verdict(same, path)
        if fields is not None:
            field_answers[path.name] = fields
            # A refusal for a stale field is the right answer for the labels too.
            if result in ("agrees", "reader refuses: stale-field"):
                result = field_verdict(fields, path)
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
                "whole text as shown and after saving as PDF, page numbers aside"
            ),
            "recorded": datetime.date.today().isoformat(),
            "drawn": answers,
            "notes": note_answers,
            "fields": field_answers,
            "prints": print_answers,
        }
        target = args.folder / "word.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {target}\n")
    return 1 if differs else 0


if __name__ == "__main__":
    raise SystemExit(main())
