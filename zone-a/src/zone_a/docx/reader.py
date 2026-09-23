"""A fail-closed reader for the text of a Word (.docx) body.

The reader turns ``word/document.xml`` into paragraphs of text, and it refuses a document
whose text it cannot produce exactly. Refusing is the point: a reader that keeps going when it
meets something it does not understand is how a label loses a character without anyone
noticing. The EMA's own QRD files show why. Appendix II writes the "\u2265" of "Very common
(\u2265 1/10)" as a Symbol-font glyph (``<w:sym w:font="Symbol" w:char="F0B3"/>``), and Appendix III
writes the degree sign of "25 \u00b0C" the same way; a reader that collects only ``<w:t>`` text
returns "( 1/10)" and "25 C" and reports nothing. See ``docs/design/qrd-registry.md``.

What the reader does:

- ``<w:t>`` text is copied as is. Nothing is normalised, straightened or trimmed.
- ``<w:tab/>`` and ``<w:ptab/>`` are U+0009; ``<w:br/>`` and ``<w:cr/>`` are U+000A, except a
  page or column break, which is layout and emits nothing; ``<w:noBreakHyphen/>`` is U+2011
  and ``<w:softHyphen/>`` is U+00AD.
- ``<w:sym>`` and private-use characters in a Symbol-font run are mapped through
  ``SYMBOL_FONT``, a closed table. Any other font, or a code the table does not hold, is
  refused.
- A field keeps its displayed result and drops its instruction (``<w:instrText>``), so
  ``DOCPROPERTY ... MERGEFORMAT`` never reaches the text.
- Hyperlinks, smart tags, content controls and ``fldSimple`` are read through.
- Tables are read cell by cell; every paragraph carries its table position.
- List numbering is reported as metadata (``numbering``), never rendered into the text: the
  number Word would show is not in the file.

What it refuses (``DocxRefusedError.code``):

- ``tracked-change``: an unaccepted insertion, deletion or move. Such a document has two texts.
- ``hidden-text``: a run with text that is hidden (``w:vanish``), directly or through a style.
- ``unmapped-symbol``: a Symbol-font code the table does not hold, or a symbol in another font.
- ``private-use-character``: a private-use code point outside a Symbol-font run.
- ``unsupported-element``: anything that can carry text and is not read above \u2014 text boxes,
  footnote and endnote references, embedded objects, alternate content, ``altChunk``.
- ``invalid-package``: not a readable .docx, a DOCTYPE, or a part over the size cap.

Headers, footers, footnotes and comments are separate parts and are not read.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


# The Symbol font maps its glyphs onto U+F020..U+F0FF when Word stores them. This table is the
# Unicode mapping of Adobe's Symbol encoding (the Unicode Consortium's SYMBOL.TXT) for the codes
# a product-information text can be expected to use. It is closed on purpose: a code missing
# here is refused, and adding one is a reviewed change with a test.
SYMBOL_FONT: dict[int, str] = {
    0x2D: "\u2212",  # MINUS SIGN
    0x61: "\u03b1",  # GREEK SMALL LETTER ALPHA
    0x62: "\u03b2",  # GREEK SMALL LETTER BETA
    0x64: "\u03b4",  # GREEK SMALL LETTER DELTA
    0x67: "\u03b3",  # GREEK SMALL LETTER GAMMA
    0x6D: "\u03bc",  # GREEK SMALL LETTER MU
    0x7B: "{",
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

MAX_PART_BYTES = 20 * 1024 * 1024

# Elements under a run (or directly under a paragraph) that carry no text of their own.
_SILENT = {
    _w(name)
    for name in (
        "rPr",
        "pPr",
        "lastRenderedPageBreak",
        "bookmarkStart",
        "bookmarkEnd",
        "proofErr",
        "commentRangeStart",
        "commentRangeEnd",
        "commentReference",
        "annotationRef",
        "permStart",
        "permEnd",
        "separator",
        "continuationSeparator",
        "noBreakHyphen",
        "softHyphen",
    )
}
# Containers read through as if their children were the paragraph's own.
_TRANSPARENT = {_w(name) for name in ("hyperlink", "smartTag", "customXml", "fldSimple", "dir")}
_REFUSED = {
    _w(name): name
    for name in (
        "footnoteReference",
        "endnoteReference",
        "object",
        "pict",
        "txbxContent",
        "altChunk",
        "subDoc",
        "ruby",
        "fldData",
    )
}
_TRACKED = {_w(name) for name in ("ins", "del", "moveFrom", "moveTo", "delText", "delInstrText")}


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
class Paragraph:
    """One paragraph of the body, in document order.

    ``table`` is ``(table, row, cell)`` counted from zero in document order for a paragraph
    inside a table cell, else ``None``. A nested table's paragraphs carry the outermost cell.
    """

    text: str
    style: str | None
    numbering: Numbering | None
    table: tuple[int, int, int] | None
    has_drawing: bool = False


@dataclass
class _Styles:
    hidden: dict[str, bool] = field(default_factory=dict)
    based_on: dict[str, str] = field(default_factory=dict)
    default_hidden: bool = False

    def is_hidden(self, style_id: str | None) -> bool | None:
        seen: set[str] = set()
        while style_id is not None and style_id not in seen:
            seen.add(style_id)
            if style_id in self.hidden:
                return self.hidden[style_id]
            style_id = self.based_on.get(style_id)
        return None


def _flag(rpr: ET.Element | None, name: str) -> bool | None:
    """The value of an on/off run property, or None when the element is absent."""
    if rpr is None:
        return None
    element = rpr.find(_w(name))
    if element is None:
        return None
    value = element.get(_w("val"))
    return value is None or value.lower() not in {"0", "false", "off"}


def _read_part(package: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        info = package.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise DocxRefusedError("invalid-package", f"{name} is larger than {MAX_PART_BYTES} bytes")
    data = package.read(name)
    if b"<!DOCTYPE" in data:
        raise DocxRefusedError("invalid-package", f"{name} declares a DOCTYPE")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise DocxRefusedError("invalid-package", f"{name} is not well-formed XML") from error


def _styles(root: ET.Element | None) -> _Styles:
    styles = _Styles()
    if root is None:
        return styles
    default = root.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
    styles.default_hidden = bool(_flag(default, "vanish"))
    for style in root.findall(_w("style")):
        style_id = style.get(_w("styleId"))
        if style_id is None:
            continue
        based = style.find(_w("basedOn"))
        if based is not None and based.get(_w("val")):
            styles.based_on[style_id] = based.get(_w("val"), "")
        hidden = _flag(style.find(_w("rPr")), "vanish")
        if hidden is not None:
            styles.hidden[style_id] = hidden
    return styles


def _symbol(font: str | None, code: int, where: str) -> str:
    if font is None or font.strip().lower() != "symbol":
        raise DocxRefusedError("unmapped-symbol", f"{where}: font {font!r} code {code:#06x}")
    low = code - 0xF000 if code >= 0xF000 else code
    if low not in SYMBOL_FONT:
        raise DocxRefusedError("unmapped-symbol", f"{where}: Symbol code {code:#06x}")
    return SYMBOL_FONT[low]


def _run_font(rpr: ET.Element | None) -> str | None:
    if rpr is None:
        return None
    fonts = rpr.find(_w("rFonts"))
    if fonts is None:
        return None
    return fonts.get(_w("ascii")) or fonts.get(_w("hAnsi")) or fonts.get(_w("cs"))


class _ParagraphReader:
    def __init__(self, styles: _Styles, paragraph_style: str | None) -> None:
        self.styles = styles
        self.paragraph_style = paragraph_style
        self.parts: list[str] = []
        # A stack of field states: True while inside the instruction part of a field.
        self.fields: list[bool] = []
        self.has_drawing = False

    def _hidden(self, rpr: ET.Element | None) -> bool:
        direct = _flag(rpr, "vanish")
        if direct is not None:
            return direct
        run_style = None
        if rpr is not None:
            element = rpr.find(_w("rStyle"))
            run_style = element.get(_w("val")) if element is not None else None
        for style_id in (run_style, self.paragraph_style):
            inherited = self.styles.is_hidden(style_id)
            if inherited is not None:
                return inherited
        return self.styles.default_hidden

    def container(self, element: ET.Element) -> None:
        for child in element:
            tag = child.tag
            if tag in _TRACKED:
                raise DocxRefusedError("tracked-change", tag.rsplit("}", 1)[1])
            if tag in _REFUSED:
                raise DocxRefusedError("unsupported-element", _REFUSED[tag])
            if tag == f"{{{MC}}}AlternateContent":
                raise DocxRefusedError("unsupported-element", "AlternateContent")
            if tag == _w("r"):
                self.run(child)
            elif tag in _TRANSPARENT:
                self.container(child)
            elif tag == _w("sdt"):
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.container(content)
            elif tag in _SILENT:
                continue
            else:
                raise DocxRefusedError("unsupported-element", tag.rsplit("}", 1)[1])

    def run(self, run: ET.Element) -> None:
        rpr = run.find(_w("rPr"))
        hidden = self._hidden(rpr)
        font = _run_font(rpr)
        emitted: list[str] = []
        for child in run:
            tag = child.tag
            if tag in _TRACKED:
                raise DocxRefusedError("tracked-change", tag.rsplit("}", 1)[1])
            if tag in _REFUSED:
                raise DocxRefusedError("unsupported-element", _REFUSED[tag])
            if tag == f"{{{MC}}}AlternateContent":
                raise DocxRefusedError("unsupported-element", "AlternateContent")
            if tag == _w("fldChar"):
                kind = child.get(_w("fldCharType"))
                if kind == "begin":
                    self.fields.append(True)
                elif kind == "separate" and self.fields:
                    self.fields[-1] = False
                elif kind == "end" and self.fields:
                    self.fields.pop()
                continue
            if tag == _w("instrText"):
                continue
            in_instruction = bool(self.fields) and self.fields[-1]
            if tag == _w("t"):
                text = child.text or ""
                if in_instruction:
                    continue
                emitted.append(self._text(text, font))
            elif tag in (_w("tab"), _w("ptab")):
                if not in_instruction:
                    emitted.append("\t")
            elif tag == _w("br"):
                if not in_instruction and child.get(_w("type")) not in ("page", "column"):
                    emitted.append("\n")
            elif tag == _w("cr"):
                if not in_instruction:
                    emitted.append("\n")
            elif tag == _w("noBreakHyphen"):
                if not in_instruction:
                    emitted.append("\u2011")
            elif tag == _w("softHyphen"):
                if not in_instruction:
                    emitted.append("\u00ad")
            elif tag == _w("sym"):
                if not in_instruction:
                    code = int(child.get(_w("char"), "0"), 16)
                    emitted.append(_symbol(child.get(_w("font")), code, "w:sym"))
            elif tag == _w("drawing"):
                if any(True for _ in child.iter(_w("t"))):
                    raise DocxRefusedError("unsupported-element", "drawing with text")
                self.has_drawing = True
            elif tag in _SILENT:
                continue
            else:
                raise DocxRefusedError("unsupported-element", tag.rsplit("}", 1)[1])
        text = "".join(emitted)
        if hidden and text.strip():
            raise DocxRefusedError("hidden-text", "a hidden run carries text")
        if not hidden:
            self.parts.append(text)

    def _text(self, text: str, font: str | None) -> str:
        out: list[str] = []
        for character in text:
            code = ord(character)
            if 0xE000 <= code <= 0xF8FF:
                if font is None or font.strip().lower() != "symbol":
                    raise DocxRefusedError("private-use-character", f"U+{code:04X} outside Symbol")
                out.append(_symbol(font, code, "w:t"))
            else:
                out.append(character)
        return "".join(out)


def _paragraph(
    element: ET.Element, styles: _Styles, table: tuple[int, int, int] | None
) -> Paragraph:
    ppr = element.find(_w("pPr"))
    style = None
    numbering = None
    if ppr is not None:
        style_element = ppr.find(_w("pStyle"))
        style = style_element.get(_w("val")) if style_element is not None else None
        numpr = ppr.find(_w("numPr"))
        if numpr is not None:
            num_id = numpr.find(_w("numId"))
            level = numpr.find(_w("ilvl"))
            numbering = Numbering(
                num_id=int(num_id.get(_w("val"), "0")) if num_id is not None else 0,
                level=int(level.get(_w("val"), "0")) if level is not None else 0,
            )
    reader = _ParagraphReader(styles, style)
    reader.container(element)
    return Paragraph(
        text="".join(reader.parts),
        style=style,
        numbering=numbering,
        table=table,
        has_drawing=reader.has_drawing,
    )


def _block(
    element: ET.Element,
    styles: _Styles,
    out: list[Paragraph],
    table: tuple[int, int, int] | None,
    counter: list[int],
) -> None:
    for child in element:
        tag = child.tag
        if tag == _w("p"):
            out.append(_paragraph(child, styles, table))
        elif tag == _w("tbl"):
            index = counter[0]
            counter[0] += 1
            for row_index, row in enumerate(child.findall(_w("tr"))):
                for cell_index, cell in enumerate(row.findall(_w("tc"))):
                    _block(cell, styles, out, table or (index, row_index, cell_index), counter)
        elif tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                _block(content, styles, out, table, counter)
        elif tag in _TRACKED:
            raise DocxRefusedError("tracked-change", tag.rsplit("}", 1)[1])
        elif tag in _REFUSED:
            raise DocxRefusedError("unsupported-element", _REFUSED[tag])
        elif tag == f"{{{MC}}}AlternateContent":
            raise DocxRefusedError("unsupported-element", "AlternateContent")
        # sectPr, bookmarks, tblPr, trPr, tcPr and the like carry no text.


def read_docx(data: bytes) -> list[Paragraph]:
    """Every body paragraph of a .docx, in document order, or ``DocxRefusedError``."""
    try:
        package = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise DocxRefusedError("invalid-package", "not a zip archive") from error
    with package:
        document = _read_part(package, "word/document.xml")
        if document is None:
            raise DocxRefusedError("invalid-package", "no word/document.xml")
        styles = _styles(_read_part(package, "word/styles.xml"))
    body = document.find(_w("body"))
    if body is None:
        raise DocxRefusedError("invalid-package", "no w:body")
    paragraphs: list[Paragraph] = []
    _block(body, styles, paragraphs, None, [0])
    return paragraphs
