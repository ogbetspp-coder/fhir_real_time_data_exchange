"""A fail-closed reader for the sections of an EMA ePI document Bundle.

The EMA publishes electronic product information as FHIR document Bundles: one Composition whose
nested sections each carry a code, a title and an XHTML ``div``. This reader turns such a
Bundle into sections of paragraphs, with the same ``Paragraph`` and ``Mark`` model as the Word
reader (``zone_a.docx.reader``), so a checker does not care which reader produced the text.

It is written to the same rule as the Word reader: the text a browser shows, exactly, or a
refusal with a reason. It is not the fidelity scanner (``zone_a.fidelity.xhtml``), which is the
contract for narrative this repository publishes and stays as strict as it is; the EMA's own
divs carry inline CSS on nearly every element, which that scanner rightly refuses. Here each
section is read on its own, so a section the reader cannot vouch for is refused (``Section.
refusal``) without losing the rest of the document.

What a section's text is:

- Block elements (``div``, ``p``, ``li``, ``td``, ``th``, ``table``, ``tr``, ``ul``, ``ol``,
  ``thead``, ``tbody``, ``hr``) end one paragraph and start the next. A paragraph with no text
  is dropped. A list item carries ``numbering``: ``num_id`` 1 in a ``ul`` (a bullet), 2 in an
  ``ol`` (a number the browser computes); like the Word reader, the bullet or number is never
  in the text.
- Whitespace is collapsed as a browser does under ``white-space: normal``: a run of space, tab,
  line feed, carriage return or form feed is one space, and none is kept at the start or end of
  a paragraph. No-break space (U+00A0) is text and is kept. ``<br/>`` is U+000A.
- A picture (``img``) is U+FFFC OBJECT REPLACEMENT CHARACTER where it stands, as in the Word
  reader: the black triangle of the additional-monitoring statement is a picture.
- Character references are decoded by the XML parser; an HTML named entity such as
  ``&nbsp;`` is not XML, and the section is refused.
- A ``<`` that cannot open a tag (not followed by a letter, ``/``, ``!`` or ``?``) is text, as
  the HTML tokenizer reads it ("tag open state"); the EMA writes "GFR < 60" that way. Each
  section where this happened says so in ``Section.notes``, because it is not valid XHTML.

What is marked (``Paragraph.marks``, the Word reader's kinds): ``sup`` and ``vertical-align:
super`` as superscript, ``sub`` and ``vertical-align: sub`` as subscript, ``s``, ``strike`` and
``text-decoration: line-through`` as strike, a background other than white as
``shading-<colour>``, a text colour other than black as ``color-<colour>`` (white or a
nearly white colour as faint instead, a nearly black one as nothing; ``#abc`` and ``rgb()`` are
written as ``#aabbcc``, and any other colour notation refuses the section), a font size
under two points as faint, and ``u``, ``a`` with an ``href`` and ``text-decoration: underline``
as underline (an underline turns a sign into another: "<" underlined is drawn "≤", and "1"
with an underlined "a" reads "1ª"). Bold, italic, font family and every layout property are
not reported.

What refuses a section (``SectionRefusal.code``):

- ``malformed-xhtml``: the div is not well-formed XML, declares a DTD, or is not in the XHTML
  namespace.
- ``unsupported-element``: an element not listed above, and ``ins`` or ``del``.
- ``unsupported-attribute``: an attribute not on the closed list for its element.
- ``unsupported-style``: a CSS property not on the closed list, or a value of a listed property
  the reader cannot place (``display`` of any kind, ``visibility`` other than visible, an
  unparsable font size...).
- ``embedded-comment``: Word comment markup (``msocom...`` classes), whose text would otherwise
  read as label text.
- ``reserved-character``: U+FFFC in the text, which the reader uses for a picture.
- ``format-character``: an invisible formatting character (Unicode category Cf: soft hyphen,
  zero-width characters, bidirectional controls), which a browser hides or which reorders
  what it shows.

Also refused as ``malformed-xhtml``: a CDATA section (an XML parser reads it as text, an HTML
parser as a comment). As ``unsupported-element``: text between the parts of a table, which a
browser moves out of the table. As ``unsupported-style``: a margin or indent more than an inch
to the left, which moves text off the page, or in a unit the reader does not know (``%``,
``vw``, ``calc()``...).

What refuses the document (``EpiRefusedError``): not a document Bundle, not exactly one entry
with sections, or a section without a title.
"""

from __future__ import annotations

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from zone_a.docx.reader import Mark, Numbering, Paragraph

READER_VERSION = "epi-reader/1.0.0"
XHTML = "http://www.w3.org/1999/xhtml"
OBJECT = "\ufffc"
_COLLAPSIBLE = " \t\n\r\f"

_BLOCKS = {"div", "p", "li", "td", "th", "table", "tr", "ul", "ol", "thead", "tbody", "hr"}
_INLINE = {"span", "strong", "b", "em", "i", "u", "s", "strike", "sup", "sub", "a"}
_COMMON = {"style", "class", "id", "lang", "title", "align"}
_ATTRIBUTES: dict[str, set[str]] = {
    "div": _COMMON,
    "p": _COMMON,
    "li": _COMMON,
    "ul": _COMMON | {"type"},
    "ol": _COMMON | {"type", "start"},
    "table": _COMMON | {"border", "cellspacing", "cellpadding", "width"},
    "thead": _COMMON,
    "tbody": _COMMON,
    "tr": _COMMON | {"valign"},
    "td": _COMMON | {"valign", "rowspan", "colspan", "nowrap", "width"},
    "th": _COMMON | {"valign", "rowspan", "colspan", "nowrap", "width"},
    "hr": _COMMON | {"size", "width", "noshade"},
    "a": _COMMON | {"href", "name"},
    "img": _COMMON | {"src", "alt", "width", "height", "annotationsrc"},
    "br": _COMMON,
    **{name: set(_COMMON) for name in ("span", "strong", "b", "em", "i", "u", "s", "strike")},
    "sup": _COMMON,
    "sub": _COMMON,
}

# CSS properties that move or frame text but cannot hide it, change a character or change what
# it means. Anything not here, and not handled in ``_style``, refuses the section.
_LAYOUT = re.compile(
    r"(margin|padding|border)(-(top|bottom|left|right))?(-(width|style|color))?"
    r"|border-(collapse|image|spacing)|line-height|text-align|text-indent|width|height"
    r"|min-width|min-height|break-(before|after|inside)|page-break-(before|after|inside)"
    r"|font-family|font-weight|font-style|layout-grid-mode|mso-[a-z-]+"
)
_BLACK = {"black", "windowtext", "#000000", "auto", "initial"}
_WHITE = {"white", "#ffffff", "transparent"}
_KEYWORDS = {"none", "auto", "inherit", "initial", "currentcolor", "transparent"}
# A margin or indent further left than one inch moves text off the page, not to its edge.
_OFF_SCREEN_POINTS = 72.0
_POINTS = {"pt": 1.0, "px": 0.75, "pc": 12.0, "in": 72.0, "cm": 72 / 2.54, "mm": 72 / 25.4}


class EpiRefusedError(Exception):
    """The Bundle is not an ePI document this reader can read."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SectionRefusal:
    code: str
    detail: str


@dataclass(frozen=True)
class Section:
    code: str | None
    title: str
    paragraphs: tuple[Paragraph, ...]
    refusal: SectionRefusal | None
    sections: tuple[Section, ...] = ()
    # Defects in the div the reader read through by a stated rule, for the report.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    title: str
    date: str | None
    document_type: str | None
    sections: tuple[Section, ...]
    # Deviations from FHIR the reader accepted by name, for the report.
    quirks: tuple[str, ...] = ()


class _RefusedError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.refusal = SectionRefusal(code, detail)


# --- styles ---------------------------------------------------------------------------------


def _declarations(style: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in style.split(";"):
        if not part.strip():
            continue
        name, colon, value = part.partition(":")
        if not colon:
            raise _RefusedError("unsupported-style", f"not a declaration: {part.strip()!r}")
        out.append((name.strip().lower(), value.strip().lower().removesuffix("!important").strip()))
    return out


# Colour names the reader accepts: CSS's basic colours, the ones Word writes, and keywords.
_NAMED = {
    "black",
    "silver",
    "gray",
    "grey",
    "white",
    "maroon",
    "red",
    "purple",
    "fuchsia",
    "green",
    "lime",
    "olive",
    "yellow",
    "navy",
    "blue",
    "teal",
    "aqua",
    "lightgrey",
    "lightgray",
    "darkgray",
    "darkgrey",
    "windowtext",
    "transparent",
    "none",
    "auto",
    "inherit",
    "initial",
    "currentcolor",
}


def _colour(value: str) -> str:
    """A colour in one spelling (``#abc`` and ``rgb(170, 187, 204)`` as ``#aabbcc``), or a
    refusal: any other notation (alpha, ``hsl()``, percentages) could hide text unseen."""
    short = re.fullmatch(r"#([0-9a-f])([0-9a-f])([0-9a-f])", value)
    if short:
        return "#" + "".join(2 * digit for digit in short.groups())
    if re.fullmatch(r"#[0-9a-f]{6}", value) or value in _NAMED:
        return value
    rgb = re.fullmatch(r"rgb\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*\)", value)
    if rgb and all(int(part) <= 255 for part in rgb.groups()):
        return "#" + "".join(f"{int(part):02x}" for part in rgb.groups())
    raise _RefusedError("unsupported-style", f"colour {value!r}")


def _channels(colour: str) -> tuple[int, int, int] | None:
    if re.fullmatch(r"#[0-9a-f]{6}", colour):
        return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
    return None


def _light(colour: str) -> bool:
    """Nearly white: hard to see on the page (Word's light theme colours included)."""
    channels = _channels(colour)
    return channels is not None and min(channels) >= 0xE0


def _dark(colour: str) -> bool:
    """Nearly black: reads as black text."""
    channels = _channels(colour)
    return channels is not None and max(channels) <= 0x20


def _on_page(value: str) -> bool:
    """Every part of a margin or indent is ``0``, ``auto`` or a length in a unit the reader
    knows, and none moves text more than an inch to the left."""
    for part in value.split():
        if part in ("0", "auto"):
            continue
        match = re.fullmatch(r"(-?)([0-9]+(?:\.[0-9]+)?)(pt|px|pc|in|cm|mm|em)", part)
        if match is None:
            return False
        unit = match.group(3)
        size = float(match.group(2)) * (12.0 if unit == "em" else _POINTS[unit])
        if match.group(1) and size > _OFF_SCREEN_POINTS:
            return False
    return True


def _points(value: str) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(pt|px|pc|in|cm|mm)", value)
    return float(match.group(1)) * _POINTS[match.group(2)] if match else None


def _style(style: str) -> set[str]:
    """The mark kinds a style attribute asks for, or a refusal."""
    kinds: set[str] = set()
    for name, value in _declarations(style):
        if _LAYOUT.fullmatch(name):
            if name.startswith(("margin", "text-indent")) and not _on_page(value):
                raise _RefusedError("unsupported-style", f"{name}: {value}")
            continue
        if name == "visibility":
            if value != "visible":
                raise _RefusedError("unsupported-style", f"visibility: {value}")
        elif name == "color":
            colour = _colour(value)
            if colour in _KEYWORDS - {"transparent"}:
                # Not a colour of its own: a browser keeps the colour the text already has.
                continue
            if colour in _WHITE or _light(colour):
                kinds.add("faint")
            elif colour not in _BLACK and not _dark(colour):
                kinds.add(f"color-{colour}")
        elif name in ("background", "background-color"):
            colour = _colour(value)
            if colour not in _WHITE and colour not in _KEYWORDS and not _light(colour):
                kinds.add(f"shading-{colour}")
        elif name == "font-size":
            points = _points(value)
            if points is None:
                raise _RefusedError("unsupported-style", f"font-size: {value}")
            if points < 2:
                kinds.add("faint")
        elif name == "vertical-align":
            if value in ("super", "sub"):
                kinds.add("superscript" if value == "super" else "subscript")
            elif value not in ("top", "middle", "bottom", "baseline"):
                raise _RefusedError("unsupported-style", f"vertical-align: {value}")
        elif name == "text-decoration":
            words = set(value.split())
            if "line-through" in words:
                kinds.add("strike")
            if "underline" in words:
                kinds.add("underline")
            if words - {"underline", "none", "line-through", "solid"}:
                raise _RefusedError("unsupported-style", f"text-decoration: {value}")
        else:
            raise _RefusedError("unsupported-style", f"{name}: {value}")
    return kinds


# --- paragraphs -----------------------------------------------------------------------------


@dataclass
class _Builder:
    paragraphs: list[Paragraph] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    kinds: list[frozenset[str]] = field(default_factory=list)
    pending: frozenset[str] | None = None
    table: tuple[int, int, int] | None = None
    numbering: Numbering | None = None
    # 1 inside ``ul`` (a bullet), 2 inside ``ol`` (a number the browser computes).
    list_kind: int = 1
    tables: int = 0

    def text(self, text: str, marks: frozenset[str]) -> None:
        for character in text:
            if character in _COLLAPSIBLE:
                if self.characters and self.characters[-1] != "\n" and self.pending is None:
                    self.pending = marks
                continue
            if character == OBJECT:
                raise _RefusedError("reserved-character", "U+FFFC stands for a picture")
            if unicodedata.category(character) == "Cf":
                # Soft hyphens, zero-width characters and bidirectional controls: a browser
                # hides them or reorders the text around them.
                raise _RefusedError("format-character", f"U+{ord(character):04X}")
            self._emit(character, marks)

    def _emit(self, character: str, marks: frozenset[str]) -> None:
        if self.pending is not None:
            # A collapsed space carries the marks of the whitespace it replaces only where both
            # sides agree; a space is otherwise unmarked.
            self.characters.append(" ")
            self.kinds.append(self.pending & marks)
            self.pending = None
        self.characters.append(character)
        self.kinds.append(marks)

    def line_break(self, marks: frozenset[str]) -> None:
        self.pending = None
        self.characters.append("\n")
        self.kinds.append(marks)

    def picture(self, marks: frozenset[str]) -> None:
        self._emit(OBJECT, marks)

    def flush(self) -> None:
        self.pending = None
        while self.characters and self.characters[-1] == "\n":
            self.characters.pop()
            self.kinds.pop()
        if self.characters:
            text = "".join(self.characters)
            self.paragraphs.append(
                Paragraph(
                    text=text,
                    style=None,
                    numbering=self.numbering,
                    table=self.table,
                    marks=_marks(self.kinds),
                )
            )
        self.characters = []
        self.kinds = []


def _marks(kinds: list[frozenset[str]]) -> tuple[Mark, ...]:
    out: list[Mark] = []
    for kind in sorted(set().union(*kinds)) if kinds else []:
        start: int | None = None
        for index, present in enumerate([*(kind in k for k in kinds), False]):
            if present and start is None:
                start = index
            elif not present and start is not None:
                out.append(Mark(start, index, kind))
                start = None
    return tuple(sorted(out, key=lambda m: (m.start, m.end, m.kind)))


def _local(element: ET.Element) -> str:
    namespace, _, name = element.tag[1:].partition("}")
    if namespace != XHTML:
        raise _RefusedError("malformed-xhtml", f"element {element.tag!r} outside XHTML")
    return name


def _check_attributes(element: ET.Element, name: str) -> set[str]:
    allowed = _ATTRIBUTES.get(name, set())
    for attribute, value in element.attrib.items():
        if attribute not in allowed:
            raise _RefusedError("unsupported-attribute", f"{name}@{attribute}")
        if attribute == "class" and any(
            word.lower().startswith("msocom") for word in value.split()
        ):
            raise _RefusedError("embedded-comment", f"{name} class {value!r}")
        if attribute == "class" and "MsoCommentReference" in value.split():
            raise _RefusedError("embedded-comment", f"{name} class {value!r}")
    return _style(element.get("style", ""))


def _walk(element: ET.Element, builder: _Builder, marks: frozenset[str], depth: int) -> None:
    name = _local(element)
    if name in ("ins", "del"):
        raise _RefusedError("unsupported-element", name)
    if name not in _BLOCKS and name not in _INLINE and name not in ("img", "br"):
        raise _RefusedError("unsupported-element", name)
    kinds = set(marks) | _check_attributes(element, name)
    if name == "sup":
        kinds.add("superscript")
    elif name == "sub":
        kinds.add("subscript")
    elif name in ("s", "strike"):
        kinds.add("strike")
    elif name == "u" or (name == "a" and element.get("href") is not None):
        kinds.add("underline")
    here = frozenset(kinds)
    if name == "br":
        builder.line_break(here)
    elif name == "img":
        builder.picture(here)
    if name in ("br", "img"):
        if len(element) or (element.text or "").strip():
            raise _RefusedError("malformed-xhtml", f"{name} with content")
        builder.text(element.tail or "", marks)
        return
    if name == "table":
        _table(element, builder, here, depth)
        builder.text(element.tail or "", marks)
        return
    block = name in _BLOCKS
    if block:
        builder.flush()
    saved = builder.numbering
    if name == "li":
        builder.numbering = Numbering(num_id=builder.list_kind, level=depth)
    saved_kind = builder.list_kind
    if name in ("ul", "ol"):
        builder.list_kind = 1 if name == "ul" else 2
    builder.text(element.text or "", here)
    for child in element:
        _walk(child, builder, here, depth + (name in ("ul", "ol")))
    if block:
        builder.flush()
    builder.numbering = saved
    builder.list_kind = saved_kind
    builder.text(element.tail or "", marks)


def _table(element: ET.Element, builder: _Builder, marks: frozenset[str], depth: int) -> None:
    builder.flush()
    index = builder.tables
    builder.tables += 1
    outer = builder.table
    row_index = 0
    _no_stray_text(element.text)
    for part in element:
        part_name = _local(part)
        _no_stray_text(part.tail)
        if part_name != "tr":
            _no_stray_text(part.text)
        rows = [part] if part_name == "tr" else list(part)
        if part_name not in ("tr", "thead", "tbody"):
            raise _RefusedError("unsupported-element", f"{part_name} in a table")
        _check_attributes(part, part_name)
        for row in rows:
            if _local(row) != "tr":
                raise _RefusedError("unsupported-element", f"{_local(row)} in a table body")
            row_marks = frozenset(set(marks) | _check_attributes(row, "tr"))
            _no_stray_text(row.text)
            if row is not part:
                _no_stray_text(row.tail)
            for cell_index, cell in enumerate(row):
                _no_stray_text(cell.tail)
                if _local(cell) not in ("td", "th"):
                    raise _RefusedError("unsupported-element", f"{_local(cell)} in a row")
                builder.table = outer or (index, row_index, cell_index)
                _walk(cell, builder, row_marks, depth)
                builder.flush()
            row_index += 1
    builder.table = outer


_BARE_LESS_THAN = re.compile(r"<(?![A-Za-z/!?])")


def _no_stray_text(text: str | None) -> None:
    """Text between table parts: a browser moves it out of the table; the reader refuses."""
    if text is not None and text.strip(_COLLAPSIBLE):
        raise _RefusedError("unsupported-element", "text between the parts of a table")


def read_div(div: str) -> tuple[tuple[Paragraph, ...], SectionRefusal | None, tuple[str, ...]]:
    """The paragraphs of one section's XHTML div, or a refusal, and notes on defects read
    through."""
    lowered = div.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return (), SectionRefusal("malformed-xhtml", "a DTD"), ()
    if "<![cdata[" in lowered:
        # An XML parser reads CDATA as text; an HTML parser reads it as a comment.
        return (), SectionRefusal("malformed-xhtml", "a CDATA section"), ()
    notes: tuple[str, ...] = ()
    bare = len(_BARE_LESS_THAN.findall(div))
    if bare:
        notes = (f"{bare} unescaped '<' read as text (invalid XHTML)",)
        div = _BARE_LESS_THAN.sub("&lt;", div)
    try:
        root = ET.fromstring(div)
    except ET.ParseError as error:
        return (), SectionRefusal("malformed-xhtml", f"not well-formed: {error}"), notes
    builder = _Builder()
    try:
        if _local(root) != "div":
            raise _RefusedError("malformed-xhtml", "the root is not a div")
        _walk(root, builder, frozenset(), 0)
        builder.flush()
    except _RefusedError as refused:
        return (), refused.refusal, notes
    return tuple(builder.paragraphs), None, notes


# --- the Bundle ------------------------------------------------------------------------------


def _section(raw: dict[str, Any]) -> Section:
    title = raw.get("title")
    if not isinstance(title, str):
        raise EpiRefusedError("invalid-bundle", "a section without a title")
    codings = raw.get("code", {}).get("coding", [])
    code = codings[0].get("code") if codings else None
    div = raw.get("text", {}).get("div")
    paragraphs, refusal, notes = read_div(div) if isinstance(div, str) else ((), None, ())
    children = tuple(_section(child) for child in raw.get("section", []))
    return Section(
        code=code,
        title=title,
        paragraphs=paragraphs,
        refusal=refusal,
        sections=children,
        notes=notes,
    )


def read_epi(data: bytes) -> Document:
    """The sections of an EMA ePI document Bundle, or ``EpiRefusedError``."""
    try:
        bundle = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EpiRefusedError("invalid-bundle", "not UTF-8 JSON") from error
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise EpiRefusedError("invalid-bundle", "not a Bundle")
    if bundle.get("type") != "document":
        raise EpiRefusedError("invalid-bundle", "not a document Bundle")
    compositions = [
        entry["resource"]
        for entry in bundle.get("entry", [])
        if isinstance(entry.get("resource"), dict) and "section" in entry["resource"]
    ]
    if len(compositions) != 1:
        raise EpiRefusedError("invalid-bundle", f"{len(compositions)} resources with sections")
    composition = compositions[0]
    quirks: list[str] = []
    kind = composition.get("resourceType")
    if kind == 0:
        # The EMA ePI API (2024 pilot data) serialises resourceType, language and status as the
        # number 0; the Bundle is otherwise a document with one Composition.
        quirks.append("Composition.resourceType is 0, not 'Composition'")
    elif kind != "Composition":
        raise EpiRefusedError("invalid-bundle", f"the resource with sections is a {kind!r}")
    codings = composition.get("type", {}).get("coding", [])
    return Document(
        title=str(composition.get("title", "")),
        date=composition.get("date"),
        document_type=codings[0].get("code") if codings else None,
        sections=tuple(_section(raw) for raw in composition["section"]),
        quirks=tuple(quirks),
    )


def walk(sections: tuple[Section, ...]) -> list[Section]:
    """Every section, depth first, in document order."""
    out: list[Section] = []
    for section in sections:
        out.append(section)
        out.extend(walk(section.sections))
    return out
