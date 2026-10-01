r"""An independent check that a result carries its document's text over whole, every time.

The readers (``reader``, ``epi``) are large: they resolve styles, compute list labels, verify
fields and refuse what they cannot vouch for. This module is small and does none of that. It
reads the source bytes again with its own parser and its own walk, and holds the reader's result
to one statement, checked character by character:

    the output text is the source's text tokens, in document order, each mapped to its one
    character, except the tokens set aside for a reason on a closed list; and every source
    token is used exactly once.

Written as an equation over one document (``T`` the characters of every text element, ``I``
those of field instructions, ``E`` the elements that stand for one character: tab, break, symbol,
picture...; ``O`` the output characters)::

    |T| + |I| + |E| = |O| + field code + page numbers + hidden whitespace + page breaks

and, stronger than the count, the sequence: paragraph by paragraph, the output text equals the
tokens not set aside, in order, each mapped by a fixed table (a ``w:t`` character to itself, or
through the Symbol table where the run's fonts include Symbol; ``w:tab`` to U+0009...). A
dropped, added, changed, duplicated or moved character breaks the equality; so does a paragraph
moved, merged or split, or a page number, note mark or table cell put elsewhere. The check
fails closed: a result it cannot account for is not served (``output``/``epi_output`` turn it
into the refusal ``uncertified``).

What the check allows the reader to choose, and so cannot itself rule on, is stated and counted
in the certificate: a run whose fonts include Symbol may be read through the Symbol table or as
stored (``symbolMapped``; which one Word draws is held to Word, ``tests/test_word_oracle.py``),
and a run of whitespace whose formatting includes hidden may be left out (``hiddenWhitespace``).
Every other choice is fixed. The set-aside reasons are fixed too: a field's instruction (code,
not shown), a page number (set by the layout; its place must be in ``pages``), a page or column
break (layout).

Its scope is the text the reader claims to read: for a .docx the body and the footnotes and
endnotes; for an ePI the Composition's section titles and divs. Text the package holds elsewhere
(headers, footers, comments; other narratives in the Bundle) is not read by the reader, and the
certificate lists it under ``notRead`` with its size, so nothing is left out silently.

For an ePI the same statement holds with HTML's tokens (parsed here by the standard library's
HTML parser, not the reader's XML parser): every character of text, in order, with each run of
collapsible whitespace drawn as at most one space (as CSS lays it out), ``br`` as U+000A and
``img`` as U+FFFC.

What it does not check: the marks, list labels and note marks, which are interpretations of
the formatting and are held to Word and Chrome themselves (``tests/test_word_oracle.py``,
``tests/test_browser_oracle.py``). It shares one thing with the reader: the Symbol table
(``SYMBOL_FONT``), a list of 49 code points held to Word.
"""

from __future__ import annotations

import io
import json
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import Any

from label_docx.reader import SYMBOL_FONT

CHECKER_VERSION = "conservation-check/1.0.0"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OBJECT = "\ufffc"
_LAYOUT_CODES = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}
_NOTE_LAYOUT = {"separator", "continuationSeparator", "continuationNotice"}
# Run children that hold no text and stand for none.
_RUN_SILENT = {"rPr", "lastRenderedPageBreak", "commentReference"}
_NOTE_MARKS = {"footnoteReference", "endnoteReference", "footnoteRef", "endnoteRef"}

type Json = Any


class CertificationError(Exception):
    """The result does not carry the source's text over exactly; the detail says where."""


def _w(name: str) -> str:
    return f"{{{W}}}{name}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# --- the ledger ---------------------------------------------------------------------------


@dataclass
class _Ledger:
    text: int = 0
    instruction: int = 0
    elements: int = 0
    field_code: int = 0
    page_numbers: int = 0
    hidden: int = 0
    page_breaks: int = 0
    symbol: int = 0
    output: int = 0

    def balanced(self) -> bool:
        source = self.text + self.instruction + self.elements
        kept = self.output + self.field_code + self.page_numbers + self.hidden + self.page_breaks
        return source == kept


# --- .docx: the source's tokens -----------------------------------------------------------


@dataclass
class _Segment:
    """One run's shown text, with the readings the check allows, or a zero-width marker."""

    readings: tuple[str, ...] = ()
    # What each reading costs in the ledger beyond the output: (hidden, symbol) counts.
    symbol_reading: int | None = None
    hidden_reading: int | None = None
    marker: tuple[str, Any] | None = None


@dataclass
class _Paragraph:
    segments: list[_Segment] = field(default_factory=list)
    table: tuple[int, int, int] | None = None


class _Fonts:
    """What a run's formatting could include: its fonts, and whether anything hides it.

    Deliberately wider than the reader's cascade: every style a run could take formatting from
    (its own, its character style's chain, its paragraph style's chain, its tables' styles'
    chains, the default styles and the document defaults), every font slot, and every theme font
    a slot could name. Wider only lets the reader choose more; it never lets text through.
    """

    def __init__(self, styles: ET.Element | None, theme: ET.Element | None) -> None:
        self.based: dict[str, str | None] = {}
        self.rpr: dict[str, ET.Element | None] = {}
        self.defaults: list[str] = []
        self.doc_rpr: ET.Element | None = None
        if styles is not None:
            for style in styles.iter(_w("style")):
                style_id = style.get(_w("styleId"), "")
                based = style.find(_w("basedOn"))
                self.based[style_id] = None if based is None else based.get(_w("val"))
                self.rpr[style_id] = style.find(_w("rPr"))
                if style.get(_w("default")) in ("1", "true", "on"):
                    self.defaults.append(style_id)
            defaults = styles.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
            self.doc_rpr = defaults
        self.theme: set[str] = set()
        if theme is not None:
            for node in theme.iter():
                if _local(node.tag) in ("latin", "ea", "cs") and node.get("typeface"):
                    self.theme.add(node.get("typeface", ""))

    def chain(self, style_id: str | None) -> list[ET.Element]:
        out: list[ET.Element] = []
        seen: set[str] = set()
        while style_id is not None and style_id not in seen:
            seen.add(style_id)
            rpr = self.rpr.get(style_id)
            if rpr is not None:
                out.append(rpr)
            style_id = self.based.get(style_id)
        return out

    def properties(self, run: ET.Element, styles: list[str | None]) -> list[ET.Element]:
        own = run.find(_w("rPr"))
        found = [own] if own is not None else []
        style = own.find(_w("rStyle")) if own is not None else None
        ids = [None if style is None else style.get(_w("val")), *styles, *self.defaults]
        for style_id in ids:
            found += self.chain(style_id)
        if self.doc_rpr is not None:
            found.append(self.doc_rpr)
        return found

    def may_be_symbol(self, properties: list[ET.Element]) -> bool:
        names: set[str] = set()
        for rpr in properties:
            fonts = rpr.find(_w("rFonts"))
            if fonts is None:
                continue
            for name, value in fonts.attrib.items():
                names |= self.theme if _local(name).endswith("Theme") else {value}
        return any(name.lower().replace(" ", "") in ("symbol", "symbolmt") for name in names)

    @staticmethod
    def may_be_hidden(properties: list[ET.Element]) -> bool:
        return any(
            rpr.find(_w("vanish")) is not None or rpr.find(_w("specVanish")) is not None
            for rpr in properties
        )


def _symbol_reading(text: str) -> str | None:
    out: list[str] = []
    for character in text:
        code = ord(character)
        low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
        if low not in SYMBOL_FONT:
            return None
        out.append(SYMBOL_FONT[low])
    return "".join(out)


class _Story:
    """The tokens of one story (the body, or one note), paragraph by paragraph."""

    def __init__(
        self, fonts: _Fonts, ledger: _Ledger, story: tuple[str, int] | None = None
    ) -> None:
        self.fonts = fonts
        self.ledger = ledger
        self.story = story
        self.paragraphs: list[_Paragraph] = []
        # Open complex fields: [in its instruction, its instruction so far, a page number].
        self.fields: list[list[Any]] = []
        self.layout = 0
        self.tables = 0

    # The block structure: paragraphs in document order, and the cell each stands in.

    def blocks(
        self, element: ET.Element, table: tuple[int, int, int] | None, styles: list[str | None]
    ) -> None:
        for child in element:
            local = _local(child.tag)
            if child.tag == _w("p"):
                self.paragraph(child, table, styles)
            elif child.tag == _w("tbl"):
                self.table(child, table, styles)
            elif local == "t":
                raise CertificationError("text outside a paragraph")
            else:
                self.blocks(child, table, styles)

    def table(
        self, element: ET.Element, outer: tuple[int, int, int] | None, styles: list[str | None]
    ) -> None:
        index = self.tables
        self.tables += 1
        style = element.find(f"{_w('tblPr')}/{_w('tblStyle')}")
        inner = [*styles, None if style is None else style.get(_w("val"))]
        rows = _owned(element, _w("tr"), _w("tbl"))
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(_owned(row, _w("tc"), _w("tr"))):
                self.blocks(cell, outer or (index, row_index, cell_index), inner)

    def paragraph(
        self, element: ET.Element, table: tuple[int, int, int] | None, styles: list[str | None]
    ) -> None:
        if any(node.tag == _w("p") for node in element.iter() if node is not element):
            raise CertificationError("a paragraph inside a paragraph")
        properties = element.find(_w("pPr"))
        style = None if properties is None else properties.find(_w("pStyle"))
        here = _Paragraph(table=table)
        self.current = here
        self.styles = [None if style is None else style.get(_w("val")), *styles]
        self.inline(element)
        self.paragraphs.append(here)

    def inline(self, element: ET.Element) -> None:
        for child in element:
            local = _local(child.tag)
            if child.tag == _w("pPr"):
                if any(_local(n.tag) == "t" for n in child.iter()):
                    raise CertificationError("text in paragraph properties")
                continue
            if child.tag == _w("r"):
                self.run(child)
            elif child.tag == _w("fldSimple"):
                code = _code(child.get(_w("instr"), ""))
                if code in _LAYOUT_CODES and not self.layout and not self.in_instruction():
                    self.mark("page", None)
                    self.layout += 1
                    self.inline(child)
                    self.layout -= 1
                else:
                    self.inline(child)
            elif local == "t":
                raise CertificationError("text outside a run")
            else:
                self.inline(child)

    def in_instruction(self) -> bool:
        return any(entry[0] for entry in self.fields)

    def mark(self, kind: str, value: Any) -> None:
        self.current.segments.append(_Segment(marker=(kind, value)))

    def run(self, run: ET.Element) -> None:
        shown: list[str] = []
        # Characters of w:t in the run, the only ones the Symbol table may map.
        stored: list[tuple[int, int]] = []
        for child in run:
            local = _local(child.tag)
            if local in _RUN_SILENT:
                continue
            if local in _NOTE_MARKS:
                kind = "footnote" if local.startswith("footnote") else "endnote"
                self.flush_run(run, shown, stored)
                shown, stored = [], []
                if local.endswith("Ref"):
                    # A note's echo of its own mark, at the start of its text.
                    if self.story is None or self.story[0] != kind:
                        raise CertificationError(f"{local} outside a {kind}")
                    self.mark("note", (kind, self.story[1]))
                else:
                    self.mark("note", (kind, int(child.get(_w("id"), ""))))
                continue
            if local == "fldChar":
                self.flush_run(run, shown, stored)
                shown, stored = [], []
                self.field(child)
                continue
            if local == "instrText":
                text = child.text or ""
                self.ledger.instruction += len(text)
                self.ledger.field_code += len(text)
                if self.fields and self.fields[-1][0]:
                    self.fields[-1][1].append(text)
                continue
            token, length, is_text = self.token(child, local)
            if self.in_instruction():
                self.ledger.field_code += length
                if self.fields and self.fields[-1][0]:
                    self.fields[-1][1].append(token)
            elif self.layout:
                self.ledger.page_numbers += length
            elif not token and local == "br":
                self.ledger.page_breaks += 1
            else:
                if is_text:
                    stored.append((len("".join(shown)), len(token)))
                shown.append(token)
        self.flush_run(run, shown, stored)

    def token(self, child: ET.Element, local: str) -> tuple[str, int, bool]:
        """What one run child stands for, how many source tokens it is, and if it is w:t."""
        if local == "t":
            text = child.text or ""
            if len(child):
                raise CertificationError("an element inside a text element")
            self.ledger.text += len(text)
            return text, len(text), True
        self.ledger.elements += 1
        if local in ("tab", "ptab"):
            return "\t", 1, False
        if local == "br":
            if child.get(_w("type")) in ("page", "column"):
                # Layout: a page or column break stands for no character.
                return "", 1, False
            return "\n", 1, False
        if local == "cr":
            return "\n", 1, False
        if local == "noBreakHyphen":
            return "\u2011", 1, False
        if local == "softHyphen":
            return "\u00ad", 1, False
        if local == "sym":
            code = int(child.get(_w("char"), "0"), 16)
            low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
            if low not in SYMBOL_FONT:
                raise CertificationError(f"a w:sym outside the Symbol table: {code:#06x}")
            self.ledger.symbol += 1
            return SYMBOL_FONT[low], 1, False
        if local in ("drawing", "pict"):
            # A picture stands for one character. Text inside one (a text box, WordArt, whose
            # text is an attribute of its textpath) is text this check does not place.
            texts = {"t", "txbx", "txbxContent", "textbox", "textpath"}
            if any(_local(n.tag) in texts for n in child.iter()):
                raise CertificationError(f"text inside a {local}")
            return _OBJECT, 1, False
        raise CertificationError(f"a run holds {local}, which the check does not know")

    def field(self, child: ET.Element) -> None:
        kind = child.get(_w("fldCharType"))
        if kind == "begin":
            self.fields.append([True, [], False])
        elif kind == "separate" and self.fields:
            entry = self.fields[-1]
            nested = any(e[0] for e in self.fields[:-1])
            entry[0] = False
            if not nested and _code("".join(entry[1])) in _LAYOUT_CODES and not self.layout:
                self.mark("page", None)
                self.layout += 1
                entry[2] = True
        elif kind == "end" and self.fields:
            entry = self.fields.pop()
            if entry[2]:
                self.layout -= 1
        else:
            raise CertificationError(f"a field character {kind!r} out of place")

    def flush_run(self, run: ET.Element, shown: list[str], stored: list[tuple[int, int]]) -> None:
        text = "".join(shown)
        if not text:
            return
        properties = self.fonts.properties(run, self.styles)
        readings = [text]
        segment = _Segment()
        if stored and self.fonts.may_be_symbol(properties):
            mapped = list(text)
            ok = True
            for start, length in stored:
                reading = _symbol_reading(text[start : start + length])
                if reading is None:
                    ok = False
                    break
                mapped[start : start + length] = list(reading)
            if ok and "".join(mapped) != text:
                segment.symbol_reading = len(readings)
                readings.append("".join(mapped))
        if not text.strip() and self.fonts.may_be_hidden(properties):
            segment.hidden_reading = len(readings)
            readings.append("")
        segment.readings = tuple(readings)
        self.current.segments.append(segment)


def _owned(element: ET.Element, wanted: str, stop: str) -> list[ET.Element]:
    """``wanted`` descendants of ``element`` not inside another ``stop`` below it."""
    out: list[ET.Element] = []

    def visit(node: ET.Element) -> None:
        for child in node:
            if child.tag == wanted:
                out.append(child)
            elif child.tag != stop:
                visit(child)

    visit(element)
    return out


def _code(instruction: str) -> str | None:
    words = instruction.split()
    return words[0].upper() if words else None


# --- .docx: matching the result -----------------------------------------------------------


def _match(paragraph: _Paragraph, value: dict[str, Json], ledger: _Ledger, where: str) -> None:
    """The output paragraph must be the tokens, read by one allowed reading per run."""
    text: str = value["text"]
    # Positions reachable after each segment, with the reading taken: first reading first.
    states: list[dict[int, tuple[int, int]]] = [{0: (-1, -1)}]
    for segment in paragraph.segments:
        previous = states[-1]
        here: dict[int, tuple[int, int]] = {}
        for position in previous:
            if segment.marker is not None:
                here.setdefault(position, (position, -1))
                continue
            for choice, reading in enumerate(segment.readings):
                if text.startswith(reading, position):
                    here.setdefault(position + len(reading), (position, choice))
        if not here:
            raise CertificationError(f"{where}: the text is not the document's")
        states.append(here)
    if len(text) not in states[-1]:
        raise CertificationError(f"{where}: the text is not the document's")
    # Walk back the reading taken, to count it and to place the markers.
    position = len(text)
    taken: list[tuple[int, int]] = []
    for index in range(len(paragraph.segments), 0, -1):
        before, choice = states[index][position]
        taken.append((position, choice))
        position = before
    taken.reverse()
    pages: list[int] = []
    notes: list[tuple[int, tuple[str, int]]] = []
    for segment, (end, choice) in zip(paragraph.segments, taken, strict=True):
        if segment.marker is not None:
            kind, payload = segment.marker
            if kind == "page":
                pages.append(end)
            else:
                notes.append((end, payload))
            continue
        stored = segment.readings[0]
        if choice == segment.hidden_reading:
            ledger.hidden += len(stored)
        elif choice == segment.symbol_reading:
            mapped = segment.readings[choice]
            ledger.symbol += sum(1 for a, b in zip(stored, mapped, strict=True) if a != b)
    if pages != list(value["pages"]):
        raise CertificationError(f"{where}: the page numbers are not where the document has them")
    if notes != [(n["offset"], (n["kind"], n["id"])) for n in value["notes"]]:
        raise CertificationError(f"{where}: the note marks are not where the document has them")
    table = value["table"]
    if (None if table is None else tuple(table)) != paragraph.table:
        raise CertificationError(f"{where}: the paragraph is not in the document's table cell")
    ledger.output += len(text)


def _docx_parts(data: bytes) -> tuple[zipfile.ZipFile, str, dict[str, str]]:
    archive = zipfile.ZipFile(io.BytesIO(data))
    main = _relations(archive, "", "officeDocument")
    if len(main) != 1:
        raise CertificationError("not one main document part")
    related = {
        kind: targets[0]
        for kind in ("styles", "theme", "footnotes", "endnotes")
        if (targets := _relations(archive, main[0], kind))
    }
    return archive, main[0], related


def _relations(archive: zipfile.ZipFile, source: str, kind: str) -> list[str]:
    folder, base = posixpath.split(source)
    name = posixpath.join(folder, "_rels", base + ".rels")
    if name not in archive.namelist():
        return []
    out = []
    for rel in ET.fromstring(archive.read(name)).iter(f"{{{_RELS}}}Relationship"):
        if rel.get("TargetMode") == "External" or not rel.get("Type", "").endswith("/" + kind):
            continue
        target = rel.get("Target", "")
        out.append(
            posixpath.normpath(
                target[1:] if target.startswith("/") else posixpath.join(folder, target)
            )
        )
    return out


class DocxSource:
    """A .docx's text tokens, read once by this module's own walk, to hold results to."""

    def __init__(self, data: bytes) -> None:
        archive, main, related = _docx_parts(data)
        with archive:
            parse = {name: ET.fromstring(archive.read(name)) for name in {main, *related.values()}}
            fonts = _Fonts(
                parse.get(related.get("styles", "")), parse.get(related.get("theme", ""))
            )
            self.ledger = _Ledger()
            body = parse[main].find(_w("body"))
            if body is None:
                raise CertificationError("no body")
            story = _Story(fonts, self.ledger)
            story.blocks(body, None, [])
            self.body = story.paragraphs
            self.notes: dict[str, dict[int, list[_Paragraph]]] = {}
            self.not_read: dict[str, int] = {}
            for kind in ("footnote", "endnote"):
                self.notes[kind] = {}
                part = related.get(kind + "s")
                for note in parse[part] if part is not None else []:
                    if note.get(_w("type"), "normal") in _NOTE_LAYOUT:
                        size = _text_size(note)
                        if size:
                            key = f"{part}#{note.get(_w('type'))}"
                            self.not_read[key] = self.not_read.get(key, 0) + size
                        continue
                    note_id = int(note.get(_w("id"), ""))
                    story = _Story(fonts, self.ledger, (kind, note_id))
                    story.blocks(note, None, [])
                    self.notes[kind][note_id] = story.paragraphs
            self.scope = sorted(
                {main, *(related[k] for k in ("footnotes", "endnotes") if k in related)}
            )
            for name in sorted(archive.namelist()):
                if name in self.scope or not name.endswith(".xml"):
                    continue
                try:
                    size = _text_size(ET.fromstring(archive.read(name)))
                except (ET.ParseError, LookupError, ValueError) as error:
                    # A part this check cannot read is text it cannot say is not there.
                    raise CertificationError(f"{name} cannot be read") from error
                if size:
                    self.not_read[name] = size

    def certify(self, value: dict[str, Json]) -> dict[str, Json]:
        """The certificate for ``value``, a .docx result, or ``CertificationError``."""
        ledger = replace(self.ledger)
        paragraphs = value["paragraphs"]
        if len(paragraphs) != len(self.body):
            raise CertificationError(
                f"{len(paragraphs)} paragraphs, where the document has {len(self.body)}"
            )
        for index, (mine, theirs) in enumerate(zip(self.body, paragraphs, strict=True)):
            _match(mine, theirs, ledger, f"paragraph {index + 1}")
        for kind, notes in self.notes.items():
            theirs_by_id = {n["id"]: n for n in value[kind + "s"]}
            if set(theirs_by_id) != set(notes) or len(theirs_by_id) != len(value[kind + "s"]):
                raise CertificationError(f"the {kind}s are not the document's")
            for note_id, mine_paragraphs in notes.items():
                theirs = theirs_by_id[note_id]["paragraphs"]
                if len(theirs) != len(mine_paragraphs):
                    raise CertificationError(f"{kind} {note_id}: not the document's paragraphs")
                for index, (mine, paragraph) in enumerate(
                    zip(mine_paragraphs, theirs, strict=True)
                ):
                    _match(mine, paragraph, ledger, f"{kind} {note_id} paragraph {index + 1}")
        if not ledger.balanced():  # pragma: no cover - implied by the sequences; checked apart
            raise CertificationError("the ledger does not balance")
        return {
            "checker": CHECKER_VERSION,
            "scope": self.scope,
            "source": {
                "elements": ledger.elements,
                "instructionCharacters": ledger.instruction,
                "textCharacters": ledger.text,
            },
            "output": {"characters": ledger.output},
            "setAside": {
                "fieldCode": ledger.field_code,
                "hiddenWhitespace": ledger.hidden,
                "pageBreaks": ledger.page_breaks,
                "pageNumbers": ledger.page_numbers,
            },
            "symbolMapped": ledger.symbol,
            "notRead": dict(self.not_read),
        }


def certify_docx(data: bytes, value: dict[str, Json]) -> dict[str, Json]:
    """The certificate for a .docx result, or ``CertificationError``."""
    return DocxSource(data).certify(value)


def _text_size(root: ET.Element) -> int:
    """Characters in every text element (``w:t``, DrawingML ``a:t``...) under ``root``."""
    return sum(len(node.text or "") for node in root.iter() if _local(node.tag) == "t")


# --- ePI ------------------------------------------------------------------------------------

_BLOCKS = {
    "div",
    "p",
    "li",
    "td",
    "th",
    "table",
    "tr",
    "ul",
    "ol",
    "thead",
    "tbody",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}
_COLLAPSIBLE = " \t\n\r\f"


class _Div(HTMLParser):
    """A div's paragraphs, by HTML's tokens and CSS's whitespace rule (``white-space: normal``)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        # The cell of the outermost table each paragraph stands in: (table, row, cell).
        self.cells: list[tuple[int, int, int] | None] = []
        self.open_tables: list[list[int]] = []
        self.tables = 0
        self.current: list[str] = []
        self.pending = False
        self.source = 0
        self.whitespace = 0
        self.breaks = 0
        self.pictures = 0

    def flush(self) -> None:
        self.pending = False
        while self.current and self.current[-1] == "\n":
            self.current.pop()
        if self.current:
            self.paragraphs.append("".join(self.current))
            self.cells.append(tuple(self.open_tables[0]) if self.open_tables else None)  # type: ignore[arg-type]
        self.current = []

    def emit(self, character: str) -> None:
        if self.pending:
            self.current.append(" ")
            self.pending = False
        self.current.append(character)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag in _BLOCKS:
            self.flush()
            # Tables are counted in document order, nested ones too; rows across a table's
            # row groups; cells within their row.
            if tag == "table":
                self.open_tables.append([self.tables, -1, -1])
                self.tables += 1
            elif tag == "tr" and self.open_tables:
                self.open_tables[-1][1:] = [self.open_tables[-1][1] + 1, -1]
            elif tag in ("td", "th") and self.open_tables:
                self.open_tables[-1][2] += 1
        elif tag == "br":
            self.breaks += 1
            self.pending = False
            self.current.append("\n")
        elif tag == "img":
            self.pictures += 1
            self.emit(_OBJECT)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCKS:
            self.flush()
            if tag == "table" and self.open_tables:
                self.open_tables.pop()

    def handle_data(self, data: str) -> None:
        for character in data:
            self.source += 1
            if character in _COLLAPSIBLE:
                self.whitespace += 1
                if self.current and self.current[-1] != "\n":
                    self.pending = True
            else:
                self.emit(character)


@dataclass
class _EpiSection:
    title: Json
    # The div read by this module, or None where the section has none.
    div: _Div | None
    sections: list[_EpiSection]


class EpiSource:
    """An ePI's sections, read once by the standard library's HTML parser, to hold results to."""

    def __init__(self, data: bytes) -> None:
        bundle = json.loads(data.decode("utf-8"))
        compositions = [
            e["resource"]
            for e in bundle["entry"]
            if isinstance(e.get("resource"), dict) and "section" in e["resource"]
        ]
        if len(compositions) != 1:
            raise CertificationError("not one Composition with sections")
        self.sections = [self._section(raw) for raw in compositions[0]["section"]]
        self.narratives = sum(
            1 for path, _ in _strings(bundle, ()) if path[-1:] == ("div",) and "section" not in path
        )

    def _section(self, raw: dict[str, Json]) -> _EpiSection:
        div = raw.get("text", {}).get("div")
        parsed = None
        if isinstance(div, str):
            parsed = _Div()
            parsed.feed(div)
            parsed.close()
            parsed.flush()
        return _EpiSection(
            raw.get("title"), parsed, [self._section(c) for c in raw.get("section", [])]
        )

    def certify(self, value: dict[str, Json]) -> dict[str, Json]:
        """The certificate for ``value``, an ePI result, or ``CertificationError``."""
        counts = dict.fromkeys(
            ("source", "whitespace", "breaks", "pictures", "output", "spaces", "lines"), 0
        )

        def section(mine: _EpiSection, theirs: dict[str, Json], where: str) -> None:
            if theirs["title"] != mine.title:
                raise CertificationError(f"{where}: the title is not the document's")
            if theirs["refusal"] is None and mine.div is not None:
                texts = [p["text"] for p in theirs["paragraphs"]]
                cells = [
                    None if p["table"] is None else tuple(p["table"]) for p in theirs["paragraphs"]
                ]
                if cells != mine.div.cells and texts == mine.div.paragraphs:
                    raise CertificationError(f"{where}: a paragraph is not in the document's cell")
                if texts != mine.div.paragraphs:
                    at = next(
                        (
                            i
                            for i, (a, b) in enumerate(
                                zip(texts, mine.div.paragraphs, strict=False)
                            )
                            if a != b
                        ),
                        min(len(texts), len(mine.div.paragraphs)),
                    )
                    raise CertificationError(f"{where}: paragraph {at + 1} is not the document's")
                counts["source"] += mine.div.source
                counts["whitespace"] += mine.div.whitespace
                counts["breaks"] += mine.div.breaks
                counts["pictures"] += mine.div.pictures
                for text in texts:
                    counts["output"] += len(text)
                    counts["spaces"] += text.count(" ")
                    counts["lines"] += text.count("\n")
            elif theirs["paragraphs"]:
                raise CertificationError(f"{where}: paragraphs where the document has none to read")
            if len(mine.sections) != len(theirs["sections"]):
                raise CertificationError(f"{where}: not the document's sections")
            for index, (child, other) in enumerate(
                zip(mine.sections, theirs["sections"], strict=True)
            ):
                section(child, other, f"{where}.{index + 1}")

        if len(self.sections) != len(value["sections"]):
            raise CertificationError("not the document's sections")

        def count(sections: list[dict[str, Json]]) -> int:
            return sum((s["refusal"] is not None) + count(s["sections"]) for s in sections)

        refused = count(value["sections"])
        if value["refusedSections"] != refused:
            # A section the reader did not read is never hidden: the count a receipt reports
            # must be the sections the result calls refused.
            raise CertificationError("the refused sections are not the ones counted")
        for index, (mine, theirs) in enumerate(zip(self.sections, value["sections"], strict=True)):
            section(mine, theirs, f"section {index + 1}")
        # Every non-whitespace character is in the output, once; each whitespace run is at
        # most a space; a picture is one character, and a break one line break unless it ends
        # its paragraph, where it draws nothing.
        shown = counts["source"] - counts["whitespace"]
        if (
            counts["output"] != shown + counts["spaces"] + counts["lines"] + counts["pictures"]
        ):  # pragma: no cover - implied
            raise CertificationError("the ledger does not balance")
        return {
            "checker": CHECKER_VERSION,
            "scope": "Composition.section title and text.div",
            "source": {
                "breaks": counts["breaks"],
                "characters": counts["source"],
                "pictures": counts["pictures"],
                "whitespace": counts["whitespace"],
            },
            "output": {"characters": counts["output"], "spaces": counts["spaces"]},
            "setAside": {
                "closingBreaks": counts["breaks"] - counts["lines"],
                "collapsedWhitespace": counts["whitespace"] - counts["spaces"],
            },
            "notRead": {"narratives": self.narratives},
        }


def certify_epi(data: bytes, value: dict[str, Json]) -> dict[str, Json]:
    """The certificate for an ePI result, or ``CertificationError``."""
    return EpiSource(data).certify(value)


def _strings(value: Json, path: tuple[str, ...]) -> list[tuple[tuple[str, ...], str]]:
    if isinstance(value, dict):
        return [s for key, item in value.items() for s in _strings(item, (*path, key))]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item, path)]
    return [(path, value)] if isinstance(value, str) else []
