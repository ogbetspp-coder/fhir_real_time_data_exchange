r"""Fail-closed XHTML narrative scanner, ported from ``src/fidelity/xhtml.ts``.

The executable form of ``docs/fidelity-normalization.md`` section 5. Not a general HTML parser:
it accepts only the closed lists below so that no element or attribute can hide, carry, or
reorder narrative text, and rejects everything else.

Four Python-specific care points. Every one of them is a place where the two regex dialects
agree on the *syntax* and disagree on the *meaning*, which is exactly the kind of divergence a
golden vector the TypeScript author wrote will never catch:

* JavaScript's ``\s`` and Python's ``\s`` are different sets. JavaScript includes U+FEFF and
  excludes U+001C-U+001F and U+0085; Python is the reverse. Neither is used: since
  fidelity-norm/2.0.0 whitespace inside a tag is ``[\t\n\r ]`` on both sides (``_WS`` below),
  because an HTML parser reads any other code point as part of the tag name.
* JavaScript's ``\d`` is ASCII ``[0-9]`` unless the ``v``/``u`` flag is combined with a Unicode
  property escape; Python's ``\d`` on a ``str`` pattern matches every Unicode decimal digit,
  so a numeric character reference written with U+FF10-U+FF19 FULLWIDTH DIGIT would be decoded
  here and be a stray ``&`` there. No ``\d``, ``\w`` or ``\b`` appears in this module: the
  classes are written out as ``[0-9]`` and ``[0-9A-Fa-f]``. ``re.ASCII`` is not used as a
  blanket flag, because it would also silently narrow a class someone adds later.
* Python's ``$`` matches before a trailing newline and ``re.match`` is not anchored at the end,
  so ``pattern.match(value)`` against a ``$``-anchored grammar accepts ``"a\n"`` and every
  value with text hidden after the last newline. Whole-value grammars use ``fullmatch`` and
  carry no anchors at all; the scan-position patterns (``END_TAG``, ``START_TAG``, ``ENTITY``)
  use ``match(div, index)``, which is the sticky ``/y`` flag of the TypeScript.
* The TypeScript scans the div by UTF-16 index and Python by code point. An error's offset is a
  code point offset into the div on both sides: the TypeScript converts the index it failed at.
  Error offsets are not part of any vector's expected value (an error vector records its code
  only), so they change no accepted or rejected input, only the number in an exception message.

fidelity-norm/2.0.0 adds three more, about strings rather than regexes. A string decoded from
JSON holds a lone surrogate as a code point Python accepts, and so does ``chr(0xD835)`` for a
character reference: both are checked against section 2 explicitly, the div before the scan and
each reference as it is decoded. JavaScript's ``\p{N}`` is the Unicode general category, read
here from ``unicodedata``. And the scan walks code points natively where the TypeScript has to
step over surrogate pairs, so a supplementary digit inside ``sup`` is one code point on both
sides.

fidelity-norm/3.0.0 (numbered lists, table grids, pictures) adds two. A picture's token is the
SHA-256 hex of its ``src`` as UTF-8, from ``hashlib`` here and ``node:crypto`` there, over the
same code points: an attribute value can hold no character reference, so neither side decodes
anything first. And ``str(-1)`` and JavaScript's ``String(-1)`` both write U+002D, which is the
minus sign section 5 names for a negative list ordinal.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable
from typing import Final

from .normalize import (
    compose_text,
    find_forbidden_character,
    is_default_ignorable,
    is_forbidden,
    is_gap,
)

XHTML_NAMESPACE: Final = "http://www.w3.org/1999/xhtml"

# `pre` is not here: a renderer keeps its whitespace and so draws columns the check cannot see.
BLOCK_ELEMENTS: Final = frozenset(
    {
        "div",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "caption",
        "blockquote",
        "dl",
        "dt",
        "dd",
        "hr",
    }
)

# `u` and `a` are excluded from 3.0.0: a renderer underlines both (`a` with a target), and an
# underline turns a sign into another, "<" into "≤", ">" into "≥", "+" into "±", and "1" `u`"a"
# into "1ª", which no closed list of code points can bound.
INLINE_ELEMENTS: Final = frozenset(
    {"span", "b", "i", "em", "strong", "sup", "sub", "small", "abbr", "cite", "code", "img"}
)

# `q` is excluded: a renderer draws quotation marks the source may not contain. `ol` is allowed
# because the numbers a renderer draws are emitted as text (below).

# The only elements that may be, and must be, self-closing. An HTML parser ignores the `/` of
# `<sup/>`, so any other element written that way opens around the text that follows it; and a
# `<br>` without `/` is a start tag whose content an XML renderer does not draw.
VOID_ELEMENTS: Final = frozenset({"br", "hr", "img"})

# Table parts and the parents each may have. A renderer moves anything else it finds directly
# inside a table container out of the table, so that is rejected (`table-content`).
TABLE_PART_PARENTS: Final[dict[str, frozenset[str]]] = {
    "caption": frozenset({"table"}),
    "thead": frozenset({"table"}),
    "tbody": frozenset({"table"}),
    "tfoot": frozenset({"table"}),
    "tr": frozenset({"table", "thead", "tbody", "tfoot"}),
    "td": frozenset({"tr"}),
    "th": frozenset({"tr"}),
}
TABLE_CONTAINERS: Final = frozenset({"table", "thead", "tbody", "tfoot", "tr"})
ROW_GROUPS: Final = frozenset({"thead", "tbody", "tfoot"})

# A renderer numbers only the `li` children of a list, and moves nothing out of it; anything else
# directly inside `ol` or `ul` is drawn outside the numbering (`list-content`).
LIST_CONTAINERS: Final = frozenset({"ol", "ul"})

# Reserved code points: the scanner emits them for table grids and pictures, so they never occur
# in narrative text itself.
TABLE_START: Final = "\ufdd0"
TABLE_END: Final = "\ufdd1"
ROW_START: Final = "\ufdd2"
CELL_START: Final = "\ufdd3"
COVERED_LEFT: Final = "\ufdd4"
COVERED_ABOVE: Final = "\ufdd5"
PICTURE: Final = "\ufffc"


def is_grid_marker(code_point: int) -> bool:
    """The grid markers U+FDD0-U+FDD5: structure, not text a reader sees."""
    return 0xFDD0 <= code_point <= 0xFDD5


def has_drawn_text(normalized: str) -> bool:
    """Whether normalised narrative text holds anything a reader sees (section 5).

    A table of empty cells, whose text is only grid markers, and text of only gaps (a thin space, a
    blank glyph, a code point Unicode says to ignore) draw nothing inked: ``empty-narrative``.
    """
    return any(not is_gap(ord(c)) and not is_grid_marker(ord(c)) for c in normalized)


# The slots all tables of one narrative may cover together. A small table can span a large grid
# (`colspan="1000" rowspan="1000"` is a million slots, each a marker in the text), so the grid is
# bounded, and a real table is far inside the bound (`table-size`).
TABLE_SLOT_LIMIT: Final = 50_000


def is_invisible_break(code_point: int) -> bool:
    """A soft hyphen or a zero-width space: a break a renderer may draw that the check never reads.

    At a narrow width "2" U+00AD "10" is drawn "2-" / "10" and "2" U+200B "10" as "2" / "10", where
    the check reads "210", so narrative holds neither (section 2; one-sided, since page text marks
    a hyphenated line end with U+00AD).
    """
    return code_point in (0x00AD, 0x200B)


def _find_invisible_break(text: str) -> int | None:
    for offset, character in enumerate(text):
        if is_invisible_break(ord(character)):
            return offset
    return None


# How deep markup may nest (section 5). An HTML parser stops nesting at 512 open elements and moves
# what follows elsewhere; `small` inside `small` and a heading inside a heading shrink text towards
# illegible; and every indenting container moves text further right, off a narrow page.
MAX_DEPTH: Final = 32
MAX_INDENTS: Final = 6
HEADINGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# The elements a renderer draws smaller than the text around them: at most one open at once, so
# `<h6><small>` (drawn at about 9 px, and 7 px with a `sup`) is refused.
SHRINKING: Final = frozenset({"small", "code", "h5", "h6"})
INDENTING: Final = frozenset({"blockquote", "ul", "ol", "dd"})


def _check_nesting(name: str, stack: list[str], offset: int) -> None:
    if len(stack) > MAX_DEPTH:
        raise XhtmlError("nesting-depth", offset)
    if name in SHRINKING and any(open_name in SHRINKING for open_name in stack):
        raise XhtmlError("nesting-depth", offset)
    if name in HEADINGS and any(open_name in HEADINGS for open_name in stack):
        raise XhtmlError("nesting-depth", offset)
    if name in INDENTING:
        indents = sum(1 for open_name in stack if open_name in INDENTING) + 1
        if indents > MAX_INDENTS:
            raise XhtmlError("nesting-depth", offset)


# The inline elements whose tags split text without emitting anything: a renderer draws the text on
# each side in its own run, so a mark after the tag does not combine with the letter before it.
SPLITTING_INLINE: Final = frozenset(
    {"span", "b", "i", "em", "strong", "sup", "sub", "small", "abbr", "cite", "code"}
)
# How far each side of such a tag is composed to find a composition across it. A combining mark
# right after the tag is refused whatever its distance from the letter, so the window only has to
# catch the Hangul jamo that compose without being marks.
COMPOSE_WINDOW: Final = 64


def _is_mark(character: str) -> bool:
    return unicodedata.category(character)[0] == "M"


class _LoweredHalf:
    """A ``sub`` holding ½: its content's output positions and its first ½'s offset in the div."""

    __slots__ = ("end", "offset", "start")

    def __init__(self, start: int) -> None:
        self.start = start
        self.end = start
        self.offset = -1


# The neighbours of a kept lowered ½ (section 5): the half-life, ``t<sub>½</sub>``, as a word.
_BEFORE_HALF_LIFE: Final = frozenset({"\n", "\t", " ", "("})
_AFTER_HALF: Final = frozenset({"\n", "\t", " ", ")", ".", ",", ";", ":"})


def _check_lowered_halves(
    output: list[str], halves: list[_LoweredHalf], script_pieces: set[int]
) -> None:
    """Keep a lowered ½ only as the half-life, ``t<sub>½</sub>`` with ``t`` starting a word.

    The ``sub``'s whole content is ½, right after a ``t`` that follows a break, a space, ``(`` or
    nothing, and right before a break, a space, ``) . , ; :`` or nothing, every neighbour drawn on
    the line (section 5). Anywhere else it can join a number or an index, and the text cannot say
    which: ``log<sub>2½</sub>`` and ``log<sub>2</sub>½`` both read ``log₂½``, and a letter before
    it can be a number or an operator (``VIII<sub>½</sub>``, ``log<sub>½</sub>``). Nothing is read
    past: each neighbour is the adjacent emitted code point.
    """
    # A code point of the text as (piece, index in the piece), found by stepping over the pieces
    # that emit nothing; a piece emitted inside ``sup`` or ``sub`` holds one code point. The steps
    # of different halves do not overlap, so the rule stays linear without a copy of the text.

    def last(piece: int) -> tuple[str, int, int] | None:
        while piece >= 0 and output[piece] == "":
            piece -= 1
        if piece < 0:
            return None
        return output[piece][-1], piece, len(output[piece]) - 1

    def previous(at: tuple[str, int, int]) -> tuple[str, int, int] | None:
        _, piece, index = at
        if index > 0:
            return output[piece][index - 1], piece, index - 1
        return last(piece - 1)

    def following(piece: int) -> tuple[str, int, int] | None:
        while piece < len(output) and output[piece] == "":
            piece += 1
        if piece >= len(output):
            return None
        return output[piece][0], piece, 0

    def on_line(at: tuple[str, int, int] | None, allowed: frozenset[str]) -> bool:
        return at is None or (at[0] in allowed and at[1] not in script_pieces)

    for half in halves:
        letter = last(half.start - 1)
        if (
            "".join(output[half.start : half.end]) != chr(HALF)
            or letter is None
            or letter[0] != "t"
            or letter[1] in script_pieces
            or not on_line(previous(letter), _BEFORE_HALF_LIFE)
            or not on_line(following(half.end), _AFTER_HALF)
        ):
            raise XhtmlError("unmappable-script", half.offset)


def _point_offsets(output: list[str]) -> Callable[[int], int]:
    """Map output positions, asked in increasing order, to offsets in the joined text."""
    offset = 0
    piece = 0

    def at(position: int) -> int:
        nonlocal offset, piece
        while piece < position:
            offset += len(output[piece])
            piece += 1
        return offset

    return at


def _check_composition(text: str, boundaries: list[int], tags: list[int]) -> None:
    """Refuse a composition across an inline tag.

    The boundaries are offsets in the text, each with the offset in the div of the tag that makes
    it, which an error reports.
    """
    # The first code point at or after each boundary that is not a Default_Ignorable non-mark: a
    # word joiner or a zero-width joiner between the tag and a mark is drawn as nothing, and the
    # mark after it is still drawn apart from the letter before the tag. Boundaries only increase,
    # so one cursor reads each run of ignorables once.
    cursor = 0
    for boundary, tag in zip(boundaries, tags, strict=True):
        cursor = max(cursor, boundary)
        while cursor < len(text) and not (
            _is_mark(text[cursor]) or not is_default_ignorable(ord(text[cursor]))
        ):
            cursor += 1
        if cursor < len(text) and _is_mark(text[cursor]):
            raise XhtmlError("combining-across-markup", tag)
        # A boundary before a code point below U+0300, U+00AD aside, is stable: every such code
        # point is a starter that NFC never composes with what precedes it (canonical combining
        # class 0, NFC_QC=Yes; tests/test_composition_boundary.py checks each one), so the two
        # sides compose the same apart as together. U+00AD breaks that on either side (step 1
        # removes it with a line break that follows it), so a boundary next to one takes the full
        # comparison; it never reaches the text anyway (section 2).
        following = ord(text[boundary]) if boundary < len(text) else None
        preceding = ord(text[boundary - 1]) if boundary > 0 else None
        if following is None or (
            following < 0x0300 and following != 0x00AD and preceding != 0x00AD
        ):
            continue
        before = text[max(0, boundary - COMPOSE_WINDOW) : boundary]
        after = text[boundary : boundary + COMPOSE_WINDOW]
        if compose_text(before + after) != compose_text(before) + compose_text(after):
            raise XhtmlError("combining-across-markup", tag)


def is_reserved(code_point: int) -> bool:
    """U+FFFC and the noncharacters U+FDD0-U+FDEF."""
    return code_point == 0xFFFC or 0xFDD0 <= code_point <= 0xFDEF


def _find_reserved_character(text: str) -> int | None:
    for offset, character in enumerate(text):
        if is_reserved(ord(character)):
            return offset
    return None


NAMED_ENTITIES: Final[dict[str, str]] = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}

# Super- and subscript folding (section 5): the characters that change what a number means are
# folded to their script code points, so a raised 6 after 10 is U+2076 and never equals "106".
SUPERSCRIPT_DIGITS: Final = (
    0x2070,
    0x00B9,
    0x00B2,
    0x00B3,
    0x2074,
    0x2075,
    0x2076,
    0x2077,
    0x2078,
    0x2079,
)
SUBSCRIPT_DIGITS: Final = tuple(range(0x2080, 0x208A))
SUPERSCRIPT_SIGNS: Final = (0x207A, 0x207B, 0x207C, 0x207D, 0x207E)
SUBSCRIPT_SIGNS: Final = (0x208A, 0x208B, 0x208C, 0x208D, 0x208E)
PLUS_SIGNS: Final = (0x002B, 0xFE62, 0xFF0B, 0x2795)
MINUS_SIGNS: Final = (
    0x002D,
    0x2212,
    0x2010,
    0x2011,
    0x2012,
    0x2013,
    0x2014,
    0x2015,
    0x02D7,
    0xFE58,
    0xFE63,
    0xFF0D,
    0x2796,
)


# The script letters of each kind: a subscript letter raised is not a superscript one.
SUPERSCRIPT_LETTERS: Final = (0x2071, 0x207F)
SUBSCRIPT_LETTERS: Final = tuple(range(0x2090, 0x209D))


# Kept unchanged inside ``sub`` from fidelity-norm/3.1.0: U+00BD VULGAR FRACTION ONE HALF and
# U+221E INFINITY, as in ``t<sub>½</sub>`` and ``AUC<sub>(0-∞)</sub>``. Neither has a subscript
# form; raised, ``2<sup>½</sup>`` is a root. ½ is a number, so it is kept only where it cannot
# join a number on either side (_check_lowered_halves); ∞ never joins a number, and loses its
# position as a letter does (section 5's stated residual).
HALF: Final = 0x00BD
KEPT_IN_SUBSCRIPT: Final = (HALF, 0x221E)


class _ScriptRule:
    """Folding table, the element's own script digits and signs, the other script's."""

    __slots__ = ("folding", "foreign", "own")

    def __init__(
        self,
        digits: tuple[int, ...],
        signs: tuple[int, ...],
        foreign: tuple[int, ...],
        kept: tuple[int, ...] = (),
    ) -> None:
        plus, minus, equals, open_, close = signs
        folding = {0x0030 + digit: target for digit, target in enumerate(digits)}
        folding.update(dict.fromkeys(PLUS_SIGNS, plus))
        folding.update(dict.fromkeys(MINUS_SIGNS, minus))
        folding[0x003D] = equals
        folding[0x0028] = open_
        folding[0x0029] = close
        self.folding: dict[int, int] = folding
        self.own: frozenset[int] = frozenset(digits + signs + kept)
        self.foreign: frozenset[int] = frozenset(foreign)


SCRIPT_RULES: Final[dict[str, _ScriptRule]] = {
    "sup": _ScriptRule(
        SUPERSCRIPT_DIGITS,
        SUPERSCRIPT_SIGNS,
        SUBSCRIPT_DIGITS + SUBSCRIPT_SIGNS + SUBSCRIPT_LETTERS,
    ),
    "sub": _ScriptRule(
        SUBSCRIPT_DIGITS,
        SUBSCRIPT_SIGNS,
        SUPERSCRIPT_DIGITS + SUPERSCRIPT_SIGNS + SUPERSCRIPT_LETTERS,
        KEPT_IN_SUBSCRIPT,
    ),
}

# The element's own script digits and signs are kept, and so is ∞ inside ``sub`` (and ½ there,
# as the half-life only: _check_lowered_halves); the other script's digits, signs and letters,
# every other number (general category N), a plus-minus sign, and every other mathematical symbol,
# bracket or dash (general category Sm, Ps, Pe, Pd) have no script form there and reject.
UNMAPPABLE_SIGNS: Final = frozenset({0x00B1, 0x2213})
UNMAPPABLE_CATEGORIES: Final = frozenset({"Sm", "Ps", "Pe", "Pd"})


def script_code_point(element: str, code_point: int) -> int | None:
    r"""What ``sup`` or ``sub`` makes of one code point of text.

    The code point it is folded to, the code point itself when it is kept, or None when it has no
    script form there (``unmappable-script``). General category N is read from ``unicodedata``
    because ``re`` has no ``\p{N}``.
    """
    rule = SCRIPT_RULES[element]
    folded = rule.folding.get(code_point)
    if folded is not None:
        return folded
    category = unicodedata.category(chr(code_point))
    if (
        code_point in UNMAPPABLE_SIGNS
        or code_point in rule.foreign
        or (
            (category[0] == "N" or category in UNMAPPABLE_CATEGORIES) and code_point not in rule.own
        )
    ):
        return None
    return code_point


# Whitespace inside a tag: U+0009, U+000A, U+000D and U+0020, and nothing else. Neither
# language's `\s` is used: an HTML parser reads any other code point (U+00A0, U+3000, U+FEFF)
# as part of the tag name, so `sup` followed by U+00A0 is an unknown element to a renderer and
# must be `malformed-tag` here. See the module docstring.
_WS: Final = r"[\t\n\r ]"

END_TAG: Final = re.compile(rf"</([A-Za-z][A-Za-z0-9]*){_WS}*>")
START_TAG: Final = re.compile(
    rf"<([A-Za-z][A-Za-z0-9]*)"
    rf"((?:{_WS}+[A-Za-z_:][-A-Za-z0-9_:.]*{_WS}*={_WS}*(?:\"[^\"<]*\"|'[^'<]*'))*)"
    rf"{_WS}*(/?)>"
)
ATTRIBUTE: Final = re.compile(
    rf"([A-Za-z_:][-A-Za-z0-9_:.]*){_WS}*={_WS}*(?:\"([^\"<]*)\"|'([^'<]*)')"
)
# `[0-9]`, never `\d`: see the module docstring. The TypeScript `\d` is ASCII; Python's is not.
ENTITY: Final = re.compile(r"&(?:([A-Za-z]+)|#([0-9]{1,7})|#x([0-9A-Fa-f]{1,6}));")
ASCII_LETTER: Final = re.compile(r"[A-Za-z]")

# Attribute values are never compared against the source, so they must not be able to carry
# text: each allowed attribute is restricted to a short token alphabet, and to the one element that
# needs it. Nothing a viewer's stylesheet or script could key on to hide text (`class`, `id`, a
# language tag below the root, a link) is allowed. These are
# whole-value grammars and are applied with `fullmatch`, so they carry no `^`/`$`: Python's `$`
# would also match before a trailing newline, which is text this must not carry.
TOKEN_VALUE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,32}")
LIST_TYPE_VALUE: Final = re.compile(r"[1aAiI]")
LIST_START_VALUE: Final = re.compile(r"0|-?[1-9][0-9]{0,3}")
SPAN_VALUE: Final = re.compile(r"[1-9][0-9]{0,2}|1000")
# The one style allowed (from 3.3.0): the EMA ePI style guide's grey for QRD "not printed" text,
# on a `span` and in exactly this spelling. A background under black text hides nothing.
GREY_STYLE: Final = "background-color: silver;"
# A picture's source is a PNG or JPEG `data:` URI: the picture's own bytes, compared with the
# source through the hash `img` emits. A reference (a path or a URL) is refused: what it draws is
# whatever the viewer's origin serves, or nothing, and neither is bound by the check.
PICTURE_DATA_PREFIXES: Final = ("data:image/png;base64,", "data:image/jpeg;base64,")
# The base64 length of 1 MiB.
PICTURE_DATA_LIMIT: Final = 1_398_104
BASE64_ALPHABET: Final = re.compile(r"[A-Za-z0-9+/]*")


def _structural_break(name: str, cell_depth: int) -> str:
    """U+000A, except that a table cell and everything inside one is on one U+0009 line."""
    return "\t" if name in ("td", "th") or cell_depth > 0 else "\n"


class XhtmlError(ValueError):
    """Section 5 rejection. ``code`` is what a report records; it never carries text."""

    def __init__(self, code: str, offset: int) -> None:
        super().__init__(f"XHTML narrative rejected ({code}) at offset {offset}")
        self.code = code
        self.offset = offset


class _TableState:
    __slots__ = (
        "above",
        "body",
        "caption",
        "covered",
        "cursor",
        "down",
        "foot",
        "head",
        "last",
        "open_span",
        "rows",
        "single",
        "single_columns",
        "widths",
    )

    def __init__(self) -> None:
        self.caption = False
        self.head = False
        self.body = False
        self.foot = False
        self.rows = False
        # The grid, laid out by the HTML table model, and kept sparse so that a row costs only the
        # slots it covers: for each column a cell above still covers, how many rows from the
        # current one it covers.
        self.above: dict[int, int] = {}
        # The current row: the slots covered, the highest of them, the next slot not yet emitted,
        # for each column how many rows below it a cell placed in this row covers, and whether a
        # cell that spans no rows starts in it.
        self.covered: set[int] = set()
        self.last = -1
        self.cursor = 0
        self.down: dict[int, int] = {}
        self.single = False
        # The columns in which a cell that spans no columns starts, over the whole table.
        self.single_columns: set[int] = set()
        # The colspan of the open cell, whose covered-left slots its end tag emits.
        self.open_span = 1
        # Slots covered by every row of this table, or -1 for a row with a hole.
        self.widths: list[int] = []


class _ListState:
    __slots__ = ("next", "style")

    def __init__(self, style: str, start: int) -> None:
        self.style = style
        self.next = start


def _is_ascii_whitespace(character: str) -> bool:
    return character in (" ", "\t", "\n", "\r")


def _is_picture_data(value: str) -> bool:
    """A PNG or JPEG ``data:`` URI, counted and tested directly rather than by one regex.

    The body is non-empty, at most the limit, a multiple of 4 long, the base64 alphabet, and
    ``=`` only as the last one or two code points.
    """
    prefix = next((p for p in PICTURE_DATA_PREFIXES if value.startswith(p)), None)
    if prefix is None:
        return False
    body = value[len(prefix) :]
    if not body or len(body) > PICTURE_DATA_LIMIT or len(body) % 4 != 0:
        return False
    padding = 2 if body.endswith("==") else 1 if body.endswith("=") else 0
    return BASE64_ALPHABET.fullmatch(body[: len(body) - padding]) is not None


def _attribute_allowed(name: str, value: str, element: str, is_root: bool) -> bool:
    # `fullmatch`, never `match`: the TypeScript anchors each grammar at both ends and Python's
    # `$` would let a trailing U+000A (and anything after it) through.
    if name in ("xml:lang", "lang"):
        return is_root and TOKEN_VALUE.fullmatch(value) is not None
    if name == "scope":
        return element == "th" and TOKEN_VALUE.fullmatch(value) is not None
    if name == "type":
        return element == "ol" and LIST_TYPE_VALUE.fullmatch(value) is not None
    if name == "start":
        return element == "ol" and LIST_START_VALUE.fullmatch(value) is not None
    if name in ("colspan", "rowspan"):
        return element in ("td", "th") and SPAN_VALUE.fullmatch(value) is not None
    if name == "src":
        return element == "img" and _is_picture_data(value)
    if name == "style":
        return element == "span" and value == GREY_STYLE
    return False


def _check_attributes(
    element: str, attribute_source: str, is_root: bool, offset: int
) -> dict[str, str]:
    """Checks the attributes in document order and returns their values."""
    saw_namespace = False
    values: dict[str, str] = {}
    for match in ATTRIBUTE.finditer(attribute_source):
        name = match.group(1) or ""
        value = match.group(2) if match.group(2) is not None else (match.group(3) or "")
        if name in values:
            raise XhtmlError("forbidden-attribute", offset)
        values[name] = value
        if name == "xmlns":
            if not is_root or value != XHTML_NAMESPACE:
                raise XhtmlError("forbidden-attribute", offset)
            saw_namespace = True
            continue
        if not _attribute_allowed(name, value, element, is_root):
            raise XhtmlError("forbidden-attribute", offset)
    if is_root and not saw_namespace:
        raise XhtmlError("root-not-div", offset)
    # A picture without a source would be drawn as nothing, or as the broken-image mark.
    if element == "img" and "src" not in values:
        raise XhtmlError("forbidden-attribute", offset)
    return values


def _decode_entity(match: re.Match[str], offset: int) -> int:
    """The decoded code point of a character reference, checked on its own.

    A reference to half a surrogate pair rejects even when the next reference would complete it:
    ``chr(0xD835)`` is a code point Python holds without complaint, so the check is explicit.
    """
    named = match.group(1)
    if named is not None:
        decoded = NAMED_ENTITIES.get(named)
        if decoded is None:
            raise XhtmlError("unknown-entity", offset)
        return ord(decoded)
    decimal = match.group(2)
    code_point = int(decimal, 10) if decimal is not None else int(match.group(3) or "", 16)
    if code_point > 0x10FFFF:
        raise XhtmlError("unknown-entity", offset)
    if is_forbidden(code_point):
        raise XhtmlError("forbidden-character", offset)
    if is_reserved(code_point):
        raise XhtmlError("reserved-character", offset)
    if is_invisible_break(code_point):
        raise XhtmlError("invisible-character", offset)
    return code_point


# The elements a rule (`hr`) may not be drawn in (below).
RULE_BREAKS_FRACTION: Final = frozenset({"td", "th", "caption"})


def _check_parent(name: str, parent: str | None, offset: int) -> None:
    """Nothing but text in `sup`/`sub`; table parts and `li` in their parents; only parts inside."""
    if parent in ("sup", "sub"):
        raise XhtmlError("script-content", offset)
    allowed_parents = LIST_CONTAINERS if name == "li" else TABLE_PART_PARENTS.get(name)
    if allowed_parents is not None:
        if parent is None or parent not in allowed_parents:
            raise XhtmlError("misnested-tag", offset)
        return
    if parent is not None and parent in TABLE_CONTAINERS:
        raise XhtmlError("table-content", offset)
    if parent is not None and parent in LIST_CONTAINERS:
        raise XhtmlError("list-content", offset)


def _enter_table_structure(
    name: str, parent: str | None, state: _TableState | None, offset: int
) -> None:
    """Only the one document order that renders as written is accepted.

    Renderers place table parts by role, not by document position: a caption always renders
    first and sections render head then body then foot, so displayed text order must equal the
    order the source was verified in. Parents are already checked. A table inside an open table
    (in a cell or in a caption) is refused, so the grid text never nests.
    """
    if name == "table":
        if state is not None:
            raise XhtmlError("table-structure", offset)
        return
    if state is None:
        return
    if name == "tr":
        if parent == "table":
            if state.head or state.body or state.foot:
                raise XhtmlError("table-structure", offset)
            state.rows = True
        return
    if name != "caption" and name not in ROW_GROUPS:
        return
    if name == "caption":
        if state.caption or state.head or state.body or state.foot or state.rows:
            raise XhtmlError("table-structure", offset)
        state.caption = True
        return
    if state.rows:
        raise XhtmlError("table-structure", offset)
    if name == "thead" and (state.head or state.body or state.foot):
        raise XhtmlError("table-section-order", offset)
    if name == "tbody" and state.foot:
        raise XhtmlError("table-section-order", offset)
    if name == "tfoot" and state.foot:
        raise XhtmlError("table-section-order", offset)
    if name == "thead":
        state.head = True
    elif name == "tbody":
        state.body = True
    else:
        state.foot = True


def _covered_slot(marker: str) -> str:
    """One slot a cell covers but does not start in, as its own whitespace-delimited token."""
    return f"\t{marker}\t"


def _start_row(state: _TableState) -> None:
    state.covered = set(state.above)
    state.last = max(state.above, default=-1)
    state.cursor = 0
    state.down = {}
    state.single = False


def _place_cell(
    state: _TableState, colspan: int, rowspan: int, grid: list[int], offset: int
) -> str:
    """Place a cell by the HTML table model, in the first slot of its row no cell covers.

    The slots before it that a cell above covers are emitted first. A cell that would cover a
    slot already covered overlaps it, which a renderer draws as two texts on top of each other.
    """
    before = ""
    while state.cursor in state.covered:
        before += _covered_slot(COVERED_ABOVE)
        state.cursor += 1
    columns = range(state.cursor, state.cursor + colspan)
    if any(column in state.covered for column in columns):
        raise XhtmlError("table-shape", offset)
    grid[0] += colspan * rowspan
    if grid[0] > TABLE_SLOT_LIMIT:
        raise XhtmlError("table-size", offset)
    for column in columns:
        state.covered.add(column)
        if rowspan > 1:
            state.down[column] = rowspan - 1
    if rowspan == 1:
        state.single = True
    if colspan == 1:
        state.single_columns.add(state.cursor)
    state.last = max(state.last, columns.stop - 1)
    state.cursor += colspan
    state.open_span = colspan
    return before


def _end_row(state: _TableState, offset: int) -> str:
    """End a row: emit the covered slots after its last cell and record its width.

    The width is -1 when a slot inside the row is covered by nothing. A row that covers a slot
    but in which no cell spanning no rows starts is drawn at zero height, its cells' text in the
    rows around it, so it rejects.
    """
    trailing = sorted(column for column in state.above if column >= state.cursor)
    after = "".join(_covered_slot(COVERED_ABOVE) for _ in trailing)
    if state.covered and not state.single:
        raise XhtmlError("table-shape", offset)
    hole = state.last + 1 != len(state.covered)
    state.widths.append(-1 if hole else len(state.covered))
    above = {column: rows - 1 for column, rows in state.above.items() if rows > 1}
    above.update(state.down)
    state.above = above
    return after


def _end_row_group(state: _TableState, offset: int) -> None:
    """A cell whose rows run past its group's last row is clipped by a renderer, silently."""
    if state.above:
        raise XhtmlError("table-shape", offset)


def _end_table(state: _TableState, offset: int) -> None:
    """Every row as wide as the first, and a cell spanning no columns starts in every column.

    A renderer draws a column without one at zero width, its cells' text in the columns around
    it.
    """
    if state.rows:
        _end_row_group(state, offset)
    width = state.widths[0] if state.widths else 0
    if any(covered != width or covered < 0 for covered in state.widths):
        raise XhtmlError("table-shape", offset)
    if any(column not in state.single_columns for column in range(width)):
        raise XhtmlError("table-shape", offset)


# A list item's marker as a renderer draws it (CSS counter styles decimal, lower- and
# upper-alpha, lower- and upper-roman), followed by `.` and a space.
ROMAN: Final = (
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
)


def list_marker(style: str, ordinal: int) -> str:
    """A list item's marker as a renderer draws it, followed by ``.`` and a space.

    ``style`` is the list's type: ``a`` or ``A`` for letters from 1, ``i`` or ``I`` for roman
    numerals from 1 to 3999, and decimal for anything else or an ordinal outside those ranges.
    ``A`` and ``I`` are upper case.
    """
    marker = str(ordinal)
    if style in ("a", "A") and ordinal >= 1:
        marker = ""
        rest = ordinal
        while rest > 0:
            rest -= 1
            marker = chr(0x61 + rest % 26) + marker
            rest //= 26
    elif style in ("i", "I") and 1 <= ordinal <= 3999:
        marker = ""
        rest = ordinal
        for value, letters in ROMAN:
            while rest >= value:
                marker += letters
                rest -= value
    if style in ("A", "I"):
        marker = marker.upper()
    return f"{marker}. "


def _emit_text(
    code_point: int, parent: str | None, output: list[str], offset: int, is_reference: bool
) -> None:
    """One code point of text inside the root, raw or decoded, as the scanner emits it.

    Rejected directly inside a table container unless it is raw whitespace; folded or rejected
    inside ``sup`` and ``sub`` (``script_code_point``); otherwise kept as it is.
    """
    character = chr(code_point)
    # A line feed or carriage return in text is a space to a renderer: only a block boundary or
    # `br` is a line break.
    emitted = " " if code_point in (0x000A, 0x000D) else character
    if parent is not None and (parent in TABLE_CONTAINERS or parent in LIST_CONTAINERS):
        if is_reference or not _is_ascii_whitespace(character):
            raise XhtmlError(
                "table-content" if parent in TABLE_CONTAINERS else "list-content", offset
            )
        output.append(emitted)
        return
    if emitted != character:
        output.append(emitted)
        return
    if parent in ("sup", "sub"):
        scripted = script_code_point(parent, code_point)
        if scripted is None:
            raise XhtmlError("unmappable-script", offset)
        output.append(chr(scripted))
        return
    output.append(character)


def xhtml_to_text(div: str) -> str:
    """Convert a FHIR narrative ``div`` to text.

    Block boundaries become U+000A; inline markup is dropped except that ``sup`` and ``sub``
    fold their digits and signs; the result still needs ``normalize_text()`` before comparison.
    """
    # Section 2 applies to the div as received, markup and attribute values included, before
    # the scan: an unpaired surrogate split by markup would otherwise be joined in the output.
    forbidden = find_forbidden_character(div)
    if forbidden is not None:
        raise XhtmlError("forbidden-character", forbidden)
    # The code points the scanner emits for grids and pictures never occur in the narrative.
    reserved = _find_reserved_character(div)
    if reserved is not None:
        raise XhtmlError("reserved-character", reserved)
    invisible = _find_invisible_break(div)
    if invisible is not None:
        raise XhtmlError("invisible-character", invisible)

    output: list[str] = []
    stack: list[str] = []
    tables: list[_TableState] = []
    lists: list[_ListState] = []
    # The output positions (list indexes) where an inline tag splits the text, and the offset in
    # the div of each tag.
    splits: list[int] = []
    split_tags: list[int] = []
    # Each ``sub`` holding ½, checked after the scan; and the one open now.
    halves: list[_LoweredHalf] = []
    lowered: _LoweredHalf | None = None
    # The output positions of code points emitted inside ``sup`` or ``sub``.
    script_pieces: set[int] = set()
    # The slots every table so far covers, against TABLE_SLOT_LIMIT.
    grid = [0]
    root_seen = False
    root_closed = False
    cell_depth = 0
    index = 0

    while index < len(div):
        character = div[index]

        if character == "<":
            if div.startswith("<!--", index):
                raise XhtmlError("comment", index)
            if div.startswith("<![CDATA[", index):
                raise XhtmlError("cdata", index)
            if div.startswith("<!", index):
                raise XhtmlError("doctype", index)
            if div.startswith("<?", index):
                raise XhtmlError("processing-instruction", index)

            if div.startswith("</", index):
                end = END_TAG.match(div, index)
                if end is None:
                    raise XhtmlError("malformed-tag", index)
                name = end.group(1) or ""
                if name != name.lower():
                    raise XhtmlError("uppercase-element", index)
                if not stack:
                    raise XhtmlError("unbalanced-tag", index)
                open_name = stack.pop()
                if open_name != name:
                    raise XhtmlError("misnested-tag", index)
                if name in SPLITTING_INLINE:
                    splits.append(len(output))
                    split_tags.append(index)
                if name == "sub" and lowered is not None:
                    if lowered.offset >= 0:
                        lowered.end = len(output)
                        halves.append(lowered)
                    lowered = None
                table = tables[-1] if tables else None
                if table is not None and name in ROW_GROUPS:
                    _end_row_group(table, index)
                if name == "table" and table is not None:
                    _end_table(table, index)
                    tables.pop()
                    # On a line of its own, so an empty table's two markers are two tokens.
                    output.append("\n" + TABLE_END)
                if name == "tr" and table is not None:
                    output.append(_end_row(table, index))
                if name in LIST_CONTAINERS:
                    lists.pop()
                if name in ("td", "th"):
                    cell_depth -= 1
                if name in BLOCK_ELEMENTS:
                    output.append(_structural_break(name, cell_depth))
                if name in ("td", "th") and table is not None:
                    output.append(_covered_slot(COVERED_LEFT) * (table.open_span - 1))
                if not stack:
                    root_closed = True
                index = end.end()
                continue

            start = START_TAG.match(div, index)
            if start is None:
                # One code point, or "" past the end: `fullmatch` is `/[A-Za-z]/.test(next)`.
                following = div[index + 1 : index + 2]
                raise XhtmlError(
                    "malformed-tag" if ASCII_LETTER.fullmatch(following) else "stray-lt", index
                )
            name = start.group(1) or ""
            if name != name.lower():
                raise XhtmlError("uppercase-element", index)
            if name not in BLOCK_ELEMENTS and name not in INLINE_ELEMENTS and name != "br":
                raise XhtmlError("unknown-element", index)
            is_root = not stack
            if is_root:
                if root_seen or root_closed:
                    raise XhtmlError("multiple-roots", index)
                if name != "div":
                    raise XhtmlError("root-not-div", index)
                root_seen = True
            attributes = _check_attributes(name, start.group(2) or "", is_root, index)
            self_closing = (start.group(3) or "") == "/"
            if (name in VOID_ELEMENTS) != self_closing:
                raise XhtmlError("void-element", index)
            if not is_root:
                _check_nesting(name, stack, index)
            parent = stack[-1] if stack else None
            _check_parent(name, parent, index)
            # A rule in a cell or a caption is as narrow as its column, so a renderer draws "1",
            # the rule and "2" as a stacked fraction, ½, where the text says "1 2".
            if name == "hr" and any(open_name in RULE_BREAKS_FRACTION for open_name in stack):
                raise XhtmlError("table-content", index)
            if name in SPLITTING_INLINE:
                splits.append(len(output))
                split_tags.append(index)
            table = tables[-1] if tables else None
            _enter_table_structure(name, parent, table, index)
            if name in ("td", "th") and table is not None:
                output.append(
                    _place_cell(
                        table,
                        int(attributes.get("colspan", "1")),
                        int(attributes.get("rowspan", "1")),
                        grid,
                        index,
                    )
                )

            line_break = _structural_break(name, cell_depth)
            if name in BLOCK_ELEMENTS or name == "br":
                output.append(line_break)
            # What a renderer draws for the element itself: the grid markers of a table, a row
            # and a cell, a numbered item's marker, and a picture.
            if name == "table":
                output.append(TABLE_START)
            if name == "tr" and table is not None:
                _start_row(table)
                output.append(ROW_START)
            if name in ("td", "th"):
                output.append(CELL_START + "\t")
            if name == "li" and parent == "ol" and lists:
                output.append(list_marker(lists[-1].style, lists[-1].next))
                lists[-1].next += 1
            if name == "img":
                src = attributes.get("src", "")
                # U+FFFC, the hash, U+FFFC: closed, so a combining mark after the picture
                # cannot compose with its last digit.
                digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
                output.append(PICTURE + digest + PICTURE)
            # A self-closing element is `br`, `hr` or `img`; `hr`, a block, also emits its
            # closing break.
            if self_closing:
                if name in BLOCK_ELEMENTS:
                    output.append(line_break)
            else:
                stack.append(name)
                if name == "sub":
                    lowered = _LoweredHalf(len(output))
                if name in ("td", "th"):
                    cell_depth += 1
                if name == "table":
                    tables.append(_TableState())
                if name in LIST_CONTAINERS:
                    lists.append(
                        _ListState(attributes.get("type", "1"), int(attributes.get("start", "1")))
                    )
            index = start.end()
            continue

        if character == "&":
            entity = ENTITY.match(div, index)
            if entity is None:
                raise XhtmlError("stray-amp", index)
            if not stack:
                raise XhtmlError("text-outside-root", index)
            code_point = _decode_entity(entity, index)
            _emit_text(code_point, stack[-1], output, index, is_reference=True)
            if stack[-1] in ("sup", "sub"):
                script_pieces.add(len(output) - 1)
            if code_point == HALF and lowered is not None and lowered.offset < 0:
                lowered.offset = index
            index = entity.end()
            continue

        if not stack:
            if not _is_ascii_whitespace(character):
                raise XhtmlError("text-outside-root", index)
        else:
            parent = stack[-1]
            # "]]>" ends a CDATA section to an XML parser, which refuses the document: an XML
            # renderer draws none of the narrative (text directly in a table or list part is
            # refused first).
            if (
                div.startswith("]]>", index)
                and parent not in TABLE_CONTAINERS
                and parent not in LIST_CONTAINERS
            ):
                raise XhtmlError("cdata", index)
            _emit_text(ord(character), parent, output, index, is_reference=False)
            if parent in ("sup", "sub"):
                script_pieces.add(len(output) - 1)
            if ord(character) == HALF and lowered is not None and lowered.offset < 0:
                lowered.offset = index
        index += 1

    if not root_seen:
        raise XhtmlError("root-not-div", 0)
    if stack:
        raise XhtmlError("unbalanced-tag", len(div))
    text = "".join(output)
    # A combining mark after an inline tag is drawn in its own run, apart from the letter before
    # the tag, while NFC would join them ("<" and U+0338 across `b` is drawn "</", read as "≮").
    if halves:
        _check_lowered_halves(output, halves, script_pieces)
    offsets = _point_offsets(output)
    _check_composition(text, [offsets(split) for split in splits], split_tags)
    return text
