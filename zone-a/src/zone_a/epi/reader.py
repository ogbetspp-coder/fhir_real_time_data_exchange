"""A fail-closed reader for the sections of an EMA ePI document Bundle.

The EMA publishes electronic product information as FHIR document Bundles: one Composition whose
nested sections each carry a code, a title and an XHTML ``div``. This reader turns such a
Bundle into sections of paragraphs, with the same ``Paragraph`` and ``Mark`` model as the Word
reader (``zone_a.docx.reader``), so a checker does not care which reader produced the text.

It is written to the same rule as the Word reader: the text a browser shows, exactly, or a refusal
with a reason, for the text and its marks. It does not lay the page out: where a section's CSS
places or paints one text over another (a band of border or background over a line, a line height
smaller than the text in it, a block overflowing its table cell, a margin drawing a paragraph over
its list number, text at the bounds' edge, a combining mark on a space drawn as a stroke, text
moved far to the right, off a printed page, a bottom border on a block or cell drawn under a lone
sign as "≤"), the reader refuses only the cases listed below, and the rest is a stated residual;
rendering the page and comparing it with this reading is ADR 0005's renderer cross-check. Nor does
it parse the div as a browser does: it parses XML, and a browser the EMA's div as HTML. Where the
two build different trees it refuses the cases listed below (processing instructions, comments,
prefixed elements, self-closing elements other than ``br``, ``hr`` and ``img``, and the rest); any
other difference is a stated residual, and reading with an HTML5 parser, as a browser does, is a
tracked follow-up (``docs/roadmap.md``, item 3a). It is not the fidelity scanner
(``zone_a.fidelity.xhtml``), which is the contract for narrative this repository publishes and
stays as strict as it is; the EMA's own divs carry inline CSS on nearly every element, which that
scanner rightly refuses. Here each section is read on its own, so a section the reader cannot vouch
for is refused (``Section.refusal``) without losing the rest of the document.

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

What is marked (``Paragraph.marks``, the Word reader's kinds): ``sup`` and ``vertical-align: super``
as superscript, ``sub`` and ``vertical-align: sub`` as subscript, ``s``, ``strike`` and
``text-decoration: line-through`` as strike, a background other than white as ``shading-<colour>``,
a text colour other than black as ``color-<colour>`` (white or a nearly white colour as faint
instead, a nearly black one as nothing; ``#abc`` and ``rgb()`` are written as ``#aabbcc``, and any
other colour notation refuses the section), a font size under two points as faint, and ``u``, ``a``
with an ``href``, ``text-decoration: underline`` and a bottom border on an inline element as
underline (an underline turns a sign into another: "<" underlined is drawn "≤", and "1" with an
underlined "a" reads "1ª"), and a border on another side of an inline element as border (drawn as a
bar beside or over the text). Bold, italic and layout are not reported, except the layout that
draws other text, which refuses (below).

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
  what it shows, or a control character (U+007F or a C1 control, literal or as ``&#127;``),
  which a browser draws as a blank or a box.

Also refused as ``malformed-xhtml``: a root that is not a ``div``, a lone surrogate (in a div read
on its own; in a Bundle the document refuses first), a ``br``, ``img`` or ``hr`` with content,
markup an HTML parser rebuilds or reads otherwise (a block in an open ``p``, an ``li`` in an
``li``, an ``a`` in an ``a``, a table part outside a table, a processing instruction or comment, an
element with a namespace prefix, a self-closing element other than ``br``, ``hr`` and ``img``,
``</br>``, a reference to U+0080 to U+009F, which HTML maps through windows-1252 (all but five of
them)), elements nested deeper than 128 (a table's row group and row counted; a section deep in the
Bundle can be refused as nested too deeply to read within that bound, a false failure), and a CDATA
section (an XML parser reads it as text, an HTML parser as a comment). As ``unsupported-element``:
text between the parts of a table, which a browser moves out of the table, and a ``thead`` after a
table's body, which a browser draws at the top. As ``unsupported-style``: a margin or indent more
than an inch to the left, text drawn more than 12pt left of its container's start (the blocks'
margins and the indent inherited through blocks, inline elements and table rows summed, each read
as the most negative value any of its declarations names; a table cell starts again from zero, or
from the table's own offset when that is negative), which moves it off the page or over what lies
there, or a margin or indent in a unit the reader does not know (``%``, ``vw``, ``calc()``...);
layout that draws one text over another (a negative margin on inline text or at a block's top or
bottom, vertical padding on inline text and any padding on it over a background, a border on it
wider than a hairline, a height outside table parts and pictures, a line height below 12pt, 100% or
1em, a font above 14pt); a font outside a closed list of Unicode text fonts (a symbol font draws
other glyphs); a border value on inline text a browser would not accept whole, or one inherited
from the parent; a style CSS would split otherwise than the reader (a quote outside a font family
name or inside a quoted one, a comment, an escape, a bracket outside ``rgb()``, a character outside
ASCII letters, digits, whitespace and ``# % ! . , : ; ' " ( ) -``); and a margin or indent with a
value a browser drops (the wrong number of values, ``text-indent: auto``).

What refuses the document (``EpiRefusedError``): not UTF-8 JSON (or JSON with an integer longer
than Python's digit limit), a lone surrogate anywhere in it, not a document Bundle, not the shape
of one (a section, code, text, div or entry of the wrong JSON type), not exactly one entry with
sections, a resource with sections that is not a Composition, a section without a title, or nesting
too deep to read.
"""

from __future__ import annotations

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Final

from zone_a.docx.reader import Mark, Numbering, Paragraph

READER_VERSION = "epi-reader/1.1.0"
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


_STYLE_CHARS = re.compile(r"[A-Za-z0-9 \t\n\r\f#%!.,:;'\"()-]*")
_QUOTED_FAMILY = re.compile(r"'[^'\";]*'|\"[^'\";]*\"|[^'\";]*")


def _declarations(style: str) -> list[tuple[str, str]]:
    if not _STYLE_CHARS.fullmatch(style):
        raise _RefusedError("unsupported-style", "a character CSS tokenizes other than the reader")
    stripped = re.sub(r"rgb\([0-9 ,]*\)", "", style)
    if "(" in stripped or ")" in stripped:
        raise _RefusedError("unsupported-style", "a function or block")
    out: list[tuple[str, str]] = []
    for part in style.split(";"):
        if not part.strip():
            continue
        name, colon, value = part.partition(":")
        if not colon:
            raise _RefusedError("unsupported-style", f"not a declaration: {part.strip()!r}")
        if ("'" in value or '"' in value) and not (
            name.strip().lower() == "font-family"
            and all(_QUOTED_FAMILY.fullmatch(f.strip()) for f in value.split(","))
        ):
            raise _RefusedError("unsupported-style", f"a quote in {name.strip()!r}")
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
        size = float(match.group(2)) * (_LARGEST_FONT_POINTS if unit == "em" else _POINTS[unit])
        if match.group(1) and size > _OFF_SCREEN_POINTS:
            return False
    return True


def _points(value: str) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(pt|px|pc|in|cm|mm)", value)
    return float(match.group(1)) * _POINTS[match.group(2)] if match else None


_SIDES: Final = ("top", "right", "bottom", "left")
_BORDER_STYLES: Final = frozenset(
    {"none", "hidden", "solid", "dotted", "dashed", "double", "groove", "ridge", "inset", "outset"}
)


def _per_side(values: list[str]) -> dict[str, str]:
    """A 1-4 value box shorthand (top right bottom left), expanded per side."""
    if not values:
        return {}
    top = values[0]
    right = values[1] if len(values) > 1 else top
    bottom = values[2] if len(values) > 2 else top
    left = values[3] if len(values) > 3 else right
    return dict(zip(_SIDES, (top, right, bottom, left), strict=True))


def _zero_width(value: str) -> bool:
    return re.fullmatch(r"0+(\.0+)?[a-z]*", value) is not None


_WIDTH: Final = re.compile(r"thin|medium|thick|0|[0-9]+(\.[0-9]+)?(px|pt|pc|in|cm|mm|em|ex|rem)")


# CSS-wide keywords: a browser accepts one only as a declaration's whole value.
_GLOBAL_KEYWORDS: Final = frozenset({"inherit", "initial", "unset", "revert", "revert-layer"})


def _border_colour(token: str) -> bool:
    """A colour a browser accepts in a border shorthand: a named colour, or #rgb, #rgba, #rrggbb
    or #rrggbbaa (not ``none``, ``auto`` or a CSS-wide keyword, which ``_NAMED`` also holds)."""
    return (
        token in _NAMED and token not in ("none", "auto") and token not in _GLOBAL_KEYWORDS
    ) or re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})", token) is not None


def _valid_border(part: str, token: str) -> bool:
    if part == "style":
        return token in _BORDER_STYLES
    return _WIDTH.fullmatch(token) is not None


def _importance_ordered(style: str) -> list[tuple[str, str]]:
    """The declarations in the order a browser applies them: normal ones, then ``!important``."""
    normal: list[tuple[str, str]] = []
    important: list[tuple[str, str]] = []
    for part in style.split(";"):
        if not part.strip():
            continue
        name, _, value = part.partition(":")
        value = value.strip().lower()
        target = important if value.endswith("!important") else normal
        target.append((name.strip().lower(), value.removesuffix("!important").strip()))
    return normal + important


def _inline_borders(style: str) -> set[str]:
    """The marks a style's borders ask for on an inline element, read as a browser cascades them.

    A side is drawn when its style is neither none nor hidden (the initial style is none) and
    its width is not zero, the ``!important`` declarations applied last. A border along the
    bottom is drawn as an underline; one on another side as a bar beside or over the text ("|05
    mg", "1⋮5"), which is its own mark, ``border``. A border image is drawn whatever the style,
    on every side. A border value the reader cannot parse whole (a function such as ``var()``, a
    token that is no width, style or colour, the wrong number of values) refuses the section: a
    browser would drop it or read it otherwise, and neither can be told here.
    """
    styles = dict.fromkeys(_SIDES, "none")
    widths = dict.fromkeys(_SIDES, "medium")
    kinds: set[str] = set()
    for name, value in _importance_ordered(style):
        if not name.startswith("border") or name in ("border-collapse", "border-spacing"):
            continue
        if "(" in value or "\\" in value:
            raise _RefusedError("unsupported-style", f"{name}: {value}")
        tokens = value.split()
        if set(tokens) & _GLOBAL_KEYWORDS:
            # "inherit" takes the parent's border, which the reader does not follow; a keyword
            # among other values is invalid, and a browser drops the declaration.
            if len(tokens) != 1 or tokens[0] in ("inherit", "revert", "revert-layer"):
                raise _RefusedError("unsupported-style", f"{name}: {value}")
            to_initial = True
        else:
            to_initial = False
        if name.startswith("border-image"):
            if not to_initial and tokens != ["none"]:
                kinds |= {"underline", "border"}
            continue
        # `_style` has already refused any border property but the physical sides, their
        # width, style and colour, and the shorthands (`_LAYOUT`).
        rest = name.removeprefix("border").removeprefix("-")
        side, _, part = rest.partition("-")
        if side in _SIDES:
            targets = [side]
        else:
            targets, part = list(_SIDES), rest
        if to_initial:
            # "initial" and "unset" (a border is not inherited) are the initial values.
            for target in targets:
                if part in ("style", ""):
                    styles[target] = "none"
                if part in ("width", ""):
                    widths[target] = "medium"
            continue
        if part in ("style", "width"):
            count_ok = len(tokens) == 1 if side in _SIDES else 1 <= len(tokens) <= 4
            if not count_ok or not all(_valid_border(part, token) for token in tokens):
                raise _RefusedError("unsupported-style", f"{name}: {value}")
            per = {side: tokens[0]} if side in _SIDES else _per_side(tokens)
            for target in targets:
                (styles if part == "style" else widths)[target] = per[target]
        elif part == "":
            # The shorthand: at most one width, style and colour, in any order; missing ones
            # reset to the initial values.
            found_style = [t for t in tokens if t in _BORDER_STYLES]
            found_width = [t for t in tokens if _WIDTH.fullmatch(t)]
            colours = [t for t in tokens if t not in found_style and t not in found_width]
            if (
                not tokens
                or len(found_style) > 1
                or len(found_width) > 1
                or len(colours) > 1
                or not all(_border_colour(t) for t in colours)
            ):
                raise _RefusedError("unsupported-style", f"{name}: {value}")
            for target in targets:
                styles[target] = found_style[0] if found_style else "none"
                widths[target] = found_width[0] if found_width else "medium"
        # Colour neither draws nor removes a border.
    for side in _SIDES:
        if styles[side] in ("none", "hidden") or _zero_width(widths[side]):
            continue
        # A border wider than a hairline paints a band over the lines and words around the text
        # (an empty span with a 24pt white border blanks the line above it).
        width = 0.75 if widths[side] == "thin" else _length_points(widths[side])
        if width is None or width > 0.75:
            raise _RefusedError("unsupported-style", f"border-{side} wider than a hairline")
        kinds.add("underline" if side == "bottom" else "border")
    return kinds


# The Unicode text fonts a family list may name: the pinned labels' own and the common others.
_TEXT_FONTS: Final = frozenset(
    {
        "times new roman",
        "times new roman bold",
        "times",
        "arial",
        "arial unicode ms",
        "helvetica",
        "verdana",
        "calibri",
        "cambria",
        "segoe ui",
        "tahoma",
        "georgia",
        "garamond",
        "courier new",
        "courier",
        "serif",
        "sans-serif",
        "monospace",
    }
)


def _style(style: str) -> set[str]:
    """The mark kinds a style attribute asks for, or a refusal."""
    kinds: set[str] = set()
    for name, value in _declarations(style):
        if name == "font-family":
            # A symbol-encoded font draws other glyphs for the same code points (Wingdings "J"
            # is drawn as a smiling face, Symbol "³" as "≥"), so every family named must be a
            # Unicode text font the reader knows (ADR 0005's closed list).
            for family in value.split(","):
                if family.strip().strip("'\"") not in _TEXT_FONTS:
                    raise _RefusedError("unsupported-style", f"{name}: {value}")
            continue
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
    # The offset of the text's container from the section's start, in points (a cell's from
    # zero, or from its table's offset when negative); negative is to the left. The blocks' left
    # margins summed, and the first line's inherited indent.
    left: float = 0.0
    indent: float | None = None
    nesting: int = 0
    part_indent: float | None = None
    # What an HTML parser would close or move: an open p, li or a.
    open_p: bool = False
    open_li: bool = False
    open_a: bool = False
    cell: Any = None

    def text(self, text: str, marks: frozenset[str]) -> None:
        for character in text:
            if character in _COLLAPSIBLE:
                if self.characters and self.characters[-1] != "\n" and self.pending is None:
                    self.pending = marks
                continue
            if character == OBJECT:
                raise _RefusedError("reserved-character", "U+FFFC stands for a picture")
            if unicodedata.category(character) in ("Cf", "Cc"):
                # Soft hyphens, zero-width characters and bidirectional controls: a browser
                # hides them or reorders the text around them. A control (U+007F, a C1 control;
                # XML admits no other) it draws as a blank or a box.
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


# The elements a height is layout on: table parts, and a picture, whose size it sets.
_SIZED: Final = frozenset({"table", "thead", "tbody", "tfoot", "tr", "td", "th", "img"})


def _length_points(value: str) -> float | None:
    """A CSS length in points, or None for anything else (a percentage, a keyword)."""
    match = re.fullmatch(r"(-?)([0-9]+(?:\.[0-9]+)?)(pt|px|pc|in|cm|mm|em)?", value)
    if match is None:
        return None
    size = float(match.group(2)) * (
        12.0 if match.group(3) in (None, "em") else _POINTS[match.group(3)]
    )
    return -size if match.group(1) else size


# The bounds within which lines of text cannot be drawn over one another: the pinned labels set
# fonts of 12pt at most and line heights of 12.65pt or 107% at least (Brukinsa's 107%; text in
# them is at most 0.935 of its line). Text larger than the line
# it sits on reaches into the next one (a 40pt run under a 115% line hides the line above).
_LARGEST_FONT_POINTS: Final = 14.0
_SMALLEST_LINE_POINTS: Final = 12.0


def _nonzero_length(token: str) -> bool:
    """Whether a length token may be other than zero: anything but a parsed zero, a unit the
    reader cannot place (``rem``, ``ch``, ``calc()``) included."""
    points = _length_points(token)
    return points is None or points != 0


def _refuse_overprint(name: str, style: str) -> None:
    """Refuse layout a browser draws as other text (reviews 23 and 24 of fidelity-norm/3.0.0).

    A negative margin on inline text or a picture overprints its neighbour ("≥" drawn from ">"
    and "_"); a negative top or bottom margin, a height outside table parts and pictures, a line
    height below 12pt, 100% or 1em, and a font above 14pt lay one line over another; padding on
    inline text paints its background or border over the lines around it when it is vertical,
    or over its neighbours when it has a background (a border wider than a hairline is refused
    in ``_inline_borders``). These are bounds, not a layout engine: layout that still draws one
    text over another (see the module docstring) is a stated residual of the check.
    """
    declarations = _declarations(style)
    inline = name in _INLINE or name == "img"
    background = any(
        key in ("background", "background-color") and value not in ("transparent", "none")
        for key, value in declarations
    )
    for key, value in declarations:
        tokens = value.split()
        if key.startswith("margin"):
            sides = _per_side(tokens) if key == "margin" else {key.removeprefix("margin-"): value}
            for side, size in sides.items():
                points = _length_points(size)
                if points is not None and points < 0 and (inline or side in ("top", "bottom")):
                    raise _RefusedError("unsupported-style", f"{name} {key}: {value}")
        elif key.startswith("padding") and name in _INLINE:
            sides = _per_side(tokens) if key == "padding" else {key.removeprefix("padding-"): value}
            vertical = any(
                _nonzero_length(size) for side, size in sides.items() if side in ("top", "bottom")
            )
            if vertical or (background and any(_nonzero_length(t) for t in tokens)):
                raise _RefusedError("unsupported-style", f"{name} {key}: {value}")
        elif key in ("height", "max-height") and name not in _SIZED and value != "auto":
            raise _RefusedError("unsupported-style", f"{name} {key}: {value}")
        elif key == "line-height" and value != "normal":
            relative = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(%|em)?", value)
            if relative is not None:
                number = float(relative.group(1))
                low = number < (100 if relative.group(2) == "%" else 1)
            else:
                points = _length_points(value)
                low = points is None or points < _SMALLEST_LINE_POINTS
            if low:
                raise _RefusedError("unsupported-style", f"{name} {key}: {value}")
        elif key == "font-size":
            # `_style` refuses a font size in anything but an absolute unit.
            points = _length_points(value)
            if points is not None and points > _LARGEST_FONT_POINTS:
                raise _RefusedError("unsupported-style", f"{name} {key}: {value}")


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
    style = element.get("style", "")
    _refuse_overprint(name, style)
    return _style(style)


# How deep elements may nest (the fidelity scanner allows 32 below the root), and how far left of
# its container's start text may be drawn: the pinned labels never go below 0 outside a table
# cell or below -9pt inside one, and text further left is off the page or over what lies there.
_MAX_NESTING: Final = 128
_OFF_PAGE_BOUND_POINTS: Final = -12.0


def _offset_points(value: str) -> float:
    """A margin or indent in points, an em counted at the largest font the reader allows (14pt);
    ``_style`` has refused any unit it cannot place."""
    match = re.fullmatch(r"(-?)([0-9]+(?:\.[0-9]+)?)(pt|px|pc|in|cm|mm|em)", value.strip())
    if match is None:
        return 0.0
    unit = match.group(3)
    if unit == "em" and not match.group(1):
        return 0.0  # the element's font may be as small as 0pt: no credit to the right
    size = float(match.group(2)) * (_LARGEST_FONT_POINTS if unit == "em" else _POINTS[unit])
    return -size if match.group(1) else size


def _left_offsets(style: str) -> tuple[float, float | None]:
    """The most negative left margin and text indent any declaration names (order-free)."""
    margins: list[float] = []
    indents: list[float] = []
    for key, value in _declarations(style):
        tokens = value.split()
        if (
            (key in ("margin-left", "text-indent") and len(tokens) != 1)
            or (key == "margin" and not 1 <= len(tokens) <= 4)
            or (key == "text-indent" and "auto" in tokens)
        ):
            raise _RefusedError("unsupported-style", f"{key}: {value} (a browser drops it)")
        if key == "margin":
            if tokens:
                margins.append(_offset_points(_per_side(tokens).get("left", "0")))
        elif key == "margin-left":
            margins.extend(_offset_points(t) for t in tokens)
        elif key == "text-indent":
            indents.extend(_offset_points(t) for t in tokens)
    return (min(margins) if margins else 0.0), (min(indents) if indents else None)


def _walk(element: ET.Element, builder: _Builder, marks: frozenset[str], depth: int) -> None:
    builder.nesting += 1
    saved_left, saved_indent = builder.left, builder.indent
    saved_open = builder.open_p, builder.open_li, builder.open_a
    try:
        _walk_element(element, builder, marks, depth)
    finally:
        builder.nesting -= 1
        builder.left, builder.indent = saved_left, saved_indent
        builder.open_p, builder.open_li, builder.open_a = saved_open


def _enter_block(name: str, style: str, builder: _Builder) -> None:
    """Carry the block's left offset down; refuse text drawn left of its container's start.

    A table cell's content starts at the cell, so a cell starts again from zero, or from the
    table's own offset when that is negative (a block that overflows its cell is a stated
    residual). Each declaration alone is also bounded by an inch
    (``_on_page``); here the sum is: nested margins, and an indent inherited from a parent, add up.
    """
    if name in ("td", "th"):
        builder.left, builder.indent = min(0.0, builder.left), builder.part_indent
    margin, indent = _left_offsets(style)
    # A cell's margin does not apply (a table part never reaches here but through its cell).
    if name not in ("td", "th"):
        builder.left += margin
    if indent is not None:
        builder.indent = indent
    first_line = builder.left + min(0.0, builder.indent or 0.0)
    if min(builder.left, first_line) < _OFF_PAGE_BOUND_POINTS:
        raise _RefusedError("unsupported-style", f"{name} drawn left of its container's start")


def _walk_element(
    element: ET.Element, builder: _Builder, marks: frozenset[str], depth: int
) -> None:
    if builder.nesting > _MAX_NESTING:
        raise _RefusedError("malformed-xhtml", f"elements nested deeper than {_MAX_NESTING}")
    name = _local(element)
    if name in ("ins", "del"):
        raise _RefusedError("unsupported-element", name)
    if name not in _BLOCKS and name not in _INLINE and name not in ("img", "br"):
        raise _RefusedError("unsupported-element", name)
    if name in ("td", "th", "tr", "thead", "tbody") and element is not builder.cell:
        raise _RefusedError(
            "malformed-xhtml", f"{name} outside a table, which an HTML parser drops"
        )
    if builder.open_p and name in ("div", "p", "ul", "ol", "table", "hr", "li"):
        raise _RefusedError("malformed-xhtml", f"{name} in a p, which an HTML parser closes")
    if name == "li" and builder.open_li:
        raise _RefusedError("malformed-xhtml", "li in an li, which an HTML parser closes")
    if name == "a" and builder.open_a:
        raise _RefusedError("malformed-xhtml", "a in an a, which an HTML parser closes")
    if name in ("td", "th"):
        builder.open_p = builder.open_li = builder.open_a = False
    elif name == "p":
        builder.open_p = True
    elif name in ("ul", "ol"):
        builder.open_li = False
    elif name == "li":
        builder.open_li = True
    elif name == "a":
        builder.open_a = True
    kinds = set(marks) | _check_attributes(element, name)
    if name == "sup":
        kinds.add("superscript")
    elif name == "sub":
        kinds.add("subscript")
    elif name in ("s", "strike"):
        kinds.add("strike")
    elif name == "u" or (name == "a" and element.get("href") is not None):
        kinds.add("underline")
    if name in _INLINE:
        kinds |= _inline_borders(element.get("style", ""))
    here = frozenset(kinds)
    if name in _BLOCKS:
        _enter_block(name, element.get("style", ""), builder)
    elif name in _INLINE:
        _, own = _left_offsets(element.get("style", ""))
        if own is not None:
            builder.indent = own
    # Any text at all, a space or U+00A0 included: an HTML parser keeps it after the element.
    if name == "hr" and (len(element) or element.text):
        raise _RefusedError("malformed-xhtml", "hr with content")
    if name == "br":
        builder.line_break(here)
    elif name == "img":
        builder.picture(here)
    if name in ("br", "img"):
        if len(element) or element.text:
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
    # A row group and a row stand between the table and each cell: they count toward the bound.
    builder.nesting += 2
    try:
        _table_rows(element, builder, marks, depth)
    finally:
        builder.nesting -= 2


def _table_rows(element: ET.Element, builder: _Builder, marks: frozenset[str], depth: int) -> None:
    builder.flush()
    saved_part = builder.part_indent
    index = builder.tables
    builder.tables += 1
    outer = builder.table
    row_index = 0
    body_seen = False
    _no_stray_text(element.text)
    for part in element:
        part_name = _local(part)
        _no_stray_text(part.tail)
        if part_name != "tr":
            _no_stray_text(part.text)
        rows = [part] if part_name == "tr" else list(part)
        if part_name not in ("tr", "thead", "tbody"):
            raise _RefusedError("unsupported-element", f"{part_name} in a table")
        if part_name == "thead" and body_seen:
            # A browser draws a table's first header group at the top, wherever it is written.
            raise _RefusedError("unsupported-element", "thead after the table's body")
        body_seen = body_seen or part_name != "thead"
        part_marks = _check_attributes(part, part_name) if part_name != "tr" else set()
        _, own = _left_offsets(part.get("style", ""))
        part_indent = own if own is not None else builder.indent
        for row in rows:
            if _local(row) != "tr":
                raise _RefusedError("unsupported-element", f"{_local(row)} in a table body")
            row_marks = frozenset(set(marks) | part_marks | _check_attributes(row, "tr"))
            _, own = _left_offsets(row.get("style", "") if row is not part else "")
            builder.part_indent = own if own is not None else part_indent
            _no_stray_text(row.text)
            if row is not part:
                _no_stray_text(row.tail)
            for cell_index, cell in enumerate(row):
                _no_stray_text(cell.tail)
                if _local(cell) not in ("td", "th"):
                    raise _RefusedError("unsupported-element", f"{_local(cell)} in a row")
                builder.table = outer or (index, row_index, cell_index)
                builder.cell = cell
                _walk(cell, builder, row_marks, depth)
                builder.flush()
            row_index += 1
    builder.table = outer
    builder.part_indent = saved_part


_HTML_OTHERWISE = re.compile(
    r"<\?|<!--|xmlns:|</br\b|&#0*(12[89]|1[3-5][0-9]);|&#[xX]0*[89][0-9a-fA-F];"
    r"|<(?!(?:br|hr|img)[\s/>])[A-Za-z][^\s/>]*(?:\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*\s*/>"
)
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
    if _HTML_OTHERWISE.search(div):
        return (), SectionRefusal("malformed-xhtml", "markup an HTML parser reads otherwise"), ()
    notes: tuple[str, ...] = ()
    bare = len(_BARE_LESS_THAN.findall(div))
    if bare:
        notes = (f"{bare} unescaped '<' read as text (invalid XHTML)",)
        div = _BARE_LESS_THAN.sub("&lt;", div)
    try:
        root = ET.fromstring(div)
    except (ET.ParseError, ValueError) as error:
        # ValueError (UnicodeEncodeError among them): a lone surrogate. ``read_epi`` refuses a
        # Bundle holding one first; this is for a div read on its own.
        return (), SectionRefusal("malformed-xhtml", f"not well-formed: {error}"), notes
    builder = _Builder()
    try:
        if _local(root) != "div":
            raise _RefusedError("malformed-xhtml", "the root is not a div")
        _walk(root, builder, frozenset(), 0)
        builder.flush()
    except _RefusedError as refused:
        return (), refused.refusal, notes
    except RecursionError:
        return (), SectionRefusal("malformed-xhtml", "nested too deeply to read"), notes
    return tuple(builder.paragraphs), None, notes


# --- the Bundle ------------------------------------------------------------------------------


def _section(raw: dict[str, Any]) -> Section:
    title = raw.get("title")
    if not isinstance(title, str):
        raise EpiRefusedError("invalid-bundle", "a section without a title")
    codings = raw.get("code", {}).get("coding", [])
    code = codings[0].get("code") if codings else None
    if code is not None and not isinstance(code, str):
        raise EpiRefusedError("invalid-bundle", "a section code that is not a string")
    div = raw.get("text", {}).get("div")
    if div is not None and not isinstance(div, str):
        raise EpiRefusedError("invalid-bundle", "a section text that is not a string")
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
        return _read_epi(data)
    except RecursionError as error:
        raise EpiRefusedError("invalid-bundle", "nested too deeply to read") from error
    except (AttributeError, TypeError, KeyError) as error:
        # A section, code, text or entry of the wrong JSON type: not the shape of a document.
        raise EpiRefusedError("invalid-bundle", "not the shape of a document Bundle") from error


def _read_epi(data: bytes) -> Document:
    try:
        bundle = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        # ValueError: not JSON, or an integer past Python's digit limit.
        raise EpiRefusedError("invalid-bundle", "not UTF-8 JSON Python can read") from error
    try:
        # A lone surrogate escape ("\\ud800") decodes to a string no UTF-8 writer can write.
        json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as error:
        raise EpiRefusedError("invalid-bundle", "a lone surrogate") from error
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
