r"""A fail-closed reader for the text of a Word (.docx) body.

The reader turns the main document part into paragraphs of text, and it refuses a document whose
text it cannot produce exactly. Refusing is the point: a reader that keeps going when it meets
something it does not understand is how a label loses a character without anyone noticing. The
EMA's own QRD files show why. Appendix II writes the greater-than-or-equal sign of "Very common
(>= 1/10)" as a Symbol-font glyph (``<w:sym w:font="Symbol" w:char="F0B3"/>``), and Appendix III
writes the degree sign of "25 degrees C" the same way; a reader that collects only ``<w:t>`` text
returns "( 1/10)" and "25 C" and reports nothing. ``tests/test_reader.py`` reads those files.

Every character in the output has a source in the document, and every text-bearing node of the
main document part is accounted for: each run is read by the reader exactly once, run content
(``<w:t>``, ``<w:sym>``, a break...) stands only inside a run, and no character data stands
outside ``<w:t>`` and ``<w:instrText>``. A document where any of that fails is refused, so no text
the document holds can be passed over in silence.

What a paragraph carries:

- ``text``: the characters as stored. ``<w:t>`` text is copied as is; nothing is normalised,
  straightened or trimmed. ``<w:tab/>`` and ``<w:ptab/>`` are U+0009; ``<w:br/>`` and
  ``<w:cr/>`` are U+000A, except a page or column break, which is layout and emits nothing;
  ``<w:noBreakHyphen/>`` is U+2011, ``<w:softHyphen/>`` U+00AD, and a picture is U+FFFC OBJECT
  REPLACEMENT CHARACTER at the place it stands, whether it is DrawingML (``w:drawing``) or VML
  (``w:pict``, as documents from before Word 2007 hold it).
- ``marks``: ranges of ``text`` whose appearance changes what a reader sees or means, set on
  the run, its styles or the document defaults (``Mark`` lists the kinds): superscript,
  subscript, raised or lowered text, capitals and small capitals, single and double
  strike-through, highlight with its colour, shading with its fill (or its pattern, colour and
  fill) on the run or the paragraph, right-to-left, and faint text (white or a light theme
  colour, under two points in any unit, or scaled under a fifth), and underline of any style
  (an underlined "<" is how "≤" is often typed). ``text`` alone flattens "10" with a
  superscript "9" to "109"; a caller that uses ``text`` must look at ``marks``. Other
  appearance (other colours, font size, bold, italic, borders) is not reported.
- ``mark_hidden``: the paragraph mark is hidden (``vanish`` or ``specVanish``, directly or
  through the paragraph's styles), so Word shows this paragraph run on into the next one.
- ``numbering``: the list the paragraph belongs to, directly or through its style, and the list
  label Word draws before it (``Numbering.text``, with ``suffix`` naming what separates it from
  the paragraph). The label is computed, not stored, so it is never put into ``text``; see
  "List labels" below.
- ``table``: ``(table, row, cell)`` counted from zero in document order, else ``None``. A nested
  table's paragraphs carry the outermost cell; cells are counted as ``<w:tc>`` elements, not
  grid columns.
- ``pages``: where in ``text`` Word draws a page number (a table of contents' page, a PAGE
  field). Word sets it from the layout when it prints, so it is never in ``text``.
- ``notes``: the footnote and endnote marks in the paragraph (``NoteReference``): where each
  stands in ``text``, which note it refers to, and the mark Word draws there. Like a list label,
  the mark is computed and never put into ``text``; see "Notes" below.

Styles. Run properties are looked up on the run, then its character style, its paragraph
style, its table style (inside a table only) and the document defaults, each style with its
``basedOn`` chain. An absent or unknown style id falls back to the document's default style of
that kind (the last one marked default), as Word does; a reference to a style of another kind
is refused. Paragraph shading and right-to-left are looked up the same way through the
paragraph properties. The reader does not apply a table style's conditional formatting
(``tblStylePr`` for the first row, banded rows and so on), so it refuses a table whose style's
conditional formatting could change what it produces, and reads one whose conditional formatting
sets only what it cannot change: properties the reader does not report (bold, italic, spacing,
borders, cell shading) and fonts, sizes and colours that are ordinary text. Under such
formatting, Symbol text is refused, since a conditional font could replace the Symbol font.

Symbol fonts. A run whose effective ``ascii`` and ``hAnsi`` fonts (set directly, by a style, by
the document defaults or through the theme) are both Symbol, with no complex-script or
right-to-left property and no font hint other than ``default`` (which sends ambiguous characters
to the ``hAnsi`` font, Symbol here), has every character mapped through ``SYMBOL_FONT``; a
character the table does not hold is refused. ``<w:sym>`` in the Symbol font is mapped the same
way. Any other run with Symbol in one of its four font slots is refused, because Word picks the
font per character and the reader cannot be sure which characters it draws in Symbol. A dingbat
font (Wingdings, Webdings, Zapf Dingbats, Marlett, MT Extra), or any font the document's font
table declares symbol-encoded (charset 02), is refused.

Fields keep their stored result and drop their instruction, however deeply nested, so ``DOCPROPERTY
... MERGEFORMAT`` never reaches the text. Fields whose stored result is what Word shows and prints
are read: HYPERLINK, DOCPROPERTY and TOC (a table of contents, whose entries Word prints as stored
until someone updates it). Page numbers (PAGEREF, as in a table of contents' entries, PAGE,
NUMPAGES, SECTIONPAGES) Word sets from the page layout when it prints; their stored text is left out
of ``text`` and their place recorded in ``pages``. SEQ (caption numbers), STYLEREF (a heading's
number or text), REF (a cross-reference: a bookmark's text) and NOTEREF (the mark of the note a
bookmark holds) Word shows as stored but recomputes when it prints or saves as PDF, so the reader
computes them as Word does and reads them only where the stored result is the computed one;
otherwise screen and print disagree, and the document is refused (``stale-field``). A REF or NOTEREF
to a bookmark that is not there (Word prints an error), that runs across paragraphs, or over a note
mark (REF) is refused. SEQ counts each identifier in document order: one more than the last, ``\r``
n sets the count, ``\c`` repeats it, ``\h`` counts and shows nothing, ``\s`` n restarts it after any
paragraph in a built-in style "heading 1" to "heading n" (Word goes by the style's name, not its
outline level), and ``\*`` shows it in ARABIC, ROMAN, roman, ALPHABETIC or alphabetic. STYLEREF
finds the nearest paragraph of the style (a number n is "heading n") before the field, else after
it, and shows its text, or with ``\s`` its list label without the final period. Each rule is Word's
answer to a case in ``corpus/numbering-cases``. Other switches, a SEQ or STYLEREF in a note or
nested in another field's code, and a result that runs past its paragraph are refused. Any other
field whose result would be shown (DATE, IF, a formula...) is refused, because Word recomputes it on
display or print. The code is the first word of the instruction; a field nested in the instruction
ahead of or inside that word makes the code unknown, and the field is refused. So are a field with
no stored result (no ``separate``, such as a form checkbox or a SYMBOL field, or an empty
``fldSimple``), a form field, a field marked for update, any field in a document whose settings ask
Word to update fields on open, and field code outside an instruction.

List labels. Word draws "4.8", "b)" or a bullet before a numbered paragraph from the numbering
part; the reader computes that label by Word's rules, each of which is Word's own answer to a case
in ``corpus/numbering-cases`` (``word.json``; ``tests/test_word_oracle.py``). A paragraph's
``numId`` names a ``w:num``, which names an ``abstractNum``; a level of the ``w:num``'s
``lvlOverride`` replaces the abstract level's look (format, text, font), not its start. An
``abstractNum`` with a ``numStyleLink`` takes its levels from the one the numbering style names,
which must name the style back (``styleLink``). Counters belong to the ``abstractNum`` the
``w:num`` names: every list naming it shares them, so a second list continues the first. A
paragraph at level ``L`` restarts every deeper level (``lvlRestart`` 0 never restarts it;
``lvlRestart`` ``n`` restarts it only after a level up to ``n - 1``), counts every higher level
not yet counted as that level's start, and counts its own level: its list's ``startOverride``
the first time that list reaches the level, else one more than the shared count, else (after a
restart) the list's ``startOverride`` or the ``abstractNum`` level's ``w:start``, 0 when there is
none. ``lvlText`` is copied, with ``%1`` to ``%9`` replaced by the counter of that level in that
level's format (all decimal under ``isLgl``): decimal, decimalZero, upper and lower roman (1 to
3999), upper and lower letter (a to z, then aa, bb...), or none; a bullet level's text is its
bullet. The label is drawn in the level's run properties over the paragraph mark's, so its fonts
are placed as a run's are: a Symbol bullet (U+F0B7) is mapped to "•", a Wingdings one is
refused. ``suffix`` is ``tab``, ``space`` or ``nothing`` (``w:suff``), or ``legacy`` for a Word 6
level, where the gap is layout and not a character.

Notes. ``read_document`` returns the footnotes and endnotes with the body, each note's
paragraphs read by every rule above, in the order the body refers to them; ``read_docx``
returns the body alone. A note's mark is its section's ``numStart`` plus the number of notes of
its kind before it, in the document or, where the section restarts them (``numRestart``
``eachSect``), in the section; it is drawn in the section's format: decimal, roman, letters, or
symbols (``chicago``: *, †, ‡, §, then each doubled...). The section's ``footnotePr`` and
``endnotePr`` decide this; Word ignores the settings part's. Footnotes default to decimal,
endnotes to lower roman, and each kind counts apart. A note with a custom mark takes no number;
its mark is the stored text that follows the reference. Every rule is Word's answer to a case in
``corpus/numbering-cases``, held by ``tests/test_word_oracle.py``. Every note must be referred
to exactly once, and every reference must name a note.

What it refuses (``DocxRefusedError.code``):

- ``tracked-change``: any revision anywhere in the body, including changed formatting and
  deleted paragraph marks. Such a document has more than one text.
- ``hidden-text``: a run with text, or a note mark, that is hidden, directly or at any level of
  the style hierarchy (hiding is treated as a fact as soon as any level asserts it, unless the
  run itself says it is visible).
- ``unmapped-symbol``: a Symbol-font code the table does not hold, a malformed code, or a symbol
  in any other font.
- ``symbol-font``: text in a dingbat font, or a Symbol font the reader cannot place.
- ``private-use-character``: a private-use code point outside a Symbol-font run.
- ``reserved-character``: U+FFFC in ``<w:t>``, which the reader uses for a picture.
- ``unpreserved-whitespace``: ``<w:t>`` text with leading or trailing spaces without
  ``xml:space="preserve"`` (a consumer may drop them), or a tab or line break inside ``<w:t>``
  (Word writes those as elements).
- ``unbalanced-field``: a paragraph that ends inside a field instruction.
- ``field-without-result``: a field with no stored result.
- ``computed-field``: a shown field whose value Word computes rather than stores, or any field
  in a document set to update fields on open.
- ``stale-field``: a field marked for update, or a SEQ or STYLEREF field whose stored result is
  not what Word prints.
- ``unsupported-element``: anything that can carry text and is not read above, and any element
  the reader does not know: text boxes, a note mark in a field code or inside a note, a note's
  echo of its mark outside that note, embedded objects, charts and other non-picture drawings,
  alternate content, math, ``altChunk``, form fields,
  content controls bound to data (in any namespace), VML that is not a picture, conditional
  table formatting that could change the text, text in a vertically merged-away cell, and a
  style reference that names a style of another kind.
- ``invalid-package``: not a readable .docx (a PDF or a Word 97-2003 document is named as one),
  no main document relationship, a part name that
  occurs twice (ignoring case), a related part that is missing or duplicated, a part that
  cannot be read (bad checksum, truncated, encrypted), a part that is not UTF-8 or declares
  another encoding, a DTD, a part over the size cap, a style id defined twice, a list number
  that is not a number, a note defined twice, referred to twice, or referred to and not there.
- ``stray-text``: character data in the main document part outside ``<w:t>`` and
  ``<w:instrText>`` (whitespace between elements aside), or an element inside either of them.
- ``unread-content``: a run the reader did not reach (inside section, paragraph or cell
  properties, say), run content standing outside a run, or a note nothing refers to (Word does
  not show it; its text is in the file all the same).
- ``unsupported-numbering``: a list label the reader cannot draw exactly: a ``numId`` or level
  with no definition (or no numbering part), a level outside 0 to 8, a format other than those
  above (ordinal and text formats depend on the language), a custom format, a picture bullet, a
  level holding anything else the reader does not know (alternate content, say), a ``%n`` for a
  deeper level, a bullet level that shows a counter, a number past a format's range, a label in
  capitals or small capitals with letters in it, a numbering-style link the reader cannot
  follow (no ``styleLink`` back, or to a list with overrides), a list in a note, or a note
  number format other than those above.
- ``ambiguous-numbering``: a label drawn hidden (the paragraph mark or the level is hidden) or a
  numbered paragraph run on after a hidden paragraph mark, for which Word's list API reports a
  label but not whether or where it is drawn; a label that shows a level whose start the
  reader cannot find (only a ``lvlOverride`` defines it); note numbers that restart on each page,
  which depends on layout; or the echo of a custom mark inside its note, where Word draws the
  number the next note will take.

Headers, footers, comments and the glossary are separate parts and are not read.
"""

from __future__ import annotations

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace

# The version of the rules above. A change to this file changes its hash in versions.lock.json,
# and tests/test_locks.py then requires a new version here. 1.1.0 is the reader as imported
# (README, "Origin"); 1.2.0 adds stray-text and unread-content; 1.3.0 draws list labels and
# accepts the font hint "default"; 1.4.0 counts lists by the rules Word showed
# (corpus/numbering-cases/word.json); 1.5.0 reads what public regulator templates hold and
# 1.4.0 refused: VML pictures, smart-tag and custom-XML properties, and conditional table
# formatting that cannot change the text; 1.6.0 reads footnotes and endnotes, with their marks
# by the rules Word showed; 1.7.0 reads SEQ and STYLEREF fields whose stored result is what Word
# prints; 1.8.0 names a PDF in its refusal; 1.9.0 computes REF and NOTEREF, which Word reprints,
# and refuses them where the stored result is not what it prints; 1.10.0 reads tables of
# contents, which Word prints as stored, and places page numbers, which it sets from the layout.
READER_VERSION = "docx-reader/1.10.0"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
PICTURE_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"
OBJECT = "\ufffc"

MAX_PART_BYTES = 20 * 1024 * 1024


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# Adobe's Symbol encoding as the Unicode Consortium maps it (MAPPINGS/VENDORS/ADOBE/symbol.txt),
# limited to the codes a product-information text can be expected to use. Word stores a Symbol
# glyph either at its code or at U+F000 plus its code. The table is closed on purpose: a code
# missing here is refused, and adding one is a reviewed change with a test. Where symbol.txt
# gives two Unicode characters for one code, the choice is noted.
SYMBOL_FONT: dict[int, str] = {
    0x20: " ",  # symbol.txt also gives U+00A0; the ordinary space is taken
    0x21: "!",
    0x23: "#",
    0x25: "%",
    0x26: "&",
    0x28: "(",
    0x29: ")",
    0x2B: "+",
    0x2C: ",",
    0x2D: "\u2212",  # MINUS SIGN
    0x2E: ".",
    0x2F: "/",
    **{code: chr(code) for code in range(0x30, 0x3A)},  # digits
    0x3A: ":",
    0x3B: ";",
    0x3C: "<",
    0x3D: "=",
    0x3E: ">",
    0x3F: "?",
    0x5B: "[",
    0x5D: "]",
    0x5F: "_",
    0x61: "\u03b1",  # GREEK SMALL LETTER ALPHA
    0x62: "\u03b2",  # GREEK SMALL LETTER BETA
    0x64: "\u03b4",  # GREEK SMALL LETTER DELTA
    0x67: "\u03b3",  # GREEK SMALL LETTER GAMMA
    0x6D: "\u03bc",  # GREEK SMALL LETTER MU; symbol.txt also gives U+00B5 MICRO SIGN
    0x7B: "{",
    0x7C: "|",
    0x7D: "}",
    0xA3: "\u2264",  # LESS-THAN OR EQUAL TO
    0xA5: "\u221e",  # INFINITY
    0xAE: "\u2192",  # RIGHTWARDS ARROW
    0xB0: "\u00b0",  # DEGREE SIGN
    0xB1: "\u00b1",  # PLUS-MINUS SIGN
    0xB3: "\u2265",  # GREATER-THAN OR EQUAL TO
    0xB4: "\u00d7",  # MULTIPLICATION SIGN
    0xB7: "\u2022",  # BULLET
    0xB9: "\u2260",  # NOT EQUAL TO
    0xBB: "\u2248",  # ALMOST EQUAL TO
}

_DINGBAT_FONTS = ("wingdings", "webdings", "dingbat", "marlett", "mtextra")

_TRACKED = {
    _w(name)
    for name in (
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "delText",
        "delInstrText",
        "moveFromRangeStart",
        "moveToRangeStart",
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
        "customXmlDelRangeStart",
        "customXmlMoveFromRangeStart",
        "customXmlMoveToRangeStart",
    )
}
# Markers that carry no text, allowed wherever they occur.
_MARKERS = {
    _w(name)
    for name in (
        "bookmarkStart",
        "bookmarkEnd",
        "commentRangeStart",
        "commentRangeEnd",
        "permStart",
        "permEnd",
        "proofErr",
    )
}
# Run children that carry no text of their own. A comment reference points at a comment, which
# is not part of the body text Word lays out.
_RUN_SILENT = {_w(name) for name in ("rPr", "lastRenderedPageBreak", "commentReference")}
# A note's mark in the body, and its echo at the start of the note's text.
_NOTE_REFERENCES = {
    _w(name) for name in ("footnoteReference", "endnoteReference", "footnoteRef", "endnoteRef")
}
# Paragraph-level containers whose children are read as the paragraph's own. fldSimple's
# children are the field's displayed result; its instruction is an attribute and is dropped.
_INLINE_TRANSPARENT = {_w(name) for name in ("hyperlink", "smartTag", "customXml")}
# Properties of a paragraph, a content control, a smart tag or custom XML: no text of their own.
_PROPERTIES = {_w(name) for name in ("pPr", "sdtPr", "smartTagPr", "customXmlPr")}
# The elements whose character data is read. Character data in any other element of the main
# document part is refused rather than passed over.
_TEXT_ELEMENTS = {_w("t"), _w("instrText")}
_XML_WHITESPACE = " \t\r\n"
# What a run holds that stands for characters or a field. Each must be the child of a run, and
# every run must be one the reader read. <w:tab> is left out: it also names a tab stop in the
# paragraph properties, and a run's tab is covered by the run being read.
_RUN_CONTENT = {
    _w(name)
    for name in (
        "t",
        "instrText",
        "sym",
        "br",
        "cr",
        "ptab",
        "noBreakHyphen",
        "softHyphen",
        "drawing",
        "fldChar",
        "footnoteReference",
        "endnoteReference",
        "footnoteRef",
        "endnoteRef",
    )
}

_TOGGLE_MARKS = ("caps", "smallCaps", "strike", "dstrike")


class DocxRefusedError(Exception):
    """The document holds something whose text the reader cannot produce exactly."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Numbering:
    """The list a paragraph belongs to. ``num_id`` 0 means "not in a list".

    ``text`` is the label Word draws before the paragraph ("4.8.", "b)", "•", or "" for a level
    that shows nothing) and ``suffix`` what follows it: ``tab``, ``space``, ``nothing`` or
    ``legacy``. Both are None when ``num_id`` is 0.
    """

    num_id: int
    level: int
    text: str | None = None
    suffix: str | None = None


@dataclass(frozen=True)
class Mark:
    """``text[start:end]`` is shown as ``kind``.

    One of superscript, subscript, position, caps, smallCaps, strike, dstrike,
    ``highlight-<colour>`` (Word's colour name, e.g. ``highlight-lightGray``),
    ``shading-<FILL>`` (e.g. ``shading-D9D9D9``) or ``shading-<pattern>-<COLOUR>-<FILL>``, rtl
    (right-to-left), faint (white or a light theme colour, under two points, or scaled
    under a fifth) and underline. Marks of one
    kind that touch are merged; marks of different kinds may overlap.
    """

    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class NoteReference:
    """A footnote or endnote mark in a paragraph: Word draws ``mark`` before ``text[offset]``.

    ``kind`` is ``footnote`` or ``endnote`` and ``id`` the note's id. ``mark`` is the number or
    symbol Word draws ("1", "iv", "*"), or None for a note with a custom mark, whose characters
    are stored, and so read, in ``text``. The mark is computed, not stored, so it is never put
    into ``text``. A note's own text repeats its mark where the note's ``footnoteRef`` or
    ``endnoteRef`` stands, and that paragraph carries a reference to the note itself.
    """

    offset: int
    kind: str
    id: int
    mark: str | None = None


@dataclass(frozen=True)
class Paragraph:
    """One paragraph of the body or of a note, as the reader produced it.

    The module docstring describes ``text``, ``marks``, ``mark_hidden``, ``numbering``,
    ``table`` and ``notes``; ``style`` is the paragraph style id written on the paragraph, if
    any.
    """

    text: str
    style: str | None
    numbering: Numbering | None
    table: tuple[int, int, int] | None
    marks: tuple[Mark, ...] = ()
    mark_hidden: bool = False
    notes: tuple[NoteReference, ...] = ()
    # Where Word draws a page number (PAGEREF, PAGE...): set by the layout, so never in ``text``.
    pages: tuple[int, ...] = ()

    @property
    def has_drawing(self) -> bool:
        """Whether the text holds a picture (U+FFFC OBJECT REPLACEMENT CHARACTER)."""
        return OBJECT in self.text


@dataclass(frozen=True)
class Note:
    """A footnote or endnote: its id, the mark its references draw, and its paragraphs."""

    kind: str
    id: int
    mark: str | None
    paragraphs: tuple[Paragraph, ...]


@dataclass(frozen=True)
class Document:
    """The body's paragraphs, and the footnotes and endnotes in the order they are referenced."""

    body: tuple[Paragraph, ...]
    footnotes: tuple[Note, ...] = ()
    endnotes: tuple[Note, ...] = ()


# --- package -------------------------------------------------------------------------------


def _decode(name: str, data: bytes) -> bytes:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise DocxRefusedError("invalid-package", f"{name} is not UTF-8")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocxRefusedError("invalid-package", f"{name} is not UTF-8") from error
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise DocxRefusedError("invalid-package", f"{name} declares a DTD")
    declared = re.match(r"\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']*)[\"']", text)
    if declared is not None and declared.group(1).lower().replace("_", "-") not in (
        "utf-8",
        "utf8",
    ):
        # The parser would honour the declaration and decode the UTF-8 bytes as something else.
        raise DocxRefusedError("invalid-package", f"{name} declares {declared.group(1)}")
    return text.encode("utf-8")


_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_LOCAL_HEADER = b"PK\x03\x04"
_END_RECORD = b"PK\x05\x06"


def _whole_archive(data: bytes) -> None:
    """Refuse data that is not one zip archive from its first byte to its last.

    zipfile opens an archive found anywhere in the data: it skips bytes before it and does not
    look past its end record. A Word 97-2003 document (.doc, an OLE compound file) holds a small
    zip of its theme, and one holding an embedded .docx would be read as that other document.
    EMA's own site serves a .doc under a .docx name.
    """
    if data.startswith(_OLE):
        raise DocxRefusedError("invalid-package", "a Word 97-2003 document (.doc), not a .docx")
    if data.lstrip(b"\x00\t\n\r ")[:5] == b"%PDF-":
        # A PDF holds glyphs placed on a page, not the text and structure Word holds: what it
        # shows can be drawn from a font with no record of the characters meant.
        raise DocxRefusedError(
            "invalid-package", "a PDF, not a .docx: its text cannot be read exactly"
        )
    if not data.startswith(_LOCAL_HEADER):
        raise DocxRefusedError("invalid-package", "not a zip archive from its first byte")
    end = data.rfind(_END_RECORD)
    comment = int.from_bytes(data[end + 20 : end + 22], "little") if end >= 0 else 0
    if end < 0 or len(data) != end + 22 + comment:
        raise DocxRefusedError("invalid-package", "bytes after the zip archive's end record")


class _Package:
    def __init__(self, data: bytes) -> None:
        _whole_archive(data)
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except Exception as error:  # zipfile raises many types for a damaged archive
            raise DocxRefusedError("invalid-package", "not a readable zip archive") from error
        if min((info.header_offset for info in self.zip.infolist()), default=0) != 0:
            # The central directory places the first part after the start of the data.
            raise DocxRefusedError("invalid-package", "bytes before the zip archive")
        names = self.zip.namelist()
        # Part names in a package are compared without regard to case (ECMA-376 Part 2).
        if len({name.lower() for name in names}) != len(names):
            raise DocxRefusedError("invalid-package", "a part name occurs twice")
        self.names = set(names)

    def part(self, name: str) -> ET.Element | None:
        if name not in self.names:
            return None
        info = self.zip.getinfo(name)
        if info.file_size > MAX_PART_BYTES:
            raise DocxRefusedError("invalid-package", f"{name} is over {MAX_PART_BYTES} bytes")
        try:
            data = self.zip.read(name)
        except Exception as error:
            # A bad checksum, a truncated or encrypted entry, an unsupported compression, or a
            # damaged directory: zipfile raises many types, and every one is a refusal.
            raise DocxRefusedError("invalid-package", f"{name} cannot be read") from error
        try:
            return ET.fromstring(_decode(name, data))
        except ET.ParseError as error:
            raise DocxRefusedError("invalid-package", f"{name} is not well-formed") from error

    def related(self, source: str, kind: str) -> list[str]:
        """Target part names of ``source``'s internal relationships whose type ends in ``kind``."""
        folder, base = posixpath.split(source)
        rels = self.part(posixpath.join(folder, "_rels", base + ".rels"))
        if rels is None:
            return []
        out: list[str] = []
        for rel in rels.findall(f"{{{PR}}}Relationship"):
            if rel.get("TargetMode") == "External" or not rel.get("Type", "").endswith("/" + kind):
                continue
            target = rel.get("Target", "")
            resolved = target[1:] if target.startswith("/") else posixpath.join(folder, target)
            out.append(posixpath.normpath(resolved))
        return out


# --- styles --------------------------------------------------------------------------------


@dataclass
class _Style:
    kind: str
    based_on: str | None
    rpr: ET.Element | None
    ppr: ET.Element | None
    # Why the style's formatting for its first row, banded rows and the like could change what
    # the reader produces (the reader does not apply it), or None if it cannot.
    conditional: str | None = None
    # Whether that formatting sets fonts, which could override a Symbol font beneath it.
    conditional_fonts: bool = False
    # The style's name (w:name), which Word's heading levels and STYLEREF go by.
    name: str | None = None


@dataclass
class _Styles:
    styles: dict[str, _Style] = field(default_factory=dict)
    default_rpr: ET.Element | None = None
    default_ppr: ET.Element | None = None
    # settings.xml asks Word to update every field when the document opens.
    update_fields: bool = False
    defaults: dict[str, str] = field(default_factory=dict)
    theme_fonts: dict[str, str] = field(default_factory=dict)
    has_theme: bool = False
    # Fonts the font table declares symbol-encoded (charset 02), other than Symbol itself.
    symbol_encoded: set[str] = field(default_factory=set)

    def theme_font(self, theme: str) -> str:
        """The typeface a theme font reference (``minorHAnsi``...) names; refused if none."""
        if not self.has_theme:
            raise DocxRefusedError("symbol-font", f"theme font {theme} without a theme")
        for prefix in ("major", "minor"):
            if theme.startswith(prefix):
                script = {"HAnsi": "Latin", "Ascii": "Latin"}.get(theme[len(prefix) :])
                key = prefix + (script or theme[len(prefix) :])
                if key in self.theme_fonts:
                    return self.theme_fonts[key]
        raise DocxRefusedError("symbol-font", f"theme font {theme} is not in the theme")

    def effective(self, style_id: str | None, kind: str) -> str | None:
        """``style_id``, or the default style of ``kind`` when it is absent or unknown.

        That is how Word falls back. A reference to a style of another kind is refused: what Word
        does with it is not documented.
        """
        if style_id is None or style_id not in self.styles:
            return self.defaults.get(kind)
        if self.styles[style_id].kind != kind:
            raise DocxRefusedError("unsupported-element", f"{kind} style {style_id!r} is not one")
        return style_id

    def resolve(self, style_id: str | None, kind: str) -> list[_Style]:
        return self.chain(self.effective(style_id, kind))

    def chain(self, style_id: str | None) -> list[_Style]:
        out: list[_Style] = []
        seen: set[str] = set()
        while style_id is not None and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            style = self.styles[style_id]
            out.append(style)
            style_id = style.based_on
        return out


def _styles(root: ET.Element | None, theme: ET.Element | None, fonts: ET.Element | None) -> _Styles:
    styles = _Styles()
    if fonts is not None:
        for entry in fonts.findall(_w("font")):
            charset = entry.find(_w("charset"))
            name = entry.get(_w("name"), "")
            encoded = charset is not None and charset.get(_w("val"), "").upper() == "02"
            if encoded and _font_class(name) != "symbol":
                styles.symbol_encoded.add(name.lower())
    if theme is not None:
        styles.has_theme = True
        for prefix in ("major", "minor"):
            font = theme.find(f".//{{{A}}}{prefix}Font")
            if font is None:
                continue
            for script, child in (("Latin", "latin"), ("EastAsia", "ea"), ("Bidi", "cs")):
                element = font.find(f"{{{A}}}{child}")
                if element is not None:
                    styles.theme_fonts[prefix + script] = element.get("typeface", "")
    if root is None:
        return styles
    styles.default_rpr = root.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
    styles.default_ppr = root.find(f"{_w('docDefaults')}/{_w('pPrDefault')}/{_w('pPr')}")
    for style in root.findall(_w("style")):
        style_id = style.get(_w("styleId"))
        if style_id is None:
            continue
        if style_id in styles.styles:
            raise DocxRefusedError("invalid-package", f"style {style_id!r} is defined twice")
        based = style.find(_w("basedOn"))
        named = style.find(_w("name"))
        kind = style.get(_w("type"), "paragraph")
        styles.styles[style_id] = _Style(
            kind=kind,
            based_on=based.get(_w("val")) if based is not None else None,
            name=named.get(_w("val")) if named is not None else None,
            rpr=style.find(_w("rPr")),
            ppr=style.find(_w("pPr")),
            conditional=_conditional(style, styles),
            conditional_fonts=any(
                part.find(f"{_w('rPr')}/{_w('rFonts')}") is not None
                for part in style.findall(_w("tblStylePr"))
            ),
        )
        if style.get(_w("default")) in ("1", "true", "on"):
            # With more than one default of a kind, the last one is used (ECMA-376 17.7.4.17).
            styles.defaults[kind] = style_id
    return styles


# What a table style's conditional formatting may set, since the reader does not apply it: run
# and paragraph properties it does not report, and fonts, sizes and colours checked below to be
# ordinary text. Cell, row and table properties (shading, borders) are not reported either.
_CONDITIONAL_RUN = {
    _w(name)
    for name in (
        "b",
        "bCs",
        "i",
        "iCs",
        "rFonts",
        "sz",
        "szCs",
        "color",
        "kern",
        "spacing",
        "lang",
        "noProof",
    )
}
_CONDITIONAL_PARAGRAPH = {
    _w(name)
    for name in (
        "spacing",
        "jc",
        "ind",
        "keepNext",
        "keepLines",
        "contextualSpacing",
        "widowControl",
        "tabs",
        "suppressAutoHyphens",
        "pBdr",
        "snapToGrid",
    )
}


def _conditional(style: ET.Element, styles: _Styles) -> str | None:
    """Why a table style's conditional formatting could change what the reader produces.

    None when every property it sets is one the reader does not report, or a font, size or
    colour that cannot make text faint or Symbol. Such formatting changes nothing the reader
    produces except where it would override a faint size or colour beneath it, which the reader
    then over-reports as faint; a Symbol font beneath it is refused (``_in_symbol``).
    """
    for part in style.findall(_w("tblStylePr")):
        kind = part.get(_w("type"), "")
        rpr = part.find(_w("rPr"))
        for child in [] if rpr is None else list(rpr):
            if child.tag not in _CONDITIONAL_RUN:
                return f"conditional table formatting ({kind}) sets {_local(child.tag)}"
            if child.tag == _w("rFonts"):
                for slot, theme in _THEME_ATTRIBUTE.items():
                    name = child.get(_w(theme))
                    name = styles.theme_font(name) if name else child.get(_w(slot))
                    if _font_kind(styles, name) != "text":
                        return f"conditional table formatting ({kind}) sets the font {name}"
            elif child.tag in (_w("sz"), _w("szCs")) and _tiny(child.get(_w("val"))):
                return f"conditional table formatting ({kind}) sets a tiny size"
            elif child.tag == _w("color") and _faint_color(child):
                return f"conditional table formatting ({kind}) sets a faint colour"
        ppr = part.find(_w("pPr"))
        for child in [] if ppr is None else list(ppr):
            if child.tag not in _CONDITIONAL_PARAGRAPH:
                return f"conditional table formatting ({kind}) sets {_local(child.tag)}"
    return None


def _on(element: ET.Element | None) -> bool | None:
    if element is None:
        return None
    value = element.get(_w("val"))
    return value is None or value.lower() not in {"0", "false", "off"}


class _Properties:
    """The run properties in force for one run, from the run outwards.

    ``mark``, for a list label, is the paragraph mark's run properties, which the label's level
    properties (``direct``) sit over.
    """

    def __init__(
        self,
        styles: _Styles,
        direct: ET.Element | None,
        paragraph_style: str | None,
        table_style: str | None,
        mark: ET.Element | None = None,
    ) -> None:
        self.styles = styles
        self.direct = direct
        run_style = None
        if direct is not None:
            element = direct.find(_w("rStyle"))
            run_style = element.get(_w("val")) if element is not None else None
        levels: list[ET.Element | None] = [mark]
        levels += [style.rpr for style in styles.resolve(run_style, "character")]
        levels += [style.rpr for style in styles.resolve(paragraph_style, "paragraph")]
        # ``table_style`` is already resolved: None outside a table, where no table style applies.
        levels += [style.rpr for style in styles.chain(table_style)]
        levels.append(styles.default_rpr)
        self.inherited = [level for level in levels if level is not None]

    def toggle(self, name: str) -> bool:
        """True when the run asserts it, or when it is silent and any level asserts it."""
        direct = _on(self.direct.find(_w(name))) if self.direct is not None else None
        if direct is not None:
            return direct
        return any(_on(level.find(_w(name))) for level in self.inherited)

    def value(self, name: str, attribute: str = "val") -> str | None:
        for level in [self.direct, *self.inherited]:
            if level is None:
                continue
            element = level.find(_w(name))
            if element is not None and element.get(_w(attribute)) is not None:
                return element.get(_w(attribute))
        return None

    def element(self, name: str) -> ET.Element | None:
        """The nearest level's ``name`` element, whole, so its attributes stay together."""
        for level in [self.direct, *self.inherited]:
            if level is not None and (found := level.find(_w(name))) is not None:
                return found
        return None

    def font(self, slot: str) -> str | None:
        """The effective font for ``ascii``, ``hAnsi``, ``eastAsia`` or ``cs``."""
        for level in [self.direct, *self.inherited]:
            if level is None:
                continue
            fonts = level.find(_w("rFonts"))
            if fonts is None:
                continue
            theme = fonts.get(_w(_THEME_ATTRIBUTE[slot]))
            if theme is not None:
                return self.styles.theme_font(theme)
            name = fonts.get(_w(slot))
            if name is not None:
                return name
        return None


_THEME_ATTRIBUTE = {
    "ascii": "asciiTheme",
    "hAnsi": "hAnsiTheme",
    "eastAsia": "eastAsiaTheme",
    "cs": "cstheme",
}


def _font_class(name: str | None) -> str:
    if name is None:
        return "text"
    key = name.lower().replace(" ", "")
    if key in ("symbol", "symbolmt"):
        return "symbol"
    if any(part in key for part in _DINGBAT_FONTS):
        return "dingbat"
    return "text"


def _font_kind(styles: _Styles, name: str | None) -> str:
    font = _font_class(name)
    if font == "text" and name is not None and name.lower() in styles.symbol_encoded:
        return "dingbat"
    return font


def _in_symbol(styles: _Styles, properties: _Properties, table_style: str | None) -> bool:
    """Whether every character with these properties is drawn in Symbol; refused if unsure."""
    kinds = {
        slot: _font_kind(styles, properties.font(slot))
        for slot in ("ascii", "hAnsi", "eastAsia", "cs")
    }
    if "dingbat" in kinds.values():
        raise DocxRefusedError("symbol-font", "a run in a dingbat or symbol-encoded font")
    symbol = "symbol" in kinds.values()
    if symbol and (
        kinds["ascii"] != "symbol"
        or kinds["hAnsi"] != "symbol"
        or properties.toggle("cs")
        or properties.toggle("rtl")
        or properties.value("rFonts", "hint") not in (None, "default")
    ):
        # Word chooses the font per character from these slots; the reader maps a run only
        # when every Latin character is certain to be drawn in Symbol.
        raise DocxRefusedError("symbol-font", "Symbol set for only some characters")
    if symbol and any(s.conditional_fonts for s in styles.chain(table_style)):
        # The table style's conditional formatting, which the reader does not apply, may set
        # another font over it.
        raise DocxRefusedError("symbol-font", "Symbol under conditional table fonts")
    return symbol


def _characters(text: str, symbol: bool) -> str:
    """``text`` as drawn: mapped through the Symbol table in a Symbol run, else as stored."""
    out: list[str] = []
    for character in text:
        code = ord(character)
        if symbol:
            if code > 0xFF and not 0xF000 <= code <= 0xF0FF:
                raise DocxRefusedError("unmapped-symbol", f"U+{code:04X} in a Symbol run")
            out.append(_symbol(code, "w:t"))
        elif _private_use(code):
            raise DocxRefusedError("private-use-character", f"U+{code:04X}")
        elif character == OBJECT:
            raise DocxRefusedError("reserved-character", "U+FFFC stands for a picture")
        else:
            out.append(character)
    return "".join(out)


def _symbol(code: int, where: str) -> str:
    low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
    if low not in SYMBOL_FONT:
        raise DocxRefusedError("unmapped-symbol", f"{where}: Symbol code {code:#06x}")
    return SYMBOL_FONT[low]


def _private_use(code: int) -> bool:
    return 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0x10FFFF


# --- paragraphs ----------------------------------------------------------------------------


def _drawing(element: ET.Element) -> str:
    """A picture is one U+FFFC; a drawing that can hold text, or is not a picture, is refused."""
    for node in element.iter():
        local = _local(node.tag)
        if local in ("t", "txbx", "txbxContent"):
            raise DocxRefusedError("unsupported-element", "drawing with text")
        if local == "graphicData" and node.get("uri") != PICTURE_URI:
            raise DocxRefusedError("unsupported-element", f"drawing of {node.get('uri')}")
    return OBJECT


def _vml_picture(element: ET.Element) -> str:
    """A VML picture (``w:pict`` of an image) is one U+FFFC; any other VML is refused.

    Word writes pictures this way in documents from before Word 2007 and when saving for them.
    A text box, WordArt, an embedded object or a drawn shape is refused: it holds text, or it is
    not a picture.
    """
    locals_ = {_local(node.tag) for node in element.iter()}
    if locals_ & {"textbox", "txbxContent", "textpath", "t", "OLEObject"}:
        raise DocxRefusedError("unsupported-element", "pict with text or an embedded object")
    if "imagedata" not in locals_:
        raise DocxRefusedError("unsupported-element", "pict that is not a picture")
    return OBJECT


class _ParagraphReader:
    def __init__(
        self,
        styles: _Styles,
        paragraph_style: str | None,
        table_style: str | None,
        runs: set[ET.Element],
        story: tuple[str, int] | None,
    ) -> None:
        self.styles = styles
        self.paragraph_style = paragraph_style
        self.table_style = table_style
        # Every run read, shared across the body, for the accounting in read_docx.
        self.runs = runs
        # The note being read (kind and id), or None in the body.
        self.story = story
        self.notes: list[NoteReference] = []
        self.custom: set[tuple[str, int]] = set()
        # Fields whose result the reader checks against its own computation: the instruction and
        # where the stored result stands in the text. ``results`` follows ``fields``: the start
        # of each open field's result if it is one of those, else None.
        self.computed: list[tuple[str, int, int]] = []
        self.results: list[int | None] = []
        # Where a page number stands, and how many layout fields' results are open: their text
        # is the page number when Word last laid the document out, not what it prints.
        self.pages: list[int] = []
        self.layout = 0
        self.layout_open: list[bool] = []
        # Bookmark starts (id, name, offset) and ends (id, offset), for REF and NOTEREF.
        self.bookmark_starts: list[tuple[str, str, int]] = []
        self.bookmark_ends: list[tuple[str, int]] = []
        self.parts: list[str] = []
        self.length = 0
        self.marks: list[Mark] = []
        # One entry per open field: True while in its instruction, False once in its result.
        self.fields: list[bool] = []
        # The instruction text of each open field, collected while in its instruction.
        self.instructions: list[list[str]] = []
        self.rtl = 0

    def in_instruction(self) -> bool:
        return True in self.fields

    def container(self, element: ET.Element) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("r"):
                self.run(child)
            elif tag == _w("fldSimple"):
                if self.styles.update_fields:
                    raise DocxRefusedError("computed-field", "the document updates fields on open")
                if child.get(_w("dirty")) in ("1", "true", "on"):
                    raise DocxRefusedError("stale-field", "a field marked for update")
                instruction = child.get(_w("instr"), "")
                code = None if self.in_instruction() else _check_field(instruction)
                before = self.length
                if code in _LAYOUT_FIELDS and not self.layout:
                    self.pages.append(self.length)
                    self.layout += 1
                    self.container(child)
                    self.layout -= 1
                    continue
                self.container(child)
                if self.length == before:
                    raise DocxRefusedError("field-without-result", "a simple field shows nothing")
                if code in _COMPUTED_FIELDS:
                    self.computed.append((instruction, before, self.length))
            elif tag in (_w("bdo"), _w("dir")):
                rtl = child.get(_w("val")) == "rtl"
                self.rtl += rtl
                self.container(child)
                self.rtl -= rtl
            elif tag in _INLINE_TRANSPARENT:
                self.container(child)
            elif tag == _w("sdt"):
                _content_control(child)
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.container(content)
            elif tag == _w("bookmarkStart"):
                self.bookmark_starts.append(
                    (child.get(_w("id"), ""), child.get(_w("name"), ""), self.length)
                )
            elif tag == _w("bookmarkEnd"):
                self.bookmark_ends.append((child.get(_w("id"), ""), self.length))
            elif tag in _PROPERTIES or tag in _MARKERS:
                continue
            else:
                raise DocxRefusedError("unsupported-element", _local(tag))

    def run(self, run: ET.Element) -> None:
        self.runs.add(run)
        properties = _Properties(
            self.styles, run.find(_w("rPr")), self.paragraph_style, self.table_style
        )
        symbol = _in_symbol(self.styles, properties, self.table_style)
        emitted: list[str] = []
        # Page-number text, left out of the text but still drawn: it must not be hidden.
        placed: list[str] = []
        references: list[NoteReference] = []
        for child in run:
            tag = child.tag
            if tag in _NOTE_REFERENCES:
                if self.in_instruction():
                    raise DocxRefusedError("unsupported-element", "a note mark in a field code")
                offset = self.length + sum(len(part) for part in emitted)
                references.append(self._note(child, offset))
                continue
            if tag == _w("fldChar"):
                self._field(child, self.length + sum(len(part) for part in emitted))
                continue
            if tag == _w("instrText"):
                if not (self.fields and self.fields[-1]):
                    raise DocxRefusedError("unbalanced-field", "field code outside an instruction")
                self.instructions[-1].append(child.text or "")
                continue
            if tag in _RUN_SILENT:
                continue
            if tag == _w("t"):
                text = child.text or ""
                _check_whitespace(child, text)
                produced = _characters(text, symbol)
            else:
                produced = self._special(child)
            if not self.in_instruction():
                (placed if self.layout else emitted).append(produced)
            elif self.fields[-1]:
                self.instructions[-1].append(produced)
        text = "".join(emitted)
        if references and properties.toggle("vanish"):
            raise DocxRefusedError("hidden-text", "a hidden note mark")
        if "".join(placed).strip() and properties.toggle("vanish"):
            raise DocxRefusedError("hidden-text", "a hidden page number")
        self.notes += references
        if not text:
            return
        if properties.toggle("vanish"):
            if text.strip():
                raise DocxRefusedError("hidden-text", "a hidden run carries text")
            return
        start = self.length
        self.parts.append(text)
        self.length += len(text)
        self._mark(properties, start, self.length)

    def _note(self, child: ET.Element, offset: int) -> NoteReference:
        """A note reference in the body, or a note's echo of its own mark in the note."""
        tag = _local(child.tag)
        kind = "footnote" if tag.startswith("footnote") else "endnote"
        if tag.endswith("Ref"):
            # footnoteRef or endnoteRef: where a note repeats the mark that refers to it.
            if self.story is None or self.story[0] != kind:
                raise DocxRefusedError("unsupported-element", f"{tag} outside a {kind}")
            return NoteReference(offset, kind, self.story[1])
        if self.story is not None:
            raise DocxRefusedError("unsupported-element", f"{tag} inside a note")
        note = _int(child.get(_w("id"), ""), f"{tag} id")
        if child.get(_w("customMarkFollows")) in ("1", "true", "on"):
            self.custom.add((kind, note))
        return NoteReference(offset, kind, note)

    def _field(self, child: ET.Element, offset: int) -> None:
        if len(child):
            raise DocxRefusedError("unsupported-element", "form field")
        if child.get(_w("dirty")) in ("1", "true", "on"):
            raise DocxRefusedError("stale-field", "a field marked for update")
        kind = child.get(_w("fldCharType"))
        if kind == "begin":
            if self.styles.update_fields:
                raise DocxRefusedError("computed-field", "the document updates fields on open")
            if self.fields and self.fields[-1]:
                # Word puts this field's result into the enclosing instruction, so the reader
                # no longer knows that instruction's code; a NUL keeps it from matching one.
                self.instructions[-1].append("\x00")
            self.fields.append(True)
            self.instructions.append([])
            self.results.append(None)
            self.layout_open.append(False)
        elif kind == "separate" and self.fields:
            # The result is shown, so it must be one Word shows as stored, one the reader
            # computes and checks, or a page number.
            code = None if any(self.fields[:-1]) else _check_field("".join(self.instructions[-1]))
            if code in _COMPUTED_FIELDS:
                self.results[-1] = offset
            elif code in _LAYOUT_FIELDS and not self.layout:
                self.pages.append(offset)
                self.layout += 1
                self.layout_open[-1] = True
            self.fields[-1] = False
        elif kind == "end" and self.fields:
            if self.fields[-1]:
                # No separate: the field stores no result, and what Word shows is computed.
                raise DocxRefusedError("field-without-result", "a field with no stored result")
            start = self.results.pop()
            if start is not None:
                self.computed.append(("".join(self.instructions[-1]), start, offset))
            if self.layout_open.pop():
                self.layout -= 1
            self.fields.pop()
            self.instructions.pop()

    def _special(self, child: ET.Element) -> str:
        tag = child.tag
        if tag in (_w("tab"), _w("ptab")):
            return "\t"
        if tag == _w("br"):
            return "" if child.get(_w("type")) in ("page", "column") else "\n"
        if tag == _w("cr"):
            return "\n"
        if tag == _w("noBreakHyphen"):
            return "\u2011"
        if tag == _w("softHyphen"):
            return "\u00ad"
        if tag == _w("sym"):
            if _font_class(child.get(_w("font"))) != "symbol":
                raise DocxRefusedError("unmapped-symbol", f"w:sym in {child.get(_w('font'))!r}")
            char = child.get(_w("char"), "")
            if not re.fullmatch(r"[0-9A-Fa-f]{1,4}", char):
                raise DocxRefusedError("unmapped-symbol", "w:sym without a hex code")
            return _symbol(int(char, 16), "w:sym")
        if tag == _w("drawing"):
            return _drawing(child)
        if tag == _w("pict"):
            return _vml_picture(child)
        raise DocxRefusedError("unsupported-element", _local(tag))

    def _mark(self, properties: _Properties, start: int, end: int) -> None:
        kinds: list[str] = []
        vertical = properties.value("vertAlign")
        if vertical in ("superscript", "subscript"):
            kinds.append(vertical)
        if properties.value("position") not in (None, "0"):
            kinds.append("position")
        kinds += [name for name in _TOGGLE_MARKS if properties.toggle(name)]
        highlight = properties.value("highlight")
        if highlight not in (None, "none"):
            kinds.append(f"highlight-{highlight}")
        shading = _shading(properties.element("shd"))
        if shading is not None:
            kinds.append(shading)
        if self.rtl or properties.toggle("rtl"):
            kinds.append("rtl")
        if _faint(properties):
            kinds.append("faint")
        if properties.value("u") not in (None, "none"):
            kinds.append("underline")
        for kind in kinds:
            previous = next((m for m in reversed(self.marks) if m.kind == kind), None)
            if previous is not None and previous.end == start:
                self.marks[self.marks.index(previous)] = Mark(previous.start, end, kind)
            else:
                self.marks.append(Mark(start, end, kind))


def _shading(element: ET.Element | None) -> str | None:
    """The mark kind a shading element gives, or None for no shading or a white one.

    ``shading-<fill>`` for a plain fill, ``shading-<pattern>-<colour>-<fill>`` for a pattern.
    """
    if element is None:
        return None
    pattern = element.get(_w("val"))
    fill = (element.get(_w("fill")) or "auto").upper()
    if element.get(_w("themeFill")) is not None:
        fill = "THEME-" + (element.get(_w("themeFill")) or "")
    if pattern not in (None, "clear", "nil"):
        return f"shading-{pattern}-{(element.get(_w('color')) or 'auto').upper()}-{fill}"
    if fill in ("AUTO", "FFFFFF"):
        return None
    return f"shading-{fill}"


_POINTS = {"pt": 1.0, "pc": 12.0, "pi": 12.0, "in": 72.0, "cm": 72 / 2.54, "mm": 72 / 25.4}


def _faint_color(color: ET.Element | None) -> bool:
    """Whether a colour is white or a light theme colour."""
    if color is None:
        return False
    theme = (color.get(_w("themeColor")) or "").lower()
    return theme.startswith(("background", "light", "bg")) or (
        (color.get(_w("val")) or "").lower() in ("ffffff", "white")
    )


def _tiny(size: str | None) -> bool:
    """Whether a font size is under two points; one the reader cannot parse counts as tiny."""
    if size is None:
        return False
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(pt|pc|pi|in|cm|mm)?", size)
    if match is None:
        return True
    unit = match.group(2)
    points = float(match.group(1)) * _POINTS[unit] if unit else float(match.group(1)) / 2
    return points < 2


def _faint(properties: _Properties) -> bool:
    """Whether text with these properties is easy not to see.

    White text (or a light theme colour), text under two points, or text scaled under a fifth
    is. A size or scale the reader cannot parse counts as faint.
    """
    if _faint_color(properties.element("color")):
        return True
    # szCs sizes complex-script text; the reader does not know which script a character is
    # drawn as, so either size being tiny counts.
    if any(_tiny(size) for size in (properties.value("sz"), properties.value("szCs"))):
        return True
    scale = properties.value("w")
    if scale is not None:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%?", scale)
        return match is None or float(match.group(1)) < 20
    return False


# Fields whose stored result is what Word shows and prints until someone updates them by hand
# (corpus/numbering-cases records it for DOCPROPERTY and HYPERLINK). Word recomputes others when
# it lays out or prints the page (PAGE, NUMPAGES, DATE, TIME, AUTONUM, LISTNUM, IF, formulas...),
# so their stored result may not be what a reader sees.
_STORED_FIELDS = {"HYPERLINK", "DOCPROPERTY", "TOC"}
# Page numbers, which Word sets from the layout when it prints: never known from the file.
_LAYOUT_FIELDS = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}
# Fields Word recomputes when it prints or saves as PDF, which the reader computes too and reads
# only where the stored result is what Word prints (see "Fields" in the module docstring). REF
# and NOTEREF are here because Word reprints them (corpus/numbering-cases, fields-ref-stale and
# fields-noteref-stale): a cross-reference stored as one text prints as another.
_COMPUTED_FIELDS = {"SEQ", "STYLEREF", "REF", "NOTEREF"}


def _check_field(instruction: str) -> str:
    """The field's code, if its result is one the reader can vouch for; refused otherwise."""
    words = instruction.split()
    code = words[0].upper() if words else ""
    if code not in _STORED_FIELDS | _COMPUTED_FIELDS | _LAYOUT_FIELDS:
        raise DocxRefusedError("computed-field", f"a {code or 'blank'} field")
    return code


def _check_whitespace(element: ET.Element, text: str) -> None:
    if any(c in text for c in "\t\r\n"):
        # Word writes a tab or a break as an element; one inside the text is not what it shows.
        raise DocxRefusedError("unpreserved-whitespace", "a tab or line break inside w:t")
    if element.get(XML_SPACE) != "preserve" and text != text.strip(" "):
        raise DocxRefusedError("unpreserved-whitespace", "w:t without xml:space=preserve")


def _content_control(element: ET.Element) -> None:
    properties = element.find(_w("sdtPr"))
    if properties is not None and any(_local(c.tag) == "dataBinding" for c in properties):
        # The stored content is a cache; Word shows the bound data.
        raise DocxRefusedError("unsupported-element", "content control bound to data")


def _int(value: str, where: str) -> int:
    if not re.fullmatch(r"-?[0-9]{1,9}", value):
        raise DocxRefusedError("invalid-package", f"{where} is not a number: {value!r}")
    return int(value)


def _numbering(levels: list[ET.Element | None]) -> Numbering | None:
    """The numId and ilvl, each from the nearest paragraph-properties level that sets it.

    An ilvl set nowhere is 0, even where a list level names the paragraph's style (``w:pStyle``):
    Word draws such a paragraph at level 0 (corpus/numbering-cases, style-tied-deeper-level).
    """
    found: dict[str, int] = {}
    for source in levels:
        numpr = source.find(_w("numPr")) if source is not None else None
        if numpr is None:
            continue
        for name in ("numId", "ilvl"):
            element = numpr.find(_w(name))
            if name not in found and element is not None:
                found[name] = _int(element.get(_w("val"), "0"), name)
    if not found:
        return None
    return Numbering(num_id=found.get("numId", 0), level=found.get("ilvl", 0))


@dataclass(frozen=True)
class _Context:
    """What the list-label pass needs of a paragraph besides the paragraph itself."""

    # The paragraph style in force (after falling back to the default), its table's style, and
    # the paragraph mark's run properties, which a list label is drawn over.
    style: str | None
    table_style: str | None
    mark: ET.Element | None
    # The section the paragraph ends in or belongs to, counted from 0 (body only), and the
    # notes whose marks are custom.
    section: int = 0
    custom: frozenset[tuple[str, int]] = frozenset()
    # The fields the reader computes: instruction, and the stored result's start and end in text.
    fields: tuple[tuple[str, int, int], ...] = ()
    # Bookmark starts (id, name, offset) and ends (id, offset) in the paragraph.
    bookmark_starts: tuple[tuple[str, str, int], ...] = ()
    bookmark_ends: tuple[tuple[str, int], ...] = ()


def _paragraph(
    element: ET.Element,
    styles: _Styles,
    table: tuple[int, int, int] | None,
    table_style: str | None,
    runs: set[ET.Element],
    story: tuple[str, int] | None = None,
    section: int = 0,
) -> tuple[Paragraph, _Context]:
    ppr = element.find(_w("pPr"))
    style = None
    if ppr is not None:
        style_element = ppr.find(_w("pStyle"))
        style = style_element.get(_w("val")) if style_element is not None else None
    mark_rpr = ppr.find(_w("rPr")) if ppr is not None else None
    mark = _Properties(styles, mark_rpr, style, table_style)
    mark_hidden = mark.toggle("vanish") or mark.toggle("specVanish")
    reader = _ParagraphReader(styles, style, table_style, runs, story)
    reader.container(element)
    if reader.in_instruction():
        raise DocxRefusedError("unbalanced-field", "a paragraph ends inside a field instruction")
    if any(start is not None for start in reader.results):
        raise DocxRefusedError(
            "unbalanced-field", "a computed field's result runs past its paragraph"
        )
    if reader.layout:
        raise DocxRefusedError("unbalanced-field", "a page number runs past its paragraph")
    numbering = _numbering(
        [
            ppr,
            *(s.ppr for s in styles.resolve(style, "paragraph")),
            *(s.ppr for s in styles.chain(table_style)),
            styles.default_ppr,
        ]
    )
    context = _Context(
        style=styles.effective(style, "paragraph"),
        table_style=table_style,
        mark=mark_rpr,
        section=section,
        custom=frozenset(reader.custom),
        fields=tuple(reader.computed),
        bookmark_starts=tuple(reader.bookmark_starts),
        bookmark_ends=tuple(reader.bookmark_ends),
    )
    return Paragraph(
        text="".join(reader.parts),
        style=style,
        numbering=numbering,
        table=table,
        marks=_paragraph_marks(
            reader,
            [
                ppr,
                *(s.ppr for s in styles.resolve(style, "paragraph")),
                *(s.ppr for s in styles.chain(table_style)),
                styles.default_ppr,
            ],
        ),
        mark_hidden=mark_hidden,
        notes=tuple(reader.notes),
        pages=tuple(reader.pages),
    ), context


def _paragraph_marks(reader: _ParagraphReader, levels: list[ET.Element | None]) -> tuple[Mark, ...]:
    """The runs' marks, and the shading and right-to-left marks of the paragraph itself.

    Shading or right-to-left set on the paragraph (or its style) covers every character.
    """
    marks = list(reader.marks)

    def nearest(name: str) -> ET.Element | None:
        return next(
            (
                e
                for level in levels
                if level is not None and (e := level.find(_w(name))) is not None
            ),
            None,
        )

    shading = _shading(nearest("shd"))
    if reader.length and shading is not None:
        marks.append(Mark(0, reader.length, shading))
    if reader.length and _on(nearest("bidi")):
        marks.append(Mark(0, reader.length, "rtl"))
    return tuple(sorted(set(marks), key=lambda m: (m.start, m.end, m.kind)))


# --- list labels ---------------------------------------------------------------------------

_LEVELS = range(9)
_FORMATS = {
    "decimal",
    "decimalZero",
    "upperRoman",
    "lowerRoman",
    "upperLetter",
    "lowerLetter",
    "none",
    "bullet",
}
# What a list level may hold. Anything else (a picture bullet, alternate content carrying a
# custom format...) makes the level one the reader cannot draw.
_LEVEL_CHILDREN = {
    _w(name)
    for name in (
        "start",
        "numFmt",
        "lvlRestart",
        "pStyle",
        "isLgl",
        "suff",
        "lvlText",
        "lvlJc",
        "pPr",
        "rPr",
        "legacy",
    )
}
_PLACEHOLDER = re.compile(r"(%[1-9])")
_ROMAN = (
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


@dataclass(frozen=True)
class _Level:
    start: int | None
    format: str
    text: str | None
    restart: int | None
    legal: bool
    suffix: str
    rpr: ET.Element | None
    style: str | None
    # Why the level cannot be drawn, if it cannot; refused only when a paragraph uses it.
    unsupported: str | None


def _level(element: ET.Element) -> _Level:
    def value(name: str) -> str | None:
        found = element.find(_w(name))
        return None if found is None else found.get(_w("val"))

    unsupported = next(
        (f"{_local(c.tag)} in a list level" for c in element if c.tag not in _LEVEL_CHILDREN), None
    )
    number_format = element.find(_w("numFmt"))
    fmt = value("numFmt") or "decimal"
    if number_format is not None and number_format.get(_w("format")) is not None:
        unsupported = unsupported or "a custom number format"
    elif fmt not in _FORMATS:
        unsupported = unsupported or f"the number format {fmt}"
    text_element = element.find(_w("lvlText"))
    text = None
    if text_element is not None:
        null = text_element.get(_w("null")) in ("1", "true", "on")
        text = "" if null else text_element.get(_w("val"), "")
    legacy = element.find(_w("legacy"))
    suffix = value("suff") or "tab"
    if legacy is not None and legacy.get(_w("legacy")) not in ("0", "false", "off"):
        # A Word 6 level: the gap after the label is set by legacySpace and legacyIndent.
        suffix = "legacy"
    elif suffix not in ("tab", "space", "nothing"):
        unsupported = unsupported or f"the suffix {suffix}"
    start = value("start")
    restart = value("lvlRestart")
    return _Level(
        start=None if start is None else _int(start, "start"),
        format=fmt,
        text=text,
        restart=None if restart is None else _int(restart, "lvlRestart"),
        legal=bool(_on(element.find(_w("isLgl")))),
        suffix=suffix,
        rpr=element.find(_w("rPr")),
        style=value("pStyle"),
        unsupported=unsupported,
    )


def _ilvl(element: ET.Element) -> int:
    level = _int(element.get(_w("ilvl"), ""), "ilvl")
    if level not in _LEVELS:
        raise DocxRefusedError("invalid-package", f"list level {level}")
    return level


@dataclass(frozen=True)
class _Num:
    abstract: int | None
    starts: dict[int, int]
    levels: dict[int, _Level]


@dataclass(frozen=True)
class _Abstract:
    levels: dict[int, _Level]
    # numStyleLink: this abstractNum takes its levels from the one a numbering style names.
    link: str | None
    # styleLink: this abstractNum is the one that numbering style names.
    linked_from: str | None


@dataclass
class _Counters:
    """The counters of one abstractNum, shared by every list (numId) that names it."""

    values: list[int | None] = field(default_factory=lambda: [None] * len(_LEVELS))
    # A level whose start the reader cannot find (its abstractNum does not define it).
    unknown: list[bool] = field(default_factory=lambda: [False] * len(_LEVELS))
    # The (numId, level) pairs whose startOverride has been applied.
    applied: set[tuple[int, int]] = field(default_factory=set)


def _refuse_numbering(detail: str) -> DocxRefusedError:
    return DocxRefusedError("unsupported-numbering", detail)


class _Lists:
    def __init__(self, root: ET.Element | None, styles: _Styles) -> None:
        self.styles = styles
        self.present = root is not None
        self.abstracts: dict[int, _Abstract] = {}
        self.nums: dict[int, _Num] = {}
        self.counters: dict[int, _Counters] = {}
        if root is None:
            return
        for element in root.findall(_w("abstractNum")):
            key = _int(element.get(_w("abstractNumId"), ""), "abstractNumId")
            if key in self.abstracts:
                raise DocxRefusedError("invalid-package", f"abstractNum {key} is defined twice")
            levels: dict[int, _Level] = {}
            for lvl in element.findall(_w("lvl")):
                level = _ilvl(lvl)
                if level in levels:
                    raise DocxRefusedError("invalid-package", f"list level {level} twice")
                levels[level] = _level(lvl)
            link = element.find(_w("numStyleLink"))
            linked_from = element.find(_w("styleLink"))
            self.abstracts[key] = _Abstract(
                levels,
                link=None if link is None else link.get(_w("val")),
                linked_from=None if linked_from is None else linked_from.get(_w("val")),
            )
        for element in root.findall(_w("num")):
            key = _int(element.get(_w("numId"), ""), "numId")
            if key in self.nums:
                raise DocxRefusedError("invalid-package", f"numId {key} is defined twice")
            abstract = element.find(_w("abstractNumId"))
            starts: dict[int, int] = {}
            overridden: dict[int, _Level] = {}
            seen: set[int] = set()
            for override in element.findall(_w("lvlOverride")):
                level = _ilvl(override)
                if level in seen:
                    raise DocxRefusedError("invalid-package", f"list level {level} twice")
                seen.add(level)
                start = override.find(_w("startOverride"))
                if start is not None:
                    starts[level] = _int(start.get(_w("val"), ""), "startOverride")
                redefined = override.find(_w("lvl"))
                if redefined is not None:
                    overridden[level] = _level(redefined)
            self.nums[key] = _Num(
                abstract=None
                if abstract is None
                else _int(abstract.get(_w("val"), ""), "abstractNumId"),
                starts=starts,
                levels=overridden,
            )

    def definitions(self, num_id: int) -> tuple[int, _Num, dict[int, _Level], dict[int, _Level]]:
        """What counts and draws ``num_id``'s labels.

        The abstractNum whose counters it shares (the one it names, even when that one links to
        another), the num, the abstractNum's levels (through the link), and those levels with the
        num's level overrides over them.
        """
        if not self.present:
            raise _refuse_numbering("a list with no numbering part")
        num = self.nums.get(num_id)
        if num is None or num.abstract is None or num.abstract not in self.abstracts:
            raise _refuse_numbering(f"numId {num_id} is not defined")
        abstract = self.abstracts[num.abstract]
        if abstract.link is not None:
            abstract = self.abstracts[self._linked(abstract.link)]
        return num.abstract, num, abstract.levels, {**abstract.levels, **num.levels}

    def _linked(self, name: str) -> int:
        """The abstractNum a numbering style names, which must name the style back.

        Word draws an empty label for a link with no ``styleLink`` back (corpus/numbering-cases,
        numbering-style-link-one-way); the reader refuses it.
        """
        style = self.styles.styles.get(name)
        if style is None or style.kind != "numbering":
            raise _refuse_numbering(f"numbering style {name!r} is not defined")
        numbering = _numbering([style.ppr])
        linked = self.nums.get(numbering.num_id) if numbering is not None else None
        if (
            linked is None
            or linked.abstract is None
            or linked.starts
            or linked.levels
            or linked.abstract not in self.abstracts
            or self.abstracts[linked.abstract].link is not None
            or self.abstracts[linked.abstract].linked_from != name
        ):
            raise _refuse_numbering(f"numbering style {name!r} names no list the reader can use")
        return linked.abstract

    def label(self, numbering: Numbering, context: _Context) -> Numbering:
        """``numbering`` with the label Word draws, counted in document order."""
        level = numbering.level
        if level not in _LEVELS:
            raise _refuse_numbering(f"list level {level}")
        key, num, base, levels = self.definitions(numbering.num_id)
        definition = levels.get(level)
        if definition is None:
            raise _refuse_numbering(f"level {level} of numId {numbering.num_id} is not defined")
        counters = self.counters.setdefault(key, _Counters())
        self._count(counters, numbering.num_id, num, level, base, levels)
        text = self._draw(counters, level, levels, definition, context)
        return replace(numbering, text=text, suffix=definition.suffix)

    @staticmethod
    def _base_start(counters: _Counters, base: dict[int, _Level], level: int) -> None:
        """Start ``level`` at its abstractNum's start (0 when it sets none), or mark it unknown."""
        definition = base.get(level)
        if definition is None:
            counters.unknown[level] = True
        else:
            counters.values[level] = 0 if definition.start is None else definition.start

    def _count(
        self,
        counters: _Counters,
        num_id: int,
        num: _Num,
        level: int,
        base: dict[int, _Level],
        levels: dict[int, _Level],
    ) -> None:
        """Count a paragraph of list ``num_id`` at ``level``, as Word does.

        Each rule is Word's answer to a case in corpus/numbering-cases, named in brackets.
        """
        for deeper in range(level + 1, len(_LEVELS)):
            definition = levels.get(deeper)
            restart = definition.restart if definition is not None else None
            # lvlRestart n restarts the level after a paragraph at a level up to n - 1; 0 never.
            # A value that is not a higher level is ignored, and then any higher level restarts
            # [restart-never, restart-after-first].
            if restart is None or level < restart or restart - 1 >= deeper:
                counters.values[deeper] = None
                counters.unknown[deeper] = False
        for higher in range(level):
            # A higher level not counted yet counts as its abstractNum's start, not a list's
            # startOverride [ancestor-never-counted, ancestor-two-levels,
            # override-implicit-ancestor].
            if counters.values[higher] is None and not counters.unknown[higher]:
                self._base_start(counters, base, higher)
        current = counters.values[level]
        if level in num.starts and (num_id, level) not in counters.applied:
            # A startOverride sets the count the first time its list reaches the level, whatever
            # the shared count was [start-override-restart, override-ancestor, override-return].
            counters.applied.add((num_id, level))
            counters.values[level] = num.starts[level]
            counters.unknown[level] = False
        elif counters.unknown[level]:
            return
        elif current is not None:
            # Lists of one abstractNum share its count [shared-continue, return-after-restart,
            # plain-after-restart, level-override-shared].
            counters.values[level] = current + 1
        elif level in num.starts:
            # Restarted inside a list with a startOverride: its override again
            # [override-restart-within].
            counters.values[level] = num.starts[level]
        else:
            # A level override's own w:start is not used [level-override-first,
            # level-override-start]; a level with no w:start starts at 0 [missing-start].
            self._base_start(counters, base, level)

    def _draw(
        self,
        counters: _Counters,
        level: int,
        levels: dict[int, _Level],
        definition: _Level,
        context: _Context,
    ) -> str:
        if definition.unsupported is not None:
            raise _refuse_numbering(definition.unsupported)
        if definition.text is None:
            raise _refuse_numbering("a list level with no lvlText")
        pieces: list[str] = []
        for piece in _PLACEHOLDER.split(definition.text):
            if not _PLACEHOLDER.fullmatch(piece):
                if "%" in piece:
                    raise _refuse_numbering("a % in lvlText that names no level")
                pieces.append(piece)
                continue
            shown = int(piece[1]) - 1
            source = levels.get(shown)
            if shown > level or source is None:
                raise _refuse_numbering(f"lvlText shows level {shown} from level {level}")
            if definition.format == "bullet" or source.format == "bullet":
                raise _refuse_numbering("a bullet level in a list label's number")
            if source.unsupported is not None:
                raise _refuse_numbering(source.unsupported)
            if definition.legal and source.format == "none":
                raise _refuse_numbering("legal numbering of a level that shows no number")
            value = counters.values[shown]
            if counters.unknown[shown] or value is None:
                raise DocxRefusedError("ambiguous-numbering", f"the count of list level {shown}")
            pieces.append(_number(value, "decimal" if definition.legal else source.format))
        properties = _Properties(
            self.styles, definition.rpr, context.style, context.table_style, context.mark
        )
        if properties.toggle("vanish") or properties.toggle("specVanish"):
            raise DocxRefusedError("ambiguous-numbering", "a hidden list label")
        label = _characters(
            "".join(pieces), _in_symbol(self.styles, properties, context.table_style)
        )
        if (properties.toggle("caps") or properties.toggle("smallCaps")) and label.upper() != label:
            raise _refuse_numbering("a list label in capitals")
        return label


def _number(value: int, fmt: str) -> str:
    """``value`` in the number format ``fmt``."""
    if fmt == "none":
        return ""
    if fmt in ("decimal", "decimalZero"):
        if value < 0:
            raise _refuse_numbering(f"the number {value}")
        return f"{value:02d}" if fmt == "decimalZero" else str(value)
    if fmt in ("upperRoman", "lowerRoman"):
        if not 1 <= value <= 3999:
            raise _refuse_numbering(f"the number {value} in roman")
        out: list[str] = []
        rest = value
        for amount, numeral in _ROMAN:
            count, rest = divmod(rest, amount)
            out.append(numeral * count)
        roman = "".join(out)
        return roman if fmt == "upperRoman" else roman.lower()
    # upperLetter or lowerLetter: a to z, then aa to zz, and so on, each letter repeated.
    if not 1 <= value <= 780:
        raise _refuse_numbering(f"the number {value} in letters")
    letter = chr(ord("A") + (value - 1) % 26) * ((value - 1) // 26 + 1)
    return letter if fmt == "upperLetter" else letter.lower()


def _labelled(
    paragraphs: list[Paragraph], contexts: list[_Context], lists: _Lists
) -> list[Paragraph]:
    """``paragraphs`` with the label of every numbered one, counted in document order."""
    out: list[Paragraph] = []
    hidden_before = False
    for paragraph, context in zip(paragraphs, contexts, strict=True):
        numbering = paragraph.numbering
        if numbering is None or numbering.num_id == 0:
            out.append(paragraph)
        elif hidden_before:
            # Word runs this paragraph on after the previous one; where it draws the label, if
            # it draws one, is not documented.
            raise DocxRefusedError("ambiguous-numbering", "a list item run on after a hidden mark")
        else:
            out.append(replace(paragraph, numbering=lists.label(numbering, context)))
        hidden_before = paragraph.mark_hidden
    return out


# --- notes ---------------------------------------------------------------------------------

_NOTE_KINDS = ("footnote", "endnote")
# Word's defaults when neither the settings nor the section set a format.
_NOTE_DEFAULT_FORMAT = {"footnote": "decimal", "endnote": "lowerRoman"}
_NOTE_FORMATS = {"decimal", "upperRoman", "lowerRoman", "upperLetter", "lowerLetter", "chicago"}
_CHICAGO = ("*", "\u2020", "\u2021", "\u00a7")
# Separators and continuation notices: layout, not notes, and never referenced.
_NOTE_LAYOUT = {"separator", "continuationSeparator", "continuationNotice"}


@dataclass(frozen=True)
class _NoteRules:
    format: str
    start: int
    restart: str


def _note_rules(kind: str, section: ET.Element | None) -> _NoteRules:
    """How ``kind`` notes are numbered in a section, from its sectPr alone.

    Word ignores the footnotePr and endnotePr of the settings part, even with the separators it
    lists there (corpus/numbering-cases, notes-document-format), and applies the section's.
    """
    properties = section.find(_w(f"{kind}Pr")) if section is not None else None
    values: dict[str, str] = {}
    for name in ("numFmt", "numStart", "numRestart"):
        element = properties.find(_w(name)) if properties is not None else None
        if element is None:
            continue
        if name == "numFmt" and element.get(_w("format")) is not None:
            raise _refuse_numbering(f"a custom {kind} number format")
        if element.get(_w("val")) is not None:
            values[name] = element.get(_w("val"), "")
    return _NoteRules(
        format=values.get("numFmt", _NOTE_DEFAULT_FORMAT[kind]),
        start=_int(values.get("numStart", "1"), "numStart"),
        restart=values.get("numRestart", "continuous"),
    )


def _note_mark(value: int, fmt: str) -> str:
    """The mark for the ``value``-th note in the note number format ``fmt``."""
    if fmt not in _NOTE_FORMATS:
        raise _refuse_numbering(f"the note number format {fmt}")
    if fmt != "chicago":
        return _number(value, fmt)
    if value < 1:
        raise _refuse_numbering(f"the note number {value} in symbols")
    # *, †, ‡, §, then each doubled, then tripled...
    return _CHICAGO[(value - 1) % 4] * ((value - 1) // 4 + 1)


def _note_marks(
    paragraphs: list[Paragraph], contexts: list[_Context], sections: list[ET.Element | None]
) -> dict[tuple[str, int], str | None]:
    """The mark of every note the body refers to, by Word's rules.

    Each rule is Word's answer to a case in corpus/numbering-cases, named in brackets. A note's
    number is its section's numStart plus the number of notes of its kind before it: in the
    document [notes-continuous, notes-section-start-continuous], or in its section when the
    section restarts them [notes-each-section]; footnotes and endnotes count apart
    [notes-mixed]. It is drawn in its section's format [notes-section-format,
    notes-section-chicago]. A note with a custom mark takes no number [notes-custom-mark].
    """
    marks: dict[tuple[str, int], str | None] = {}
    before: dict[tuple[str, int | None], int] = {}
    for paragraph, context in zip(paragraphs, contexts, strict=True):
        for reference in paragraph.notes:
            kind, key = reference.kind, (reference.kind, reference.id)
            if key in marks:
                raise DocxRefusedError(
                    "invalid-package", f"{kind} {reference.id} referred to twice"
                )
            if key in context.custom:
                marks[key] = None
                continue
            rules = _note_rules(kind, sections[context.section])
            if rules.restart == "eachPage":
                # The count restarts on each page, which depends on how Word lays the pages out.
                raise DocxRefusedError("ambiguous-numbering", f"{kind} numbers restart each page")
            if rules.restart not in ("continuous", "eachSect"):
                raise _refuse_numbering(f"{kind} numbers restart {rules.restart}")
            scope = (kind, context.section if rules.restart == "eachSect" else None)
            marks[key] = _note_mark(rules.start + before.get(scope, 0), rules.format)
            # Every note counts in the document and in its section, whichever rule a later
            # section follows.
            for counted in {(kind, None), (kind, context.section)}:
                before[counted] = before.get(counted, 0) + 1
    return marks


def _read_notes(
    root: ET.Element | None, kind: str, styles: _Styles
) -> dict[int, tuple[Paragraph, ...]]:
    """The paragraphs of every note in a footnotes or endnotes part, by id."""
    if root is None:
        return {}
    _check_part(root)
    notes: dict[int, tuple[Paragraph, ...]] = {}
    for element in root:
        if element.tag != _w(kind):
            raise DocxRefusedError("unsupported-element", f"{_local(element.tag)} in {kind}s")
        if element.get(_w("type"), "normal") in _NOTE_LAYOUT:
            continue
        if element.get(_w("type"), "normal") != "normal":
            raise DocxRefusedError(
                "unsupported-element", f"a {kind} of type {element.get(_w('type'))}"
            )
        note = _int(element.get(_w("id"), ""), f"{kind} id")
        if note in notes:
            raise DocxRefusedError("invalid-package", f"{kind} {note} is defined twice")
        runs: set[ET.Element] = set()
        reader = _Body(styles, runs, (kind, note))
        reader.blocks(element, None, None)
        _check_accounted(element, runs)
        if any(c.fields for c in reader.contexts):
            # Whether Word counts a SEQ in a note with the body's is not yet on record.
            raise DocxRefusedError("computed-field", f"a SEQ or STYLEREF field in a {kind}")
        if any(p.numbering is not None and p.numbering.num_id for p in reader.out):
            # Whether a list in a note counts with the body's lists is not yet on record.
            raise _refuse_numbering(f"a list in a {kind}")
        notes[note] = tuple(reader.out)
    return notes


def _with_marks(paragraph: Paragraph, marks: dict[tuple[str, int], str | None]) -> Paragraph:
    if not paragraph.notes:
        return paragraph
    return replace(
        paragraph, notes=tuple(replace(n, mark=marks[(n.kind, n.id)]) for n in paragraph.notes)
    )


# --- computed fields -----------------------------------------------------------------------

# SEQ's number formats (\\*), as list formats; ARABIC is matched without regard to case.
_SEQ_FORMATS = {
    "ARABIC": "decimal",
    "ROMAN": "upperRoman",
    "roman": "lowerRoman",
    "ALPHABETIC": "upperLetter",
    "alphabetic": "lowerLetter",
}
# \\* switches that change how the result is formatted, not what it says.
_FORMATTING = {"MERGEFORMAT", "CHARFORMAT"}
_HEADING = re.compile(r"heading ([1-9])")
_FIELD_TOKENS = re.compile(r'"([^"]*)"|(\S+)')


def _tokens(instruction: str) -> list[str]:
    return [quoted or bare for quoted, bare in _FIELD_TOKENS.findall(instruction)]


def _switches(
    tokens: list[str], with_argument: set[str], flags: set[str]
) -> tuple[list[str], dict[str, str]]:
    """The arguments and the switches of a field after its code; refused if unknown."""
    arguments: list[str] = []
    switches: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("\\"):
            arguments.append(token)
        elif token[1:] == "*":
            index += 1
            value = tokens[index] if index < len(tokens) else ""
            if value.upper() not in _FORMATTING:
                if "*" in switches:
                    raise DocxRefusedError("computed-field", "a field with two number formats")
                switches["*"] = value
        elif token[1:] in with_argument:
            index += 1
            switches[token[1:]] = tokens[index] if index < len(tokens) else ""
        elif token[1:] in flags:
            switches[token[1:]] = ""
        else:
            raise DocxRefusedError("computed-field", f"a field switch {token}")
        index += 1
    return arguments, switches


def _heading_level(style: str | None, styles: _Styles) -> int | None:
    """The level of a built-in heading style: Word goes by the name, not the outline level."""
    name = styles.styles[style].name if style in styles.styles else None
    match = _HEADING.fullmatch((name or "").lower())
    return int(match.group(1)) if match else None


def _verify_fields(
    paragraphs: list[Paragraph], contexts: list[_Context], styles: _Styles, loose: set[str]
) -> None:
    """Refuse a SEQ or STYLEREF field whose stored result is not what Word prints.

    Word shows a field's stored result on screen and recomputes SEQ and STYLEREF when it prints
    or saves as PDF (corpus/numbering-cases, fields-stale); a stored result that differs is a
    document whose screen and print disagree. Each rule is Word's answer to a case there, named
    in brackets.
    """
    names = [
        (styles.styles[c.style].name or "").lower() if c.style in styles.styles else ""
        for c in contexts
    ]
    levels = [_heading_level(c.style, styles) for c in contexts]
    bookmarks = _bookmarks(contexts, loose)
    # Per SEQ identifier: its value, and the paragraph of its last field.
    counted: dict[str, tuple[int, int]] = {}
    for index, (paragraph, context) in enumerate(zip(paragraphs, contexts, strict=True)):
        for instruction, start, end in context.fields:
            tokens = _tokens(instruction)
            code = tokens[0].upper()
            if code == "SEQ":
                shown = _seq(tokens[1:], index, counted, levels)
            elif code == "STYLEREF":
                shown = _styleref(tokens[1:], index, paragraphs, names)
            else:
                shown = _reference(code, tokens[1:], paragraphs, bookmarks)
            stored = paragraph.text[start:end]
            if stored != shown:
                raise DocxRefusedError(
                    "stale-field", f"a {code} field shows {stored!r}; Word prints {shown!r}"
                )


def _bookmarks(contexts: list[_Context], loose: set[str]) -> dict[str, tuple[int, int, int] | None]:
    """Each bookmark's paragraph, start and end; None for one REF cannot be read from.

    That is one that starts and ends in different paragraphs or between them, has no end, or
    shares its name with another.
    """
    starts: dict[str, tuple[str, int, int]] = {}
    ends: dict[str, tuple[int, int]] = {}
    for index, context in enumerate(contexts):
        for key, name, offset in context.bookmark_starts:
            starts[key] = (name, index, offset)
        for key, offset in context.bookmark_ends:
            ends[key] = (index, offset)
    spans: dict[str, tuple[int, int, int] | None] = {}
    for key, (name, index, start) in starts.items():
        end = ends.get(key)
        whole = key not in loose and end is not None and end[0] == index and name not in spans
        spans[name] = (index, start, end[1]) if whole and end is not None else None
    return spans


def _reference(
    code: str,
    tokens: list[str],
    paragraphs: list[Paragraph],
    bookmarks: dict[str, tuple[int, int, int] | None],
) -> str:
    """What REF (the bookmark's text) or NOTEREF (its note's mark) prints."""
    arguments, switches = _switches(tokens, set(), {"h", "f"} if code == "NOTEREF" else {"h"})
    if len(arguments) != 1 or "*" in switches:
        raise DocxRefusedError("computed-field", f"a {code} field the reader cannot compute")
    name = arguments[0]
    if name not in bookmarks:
        # Word prints "Error! Reference source not found." [fields-ref-missing].
        raise DocxRefusedError("computed-field", f"a {code} to a bookmark that is not there")
    span = bookmarks[name]
    if span is None:
        raise DocxRefusedError("computed-field", f"a {code} to a bookmark it cannot read")
    index, start, end = span
    paragraph = paragraphs[index]
    inside = [n for n in paragraph.notes if start <= n.offset <= end]
    if code == "NOTEREF":
        # The mark of the note referred to in the bookmark [fields-noteref].
        if len(inside) != 1 or inside[0].mark is None:
            raise DocxRefusedError("computed-field", "a NOTEREF to a bookmark without one note")
        return inside[0].mark
    if inside:
        raise DocxRefusedError("computed-field", "a REF to a bookmark holding a note mark")
    # The bookmark's text [fields-ref].
    return paragraph.text[start:end]


def _seq(
    tokens: list[str], index: int, counted: dict[str, tuple[int, int]], levels: list[int | None]
) -> str:
    arguments, switches = _switches(tokens, {"r", "s"}, {"c", "n", "h"})
    if len(arguments) != 1:
        raise DocxRefusedError("computed-field", "a SEQ field without one identifier")
    identifier = arguments[0]
    value, last = counted.get(identifier, (0, -1))
    if "s" in switches:
        level = _int(switches["s"], "SEQ \\s")
        # A heading of that level or higher since the last field restarts the count
        # [seq-chapter-reset, seq-s2-reset-by-h1].
        if any(lv is not None and lv <= level for lv in levels[last + 1 : index + 1]):
            value = 0
    if "r" in switches:
        value = _int(switches["r"], "SEQ \\r")
    elif "c" in switches:
        if identifier not in counted:
            raise DocxRefusedError("computed-field", "a SEQ \\c field before any count")
    else:
        # \\n, or no switch: the next number [seq-basic, seq-reset-repeat-next].
        value += 1
    counted[identifier] = (value, index)
    if "h" in switches:
        # Counted, and shown as nothing [seq-reset-repeat-next].
        return ""
    fmt = switches.get("*", "ARABIC")
    key = "ARABIC" if fmt.upper() == "ARABIC" else fmt
    if key not in _SEQ_FORMATS:
        raise DocxRefusedError("computed-field", f"a SEQ number format {fmt}")
    return _number(value, _SEQ_FORMATS[key])


def _styleref(tokens: list[str], index: int, paragraphs: list[Paragraph], names: list[str]) -> str:
    arguments, switches = _switches(tokens, set(), {"s"})
    if len(arguments) != 1:
        raise DocxRefusedError("computed-field", "a STYLEREF field without one style")
    wanted = arguments[0].lower()
    if wanted.isdigit() and len(wanted) == 1 and wanted != "0":
        wanted = f"heading {wanted}"
    if names[index] == wanted:
        raise DocxRefusedError("computed-field", "a STYLEREF field in a paragraph of its style")
    # The nearest paragraph of the style before the field, else the nearest after
    # [styleref-caption, styleref-none-before].
    before = [i for i in range(index - 1, -1, -1) if names[i] == wanted]
    after = [i for i in range(index + 1, len(names)) if names[i] == wanted]
    if not before and not after:
        raise DocxRefusedError(
            "computed-field", f"a STYLEREF to {wanted!r}, which no paragraph has"
        )
    target = paragraphs[(before or after)[0]]
    if target.notes:
        raise DocxRefusedError("computed-field", "a STYLEREF to a paragraph with a note mark")
    if "s" not in switches:
        return target.text
    label = target.numbering.text if target.numbering is not None else None
    if label is None or not re.fullmatch(r"[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*\.?", label):
        raise DocxRefusedError("computed-field", "a STYLEREF \\s to a label it cannot read")
    # The label without its final period [styleref-dotted].
    return label.removesuffix(".")


# --- blocks and tables ---------------------------------------------------------------------


class _Body:
    """Reads the blocks of one story: the body, or one note."""

    def __init__(
        self, styles: _Styles, runs: set[ET.Element], story: tuple[str, int] | None = None
    ) -> None:
        self.styles = styles
        self.story = story
        self.out: list[Paragraph] = []
        self.contexts: list[_Context] = []
        self.tables = 0
        self.runs = runs
        # The sectPr closing each section so far; a paragraph holding one ends its section.
        self.sections: list[ET.Element] = []
        # Ids of bookmarks that start or end between paragraphs, which REF cannot be read from.
        self.loose_bookmarks: set[str] = set()

    def blocks(
        self, element: ET.Element, table: tuple[int, int, int] | None, table_style: str | None
    ) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("p"):
                paragraph, context = _paragraph(
                    child,
                    self.styles,
                    table,
                    table_style,
                    self.runs,
                    self.story,
                    len(self.sections),
                )
                self.out.append(paragraph)
                self.contexts.append(context)
                closing = child.find(f"{_w('pPr')}/{_w('sectPr')}")
                if closing is not None:
                    self.sections.append(closing)
            elif tag == _w("tbl"):
                self.table(child, table)
            elif tag == _w("sdt"):
                _content_control(child)
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.blocks(content, table, table_style)
            elif tag == _w("customXml"):
                self.blocks(child, table, table_style)
            elif tag in (_w("bookmarkStart"), _w("bookmarkEnd")):
                self.loose_bookmarks.add(child.get(_w("id"), ""))
            elif tag in (_w("sectPr"), _w("tcPr")) or tag in _PROPERTIES or tag in _MARKERS:
                continue
            else:
                raise DocxRefusedError("unsupported-element", _local(tag))

    def table(self, element: ET.Element, outer: tuple[int, int, int] | None) -> None:
        index = self.tables
        self.tables += 1
        style_element = element.find(f"{_w('tblPr')}/{_w('tblStyle')}")
        table_style = self.styles.effective(
            style_element.get(_w("val")) if style_element is not None else None, "table"
        )
        problem = next(
            (s.conditional for s in self.styles.chain(table_style) if s.conditional), None
        )
        if problem is not None:
            # Formatting for the first row, banded rows and the like; the reader does not apply
            # it, so formatting that could hide or change text is refused.
            raise DocxRefusedError("unsupported-element", problem)
        rows: list[ET.Element] = []
        _collect(element, _w("tr"), rows, {_w("tblPr"), _w("tblGrid")})
        for row_index, row in enumerate(rows):
            cells: list[ET.Element] = []
            _collect(row, _w("tc"), cells, {_w("trPr"), _w("tblPrEx")})
            for cell_index, cell in enumerate(cells):
                start = len(self.out)
                self.blocks(cell, outer or (index, row_index, cell_index), table_style)
                merge = cell.find(f"{_w('tcPr')}/{_w('vMerge')}")
                continued = merge is not None and merge.get(_w("val")) in (None, "continue")
                if continued and any(p.text.strip() for p in self.out[start:]):
                    raise DocxRefusedError("unsupported-element", "text in a merged-away cell")


def _collect(element: ET.Element, wanted: str, out: list[ET.Element], silent: set[str]) -> None:
    """The ``wanted`` children of a table or row, through content controls and custom XML."""
    for child in element:
        tag = child.tag
        if tag == wanted:
            out.append(child)
        elif tag == _w("sdt"):
            _content_control(child)
            content = child.find(_w("sdtContent"))
            if content is not None:
                _collect(content, wanted, out, silent)
        elif tag == _w("customXml"):
            _collect(child, wanted, out, silent)
        elif tag in silent or tag in _PROPERTIES or tag in _MARKERS:
            continue
        else:
            raise DocxRefusedError("unsupported-element", _local(tag))


def read_docx(data: bytes) -> list[Paragraph]:
    """Every body paragraph of a .docx, in document order, or ``DocxRefusedError``."""
    return list(read_document(data).body)


def read_document(data: bytes) -> Document:
    """The body, footnotes and endnotes of a .docx, or ``DocxRefusedError``."""
    package = _Package(data)
    with package.zip:
        mains = package.related("", "officeDocument")
        if len(mains) != 1:
            raise DocxRefusedError("invalid-package", f"{len(mains)} main document parts")
        document = package.part(mains[0])
        if document is None:
            raise DocxRefusedError("invalid-package", f"no {mains[0]}")
        parts: list[ET.Element | None] = []
        for kind in ("styles", "theme", "fontTable", "settings", "numbering", *_NOTE_KINDS):
            targets = package.related(mains[0], kind + "s" if kind in _NOTE_KINDS else kind)
            if len(targets) > 1:
                raise DocxRefusedError("invalid-package", f"more than one {kind} part")
            part = package.part(targets[0]) if targets else None
            if targets and part is None:
                raise DocxRefusedError("invalid-package", f"no {targets[0]}")
            parts.append(part)
        styles = _styles(*parts[:3])
        if parts[3] is not None:
            styles.update_fields = bool(_on(parts[3].find(_w("updateFields"))))
        lists = _Lists(parts[4], styles)
    _check_part(document)
    body = document.find(_w("body"))
    if body is None:
        raise DocxRefusedError("invalid-package", "no w:body")
    runs: set[ET.Element] = set()
    reader = _Body(styles, runs)
    reader.blocks(body, None, None)
    _check_accounted(document, runs)
    paragraphs = _labelled(reader.out, reader.contexts, lists)
    sections: list[ET.Element | None] = [*reader.sections, body.find(_w("sectPr"))]
    marks = _note_marks(paragraphs, reader.contexts, sections)
    # NOTEREF prints a note's mark, so the fields are checked once the marks are known.
    _verify_fields(
        [_with_marks(p, marks) for p in paragraphs], reader.contexts, styles, reader.loose_bookmarks
    )
    notes = {
        kind: _read_notes(part, kind, styles)
        for kind, part in zip(_NOTE_KINDS, parts[5:], strict=True)
    }
    for kind, key in marks:
        if key not in notes[kind]:
            raise DocxRefusedError("invalid-package", f"a reference to {kind} {key}, not defined")
    for (kind, key), mark in marks.items():
        if mark is None and any(n.kind == kind for p in notes[kind][key] for n in p.notes):
            # Word draws the next note's number there, which no reference shows
            # (corpus/numbering-cases, notes-custom-mark).
            raise DocxRefusedError("ambiguous-numbering", f"the mark in custom-marked {kind} {key}")
    for kind in _NOTE_KINDS:
        unreferenced = sorted(key for key in notes[kind] if (kind, key) not in marks)
        if unreferenced:
            # Word does not show a note nothing refers to; its text is in the file all the same.
            raise DocxRefusedError("unread-content", f"{kind} {unreferenced[0]}, never referred to")
    order = [(n.kind, n.id) for paragraph in paragraphs for n in paragraph.notes]

    def in_order(kind: str) -> tuple[Note, ...]:
        return tuple(
            Note(
                kind,
                key,
                marks[(kind, key)],
                tuple(_with_marks(p, marks) for p in notes[kind][key]),
            )
            for k, key in order
            if k == kind
        )

    return Document(
        body=tuple(_with_marks(p, marks) for p in paragraphs),
        footnotes=in_order("footnote"),
        endnotes=in_order("endnote"),
    )


def _check_part(root: ET.Element) -> None:
    """Refuse a part with tracked changes, alternate content, or text outside text elements."""
    for element in root.iter():
        if element.tag in _TRACKED:
            raise DocxRefusedError("tracked-change", _local(element.tag))
        if element.tag == f"{{{MC}}}AlternateContent":
            raise DocxRefusedError("unsupported-element", "AlternateContent")
    _check_character_data(root)


def _check_character_data(document: ET.Element) -> None:
    """Refuse character data the reader would not read.

    Only ``<w:t>`` and ``<w:instrText>`` hold text; elsewhere in WordprocessingML character data
    is whitespace between elements. Elements of other namespaces (DrawingML positions, say) hold
    values, not text, and are left to the rules for their containers.
    """
    for element in document.iter():
        if not element.tag.startswith(f"{{{W}}}"):
            continue
        if element.tag in _TEXT_ELEMENTS:
            if len(element):
                raise DocxRefusedError("stray-text", f"an element inside {_local(element.tag)}")
        elif (element.text or "").strip(_XML_WHITESPACE):
            raise DocxRefusedError("stray-text", f"character data in {_local(element.tag)}")
        if any((child.tail or "").strip(_XML_WHITESPACE) for child in element):
            raise DocxRefusedError("stray-text", f"character data in {_local(element.tag)}")


def _check_accounted(document: ET.Element, runs: set[ET.Element]) -> None:
    """Refuse a run the reader did not read, or run content that is not in a run.

    The reader walks the body by its own rules and passes over some containers whole (section,
    paragraph and cell properties). This checks that walk against every element of the part, so
    a run in a place the walk does not go is refused instead of lost.
    """
    for element in document.iter():
        if element.tag == _w("r") and element not in runs:
            raise DocxRefusedError("unread-content", "a run the reader did not reach")
        if element.tag != _w("r"):
            for child in element:
                if child.tag in _RUN_CONTENT:
                    raise DocxRefusedError("unread-content", f"{_local(child.tag)} outside a run")
