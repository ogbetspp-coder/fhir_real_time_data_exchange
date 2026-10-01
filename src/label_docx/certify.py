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

The check leaves the reader no choice: every token has one reading. Two of them depend on the
run's formatting, and the check works both out itself, by rules written here and not shared with
the reader (``_Fonts``): whether a run is drawn in the Symbol font (its text is then read
through the Symbol table, counted as ``symbolMapped``), and whether it is hidden (hidden
whitespace is left out, counted as ``hiddenWhitespace``; hidden text with characters to show is
never certified). The set-aside reasons are fixed too: a field's instruction (code, not shown),
a page number (set by the layout; its place must be in ``pages``), a page or column break
(layout).

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
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from label_docx.reader import SYMBOL_FONT

CHECKER_VERSION = "conservation-check/1.3.0"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OBJECT = "\ufffc"
_LAYOUT_CODES = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}
_NOTE_LAYOUT = {"separator", "continuationSeparator", "continuationNotice"}
# Run children that hold no text and stand for none.
_RUN_SILENT = {"rPr", "lastRenderedPageBreak"}
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
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
    """The text one run shows (as the check reads it), or a zero-width marker."""

    text: str = ""
    marker: tuple[str, Any] | None = None
    # The marks the check works out for the run's text (``CHECKED_MARKS``).
    kinds: frozenset[str] = frozenset()


@dataclass
class _Paragraph:
    segments: list[_Segment] = field(default_factory=list)
    table: tuple[int, int, int] | None = None


# The marks the check works out itself and holds every result to; the others (highlight,
# shading, faint, raised text, right-to-left) are held to Word (tests/test_word_oracle.py).
_CHECKED_TOGGLES = {
    "b": "bold",
    "i": "italic",
    "caps": "caps",
    "smallCaps": "smallCaps",
    "strike": "strike",
    "dstrike": "dstrike",
}
CHECKED_MARKS = frozenset({*_CHECKED_TOGGLES.values(), "superscript", "subscript", "underline"})

_THEME_SLOT = {
    "ascii": "asciiTheme",
    "hAnsi": "hAnsiTheme",
    "eastAsia": "eastAsiaTheme",
    "cs": "cstheme",
}
_SCRIPT = {"Ascii": "latin", "HAnsi": "latin", "EastAsia": "ea", "Bidi": "cs"}


def _on(element: ET.Element | None) -> bool | None:
    """A toggle element's setting: None when absent, else on unless its value says off."""
    if element is None:
        return None
    return element.get(_w("val"), "true").lower() not in ("0", "false", "off")


class _Fonts:
    """The two run properties that decide which characters a run shows: font and hidden.

    Worked out here, by Word's precedence, written down on its own (it shares no code with the
    reader): the run's own properties, then its character style, its paragraph style and the
    innermost table's style, each with its ``basedOn`` chain, then the document defaults; a
    style id that is absent or unknown means the default style of its kind (the last marked
    default). A font slot (``ascii``, ``hAnsi``...) is set by the first level that names it, a
    theme reference there naming the theme's typeface for its script. A run is hidden when it
    says so itself, or, saying nothing, when any level says so (the reading the reader states;
    Word's prints are held to it by ``tests/test_word_oracle.py``).
    """

    def __init__(self, styles: ET.Element | None, theme: ET.Element | None) -> None:
        self.kind: dict[str, str] = {}
        self.based: dict[str, str | None] = {}
        self.rpr: dict[str, ET.Element | None] = {}
        self.defaults: dict[str, str] = {}
        self.doc_rpr: ET.Element | None = None
        if styles is not None:
            for style in styles.findall(_w("style")):
                style_id = style.get(_w("styleId"))
                if style_id is None:
                    continue
                kind = style.get(_w("type"), "paragraph")
                based = style.find(_w("basedOn"))
                self.kind[style_id] = kind
                self.based[style_id] = None if based is None else based.get(_w("val"))
                self.rpr[style_id] = style.find(_w("rPr"))
                if style.get(_w("default")) in ("1", "true", "on"):
                    self.defaults[kind] = style_id
            self.doc_rpr = styles.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
        self.theme: dict[str, str] = {}
        scheme = (
            None
            if theme is None
            else next((n for n in theme.iter() if _local(n.tag) == "fontScheme"), None)
        )
        for group in [] if scheme is None else list(scheme):
            prefix = _local(group.tag).removesuffix("Font")
            for child in group:
                if _local(child.tag) in ("latin", "ea", "cs"):
                    self.theme[f"{prefix}:{_local(child.tag)}"] = child.get("typeface", "")

    def chain(self, style_id: str | None, kind: str) -> list[ET.Element]:
        if style_id is None or style_id not in self.kind:
            style_id = self.defaults.get(kind)
        out: list[ET.Element] = []
        seen: set[str] = set()
        while style_id is not None and style_id in self.kind and style_id not in seen:
            seen.add(style_id)
            rpr = self.rpr[style_id]
            if rpr is not None:
                out.append(rpr)
            style_id = self.based[style_id]
        return out

    def levels(
        self, run: ET.Element, paragraph_style: str | None, table_style: str | None, in_table: bool
    ) -> list[ET.Element]:
        own = run.find(_w("rPr"))
        style = None if own is None else own.find(_w("rStyle"))
        found = [] if own is None else [own]
        found += self.chain(None if style is None else style.get(_w("val")), "character")
        found += self.chain(paragraph_style, "paragraph")
        if in_table:
            found += self.chain(table_style, "table")
        if self.doc_rpr is not None:
            found.append(self.doc_rpr)
        return found

    def marks(
        self, run: ET.Element, paragraph_style: str | None, table_style: str | None, in_table: bool
    ) -> frozenset[str]:
        """The marks of ``CHECKED_MARKS`` Word shows on the run, by Word's rules, worked out here.

        Toggles (bold, italic, capitals, small capitals, strike, double strike): the run's own
        setting wins, on or off; else each kind of style (character, paragraph, table, each with
        its ``basedOn`` chain) gives its nearest setting and the kinds cancel in pairs; the
        document defaults then turn it on whatever the styles give. Superscript, subscript and
        underline: the nearest level that sets a value, the run first. Each rule is Word's answer
        to a case in ``corpus/numbering-cases``.
        """
        own = run.find(_w("rPr"))
        style = None if own is None else own.find(_w("rStyle"))
        chains = [
            self.chain(None if style is None else style.get(_w("val")), "character"),
            self.chain(paragraph_style, "paragraph"),
            self.chain(table_style, "table") if in_table else [],
        ]
        kinds: set[str] = set()
        for name, kind in _CHECKED_TOGGLES.items():
            direct = None if own is None else _on(own.find(_w(name)))
            if direct is not None:
                shown = direct
            else:
                shown = False
                for chain in chains:
                    settings = (_on(rpr.find(_w(name))) for rpr in chain)
                    shown ^= next((v for v in settings if v is not None), False)
                if self.doc_rpr is not None and _on(self.doc_rpr.find(_w(name))):
                    shown = True
            if shown:
                kinds.add(kind)
        levels = [own, *(rpr for chain in chains for rpr in chain), self.doc_rpr]

        def nearest(name: str) -> str | None:
            for level in levels:
                found = None if level is None else level.find(_w(name))
                if found is not None and found.get(_w("val")) is not None:
                    return found.get(_w("val"))
            return None

        align = nearest("vertAlign")
        if align in ("superscript", "subscript"):
            kinds.add(str(align))
        if nearest("u") not in (None, "none"):
            kinds.add("underline")
        return frozenset(kinds)

    def font(self, levels: list[ET.Element], slot: str) -> str | None:
        for level in levels:
            fonts = level.find(_w("rFonts"))
            if fonts is None:
                continue
            theme = fonts.get(_w(_THEME_SLOT[slot]))
            if theme is not None:
                prefix = "major" if theme.startswith("major") else "minor"
                script = _SCRIPT.get(theme.removeprefix(prefix), "")
                key = f"{prefix}:{script}"
                if key not in self.theme:
                    raise CertificationError(f"the theme font {theme} is not in the theme")
                return self.theme[key]
            if fonts.get(_w(slot)) is not None:
                return fonts.get(_w(slot))
        return None

    def symbol(self, levels: list[ET.Element]) -> bool:
        """Whether the run's text is drawn in Symbol: its ``ascii`` and ``hAnsi`` fonts both."""
        slots = {slot: _is_symbol(self.font(levels, slot)) for slot in _THEME_SLOT}
        if any(slots.values()) and not (slots["ascii"] and slots["hAnsi"]):
            raise CertificationError("Symbol set for only some of a run's characters")
        return slots["ascii"]

    @staticmethod
    def hidden(levels: list[ET.Element], own: ET.Element | None) -> bool:
        direct = None if own is None else _on(own.find(_w("vanish")))
        if direct is not None:
            return direct
        return any(_on(level.find(_w("vanish"))) for level in levels)


def _is_symbol(name: str | None) -> bool:
    return name is not None and name.lower().replace(" ", "") in ("symbol", "symbolmt")


def _symbol_reading(text: str) -> str:
    out: list[str] = []
    for character in text:
        code = ord(character)
        low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
        if low not in SYMBOL_FONT:
            raise CertificationError(f"a Symbol character outside the table: {code:#06x}")
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
        self, element: ET.Element, table: tuple[int, int, int] | None, table_style: str | None
    ) -> None:
        for child in element:
            local = _local(child.tag)
            if child.tag == _w("p"):
                self.paragraph(child, table, table_style)
            elif child.tag == _w("tbl"):
                self.table(child, table)
            elif local == "t":
                raise CertificationError("text outside a paragraph")
            else:
                self.blocks(child, table, table_style)

    def table(self, element: ET.Element, outer: tuple[int, int, int] | None) -> None:
        index = self.tables
        self.tables += 1
        style = element.find(f"{_w('tblPr')}/{_w('tblStyle')}")
        own = None if style is None else style.get(_w("val"))
        rows = _owned(element, _w("tr"), _w("tbl"))
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(_owned(row, _w("tc"), _w("tr"))):
                self.blocks(cell, outer or (index, row_index, cell_index), own)

    def paragraph(
        self, element: ET.Element, table: tuple[int, int, int] | None, table_style: str | None
    ) -> None:
        if any(node.tag == _w("p") for node in element.iter() if node is not element):
            raise CertificationError("a paragraph inside a paragraph")
        properties = element.find(_w("pPr"))
        style = None if properties is None else properties.find(_w("pStyle"))
        here = _Paragraph(table=table)
        self.current = here
        self.paragraph_style = None if style is None else style.get(_w("val"))
        self.table_style = table_style
        self.in_table = table is not None
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
        levels = self.fonts.levels(run, self.paragraph_style, self.table_style, self.in_table)
        symbol = self.fonts.symbol(levels)
        hidden = self.fonts.hidden(levels, run.find(_w("rPr")))
        kinds = self.fonts.marks(run, self.paragraph_style, self.table_style, self.in_table)
        shown: list[str] = []
        for child in run:
            local = _local(child.tag)
            if local in _RUN_SILENT:
                continue
            if local == "annotationRef":
                # A comment's echo of its own mark, at the start of its text: no character.
                if self.story is None or self.story[0] != "comment":
                    raise CertificationError("annotationRef outside a comment")
                continue
            if local == "commentReference":
                if self.story is not None and self.story[0] == "comment":
                    raise CertificationError("a comment's mark in a comment")
                self.flush_run(shown, hidden, kinds)
                shown = []
                self.mark("comment", int(child.get(_w("id"), "")))
                continue
            if local in _NOTE_MARKS:
                kind = "footnote" if local.startswith("footnote") else "endnote"
                self.flush_run(shown, hidden, kinds)
                shown = []
                if local.endswith("Ref"):
                    # A note's echo of its own mark, at the start of its text.
                    if self.story is None or self.story[0] != kind:
                        raise CertificationError(f"{local} outside a {kind}")
                    self.mark("note", (kind, self.story[1]))
                else:
                    self.mark("note", (kind, int(child.get(_w("id"), ""))))
                continue
            if local == "fldChar":
                self.flush_run(shown, hidden, kinds)
                shown = []
                self.field(child)
                continue
            if local == "instrText":
                if not self.in_instruction():
                    raise CertificationError("field code outside a field's instruction")
                text = child.text or ""
                self.ledger.instruction += len(text)
                self.ledger.field_code += len(text)
                if self.fields[-1][0]:
                    self.fields[-1][1].append(text)
                continue
            token = self.token(child, local, symbol)
            # One token for each character of a text element, one for any other element.
            length = len(child.text or "") if local == "t" else 1
            if self.in_instruction():
                self.ledger.field_code += length
                if self.fields[-1][0]:
                    self.fields[-1][1].append(token)
            elif self.layout:
                self.ledger.page_numbers += length
            elif not token and local == "br":
                self.ledger.page_breaks += 1
            else:
                shown.append(token)
        self.flush_run(shown, hidden, kinds)

    def token(self, child: ET.Element, local: str, symbol: bool) -> str:
        """What one run child stands for, as the check reads it."""
        if local == "t":
            text = child.text or ""
            if len(child):
                raise CertificationError("an element inside a text element")
            self.ledger.text += len(text)
            if symbol:
                mapped = _symbol_reading(text)
                self.ledger.symbol += sum(1 for a, b in zip(text, mapped, strict=True) if a != b)
                return mapped
            return text
        self.ledger.elements += 1
        if local in ("tab", "ptab"):
            return "\t"
        if local == "br":
            if child.get(_w("type")) in ("page", "column"):
                # Layout: a page or column break stands for no character.
                return ""
            return "\n"
        if local == "cr":
            return "\n"
        if local == "noBreakHyphen":
            return "\u2011"
        if local == "softHyphen":
            return "\u00ad"
        if local == "sym":
            code = int(child.get(_w("char"), "0"), 16)
            low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
            if low not in SYMBOL_FONT:
                raise CertificationError(f"a w:sym outside the Symbol table: {code:#06x}")
            self.ledger.symbol += 1
            return SYMBOL_FONT[low]
        if local in ("drawing", "pict"):
            # A picture stands for one character. Text inside one (a text box, WordArt, whose
            # text is an attribute of its textpath) is text this check does not place.
            texts = {"t", "txbx", "txbxContent", "textbox", "textpath"}
            if any(_local(n.tag) in texts for n in child.iter()):
                raise CertificationError(f"text inside a {local}")
            return _OBJECT
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

    def flush_run(self, shown: list[str], hidden: bool, kinds: frozenset[str]) -> None:
        text = "".join(shown)
        if not text:
            return
        if hidden:
            if text.strip():
                # Hidden text Word does not show: the reader refuses it, never reads it.
                raise CertificationError("hidden text with characters to show")
            self.ledger.hidden += len(text)
            return
        self.current.segments.append(_Segment(text=text, kinds=kinds))


def _owned(element: ET.Element, wanted: str, stop: str) -> list[ET.Element]:
    """``wanted`` descendants of ``element``: a table's rows, or a row's cells.

    Through any wrapper (a content control, custom XML), not into what is found. A ``stop`` met
    on the way (a table inside a table but outside its cells, a row inside a row) is refused:
    its text would otherwise be passed over.
    """
    out: list[ET.Element] = []

    def visit(node: ET.Element) -> None:
        for child in node:
            if child.tag == wanted:
                out.append(child)
            elif child.tag == stop:
                raise CertificationError(f"a {_local(stop)} outside the cells of another")
            else:
                visit(child)

    visit(element)
    return out


def _code(instruction: str) -> str | None:
    words = instruction.split()
    return words[0].upper() if words else None


# --- .docx: matching the result -----------------------------------------------------------


def _match(paragraph: _Paragraph, value: dict[str, Json], where: str) -> None:
    """The output paragraph must be exactly the tokens the check read, and its marks placed."""
    text: str = value["text"]
    expected: list[str] = []
    pages: list[int] = []
    notes: list[tuple[int, tuple[str, int]]] = []
    comments: list[tuple[int, int]] = []
    position = 0
    for segment in paragraph.segments:
        if segment.marker is None:
            expected.append(segment.text)
            position += len(segment.text)
        elif segment.marker[0] == "page":
            pages.append(position)
        elif segment.marker[0] == "comment":
            comments.append((position, segment.marker[1]))
        else:
            notes.append((position, segment.marker[1]))
    if text != "".join(expected):
        raise CertificationError(f"{where}: the text is not the document's")
    # The marks this check works out, character by character.
    shown = [
        kinds for segment in paragraph.segments for kinds in [segment.kinds] * len(segment.text)
    ]
    claimed: list[set[str]] = [set() for _ in text]
    for mark in value["marks"]:
        if mark["kind"] in CHECKED_MARKS:
            for index in range(mark["start"], mark["end"]):
                claimed[index].add(mark["kind"])
    if [set(k) for k in shown] != claimed:
        at = next(i for i, (a, b) in enumerate(zip(shown, claimed, strict=True)) if set(a) != b)
        raise CertificationError(f"{where}: the marks at character {at + 1} are not Word's")
    if pages != list(value["pages"]):
        raise CertificationError(f"{where}: the page numbers are not where the document has them")
    if notes != [(n["offset"], (n["kind"], n["id"])) for n in value["notes"]]:
        raise CertificationError(f"{where}: the note marks are not where the document has them")
    if comments != [(c["offset"], c["id"]) for c in value["comments"]]:
        raise CertificationError(f"{where}: the comment marks are not where the document has them")
    table = value["table"]
    if (None if table is None else tuple(table)) != paragraph.table:
        raise CertificationError(f"{where}: the paragraph is not in the document's table cell")


def _docx_parts(data: bytes) -> tuple[zipfile.ZipFile, str, dict[str, str]]:
    archive = zipfile.ZipFile(io.BytesIO(data))
    main = _relations(archive, "", "officeDocument")
    if len(main) != 1:
        raise CertificationError("not one main document part")
    related = {
        kind: targets[0]
        for kind in ("styles", "theme", "footnotes", "endnotes", "comments")
        if (targets := _relations(archive, main[0], kind))
    }
    return archive, main[0], related


def _relation(archive: zipfile.ZipFile, source: str, relationship: str | None, kind: str) -> str:
    """The part ``source``'s relationship ``relationship`` names, which must be a ``kind``."""
    folder, base = posixpath.split(source)
    name = posixpath.join(folder, "_rels", base + ".rels")
    for rel in ET.fromstring(archive.read(name)).iter(f"{{{_RELS}}}Relationship"):
        if rel.get("Id") != relationship:
            continue
        if rel.get("TargetMode") == "External" or not rel.get("Type", "").endswith("/" + kind):
            raise CertificationError(f"{relationship} is not a {kind} part")
        target = rel.get("Target", "")
        return posixpath.normpath(
            target[1:] if target.startswith("/") else posixpath.join(folder, target)
        )
    raise CertificationError(f"no relationship {relationship}")


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


@dataclass
class _Part:
    """One story's paragraphs as the check reads them, with its own ledger, or why it cannot."""

    paragraphs: list[_Paragraph]
    ledger: _Ledger
    error: str | None = None


def _total(ledgers: list[_Ledger]) -> _Ledger:
    total = _Ledger()
    for ledger in ledgers:
        for name in total.__dataclass_fields__:
            setattr(total, name, getattr(total, name) + getattr(ledger, name))
    return total


# --- the text read a second time, without an XML parser --------------------------------------

_XML_NS = "http://www.w3.org/XML/1998/namespace"
_REFERENCE = re.compile(r"&(?:(lt|gt|amp|quot|apos)|#([0-9]+)|#x([0-9a-fA-F]+));|&")
_PREDEFINED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}
_ATTRIBUTE = re.compile(r"""([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TEXT_LOCALS = ("t", "instrText")


def _unescape(text: str) -> str:
    def one(found: re.Match[str]) -> str:
        if found.group(1):
            return _PREDEFINED[found.group(1)]
        if found.group(2) or found.group(3):
            return chr(int(found.group(2) or found.group(3), 10 if found.group(2) else 16))
        raise CertificationError("an '&' that is not a reference")

    return _REFERENCE.sub(one, text)


def _raw_texts(data: bytes) -> list[tuple[str, str]]:
    """Every ``w:t`` and ``w:instrText`` of a part, with its text, read by a tokenizer here.

    Written apart from Python's XML parser, so that the text of a part rests on two readings
    that must agree (``_parse``): line ends as XML normalises them, the five predefined and the
    numeric references, CDATA sections, comments and processing instructions passed over,
    namespace prefixes bound where they are declared. Anything else (a DTD, an unclosed tag)
    stops it.
    """
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    out: list[tuple[str, str]] = []
    scopes: list[dict[str, str]] = [{"xml": _XML_NS}]
    capture: list[str] | None = None
    captured = ""
    depth_of_capture = -1
    at = 0
    while True:
        less = text.find("<", at)
        chunk = text[at:] if less < 0 else text[at:less]
        if capture is not None:
            capture.append(_unescape(chunk))
        if less < 0:
            break
        if text.startswith("<!--", less):
            at = text.index("-->", less) + 3
        elif text.startswith("<![CDATA[", less):
            end = text.index("]]>", less)
            if capture is not None:
                capture.append(text[less + 9 : end])
            at = end + 3
        elif text.startswith("<?", less):
            at = text.index("?>", less) + 2
        elif text.startswith("<!", less):
            raise CertificationError("a declaration the check does not read")
        else:
            end, quote = less + 1, ""
            while end < len(text) and (quote or text[end] != ">"):
                if text[end] in "\"'":
                    quote = "" if quote == text[end] else quote or text[end]
                end += 1
            if end >= len(text):
                raise CertificationError("a tag that does not end")
            tag = text[less + 1 : end]
            at = end + 1
            if tag.startswith("/"):
                scopes.pop()
                if capture is not None and len(scopes) == depth_of_capture:
                    out.append((captured, "".join(capture)))
                    capture = None
                continue
            closed = tag.endswith("/")
            body = tag[:-1] if closed else tag
            name = body.split(None, 1)[0] if body.strip() else ""
            scope = dict(scopes[-1])
            for key, double, single in _ATTRIBUTE.findall(body[len(name) :]):
                value = _unescape(double or single)
                if key == "xmlns":
                    scope[""] = value
                elif key.startswith("xmlns:"):
                    scope[key[6:]] = value
            prefix, _, local = name.rpartition(":")
            if scope.get(prefix) == W and local in _TEXT_LOCALS and capture is None:
                if closed:
                    out.append((local, ""))
                else:
                    capture, captured, depth_of_capture = [], local, len(scopes)
            if not closed:
                scopes.append(scope)
    if capture is not None or len(scopes) != 1:
        raise CertificationError("elements that do not close")
    return out


def _parse(archive: zipfile.ZipFile, name: str) -> ET.Element:
    """A part parsed, its text read twice, by Python's XML parser and by ``_raw_texts``."""
    data = archive.read(name)
    root = ET.fromstring(data)
    parsed = [
        (_local(node.tag), node.text or "")
        for node in root.iter()
        if node.tag in (_w("t"), _w("instrText"))
    ]
    if _raw_texts(data) != parsed:
        raise CertificationError(f"{name}: two readings of its text do not agree")
    return root


class DocxSource:
    """A .docx's text tokens, read once by this module's own walk, to hold results to.

    The body and the notes must be read; a header, a footer or a comment the reader may refuse
    on its own, and then its text must be empty in the result and its name is listed as refused.
    """

    def __init__(self, data: bytes) -> None:
        archive, main, related = _docx_parts(data)
        with archive:
            parse = {name: _parse(archive, name) for name in {main, *related.values()}}
            self.fonts = _Fonts(
                parse.get(related.get("styles", "")), parse.get(related.get("theme", ""))
            )
            body = parse[main].find(_w("body"))
            if body is None:
                raise CertificationError("no body")
            self.body = self._part(body, None)
            self.notes: dict[str, dict[int, _Part]] = {}
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
                    self.notes[kind][note_id] = self._part(note, (kind, note_id))
            # Headers and footers: each part once, in the order the sections refer to them.
            self.stories: dict[str, list[tuple[str, list[dict[str, Json]], _Part]]] = {
                "header": [],
                "footer": [],
            }
            for section, properties in enumerate(parse[main].iter(_w("sectPr"))):
                for reference in properties:
                    story_kind = next(
                        (k for k in self.stories if reference.tag == _w(f"{k}Reference")), None
                    )
                    if story_kind is None:
                        continue
                    kind = story_kind
                    name = _relation(archive, main, reference.get(f"{{{_R}}}id"), kind)
                    use = {"section": section, "type": reference.get(_w("type"), "default")}
                    found = next((e for e in self.stories[kind] if e[0] == name), None)
                    if found is not None:
                        found[1].append(use)
                        continue
                    root = _parse(archive, name)
                    index = len(self.stories[kind])
                    self.stories[kind].append((name, [use], self._optional(root, (kind, index))))
            # Comments, as stored, each with what the part says of it.
            self.comments: list[tuple[dict[str, Json], _Part]] = []
            comments_part = related.get("comments")
            for comment in parse[comments_part] if comments_part is not None else []:
                comment_id = int(comment.get(_w("id"), ""))
                stored = {
                    "author": comment.get(_w("author")),
                    "date": comment.get(_w("date")),
                    "id": comment_id,
                    "initials": comment.get(_w("initials")),
                }
                self.comments.append((stored, self._optional(comment, ("comment", comment_id))))
            self.parts = {
                main,
                *(related[k] for k in ("footnotes", "endnotes", "comments") if k in related),
                *(name for found in self.stories.values() for name, _, _ in found),
            }
            for name in sorted(archive.namelist()):
                if name in self.parts or not name.endswith(".xml"):
                    continue
                try:
                    size = _text_size(ET.fromstring(archive.read(name)))
                except (ET.ParseError, LookupError, ValueError) as error:
                    # A part this check cannot read is text it cannot say is not there.
                    raise CertificationError(f"{name} cannot be read") from error
                if size:
                    self.not_read[name] = size
            self.comments_part = comments_part

    def _part(self, element: ET.Element, story: tuple[str, int] | None) -> _Part:
        ledger = _Ledger()
        walk = _Story(self.fonts, ledger, story)
        walk.blocks(element, None, None)
        return _Part(walk.paragraphs, ledger)

    def _optional(self, element: ET.Element, story: tuple[str, int]) -> _Part:
        """A part the reader may refuse on its own: what the check cannot read is kept as such."""
        try:
            return self._part(element, story)
        except CertificationError as error:
            return _Part([], _Ledger(), str(error))

    def certify(self, value: dict[str, Json]) -> dict[str, Json]:
        """The certificate for ``value``, a .docx result, or ``CertificationError``."""
        read = [self.body]
        refused: list[str] = []
        _paragraphs(self.body, value["paragraphs"], "paragraph")
        for kind, notes in self.notes.items():
            theirs_by_id = {n["id"]: n for n in value[kind + "s"]}
            if set(theirs_by_id) != set(notes) or len(theirs_by_id) != len(value[kind + "s"]):
                raise CertificationError(f"the {kind}s are not the document's")
            for note_id, part in notes.items():
                _paragraphs(
                    part, theirs_by_id[note_id]["paragraphs"], f"{kind} {note_id} paragraph"
                )
                read.append(part)
        for kind, found in self.stories.items():
            theirs = value[kind + "s"]
            if [(t["part"], t["uses"]) for t in theirs] != [(n, u) for n, u, _ in found]:
                raise CertificationError(f"the {kind}s are not the ones the sections refer to")
            for (name, _, part), story in zip(found, theirs, strict=True):
                if _refused(part, story, name):
                    refused.append(name)
                else:
                    read.append(part)
        stored = [c for c, _ in self.comments]
        if [{k: c[k] for k in ("author", "date", "id", "initials")} for c in value["comments"]] != (
            stored
        ):
            raise CertificationError("the comments are not the document's")
        for (comment, part), theirs in zip(self.comments, value["comments"], strict=True):
            where = f"{self.comments_part}#{comment['id']}"
            if _refused(part, theirs, where):
                refused.append(where)
            else:
                read.append(part)
        if value["refusedParts"] != len(refused):
            raise CertificationError("the refused parts are not the ones counted")
        ledger = _total([part.ledger for part in read])
        for _part, theirs in self._pairs(value, refused):
            ledger.output += sum(len(p["text"]) for p in theirs)
        if not ledger.balanced():  # pragma: no cover - implied by the sequences; checked apart
            raise CertificationError("the ledger does not balance")
        return {
            "checker": CHECKER_VERSION,
            "scope": sorted(self.parts),
            "refused": refused,
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
            "marksChecked": sorted(CHECKED_MARKS),
            "notRead": dict(self.not_read),
        }

    def _pairs(
        self, value: dict[str, Json], refused: list[str]
    ) -> list[tuple[_Part, list[dict[str, Json]]]]:
        """Each read part with the result's paragraphs for it, to count the output."""
        out = [(self.body, value["paragraphs"])]
        for kind, notes in self.notes.items():
            theirs_by_id = {n["id"]: n for n in value[kind + "s"]}
            out += [(part, theirs_by_id[i]["paragraphs"]) for i, part in notes.items()]
        for kind, found in self.stories.items():
            for (name, _, part), story in zip(found, value[kind + "s"], strict=True):
                if name not in refused:
                    out.append((part, story["paragraphs"]))
        for (comment, part), theirs in zip(self.comments, value["comments"], strict=True):
            if f"{self.comments_part}#{comment['id']}" not in refused:
                out.append((part, theirs["paragraphs"]))
        return out


def _paragraphs(part: _Part, theirs: list[dict[str, Json]], where: str) -> None:
    """The result's paragraphs for a part must be the part's, one by one."""
    if len(theirs) != len(part.paragraphs):
        raise CertificationError(
            f"{len(theirs)} {where}s, where the document has {len(part.paragraphs)}"
        )
    for index, (mine, paragraph) in enumerate(zip(part.paragraphs, theirs, strict=True)):
        _match(mine, paragraph, f"{where} {index + 1}")


def _refused(part: _Part, theirs: dict[str, Json], where: str) -> bool:
    """Whether the result refuses a part (then it holds no text), else holds it to the part."""
    if theirs["refusal"] is not None:
        if theirs["paragraphs"]:
            raise CertificationError(f"{where}: refused, yet with paragraphs")
        return True
    if part.error is not None:
        raise CertificationError(f"{where}: {part.error}")
    _paragraphs(part, theirs["paragraphs"], f"{where} paragraph")
    return False


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
