"""A fail-closed reader for the sections of an EMA ePI document Bundle.

The EMA publishes electronic product information as FHIR document Bundles: one Composition whose
nested sections each carry a code, a title and an XHTML ``div``. This reader turns such a
Bundle into sections of paragraphs, with the same ``Paragraph`` and ``Mark`` model as the Word
reader (``label_docx.reader``), so a checker does not care which reader produced the text.

It is written to the same rule as the Word reader: the text a browser shows, exactly, or a refusal
with a reason, for the text and its marks. It does not lay the page out: where a section's CSS
places or paints one text over another (a band of border or background over a line, a line height
smaller than the text in it, a block overflowing its table cell, a margin drawing a paragraph over
its list number, text at the bounds' edge, a combining mark on a space drawn as a stroke, text
moved far to the right, off a printed page, a bottom border on a block or cell drawn under a lone
sign as "≤", text shifted by up to 6pt over the line above or below), the reader refuses only the
cases listed below. Nor does it parse the div as a browser does: it parses XML, and a browser the
EMA's div as HTML. Where the two build different trees it refuses the cases listed below
(processing instructions, comments, prefixed elements, self-closing elements other than ``br``,
``hr`` and ``img``, and the rest). What remains of either is held in check by Chrome itself, which
shows every section read (``scripts/browser_oracle.py``, and the service for every ePI it ingests).
Each section is read on its own, so a section the reader cannot vouch for is refused
(``Section.refusal``) without losing the rest of the document.

What a section's text is:

- Block elements (``div``, ``p``, ``h1`` to ``h6``, ``li``, ``td``, ``th``, ``table``, ``tr``,
  ``ul``, ``ol``, ``thead``, ``tbody``, ``hr``) end one paragraph and start the next. A paragraph
  with no text is dropped. A list item carries ``numbering``: ``num_id`` 1 in a ``ul`` (a bullet), 2
  in an ``ol`` (a number); its first paragraph's ``numbering.text`` is the marker a browser draws
  before it ("1.", "b.", "iv.", "\u2022", "\u25e6", "\u25a0"), with ``suffix`` ``space``, by the
  list's ``type`` and ``start`` and, for bullets, its depth (``_list_state``). Like the Word
  reader's labels, a marker is never in the text. A list item without text, or one that begins with
  a list, is refused: its marker would stand beside nothing the reader reads.
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

What is marked (``Paragraph.marks``, the Word reader's kinds): ``sup``, ``vertical-align: super``
and a ``position: relative`` shift up by a point or more as superscript, ``sub``,
``vertical-align: sub`` and such a shift down as subscript, ``s``, ``strike`` and
``text-decoration: line-through`` as strike, a background other than white as ``shading-<colour>``,
text whose colour has a contrast under 1.33:1 (WCAG 2) with the background painted under it (the
nearest one, else the white page) as faint, whatever the two colours are (white on white, black
on black, navy on navy; white on black is read), other text in a colour other than black as
``color-<colour>`` (a nearly black one as nothing; ``#abc`` and ``rgb()`` are written as
``#aabbcc``, and any other colour notation refuses the section), a font size under two points as
faint, and ``u``, ``a``
with an ``href``, ``text-decoration: underline`` and a bottom border on an inline element as
underline (an underline turns a sign into another: "<" underlined is drawn "≤", and "1" with an
underlined "a" reads "1ª"), and a border on another side of an inline element as border (drawn as a
bar beside or over the text). A line is drawn in its own colour (a decoration in the colour of the
element that declares it, a border in its border colour) and is marked only where it can be seen
on the background under it, by the faint rule; one that cannot be seen refuses. Bold and italic
are marked as a browser computes them: the font weight carried down from the parent (``b`` and
``strong`` bolder than it, ``th`` and ``h1`` to ``h6`` bold, then ``font-weight``; ``bolder`` and
``lighter`` as CSS Fonts 4 steps them) drawn bold at 600 and above (a numeric weight from 501 to
599, which a family's bold face may draw, refuses), and ``em``, ``i`` and ``font-style: italic``
or ``oblique`` as italic. Layout is not reported, except the layout that draws other text, which
refuses (below).

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
- ``format-character``: an invisible formatting character (Unicode category Cf: zero-width
  characters, bidirectional controls; a soft hyphen, U+00AD, is read as text, as a browser and
  the Word reader keep it) or any other code point Unicode says to ignore
  (Default_Ignorable_Code_Point: a variation selector, a Hangul filler), which a browser hides or
  which reorders what it shows, or a control character (U+007F or a C1 control, literal or as
  ``&#127;``), which a browser draws as a blank or a box.
- ``private-use-character``: a private-use code point (category Co), whose glyph is the font's
  choice (a Symbol font's U+F0B3 is drawn "≥").
- ``unassigned-character``: a code point Unicode 16.0 does not assign (category Cn).

Also refused as ``malformed-xhtml``: a root that is not a ``div``, a lone surrogate (in a div read
on its own; in a Bundle the document refuses first), a ``br``, ``img`` or ``hr`` with content,
markup an HTML parser rebuilds or reads otherwise (a block in an open ``p``, an ``li`` in an ``li``,
an ``a`` in an ``a``, a heading in a heading, a table part outside a table, a processing instruction
or comment, an element with a namespace prefix, a self-closing element other than ``br``, ``hr`` and
``img``, ``</br>``, a reference to U+0080 to U+009F, which HTML maps through windows-1252 (all but
five of them)), elements nested deeper than 128 (a table's row group and row counted; a section deep
in the Bundle can be refused as nested too deeply to read within that bound, a false failure), and a
CDATA section (an XML parser reads it as text, an HTML parser as a comment). As
``unsupported-element``: text between the parts of a table, which a browser moves out of the table,
and a ``thead`` after a table's body (a browser draws a table's first header group at the top). As
``unsupported-style``: a heading without its own ``font-size`` (a browser draws it larger), a margin
or indent more than an inch to the left, text drawn more than 12pt left of its container's start
(the blocks' margins and the indent inherited through blocks, inline elements and table rows summed,
each read as the most negative value any of its declarations names; a table cell starts again from
zero, or from the table's own offset when that is negative), which moves it off the page or over
what lies there, or a margin or indent in a unit the reader does not know (``%``, ``vw``,
``calc()``...); layout that draws one text over another (a negative margin on inline text or at a
block's top or bottom, vertical padding on inline text and any padding on it over a background, a
border on it wider than a hairline, a height outside table parts and pictures, a line height below
12pt, 100% or 1em, a font above 14pt); a font outside a closed list of Unicode text fonts (a symbol
font draws other glyphs); a border value on inline text a browser would not accept whole, or one
inherited from the parent; a shift other than ``position: relative`` with exactly one of ``top``
and ``bottom`` on an inline element other than ``sup`` and ``sub``, without ``vertical-align``,
by at most 6pt (``top`` or ``bottom`` alone included; a background on or inside a shifted element,
which is painted over the text around it; a shift inside another, or inside a ``sup``, ``sub``
or ``vertical-align``, and one of a point or more around one, since each is bounded only on its
own); a colour or background keyword a browser drops (``color: none``, ``background-color:
auto``), which leaves the declaration before it in force; a style CSS would split otherwise than
the reader (a quote outside a font family name or inside a quoted one, a comment, an escape, a
bracket outside ``rgb()``, a character outside ASCII letters, digits, whitespace and
``# % ! . , : ; ' " ( ) -``); a margin or indent with a value a browser drops (the wrong
number of values, ``text-indent: auto``); a decoration line or an inline border drawn in a colour
that cannot be seen on the background under it, or a border colour with alpha; ``vertical-align``
``top``, ``middle`` or ``bottom`` on inline text, which moves it with no mark; a ``width`` other
than ``auto`` outside ``table``, ``td``, ``th`` and ``img`` (text overflows a narrow box and its
background); text drawn left of the block box that paints a background other than the one around
it (margins and indent summed as above; in a table cell, left of the cell); a list item whose
marker would be faint (the item's colour on the background outside it, or a size under 2pt); and
a font weight from 501 to 599. As ``unsupported-element``: a block inside an inline element, which
the inline's background, raise, shift and border do not reach as the reader would carry them.

What refuses the document (``EpiRefusedError``): not UTF-8 JSON (or JSON with an integer longer
than Python's digit limit, a name repeated in an object, ``NaN`` or ``Infinity``), a lone surrogate
anywhere in it, not a document Bundle, not the shape of one (a section, code, text, div or entry
of the wrong JSON type), not exactly one entry with sections, a resource with sections that is not
a Composition or not the first entry's, a section without a title, or arrays and objects nested
more than 100 deep (``invalid-bundle``); and a section's or the Composition's title holding a
character a div refuses (other than whitespace), with that character's code. A title is otherwise
served as written, a raw FHIR string: its whitespace is not collapsed.
"""

from __future__ import annotations

import bisect
import functools
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Any, Final

from label_docx.reader import Mark, Numbering, Paragraph

# The version of the rules above; versions.lock.json ties it to this file (tests/test_locks.py).
READER_VERSION = "epi-reader/1.3.3"
XHTML = "http://www.w3.org/1999/xhtml"
OBJECT = "\ufffc"
_COLLAPSIBLE = " \t\n\r\f"
_SOFT_HYPHEN = "\u00ad"

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
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
} | _HEADINGS
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
    "br": _COMMON | {"clear"},
    **{name: set(_COMMON) for name in _HEADINGS},
    **{name: set(_COMMON) for name in ("span", "strong", "b", "em", "i", "u", "s", "strike")},
    "sup": _COMMON,
    "sub": _COMMON,
}

# CSS properties that move or frame text but cannot hide it, change a character or change what
# it means (``tab-stops``, like the ``mso-`` properties, is Word's own and a browser ignores it).
# Anything not here, and not handled in ``_style``, refuses the section.
_LAYOUT = re.compile(
    r"(margin|padding|border)(-(top|bottom|left|right))?(-(width|style|color))?"
    r"|border-(collapse|image|spacing)|line-height|text-align|text-indent|width|height"
    r"|min-width|min-height|break-(before|after|inside)|page-break-(before|after|inside)"
    r"|font-family|font-weight|font-style|layout-grid-mode|mso-[a-z-]+|tab-stops"
)
_BLACK = {"black", "windowtext", "#000000", "auto", "initial"}
_WHITE = {"white", "#ffffff", "transparent"}
_KEYWORDS = {"none", "auto", "inherit", "initial", "currentcolor", "transparent"}
# A margin or indent further left than one inch moves text off the page, not to its edge.
_OFF_SCREEN_POINTS = 72.0
_POINTS = {"pt": 1.0, "px": 0.75, "pc": 12.0, "in": 72.0, "cm": 72 / 2.54, "mm": 72 / 25.4}


# Unicode's Default_Ignorable_Code_Point, spelled out (Unicode 16.0, DerivedCoreProperties.txt):
# code points a renderer draws as nothing. Python's unicodedata does not expose the property.
DEFAULT_IGNORABLE: Final = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


_IGNORABLE_LOWS: Final = tuple(low for low, _ in DEFAULT_IGNORABLE)


def is_default_ignorable(code_point: int) -> bool:
    """Unicode's Default_Ignorable_Code_Point (Unicode 16.0), from the table above."""
    # The ranges are sorted and apart: only the last one starting at or below can hold it.
    index = bisect.bisect_right(_IGNORABLE_LOWS, code_point) - 1
    return index >= 0 and code_point <= DEFAULT_IGNORABLE[index][1]


class EpiRefusedError(Exception):
    """The Bundle is not an ePI document this reader can read."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SectionRefusal:
    """Why the reader refused a section's div: a code the module docstring lists, and a detail."""

    code: str
    detail: str


@dataclass(frozen=True)
class Section:
    """One section of the Composition, read: its code, title, paragraphs and nested sections.

    ``code`` is the code of the section's first coding, if any. ``refusal`` is set when the
    reader refused the section's div; the rest of the document is still read.
    """

    code: str | None
    title: str
    paragraphs: tuple[Paragraph, ...]
    refusal: SectionRefusal | None
    sections: tuple[Section, ...] = ()
    # Defects in the div the reader read through by a stated rule, for the report.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    """The Composition of an ePI document Bundle, read: its title, date, type and sections.

    ``document_type`` is the code of the Composition's first ``type`` coding, if any.
    """

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


# A style is read many times over (each element's checks, each character's marks); the reading is
# a function of the string alone, and a refusal is raised again each time (no exception is kept).
@functools.lru_cache(maxsize=4096)
def _declarations(style: str) -> tuple[tuple[str, str], ...]:
    if not _STYLE_CHARS.fullmatch(style):
        raise _RefusedError("unsupported-style", "a character CSS tokenizes other than the reader")
    # A bracket in a quoted font family name is part of the name ('CG Times (WN)').
    stripped = re.sub(r"rgb\([0-9 ,]*\)|'[^'\";]*'|\"[^'\";]*\"", "", style)
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
    return tuple(out)


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
    """A colour in one spelling, or a refusal.

    ``#abc`` and ``rgb(170, 187, 204)`` are written as ``#aabbcc``; ``#aabbcc`` and a named
    colour pass as they are. Any other notation (alpha, ``hsl()``, percentages) is refused: it
    could hide text unseen.
    """
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
    """Nearly white: a background that reads as the page (Word's light theme colours included)."""
    channels = _channels(colour)
    return channels is not None and min(channels) >= 0xE0


# The named colours ``_NAMED`` accepts, as channels (CSS Color 4; ``windowtext`` is black).
_NAMED_CHANNELS: Final[dict[str, tuple[int, int, int]]] = {
    "black": (0, 0, 0),
    "windowtext": (0, 0, 0),
    "silver": (0xC0, 0xC0, 0xC0),
    "gray": (0x80, 0x80, 0x80),
    "grey": (0x80, 0x80, 0x80),
    "white": (0xFF, 0xFF, 0xFF),
    "maroon": (0x80, 0, 0),
    "red": (0xFF, 0, 0),
    "purple": (0x80, 0, 0x80),
    "fuchsia": (0xFF, 0, 0xFF),
    "green": (0, 0x80, 0),
    "lime": (0, 0xFF, 0),
    "olive": (0x80, 0x80, 0),
    "yellow": (0xFF, 0xFF, 0),
    "navy": (0, 0, 0x80),
    "blue": (0, 0, 0xFF),
    "teal": (0, 0x80, 0x80),
    "aqua": (0, 0xFF, 0xFF),
    "lightgrey": (0xD3, 0xD3, 0xD3),
    "lightgray": (0xD3, 0xD3, 0xD3),
    "darkgray": (0xA9, 0xA9, 0xA9),
    "darkgrey": (0xA9, 0xA9, 0xA9),
}
# Text is faint (a reader cannot see it) where its colour's contrast with what is painted under
# it (WCAG 2's ratio) is below 1.33:1: #e0e0e0 on white, the palest grey read as faint since
# epi-reader/1.0.0, is 1.32:1, and black on a black or #111111 background is 1:1 and 1.1:1.
_FAINT_CONTRAST: Final = 1.33
# The mark for text faint by its colour, kept apart from a tiny font's while the walk carries
# marks down (a descendant may set a colour that can be seen); ``_marks`` writes it as faint.
_FAINT_COLOUR: Final = "faint-colour"


def _rgb(colour: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """The channels of a colour in ``_colour``'s spelling; ``default`` for None."""
    if colour is None:
        return default
    channels = _channels(colour) or _NAMED_CHANNELS.get(colour)
    if channels is None:
        # ``_paint`` stores only a colour of ``_NAMED_CHANNELS`` or ``#rrggbb``.
        raise ValueError(f"not a colour: {colour!r}")
    return channels


def _luminance(channels: tuple[int, int, int]) -> float:
    def linear(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    """WCAG 2's contrast ratio of two colours, as ``src/authority/t/css.ts`` computes it."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _dark(colour: str) -> bool:
    """Nearly black: reads as black text."""
    channels = _channels(colour)
    return channels is not None and max(channels) <= 0x20


def _on_page(value: str) -> bool:
    """Whether a margin or indent keeps text on the page.

    Every part of it is ``0``, ``auto`` or a length in a unit the reader knows, and none moves
    text more than an inch to the left.
    """
    for part in value.split():
        if part in ("0", "auto"):
            continue
        match = re.fullmatch(r"(-?)([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(pt|px|pc|in|cm|mm|em)", part)
        if match is None:
            return False
        unit = match.group(3)
        size = float(match.group(2)) * (_LARGEST_FONT_POINTS if unit == "em" else _POINTS[unit])
        if match.group(1) and size > _OFF_SCREEN_POINTS:
            return False
    return True


def _points(value: str) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(pt|px|pc|in|cm|mm)", value)
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


_WIDTH: Final = re.compile(
    r"thin|medium|thick|0|(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(px|pt|pc|in|cm|mm|em|ex|rem)"
)


# CSS-wide keywords: a browser accepts one only as a declaration's whole value.
_GLOBAL_KEYWORDS: Final = frozenset({"inherit", "initial", "unset", "revert", "revert-layer"})


def _border_colour(token: str) -> bool:
    """Whether the token is a colour a browser accepts in a border shorthand.

    That is a named colour, or #rgb, #rgba, #rrggbb or #rrggbbaa (not ``none``, ``auto`` or a
    CSS-wide keyword, which ``_NAMED`` also holds).
    """
    return (
        token in _NAMED and token not in ("none", "auto") and token not in _GLOBAL_KEYWORDS
    ) or re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})", token) is not None


def _valid_border(part: str, token: str) -> bool:
    if part == "style":
        return token in _BORDER_STYLES
    if part == "color":
        return _border_colour(token)
    return _WIDTH.fullmatch(token) is not None


@functools.lru_cache(maxsize=4096)
def _importance_ordered(style: str) -> tuple[tuple[str, str], ...]:
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
    return (*normal, *important)


def _inline_borders(style: str, builder: _Builder) -> set[str]:
    """The marks a style's borders ask for on an inline element, read as a browser cascades them.

    A side is drawn when its style is neither none nor hidden (the initial style is none) and
    its width is not zero, the ``!important`` declarations applied last. A border along the
    bottom is drawn as an underline; one on another side as a bar beside or over the text ("|05
    mg", "1⋮5"), which is its own mark, ``border``. A border image is drawn whatever the style,
    on every side. A border value the reader cannot parse whole (a function such as ``var()``, a
    token that is no width, style or colour, the wrong number of values) refuses the section: a
    browser would drop it or read it otherwise, and neither can be told here. A side drawn in a
    colour that cannot be seen on the background under it (``_faint``; the colour is the text's
    unless one is given) refuses, and so does one drawn in a colour with alpha.
    """
    styles = dict.fromkeys(_SIDES, "none")
    widths = dict.fromkeys(_SIDES, "medium")
    colours = dict.fromkeys(_SIDES, "currentcolor")
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
                if part in ("color", ""):
                    colours[target] = "currentcolor"
            continue
        if part in ("style", "width", "color"):
            count_ok = len(tokens) == 1 if side in _SIDES else 1 <= len(tokens) <= 4
            if not count_ok or not all(_valid_border(part, token) for token in tokens):
                raise _RefusedError("unsupported-style", f"{name}: {value}")
            per = {side: tokens[0]} if side in _SIDES else _per_side(tokens)
            for target in targets:
                {"style": styles, "width": widths, "color": colours}[part][target] = per[target]
        elif not part:
            # The shorthand: at most one width, style and colour, in any order; missing ones
            # reset to the initial values.
            found_style = [t for t in tokens if t in _BORDER_STYLES]
            found_width = [t for t in tokens if _WIDTH.fullmatch(t)]
            found_colour = [t for t in tokens if t not in found_style and t not in found_width]
            if (
                not tokens
                or len(found_style) > 1
                or len(found_width) > 1
                or len(found_colour) > 1
                or not all(_border_colour(t) for t in found_colour)
            ):
                raise _RefusedError("unsupported-style", f"{name}: {value}")
            for target in targets:
                styles[target] = found_style[0] if found_style else "none"
                widths[target] = found_width[0] if found_width else "medium"
                colours[target] = found_colour[0] if found_colour else "currentcolor"
    for side in _SIDES:
        if styles[side] in ("none", "hidden") or _zero_width(widths[side]):
            continue
        # A border is drawn in its colour over the element's background (``currentcolor``, the
        # initial colour, is the text's); one that cannot be seen there is no line to mark.
        colour = builder.colour if colours[side] == "currentcolor" else _colour(colours[side])
        if _faint(colour, builder.backdrop):
            raise _RefusedError("unsupported-style", f"border-{side} in a colour not seen")
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
        "arial narrow",
        "cg times",
        "cg times (wn)",
        "cg times (w1)",
        "new york",
        "sabon",
        "gulliver",
        "trebuchet ms",
        "tempus sans itc",
        "serif",
        "sans-serif",
        "monospace",
    }
)


# Keywords ``_colour`` passes that are no value of the property: a browser drops the declaration
# and keeps the one before it ("color: black; color: none" is black), where the reader would read
# the last. The reader refuses them rather than follow two readings. ``background: none`` is valid
# (no image, and the colour reset to transparent).
_DROPPED: Final[dict[str, frozenset[str]]] = {
    "color": frozenset({"none", "auto"}),
    "background-color": frozenset({"none", "auto"}),
    "background": frozenset({"auto"}),
}


def _style(style: str) -> None:
    """Refuse a style attribute with a declaration the reader cannot read as a browser does."""
    for name, value in _declarations(style):
        if name == "font-family":
            # A symbol-encoded font draws other glyphs for the same code points (Wingdings "J"
            # is drawn as a smiling face, Symbol "³" as "≥"), so every family named must be a
            # Unicode text font the reader knows (``_TEXT_FONTS``, a closed list).
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
            # Checked here; the colour is judged against what is painted under it (``_paint``).
            if _colour(value) in _DROPPED[name]:
                raise _RefusedError("unsupported-style", f"{name}: {value} (a browser drops it)")
        elif name in ("position", "top", "bottom"):
            # A shift, read with the element it is on (``_shift``).
            continue
        elif name in ("background", "background-color"):
            colour = _colour(value)
            if colour in _DROPPED[name]:
                raise _RefusedError("unsupported-style", f"{name}: {value} (a browser drops it)")
        elif name == "font-size":
            points = _points(value)
            if points is None:
                raise _RefusedError("unsupported-style", f"font-size: {value}")
        elif name == "vertical-align":
            # Marked by the element's own value (``_own_lines``), which a later one replaces.
            if value not in ("super", "sub", "top", "middle", "bottom", "baseline"):
                raise _RefusedError("unsupported-style", f"vertical-align: {value}")
        elif name == "text-decoration":
            words = set(value.split())
            if words - {"underline", "none", "line-through", "solid"}:
                raise _RefusedError("unsupported-style", f"text-decoration: {value}")
        else:
            raise _RefusedError("unsupported-style", f"{name}: {value}")


# A relative shift moves text off its line by at most half the smallest line the reader allows
# (``_SMALLEST_LINE_POINTS``); one of a point or more raises or lowers it as a superscript or a
# subscript does.
_SHIFT_BOUND_POINTS: Final = 6.0
_SHIFT_MARK_POINTS: Final = 1.0
_SHIFT_LENGTH: Final = re.compile(r"([+-]?)([0-9]+(?:\.[0-9]*)?|\.[0-9]+)(pt|px|pc|in|cm|mm|em)?")


def _shift(name: str, style: str) -> set[str]:
    """The mark a ``position: relative`` shift asks for (superscript, subscript or none).

    Read only on an inline element other than ``sup`` and ``sub``, with exactly one of ``top``
    and ``bottom``, and no ``vertical-align``; ``top`` or ``bottom`` without it, or any other
    ``position``, refuses. The shift is bounded by ``_SHIFT_BOUND_POINTS`` (an ``em`` counted at
    the largest font the reader allows); what a shift within it still draws over the line above
    or below is a stated residual.
    """
    declared = dict(_importance_ordered(style))
    position, top, bottom = declared.get("position"), declared.get("top"), declared.get("bottom")
    if position is None and top is None and bottom is None:
        return set()
    shape = f"{name}: position {position}, top {top}, bottom {bottom}"
    if (
        position != "relative"
        or name not in _INLINE
        or name in ("sup", "sub")
        or (top is None) == (bottom is None)
        or "vertical-align" in declared
    ):
        raise _RefusedError("unsupported-style", shape)
    offset = _SHIFT_LENGTH.fullmatch(top if top is not None else str(bottom))
    if offset is None or (offset.group(3) is None and float(offset.group(2)) != 0):
        raise _RefusedError("unsupported-style", shape)
    unit = offset.group(3)
    size = float(offset.group(2)) * (
        0.0 if unit is None else _LARGEST_FONT_POINTS if unit == "em" else _POINTS[unit]
    )
    # A positive ``top`` moves the text down; a positive ``bottom`` moves it up.
    raised = size * (-1 if offset.group(1) == "-" else 1) * (-1 if top is not None else 1)
    if abs(raised) > _SHIFT_BOUND_POINTS:
        raise _RefusedError("unsupported-style", shape)
    if raised >= _SHIFT_MARK_POINTS:
        return {"superscript"}
    if raised <= -_SHIFT_MARK_POINTS:
        return {"subscript"}
    return set()


def _paint(style: str, builder: _Builder) -> bool:
    """Carry an element's text colour, and the background painted under its text, down the walk.

    The last declaration of each wins, ``!important`` ones last. A colour keyword keeps the
    colour the text already has (``initial`` is black); a background that paints nothing keeps
    what is under it, and ``currentcolor`` paints the text's own colour. Whether the element
    paints a background.
    """
    colour: str | None = None
    background: str | None = None
    for name, value in _importance_ordered(style):
        if name == "color":
            colour = _colour(value)
        elif name in ("background", "background-color"):
            background = _colour(value)
    if colour == "initial":
        builder.colour = None
    elif colour is not None and colour not in _KEYWORDS - {"transparent"}:
        builder.colour = colour
    if background == "currentcolor":
        if builder.colour == "transparent":
            return False
        builder.backdrop = builder.colour or "black"
        return True
    if background is not None and background not in _KEYWORDS:
        builder.backdrop = background
        return True
    return False


def _paint_element(style: str, builder: _Builder, name: str) -> bool:
    """``_paint`` for an element, and the refusals that depend on the elements around it.

    Whether the element paints a background.

    As T4: a shifted box is painted over the text around it, so a background on or inside one is
    refused; and a shift is bounded only on its own, so one inside another shift or inside a
    superscript, a subscript or a ``vertical-align`` is refused (five nested 6pt shifts move text
    30pt), and so is one of a point or more around them. One under a point may hold a
    superscript (Imatinib Teva's 5.1 writes "m" and a raised "2" in a 0.5pt shift).
    """
    painted = _paint(style, builder)
    _font(style, builder, name)
    declared = dict(_importance_ordered(style))
    shifted = "position" in declared
    # Inline text raised or lowered (``vertical-align`` on a table part aligns it in its row).
    aligned = name in ("sup", "sub") or (
        name in _INLINE and declared.get("vertical-align", "baseline") != "baseline"
    )
    if shifted and (builder.shifted or builder.raised):
        raise _RefusedError("unsupported-style", f"{name}: a shift inside a shifted or raised text")
    if aligned and builder.moved:
        raise _RefusedError("unsupported-style", f"{name}: a raised text inside a shifted one")
    builder.shifted = builder.shifted or shifted
    # A shift under a point may hold a superscript (see the docstring for T's own bound).
    builder.moved = builder.moved or bool(shifted and _shift(name, style))
    builder.raised = builder.raised or aligned
    if painted and builder.shifted:
        raise _RefusedError("unsupported-style", f"{name}: a background on a shifted element")
    return painted


def _faint(colour: str | None, backdrop: str | None) -> bool:
    """Whether a colour cannot be told from the background under it (None: black, the page)."""
    if colour == "transparent":
        return True
    under = _rgb(backdrop, (0xFF, 0xFF, 0xFF))
    return _contrast(_rgb(colour, (0, 0, 0)), under) < _FAINT_CONTRAST


def _colour_kinds(builder: _Builder) -> set[str]:
    """The background under the text; faint where its colour cannot be told from it, else that.

    The background nearest the text is the one drawn under it.
    """
    backdrop = builder.backdrop
    kinds: set[str] = set()
    if backdrop is not None and backdrop not in _WHITE and not _light(backdrop):
        kinds.add(f"shading-{backdrop}")
    if _faint(builder.colour, backdrop):
        return kinds | {_FAINT_COLOUR}
    colour = builder.colour
    if colour is None or colour in _BLACK or _dark(colour):
        return kinds
    return kinds | {f"color-{colour}"}


# A browser's own style for the elements read (its default style sheet): ``b`` and ``strong``
# are bolder than their parent, ``th`` bold, ``em`` and ``i`` italic.
_DEFAULT_WEIGHT: Final = {
    "b": "bolder",
    "strong": "bolder",
    "th": "bold",
    **dict.fromkeys(_HEADINGS, "bold"),
}
_DEFAULT_ITALIC: Final = frozenset({"em", "i"})
# The colour a browser draws an unvisited link in (HTML's rendering section; Chrome's #0000ee).
_LINK_COLOUR: Final = "#0000ee"
# A weight a browser draws bold: 600 and above (CSS Fonts 4, font matching; a face synthesised).
_BOLD_WEIGHT: Final = 600


def _bolder(weight: int) -> int:
    """CSS Fonts 4's ``bolder``, from the parent's weight."""
    return 400 if weight < 350 else 700 if weight < 550 else 900


def _lighter(weight: int) -> int:
    """CSS Fonts 4's ``lighter``, from the parent's weight."""
    return weight if weight < 100 else 100 if weight < 550 else 400 if weight < 750 else 700


def _font(style: str, builder: _Builder, name: str) -> None:
    """Carry the element's font weight and slant down the walk, as a browser computes them.

    The browser's style for the element first, then the style attribute's declarations in the
    order a browser applies them (the last wins, ``!important`` ones last). A value the reader
    does not know is refused, since a browser would drop it and keep the one before.
    """
    parent = builder.weight
    declarations = [("font-weight", _DEFAULT_WEIGHT[name])] if name in _DEFAULT_WEIGHT else []
    if name in _DEFAULT_ITALIC:
        declarations.append(("font-style", "italic"))
    if name in ("sup", "sub"):
        # The browser's style sheet: font-size: smaller, the parent's size over 1.2.
        builder.size = builder.size / 1.2
    declarations += [
        (n, v)
        for n, v in _importance_ordered(style)
        if n in ("font-weight", "font-style", "font-size")
    ]
    for property_name, value in declarations:
        if property_name == "font-size":
            points = _points(value)
            if points is None:  # pragma: no cover - refused by _style first
                raise _RefusedError("unsupported-style", f"font-size: {value}")
            builder.size = points
        elif property_name == "font-weight":
            if value == "normal":
                builder.weight = 400
            elif value == "bold":
                builder.weight = 700
            elif value == "bolder":
                builder.weight = _bolder(parent)
            elif value == "lighter":
                builder.weight = _lighter(parent)
            elif re.fullmatch(r"[0-9]{1,4}", value) and 1 <= int(value) <= 1000:
                if 500 < int(value) < _BOLD_WEIGHT:
                    # Drawn bold where the family's next face is bold (a 700 face for 550):
                    # the face Chrome picks is not on record.
                    raise _RefusedError("unsupported-style", f"font-weight: {value}")
                builder.weight = int(value)
            else:
                raise _RefusedError("unsupported-style", f"font-weight: {value}")
        elif value in ("normal", "italic", "oblique"):
            builder.italic = value != "normal"
        else:
            raise _RefusedError("unsupported-style", f"font-style: {value}")


def _font_kinds(builder: _Builder) -> set[str]:
    """Bold where the weight is drawn bold, italic where the text slants, faint where tiny.

    A size under two points is faint whatever its colour (and keeps its colour mark).
    """
    kinds = {"bold"} if builder.weight >= _BOLD_WEIGHT else set()
    if builder.size < _TINY_POINTS:
        kinds.add("faint")
    return kinds | {"italic"} if builder.italic else kinds


_TINY_POINTS: Final = 2.0


# --- paragraphs -----------------------------------------------------------------------------


# A function of the character and the interpreter's Unicode data alone, asked once per character.
@functools.cache
def _check_character(character: str) -> None:
    """Refuse a character a browser does not show as itself (or the reader's U+FFFC)."""
    if character == OBJECT:
        raise _RefusedError("reserved-character", "U+FFFC stands for a picture")
    if character == _SOFT_HYPHEN:
        # Where the line may break: kept in the text, as a browser keeps it (and as the Word
        # reader reads ``w:softHyphen``).
        return
    category = unicodedata.category(character)
    if category in ("Cf", "Cc") or is_default_ignorable(ord(character)):
        # Zero-width characters and bidirectional controls: a browser hides them or reorders the
        # text around them, and so any code point Unicode says to ignore (a variation selector,
        # a Hangul filler). A control (U+007F, a C1 control; XML admits no other) it draws as a
        # blank or a box.
        raise _RefusedError("format-character", f"U+{ord(character):04X}")
    if category == "Co":
        # What a private-use code point shows is the font's choice: a Symbol font's U+F0B3 is
        # drawn "≥", another font draws a box.
        raise _RefusedError("private-use-character", f"U+{ord(character):04X}")
    if category == "Cn":
        raise _RefusedError("unassigned-character", f"U+{ord(character):04X}")


_PaintState = tuple[
    str | None, str | None, bool, bool, bool, int, bool, float, tuple[str | None, ...], float | None
]


@dataclass
class _Builder:
    paragraphs: list[Paragraph] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    kinds: list[frozenset[str]] = field(default_factory=list)
    pending: frozenset[str] | None = None
    table: tuple[int, int, int] | None = None
    numbering: Numbering | None = None
    # The list the walk is in (its kind, marker format and next number), and the marker of the
    # list item whose first line is yet to come.
    list_state: list[Any] | None = None
    label: str | None = None
    # The element being walked is a child of a ul or ol.
    in_list: bool = False
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
    open_h: bool = False
    # The element being walked is inside an inline element (``_INLINE``).
    in_inline: bool = False
    cell: Any = None
    # The text's colour and the background painted under it, in ``_colour``'s spelling (None:
    # black text, the white page), and whether an element above is shifted (``_shift``) or
    # raised or lowered otherwise (``sup``, ``sub``, ``vertical-align``).
    colour: str | None = None
    backdrop: str | None = None
    shifted: bool = False
    moved: bool = False
    raised: bool = False
    # The text's font weight and whether it is italic, as a browser computes them.
    weight: int = 400
    italic: bool = False
    # The text's size in points, as a browser computes it (16px, 12pt, by default).
    size: float = 12.0
    # The colours of the decoration lines (underline, line-through) the text inherits.
    decorations: tuple[str | None, ...] = ()
    # The left edge (as ``left``) of the block box that paints the background under the text;
    # None where the page or an inline element paints it.
    backdrop_left: float | None = None

    def paint_state(self) -> _PaintState:
        """What an element's colour, background, shift and font set, to restore after it."""
        return (
            self.colour,
            self.backdrop,
            self.shifted,
            self.moved,
            self.raised,
            self.weight,
            self.italic,
            self.size,
            self.decorations,
            self.backdrop_left,
        )

    def restore_paint(self, state: _PaintState) -> None:
        (
            self.colour,
            self.backdrop,
            self.shifted,
            self.moved,
            self.raised,
            self.weight,
            self.italic,
            self.size,
            self.decorations,
            self.backdrop_left,
        ) = state

    def text(self, text: str, marks: frozenset[str]) -> None:
        for character in text:
            if character in _COLLAPSIBLE:
                if self.characters and self.characters[-1] != "\n" and self.pending is None:
                    self.pending = marks
                continue
            _check_character(character)
            self._emit(character, marks)

    def _emit(self, character: str, marks: frozenset[str]) -> None:
        if self.pending is not None:
            # A run of whitespace is drawn as its first character (CSS Text 3, 4.1.1): the
            # space carries that character's marks.
            self.characters.append(" ")
            self.kinds.append(self.pending)
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
            numbering = self.numbering
            if numbering is not None and self.label is not None:
                # The item's marker stands before its first line, followed by a space.
                numbering = replace(numbering, text=self.label, suffix="space")
                self.label = None
            self.paragraphs.append(
                Paragraph(
                    text=text,
                    style=None,
                    numbering=numbering,
                    table=self.table,
                    marks=_marks(self.kinds),
                )
            )
        self.characters = []
        self.kinds = []


def _marks(kinds: list[frozenset[str]]) -> tuple[Mark, ...]:
    kinds = [frozenset("faint" if k == _FAINT_COLOUR else k for k in each) for each in kinds]
    out: list[Mark] = []
    started: dict[str, int] = {}  # each kind open at this character, from where
    previous: frozenset[str] | None = None
    for index, each in enumerate([*kinds, frozenset()]):
        if each == previous:
            continue  # what is open is already ``each``
        previous = each
        for kind in [kind for kind in started if kind not in each]:
            out.append(Mark(started.pop(kind), index, kind))
        for kind in each:
            started.setdefault(kind, index)
    return tuple(sorted(out, key=lambda m: (m.start, m.end, m.kind)))


def _local(element: ET.Element) -> str:
    namespace, _, name = element.tag[1:].partition("}")
    if namespace != XHTML:
        raise _RefusedError("malformed-xhtml", f"element {element.tag!r} outside XHTML")
    return name


# The elements a height is layout on: table parts, and a picture, whose size it sets.
_SIZED: Final = frozenset({"table", "thead", "tbody", "tfoot", "tr", "td", "th", "img"})
# The elements a width is layout on: a table and its cells grow to hold their text; a picture.
_WIDE: Final = frozenset({"table", "td", "th", "img"})


def _length_points(value: str) -> float | None:
    """A CSS length in points, or None for anything else (a percentage, a keyword)."""
    match = re.fullmatch(r"(-?)([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(pt|px|pc|in|cm|mm|em)?", value)
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
    """Whether a length token may be other than zero.

    Only a length the reader parses as zero is certainly zero; any other token may not be, a
    unit the reader cannot place (``rem``, ``ch``, ``calc()``) included.
    """
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
        elif key == "width" and name not in _WIDE and value != "auto":
            # A narrower box paints its background narrower than the text that overflows it.
            raise _RefusedError("unsupported-style", f"{name} {key}: {value}")
        elif key == "line-height" and value != "normal":
            relative = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(%|em)?", value)
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
    _style(style)
    if name in _INLINE and dict(_importance_ordered(style)).get("vertical-align") in (
        "top",
        "middle",
        "bottom",
    ):
        # Raised or lowered against the line, with no mark: "10" and a raised "9" read "109".
        raise _RefusedError("unsupported-style", f"{name}: vertical-align against the line")
    return _shift(name, style)


# How deep elements may nest (the fidelity scanner allows 32 below the root), and how far left of
# its container's start text may be drawn: the pinned labels never go below 0 outside a table
# cell or below -9pt inside one, and text further left is off the page or over what lies there.
_MAX_NESTING: Final = 128
_OFF_PAGE_BOUND_POINTS: Final = -12.0


def _offset_points(value: str) -> float:
    """A margin or indent in points, an em counted at the largest font the reader allows (14pt).

    ``_style`` has refused any unit it cannot place.
    """
    match = re.fullmatch(r"(-?)([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(pt|px|pc|in|cm|mm|em)", value.strip())
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
    saved_open = builder.open_p, builder.open_li, builder.open_a, builder.open_h, builder.in_inline
    saved_paint = builder.paint_state()
    try:
        _walk_element(element, builder, marks, depth)
    finally:
        builder.nesting -= 1
        builder.left, builder.indent = saved_left, saved_indent
        (builder.open_p, builder.open_li, builder.open_a, builder.open_h, builder.in_inline) = (
            saved_open
        )
        builder.restore_paint(saved_paint)


def _repaints(builder: _Builder, outside: str | None) -> bool:
    """Whether the background now under the text differs from ``outside``, the one around it.

    White on the white page (or on white) is the same paint: text beyond it lies on the same.
    """
    page = (None, "white", "#ffffff")
    return builder.backdrop != outside and not (builder.backdrop in page and outside in page)


def _enter_block(name: str, style: str, builder: _Builder, painted: bool) -> None:
    """Carry the block's left offset down; refuse text drawn left of its container's start.

    A table cell's content starts at the cell, so a cell starts again from zero, or from the
    table's own offset when that is negative (a block that overflows its cell is a stated
    residual). Each declaration alone is also bounded by an inch
    (``_on_page``); here the sum is: nested margins, and an indent inherited from a parent, add up.
    Text left of the block box that paints the background under it (``painted``, a background
    other than the one around it, or one around it; in a cell, the cell's start) is drawn on
    what lies outside, and refuses.
    """
    if name in ("td", "th"):
        builder.left, builder.indent = min(0.0, builder.left), builder.part_indent
        if builder.backdrop_left is not None:
            builder.backdrop_left = builder.left
    margin, indent = _left_offsets(style)
    # A cell's margin does not apply (a table part never reaches here but through its cell).
    if name not in ("td", "th"):
        builder.left += margin
    if indent is not None:
        builder.indent = indent
    if painted:
        builder.backdrop_left = builder.left
    first_line = builder.left + min(0.0, builder.indent or 0.0)
    if min(builder.left, first_line) < _OFF_PAGE_BOUND_POINTS:
        raise _RefusedError("unsupported-style", f"{name} drawn left of its container's start")
    if builder.backdrop_left is not None and min(builder.left, first_line) < builder.backdrop_left:
        raise _RefusedError("unsupported-style", f"{name} drawn left of its background")


def _walk_element(
    element: ET.Element, builder: _Builder, marks: frozenset[str], depth: int
) -> None:
    if builder.nesting > _MAX_NESTING:
        raise _RefusedError("malformed-xhtml", f"elements nested deeper than {_MAX_NESTING}")
    name = _local(element)
    in_list, builder.in_list = builder.in_list, False
    if name in ("ins", "del"):
        raise _RefusedError("unsupported-element", name)
    if name not in _BLOCKS and name not in _INLINE and name not in ("img", "br"):
        raise _RefusedError("unsupported-element", name)
    if name in ("td", "th", "tr", "thead", "tbody") and element is not builder.cell:
        raise _RefusedError(
            "malformed-xhtml", f"{name} outside a table, which an HTML parser drops"
        )
    if builder.open_p and name in ("div", "p", "ul", "ol", "table", "hr", "li", *_HEADINGS):
        raise _RefusedError("malformed-xhtml", f"{name} in a p, which an HTML parser closes")
    if name in _HEADINGS and not any(
        n == "font-size" for n, _ in _declarations(element.get("style", ""))
    ):
        # A browser draws a heading larger (h1 at twice the text's size), which the reader's
        # bounds on line height and font size do not hold; a heading must give its own size.
        raise _RefusedError("unsupported-style", f"{name} without a font size")
    if name in _HEADINGS and builder.open_h:
        raise _RefusedError("malformed-xhtml", f"{name} in a heading, which an HTML parser closes")
    if name == "li" and builder.open_li:
        raise _RefusedError("malformed-xhtml", "li in an li, which an HTML parser closes")
    if name == "a" and builder.open_a:
        raise _RefusedError("malformed-xhtml", "a in an a, which an HTML parser closes")
    if name == "li" and not in_list:
        # Not in a list, a browser draws it with the marker of whatever list is around it.
        raise _RefusedError("unsupported-element", "li outside a ul or ol")
    if name == "li" and builder.label is not None:
        raise _RefusedError("unsupported-element", "a list item that begins with a list item")
    if name in ("td", "th"):
        builder.open_p = builder.open_li = builder.open_a = builder.open_h = False
    elif name == "p":
        builder.open_p = True
    elif name in ("ul", "ol"):
        builder.open_li = False
    elif name == "li":
        builder.open_li = True
    elif name == "a":
        builder.open_a = True
    elif name in _HEADINGS:
        builder.open_h = True
    # The colour marks are the parent's; this element's are judged again below.
    inherited = {
        k
        for k in marks
        if k not in (_FAINT_COLOUR, "faint", "bold", "italic")
        and not k.startswith(("color-", "shading-"))
    }
    kinds = inherited | _check_attributes(element, name)
    declared_colour = dict(_importance_ordered(element.get("style", ""))).get("color")
    if (
        name == "a"
        and element.get("href") is not None
        and (declared_colour not in ("inherit", "currentcolor"))
    ):
        # A browser draws a link in its link colour (blue) unless the link's own style says
        # otherwise; the colour of the text around it is not inherited. ``inherit`` and
        # ``currentcolor`` keep the colour around it, before a background takes it.
        builder.colour = _LINK_COLOUR
    outside = builder.backdrop
    painted = _paint_element(element.get("style", ""), builder, name) and _repaints(
        builder, outside
    )
    if name == "li" and (_faint(builder.colour, outside) or builder.size < _TINY_POINTS):
        # The marker takes the item's colour and size, outside its box (on what lies there).
        raise _RefusedError("unsupported-style", "a list marker drawn faint")
    kinds |= _colour_kinds(builder) | _font_kinds(builder)
    own_lines = _own_lines(name, element)
    _decorate(builder, own_lines)
    kinds |= own_lines
    if name in _INLINE:
        kinds |= _inline_borders(element.get("style", ""), builder)
    here = frozenset(kinds)
    if name in _BLOCKS:
        _enter_block(name, element.get("style", ""), builder, painted)
        if builder.in_inline:
            # A block breaks the inline around it: its background, raise, shift and borders
            # do not reach the block as the reader carries them down.
            raise _RefusedError("unsupported-element", f"{name} inside an inline element")
    elif name in _INLINE:
        builder.in_inline = True
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
    saved_list = builder.list_state
    if name == "li":
        builder.numbering = Numbering(num_id=builder.list_kind, level=depth)
        builder.label = _marker(builder)
    saved_kind = builder.list_kind
    if name in ("ul", "ol"):
        if builder.label is not None:
            # Its marker would stand beside the nested list's first line, with that one's.
            raise _RefusedError("unsupported-element", "a list item that begins with a list")
        builder.list_kind = 1 if name == "ul" else 2
        builder.list_state = _list_state(element, name, depth)
    builder.text(element.text or "", here)
    for child in element:
        builder.in_list = name in ("ul", "ol")
        _walk(child, builder, here, depth + (name in ("ul", "ol")))
    if block:
        builder.flush()
    if name == "li" and builder.label is not None:
        # A browser draws the marker all the same, beside nothing the reader reads.
        raise _RefusedError("unsupported-element", "a list item without text")
    builder.numbering = saved
    builder.list_state = saved_list
    builder.list_kind = saved_kind
    builder.text(element.tail or "", marks)


# The bullets a browser draws by its default style sheet: a list nested in no list, in one, in
# two or more (CSS Lists 3, the HTML rendering section); as Chrome draws them.
_BULLETS: Final = ("\u2022", "\u25e6", "\u25a0")
_UL_TYPES: Final = {"disc": 0, "circle": 1, "square": 2}
_OL_TYPES: Final = frozenset({"1", "a", "A", "i", "I"})


def _list_state(element: ET.Element, name: str, depth: int) -> list[Any]:
    """How a list's markers are drawn: [kind, format, next number] from its attributes.

    ``ol``: ``type`` (``1``, ``a``, ``A``, ``i``, ``I``) and ``start`` (an integer); ``ul``:
    ``type`` (``disc``, ``circle``, ``square``, in any case), else the bullet for its depth. Any
    other value is refused: a browser would read it by rules the reader does not follow.
    """
    kind = element.get("type")
    if name == "ul":
        if kind is None:
            return ["ul", _BULLETS[min(depth, 2)], 0]
        if kind.lower() not in _UL_TYPES:
            raise _RefusedError("unsupported-attribute", f"ul@type {kind!r}")
        return ["ul", _BULLETS[_UL_TYPES[kind.lower()]], 0]
    if kind is not None and kind not in _OL_TYPES:
        raise _RefusedError("unsupported-attribute", f"ol@type {kind!r}")
    start = element.get("start", "1")
    if not re.fullmatch(r"-?[0-9]{1,9}", start):
        raise _RefusedError("unsupported-attribute", f"ol@start {start!r}")
    return ["ol", kind or "1", int(start)]


def _marker(builder: _Builder) -> str:
    """The marker a browser draws for the next item of the list the walk is in."""
    if builder.list_state is None:  # pragma: no cover - an li is walked only inside a list
        raise _RefusedError("malformed-xhtml", "li outside a list")
    kind, fmt, number = builder.list_state
    if kind == "ul":
        return str(fmt)
    builder.list_state[2] = number + 1
    return _ordinal(int(number), str(fmt)) + "."


def _ordinal(number: int, fmt: str) -> str:
    """``number`` in a list's format, as CSS counter styles write it.

    Letters are alphabetic (a ... z, aa, ab ...) from 1; roman numerals are 1 to 3999; outside
    its range a style falls back to decimal.
    """
    if fmt in ("a", "A") and number >= 1:
        letters = ""
        while number:
            number, digit = divmod(number - 1, 26)
            letters = chr(ord("a") + digit) + letters
        return letters if fmt == "a" else letters.upper()
    if fmt in ("i", "I") and 1 <= number <= 3999:
        numerals = ""
        for value, symbol in (
            (1000, "m"),
            (900, "cm"),
            (500, "d"),
            (400, "cd"),
            (100, "c"),
            (90, "xc"),
            (50, "l"),
            (40, "xl"),
            (10, "x"),
            (9, "ix"),
            (5, "v"),
            (4, "iv"),
            (1, "i"),
        ):
            count, number = divmod(number, value)
            numerals += symbol * count
        return numerals if fmt == "i" else numerals.upper()
    return str(number)


# What an element draws by the browser's default style sheet, before its own style: a raised or
# lowered line, and a decoration line.
_DEFAULT_ALIGN: Final = {"sup": "super", "sub": "sub"}


def _own_lines(name: str, element: ET.Element) -> set[str]:
    """The superscript, subscript, underline and strike an element draws itself.

    Its own ``vertical-align`` and ``text-decoration`` (the last declared, ``!important`` last)
    replace what the browser's style sheet gives the element (``sup`` raised, ``sub`` lowered,
    ``u`` and a link underlined, ``s`` and ``strike`` struck): a ``sup`` styled
    ``vertical-align: sub`` is lowered only, a ``u`` styled ``line-through`` struck only.
    Decorations then reach everything inside (``inherited``), which cannot take them away;
    ``vertical-align`` raises inline text only.
    """
    declared = dict(_importance_ordered(element.get("style", "")))
    kinds: set[str] = set()
    align = declared.get("vertical-align", _DEFAULT_ALIGN.get(name, "baseline"))
    if name in _INLINE and align in ("super", "sub"):
        kinds.add("superscript" if align == "super" else "subscript")
    if name == "u" or (name == "a" and element.get("href") is not None):
        default = "underline"
    elif name in ("s", "strike"):
        default = "line-through"
    else:
        default = "none"
    words = set(declared.get("text-decoration", default).split())
    if "underline" in words:
        kinds.add("underline")
    if "line-through" in words:
        kinds.add("strike")
    return kinds


def _decorate(builder: _Builder, own: set[str]) -> None:
    """Carry the element's own decoration lines down; refuse one that cannot be seen.

    A line is drawn in the colour of the element that declares it, across the text of everything
    inside, over each background there (``_faint``, as for text).
    """
    if own & {"underline", "strike"}:
        builder.decorations = (*builder.decorations, builder.colour)
    if any(_faint(colour, builder.backdrop) for colour in builder.decorations):
        raise _RefusedError("unsupported-style", "a decoration line in a colour not seen")


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
        part_marks = (
            _check_attributes(part, part_name) | _own_lines(part_name, part)
            if part_name != "tr"
            else set()
        )
        _, own = _left_offsets(part.get("style", ""))
        part_indent = own if own is not None else builder.indent
        # A row group's and a row's colour and background reach their cells' text.
        table_paint = builder.paint_state()
        if part_name != "tr":
            outside = builder.backdrop
            painted = _paint_element(part.get("style", ""), builder, part_name)
            if painted and _repaints(builder, outside):
                builder.backdrop_left = 0.0  # bounded at each cell's start (``_enter_block``)
            _decorate(builder, _own_lines(part_name, part))
        part_paint = builder.paint_state()
        for row in rows:
            if _local(row) != "tr":
                raise _RefusedError("unsupported-element", f"{_local(row)} in a table body")
            row_marks = frozenset(
                set(marks) | part_marks | _check_attributes(row, "tr") | _own_lines("tr", row)
            )
            builder.restore_paint(part_paint)
            outside = builder.backdrop
            painted = _paint_element(row.get("style", ""), builder, "tr")
            if painted and _repaints(builder, outside):
                builder.backdrop_left = 0.0  # bounded at each cell's start (``_enter_block``)
            _decorate(builder, _own_lines("tr", row))
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
        builder.restore_paint(table_paint)
    builder.table = outer
    builder.part_indent = saved_part


_HTML_OTHERWISE = re.compile(
    r"<\?|<!--|xmlns:|</br\b|&#0*(12[89]|1[3-5][0-9]);|&#[xX]0*[89][0-9a-fA-F];"
)
# A run a tag name cannot leave, and what may follow a name up to "/>" (quoted attributes).
_NAME_RUN = re.compile(r"[^\s/>]+")
_ATTRIBUTE = re.compile(r"\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*')")
_CLOSING = re.compile(r"\s*/>")


def _self_closing_tail(div: str, at: int, known: dict[int, bool]) -> bool:
    """Whether quoted attributes from ``at`` run on to "/>", each place's answer kept in ``known``.

    An attribute matches one way only, and "/>" never follows where one does (a name cannot
    begin with "/"), so the attributes from any place lead to one end, which answers for every
    place on the way. Keeping the answers scans each attribute once, where scanning the tail
    afresh from each "<" in the attribute values ahead of it took quadratic time.
    """
    passed: list[int] = []
    while at not in known:
        attribute = _ATTRIBUTE.match(div, at)
        if attribute is None:
            known[at] = _CLOSING.match(div, at) is not None
            break
        passed.append(at)
        at = attribute.end()
    for place in passed:
        known[place] = known[at]
    return known[at]


def _html_otherwise(div: str) -> bool:
    """Markup an HTML parser reads otherwise: ``_HTML_OTHERWISE``, or a self-closing element.

    A self-closing element other than ``br``, ``hr`` and ``img``: "<", a letter, a name up to
    whitespace, "/" or ">", quoted attributes, "/>". Every "<" in one run of name characters
    has its name end where the run ends, so the attributes are matched once per run, not once
    per "<" (a regular expression scanned "<a<a<a..." in quadratic time).
    """
    if _HTML_OTHERWISE.search(div):
        return True
    known: dict[int, bool] = {}
    for run in _NAME_RUN.finditer(div):
        text = run.group()
        if "<" not in text:
            continue
        names = (
            text[at + 1 :] if len(text) - at <= 4 else None
            for at in range(len(text) - 1)
            if text[at] == "<" and text[at + 1].isascii() and text[at + 1].isalpha()
        )
        if any(name not in ("br", "hr", "img") for name in names) and (
            _self_closing_tail(div, run.end(), known)
        ):
            return True
    return False


_BARE_LESS_THAN = re.compile(r"<(?![A-Za-z/!?])")


def _no_stray_text(text: str | None) -> None:
    """Text between table parts: a browser moves it out of the table; the reader refuses."""
    if text is not None and text.strip(_COLLAPSIBLE):
        raise _RefusedError("unsupported-element", "text between the parts of a table")


def read_div(div: str) -> tuple[tuple[Paragraph, ...], SectionRefusal | None, tuple[str, ...]]:
    """The paragraphs of one section's XHTML div, or a refusal.

    With them, notes on the defects the reader read through (``Section.notes``).
    """
    lowered = div.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return (), SectionRefusal("malformed-xhtml", "a DTD"), ()
    if "<![cdata[" in lowered:
        # An XML parser reads CDATA as text; an HTML parser reads it as a comment.
        return (), SectionRefusal("malformed-xhtml", "a CDATA section"), ()
    if _html_otherwise(div):
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


def _title(title: str, where: str) -> str:
    """A title as written, or ``EpiRefusedError`` for a character a div would refuse.

    A title is served whatever the section's refusal, so a character the reader refuses in a
    div (other than whitespace) refuses the document, with the code it has there.
    """
    for character in title:
        if character not in _COLLAPSIBLE:
            try:
                _check_character(character)
            except _RefusedError as refused:
                code, detail = refused.refusal.code, refused.refusal.detail
                raise EpiRefusedError(code, f"{detail} in {where}") from None
    return title


def _section(raw: dict[str, Any]) -> Section:
    title = raw.get("title")
    if not isinstance(title, str):
        raise EpiRefusedError("invalid-bundle", "a section without a title")
    _title(title, "a section title")
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


# How deep a Bundle's arrays and objects may nest (the pinned ePIs reach 17): the JSON parser
# recurses, and how deep it can go depends on the thread's stack.
_JSON_DEPTH: Final = 100
# A string, or one never closed, which runs to the end: tried from each quote in turn, a string
# never closed was scanned to the end from every one, in quadratic time. Not JSON either way.
_JSON_STRING = re.compile(r'"[^"\\]*+(?:\\.[^"\\]*+)*+(?:"|\\?\Z)', re.S)


def _one_reading(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({name for name, _ in pairs}) != len(pairs):
        # Two values for one name: JSON leaves which one counts undefined, FHIR forbids it.
        raise ValueError("a name repeated in an object")
    return dict(pairs)


def _not_a_number(constant: str) -> None:
    raise ValueError(f"{constant} is not JSON")


def load_bundle(data: bytes) -> dict[str, Any]:
    """The document Bundle in ``data``, parsed strictly, or ``EpiRefusedError``."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EpiRefusedError("invalid-bundle", "not UTF-8 JSON Python can read") from error
    depth = 0
    for bracket in re.sub(r"[^\[\]{}]", "", _JSON_STRING.sub("", text)):
        depth += 1 if bracket in "[{" else -1
        if depth > _JSON_DEPTH:
            raise EpiRefusedError("invalid-bundle", "nested too deeply to read")
    try:
        bundle = json.loads(text, object_pairs_hook=_one_reading, parse_constant=_not_a_number)
    except ValueError as error:
        # Not JSON, a name repeated in an object, NaN or Infinity, or an integer past Python's
        # digit limit.
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
    return bundle


def composition_of(bundle: dict[str, Any]) -> dict[str, Any]:
    """The resource with sections: the only one, and the first entry's (FHIR's bdl-11)."""
    entries = bundle.get("entry", [])
    compositions = [
        entry["resource"]
        for entry in entries
        if isinstance(entry.get("resource"), dict) and "section" in entry["resource"]
    ]
    if len(compositions) != 1:
        raise EpiRefusedError("invalid-bundle", f"{len(compositions)} resources with sections")
    if entries[0].get("resource") is not compositions[0]:
        raise EpiRefusedError("invalid-bundle", "the resource with sections is not the first")
    composition: dict[str, Any] = compositions[0]
    return composition


def _read_epi(data: bytes) -> Document:
    composition = composition_of(load_bundle(data))
    quirks: list[str] = []
    kind = composition.get("resourceType")
    if kind == 0:
        # The EMA ePI API (2024 pilot data) serialises resourceType, language and status as the
        # number 0; the Bundle is otherwise a document with one Composition.
        quirks.append("Composition.resourceType is 0, not 'Composition'")
    elif kind != "Composition":
        raise EpiRefusedError("invalid-bundle", f"the resource with sections is a {kind!r}")
    codings = composition.get("type", {}).get("coding", [])
    title = composition.get("title", "")
    date = composition.get("date")
    document_type = codings[0].get("code") if codings else None
    for name, value in (("title", title), ("date", date), ("type", document_type)):
        if value is not None and not isinstance(value, str):
            raise EpiRefusedError("invalid-bundle", f"a Composition {name} that is not a string")
    _title(title, "the Composition's title")
    return Document(
        title=title,
        date=date,
        document_type=document_type,
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
