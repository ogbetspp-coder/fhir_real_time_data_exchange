"""A fail-closed reader for the text of a Word (.docx) body.

The reader turns the main document part into paragraphs of text, and it refuses a document whose
text it cannot produce exactly. Refusing is the point: a reader that keeps going when it meets
something it does not understand is how a label loses a character without anyone noticing. The
EMA's own QRD files show why. Appendix II writes the greater-than-or-equal sign of "Very common
(>= 1/10)" as a Symbol-font glyph (``<w:sym w:font="Symbol" w:char="F0B3"/>``), and Appendix III
writes the degree sign of "25 degrees C" the same way; a reader that collects only ``<w:t>`` text
returns "( 1/10)" and "25 C" and reports nothing. See ``docs/design/qrd-registry.md``.

What a paragraph carries:

- ``text``: the characters as stored. ``<w:t>`` text is copied as is; nothing is normalised,
  straightened or trimmed. ``<w:tab/>`` and ``<w:ptab/>`` are U+0009; ``<w:br/>`` and
  ``<w:cr/>`` are U+000A, except a page or column break, which is layout and emits nothing;
  ``<w:noBreakHyphen/>`` is U+2011, ``<w:softHyphen/>`` U+00AD, and a picture is U+FFFC OBJECT
  REPLACEMENT CHARACTER at the place it stands.
- ``marks``: every range of ``text`` whose appearance changes what a reader sees or means:
  superscript, subscript, raised or lowered text, capitals and small capitals, single and double
  strike-through, highlight and shading. ``text`` alone flattens "10" with a superscript "9" to
  "109"; a caller that uses ``text`` must look at ``marks``.
- ``mark_hidden``: the paragraph mark is hidden, so Word shows this paragraph run on into the
  next one.
- ``numbering``: the list the paragraph belongs to, directly or through its style. The number
  Word shows is computed, not stored, and is never rendered into ``text``.
- ``table``: ``(table, row, cell)`` counted from zero in document order, else ``None``. A nested
  table's paragraphs carry the outermost cell; cells are counted as ``<w:tc>`` elements, not
  grid columns.

Symbol fonts. A run whose effective Latin font (``ascii`` or ``hAnsi``, set directly, by a
style, by the document defaults or through the theme) is Symbol has every character mapped
through ``SYMBOL_FONT``; a character the table does not hold is refused. ``<w:sym>`` in the
Symbol font is mapped the same way. A dingbat font (Wingdings, Webdings, Zapf Dingbats, Marlett,
MT Extra), or a Symbol font set only for East Asian or complex-script text, is refused.

Fields keep their displayed result and drop their instruction, however deeply nested, so
``DOCPROPERTY ... MERGEFORMAT`` never reaches the text.

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
- ``unpreserved-whitespace``: ``<w:t>`` text with leading or trailing XML whitespace, or a tab or
  line break, without ``xml:space="preserve"``; a consumer may drop it.
- ``unbalanced-field``: a paragraph that ends inside a field instruction.
- ``unsupported-element``: anything that can carry text and is not read above, and any element
  the reader does not know: text boxes, footnote and endnote references, embedded objects,
  charts and other non-picture drawings, alternate content, math, ``altChunk``.
- ``invalid-package``: not a readable .docx, no main document relationship, a duplicate part
  name, a part that is not UTF-8, a DTD, or a part over the size cap.

Headers, footers, footnotes, comments and the glossary are separate parts and are not read.
"""

from __future__ import annotations

import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

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
_INLINE_TRANSPARENT = {
    _w(name) for name in ("hyperlink", "smartTag", "customXml", "fldSimple", "dir", "bdo")
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
    """The list a paragraph belongs to. ``num_id`` 0 means "not in a list"."""

    num_id: int
    level: int


@dataclass(frozen=True)
class Mark:
    """``text[start:end]`` is shown as ``kind``.

    One of superscript, subscript, position, caps, smallCaps, strike, dstrike, highlight,
    shading.
    """

    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class Paragraph:
    text: str
    style: str | None
    numbering: Numbering | None
    table: tuple[int, int, int] | None
    marks: tuple[Mark, ...] = ()
    mark_hidden: bool = False

    @property
    def has_drawing(self) -> bool:
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
    return text.encode("utf-8")


class _Package:
    def __init__(self, data: bytes) -> None:
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as error:
            raise DocxRefusedError("invalid-package", "not a zip archive") from error
        names = self.zip.namelist()
        if len(names) != len(set(names)):
            raise DocxRefusedError("invalid-package", "a part name occurs twice")
        self.names = set(names)

    def part(self, name: str) -> ET.Element | None:
        if name not in self.names:
            return None
        info = self.zip.getinfo(name)
        if info.file_size > MAX_PART_BYTES:
            raise DocxRefusedError("invalid-package", f"{name} is over {MAX_PART_BYTES} bytes")
        try:
            return ET.fromstring(_decode(name, self.zip.read(name)))
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


@dataclass
class _Styles:
    styles: dict[str, _Style] = field(default_factory=dict)
    default_rpr: ET.Element | None = None
    default_paragraph: str | None = None
    theme_fonts: dict[str, str] = field(default_factory=dict)
    has_theme: bool = False

    def chain(self, style_id: str | None) -> list[_Style]:
        out: list[_Style] = []
        seen: set[str] = set()
        while style_id is not None and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            style = self.styles[style_id]
            out.append(style)
            style_id = style.based_on
        return out


def _styles(root: ET.Element | None, theme: ET.Element | None) -> _Styles:
    styles = _Styles()
    if theme is not None:
        styles.has_theme = True
        for prefix in ("major", "minor"):
            font = next(iter(theme.iter(f"{{{A}}}{prefix}Font")), None)
            if font is None:
                continue
            for script, child in (("Latin", "latin"), ("EastAsia", "ea"), ("Bidi", "cs")):
                element = font.find(f"{{{A}}}{child}")
                if element is not None:
                    styles.theme_fonts[prefix + script] = element.get("typeface", "")
    if root is None:
        return styles
    styles.default_rpr = root.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
    for style in root.findall(_w("style")):
        style_id = style.get(_w("styleId"))
        if style_id is None:
            continue
        based = style.find(_w("basedOn"))
        kind = style.get(_w("type"), "paragraph")
        styles.styles[style_id] = _Style(
            kind=kind,
            based_on=based.get(_w("val")) if based is not None else None,
            rpr=style.find(_w("rPr")),
            ppr=style.find(_w("pPr")),
        )
        if kind == "paragraph" and style.get(_w("default")) in ("1", "true", "on"):
            styles.default_paragraph = style_id
    return styles


def _on(element: ET.Element | None) -> bool | None:
    if element is None:
        return None
    value = element.get(_w("val"))
    return value is None or value.lower() not in {"0", "false", "off"}


class _Properties:
    """The run properties in force for one run, from the run outwards."""

    def __init__(
        self,
        styles: _Styles,
        direct: ET.Element | None,
        paragraph_style: str | None,
        table_style: str | None,
    ) -> None:
        self.styles = styles
        self.direct = direct
        run_style = None
        if direct is not None:
            element = direct.find(_w("rStyle"))
            run_style = element.get(_w("val")) if element is not None else None
        levels: list[ET.Element | None] = []
        levels += [style.rpr for style in styles.chain(run_style)]
        levels += [style.rpr for style in styles.chain(paragraph_style or styles.default_paragraph)]
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

    def font(self, slot: str) -> str | None:
        """The effective font for ``ascii``, ``hAnsi``, ``eastAsia`` or ``cs``."""
        for level in [self.direct, *self.inherited]:
            if level is None:
                continue
            fonts = level.find(_w("rFonts"))
            if fonts is None:
                continue
            theme = fonts.get(_w(slot + "Theme"))
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
                script = theme[len(prefix) :]
                key = prefix + ("Latin" if script in ("HAnsi", "Ascii") else script)
                return self.styles.theme_fonts.get(key, "")
        raise DocxRefusedError("symbol-font", f"unknown theme font {theme}")


def _font_class(name: str | None) -> str:
    if name is None:
        return "text"
    key = name.lower().replace(" ", "")
    if key in ("symbol", "symbolmt"):
        return "symbol"
    if any(part in key for part in _DINGBAT_FONTS):
        return "dingbat"
    return "text"


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
        self, styles: _Styles, paragraph_style: str | None, table_style: str | None
    ) -> None:
        self.styles = styles
        self.paragraph_style = paragraph_style
        self.table_style = table_style
        self.parts: list[str] = []
        self.length = 0
        self.marks: list[Mark] = []
        # One entry per open field: True while in its instruction, False once in its result.
        self.fields: list[bool] = []

    def in_instruction(self) -> bool:
        return True in self.fields

    def container(self, element: ET.Element) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("r"):
                self.run(child)
            elif tag in _INLINE_TRANSPARENT:
                self.container(child)
            elif tag == _w("sdt"):
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.container(content)
            elif tag in (_w("pPr"), _w("sdtPr")) or tag in _MARKERS:
                continue
            else:
                raise DocxRefusedError("unsupported-element", _local(tag))

    def run(self, run: ET.Element) -> None:
        properties = _Properties(
            self.styles, run.find(_w("rPr")), self.paragraph_style, self.table_style
        )
        latin = [_font_class(properties.font(slot)) for slot in ("ascii", "hAnsi")]
        other = [_font_class(properties.font(slot)) for slot in ("eastAsia", "cs")]
        if "dingbat" in latin + other:
            raise DocxRefusedError("symbol-font", "a run in a dingbat font")
        symbol = "symbol" in latin
        if not symbol and "symbol" in other:
            raise DocxRefusedError("symbol-font", "Symbol set for East Asian or complex script")
        emitted: list[str] = []
        for child in run:
            tag = child.tag
            if tag == _w("fldChar"):
                kind = child.get(_w("fldCharType"))
                if kind == "begin":
                    self.fields.append(True)
                elif kind == "separate" and self.fields:
                    self.fields[-1] = False
                elif kind == "end" and self.fields:
                    self.fields.pop()
                continue
            if tag == _w("instrText") or tag in _RUN_SILENT:
                continue
            if tag == _w("t"):
                text = child.text or ""
                _check_whitespace(child, text)
                produced = self._text(text, symbol)
            else:
                produced = self._special(child)
            if not self.in_instruction():
                emitted.append(produced)
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
            try:
                code = int(child.get(_w("char"), ""), 16)
            except ValueError as error:
                raise DocxRefusedError("unmapped-symbol", "w:sym without a hex code") from error
            return _symbol(code, "w:sym")
        if tag == _w("drawing"):
            return _drawing(child)
        raise DocxRefusedError("unsupported-element", _local(tag))

    def _text(self, text: str, symbol: bool) -> str:
        out: list[str] = []
        for character in text:
            code = ord(character)
            if symbol:
                if code > 0xFF and not 0xF000 <= code <= 0xF0FF:
                    raise DocxRefusedError("unmapped-symbol", f"U+{code:04X} in a Symbol run")
                out.append(_symbol(code, "w:t"))
            elif _private_use(code):
                raise DocxRefusedError("private-use-character", f"U+{code:04X}")
            else:
                out.append(character)
        return "".join(out)

    def _mark(self, properties: _Properties, start: int, end: int) -> None:
        kinds: list[str] = []
        vertical = properties.value("vertAlign")
        if vertical in ("superscript", "subscript"):
            kinds.append(vertical)
        if properties.value("position") not in (None, "0"):
            kinds.append("position")
        kinds += [name for name in _TOGGLE_MARKS if properties.toggle(name)]
        if properties.value("highlight") not in (None, "none"):
            kinds.append("highlight")
        fill = properties.value("shd", "fill")
        if fill is not None and fill.lower() not in ("auto", "ffffff"):
            kinds.append("shading")
        for kind in kinds:
            previous = next((m for m in reversed(self.marks) if m.kind == kind), None)
            if previous is not None and previous.end == start:
                self.marks[self.marks.index(previous)] = Mark(previous.start, end, kind)
            else:
                self.marks.append(Mark(start, end, kind))


def _check_whitespace(element: ET.Element, text: str) -> None:
    if element.get(XML_SPACE) == "preserve":
        return
    if text != text.strip(" \t\r\n") or any(c in text for c in "\t\r\n"):
        raise DocxRefusedError("unpreserved-whitespace", "w:t without xml:space=preserve")


def _numbering(ppr: ET.Element | None, style_chain: list[_Style]) -> Numbering | None:
    for source in [ppr, *(style.ppr for style in style_chain)]:
        if source is None:
            continue
        numpr = source.find(_w("numPr"))
        if numpr is None:
            continue
        num_id = numpr.find(_w("numId"))
        level = numpr.find(_w("ilvl"))
        return Numbering(
            num_id=int(num_id.get(_w("val"), "0")) if num_id is not None else 0,
            level=int(level.get(_w("val"), "0")) if level is not None else 0,
        )
    return None


def _paragraph(
    element: ET.Element,
    styles: _Styles,
    table: tuple[int, int, int] | None,
    table_style: str | None,
) -> Paragraph:
    ppr = element.find(_w("pPr"))
    style = None
    mark_hidden = False
    if ppr is not None:
        style_element = ppr.find(_w("pStyle"))
        style = style_element.get(_w("val")) if style_element is not None else None
        mark_hidden = bool(_on(ppr.find(f"{_w('rPr')}/{_w('vanish')}")))
    reader = _ParagraphReader(styles, style, table_style)
    reader.container(element)
    if reader.in_instruction():
        raise DocxRefusedError("unbalanced-field", "a paragraph ends inside a field instruction")
    return Paragraph(
        text="".join(reader.parts),
        style=style,
        numbering=_numbering(ppr, styles.chain(style or styles.default_paragraph)),
        table=table,
        marks=tuple(sorted(reader.marks, key=lambda m: (m.start, m.kind))),
        mark_hidden=mark_hidden,
    )


# --- blocks and tables ---------------------------------------------------------------------


class _Body:
    def __init__(self, styles: _Styles) -> None:
        self.styles = styles
        self.out: list[Paragraph] = []
        self.tables = 0

    def blocks(
        self, element: ET.Element, table: tuple[int, int, int] | None, table_style: str | None
    ) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("p"):
                self.out.append(_paragraph(child, self.styles, table, table_style))
            elif tag == _w("tbl"):
                self.table(child, table)
            elif tag == _w("sdt"):
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
        table_style = style_element.get(_w("val")) if style_element is not None else None
        rows: list[ET.Element] = []
        _collect(element, _w("tr"), rows, {_w("tblPr"), _w("tblGrid")})
        for row_index, row in enumerate(rows):
            cells: list[ET.Element] = []
            _collect(row, _w("tc"), cells, {_w("trPr"), _w("tblPrEx")})
            for cell_index, cell in enumerate(cells):
                self.blocks(cell, outer or (index, row_index, cell_index), table_style)


def _collect(element: ET.Element, wanted: str, out: list[ET.Element], silent: set[str]) -> None:
    """The ``wanted`` children of a table or row, through content controls and custom XML."""
    for child in element:
        tag = child.tag
        if tag == wanted:
            out.append(child)
        elif tag == _w("sdt"):
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
        style_parts = package.related(mains[0], "styles")
        theme_parts = package.related(mains[0], "theme")
        if len(style_parts) > 1 or len(theme_parts) > 1:
            raise DocxRefusedError("invalid-package", "more than one styles or theme part")
        styles = _styles(
            package.part(style_parts[0]) if style_parts else None,
            package.part(theme_parts[0]) if theme_parts else None,
        )
    for element in document.iter():
        if element.tag in _TRACKED:
            raise DocxRefusedError("tracked-change", _local(element.tag))
        if element.tag == f"{{{MC}}}AlternateContent":
            raise DocxRefusedError("unsupported-element", "AlternateContent")
    body = document.find(_w("body"))
    if body is None:
        raise DocxRefusedError("invalid-package", "no w:body")
    reader = _Body(styles)
    reader.blocks(body, None, None)
    return reader.out
