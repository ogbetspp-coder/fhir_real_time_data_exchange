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
                        + floating objects + unread objects' text

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
(layout), a picture or shape anchored to its paragraph (it floats apart from the text, and
Word's text shows none; one in line is U+FFFC), and a floating object holding text whose text
nothing elsewhere counts or refers to (``_unread_kind``): set aside whole, its text in every
branch of alternate content counted (``unreadObjects``, ``unreadObjectCharacters``). Each
floating object must be placed where the check finds its anchor, of the kind it finds
(``anchored``, never read). The walk reads a closed list of elements and refuses any other, so
no text is passed over unseen.

Its scope, and what it lists as not read, is stated in ``docs/conservation.md`` ("Scope").

For an ePI the same statement holds with HTML's tokens (parsed here by the standard library's
HTML parser, not the reader's XML parser): every character of text, in order, with each run of
collapsible whitespace drawn as at most one space (as CSS lays it out), ``br`` as U+000A and
``img`` as U+FFFC.

A document with tracked changes is read as two views, every change accepted and every one
rejected, each a package of its own that the reader reads and this check certifies as above.
The views themselves are held to the source by ``certify_tracked``, again with its own walk: in each
part with revisions, every run's content is kept or dropped by the change around it, in order, in
the same table cell, and a paragraph is joined to the next exactly where the view drops its mark
(past a table it drops whole, an empty paragraph only, to the paragraph after it; a bookmark's end
between the two after what is joined); a row the view drops goes, and a table whose every row it
drops; a field's marks are all kept or all dropped, and a field the view drops goes with every run
inside it (``_field_runs``, its own reading of Word's rule); no revision is left in any part, and
every other part is the source's, byte for byte. In a part with revisions, everything outside the
changes is the source's too, element by element (``_canon``). The former formatting the original
view takes from a change is held to Word (``corpus/tracked-cases``).

Beyond the text, the check works out on its own, by Word's rules written apart from the
reader's, the key marks (``CHECKED_MARKS``), every list label and every note mark; the result's
must be the check's. So must each body table's grid (``_grid``): its columns, each row's cells
laid on them by ``gridBefore`` and ``gridSpan``, filling them exactly with ``gridAfter``, and each
cell's vertical merge as stored; or, where Word's is not on record, no grid and the same reason
for it. So must what each U+FFFC stands for (``DocxSource._picture``): the image part through
the story part's own relationships, its bytes' SHA-256, PNG or JPEG by signature, its pixels from
its chunks or segments, the drawing's extent and crop, and the first reason (``_NOT_AS_IS``) its
bytes may not be what Word draws, by a closed list of what a drawing may hold. An ePI's other
marks are
held to Chrome (``tests/test_browser_oracle.py``); a .docx's (highlight, shading, faint, raised
text, right-to-left) to nothing but the reader's tests. A table style's conditional parts it
applies on its own (``_Applied``), as Word's answers have them (corpus/numbering-cases,
table-style-*). Where Word's key marks are not on record the check refuses on its own, as the
reader does: a table style's part over text where Word was not asked how it applies, or setting
a checked mark other than bold, italic, capitals and strike, or a font that changes what the
text is drawn in;
complex script (right-to-left, ``cs``, ``bdo``/``dir``, or Hebrew, Arabic, Indic... text) whose
``b`` and ``bCs``, or ``i`` and ``iCs``, differ; text hidden by some level and shown by Word's
toggle rule; and anything in a cell merged into the one above. It shares no code with the
reader, only two tables: the Symbol table (``SYMBOL_FONT``, 55 codes) and the Wingdings bullets
(``WINGDINGS_BULLETS``). See ``docs/conservation.md`` ("Why it is independent") for what that
leaves unchecked.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import json
import posixpath
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from label_docx.reader import SYMBOL_FONT, WINGDINGS_BULLETS

CHECKER_VERSION = "conservation-check/1.19.0"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OBJECT = "\ufffc"
_CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"
# A run's elements that stand for one character, for the size of what is not read.
_STANDS = {
    *("sym", "tab", "ptab", "br", "cr", "noBreakHyphen", "softHyphen"),
    *("drawing", "pict", "AlternateContent"),
}
_LAYOUT_CODES = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}
_PAGE_FORMATS = {"MERGEFORMAT", "CHARFORMAT", "ARABIC"}
_VARIABLE_FORMATS = {"MERGEFORMAT", "CHARFORMAT"}
_NOTE_LAYOUT = {"separator", "continuationSeparator", "continuationNotice"}
# Run children that hold no text and stand for none.
_RUN_SILENT = {"rPr", "lastRenderedPageBreak"}
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_POSITION = re.compile(r"(?:^|;)\s*position\s*:\s*([^;]*)", re.IGNORECASE)
_NOTE_MARKS = {"footnoteReference", "endnoteReference", "footnoteRef", "endnoteRef"}
_HEX = re.compile(r"[0-9A-Fa-f]{1,4}")
# The walk reads a closed list of elements and refuses any other: what it does not know may hold
# text it would pass over. Containers it reads through, around blocks or rows (content controls,
# custom XML), and around runs (also hyperlinks and smart tags; bidirectional embeddings are read
# through on their own, as complex script).
_CONTAINERS = {f"{{{W}}}{name}" for name in ("sdt", "sdtContent", "customXml")}
_RUN_CONTAINERS = _CONTAINERS | {f"{{{W}}}{name}" for name in ("hyperlink", "smartTag")}
# Elements it passes over, which must hold no text: properties, and markers of a place.
_INERT = {
    f"{{{W}}}{name}"
    for name in (
        "pPr",
        "sectPr",
        "tblPr",
        "tblGrid",
        "tblPrEx",
        "trPr",
        "tcPr",
        "sdtPr",
        "sdtEndPr",
        "customXmlPr",
        "smartTagPr",
        "fldData",
        "bookmarkStart",
        "bookmarkEnd",
        "proofErr",
        "permStart",
        "permEnd",
        "commentRangeStart",
        "commentRangeEnd",
    )
}
# Characters of a text element Word does not show as themselves: format characters (zero-width
# and bidirectional controls, which reorder what is around them), controls (a tab or a line
# break stored as text: Word writes those as elements), unassigned code points, and the rest of
# Unicode's Default_Ignorable_Code_Point (16.0), which are none of these and are drawn as nothing.
_UNSHOWN = {"Cf", "Cc", "Cn"}
_IGNORED = frozenset(
    chr(code)
    for low, high in (
        (0x034F, 0x034F),
        (0x115F, 0x1160),
        (0x17B4, 0x17B5),
        (0x180B, 0x180F),
        (0x3164, 0x3164),
        (0xFE00, 0xFE0F),
        (0xFFA0, 0xFFA0),
        (0xE0100, 0xE01EF),
    )
    for code in range(low, high + 1)
)
# Drawings it reads as one character: a picture, or a shape (whose text it refuses).
_DRAWN = {
    "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}

type Json = Any


class CertificationError(Exception):
    """The result does not carry the source's text over exactly; the detail says where."""


def _w(name: str) -> str:
    return f"{{{W}}}{name}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# A table as the walk meets it: its element, the cell it stands in, its rows, each row's cells,
# and whether each row's height may be exact.
type _Met = tuple[ET.Element, Any, list[ET.Element], list[list[ET.Element]], list[bool]]


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
    floating: int = 0
    # Floating objects holding text, set aside whole (``_unread_kind``), and the characters of
    # their text elements and run elements, in every branch.
    objects: int = 0
    unread: int = 0
    symbol: int = 0
    output: int = 0

    def balanced(self) -> bool:
        source = self.text + self.instruction + self.elements
        kept = self.output + self.field_code + self.page_numbers + self.hidden + self.page_breaks
        return source == kept + self.floating + self.unread


# --- .docx: the source's tokens -----------------------------------------------------------


@dataclass
class _Segment:
    """The text one run shows (as the check reads it), or a zero-width marker."""

    text: str = ""
    marker: tuple[str, Any] | None = None
    # The marks the check works out for the run's text (``CHECKED_MARKS``).
    kinds: frozenset[str] = frozenset()
    # A note mark that is custom (customMarkFollows): Word draws no number for it.
    custom: bool = False


@dataclass
class _Paragraph:
    # Whether a row of exact height holds it.
    in_exact_row: bool
    segments: list[_Segment] = field(default_factory=list)
    table: tuple[int, int, int] | None = None
    # What the list and note numbering need: the paragraph's properties, its style, its
    # table's style, and its section (counted from 0).
    properties: ET.Element | None = None
    style: str | None = None
    table_style: str | None = None
    # The run properties of the table style's parts Word applies to its cell, nearest first.
    conditional: tuple[ET.Element, ...] = ()
    section: int = 0
    # What the result must say of the paragraph beyond its text: whether Word hides its mark,
    # and its list label.
    mark_hidden: bool = False
    numbering: dict[str, Any] | None = None
    # Each drawing read as U+FFFC, in order, with the reasons its run gives (a field's result, a
    # border), and what each stands for (``DocxSource._picture``).
    drawings: list[tuple[ET.Element, set[str]]] = field(default_factory=list)
    pictures: list[dict[str, Json]] = field(default_factory=list)


# The marks the check works out itself and holds every result to. The others (highlight,
# shading, faint, raised text, right-to-left) are held by nothing but the reader's own tests:
# the Word oracle reads bold, italic, capitals and strike only (tests/test_word_oracle.py).
_CHECKED_TOGGLES = {
    "b": "bold",
    "i": "italic",
    "caps": "caps",
    "smallCaps": "smallCaps",
    "strike": "strike",
    "dstrike": "dstrike",
}
CHECKED_MARKS = frozenset({*_CHECKED_TOGGLES.values(), "superscript", "subscript", "underline"})
# Every mark kind a .docx result may name (``reader.Mark``): the checked ones, the others (held
# only by the reader's tests; Word is not asked about them), and highlight and shading, named
# after their colour.
_MARK_KINDS = CHECKED_MARKS | {"position", "rtl", "faint"}
_MARK_PATTERN = re.compile(r"(?:highlight|shading)-.+", re.DOTALL)

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
    paragraph or table style id that is absent or unknown means the default style of its kind
    (the last marked default), and a character one means none (Word gives text no default
    character style). A font slot (``ascii``, ``hAnsi``...) is set by the first level that names
    it, a theme reference there naming the theme's typeface for its script. A run is hidden when it
    says so itself, or, saying nothing, when any level says so (the reading the reader states;
    Word's prints are held to it by ``tests/test_word_oracle.py``).
    """

    def __init__(
        self, styles: ET.Element | None, theme: ET.Element | None, font_table: ET.Element | None
    ) -> None:
        self.kind: dict[str, str] = {}
        self.based: dict[str, str | None] = {}
        self.rpr: dict[str, ET.Element | None] = {}
        self.ppr: dict[str, ET.Element | None] = {}
        self.doc_ppr: ET.Element | None = None
        self.defaults: dict[str, str] = {}
        self.doc_rpr: ET.Element | None = None
        # ``chain``'s answers, worked out once each.
        self.chain_memo: dict[tuple[str | None, str], list[ET.Element]] = {}
        # Per table style, its conditional parts (tblStylePr), and its band sizes (rows, columns).
        self.parts: dict[str, list[ET.Element]] = {}
        self.bands: dict[str, tuple[str | None, str | None]] = {}
        # Table styles whose row properties (their own, or a part's) make a row's height exact.
        self.fixed_rows: set[str] = set()
        if styles is not None:
            for style in styles.findall(_w("style")):
                style_id = style.get(_w("styleId"))
                if style_id is None:
                    continue
                if style_id in self.kind:
                    # Which definition Word takes is not on record.
                    raise CertificationError(f"style {style_id} is defined twice")
                kind = style.get(_w("type"), "paragraph")
                based = style.find(_w("basedOn"))
                self.kind[style_id] = kind
                self.based[style_id] = None if based is None else based.get(_w("val"))
                self.rpr[style_id] = style.find(_w("rPr"))
                self.ppr[style_id] = style.find(_w("pPr"))
                self.parts[style_id] = style.findall(_w("tblStylePr"))
                self.bands[style_id] = (_size_of(style, "Row"), _size_of(style, "Col"))
                if any(
                    _row_fixed(holder.find(_w("trPr"))) for holder in (style, *self.parts[style_id])
                ):
                    self.fixed_rows.add(style_id)
                if style.get(_w("default")) in ("1", "true", "on"):
                    self.defaults[kind] = style_id
            self.doc_rpr = styles.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
            self.doc_ppr = styles.find(f"{_w('docDefaults')}/{_w('pPrDefault')}/{_w('pPr')}")
        # The fonts the font table says are symbol-encoded (charset 02), by lower-case name.
        self.encoded: set[str] = set()
        for font in [] if font_table is None else font_table.findall(_w("font")):
            charset = font.find(_w("charset"))
            if charset is not None and charset.get(_w("val"), "").upper() == "02":
                self.encoded.add(font.get(_w("name"), "").lower().replace(" ", ""))
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

    def style_ids(self, style_id: str | None, kind: str) -> list[str]:
        """``style_id`` (or its kind's default, never for text) and its basedOn chain."""
        if style_id is None or style_id not in self.kind:
            # No style named, or none there: the default one of the kind, except for text,
            # which Word gives no default character style.
            style_id = None if kind == "character" else self.defaults.get(kind)
        out: list[str] = []
        seen: set[str] = set()
        while style_id is not None and style_id in self.kind and style_id not in seen:
            out.append(style_id)
            seen.add(style_id)
            base = self.based[style_id]
            if base in self.kind and self.kind[base] != self.kind[style_id]:
                # Word takes nothing from a character style a paragraph style is based on;
                # any other pair is not on record.
                if (self.kind[style_id], self.kind[base]) != ("paragraph", "character"):
                    raise CertificationError(f"style {style_id} is based on another kind")
                break
            style_id = base
        return out

    def chain(self, style_id: str | None, kind: str) -> list[ET.Element]:
        """The run properties of each style in ``style_ids``, nearest first (never changed)."""
        key = (style_id, kind)
        if key not in self.chain_memo:
            self.chain_memo[key] = [
                rpr for i in self.style_ids(style_id, kind) if (rpr := self.rpr[i]) is not None
            ]
        return self.chain_memo[key]

    def chains(
        self,
        own: ET.Element | None,
        paragraph_style: str | None,
        table_style: str | None,
        in_table: bool,
        conditional: tuple[ET.Element, ...] = (),
    ) -> list[list[ET.Element]]:
        """The run properties of each kind of style over ``own``: character, paragraph, table.

        ``conditional``, the table style's parts Word applies to the cell, stand first in the
        table's (over the style's own, as Word's answer table-style-base-and-row has it).
        """
        style = None if own is None else own.find(_w("rStyle"))
        return [
            self.chain(None if style is None else style.get(_w("val")), "character"),
            self.chain(paragraph_style, "paragraph"),
            [*conditional, *self.chain(table_style, "table")] if in_table else [],
        ]

    def levels(
        self,
        own: ET.Element | None,
        paragraph_style: str | None,
        table_style: str | None,
        in_table: bool,
        conditional: tuple[ET.Element, ...] = (),
    ) -> list[ET.Element]:
        """Every level of run properties over ``own``, nearest first, the defaults last."""
        chains = self.chains(own, paragraph_style, table_style, in_table, conditional)
        found = [own, *(rpr for chain in chains for rpr in chain), self.doc_rpr]
        return [level for level in found if level is not None]

    def shown(self, own: ET.Element | None, chains: list[list[ET.Element]], name: str) -> bool:
        """Whether Word shows the toggle ``name`` (``b``, ``caps``, ``vanish``...), by its rule.

        The run's own setting wins, on or off; else it starts as the document defaults set it,
        and each kind of style (character, paragraph, table, each with its ``basedOn`` chain)
        whose nearest setting differs from that turns it over. Word's answer to cases in
        ``corpus/numbering-cases``.
        """
        direct = None if own is None else _on(own.find(_w(name)))
        if direct is not None:
            return direct
        default = self.doc_rpr is not None and bool(_on(self.doc_rpr.find(_w(name))))
        shown = default
        for chain in chains:
            settings = (_on(rpr.find(_w(name))) for rpr in chain)
            setting = next((v for v in settings if v is not None), None)
            # Each kind that differs from the document defaults turns it over.
            if setting is not None and setting != default:
                shown = not shown
        return shown

    def marks(
        self,
        own: ET.Element | None,
        paragraph_style: str | None,
        table_style: str | None,
        in_table: bool,
        conditional: tuple[ET.Element, ...] = (),
    ) -> frozenset[str]:
        """The marks of ``CHECKED_MARKS`` Word shows on the run, by Word's rules, worked out here.

        Toggles (bold, italic, capitals, small capitals, strike, double strike) by ``shown``.
        Superscript, subscript and underline: the nearest level that sets a value, the run
        first. Each rule is Word's answer to a case in ``corpus/numbering-cases``; that a ``w:u``
        without a value sets none (no underline, the next level's shown through) is Word
        16.113.3's for Mac, asked 2026-10-05.
        """
        chains = self.chains(own, paragraph_style, table_style, in_table, conditional)
        kinds = {kind for name, kind in _CHECKED_TOGGLES.items() if self.shown(own, chains, name)}
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

    def family(self, name: str | None) -> str:
        """``symbol``, ``wingdings`` (exactly so named), ``dingbat`` or ``text``.

        A dingbat font draws other characters than it stores: Wingdings by any other spelling,
        Webdings, Zapf Dingbats, Marlett, MT Extra, and any font the font table says is
        symbol-encoded (charset 02) other than Symbol.
        """
        if _is_symbol(name):
            return "symbol"
        if name == "Wingdings":
            return "wingdings"
        key = "" if name is None else name.lower().replace(" ", "")
        dingbats = ("dingbat", "wingding", "webding", "marlett", "mtextra")
        if any(part in key for part in dingbats) or key in self.encoded:
            return "dingbat"
        return "text"

    def drawn(self, levels: list[ET.Element]) -> str:
        """The family a run's or label's characters are drawn in, where it is certain.

        Word picks a font per character from the four slots, so a symbol font counts only where
        it is in both Latin slots (``ascii``, ``hAnsi``) and nothing sends a character to
        another slot: complex script, right to left, or a font hint other than ``default``.
        Symbol in the East Asian slot alone, with none of those, is ``east-asian-symbol``: the
        text Word draws there (``_EAST_ASIAN``) is never certified, and the rest is as stored.
        """
        families = {slot: self.family(self.font(levels, slot)) for slot in _THEME_SLOT}
        named = set(families.values()) - {"text"}
        if "dingbat" in named or len(named) > 1:
            raise CertificationError("characters in a dingbat or symbol-encoded font")
        if not named:
            return "text"
        family = min(named)  # the one family named
        hints = [level.find(_w("rFonts")) for level in levels]
        if (
            family == "symbol"
            and [slot for slot, kind in families.items() if kind == "symbol"] == ["eastAsia"]
            and not any(_on(level.find(_w(name))) for level in levels for name in ("cs", "rtl"))
            and all(h is None or h.get(_w("hint"), "default") == "default" for h in hints)
        ):
            return "east-asian-symbol"
        if (
            families["ascii"] != family
            or families["hAnsi"] != family
            or any(_on(level.find(_w(name))) for level in levels for name in ("cs", "rtl"))
            or any(h is not None and h.get(_w("hint"), "default") != "default" for h in hints)
        ):
            raise CertificationError(f"the {family} font for only some characters")
        return family

    @staticmethod
    def hidden(levels: list[ET.Element], own: ET.Element | None, name: str = "vanish") -> bool:
        """The cautious reading of hiding: the run's own setting, else any level that sets it."""
        direct = None if own is None else _on(own.find(_w(name)))
        if direct is not None:
            return direct
        return any(_on(level.find(_w(name))) for level in levels)


# What Word may draw in the East Asian font when no hint says otherwise: Hangul Jamo, and all
# from CJK Radicals Supplement on, every plane above too (ECMA-376 Part 1, 17.3.2.26, broadly).
_EAST_ASIAN = ((0x1100, 0x11FF), (0x2E80, 0x10FFFF))


def _drawn_complex(character: str) -> bool:
    """Whether Word draws the character as complex script whatever its run says."""
    code = ord(character)
    return (
        0x0590 <= code <= 0x0DFF
        or 0x0E00 <= code <= 0x109F
        or 0x1780 <= code <= 0x17FF
        or 0xFB1D <= code <= 0xFDFF
        or 0xFE70 <= code <= 0xFEFF
    )


def _is_symbol(name: str | None) -> bool:
    """Whether a font is Symbol, by its exact name; another spelling of it is not on record."""
    # Segoe UI Symbol is a Unicode font, whose characters are drawn as stored.
    unicode_font = name == "Segoe UI Symbol"
    if (
        name is not None
        and name != "Symbol"
        and not unicode_font
        and "symbol" in name.lower().replace(" ", "")
    ):
        raise CertificationError(f"the font {name!r}")
    return name == "Symbol"


def _symbol_reading(text: str) -> str:
    out: list[str] = []
    for character in text:
        code = ord(character)
        low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
        if low not in SYMBOL_FONT:
            raise CertificationError(f"a Symbol character outside the table: {code:#06x}")
        out.append(SYMBOL_FONT[low])
    return "".join(out)


def _in_line(drawing: ET.Element) -> bool:
    """Whether a picture or shape stands in the text, or floats apart from it (anchored).

    DrawingML says so by its frame (``wp:inline`` or ``wp:anchor``), VML by the image's style
    (``position:absolute`` floats); alternate content by the drawing of its one ``wps`` choice,
    which Word draws.
    """
    if _local(drawing.tag) == "AlternateContent":
        choices = [c for c in drawing if _local(c.tag) == "Choice"]
        if len(choices) != 1 or choices[0].get("Requires") != "wps":
            raise CertificationError("alternate content without one wps choice")
        drawn = list(choices[0])
        if [d.tag for d in drawn] != [_w("drawing")]:
            raise CertificationError("alternate content whose choice is not one drawing")
        drawing = drawn[0]
    if drawing.tag == _w("drawing"):
        frames = [c.tag for c in drawing]
        if frames not in ([f"{{{_WP}}}inline"], [f"{{{_WP}}}anchor"]):
            raise CertificationError("a drawing neither in line nor anchored")
        return frames == [f"{{{_WP}}}inline"]
    styles = [
        n.get("style", "") for n in drawing.iter() if any(_local(c.tag) == "imagedata" for c in n)
    ]
    if len(styles) != 1:
        raise CertificationError("a VML picture of other than one image")
    position = _POSITION.search(styles[0])
    if position is None:
        return True
    if position.group(1).strip().lower() != "absolute":
        raise CertificationError(f"a VML picture positioned {position.group(1)!r}")
    return False


_VML = "urn:schemas-microsoft-com:vml"
_PICTURE = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_SHAPE = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
# A floating object holding text, by the graphic its anchor draws: a text box, or shapes.
_HOLDING = {
    _SHAPE: "text-box",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup": "shapes",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas": "shapes",
}
# What in such an object Word refers to or counts elsewhere (and any revision, ``_REVISIONS``).
_REFERRED = frozenset(
    {
        *("fldChar", "instrText", "fldSimple", "bookmarkStart", "bookmarkEnd", "numPr", "sectPr"),
        *("commentRangeStart", "commentRangeEnd", "commentReference", "annotationRef"),
        *_NOTE_MARKS,
    }
)


def _unread_kind(drawing: ET.Element, fonts: _Fonts) -> str | None:
    """The kind of a floating object whose text the walk sets aside unread, else None.

    One that holds a text box (``w:txbxContent``) and floats: a DrawingML drawing whose one frame
    is ``wp:anchor``, alone or the one ``wps``, ``wpg`` or ``wpc`` choice of alternate content
    (Word draws the choice), of a shape (``text-box``), a group or a canvas (``shapes``); or VML
    whose one shape or group (a ``shapetype`` aside) is positioned absolutely. And holding nothing
    Word refers to or counts elsewhere: no field, note mark, bookmark, comment, section or
    revision, no list item (``numPr``, or a style, a table style or the defaults that set one),
    no content control Word shows from elsewhere, no WordArt or embedded object, no graphic but a
    picture, a shape, a group or a canvas, and none of Word's own elements outside the text boxes
    but the drawing and its fallback. Anything else is a token as before (``token``).
    """
    texts = [n for n in drawing.iter() if n.tag == _w("txbxContent")]
    if not texts:
        return None
    shown = drawing
    if _local(drawing.tag) == "AlternateContent":
        choices = [c for c in drawing if _local(c.tag) == "Choice"]
        if len(choices) != 1 or choices[0].get("Requires") not in ("wps", "wpg", "wpc"):
            return None
        if [c.tag for c in choices[0]] != [_w("drawing")]:
            return None
        shown = choices[0][0]
    kind: str | None = None
    if shown.tag == _w("drawing"):
        if [c.tag for c in shown] != [f"{{{_WP}}}anchor"]:
            return None
        top = next((g for g in shown[0].iter() if _local(g.tag) == "graphicData"), None)
        kind = None if top is None else _HOLDING.get(top.get("uri", ""))
    elif shown.tag == _w("pict"):
        drawn = [c for c in shown if c.tag != f"{{{_VML}}}shapetype"]
        if len(drawn) == 1 and drawn[0].tag.startswith(f"{{{_VML}}}"):
            position = _POSITION.search(drawn[0].get("style", ""))
            if position is not None and position.group(1).strip().lower() == "absolute":
                kind = "shapes" if _local(drawn[0].tag) == "group" else "text-box"
    if kind is None:
        return None
    within = {id(n) for text in texts for n in text.iter()}
    try:
        for node in drawing.iter():
            name = _local(node.tag)
            ours = node.tag.startswith(f"{{{W}}}")
            if node is drawing:
                continue
            if (
                (ours and id(node) not in within and node.tag not in (_w("drawing"), _w("pict")))
                or (ours and name in _REFERRED)
                or node.tag in _REVISIONS
                or name in ("textpath", "OLEObject")
                or (name == "graphicData" and node.get("uri") not in (_PICTURE, *_HOLDING))
            ):
                return None
            if node.tag == _w("sdt"):
                _control(node)
        styled = [t.find(f"{_w('tblPr')}/{_w('tblStyle')}") for t in drawing.iter(_w("tbl"))]
        tables = [
            i
            for s in styled
            for i in fonts.style_ids(None if s is None else s.get(_w("val")), "table")
        ]
        for paragraph in drawing.iter(_w("p")):
            named = paragraph.find(f"{_w('pPr')}/{_w('pStyle')}")
            ids = fonts.style_ids(None if named is None else named.get(_w("val")), "paragraph")
            sources = [*(fonts.ppr.get(i) for i in [*ids, *tables]), fonts.doc_ppr]
            if any(
                n.find(_w("numId")) is not None or n.find(_w("ilvl")) is not None
                for source in sources
                if source is not None
                for n in source.findall(_w("numPr"))
            ):
                return None
    except CertificationError:
        return None
    return kind


def _floating_kind(drawing: ET.Element) -> str:
    """What a floating object without text is: ``shape`` (a ``wps`` graphic) or ``picture``."""
    uris = {n.get("uri") for n in drawing.iter() if _local(n.tag) == "graphicData"}
    return "shape" if _SHAPE in uris else "picture"


def _bullet_reading(code: int) -> str:
    for low, reading in WINGDINGS_BULLETS.items():
        if code in (low, 0xF000 + low):
            return reading
    raise CertificationError(f"a Wingdings label outside the table: {code:#06x}")


class _Story:
    """The tokens of one story (the body, a note, a header, a footer or a comment), by paragraph."""

    def __init__(
        self,
        fonts: _Fonts,
        ledger: _Ledger,
        story: tuple[str, int] | None = None,
        variables: list[tuple[str | None, str | None]] | None = None,
    ) -> None:
        self.fonts = fonts
        self.ledger = ledger
        self.story = story
        self.paragraphs: list[_Paragraph] = []
        # Open complex fields: [in its instruction, its instruction so far, a page number, a
        # field in its instruction].
        self.fields: list[list[Any]] = []
        # The settings' document variables (name, value), and the DOCVARIABLE result being read:
        # the value it must show, the text it shows so far, and whether a complex field's end
        # closes it (a simple field's result holds no field character).
        self.variables = variables or []
        self.variable: tuple[str, list[str], bool] | None = None
        self.layout = 0
        self.tables = 0
        # Every table met, in document order: its element, the cell it stands in, its rows and
        # each row's cells (``_grid`` reads the body's from them).
        self.met: list[_Met] = []
        self.section = 0
        # How deep in bdo and dir elements the walk is, and whether the current run's two
        # emphasis settings (b and bCs, i and iCs) differ where it is drawn as complex script.
        self.bidi = 0
        self.unsure: tuple[bool, bool] = (False, False)
        # Whether the run read has Symbol in its East Asian slot alone (``_Fonts.drawn``).
        self.east_asian_symbol = False
        # The table style's parts Word applies to the cell being read (``_Applied``).
        self.conditional: tuple[ET.Element, ...] = ()
        # How many simple fields, and rows of exact height, hold what is being read.
        self.simple = 0
        self.fixed = 0

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
            elif child.tag in _CONTAINERS:
                self.blocks(child, table, table_style)
            elif not _inert(child):
                raise CertificationError(f"{local} among paragraphs, which the check does not read")

    def table(self, element: ET.Element, outer: tuple[int, int, int] | None) -> None:
        index = self.tables
        self.tables += 1
        style = element.find(f"{_w('tblPr')}/{_w('tblStyle')}")
        own = None if style is None else style.get(_w("val"))
        rows = _owned(element, _w("tr"))
        cells = [_owned(row, _w("tc")) for row in rows]
        styled = not self.fonts.fixed_rows.isdisjoint(self.fonts.style_ids(own, "table"))
        fixed = [styled or _row_fixed(row.find(_w("trPr"))) for row in rows]
        self.met.append((element, outer, rows, cells, fixed))
        applied = _Applied(
            self.fonts,
            self.fonts.style_ids(own, "table"),
            element,
            rows,
            cells,
            self.story is not None or outer is not None,
        )
        for row_index, row_cells in enumerate(cells):
            self.fixed += fixed[row_index]
            for cell_index, cell in enumerate(row_cells):
                before = len(self.paragraphs)
                rprs, problem = applied.at(row_index, cell_index)
                outside, self.conditional = self.conditional, () if problem else rprs
                here = (index, row_index, cell_index)
                # A body paragraph is in its own table's cell; any other, in the outermost one's.
                self.blocks(cell, here if self.story is None else outer or here, own)
                self.conditional = outside
                if problem and any(
                    any(s.text for s in p.segments)
                    or ((n := _numbering_of(self.fonts, p)) is not None and n[0] != 0)
                    for p in self.paragraphs[before:]
                ):
                    # What Word draws there is not on record.
                    raise CertificationError(f"a table style's part over text: {problem}")
                merge = cell.find(f"{_w('tcPr')}/{_w('vMerge')}")
                if merge is None or merge.get(_w("val"), "continue") != "continue":
                    continue
                # Word draws nothing of a cell merged into the one above: a character, a
                # mark or a list label there would be one it does not show.
                for paragraph in self.paragraphs[before:]:
                    numbered = _numbering_of(self.fonts, paragraph)
                    if paragraph.segments or (numbered is not None and numbered[0] != 0):
                        raise CertificationError("content in a merged-away cell")
            self.fixed -= fixed[row_index]

    def paragraph(
        self, element: ET.Element, table: tuple[int, int, int] | None, table_style: str | None
    ) -> None:
        # A floating object's text is set aside whole (``_unread_kind``), its paragraphs too.
        unread = {
            id(node)
            for run in element.iter(_w("r"))
            for drawing in run
            if _local(drawing.tag) in ("drawing", "pict", "AlternateContent")
            and _unread_kind(drawing, self.fonts) is not None
            for node in drawing.iter()
        }
        if any(
            n.tag == _w("p") and id(n) not in unread for n in element.iter() if n is not element
        ):
            raise CertificationError("a paragraph inside a paragraph")
        properties = element.find(_w("pPr"))
        style = None if properties is None else properties.find(_w("pStyle"))
        here = _Paragraph(
            in_exact_row=self.fixed > 0,
            table=table,
            properties=properties,
            style=None if style is None else style.get(_w("val")),
            table_style=table_style,
            conditional=self.conditional,
            section=self.section,
        )
        if properties is not None and properties.find(_w("sectPr")) is not None:
            self.section += 1
        # The paragraph mark: hidden (vanish, or specVanish) runs the paragraph on into the next.
        # The cautious reading and Word's toggle rule must agree, else it is not certain.
        own = None if properties is None else properties.find(_w("rPr"))
        in_table = table is not None
        levels = self.fonts.levels(own, here.style, table_style, in_table, self.conditional)
        chains = self.fonts.chains(own, here.style, table_style, in_table, self.conditional)
        hiding = ("vanish", "specVanish")
        here.mark_hidden = any(self.fonts.hidden(levels, own, name) for name in hiding)
        if here.mark_hidden != any(self.fonts.shown(own, chains, name) for name in hiding):
            raise CertificationError("a paragraph mark hidden by one reading and not by another")
        self.current = here
        self.inline(element)
        if self.in_instruction() or self.layout:
            # What follows would be code, or a page number, as the walk reads it: Word's reading
            # of a field's code or page number across a paragraph mark is not on record.
            raise CertificationError("a paragraph ends inside a field's code or page number")
        if self.variable is not None:
            raise CertificationError("a paragraph ends inside a DOCVARIABLE's result")
        self.paragraphs.append(here)

    def inline(self, element: ET.Element) -> None:
        for child in element:
            local = _local(child.tag)
            if child.tag == _w("r"):
                self.run(child)
            elif child.tag == _w("fldSimple"):
                if self.variable is not None:
                    raise CertificationError("a field in a DOCVARIABLE's result")
                self.simple += 1
                instruction = child.get(_w("instr"), "")
                value = None if self.in_instruction() else _docvariable(instruction, self.variables)
                if not self.in_instruction() and _page_field(instruction) and not self.layout:
                    self.mark("page", None)
                    self.layout += 1
                    self.inline(child)
                    self.layout -= 1
                elif value is not None:
                    self.variable = (value, [], False)
                    self.inline(child)
                    self.close_variable()
                else:
                    self.inline(child)
                self.simple -= 1
            elif child.tag in (_w("bdo"), _w("dir")):
                # Text in a bidirectional embedding or override is drawn as complex script.
                self.bidi += 1
                self.inline(child)
                self.bidi -= 1
            elif child.tag in _RUN_CONTAINERS:
                self.inline(child)
            elif not _inert(child):
                raise CertificationError(f"{local} in a paragraph, which the check does not read")

    def in_instruction(self) -> bool:
        return any(entry[0] for entry in self.fields)

    def mark(self, kind: str, value: Any, custom: bool = False) -> None:
        self.current.segments.append(_Segment(marker=(kind, value), custom=custom))

    def run(self, run: ET.Element) -> None:
        here = self.current
        in_table = here.table is not None
        own = run.find(_w("rPr"))
        levels = self.fonts.levels(own, here.style, here.table_style, in_table, here.conditional)
        family = self.fonts.drawn(levels)
        if here.conditional and family != self.fonts.drawn(
            self.fonts.levels(own, here.style, here.table_style, in_table)
        ):
            # Fonts from the table style's parts: what Word draws is not on record.
            raise CertificationError("a font set by a table style's part")
        if family == "wingdings":
            raise CertificationError("text in the Wingdings font, which the check does not read")
        if self.bidi and family != "text":
            # In a right-to-left container Word may draw the characters in another font.
            raise CertificationError(f"text in {family} drawn as complex script")
        symbol = family == "symbol"
        self.east_asian_symbol = family == "east-asian-symbol"
        hidden: bool | None = self.fonts.hidden(levels, own)
        chains = self.fonts.chains(own, here.style, here.table_style, in_table, here.conditional)
        if hidden and not self.fonts.shown(own, chains, "vanish"):
            hidden = None  # hidden by any level, shown by Word's toggle rule: not on record
        kinds = self.fonts.marks(own, here.style, here.table_style, in_table, here.conditional)
        # Word draws complex script with bCs and iCs, and b and i are what this check reads.
        forced = bool(self.bidi) or any(
            _on(level.find(_w(name))) for level in levels for name in ("rtl", "cs")
        )
        differ = any(
            self.fonts.shown(own, chains, name) != self.fonts.shown(own, chains, name + "Cs")
            for name in ("b", "i")
        )
        self.unsure = (forced, differ)
        shown: list[str] = []
        for child in run:
            local = _local(child.tag)
            if local in _RUN_SILENT:
                continue
            if self.variable is not None and not (
                (local == "t" and not symbol) or (local == "fldChar" and self.variable[2])
            ):
                # Updated, Word shows the variable's value in place of the whole result.
                raise CertificationError("a DOCVARIABLE's result holding other than text")
            if local == "annotationRef":
                # A comment's echo of its own mark, at the start of its text: no character.
                if self.story is None or self.story[0] != "comment":
                    raise CertificationError("annotationRef outside a comment")
                continue
            marked = local == "commentReference" or local in _NOTE_MARKS
            if marked and (hidden is not False or self.in_instruction()):
                # Word draws no mark there, or what it draws is not on record.
                raise CertificationError(f"a {local} hidden or in a field's code")
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
                    custom = child.get(_w("customMarkFollows")) in ("1", "true", "on")
                    self.mark("note", (kind, int(child.get(_w("id"), ""))), custom)
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
            if (
                local in ("drawing", "pict", "AlternateContent")
                and not (self.fields or self.simple or self.layout)
                and (boxed := _unread_kind(child, self.fonts)) is not None
            ):
                # Set aside whole: one token, and its text, every branch's, in the source.
                characters = sum(len(n.text or "") for n in child.iter(_w("t")))
                elements = sum(
                    1 for r in child.iter(_w("r")) for c in r if _local(c.tag) in _STANDS
                )
                self.ledger.text += characters
                self.ledger.elements += 1 + elements
                self.ledger.floating += 1
                self.ledger.objects += 1
                self.ledger.unread += characters + elements
                self.flush_run(shown, hidden, kinds)
                shown = []
                self.mark("anchored", boxed)
                continue
            token = self.token(child, local, symbol)
            # One token for each character of a text element, one for any other element.
            length = len(child.text or "") if local == "t" else 1
            if self.in_instruction():
                self.ledger.field_code += length
                if self.fields[-1][0]:
                    self.fields[-1][1].append(token)
            elif self.layout:
                if length and hidden is not False:
                    # Word shows no page number there, or one not on record.
                    raise CertificationError("a hidden page number")
                self.ledger.page_numbers += length
            elif not token and local == "br":
                self.ledger.page_breaks += 1
            elif not token and local in ("drawing", "pict", "AlternateContent"):
                self.ledger.floating += 1
                self.flush_run(shown, hidden, kinds)
                shown = []
                self.mark("anchored", _floating_kind(child))
            else:
                shown.append(token)
                if local in ("drawing", "pict", "AlternateContent"):
                    # A field's result Word prints again (it may hold another picture then),
                    # and a border on the run, nearest level first.
                    around = {"field"} if self.fields or self.simple else set()
                    edges = [e for level in levels if (e := level.find(_w("bdr"))) is not None]
                    edge = edges[0] if edges else None
                    if edge is not None and edge.get(_w("val")) not in ("none", "nil"):
                        around.add("border")
                    here.drawings.append((child, around))
        self.flush_run(shown, hidden, kinds)

    def token(self, child: ET.Element, local: str, symbol: bool) -> str:
        """What one run child stands for, as the check reads it."""
        if local == "t":
            text = child.text or ""
            if len(child):
                raise CertificationError("an element inside a text element")
            if text != text.strip(" ") and child.get(f"{{{_XML_NS}}}space") != "preserve":
                # Spaces at an edge an XML consumer may drop: what Word shows is not certain.
                raise CertificationError("spaces at the edge of a text element not preserved")
            self.ledger.text += len(text)
            if self.east_asian_symbol and any(
                low <= ord(c) <= high for c in text for low, high in _EAST_ASIAN
            ):
                raise CertificationError("East Asian text with Symbol in its slot")
            if symbol:
                mapped = _symbol_reading(text)
                self.ledger.symbol += sum(1 for a, b in zip(text, mapped, strict=True) if a != b)
                return mapped
            # Text Python finds printable holds no character of _UNSHOWN's categories.
            if not _IGNORED.isdisjoint(text) or (
                not text.isprintable() and any(unicodedata.category(c) in _UNSHOWN for c in text)
            ):
                raise CertificationError("a character Word does not show as itself")
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
            char = child.get(_w("char"), "")
            # Read only in the Symbol font, and only by a code of one to four hex digits.
            if child.get(_w("font")) != "Symbol" or not _HEX.fullmatch(char):
                raise CertificationError(
                    f"a w:sym in {child.get(_w('font'))!r} the check does not read"
                )
            code = int(char, 16)
            low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
            if low not in SYMBOL_FONT:
                raise CertificationError(f"a w:sym outside the Symbol table: {code:#06x}")
            self.ledger.symbol += 1
            return SYMBOL_FONT[low]
        if local in ("drawing", "pict", "AlternateContent"):
            # A picture or a drawn shape stands for one character, whichever branch of alternate
            # content Word draws. Text inside one (a text box, WordArt, whose text is an
            # attribute of its textpath) is text this check does not place.
            texts = {"t", "txbx", "txbxContent", "textbox", "textpath", "AlternateContent"}
            inner = [n for n in child.iter() if n is not child]
            if any(_local(n.tag) in texts for n in inner):
                raise CertificationError(f"text inside a {local}")
            if local == "pict" and any(n.tag.startswith(f"{{{W}}}") for n in inner):
                raise CertificationError("run content inside a VML picture")
            if local == "AlternateContent" and any(
                n.tag.startswith(f"{{{W}}}") and _local(n.tag) not in ("drawing", "pict")
                for n in inner
            ):
                raise CertificationError("run content inside alternate content")
            if any(_local(n.tag) == "graphicData" and n.get("uri") not in _DRAWN for n in inner):
                # A chart, a diagram...: text kept elsewhere, which the check does not read.
                raise CertificationError(f"a {local} of a kind the check does not read")
            return _OBJECT if _in_line(child) else ""
        raise CertificationError(f"a run holds {local}, which the check does not know")

    def field(self, child: ET.Element) -> None:
        kind = child.get(_w("fldCharType"))
        if kind == "begin":
            if self.variable is not None:
                raise CertificationError("a field in a DOCVARIABLE's result")
            if self.fields and self.fields[-1][0]:
                self.fields[-1][3] = True
            self.fields.append([True, [], False, False])
        elif kind == "separate" and self.fields and self.fields[-1][0]:
            entry = self.fields[-1]
            nested = any(e[0] for e in self.fields[:-1])
            entry[0] = False
            instruction = "".join(entry[1])
            if not nested and _page_field(instruction) and not self.layout:
                self.mark("page", None)
                self.layout += 1
                entry[2] = True
            elif not nested and (value := _docvariable(instruction, self.variables)) is not None:
                if entry[3]:
                    raise CertificationError("a DOCVARIABLE with a field in its code")
                self.variable = (value, [], True)
        elif kind == "end" and self.fields:
            entry = self.fields.pop()
            if entry[2]:
                self.layout -= 1
            self.close_variable()
        else:
            raise CertificationError(f"a field character {kind!r} out of place")

    def close_variable(self) -> None:
        """End the DOCVARIABLE result being read, if one is: it must show its variable's value."""
        if self.variable is None:
            return
        value, shown, _ = self.variable
        self.variable = None
        if "".join(shown) != value:
            raise CertificationError("a DOCVARIABLE showing other than its value")

    def flush_run(self, shown: list[str], hidden: bool | None, kinds: frozenset[str]) -> None:
        text = "".join(shown)
        if not text:
            return
        if hidden is None:
            raise CertificationError("text whose hiding Word's toggle rule cancels")
        forced, differ = self.unsure
        if differ and (forced or any(_drawn_complex(c) for c in text)):
            raise CertificationError("complex script whose two emphasis settings differ")
        if hidden:
            if text.strip():
                # Hidden text Word does not show: the reader refuses it, never reads it.
                raise CertificationError("hidden text with characters to show")
            self.ledger.hidden += len(text)
            return
        if self.variable is not None:
            self.variable[1].append(text)
        self.current.segments.append(_Segment(text=text, kinds=kinds))


# What a table style's part may set over text that this check reads: bold, italic, capitals and
# strike, which Word answered (corpus/numbering-cases, table-style-*), with b and i's complex
# script twins; or small capitals, double strike, raised text, underline or hiding, which it did
# not, so never certified where the part applies.
_PART_ANSWERED = {"b", "bCs", "i", "iCs", "caps", "strike"}
_PART_UNANSWERED = {"smallCaps", "dstrike", "vertAlign", "u", "vanish", "specVanish"}
# The parts of a table, nearest first, by Word's answers: corner, then row, then column, then
# the vertical band, then the horizontal one. The whole-table part Word never applies.
_PART_ORDER = (
    "nwCell",
    "neCell",
    "swCell",
    "seCell",
    "firstRow",
    "lastRow",
    "firstCol",
    "lastCol",
    "band1Vert",
    "band2Vert",
    "band1Horz",
    "band2Horz",
)


def _size_of(holder: ET.Element, way: str) -> str | None:
    """The band size (``Row`` or ``Col``) a style's or table's ``tblPr`` sets, if it sets one."""
    found = holder.find(f"{_w('tblPr')}/{_w('tblStyle' + way + 'BandSize')}")
    return None if found is None else found.get(_w("val"), "")


def _table_look(table: ET.Element, rows: list[ET.Element]) -> dict[str, bool | None]:
    """Each look of the table, by Word's answers: True, False, or None where not on record.

    The attribute where there is one, else the ``val`` bit, else not on record; with no look,
    the first row on and the rest not on record. Where a row's own look (``tblPrEx``) says
    otherwise than the table's, not on record.
    """
    own = _one_look(table.find(f"{_w('tblPr')}/{_w('tblLook')}"))
    for row in rows:
        other = row.find(f"{_w('tblPrEx')}/{_w('tblLook')}")
        if other is not None:
            said = _one_look(other)
            own = {name: value if said[name] == value else None for name, value in own.items()}
    return own


def _one_look(look: ET.Element | None) -> dict[str, bool | None]:
    """What one table look says of each look (``_table_look``)."""
    names = ("firstRow", "lastRow", "firstColumn", "lastColumn", "noHBand", "noVBand")
    if look is None:
        return {name: True if name == "firstRow" else None for name in names}
    raw = look.get(_w("val"))
    if raw is not None and not re.fullmatch(r"[0-9A-Fa-f]{1,4}", raw):
        raise CertificationError(f"a table look {raw!r}")
    out: dict[str, bool | None] = {}
    for position, name in enumerate(names):
        said = (look.get(_w(name)) or "").lower()
        if not said:
            # val's bits: firstRow is 0x20, and each look the next bit up.
            out[name] = None if raw is None else bool(int(raw, 16) >> (position + 5) & 1)
        else:
            out[name] = {"1": True, "true": True, "on": True, "0": False, "false": False}.get(
                said, False if said == "off" else None
            )
    return out


class _Applied:
    """The table style's parts Word applies to each cell of one table, worked out here.

    Written apart from the reader. Where Word's answer is not on record (``at`` says why), the
    check certifies no text or list label in the cell.
    """

    def __init__(
        self,
        fonts: _Fonts,
        style_ids: list[str],
        table: ET.Element,
        rows: list[ET.Element],
        cells: list[list[ET.Element]],
        elsewhere: bool,
    ) -> None:
        self.cells = cells
        self.elsewhere = elsewhere
        # Each style's parts by type: the table's own style first, then its basedOn chain.
        self.styles = [
            {part.get(_w("type"), ""): part for part in fonts.parts.get(i, [])} for i in style_ids
        ]
        self.twice = any(
            len(fonts.parts.get(i, [])) != len(found)
            for i, found in zip(style_ids, self.styles, strict=True)
        )
        self.sizes = [fonts.bands.get(i, (None, None)) for i in style_ids]
        self.table_sizes = (_size_of(table, "Row"), _size_of(table, "Col"))
        # The parts that set something this check reads, by type, from any style of the chain.
        setting = [
            {
                kind
                for kind, part in found.items()
                if any(
                    _local(c.tag) in _PART_ANSWERED | _PART_UNANSWERED for c in _part_props(part)
                )
            }
            for found in self.styles
        ]
        self.setting: set[str] = set().union(*setting)
        # The parts that set it in a style the table's own is based on.
        self.inherited: set[str] = set().union(*setting[1:])
        self.look = _table_look(table, rows) if self.setting else {}
        self.marked = [_on(r.find(f"{_w('trPr')}/{_w('tblHeader')}")) is True for r in rows]
        # How many header rows stand at the top.
        self.top = 0
        while self.top < len(rows) and self.marked[self.top]:
            self.top += 1
        self.off_grid = [
            r.find(f"{_w('trPr')}/{_w('gridBefore')}") is not None
            or r.find(f"{_w('trPr')}/{_w('gridAfter')}") is not None
            for r in rows
        ]
        self.merged = False
        for row in cells:
            for cell in row:
                props = cell.find(_w("tcPr"))
                span = None if props is None else props.find(_w("gridSpan"))
                if props is not None and (
                    props.find(_w("vMerge")) is not None
                    or props.find(_w("hMerge")) is not None
                    or (span is not None and span.get(_w("val")) != "1")
                ):
                    self.merged = True

    def at(self, row: int, column: int) -> tuple[tuple[ET.Element, ...], str | None]:
        """The run properties of the parts Word applies to a cell, and why that is not on record.

        The run properties nearest first; the reason None when it is on record.
        """
        if not self.setting:
            return (), None
        why: list[str] = ["a part defined twice"] if self.twice else []
        why += [
            f"a part of type {k!r}" for k in sorted(self.setting - {*_PART_ORDER, "wholeTable"})
        ]
        rows, columns = len(self.cells), len(self.cells[row])
        loose = self.off_grid[row] or self.merged
        # Where the cell stands: first row (with the header rows at the top), last row, first
        # column, last column. A row off the grid, or merged cells, may be anywhere.
        edges = {
            "firstRow": row == 0 or row < self.top or self.marked[row] or self.merged,
            "lastRow": row == rows - 1 or self.merged,
            "firstColumn": column == 0 or loose,
            "lastColumn": column == columns - 1 or loose,
        }
        found: set[str] = set()
        for kind, looks in (
            ("nwCell", ("firstRow", "firstColumn")),
            ("neCell", ("firstRow", "lastColumn")),
            ("swCell", ("lastRow", "firstColumn")),
            ("seCell", ("lastRow", "lastColumn")),
            ("firstRow", ("firstRow",)),
            ("lastRow", ("lastRow",)),
            ("firstCol", ("firstColumn",)),
            ("lastCol", ("lastColumn",)),
        ):
            if kind not in self.setting or not all(edges[look] for look in looks):
                continue
            said = [self.look[look] for look in looks]
            if said == [True] * len(looks):
                found.add(kind)
            if len(looks) == 2 and (said != [True, True] or (row and kind[0] == "n")):
                why.append(f"{kind}: a corner Word was not asked about")
            elif None in said:
                why.append(f"{kind} under a look not on record")
        ours, below = self.styles[0], {k for s in self.styles[1:] for k in s}
        for way, index, count, start, end, slot in (
            ("Vert", column, columns, ("firstCol", "firstColumn"), ("lastCol", "lastColumn"), 1),
            ("Horz", row, rows, ("firstRow", "firstRow"), ("lastRow", "lastRow"), 0),
        ):
            if not {f"band1{way}", f"band2{way}"} & self.setting:
                continue
            size, off = self.sizes[0][slot], self.look["noVBand" if slot else "noHBand"]
            if self.table_sizes[slot] is not None or any(
                s[slot] is not None for s in self.sizes[1:]
            ):
                why.append(f"{way} band size not on record")
            elif size is None or off is True:
                pass  # no band size, no bands; or banding off
            elif off is None or not re.fullmatch(r"[1-9][0-9]{0,3}", size):
                why.append(f"{way} banding not on record")
            elif loose:
                why.append(f"{way} banding in a row off the grid or among merged cells")
            elif (start[0] in below and start[0] not in ours) or (
                start[0] in ours and self.look[start[1]] is None
            ):
                why.append(f"{way} banding past a {start[0]} not on record")
            elif start[0] in ours and self.look[start[1]] and way == "Horz" and self.top > 1:
                why.append("banding past several header rows")
            elif (
                index == count - 1
                and self.look[end[1]] is not False
                and (
                    (end[0] in below and end[0] not in ours)
                    or (end[0] in ours and self.look[end[1]] is None)
                )
            ):
                why.append(f"{way} banding over a {end[0]} not on record")
            elif index == count - 1 and end[0] in ours and self.look[end[1]]:
                pass  # Word leaves a last row (column) the style defines, look on, out of bands
            else:
                past = 1 if start[0] in ours and self.look[start[1]] else 0
                if index >= past:
                    found.add(f"band{(index - past) // int(size) % 2 + 1}{way}")
        found &= self.setting
        if {"firstRow", "lastRow"} <= found or {"firstCol", "lastCol"} <= found:
            why.append("first and last over one cell")
        if len(found & {"nwCell", "neCell", "swCell", "seCell"}) > 1:
            why.append("two corners over one cell")
        if found or why:
            for problem, reason in (
                (
                    bool(found & self.inherited) or not found <= ours.keys(),
                    "a part through basedOn",
                ),
                (self.marked[row] and row >= self.top, "a header row below a row that is none"),
                (self.off_grid[row], "a row off the grid"),
                (self.merged, "merged cells"),
                (self.elsewhere, "a nested table, or a note, header, footer or comment"),
                (any(n.tag == _w("tbl") for n in self.cells[row][column].iter()), "a table in it"),
            ):
                if problem:
                    why.append(reason)
        rprs: list[ET.Element] = []
        for kind in _PART_ORDER:
            rpr = ours[kind].find(_w("rPr")) if kind in found and kind in ours else None
            if rpr is None:
                continue
            if any(_local(c.tag) in _PART_UNANSWERED for c in rpr):
                why.append(f"{kind} sets a mark Word was not asked about")
            rprs.append(rpr)
        return tuple(rprs), (why[0] if why else None)


def _part_props(part: ET.Element) -> list[ET.Element]:
    """The run properties a table style's part sets, each its own element."""
    rpr = part.find(_w("rPr"))
    return [] if rpr is None else list(rpr)


def _owned(element: ET.Element, wanted: str) -> list[ET.Element]:
    """``wanted`` descendants of ``element``: a table's rows, or a row's cells.

    Through a container (a content control, custom XML), not into what is found. Anything else
    met on the way that is not inert (a paragraph, a run, a table in a table but outside its
    cells, a row in a row) is refused: its text would otherwise be passed over.
    """
    out: list[ET.Element] = []

    def visit(node: ET.Element) -> None:
        for child in node:
            if child.tag == wanted:
                out.append(child)
            elif child.tag in _CONTAINERS:
                visit(child)
            elif not _inert(child):
                raise CertificationError(f"a {_local(child.tag)} outside the cells of a table")

    visit(element)
    return out


class _NoGridError(Exception):
    """Why a table's grid is not on record, as the result must name it."""


def _grid(
    table: ET.Element,
    parent: Any,
    rows: list[ET.Element],
    cells: list[list[ET.Element]],
    fixed: list[bool],
) -> dict[str, Json]:
    """A body table as this check reads it from the source, by its own rules.

    Its grid: its ``tblGrid``'s ``gridCol`` elements, and each row's cells laid side by side from
    the column after the ``gridBefore`` it leaves out, each over its ``gridSpan`` (one when
    absent), covering the columns exactly with ``gridAfter``. Else no grid, and the first reason,
    in this order: no ``tblGrid`` or two; then row by row a ``gridBefore`` or ``gridAfter`` that
    is not digits; cell by cell a horizontal merge, a vertical merge that is neither ``restart``
    nor ``continue`` (as an empty one is), a ``gridSpan`` that is not digits or is 0; and the row
    not covering the columns.
    """
    try:
        grid: Json = _grid_laid(table, rows, cells, fixed)
        reason = None
    except _NoGridError as why:
        grid, reason = None, str(why)
    return {"grid": grid, "parent": None if parent is None else list(parent), "reason": reason}


def _grid_laid(
    table: ET.Element, rows: list[ET.Element], cells: list[list[ET.Element]], fixed: list[bool]
) -> Json:
    grids = [child for child in table if child.tag == _w("tblGrid")]
    if not grids:
        raise _NoGridError("no-grid")
    if len(grids) > 1:
        raise _NoGridError("two-grids")
    width = sum(1 for child in grids[0] if child.tag == _w("gridCol"))
    out_rows: list[Json] = []
    for row, row_cells in zip(rows, cells, strict=True):
        skipped = _grid_number(row.find(_w("trPr")), "gridBefore", 0)
        left = _grid_number(row.find(_w("trPr")), "gridAfter", 0)
        at = skipped
        laid: list[Json] = []
        for cell in row_cells:
            props = cell.find(_w("tcPr"))
            if props is not None and props.find(_w("hMerge")) is not None:
                raise _NoGridError("h-merge")
            down = None if props is None else props.find(_w("vMerge"))
            merge = None if down is None else down.get(_w("val"), "continue")
            if merge not in (None, "restart", "continue"):
                raise _NoGridError("bad-merge")
            span = _grid_number(props, "gridSpan", 1)
            if not span:
                raise _NoGridError("bad-span")
            laid.append({"column": at, "merge": merge, "span": span})
            at += span
        if at + left != width:
            raise _NoGridError("row-off-grid")
        exact = fixed[len(out_rows)]
        out_rows.append({"after": left, "before": skipped, "cells": laid, "exactHeight": exact})
    return {"columns": width, "rows": out_rows}


def _row_fixed(row_properties: ET.Element | None) -> bool:
    """Whether a ``trPr`` makes its row's height exact: its first ``trHeight``'s ``hRule``."""
    heights = [] if row_properties is None else row_properties.findall(_w("trHeight"))
    return bool(heights) and heights[0].get(_w("hRule")) == "exact"


def _line_rule(fonts: _Fonts, paragraph: _Paragraph) -> str | None:
    """A paragraph's line rule, from the first ``spacing`` that sets one.

    Looked for in the paragraph's own properties, then its style's, its table style's (in a
    table) and the defaults'.
    """
    sources = [
        paragraph.properties,
        *(fonts.ppr.get(i) for i in fonts.style_ids(paragraph.style, "paragraph")),
        *(
            fonts.ppr.get(i)
            for i in (
                fonts.style_ids(paragraph.table_style, "table")
                if paragraph.table is not None
                else []
            )
        ),
        fonts.doc_ppr,
    ]
    rules = [
        rule
        for source in sources
        if source is not None
        for spacing in source.findall(_w("spacing"))[:1]
        if (rule := spacing.get(_w("lineRule"))) is not None
    ]
    return rules[0] if rules else None


def _grid_number(holder: ET.Element | None, name: str, absent: int) -> int:
    """``holder``'s ``name`` child's value, digits only, or ``absent`` without one."""
    element = None if holder is None else holder.find(_w(name))
    if element is None:
        return absent
    raw = element.get(_w("val"), "")
    if not re.fullmatch(r"[0-9]{1,9}", raw):
        raise _NoGridError("bad-number")
    return int(raw)


# --- pictures: what each U+FFFC stands for ------------------------------------------------

# Why a picture's image part, its bytes stretched over its extent, is not what Word draws: the
# first that holds, in this order.
_NOT_AS_IS = (
    "shape",
    "vml",
    "field",
    "linked",
    "no-part",
    "not-png-or-jpeg",
    "bad-image-header",
    "colour",
    "animated",
    "orientation",
    "bad-number",
    "cropped",
    "rotated",
    "flipped",
    "line-height",
    "row-height",
    "border",
    "effects",
)
# An image read past this many chunks or segments, or larger on a side, is a bad one.
_PIECES = 100_000
_SIDE = 10_000
_CODE = re.compile(rb"[^\xff]")
_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
# Short names for the namespaces a picture's drawing is written in.
_PREFIXES = {
    W: "w",
    _R: "r",
    _WP: "wp",
    "http://schemas.openxmlformats.org/markup-compatibility/2006": "mc",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "a",
    "http://schemas.openxmlformats.org/drawingml/2006/picture": "pic",
    "http://schemas.microsoft.com/office/drawing/2010/main": "a14",
}
# A PNG's colour types, each with the bit depths it may have.
_PNG_KINDS = {
    *((0, depth) for depth in (1, 2, 4, 8, 16)),
    *((3, depth) for depth in (1, 2, 4, 8)),
    *((colour, depth) for colour in (2, 4, 6) for depth in (8, 16)),
}


def _short(name: str) -> str:
    """A tag or attribute name as ``prefix:local`` (``a:blip``), or as is in no known namespace."""
    if not name.startswith("{"):
        return name
    space, local = name[1:].split("}", 1)
    return f"{_PREFIXES[space]}:{local}" if space in _PREFIXES else name


def _kids(element: ET.Element) -> list[str]:
    return [_short(child.tag) for child in element]


def _keys(element: ET.Element) -> set[str]:
    return {_short(key) for key in element.attrib}


def _empty(element: ET.Element) -> bool:
    """No attributes and no children."""
    return not element.attrib and not len(element)


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    """``element``'s first child named ``name`` (``prefix:local``)."""
    found = [] if element is None else [c for c in element if _short(c.tag) == name]
    return found[0] if found else None


def _whole(
    element: ET.Element | None,
    names: tuple[str, ...],
    why: set[str],
    signed: bool,
    absent: str = "",
) -> list[int] | None:
    """The attributes as whole numbers (an absent one ``absent``), or None and ``bad-number``."""
    pattern = ("-?" if signed else "") + "[0-9]{1,15}"
    raw = [None if element is None else element.get(n, absent or None) for n in names]
    if any(value is None or not re.fullmatch(pattern, value) for value in raw):
        why.add("bad-number")
        return None
    return [int(value) for value in raw if value is not None]


def _blip_fill(fill: ET.Element, blip: ET.Element, why: set[str]) -> dict[str, int] | None:
    """The crop a picture's fill names; what in it may change Word's drawing is ``effects``."""
    stretch = _child(fill, "a:stretch")
    if fill.attrib or _kids(fill) not in (
        ["a:blip", "a:stretch"],
        ["a:blip", "a:srcRect", "a:stretch"],
    ):
        why.add("effects")
    if stretch is None or stretch.attrib or _kids(stretch) != ["a:fillRect"]:
        why.add("effects")
    if stretch is not None and not all(_empty(c) for c in stretch):
        why.add("effects")
    # A link is ``linked``, which comes before ``effects``: it needs no place here.
    if not _keys(blip) - {"r:link"} <= {"r:embed", "cstate"}:
        why.add("effects")
    if "r:link" in _keys(blip):
        why.add("linked")
    for holder in blip:
        if _short(holder.tag) != "a:extLst":
            why.add("effects")
        for extension in holder:
            # Word's compression setting alone, which leaves the drawing as it is.
            dpi = extension.get("uri") == "{28A0092B-C50C-407E-A947-70E740481C1C}"
            if not (dpi and _short(extension.tag) == "a:ext"):
                why.add("effects")
            if _kids(extension) != ["a14:useLocalDpi"]:
                why.add("effects")
    source = _child(fill, "a:srcRect")
    if source is None:
        return None
    if len(source) or not _keys(source) <= {"l", "t", "r", "b"}:
        why.add("effects")
    sides = _whole(source, ("l", "t", "r", "b"), why, True, "0")
    if sides is None:
        return None
    if any(sides):
        why.add("cropped")
    return dict(zip(("l", "t", "r", "b"), sides, strict=True))


def _shape_properties(shape: ET.Element | None, extent: list[int] | None, why: set[str]) -> None:
    """What in a picture's shape properties may change how Word draws its image."""
    if shape is None:
        return
    names = _kids(shape)
    if (
        not _keys(shape) <= {"bwMode"}
        or sorted(set(names)) != sorted(names)
        or not set(names) <= {"a:xfrm", "a:prstGeom", "a:noFill", "a:ln"}
    ):
        why.add("effects")
    transform = _child(shape, "a:xfrm")
    if transform is not None:
        inside = _kids(transform)
        if not _keys(transform) <= {"rot", "flipH", "flipV"} or sorted(set(inside)) != sorted(
            inside
        ):
            why.add("effects")
        if not set(inside) <= {"a:off", "a:ext"}:
            why.add("effects")
        turn = _whole(transform, ("rot",), why, True, "0")
        if turn and turn[0] != 0:
            why.add("rotated")
        for mirror in ("flipH", "flipV"):
            said = transform.get(mirror, "0")
            if said in ("true", "1"):
                why.add("flipped")
            elif said not in ("false", "0"):
                why.add("bad-number")
        offset = _child(transform, "a:off")
        if offset is not None and _whole(offset, ("x", "y"), why, True) != [0, 0]:
            why.add("effects")
        size = _child(transform, "a:ext")
        if size is not None and _whole(size, ("cx", "cy"), why, False) != extent:
            why.add("effects")
    outline = _child(shape, "a:prstGeom")
    if outline is not None:
        if _keys(outline) != {"prst"} or outline.get("prst") != "rect":
            why.add("effects")
        if _kids(outline) not in ([], ["a:avLst"]) or not all(_empty(c) for c in outline):
            why.add("effects")
    fill = _child(shape, "a:noFill")
    if fill is not None and not _empty(fill):
        why.add("effects")
    line = _child(shape, "a:ln")
    if line is not None and (_kids(line) != ["a:noFill"] or not _empty(line[0])):
        why.add("effects")


def _png_facts(data: bytes) -> tuple[list[int] | None, set[str]]:
    """A PNG's pixels and reasons, its chunks read one by one (none kept but IHDR and eXIf).

    Each chunk whole, with its CRC; IHDR first and only first, of a size up to ``_SIDE``, a bit
    depth its colour type allows; no critical chunk but IHDR, PLTE, IDAT and IEND; a palette
    image's PLTE before its first IDAT; image data; nothing after IEND; at most ``_PIECES``.
    """
    broken: tuple[None, set[str]] = (None, {"bad-image-header"})
    seen: list[bytes] = []
    exif: list[bytes] = []
    pixels: list[int] = []
    colour = -1
    at = 8
    for number in range(_PIECES):
        if len(data) - at < 12:
            return broken
        length, kind = struct.unpack_from(">I4s", data, at)
        stop = at + length + 12
        # A length of 2**31 or more runs past any image a package holds.
        if stop > len(data) or not re.fullmatch(rb"[A-Za-z]{4}", kind):
            return broken
        (crc,) = struct.unpack_from(">I", data, stop - 4)
        if crc != zlib.crc32(memoryview(data)[at + 4 : stop - 4]):
            return broken
        # Upper-case letters stand below the lower-case ones.
        critical = kind[0] < 0x61
        if (number == 0) != (kind == b"IHDR"):
            return broken
        if critical and kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            return broken
        if kind == b"IHDR":
            if length != 13:
                return broken
            width, height, depth, colour, method, filtering, interlace = struct.unpack_from(
                ">IIBBBBB", data, at + 8
            )
            if (
                not (1 <= width <= _SIDE and 1 <= height <= _SIDE)
                or (colour, depth) not in _PNG_KINDS
                or (method, filtering) != (0, 0)
                or interlace not in (0, 1)
            ):
                return broken
            pixels = [width, height]
        elif kind == b"IDAT" and colour == 3 and b"IDAT" not in seen and b"PLTE" not in seen:
            return broken
        elif kind == b"eXIf" and len(exif) < 2:
            exif.append(data[at + 8 : stop - 4])
        if kind not in seen:
            seen.append(kind)
        at = stop
        if kind == b"IEND":
            break
    else:
        return broken
    if at != len(data) or b"IDAT" not in seen:
        return broken
    why = _exif_turn(exif)
    if b"iCCP" in seen or b"cHRM" in seen or (b"gAMA" in seen and b"sRGB" not in seen):
        why.add("colour")
    if b"acTL" in seen:
        why.add("animated")
    return pixels, why


def _jpeg_facts(data: bytes) -> tuple[list[int] | None, set[str]]:
    """A JPEG's pixels and reasons, by its one frame header among whole segments before its scan.

    The frame baseline, extended or progressive, of precision 8, 1, 3 or 4 components (4 is
    ``colour``, as an ICC profile is) and a size up to ``_SIDE``; at most ``_PIECES`` segments.
    """
    broken: tuple[None, set[str]] = (None, {"bad-image-header"})
    frame: tuple[int, bytes] | None = None
    exif: list[bytes] = []
    icc = False
    at = 2
    for _ in range(_PIECES):
        if data[at : at + 1] != b"\xff":
            return broken
        code_at = _past_fill(data, at)
        if code_at >= len(data):
            return broken
        code = data[code_at]
        at = code_at + 1
        if code == 0x01 or code & 0xF8 == 0xD0:
            continue  # TEM and RST0 to RST7 stand alone
        if code in (0x00, 0xD8, 0xD9) or len(data) - at < 2:
            return broken
        (length,) = struct.unpack_from(">H", data, at)
        if length < 2 or at + length > len(data):
            return broken
        segment = data[at + 2 : at + length]
        at += length
        if code == 0xDA:
            break
        if code & 0xF0 == 0xC0 and code not in (0xC4, 0xC8, 0xCC):
            if frame is not None:
                return broken
            frame = (code, segment)
        elif code == 0xE1 and segment[:6] == b"Exif\x00\x00":
            exif = [*exif, segment[6:]][:2]
        elif code == 0xE2 and segment[:12] == b"ICC_PROFILE\x00":
            icc = True
    else:
        return broken
    if frame is None or frame[0] > 0xC2 or len(frame[1]) < 6:
        return broken
    precision, height, width, parts = struct.unpack_from(">BHHB", frame[1])
    if (
        precision != 8
        or parts not in (1, 3, 4)
        or len(frame[1]) < 6 + 3 * parts
        or not (1 <= width <= _SIDE and 1 <= height <= _SIDE)
    ):
        return broken
    why = _exif_turn(exif)
    if icc or parts == 4:
        why.add("colour")
    return [width, height], why


def _past_fill(data: bytes, at: int) -> int:
    """Where the first byte that is not 0xFF stands, from ``at`` (``len(data)`` if none)."""
    found = _CODE.search(data, at)
    return len(data) if found is None else found.start()


def _exif_turn(blocks: list[bytes]) -> set[str]:
    """``orientation`` where Exif data turns or mirrors the image (its tag 0x0112 not 1)."""
    if not blocks:
        return set()
    tiff = blocks[0]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if len(blocks) != 1 or order is None or len(tiff) < 8:
        return {"bad-image-header"}
    magic, directory = struct.unpack_from(order + "HI", tiff, 2)
    if magic != 42 or directory + 2 > len(tiff):
        return {"bad-image-header"}
    (count,) = struct.unpack_from(order + "H", tiff, directory)
    if directory + 2 + 12 * count > len(tiff):
        return {"bad-image-header"}
    for index in range(count):
        tag, kind, many, value = struct.unpack_from(
            order + "HHIH", tiff, directory + 2 + 12 * index
        )
        if tag == 0x0112:
            if (kind, many) != (3, 1):
                return {"bad-image-header"}
            return set() if value == 1 else {"orientation"}
    return set()


def _inert(element: ET.Element) -> bool:
    """Whether the walk passes over ``element``: one of ``_INERT``, holding no text."""
    if element.tag not in _INERT:
        return False
    for node in element.iter():
        if node.tag in (_w("p"), _w("r")) or _local(node.tag) in ("t", "instrText", "delText"):
            raise CertificationError(f"text in {_local(element.tag)}")
    return True


def _control(sdt: ET.Element) -> None:
    """A content control whose text Word shows from elsewhere than its content is refused.

    One bound to data (Word shows the data; the content is a cache), and one whose content shows
    nothing while it names a placeholder or says it shows one (Word shows the placeholder's
    text, kept elsewhere). A placeholder Word shows from the content is the content's text.
    """
    properties = sdt.find(_w("sdtPr"))
    if properties is None:
        return
    if any(_local(c.tag) == "dataBinding" for c in properties):
        raise CertificationError("a content control bound to data")
    content = sdt.find(_w("sdtContent"))
    shows = any(
        (node.tag == _w("t") and node.text) or _local(node.tag) in _STANDS | _NOTE_MARKS
        for node in ([] if content is None else content.iter())
    )
    placeholder = properties.find(f"{_w('placeholder')}/{_w('docPart')}") is not None
    if not shows and (placeholder or _on(properties.find(_w("showingPlcHdr")))):
        raise CertificationError("an empty content control showing a placeholder")


def _docvariable(instruction: str, variables: list[tuple[str | None, str | None]]) -> str | None:
    r"""The value a DOCVARIABLE field must show, or None for another field.

    Word shows its stored result, and once fields are updated its document variable's value, so
    the two must agree: the name unquoted after the code, no switch but ``\*`` MERGEFORMAT or
    CHARFORMAT, and one variable of that name in the settings, ignoring case, in the field's
    case, with a value. Anything else is never certified.
    """
    words = instruction.split()
    if not words or words[0].upper() != "DOCVARIABLE":
        return None
    if '"' in instruction or len(words) < 2 or words[1].startswith("\\"):
        raise CertificationError("a DOCVARIABLE field the check does not read")
    name, rest = words[1], words[2:]
    while rest:
        if rest[:1] != ["\\*"] or len(rest) < 2 or rest[1].upper() not in _VARIABLE_FORMATS:
            raise CertificationError("a DOCVARIABLE field with a switch the check does not read")
        rest = rest[2:]
    same = [(n, v) for n, v in variables if n is not None and n.casefold() == name.casefold()]
    if len(same) != 1 or same[0][0] != name or same[0][1] is None:
        raise CertificationError("a DOCVARIABLE without one variable of its name")
    return same[0][1]


def _page_field(instruction: str) -> bool:
    r"""Whether a field is a page number; one with a switch Word has not answered is refused.

    Word draws the page for PAGEREF with ``\h`` and for ``\*`` MERGEFORMAT, CHARFORMAT or
    Arabic; ``\p`` shows "above" or "below", ``\#`` a picture's text, other formats words.
    """
    words = instruction.split()
    code = words[0].upper() if words else None
    if code not in _LAYOUT_CODES:
        return False
    rest = [word for word in words[1:] if not (code == "PAGEREF" and word == "\\h")]
    if code == "PAGEREF":
        if not rest or rest[0].startswith("\\"):
            raise CertificationError("a PAGEREF without its bookmark")
        rest = rest[1:]
    while rest:
        if rest[:1] != ["\\*"] or len(rest) < 2 or rest[1].upper() not in _PAGE_FORMATS:
            raise CertificationError(f"a {code} field with a switch Word has not answered")
        rest = rest[2:]
    return True


# --- .docx: matching the result -----------------------------------------------------------


def _match(paragraph: _Paragraph, value: dict[str, Json], where: str) -> None:
    """The output paragraph must be exactly the tokens the check read, and its marks placed."""
    text: str = value["text"]
    expected: list[str] = []
    pages: list[int] = []
    notes: list[tuple[int, tuple[str, int]]] = []
    comments: list[tuple[int, int]] = []
    anchored: list[dict[str, Json]] = []
    position = 0
    for segment in paragraph.segments:
        if segment.marker is None:
            expected.append(segment.text)
            position += len(segment.text)
        elif segment.marker[0] == "page":
            pages.append(position)
        elif segment.marker[0] == "comment":
            comments.append((position, segment.marker[1]))
        elif segment.marker[0] == "anchored":
            anchored.append({"kind": segment.marker[1], "offset": position, "read": False})
        else:
            notes.append((position, segment.marker[1]))
    if text != "".join(expected):
        raise CertificationError(f"{where}: the text is not the document's")
    if value["anchored"] != anchored:
        raise CertificationError(f"{where}: not the floating objects the document anchors there")
    # What each U+FFFC stands for, where it stands.
    places = [at for at, character in enumerate(text) if character == _OBJECT]
    if len(places) != len(paragraph.pictures) or value["pictures"] != [
        {**picture, "offset": at} for at, picture in zip(places, paragraph.pictures, strict=True)
    ]:
        raise CertificationError(f"{where}: not the pictures the document holds")
    # The marks this check works out, character by character.
    shown = [
        kinds for segment in paragraph.segments for kinds in [segment.kinds] * len(segment.text)
    ]
    claimed: list[set[str]] = [set() for _ in text]
    for mark in value["marks"]:
        start, end, kind = mark["start"], mark["end"], mark["kind"]
        # A mark means text[start:end]: a span of the text, of a kind the result format names.
        if not (type(start) is int and type(end) is int and 0 <= start < end <= len(text)):
            raise CertificationError(f"{where}: a mark that is no span of the text")
        if not (type(kind) is str and (kind in _MARK_KINDS or _MARK_PATTERN.fullmatch(kind))):
            raise CertificationError(f"{where}: a mark of no kind the result format names")
        if kind in CHECKED_MARKS:
            for index in range(start, end):
                claimed[index].add(kind)
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
    if value["style"] != paragraph.style:
        raise CertificationError(f"{where}: not the paragraph style the document names")
    if value["markHidden"] is not paragraph.mark_hidden:
        raise CertificationError(f"{where}: the paragraph mark is not hidden as Word hides it")
    if value["numbering"] != paragraph.numbering:
        raise CertificationError(f"{where}: not the list label Word draws")


def _docx_parts(data: bytes) -> tuple[zipfile.ZipFile, str, dict[str, str]]:
    archive = zipfile.ZipFile(io.BytesIO(data))
    for info in archive.infolist():
        stored = info.orig_filename
        # The stored name, segment by segment: zipfile may read another one, and another zip
        # reader may find another part under a near spelling.
        if info.filename != stored or "\\" in stored or {"", ".", ".."} & set(stored.split("/")):
            raise CertificationError("a zip entry whose name is not a part name")
        if info.compress_type not in (0, 8):
            raise CertificationError("a part neither stored nor deflated")
    names = archive.namelist()
    # Part names are compared without regard to case: two entries under one name, in any case,
    # are two parts zipfile would read as one (the last).
    if len({name.casefold() for name in names}) != len(names):
        raise CertificationError("a part name occurs twice")
    if "[Content_Types].xml" not in names:
        raise CertificationError("no [Content_Types].xml")
    main = _relations(archive, "", "officeDocument")
    if len(main) != 1:
        raise CertificationError("not one main document part")
    related: dict[str, str] = {}
    for kind in (
        "styles",
        "theme",
        "footnotes",
        "endnotes",
        "comments",
        "numbering",
        "fontTable",
        "settings",
    ):
        targets = _relations(archive, main[0], kind)
        if len(targets) > 1:
            raise CertificationError(f"two {kind} parts")
        if targets:
            related[kind] = targets[0]
    return archive, main[0], related


def _by_id(archive: zipfile.ZipFile, source: str) -> dict[str | None, list[ET.Element]]:
    """``source``'s relationships by Id."""
    folder, base = posixpath.split(source)
    name = posixpath.join(folder, "_rels", base + ".rels")
    out: dict[str | None, list[ET.Element]] = {}
    if name not in archive.namelist():
        return out
    for rel in ET.fromstring(archive.read(name)).iter(f"{{{_RELS}}}Relationship"):
        out.setdefault(rel.get("Id"), []).append(rel)
    return out


def _relation(
    by_id: dict[str | None, list[ET.Element]], source: str, relationship: str | None, kind: str
) -> str:
    """The part ``source``'s relationship ``relationship`` names, which must be a ``kind``."""
    rels = by_id.get(relationship, [])
    if len(rels) > 1:
        raise CertificationError(f"two relationships {relationship}")
    if not rels:
        raise CertificationError(f"no relationship {relationship}")
    rel = rels[0]
    if rel.get("TargetMode") == "External" or not _typed(rel, kind):
        raise CertificationError(f"{relationship} is not a {kind} part")
    target = rel.get("Target", "")
    folder = posixpath.dirname(source)
    return posixpath.normpath(
        target[1:] if target.startswith("/") else posixpath.join(folder, target)
    )


def _typed(rel: ET.Element, kind: str) -> bool:
    """Whether ``rel`` is of the type Word writes for ``kind``; one that only ends so is refused."""
    found = rel.get("Type", "")
    if found == f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}":
        return True
    if found.rsplit("/", 1)[-1] == kind:
        raise CertificationError(f"a relationship of type {found!r}")
    return False


def _relations(archive: zipfile.ZipFile, source: str, kind: str) -> list[str]:
    folder, base = posixpath.split(source)
    name = posixpath.join(folder, "_rels", base + ".rels")
    if name not in archive.namelist():
        return []
    out = []
    for rel in ET.fromstring(archive.read(name)).iter(f"{{{_RELS}}}Relationship"):
        if rel.get("TargetMode") == "External" or not _typed(rel, kind):
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
    # The tables the walk met, as ``_Story.met`` keeps them.
    tables: list[_Met] = field(default_factory=list)


def _total(ledgers: list[_Ledger]) -> _Ledger:
    total = _Ledger()
    for ledger in ledgers:
        for name in total.__dataclass_fields__:
            setattr(total, name, getattr(total, name) + getattr(ledger, name))
    return total


# --- list labels and note marks, worked out a second time --------------------------------------
#
# Word draws "4.8" or "b)" before a list item and "1" or "*" where a note is referred to; the
# reader computes them, and so does this, on its own, by the rules Word answered in
# corpus/numbering-cases (named in brackets). A result whose labels or note marks are not these
# is not certified.

_ROMAN_NUMERALS = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)
_NOTE_DEFAULTS = {"footnote": "decimal", "endnote": "lowerRoman"}
_CHICAGO_SIGNS = ("*", "\u2020", "\u2021", "\u00a7")


def _formatted(value: int, fmt: str) -> str:
    """``value`` as Word writes it in the number format ``fmt``."""
    if fmt == "none":
        return ""
    if fmt in ("decimal", "decimalZero") and value >= 0:
        return f"{value:02d}" if fmt == "decimalZero" else str(value)
    if fmt in ("upperRoman", "lowerRoman") and 1 <= value <= 3999:
        numerals, rest = "", value
        for amount, numeral in _ROMAN_NUMERALS:
            count, rest = divmod(rest, amount)
            numerals += numeral * count
        return numerals if fmt == "upperRoman" else numerals.lower()
    if fmt in ("upperLetter", "lowerLetter") and 1 <= value <= 780:
        # a to z, then aa to zz: the letter repeated.
        letters = chr(ord("A") + (value - 1) % 26) * ((value - 1) // 26 + 1)
        return letters if fmt == "upperLetter" else letters.lower()
    if fmt == "chicago" and 1 <= value <= 6:  # as far as Word drew them [notes-chicago]
        return _CHICAGO_SIGNS[(value - 1) % 4] * ((value - 1) // 4 + 1)
    raise CertificationError(f"the number {value} in {fmt}")


@dataclass
class _ListLevel:
    start: int | None
    fmt: str
    text: str | None
    restart: int | None
    legal: bool
    suffix: str
    rpr: ET.Element | None
    picture: bool


def _list_level(element: ET.Element) -> _ListLevel:
    def value(name: str) -> str | None:
        found = element.find(_w(name))
        return None if found is None else found.get(_w("val"))

    fmt_element = element.find(_w("numFmt"))
    if fmt_element is not None and fmt_element.get(_w("format")) is not None:
        raise CertificationError("a custom list number format")
    text_element = element.find(_w("lvlText"))
    text = None
    if text_element is not None:
        null = text_element.get(_w("null")) in ("1", "true", "on")
        text = "" if null else text_element.get(_w("val"), "")
    legacy = element.find(_w("legacy"))
    suffix = value("suff") or "tab"
    if legacy is not None and legacy.get(_w("legacy")) not in ("0", "false", "off"):
        suffix = "legacy"
    start, restart = value("start"), value("lvlRestart")
    return _ListLevel(
        start=None if start is None else int(start),
        fmt=value("numFmt") or "decimal",
        text=text,
        restart=None if restart is None else int(restart),
        legal=_on(element.find(_w("isLgl"))) is True,
        suffix=suffix,
        rpr=element.find(_w("rPr")),
        picture=element.find(_w("lvlPicBulletId")) is not None,
    )


def _once[T](pairs: Iterator[tuple[int, T]]) -> dict[int, T]:
    """``pairs`` as a dict of list levels; a level given twice is refused."""
    out: dict[int, T] = {}
    for level, value in pairs:
        if level in out:
            raise CertificationError(f"list level {level} is defined twice")
        out[level] = value
    return out


class _Numbering:
    """The labels of a document's lists, counted in document order."""

    def __init__(self, root: ET.Element | None, fonts: _Fonts) -> None:
        self.fonts = fonts
        self.abstracts: dict[int, tuple[dict[int, _ListLevel], str | None, str | None]] = {}
        self.nums: dict[int, tuple[int, dict[int, int], dict[int, _ListLevel]]] = {}
        # Anything defined twice (a list, a list's definition, a level of either) is refused:
        # which one Word takes is not on record.
        for element in [] if root is None else root.findall(_w("abstractNum")):
            levels = _once(
                (int(lvl.get(_w("ilvl"), "")), _list_level(lvl))
                for lvl in element.findall(_w("lvl"))
            )
            link = element.find(_w("numStyleLink"))
            back = element.find(_w("styleLink"))
            abstract_id = int(element.get(_w("abstractNumId"), ""))
            if abstract_id in self.abstracts:
                raise CertificationError(f"abstractNum {abstract_id} is defined twice")
            self.abstracts[abstract_id] = (
                levels,
                None if link is None else link.get(_w("val")),
                None if back is None else back.get(_w("val")),
            )
        num_ids: set[int] = set()
        for element in [] if root is None else root.findall(_w("num")):
            num_id = int(element.get(_w("numId"), ""))
            if num_id in num_ids:
                raise CertificationError(f"numId {num_id} is defined twice")
            num_ids.add(num_id)
            abstract = element.find(_w("abstractNumId"))
            if abstract is None:
                # A list naming no abstractNum is as one not defined.
                continue
            starts: dict[int, int] = {}
            looks: dict[int, _ListLevel] = {}
            overrides = element.findall(_w("lvlOverride"))
            _once((int(o.get(_w("ilvl"), "")), o) for o in overrides)
            for override in overrides:
                level = int(override.get(_w("ilvl"), ""))
                start = override.find(_w("startOverride"))
                if start is not None:
                    starts[level] = int(start.get(_w("val"), ""))
                if override.find(_w("lvl")) is not None:
                    looks[level] = _list_level(override.find(_w("lvl")))  # type: ignore[arg-type]
            key = int(abstract.get(_w("val"), ""))
            self.nums[num_id] = (key, starts, looks)
        # Each abstractNum's counts, shared by its lists: the values, the startOverrides
        # applied, where a restarted level restarts from, and what a level counted only through
        # a deeper paragraph shows meanwhile.
        self.values: dict[int, list[int | None]] = {}
        self.applied: dict[int, set[tuple[int, int]]] = {}
        self.restarts: dict[int, list[int | None]] = {}
        self.showing: dict[int, list[int | None]] = {}

    def levels(
        self, num_id: int
    ) -> tuple[int, dict[int, int], dict[int, _ListLevel], dict[int, _ListLevel]]:
        """The abstractNum counting a list, its startOverrides, its levels, and their looks."""
        if num_id not in self.nums or self.nums[num_id][0] not in self.abstracts:
            raise CertificationError(f"numId {num_id} names no list")
        key, starts, looks = self.nums[num_id]
        levels, link, _ = self.abstracts[key]
        if link is not None:
            # A numbering style's list, which must name the style back [numbering-style-link].
            style = self.fonts.ppr.get(link)
            linked = None if style is None else style.find(f"{_w('numPr')}/{_w('numId')}")
            target = None if linked is None else self.nums.get(int(linked.get(_w("val"), "")))
            if target is None or self.abstracts.get(target[0], ({}, None, None))[2] != link:
                raise CertificationError(f"numbering style {link} names no list back")
            levels = self.abstracts[target[0]][0]
        return key, starts, levels, {**levels, **looks}

    def label(self, num_id: int, level: int, paragraph: _Paragraph) -> tuple[str, str]:
        """The label Word draws for this list item, and what follows it."""
        key, starts, base, looks = self.levels(num_id)
        values = self.values.setdefault(key, [None] * 9)
        applied = self.applied.setdefault(key, set())
        restarts = self.restarts.setdefault(key, [None] * 9)
        showing = self.showing.setdefault(key, [None] * 9)
        for upper in range(level + 1):
            rule = looks[upper].restart if upper in looks else None
            # lvlRestart is 0 or names a level above the one directly above; Word draws a level
            # whose lvlRestart names itself, a deeper one or the one directly above, empty
            # [restart-level-above].
            if rule is not None and rule != 0 and not 1 <= rule < upper:
                raise CertificationError(f"lvlRestart {rule} on level {upper}")
        for deeper in range(level + 1, 9):
            rule = looks[deeper].restart if deeper in looks else None
            # lvlRestart n restarts the level after a level up to n - 1, 0 never; a value
            # naming no higher level restarts it after any [restart-never, restart-after-first].
            # It restarts as this paragraph's list says [restart-source-override,
            # restart-source-plain, restart-source-unused].
            if rule is not None and rule < 0:
                raise CertificationError(f"lvlRestart {rule} on level {deeper}")
            if rule is None or level < rule:
                values[deeper] = None
                restarts[deeper] = starts.get(deeper)
                showing[deeper] = None
        for higher in range(level):
            # Not counted yet: it shows the abstractNum's start [ancestor-never-counted,
            # override-implicit-ancestor] and counts on from this list's startOverride, which
            # stays unused [override-implicit-continued, override-implicit-reused,
            # override-implicit-levels].
            if values[higher] is None:
                if higher not in base:
                    raise CertificationError(f"level {higher} of a list is not defined")
                showing[higher] = base[higher].start or 0
                values[higher] = starts[higher] if higher in starts else showing[higher]
        showing[level] = None
        if level in starts and (num_id, level) not in applied:
            # A startOverride, the first time its list reaches the level [start-override-first].
            applied.add((num_id, level))
            values[level] = starts[level]
        elif values[level] is not None:
            values[level] = int(values[level]) + 1  # type: ignore[arg-type]
        elif restarts[level] is not None:
            values[level] = restarts[level]
        elif level in base:
            # Its abstractNum's start, never a level override's [level-override-first]; none, 0
            # [missing-start].
            values[level] = base[level].start or 0
        else:
            raise CertificationError(f"level {level} of a list is not defined")
        look = looks.get(level)
        if look is None or look.text is None:
            raise CertificationError(f"level {level} of a list draws nothing defined")
        if len(look.text) > 255:
            raise CertificationError(f"level {level} of a list has a text past 255 characters")
        out = ""
        for piece in re.split(r"(%[1-9])", look.text):
            if not re.fullmatch(r"%[1-9]", piece):
                out += piece
                continue
            shown = int(piece[1]) - 1
            source = looks.get(shown)
            count = values[shown] if showing[shown] is None else showing[shown]
            if source is None or count is None:
                raise CertificationError(f"a list label shows level {shown}, never counted")
            # isLgl writes decimal, but keeps a decimalZero level's zero [Word's answer].
            legal = look.legal and source.fmt != "decimalZero"
            out += _formatted(count, "decimal" if legal else source.fmt)
        # The paragraph mark's properties, with its character style, as a run's; a label over a
        # hidden mark is not on record.
        mark = None if paragraph.properties is None else paragraph.properties.find(_w("rPr"))
        mark_style = None if mark is None else mark.find(_w("rStyle"))
        mark_levels = [
            x
            for x in (
                mark,
                *self.fonts.chain(
                    None if mark_style is None else mark_style.get(_w("val")), "character"
                ),
                *self.fonts.chain(paragraph.style, "paragraph"),
                *(
                    [*paragraph.conditional, *self.fonts.chain(paragraph.table_style, "table")]
                    if paragraph.table is not None
                    else []
                ),
                self.fonts.doc_rpr,
            )
            if x is not None
        ]
        if self.fonts.hidden(mark_levels, mark) or self.fonts.hidden(
            mark_levels, mark, "specVanish"
        ):
            raise CertificationError("a list label over a hidden paragraph mark")
        # A bullet in the Symbol font: through the table, as the label's fonts say.
        label_levels = mark_levels if look.rpr is None else [look.rpr, *mark_levels]
        family = self.fonts.drawn(label_levels)
        if family == "east-asian-symbol":
            raise CertificationError("a list label with Symbol in its East Asian slot")
        if family == "symbol":
            out = _symbol_reading(out)
        elif family == "wingdings":
            out = "".join(_bullet_reading(ord(c)) for c in out)

        def any_level(*names: str) -> bool:
            return any(_on(x.find(_w(n))) for x in label_levels for n in names)

        # What Word draws otherwise than the characters: a picture bullet, a hidden label, and
        # letters in capitals.
        if look.picture:
            raise CertificationError(f"level {level} of a list draws a picture")
        if any_level("vanish", "specVanish"):
            raise CertificationError("a hidden list label")
        if out != out.upper() and any_level("caps", "smallCaps"):
            raise CertificationError("a list label in capitals")
        return out, look.suffix


def _numbering_of(fonts: _Fonts, paragraph: _Paragraph) -> tuple[int, int] | None:
    """A paragraph's (numId, ilvl): each from the nearest properties that set it, or None."""
    own = [
        paragraph.properties,
        *(fonts.ppr.get(i) for i in fonts.style_ids(paragraph.style, "paragraph")),
    ]
    table = [
        fonts.ppr.get(i)
        for i in (
            fonts.style_ids(paragraph.table_style, "table") if paragraph.table is not None else []
        )
    ]

    def nearest(sources: list[ET.Element | None]) -> tuple[int, int] | None:
        found: dict[str, int] = {}
        for source in sources:
            numbering = None if source is None else source.find(_w("numPr"))
            for name in ("numId", "ilvl"):
                element = None if numbering is None else numbering.find(_w(name))
                if element is not None and name not in found:
                    found[name] = int(element.get(_w("val"), "0"))
        if not found:
            return None
        return found.get("numId", 0), found.get("ilvl", 0)

    found = nearest([*own, *table, fonts.doc_ppr])
    if found != nearest([*own, fonts.doc_ppr]):
        # The list, or its level, set by the table style: Word's answer is not on record.
        raise CertificationError("a list from a table style")
    return found


def _note_marks(
    paragraphs: list[_Paragraph], sections: list[ET.Element | None]
) -> dict[tuple[str, int], str | None]:
    """The mark Word draws for every note the body refers to [notes-*].

    A custom mark draws no number. Else the section's numStart plus the notes of its kind before
    it, in the document or, where the section restarts them, in the section, in the section's
    format; the settings part's are not Word's.
    """
    marks: dict[tuple[str, int], str | None] = {}
    before: dict[tuple[str, int | None], int] = {}
    for paragraph in paragraphs:
        section = sections[min(paragraph.section, len(sections) - 1)] if sections else None
        for segment in paragraph.segments:
            if segment.marker is None or segment.marker[0] != "note":
                continue
            kind, note = segment.marker[1]
            if (kind, note) in marks:
                raise CertificationError(f"{kind} {note} referred to twice")
            if segment.custom:
                marks[(kind, note)] = None
                continue
            settings = None if section is None else section.find(_w(f"{kind}Pr"))

            def value(name: str, default: str, settings: ET.Element | None = settings) -> str:
                element = None if settings is None else settings.find(_w(name))
                found = None if element is None else element.get(_w("val"))
                return default if found is None else found

            fmt = value("numFmt", _NOTE_DEFAULTS[kind])
            if value("numRestart", "continuous") not in ("continuous", "eachSect"):
                raise CertificationError(f"{kind} numbers that restart otherwise")
            start = int(value("numStart", "1"))
            scope = (
                kind,
                paragraph.section if value("numRestart", "continuous") == "eachSect" else None,
            )
            marks[(kind, note)] = _formatted(start + before.get(scope, 0), fmt)
            before[(kind, None)] = before.get((kind, None), 0) + 1
            before[(kind, paragraph.section)] = before.get((kind, paragraph.section), 0) + 1
    return marks


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
            # Leading zeros stripped: Python reads no more than 4300 decimal digits.
            digits = (found.group(2) or found.group(3)).lstrip("0") or "0"
            return chr(int(digits, 10 if found.group(2) else 16))
        raise CertificationError("an '&' that is not a reference")

    return _REFERENCE.sub(one, text)


# A tag after its "<": up to the first ">" outside a quoted value; a quote ends only at the same
# quote character. A quote left open matches nothing.
_TAG = re.compile(r"""[^>"']*(?:(?:"[^"]*"|'[^']*')[^>"']*)*>""")


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
            found = _TAG.match(text, less + 1)
            if found is None:
                raise CertificationError("a tag that does not end")
            end = found.end() - 1
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
            # Copied only where the tag declares a prefix: a scope is never changed once made.
            scope = scopes[-1]
            # Attributes matter only for a declaration or a reference; most tags have neither.
            for key, double, single in (
                _ATTRIBUTE.findall(body, len(name)) if "xmlns" in body or "&" in body else ()
            ):
                value = double or single
                # Without "&" a value has no reference to resolve, and no bare "&" to refuse.
                value = _unescape(value) if "&" in value else value
                if key == "xmlns" or key.startswith("xmlns:"):
                    scope = dict(scope) if scope is scopes[-1] else scope
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


# An XML declaration's encoding, read from the part's bytes.
_ENCODING = re.compile(rb"""(?:\xef\xbb\xbf)?\s*<\?xml\s[^>]*?encoding\s*=\s*(["'])(.*?)\1""")


def _utf8(name: str, data: bytes) -> None:
    """Refuse a part whose declared encoding would read its bytes otherwise than UTF-8 does.

    UTF-8 by any name Python gives it, or ASCII (UTF-8's first 128 characters) over bytes all
    below 0x80; any other declaration, or one Python does not know, is never certified.
    """
    found = _ENCODING.match(data)
    if found is None:
        return
    try:
        codec = codecs.lookup(found.group(2).decode("ascii")).name
    except (LookupError, UnicodeDecodeError) as error:
        raise CertificationError(f"{name} declares an encoding the check does not know") from error
    if not (codec == "utf-8" or (codec == "ascii" and data.isascii())):
        raise CertificationError(f"{name} declares {codec}")


def _parse(archive: zipfile.ZipFile, name: str) -> ET.Element:
    """A part parsed, its text read twice, by Python's XML parser and by ``_raw_texts``."""
    data = archive.read(name)
    _utf8(name, data)
    root = ET.fromstring(data)
    for node in root.iter():
        if any(_local(key) in ("ProcessContent", "MustUnderstand") for key in node.attrib):
            raise CertificationError(f"{name}: markup compatibility to process")
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
            # Read while the archive is open, for the pictures (``_picture``).
            self.archive = archive
            self.names = set(archive.namelist())
            self.rels: dict[str, dict[str | None, list[ET.Element]]] = {}
            self.images: dict[str, dict[str, Json]] = {}
            parse = {name: _parse(archive, name) for name in {main, *related.values()}}
            for kind in ("styles", "theme", "numbering"):
                root = parse.get(related.get(kind, ""))
                # A picture bullet's definition: drawn only through a level that names it, and
                # a label drawn as a picture is never certified (``label``).
                bullets = (
                    [] if root is None else [n for n in root if _local(n.tag) == "numPicBullet"]
                )
                aside = {id(n) for bullet in bullets for n in bullet.iter()}
                for node in [] if root is None else root.iter():
                    # Alternate content Word resolves, which this check does not; a list level's
                    # own is the level's, never drawn.
                    if (
                        _local(node.tag) != "lvl"
                        and id(node) not in aside
                        and any(_local(c.tag) == "AlternateContent" for c in node)
                    ):
                        raise CertificationError(f"alternate content in the {kind}")
            self.fonts = _Fonts(
                parse.get(related.get("styles", "")),
                parse.get(related.get("theme", "")),
                parse.get(related.get("fontTable", "")),
            )
            settings_root = parse.get(related.get("settings", ""))
            self.variables = [
                (v.get(_w("name")), v.get(_w("val")))
                for v in ([] if settings_root is None else settings_root.iter(_w("docVar")))
            ]
            body = parse[main].find(_w("body"))
            if body is None:
                raise CertificationError("no body")
            self.body = self._part(body, None, main)
            self.lists = _Numbering(parse.get(related.get("numbering", "")), self.fonts)
            for paragraph in self.body.paragraphs:
                paragraph.numbering = self._label(paragraph, None)
            sections: list[ET.Element | None] = [
                p.properties.find(_w("sectPr"))
                for p in self.body.paragraphs
                if p.properties is not None and p.properties.find(_w("sectPr")) is not None
            ]
            self.note_marks = _note_marks(
                self.body.paragraphs, [*sections, body.find(_w("sectPr"))]
            )
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
                    if note_id in self.notes[kind]:
                        # Its text would be counted once, the other's nowhere.
                        raise CertificationError(f"{kind} {note_id} is defined twice")
                    self.notes[kind][note_id] = self._part(note, (kind, note_id), part or "")
                    echo = ("note", (kind, note_id))
                    if self.note_marks.get((kind, note_id), "") is None and any(
                        s.marker == echo
                        for p in self.notes[kind][note_id].paragraphs
                        for s in p.segments
                    ):
                        # A custom mark's echo: Word draws a number there, which is not the
                        # custom mark (corpus/numbering-cases, notes-custom-mark).
                        raise CertificationError(f"{kind} {note_id}: the echo of a custom mark")
            # Headers and footers: each part once, in the order the sections refer to them.
            self.stories: dict[str, list[tuple[str, list[dict[str, Json]], _Part]]] = {
                "header": [],
                "footer": [],
            }
            main_rels: dict[str | None, list[ET.Element]] | None = None
            # Which parts Word shows on some page: a section naming no part of a type keeps the
            # one before; a first part shows only under titlePg, an even one only when the
            # settings turn even pages on; else Word draws the default one there.
            settings = _parse(archive, related["settings"]) if "settings" in related else None
            even_pages = settings is not None and _on(settings.find(_w("evenAndOddHeaders")))
            in_force: dict[tuple[str, str], str] = {}
            self.shown: set[str] = set()
            self.sizes: dict[str, int] = {}
            for section, properties in enumerate(parse[main].iter(_w("sectPr"))):
                named = [
                    (_local(c.tag), c.get(_w("type"), "default"))
                    for c in properties
                    if c.tag in (_w("headerReference"), _w("footerReference"))
                ]
                if len(set(named)) != len(named):
                    raise CertificationError(f"section {section} names one type of header twice")
                for reference in properties:
                    story_kind = next(
                        (k for k in self.stories if reference.tag == _w(f"{k}Reference")), None
                    )
                    if story_kind is None:
                        continue
                    kind = story_kind
                    if main_rels is None:
                        main_rels = _by_id(archive, main)
                    name = _relation(main_rels, main, reference.get(f"{{{_R}}}id"), kind)
                    page_type = reference.get(_w("type"), "default")
                    use = {"section": section, "type": page_type}
                    in_force[(kind, page_type)] = name
                    found = next((e for e in self.stories[kind] if e[0] == name), None)
                    if found is not None:
                        found[1].append(use)
                        continue
                    root = _parse(archive, name)
                    self.sizes[name] = _text_size(root)
                    index = len(self.stories[kind])
                    self.stories[kind].append(
                        (name, [use], self._optional(root, (kind, index), name))
                    )
                title_page = _on(properties.find(_w("titlePg")))
                for (_, page), name in in_force.items():
                    if (
                        page == "default"
                        or (page == "first" and title_page)
                        or (page == "even" and even_pages)
                    ):
                        self.shown.add(name)
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
                self.comments.append(
                    (stored, self._optional(comment, ("comment", comment_id), comments_part or ""))
                )
            self.parts = {
                main,
                *(related[k] for k in ("footnotes", "endnotes", "comments") if k in related),
                *(name for found in self.stories.values() for name, _, _ in found),
            }
            for name in sorted(archive.namelist()):
                if name in self.parts:
                    continue
                try:
                    data = archive.read(name)
                    root = ET.fromstring(data)
                except (ET.ParseError, LookupError, ValueError) as error:
                    if not name.endswith(".xml"):
                        continue  # not XML: a picture, a font, an embedded object
                    # A part this check cannot read is text it cannot say is not there.
                    raise CertificationError(f"{name} cannot be read") from error
                if name.endswith((".xml", ".rels")):
                    _utf8(name, data)
                if any(
                    rel.get("Type", "").endswith("/aFChunk")
                    for rel in root.iter(f"{{{_RELS}}}Relationship")
                ):
                    # A chunk of another format (HTML, RTF...) Word shows as content.
                    raise CertificationError(f"{name} names a chunk the check does not read")
                size = _text_size(root)
                if size:
                    self.not_read[name] = size
            self.comments_part = comments_part

    def _label(
        self, paragraph: _Paragraph, story: tuple[str, int] | None
    ) -> dict[str, Json] | None:
        """The numbering a paragraph's result must carry, label and suffix drawn here.

        Only the body's lists are drawn: a list in a note, header, footer or comment is not.
        """
        found = _numbering_of(self.fonts, paragraph)
        if found is None:
            return None
        num_id, level = found
        if num_id == 0:
            return {"level": level, "numId": 0, "suffix": None, "text": None}
        if story is not None:
            raise CertificationError(f"a list in a {story[0]}")
        text, suffix = self.lists.label(num_id, level, paragraph)
        return {"level": level, "numId": num_id, "suffix": suffix, "text": text}

    def _part(self, element: ET.Element, story: tuple[str, int] | None, source: str) -> _Part:
        ledger = _Ledger()
        for control in element.iter(_w("sdt")):
            _control(control)
        walk = _Story(self.fonts, ledger, story, self.variables)
        walk.blocks(element, None, None)
        if walk.fields:
            raise CertificationError("a field still open where its story ends")
        if story is not None:
            for paragraph in walk.paragraphs:
                paragraph.numbering = self._label(paragraph, story)
        for paragraph in walk.paragraphs:
            # Where Word may clip the paragraph's pictures: an exact line, an exact row.
            clipped = {"line-height"} if _line_rule(self.fonts, paragraph) == "exact" else set()
            if paragraph.in_exact_row:
                clipped.add("row-height")
            paragraph.pictures = [
                self._picture(d, source, around | clipped) for d, around in paragraph.drawings
            ]
        return _Part(walk.paragraphs, ledger, tables=walk.met)

    def _picture(self, drawing: ET.Element, source: str, around: set[str]) -> dict[str, Json]:
        """What one U+FFFC stands for, read from the source by this check's own rules.

        A VML picture's image is its first ``imagedata``'s; a DrawingML one's the ``blip`` of
        the one ``pic:pic`` of the one ``graphicData`` its ``wp:inline`` holds; a ``wps`` shape
        has none. ``around`` are the reasons its place gives. Each reason found is kept; the first
        of ``_NOT_AS_IS`` is the result's.
        """
        why: set[str] = set(around)
        found: dict[str, Json] = dict.fromkeys(
            ("crop", "extent", "part", "pixels", "reason", "sha256", "type")
        )
        found["kind"] = "picture"
        if _short(drawing.tag) == "w:pict":
            image = next(n for n in drawing.iter() if _local(n.tag) == "imagedata")
            why |= {"vml", *self._image(image.get(f"{{{_R}}}id"), source, found)}
        else:
            if _short(drawing.tag) == "mc:AlternateContent":
                drawing = next(c for c in drawing if _short(c.tag) == "mc:Choice")[0]
            frame = drawing[0]
            extent = _whole(_child(frame, "wp:extent"), ("cx", "cy"), why, False)
            found["extent"] = extent
            spread = _child(frame, "wp:effectExtent")
            if spread is not None and _whole(spread, ("l", "t", "r", "b"), why, True) != [0] * 4:
                why.add("effects")
            data = [n for n in frame.iter() if _short(n.tag) == "a:graphicData"]
            shape = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
            if any(d.get("uri") == shape for d in data):
                found["kind"] = "shape"
                why.add("shape")
            else:
                self._drawn(data, extent, source, found, why)
        found["reason"] = next((w for w in _NOT_AS_IS if w in why), None)
        return found

    def _drawn(
        self,
        data: list[ET.Element],
        extent: list[int] | None,
        source: str,
        found: dict[str, Json],
        why: set[str],
    ) -> None:
        """A DrawingML picture's image and drawing: its one ``pic:pic`` and what it holds."""
        picture = data[0][0] if len(data) == 1 and _kids(data[0]) == ["pic:pic"] else None
        fill = _child(picture, "pic:blipFill")
        blip = _child(fill, "a:blip")
        if picture is None or fill is None or blip is None:
            why.add("no-part")
        if picture is not None:
            if _kids(picture) != ["pic:nvPicPr", "pic:blipFill", "pic:spPr"]:
                why.add("effects")
            names = _child(_child(picture, "pic:nvPicPr"), "pic:cNvPr")
            if names is not None and names.get("hidden") in ("true", "1"):
                why.add("effects")
            _shape_properties(_child(picture, "pic:spPr"), extent, why)
        if picture is not None and fill is not None and blip is not None:
            found["crop"] = _blip_fill(fill, blip, why)
            why |= self._image(blip.get(f"{{{_R}}}embed"), source, found)

    def _image(self, rid: str | None, source: str, found: dict[str, Json]) -> set[str]:
        """The image part ``source``'s relationship ``rid`` names, into ``found``; its reasons.

        Only an internal relationship of the image type, once, to a part under that very name.
        """
        if source not in self.rels:
            self.rels[source] = _by_id(self.archive, source)
        rels = [] if rid is None else self.rels[source].get(rid, [])
        if len(rels) != 1 or rels[0].get("TargetMode") == "External":
            return {"no-part"}
        if rels[0].get("Type") != _IMAGE:
            return {"no-part"}
        target = rels[0].get("Target", "")
        folder = posixpath.dirname(source)
        name = posixpath.normpath(
            target[1:] if target.startswith("/") else posixpath.join(folder, target)
        )
        if name not in self.names:
            return {"no-part"}
        if name not in self.images:
            data = self.archive.read(name)
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                kind, (pixels, why) = "png", _png_facts(data)
            elif data[:3] == b"\xff\xd8\xff":
                kind, (pixels, why) = "jpeg", _jpeg_facts(data)
            else:
                kind, pixels, why = None, None, {"not-png-or-jpeg"}
            digest = hashlib.sha256(data).hexdigest()
            self.images[name] = {
                "why": sorted(why),
                "pixels": pixels,
                "sha256": digest,
                "type": kind,
            }
        image = self.images[name]
        found.update(part=name, pixels=image["pixels"], sha256=image["sha256"], type=image["type"])
        return set(image["why"])

    def _optional(self, element: ET.Element, story: tuple[str, int], source: str) -> _Part:
        """A part the reader may refuse on its own: what the check cannot read is kept as such.

        Whatever stops the check (a number that is not one too) is kept: the result must then
        refuse the part (``_refused``).
        """
        try:
            return self._part(element, story, source)
        except Exception as error:  # noqa: BLE001 - kept, and certified only as refused
            return _Part([], _Ledger(), str(error))

    def certify(self, value: dict[str, Json]) -> dict[str, Json]:
        """The certificate for ``value``, a .docx result, or ``CertificationError``."""
        read = [self.body]
        refused: list[str] = []
        not_read = dict(self.not_read)
        _paragraphs(self.body, value["paragraphs"], "paragraph")
        grids = [_grid(*met) for met in self.body.tables]
        if len(value["tables"]) != len(grids):
            raise CertificationError(
                f"{len(value['tables'])} tables, where the body has {len(grids)}"
            )
        for index, (theirs, mine) in enumerate(zip(value["tables"], grids, strict=True)):
            if theirs != mine:
                raise CertificationError(f"table {index + 1}: not the grid the document stores")
        marked = [
            (n["kind"], n["id"], n["mark"]) for p in _every_paragraph(value) for n in p["notes"]
        ]
        marked += [(k, n["id"], n["mark"]) for k in ("footnote", "endnote") for n in value[k + "s"]]
        for kind, note_id, mark in marked:
            # A note the body never refers to has no mark Word draws.
            if (kind, note_id) not in self.note_marks or mark != self.note_marks[(kind, note_id)]:
                raise CertificationError(f"{kind} {note_id}: not the mark Word draws")
        for kind, notes in self.notes.items():
            theirs_by_id = {n["id"]: n for n in value[kind + "s"]}
            # Every note, once, in the order the body refers to them.
            order = [note for k, note in self.note_marks if k == kind]
            if [n["id"] for n in value[kind + "s"]] != order or set(order) != set(notes):
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
                never = (story["refusal"] or {}).get("code") == "never-shown"
                if never != (name not in self.shown):
                    raise CertificationError(f"{name}: shown or not, as the check finds, otherwise")
                if never:
                    if story["paragraphs"]:
                        raise CertificationError(f"{name}: never shown, yet with paragraphs")
                    not_read[name] = self.sizes[name]
                elif _refused(part, story, name):
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
                "floatingObjects": ledger.floating,
                "pageBreaks": ledger.page_breaks,
                "pageNumbers": ledger.page_numbers,
                "unreadObjects": ledger.objects,
                "unreadObjectCharacters": ledger.unread,
            },
            "symbolMapped": ledger.symbol,
            "marksChecked": sorted(CHECKED_MARKS),
            "notRead": dict(sorted(not_read.items())),
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


def _every_paragraph(value: dict[str, Json]) -> list[dict[str, Json]]:
    """Every paragraph of a .docx result: body, notes, headers, footers and comments."""
    out = list(value["paragraphs"])
    for kind in ("footnotes", "endnotes", "headers", "footers", "comments"):
        out += [p for part in value[kind] for p in part["paragraphs"]]
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
    """The characters ``root`` holds, to list what is not read.

    Those of every text element (``w:t``, DrawingML ``a:t``...) and chart value, and one for
    each run's element that stands for a character (a tab, a symbol, a picture...).
    """
    size = sum(
        len(node.text or "")
        for node in root.iter()
        if _local(node.tag) == "t" or node.tag == f"{{{_CHART}}}v"
    )
    return size + sum(1 for run in root.iter(f"{{{W}}}r") for c in run if _local(c.tag) in _STANDS)


# --- tracked changes ----------------------------------------------------------------------

# The changes a view drops whole; it keeps the others' content as content.
_VIEW_DROPS = {"accepted": ("del", "moveFrom"), "original": ("ins", "moveTo")}
# Every element that records a revision. None may remain in a view, in any part.
_REVISIONS = {
    _w(name)
    for name in (
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "delText",
        "delInstrText",
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
        "rPrChange",
        "pPrChange",
        "sectPrChange",
        "tblPrChange",
        "tblPrExChange",
        "tblGridChange",
        "trPrChange",
        "tcPrChange",
        "numberingChange",
        "cellIns",
        "cellDel",
        "cellMerge",
        "customXmlInsRangeStart",
        "customXmlInsRangeEnd",
        "customXmlDelRangeStart",
        "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart",
        "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart",
        "customXmlMoveToRangeEnd",
    )
}
_SAME_AS = {"delText": "t", "delInstrText": "instrText"}


type _Place = tuple[int, int, int] | None


class _Any:
    """Properties a change sets in the original view: held to Word, so anything agrees here."""


_ANY = _Any()
_CHANGES = {_w(name) for name in ("ins", "del", "moveFrom", "moveTo")}


def _agree(expected: Json, found: Json) -> bool:
    """Whether ``found`` is ``expected``, where ``_ANY`` in ``expected`` stands for anything."""
    if expected is _ANY:
        return True
    if isinstance(expected, (tuple, list)):
        return (
            isinstance(found, (tuple, list))
            and len(found) == len(expected)
            and all(_agree(a, b) for a, b in zip(expected, found, strict=True))
        )
    return bool(expected == found)


def _row_gone(row: ET.Element, dropping: set[str]) -> bool:
    properties = row.find(_w("trPr"))
    return properties is not None and any(c.tag in dropping for c in properties)


def _canon(element: ET.Element, drops: tuple[str, ...], outside: bool = False) -> Json:
    """``element`` as the view must hold it, as nested tuples: tag, attributes, text, children.

    Content in a change the view drops goes; in a change it keeps, it stays where it is; a kept
    change that marks a paragraph mark or a row goes, and so do the move ranges; a deleted text
    is a text. Properties holding a change of themselves are, in the accepted view, the current
    ones without it; in the original they are the former ones, held to Word (``_ANY``). With
    ``outside``, paragraphs are left out, and the rows the view drops, and a table whose every
    row it drops: what stands outside paragraphs.
    """
    dropping = {_w(name) for name in drops}
    if "del" not in drops and any(
        c.tag in _REVISIONS and _local(c.tag).endswith("Change") for c in element
    ):
        return _ANY
    children: list[Json] = []

    def add(parent: ET.Element) -> None:
        joining = False  # after a paragraph whose mark the view drops, its bookmark ends aside
        for child in parent:
            local = _local(child.tag)
            if outside and joining and child.tag == _w("bookmarkEnd"):
                continue  # in the view, after what is joined (``_run_tokens``)
            mark = child.find(f"{_w('pPr')}/{_w('rPr')}") if child.tag == _w("p") else None
            joining = mark is not None and any(c.tag in dropping for c in mark)
            if child.tag in dropping or (outside and child.tag == _w("p")):
                continue
            if outside and child.tag == _w("tr") and _row_gone(child, dropping):
                continue
            if outside and child.tag == _w("tbl"):
                rows = list(_within(child, _w("tr")))
                if rows and all(_row_gone(row, dropping) for row in rows):
                    continue
            if child.tag in _CHANGES:
                add(child)  # one marking a paragraph mark or a row holds nothing, and goes
                continue
            if child.tag in _REVISIONS and local not in _SAME_AS:
                if local in ("cellIns", "cellDel", "cellMerge"):
                    raise CertificationError(f"{local}, which the check does not apply")
                continue  # a property change (accepted), or a move range
            children.append(_canon(child, drops, outside))

    add(element)
    local = _local(element.tag)
    tag = _w(_SAME_AS[local]) if element.tag in _REVISIONS and local in _SAME_AS else element.tag
    return (tag, tuple(sorted(element.attrib.items())), element.text, tuple(children))


def _run_tokens(
    root: ET.Element, drops: tuple[str, ...]
) -> tuple[list[tuple[_Place, list[tuple[Json, ...]], Json]], int]:
    """Each paragraph's place, content and own make, in order, as the view must hold it; joins.

    A paragraph's place is its outermost table cell (table, row, cell, counted from 0) or None.
    A paragraph whose mark the view drops is joined to the next in document order (inside a
    table, its first cell's first paragraph): its tokens open the next one's. A row the view
    drops goes whole, and a table whose every row it drops goes with them; a table with no row
    at all is still a table. Content inside a change the view drops is left out; a deleted text
    is a text.

    A token is a run's element (kind, text, attributes, and in full: the elements it stands in
    within its paragraph, its run among them, its run's properties, and the element), or
    anything else in the paragraph with no children (kind None). A paragraph's make is the
    elements it stands in, its attributes and its properties (``_canon``).
    """
    dropping = {_w(d) for d in drops}
    with_field = _field_runs(root, dropping)
    paragraphs: list[tuple[_Place, list[tuple[Json, ...]], Json]] = []
    carried: list[tuple[Json, ...]] = []
    joining = False  # a paragraph is being joined to the next (an empty one carries nothing)
    joins = tables = 0

    def signature(element: ET.Element) -> Json:
        return (element.tag, tuple(sorted(element.attrib.items())))

    def table(
        element: ET.Element, dropped: bool, mine: list[tuple[Json, ...]], chain: Json
    ) -> None:
        nonlocal tables
        rows = list(_within(element, _w("tr")))
        kept = [row for row in rows if not _row_gone(row, dropping)]
        if rows and not kept:
            if carried:
                # Word moves it into the table, leaving the rows' changes: no view of Word's.
                raise CertificationError("a joined paragraph's content meets a table dropped")
            return  # gone whole: an empty joined paragraph goes on to the paragraph after it
        if joining and rows and _row_gone(rows[0], dropping):
            raise CertificationError("a joined paragraph meets a row the view drops")
        index, tables = tables, tables + 1
        for r, row in enumerate(kept):
            for c, cell in enumerate(_within(row, _w("tc"))):
                inner = (*chain, signature(element), signature(row), signature(cell))
                walk(cell, dropped, mine, (index, r, c), inner, False)

    def walk(
        element: ET.Element,
        dropped: bool,
        mine: list[tuple[Json, ...]],
        place: _Place,
        chain: Json,
        inline: bool,
    ) -> None:
        nonlocal carried, joining, joins
        for child in element:
            local = _local(child.tag)
            if child.tag == _w("p"):
                own: list[tuple[Json, ...]] = carried
                carried, joining = [], False
                walk(child, dropped, own, place, (), True)
                mark = child.find(f"{_w('pPr')}/{_w('rPr')}")
                if mark is not None and any(c.tag in dropping for c in mark):
                    carried, joining = own, True
                    joins += 1
                else:
                    properties = child.find(_w("pPr"))
                    make = (
                        chain,
                        signature(child),
                        None if properties is None else _canon(properties, drops),
                    )
                    paragraphs.append((place, own, make))
                continue
            if child.tag == _w("tbl") and place is None:
                table(child, dropped, mine, chain)
                continue
            if joining and not inline and child.tag == _w("bookmarkEnd") and not len(child):
                # Between a paragraph joined and the next: in the view, after what is joined.
                carried.append((None, "", [], ((), None, _canon(child, drops))))
                continue
            if child.tag == _w("tr") and _row_gone(child, dropping):
                if joining:
                    raise CertificationError("a joined paragraph meets a row the view drops")
                continue  # a row of a nested table the view drops goes whole too
            if element.tag == _w("r"):
                # Its properties stand in each of its tokens.
                if child.tag != _w("rPr") and not dropped:
                    properties = element.find(_w("rPr"))
                    full = (
                        chain,
                        None if properties is None else _canon(properties, drops),
                        _canon(child, drops),
                    )
                    attributes = sorted(child.attrib.items())
                    mine.append((_SAME_AS.get(local, local), child.text or "", attributes, full))
                continue
            if inline and child.tag == _w("pPr"):
                continue  # the paragraph's make
            if inline and not len(child) and not dropped and child.tag not in _REVISIONS:
                mine.append((None, "", [], (chain, None, _canon(child, drops))))
                continue
            gone = (
                dropped
                or (child.tag in dropping and element.tag != _w("rPr"))
                or child in with_field
            )
            inner = chain if child.tag in _CHANGES else (*chain, signature(child))
            walk(child, gone, mine, place, inner, inline)

    walk(root, False, [], None, (), False)
    if joining:
        raise CertificationError("a joined paragraph has no paragraph after it")
    return paragraphs, joins


def _field_runs(root: ET.Element, dropping: set[str]) -> set[ET.Element]:
    """The runs a view drops with their field; ``CertificationError`` for a field kept in part.

    By its own reading of Word's rule (not the reader's): a view keeps every mark of a field
    (begin, separator, end) or drops every one, and a field it drops goes with every run inside
    it, those its changes keep too, where it stands in no other field, holds none the view
    keeps and ends in the paragraph it begins in. Anything else is never certified.
    """
    found: list[tuple[ET.Element, bool, ET.Element | None]] = []

    def collect(element: ET.Element, dropped: bool, paragraph: ET.Element | None) -> None:
        for child in element:
            gone = dropped or (child.tag in dropping and element.tag != _w("rPr"))
            if child.tag in (_w("fldChar"), _w("r")):
                found.append((child, gone, paragraph))
            collect(child, gone, child if child.tag == _w("p") else paragraph)

    collect(root, False, None)
    # Each field as the places of its begin, its separator (or none) and its end in ``found``.
    fields: list[tuple[int, int | None, int]] = []
    opened: list[tuple[int, list[int]]] = []  # each open field's begin, and its separator
    for at, (node, _, _) in enumerate(found):
        kind = node.get(_w("fldCharType")) if node.tag == _w("fldChar") else None
        if kind == "begin":
            opened.append((at, []))
        elif kind == "separate" and opened and not opened[-1][1]:
            opened[-1][1].append(at)
        elif kind == "end" and opened:
            start, separators = opened.pop()
            fields.append((start, separators[0] if separators else None, at))
    out: set[ET.Element] = set()
    for begin, separator, end in fields:
        fates = {found[at][1] for at in (begin, separator, end) if at is not None}
        if len(fates) > 1:
            raise CertificationError("a view keeps part of a field")
        inside = found[begin + 1 : end]
        kept = [
            node
            for node, gone, _ in inside
            if node.tag == _w("r") and not gone and any(_local(c.tag) != "rPr" for c in node)
        ]
        if fates != {True} or not kept:
            continue
        if (
            any(b < begin < e for b, _, e in fields)
            or found[begin][2] is not found[end][2]
            or any(node.tag == _w("fldChar") and not gone for node, gone, _ in inside)
        ):
            raise CertificationError("a view drops a field whose content is not on record")
        out.update(kept)
    return out


def _within(element: ET.Element, tag: str) -> Iterator[ET.Element]:
    """The ``tag`` elements of ``element``, through any wrapper but never inside one found.

    A table's rows and a row's cells: a nested table stands inside a cell, which is never
    entered here.
    """
    for child in element:
        if child.tag == tag:
            yield child
        else:
            yield from _within(child, tag)


def certify_tracked(source: bytes, views: dict[str, bytes]) -> dict[str, Json]:
    """The account of a document's two views, or ``CertificationError``.

    Each view must be the source package with only the parts holding revisions written again,
    with no revision left anywhere, and each part's paragraphs must hold the source's run
    content as the view keeps it: every token, in order, with paragraphs joined only where the
    view drops a paragraph mark, and to the paragraph after. A footnote or endnote whose every
    reference in the body the view drops is gone from the view's notes, and only such a note.
    """
    out: dict[str, Json] = {"checker": CHECKER_VERSION}
    with zipfile.ZipFile(io.BytesIO(source)) as original:
        names = original.namelist()
        roots = {
            name: ET.fromstring(original.read(name)) for name in names if name.endswith(".xml")
        }
        revised = {
            name: any(e.tag in _REVISIONS for e in root.iter()) for name, root in roots.items()
        }
        for view, data in views.items():
            characters = elements = joined = 0
            gone = _notes_gone(roots, _VIEW_DROPS[view])
            with zipfile.ZipFile(io.BytesIO(data)) as copy:
                if copy.namelist() != names:
                    raise CertificationError(f"the {view} view's parts are not the document's")
                for name in names:
                    before, after = original.read(name), copy.read(name)
                    if not name.endswith(".xml"):
                        if before != after:
                            raise CertificationError(f"{view} view: {name} is changed")
                        continue
                    dropped = gone.get(roots[name].tag, set())
                    if before == after and not dropped and not revised[name]:
                        # The source's part as it is, with no revision and no note to drop.
                        continue
                    source_root, view_root = ET.fromstring(before), ET.fromstring(after)
                    if any(e.tag in _REVISIONS for e in view_root.iter()):
                        raise CertificationError(f"{view} view: a revision is left in {name}")
                    for note in [n for n in source_root if n.get(_w("id")) in dropped]:
                        source_root.remove(note)
                    if not dropped and not any(e.tag in _REVISIONS for e in source_root.iter()):
                        # Not the source's bytes (those are passed over above), yet nothing to undo.
                        raise CertificationError(f"{view} view: {name} is changed")
                    if source_root.tag != _w("glossaryDocument") and any(
                        e.tag in _REVISIONS
                        for run in source_root.iter(_w("r"))
                        for drawing in run
                        if _local(drawing.tag) in ("drawing", "pict", "AlternateContent")
                        for e in drawing.iter()
                    ):
                        # A floating object's text is in neither view, its change only listed.
                        raise CertificationError(f"{view} view: a revision inside a drawing")
                    drops = _VIEW_DROPS[view]
                    expected, joins = _run_tokens(source_root, drops)
                    # Every paragraph's content and make, and everything outside paragraphs.
                    if not _agree(expected, _run_tokens(view_root, ())[0]) or not _agree(
                        _canon(source_root, drops, outside=True), _canon(view_root, (), True)
                    ):
                        raise CertificationError(f"{view} view: {name} does not hold its content")
                    joined += joins
                    tokens = [token for _, p, _ in expected for token in p if token[0] is not None]
                    # Counted so that a run split in two counts the same: its characters, and
                    # the run content that is not text (tabs, breaks, symbols, field marks...).
                    characters += sum(len(str(token[1])) for token in tokens)
                    elements += sum(1 for token in tokens if token[0] not in ("t", "instrText"))
            out[view] = {"characters": characters, "elements": elements, "paragraphsJoined": joined}
    return out


def _notes_gone(roots: dict[str, ET.Element], drops: tuple[str, ...]) -> dict[str, set[str]]:
    """The notes a view drops, by notes part: those whose every body reference it drops."""
    body = next(root for root in roots.values() if root.tag == _w("document"))
    kept = {
        (token[0], value)
        for _, tokens, _ in _run_tokens(body, drops)[0]
        for token in tokens
        for key, value in token[2]
        if key == _w("id")
    }
    out: dict[str, set[str]] = {}
    for kind in ("footnote", "endnote"):
        referred = {e.get(_w("id"), "") for e in body.iter(_w(f"{kind}Reference"))}
        out[_w(f"{kind}s")] = {i for i in referred if (f"{kind}Reference", i) not in kept}
    return out


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
        bundle = json.loads(
            data.decode("utf-8"), object_pairs_hook=_one_value, parse_constant=_no_constant
        )
        compositions = [
            e["resource"]
            for e in bundle["entry"]
            if isinstance(e.get("resource"), dict) and "section" in e["resource"]
        ]
        if len(compositions) != 1:
            raise CertificationError("not one Composition with sections")
        if bundle["entry"][0].get("resource") is not compositions[0]:
            raise CertificationError("the Composition is not the first entry")
        self.sections = [self._section(raw) for raw in compositions[0]["section"]]
        self.narratives = _narratives(bundle)

    def _section(self, raw: dict[str, Json]) -> _EpiSection:
        div = raw.get("text", {}).get("div")
        parsed = None
        if isinstance(div, str):
            parsed = _Div()
            # html.unescape reads no more than 4300 decimal digits: leading zeros go first.
            parsed.feed(re.sub(r"&#0+(?=[0-9])", "&#", div))
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


def _one_value(pairs: list[tuple[str, Json]]) -> dict[str, Json]:
    if len({name for name, _ in pairs}) != len(pairs):
        raise CertificationError("a name repeated in an object")
    return dict(pairs)


def _no_constant(constant: str) -> None:
    raise CertificationError(f"{constant} is not JSON")


def _narratives(bundle: Json) -> int:
    """The strings named ``div`` outside any ``section``, counted without recursion."""
    count = 0
    stack: list[tuple[Json, str | None, bool]] = [(bundle, None, False)]
    while stack:
        value, name, in_section = stack.pop()
        if isinstance(value, dict):
            stack += [(item, key, in_section or key == "section") for key, item in value.items()]
        elif isinstance(value, list):
            stack += [(item, name, in_section) for item in value]
        elif isinstance(value, str) and name == "div" and not in_section:
            count += 1
    return count
