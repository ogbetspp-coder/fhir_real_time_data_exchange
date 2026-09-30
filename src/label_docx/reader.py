"""A fail-closed reader for the text of a Word (.docx) body.

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
  REPLACEMENT CHARACTER at the place it stands.
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

Styles. Run properties are looked up on the run, then its character style, its paragraph
style, its table style (inside a table only) and the document defaults, each style with its
``basedOn`` chain. An absent or unknown style id falls back to the document's default style of
that kind (the last one marked default), as Word does; a reference to a style of another kind
is refused. Paragraph shading and right-to-left are looked up the same way through the
paragraph properties. A table whose effective table style has conditional formatting
(``tblStylePr`` for the first row, banded rows and so on) is refused, because the reader does
not apply it.

Symbol fonts. A run whose effective ``ascii`` and ``hAnsi`` fonts (set directly, by a style, by
the document defaults or through the theme) are both Symbol, with no complex-script or
right-to-left property and no font hint other than ``default`` (which sends ambiguous characters
to the ``hAnsi`` font, Symbol here), has every character mapped through ``SYMBOL_FONT``; a
character the table does not hold is refused. ``<w:sym>`` in the Symbol font is mapped the same
way. Any other run with Symbol in one of its four font slots is refused, because Word picks the
font per character and the reader cannot be sure which characters it draws in Symbol. A dingbat
font (Wingdings, Webdings, Zapf Dingbats, Marlett, MT Extra), or any font the document's font
table declares symbol-encoded (charset 02), is refused.

Fields keep their stored result and drop their instruction, however deeply nested, so
``DOCPROPERTY ... MERGEFORMAT`` never reaches the text. Only fields whose stored result is what
Word shows are read: HYPERLINK, REF, NOTEREF and DOCPROPERTY. Any other field whose result
would be shown (PAGE, DATE, SEQ, IF, a formula...) is refused, because Word recomputes it on
display or print. The code is the first word of the instruction; a field nested in the
instruction ahead of or inside that word makes the code unknown, and the field is refused. So
are a field with no stored result (no ``separate``, such as a form checkbox or a SYMBOL field,
or an empty ``fldSimple``), a form field, a field marked for update, any field in a document
whose settings ask Word to update fields on open, and field code outside an instruction.

List labels. Word draws "4.8", "b)" or a bullet before a numbered paragraph from the numbering
part; the reader computes that label the way Word does and refuses where the result is not
certain. A paragraph's ``numId`` names a ``w:num``, which names an ``abstractNum`` (through one
numbering-style link, ``numStyleLink``, if it has one); a level of the ``w:num``'s
``lvlOverride`` replaces the abstract level whole, and its ``startOverride`` replaces the start.
Counters belong to the ``abstractNum``: lists that share one continue each other's numbers, which
is why Word writes a ``startOverride`` to restart one. A paragraph at level ``L`` restarts every
deeper level (``lvlRestart`` 0 never restarts it; ``lvlRestart`` ``n`` restarts it only after a
level up to ``n - 1``) and then counts its own: the level's start the first time, one more after.
``lvlText`` is copied, with ``%1`` to ``%9`` replaced by the counter of that level in that
level's format (all decimal under ``isLgl``): decimal, decimalZero, upper and lower roman
(1 to 3999), upper and lower letter (a to z, then aa, bb...), or none; a bullet level's text is
its bullet. The label is drawn in the level's run properties over the paragraph mark's, so its
fonts are placed as a run's are: a Symbol bullet (U+F0B7) is mapped to "•", a Wingdings one is
refused. ``suffix`` is ``tab``, ``space`` or ``nothing`` (``w:suff``), or ``legacy`` for a
Word 6 level, where the gap is layout and not a character. A counter is certain when the
paragraphs of one ``abstractNum`` stay in one ``w:num``, move between ``w:num`` elements that
override nothing, or move to a new ``w:num`` that restarts the level (``startOverride``); after any
other move, and before a level is first counted, it is unknown, and a label that shows it is
refused. A bullet shows no counter and is never refused for one.

What it refuses (``DocxRefusedError.code``):

- ``tracked-change``: any revision anywhere in the body, including changed formatting and
  deleted paragraph marks. Such a document has more than one text.
- ``hidden-text``: a run with text that is hidden, directly or at any level of the style
  hierarchy (hiding is treated as a fact as soon as any level asserts it, unless the run itself
  says it is visible).
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
- ``stale-field``: a field marked for update.
- ``unsupported-element``: anything that can carry text and is not read above, and any element
  the reader does not know: text boxes, footnote and endnote references, embedded objects,
  charts and other non-picture drawings, alternate content, math, ``altChunk``, form fields,
  content controls bound to data (in any namespace), conditional table formatting, text in a
  vertically merged-away cell, and a style reference that names a style of another kind.
- ``invalid-package``: not a readable .docx, no main document relationship, a part name that
  occurs twice (ignoring case), a related part that is missing or duplicated, a part that
  cannot be read (bad checksum, truncated, encrypted), a part that is not UTF-8 or declares
  another encoding, a DTD, a part over the size cap, a style id defined twice, or a list number
  that is not a number.
- ``stray-text``: character data in the main document part outside ``<w:t>`` and
  ``<w:instrText>`` (whitespace between elements aside), or an element inside either of them.
- ``unread-content``: a run the reader did not reach (inside section, paragraph or cell
  properties, say), or run content standing outside a run.
- ``unsupported-numbering``: a list label the reader cannot draw exactly: a ``numId`` or level
  with no definition (or no numbering part), a level outside 0 to 8, a format other than those
  above (ordinal and text formats depend on the language), a custom format, a picture bullet, a
  level holding anything else the reader does not know (alternate content, say), a ``%n`` for a
  deeper level, a bullet level that shows a counter, a number past a format's range, or a label in
  capitals or small capitals with letters in it.
- ``ambiguous-numbering``: a list label that shows a counter the reader cannot be sure of (above),
  a label drawn hidden (the paragraph mark or the level is hidden), a numbered paragraph run on
  after a hidden paragraph mark, or a paragraph style that names a list level other than 0 for a
  paragraph that sets no level.

Headers, footers, footnotes, comments and the glossary are separate parts and are not read.
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
# accepts the font hint "default".
READER_VERSION = "docx-reader/1.3.0"

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
# Paragraph-level containers whose children are read as the paragraph's own. fldSimple's
# children are the field's displayed result; its instruction is an attribute and is dropped.
_INLINE_TRANSPARENT = {_w(name) for name in ("hyperlink", "smartTag", "customXml")}
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
class Paragraph:
    """One paragraph of the body, as the reader produced it.

    The module docstring describes ``text``, ``marks``, ``mark_hidden``, ``numbering`` and
    ``table``; ``style`` is the paragraph style id written on the paragraph, if any.
    """

    text: str
    style: str | None
    numbering: Numbering | None
    table: tuple[int, int, int] | None
    marks: tuple[Mark, ...] = ()
    mark_hidden: bool = False

    @property
    def has_drawing(self) -> bool:
        """Whether the text holds a picture (U+FFFC OBJECT REPLACEMENT CHARACTER)."""
        return OBJECT in self.text


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


class _Package:
    def __init__(self, data: bytes) -> None:
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except Exception as error:  # zipfile raises many types for a damaged archive
            raise DocxRefusedError("invalid-package", "not a readable zip archive") from error
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
    # A table style with formatting for its first row, banded rows and the like.
    conditional: bool = False


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
        kind = style.get(_w("type"), "paragraph")
        styles.styles[style_id] = _Style(
            kind=kind,
            based_on=based.get(_w("val")) if based is not None else None,
            rpr=style.find(_w("rPr")),
            ppr=style.find(_w("pPr")),
            conditional=any(
                part.find(_w("rPr")) is not None or part.find(_w("pPr")) is not None
                for part in style.findall(_w("tblStylePr"))
            ),
        )
        if style.get(_w("default")) in ("1", "true", "on"):
            # With more than one default of a kind, the last one is used (ECMA-376 17.7.4.17).
            styles.defaults[kind] = style_id
    return styles


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
                return self._theme_font(theme)
            name = fonts.get(_w(slot))
            if name is not None:
                return name
        return None

    def _theme_font(self, theme: str) -> str:
        if not self.styles.has_theme:
            raise DocxRefusedError("symbol-font", f"theme font {theme} without a theme")
        for prefix in ("major", "minor"):
            if theme.startswith(prefix):
                script = {"HAnsi": "Latin", "Ascii": "Latin"}.get(theme[len(prefix) :])
                script = script or theme[len(prefix) :]
                key = prefix + script
                if key in self.styles.theme_fonts:
                    return self.styles.theme_fonts[key]
        raise DocxRefusedError("symbol-font", f"theme font {theme} is not in the theme")


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


def _in_symbol(styles: _Styles, properties: _Properties) -> bool:
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


class _ParagraphReader:
    def __init__(
        self,
        styles: _Styles,
        paragraph_style: str | None,
        table_style: str | None,
        runs: set[ET.Element],
    ) -> None:
        self.styles = styles
        self.paragraph_style = paragraph_style
        self.table_style = table_style
        # Every run read, shared across the body, for the accounting in read_docx.
        self.runs = runs
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
                if not self.in_instruction():
                    _check_field(child.get(_w("instr"), ""))
                before = self.length
                self.container(child)
                if self.length == before:
                    raise DocxRefusedError("field-without-result", "a simple field shows nothing")
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
            elif tag in (_w("pPr"), _w("sdtPr")) or tag in _MARKERS:
                continue
            else:
                raise DocxRefusedError("unsupported-element", _local(tag))

    def run(self, run: ET.Element) -> None:
        self.runs.add(run)
        properties = _Properties(
            self.styles, run.find(_w("rPr")), self.paragraph_style, self.table_style
        )
        symbol = _in_symbol(self.styles, properties)
        emitted: list[str] = []
        for child in run:
            tag = child.tag
            if tag == _w("fldChar"):
                self._field(child)
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
                emitted.append(produced)
            elif self.fields[-1]:
                self.instructions[-1].append(produced)
        text = "".join(emitted)
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

    def _field(self, child: ET.Element) -> None:
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
        elif kind == "separate" and self.fields:
            if not any(self.fields[:-1]):
                # The result is shown, so it must be one Word shows as stored.
                _check_field("".join(self.instructions[-1]))
            self.fields[-1] = False
        elif kind == "end" and self.fields:
            if self.fields[-1]:
                # No separate: the field stores no result, and what Word shows is computed.
                raise DocxRefusedError("field-without-result", "a field with no stored result")
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


def _faint(properties: _Properties) -> bool:
    """Whether text with these properties is easy not to see.

    White text (or a light theme colour), text under two points, or text scaled under a fifth
    is. A size or scale the reader cannot parse counts as faint.
    """
    color = properties.element("color")
    if color is not None:
        theme = (color.get(_w("themeColor")) or "").lower()
        if theme.startswith(("background", "light", "bg")):
            return True
        if (color.get(_w("val")) or "").lower() in ("ffffff", "white"):
            return True
    # szCs sizes complex-script text; the reader does not know which script a character is
    # drawn as, so either size being tiny counts.
    for size in (properties.value("sz"), properties.value("szCs")):
        if size is None:
            continue
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(pt|pc|pi|in|cm|mm)?", size)
        if match is None:
            return True
        points = (
            float(match.group(1)) * _POINTS[match.group(2)]
            if match.group(2)
            else float(match.group(1)) / 2
        )
        if points < 2:
            return True
    scale = properties.value("w")
    if scale is not None:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%?", scale)
        return match is None or float(match.group(1)) < 20
    return False


# Fields whose stored result is what Word shows until someone updates them by hand. Word
# recomputes others when it lays out or prints the page (PAGE, NUMPAGES, DATE, TIME, SEQ,
# AUTONUM, LISTNUM, IF, formulas...), so their stored result may not be what a reader sees.
_STORED_FIELDS = {"HYPERLINK", "REF", "NOTEREF", "DOCPROPERTY"}


def _check_field(instruction: str) -> None:
    words = instruction.split()
    code = words[0].upper() if words else ""
    if code not in _STORED_FIELDS:
        raise DocxRefusedError("computed-field", f"a {code or 'blank'} field")


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


def _numbering(levels: list[ET.Element | None]) -> tuple[Numbering | None, bool]:
    """The numId and ilvl, each from the nearest paragraph-properties level that sets it.

    The flag says whether any level set ilvl, rather than it defaulting to 0.
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
        return None, False
    return Numbering(num_id=found.get("numId", 0), level=found.get("ilvl", 0)), "ilvl" in found


@dataclass(frozen=True)
class _ListContext:
    """What the list-label pass needs of a paragraph besides the paragraph itself."""

    # The paragraph style in force (after falling back to the default), its table's style, and
    # the paragraph mark's run properties, which a list label is drawn over.
    style: str | None
    table_style: str | None
    mark: ET.Element | None
    level_set: bool


def _paragraph(
    element: ET.Element,
    styles: _Styles,
    table: tuple[int, int, int] | None,
    table_style: str | None,
    runs: set[ET.Element],
) -> tuple[Paragraph, _ListContext]:
    ppr = element.find(_w("pPr"))
    style = None
    if ppr is not None:
        style_element = ppr.find(_w("pStyle"))
        style = style_element.get(_w("val")) if style_element is not None else None
    mark_rpr = ppr.find(_w("rPr")) if ppr is not None else None
    mark = _Properties(styles, mark_rpr, style, table_style)
    mark_hidden = mark.toggle("vanish") or mark.toggle("specVanish")
    reader = _ParagraphReader(styles, style, table_style, runs)
    reader.container(element)
    if reader.in_instruction():
        raise DocxRefusedError("unbalanced-field", "a paragraph ends inside a field instruction")
    numbering, level_set = _numbering(
        [
            ppr,
            *(s.ppr for s in styles.resolve(style, "paragraph")),
            *(s.ppr for s in styles.chain(table_style)),
            styles.default_ppr,
        ]
    )
    context = _ListContext(
        style=styles.effective(style, "paragraph"),
        table_style=table_style,
        mark=mark_rpr,
        level_set=level_set,
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

    @property
    def overrides(self) -> bool:
        return bool(self.starts or self.levels)


@dataclass(frozen=True)
class _Abstract:
    levels: dict[int, _Level]
    link: str | None


@dataclass
class _Counters:
    """The counters of one abstractNum, and which list (numId) is counting in it."""

    values: list[int | None] = field(default_factory=lambda: [None] * len(_LEVELS))
    unknown: list[bool] = field(default_factory=lambda: [False] * len(_LEVELS))
    current: int | None = None
    used: set[int] = field(default_factory=set)


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
            self.abstracts[key] = _Abstract(levels, None if link is None else link.get(_w("val")))
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

    def definitions(self, num_id: int) -> tuple[int, _Num, dict[int, _Level]]:
        """The abstractNum counting for ``num_id``, the num, and its levels after overrides."""
        if not self.present:
            raise _refuse_numbering("a list with no numbering part")
        num = self.nums.get(num_id)
        if num is None or num.abstract is None or num.abstract not in self.abstracts:
            raise _refuse_numbering(f"numId {num_id} is not defined")
        abstract_id = num.abstract
        abstract = self.abstracts[abstract_id]
        if abstract.link is not None:
            abstract_id = self._linked(abstract.link)
            abstract = self.abstracts[abstract_id]
        return abstract_id, num, {**abstract.levels, **num.levels}

    def _linked(self, name: str) -> int:
        """The abstractNum a numbering style names: one link, to a list that overrides nothing."""
        style = self.styles.styles.get(name)
        if style is None or style.kind != "numbering":
            raise _refuse_numbering(f"numbering style {name!r} is not defined")
        numbering, _ = _numbering([style.ppr])
        linked = self.nums.get(numbering.num_id) if numbering is not None else None
        if (
            linked is None
            or linked.abstract is None
            or linked.overrides
            or linked.abstract not in self.abstracts
            or self.abstracts[linked.abstract].link is not None
        ):
            raise _refuse_numbering(f"numbering style {name!r} names no list the reader can use")
        return linked.abstract

    def label(self, numbering: Numbering, context: _ListContext) -> Numbering:
        """``numbering`` with the label Word draws, counted in document order."""
        level = numbering.level
        if level not in _LEVELS:
            raise _refuse_numbering(f"list level {level}")
        abstract_id, num, levels = self.definitions(numbering.num_id)
        if not context.level_set and any(
            k != 0 and d.style is not None and d.style == context.style for k, d in levels.items()
        ):
            # The paragraph sets no level and its style is tied to a deeper one; which of the two
            # Word uses is not documented.
            raise DocxRefusedError("ambiguous-numbering", "a style tied to a list level not set")
        counters = self.counters.setdefault(abstract_id, _Counters())
        self._move(counters, numbering.num_id, num)
        definition = levels.get(level)
        if definition is None:
            raise _refuse_numbering(f"level {level} of numId {numbering.num_id} is not defined")
        self._count(counters, level, levels, num)
        text = self._draw(counters, level, levels, definition, context)
        return replace(numbering, text=text, suffix=definition.suffix)

    def _move(self, counters: _Counters, num_id: int, num: _Num) -> None:
        """Carry the counters over to ``num_id`` where Word's behaviour is certain; else unknown."""
        if counters.current == num_id:
            return
        first = counters.current is None
        new = num_id not in counters.used
        plain_before = not any(self.nums[n].overrides for n in counters.used)
        counters.current = num_id
        counters.used.add(num_id)
        if first or (plain_before and not num.overrides):
            # A fresh set of counters, or lists that share one and override nothing.
            return
        for level in _LEVELS:
            if new and level in num.starts:
                counters.values[level] = None
                counters.unknown[level] = False
            elif not (new and plain_before and level not in num.levels):
                counters.unknown[level] = True

    def _count(self, counters: _Counters, level: int, levels: dict[int, _Level], num: _Num) -> None:
        for deeper in range(level + 1, len(_LEVELS)):
            definition = levels.get(deeper)
            restart = definition.restart if definition is not None else None
            # lvlRestart n restarts the level after a paragraph at a level up to n - 1; 0 never.
            # A value that is not a higher level is ignored, and then any higher level restarts.
            if restart is None or level < restart or restart - 1 >= deeper:
                counters.values[deeper] = None
                counters.unknown[deeper] = False
        if counters.unknown[level]:
            return
        current = counters.values[level]
        if current is not None:
            counters.values[level] = current + 1
            return
        start = num.starts.get(level, levels[level].start)
        if start is None:
            counters.unknown[level] = True
        counters.values[level] = start

    def _draw(
        self,
        counters: _Counters,
        level: int,
        levels: dict[int, _Level],
        definition: _Level,
        context: _ListContext,
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
        label = _characters("".join(pieces), _in_symbol(self.styles, properties))
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
    paragraphs: list[Paragraph], contexts: list[_ListContext], lists: _Lists
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


# --- blocks and tables ---------------------------------------------------------------------


class _Body:
    def __init__(self, styles: _Styles) -> None:
        self.styles = styles
        self.out: list[Paragraph] = []
        self.contexts: list[_ListContext] = []
        self.tables = 0
        self.runs: set[ET.Element] = set()

    def blocks(
        self, element: ET.Element, table: tuple[int, int, int] | None, table_style: str | None
    ) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("p"):
                paragraph, context = _paragraph(child, self.styles, table, table_style, self.runs)
                self.out.append(paragraph)
                self.contexts.append(context)
            elif tag == _w("tbl"):
                self.table(child, table)
            elif tag == _w("sdt"):
                _content_control(child)
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.blocks(content, table, table_style)
            elif tag == _w("customXml"):
                self.blocks(child, table, table_style)
            elif tag in (_w("sectPr"), _w("tcPr"), _w("sdtPr")) or tag in _MARKERS:
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
        if any(style.conditional for style in self.styles.chain(table_style)):
            # Formatting for the first row, banded rows and the like; the reader does not apply
            # it, so it could hide or change text unseen.
            raise DocxRefusedError("unsupported-element", "conditional table formatting")
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
        elif tag in silent or tag == _w("sdtPr") or tag in _MARKERS:
            continue
        else:
            raise DocxRefusedError("unsupported-element", _local(tag))


def read_docx(data: bytes) -> list[Paragraph]:
    """Every body paragraph of a .docx, in document order, or ``DocxRefusedError``."""
    package = _Package(data)
    with package.zip:
        mains = package.related("", "officeDocument")
        if len(mains) != 1:
            raise DocxRefusedError("invalid-package", f"{len(mains)} main document parts")
        document = package.part(mains[0])
        if document is None:
            raise DocxRefusedError("invalid-package", f"no {mains[0]}")
        parts: list[ET.Element | None] = []
        for kind in ("styles", "theme", "fontTable", "settings", "numbering"):
            targets = package.related(mains[0], kind)
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
    for element in document.iter():
        if element.tag in _TRACKED:
            raise DocxRefusedError("tracked-change", _local(element.tag))
        if element.tag == f"{{{MC}}}AlternateContent":
            raise DocxRefusedError("unsupported-element", "AlternateContent")
    _check_character_data(document)
    body = document.find(_w("body"))
    if body is None:
        raise DocxRefusedError("invalid-package", "no w:body")
    reader = _Body(styles)
    reader.blocks(body, None, None)
    _check_accounted(document, reader.runs)
    return _labelled(reader.out, reader.contexts, lists)


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
