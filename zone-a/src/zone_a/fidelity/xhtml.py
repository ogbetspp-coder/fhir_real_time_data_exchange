"""Fail-closed XHTML narrative scanner, ported from ``src/fidelity/xhtml.ts``.

The executable form of ``docs/fidelity-normalization.md`` section 5. Not a general HTML parser:
it accepts only the closed lists below so that no element or attribute can hide, carry, or
reorder narrative text, and rejects everything else.

Four Python-specific care points. Every one of them is a place where the two regex dialects
agree on the *syntax* and disagree on the *meaning*, which is exactly the kind of divergence a
golden vector the TypeScript author wrote will never catch:

* JavaScript's ``\\s`` and Python's ``\\s`` are different sets. JavaScript includes U+FEFF and
  excludes U+001C-U+001F and U+0085; Python is the reverse. Neither is used: since
  fidelity-norm/2.0.0 whitespace inside a tag is ``[\\t\\n\\r ]`` on both sides (``_WS`` below),
  because an HTML parser reads any other code point as part of the tag name.
* JavaScript's ``\\d`` is ASCII ``[0-9]`` unless the ``v``/``u`` flag is combined with a Unicode
  property escape; Python's ``\\d`` on a ``str`` pattern matches every Unicode decimal digit,
  so a numeric character reference written with U+FF10-U+FF19 FULLWIDTH DIGIT would be decoded
  here and be a stray ``&`` there. No ``\\d``, ``\\w`` or ``\\b`` appears in this module: the
  classes are written out as ``[0-9]`` and ``[0-9A-Fa-f]``. ``re.ASCII`` is not used as a
  blanket flag, because it would also silently narrow a class someone adds later.
* Python's ``$`` matches before a trailing newline and ``re.match`` is not anchored at the end,
  so ``pattern.match(value)`` against a ``$``-anchored grammar accepts ``"a\\n"`` and every
  value with text hidden after the last newline. Whole-value grammars use ``fullmatch`` and
  carry no anchors at all; the scan-position patterns (``END_TAG``, ``START_TAG``, ``ENTITY``)
  use ``match(div, index)``, which is the sticky ``/y`` flag of the TypeScript.
* TypeScript offsets are UTF-16 code units and Python's are code points. Error offsets are not
  part of any vector's expected value (an error vector records its code only), so indexing by
  code point changes no accepted or rejected input, only the number in an exception message.

fidelity-norm/2.0.0 adds three more, about strings rather than regexes. A string decoded from
JSON holds a lone surrogate as a code point Python accepts, and so does ``chr(0xD835)`` for a
character reference: both are checked against section 2 explicitly, the div before the scan and
each reference as it is decoded. JavaScript's ``\\p{N}`` is the Unicode general category, read
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
from typing import Final

from .normalize import find_forbidden_character, is_forbidden

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

INLINE_ELEMENTS: Final = frozenset(
    {
        "span",
        "b",
        "i",
        "u",
        "em",
        "strong",
        "sup",
        "sub",
        "small",
        "a",
        "abbr",
        "cite",
        "code",
        "img",
    }
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


class _ScriptRule:
    """Folding table, the element's own script digits and signs, the other script's."""

    __slots__ = ("folding", "foreign", "own")

    def __init__(
        self, digits: tuple[int, ...], signs: tuple[int, ...], foreign: tuple[int, ...]
    ) -> None:
        plus, minus, equals, open_, close = signs
        folding = {0x0030 + digit: target for digit, target in enumerate(digits)}
        folding.update(dict.fromkeys(PLUS_SIGNS, plus))
        folding.update(dict.fromkeys(MINUS_SIGNS, minus))
        folding[0x003D] = equals
        folding[0x0028] = open_
        folding[0x0029] = close
        self.folding: dict[int, int] = folding
        self.own: frozenset[int] = frozenset(digits + signs)
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
    ),
}

# The element's own script digits and signs are kept; the other script's digits, signs and
# letters, every other number (general category N), a plus-minus sign, and every other
# mathematical symbol, bracket or dash (general category Sm, Ps, Pe, Pd) have no script form
# there and reject.
UNMAPPABLE_SIGNS: Final = frozenset({0x00B1, 0x2213})
UNMAPPABLE_CATEGORIES: Final = frozenset({"Sm", "Ps", "Pe", "Pd"})

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
# text: each allowed attribute is restricted to a short token alphabet or a safe link form, and to
# the one element that needs it. Nothing a viewer's stylesheet or script could key on to hide text
# (`class`, `id`, a language tag below the root, an in-page link) is allowed. These are
# whole-value grammars and are applied with `fullmatch`, so they carry no `^`/`$`: Python's `$`
# would also match before a trailing newline, which is text this must not carry.
TOKEN_VALUE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,32}")
HREF_VALUE: Final = re.compile(r"https://[A-Za-z0-9.-]{1,64}(?:/[A-Za-z0-9._~-]{0,32}){0,8}/?")
LIST_TYPE_VALUE: Final = re.compile(r"[1aAiI]")
LIST_START_VALUE: Final = re.compile(r"0|-?[1-9][0-9]{0,3}")
SPAN_VALUE: Final = re.compile(r"[1-9][0-9]{0,2}|1000")
# A picture's source: a relative reference whose segments cannot start with `.` (so no `.` or
# `..` segment, no scheme, no leading `/` and no `//`), or a PNG or JPEG `data:` URI. The value is
# compared with the source through its hash (the text `img` emits), so it carries only what the
# source carries.
PICTURE_REFERENCE: Final = re.compile(
    r"[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,63}(?:/[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,63}){0,15}"
)
PICTURE_DATA_PREFIXES: Final = ("data:image/png;base64,", "data:image/jpeg;base64,")
# The base64 length of 1 MiB.
PICTURE_DATA_LIMIT: Final = 1_398_104
BASE64_ALPHABET: Final = re.compile(r"[A-Za-z0-9+/]*")

SOFT_HYPHEN: Final = chr(0x00AD)
# U+00AD followed by U+000A in the emitted text. The emitted text has U+000A only from a block
# boundary or `br`, and no U+000D at all (text line breaks are emitted as U+0020).
SOFT_HYPHEN_BEFORE_BREAK: Final = re.compile(SOFT_HYPHEN + "\n")


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
        "cursor",
        "down",
        "filled",
        "foot",
        "head",
        "open_span",
        "rows",
        "widths",
    )

    def __init__(self) -> None:
        self.caption = False
        self.head = False
        self.body = False
        self.foot = False
        self.rows = False
        # The grid, laid out by the HTML table model. For each column, how many rows, from the
        # current one, a cell above still covers.
        self.above: list[int] = []
        # The current row: which slots are covered, the next slot not yet emitted, and for each
        # column how many rows below it a cell placed in this row covers.
        self.filled: list[bool] = []
        self.cursor = 0
        self.down: dict[int, int] = {}
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
    if name == "href":
        return element == "a" and HREF_VALUE.fullmatch(value) is not None
    if name == "scope":
        return element == "th" and TOKEN_VALUE.fullmatch(value) is not None
    if name == "type":
        return element == "ol" and LIST_TYPE_VALUE.fullmatch(value) is not None
    if name == "start":
        return element == "ol" and LIST_START_VALUE.fullmatch(value) is not None
    if name in ("colspan", "rowspan"):
        return element in ("td", "th") and SPAN_VALUE.fullmatch(value) is not None
    if name == "src":
        return element == "img" and (
            PICTURE_REFERENCE.fullmatch(value) is not None or _is_picture_data(value)
        )
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
    return code_point


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
    name: str, parent: str | None, state: _TableState | None, cell_depth: int, offset: int
) -> None:
    """Only the one document order that renders as written is accepted.

    Renderers place table parts by role, not by document position: a caption always renders
    first and sections render head then body then foot, so displayed text order must equal the
    order the source was verified in. Parents are already checked. A table inside a cell is
    refused, so the grid text never nests.
    """
    if name == "table":
        if cell_depth > 0:
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
    state.filled = [rows > 0 for rows in state.above]
    state.cursor = 0
    state.down = {}


def _is_filled(state: _TableState, column: int) -> bool:
    return column < len(state.filled) and state.filled[column]


def _place_cell(state: _TableState, colspan: int, rowspan: int, offset: int) -> str:
    """Place a cell by the HTML table model, in the first slot of its row no cell covers.

    The slots before it that a cell above covers are emitted first. A cell that would cover a
    slot already covered overlaps it, which a renderer draws as two texts on top of each other.
    """
    before = ""
    while _is_filled(state, state.cursor):
        before += _covered_slot(COVERED_ABOVE)
        state.cursor += 1
    columns = range(state.cursor, state.cursor + colspan)
    if any(_is_filled(state, column) for column in columns):
        raise XhtmlError("table-shape", offset)
    if len(state.filled) < columns.stop:
        state.filled.extend([False] * (columns.stop - len(state.filled)))
    for column in columns:
        state.filled[column] = True
        state.down[column] = rowspan - 1
    state.cursor += colspan
    state.open_span = colspan
    return before


def _end_row(state: _TableState) -> str:
    """End a row: emit the covered slots after its last cell and record its width.

    The width is -1 when a slot inside the row is covered by nothing.
    """
    after = "".join(
        _covered_slot(COVERED_ABOVE)
        for column in range(state.cursor, len(state.filled))
        if state.filled[column]
    )
    width = 0
    while _is_filled(state, width):
        width += 1
    hole = any(state.filled[width:])
    state.widths.append(-1 if hole else width)
    columns = max(len(state.above), max(state.down, default=-1) + 1)
    above: list[int] = []
    for column in range(columns):
        rows = state.above[column] if column < len(state.above) else 0
        above.append(rows - 1 if rows > 0 else state.down.get(column, 0))
    state.above = above
    return after


def _end_row_group(state: _TableState, offset: int) -> None:
    """A cell whose rows run past its group's last row is clipped by a renderer, silently."""
    if any(rows > 0 for rows in state.above):
        raise XhtmlError("table-shape", offset)
    state.above = []


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
    inside ``sup`` and ``sub``; otherwise kept as it is. General category N is read from
    ``unicodedata`` because ``re`` has no ``\\p{N}``.
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
    rule = SCRIPT_RULES.get(parent) if parent is not None else None
    if rule is not None:
        folded = rule.folding.get(code_point)
        if folded is not None:
            output.append(chr(folded))
            return
        if (
            code_point in UNMAPPABLE_SIGNS
            or code_point in rule.foreign
            or (
                (
                    unicodedata.category(character)[0] == "N"
                    or unicodedata.category(character) in UNMAPPABLE_CATEGORIES
                )
                and code_point not in rule.own
            )
        ):
            raise XhtmlError("unmappable-script", offset)
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

    output: list[str] = []
    stack: list[str] = []
    tables: list[_TableState] = []
    lists: list[_ListState] = []
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
                table = tables[-1] if tables else None
                if table is not None and name in ROW_GROUPS:
                    _end_row_group(table, index)
                if name == "table" and table is not None:
                    if table.rows:
                        _end_row_group(table, index)
                    widths = table.widths
                    if any(width != widths[0] or width < 0 for width in widths):
                        raise XhtmlError("table-shape", index)
                    tables.pop()
                    # On a line of its own, so an empty table's two markers are two tokens.
                    output.append("\n" + TABLE_END)
                if name == "tr" and table is not None:
                    output.append(_end_row(table))
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
            parent = stack[-1] if stack else None
            _check_parent(name, parent, index)
            table = tables[-1] if tables else None
            _enter_table_structure(name, parent, table, cell_depth, index)
            if name in ("td", "th") and table is not None:
                output.append(
                    _place_cell(
                        table,
                        int(attributes.get("colspan", "1")),
                        int(attributes.get("rowspan", "1")),
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
                output.append(PICTURE + hashlib.sha256(src.encode("utf-8")).hexdigest())
            # A self-closing element is `br`, `hr` or `img`; `hr`, a block, also emits its
            # closing break.
            if self_closing:
                if name in BLOCK_ELEMENTS:
                    output.append(line_break)
            else:
                stack.append(name)
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
            index = entity.end()
            continue

        if not stack:
            if not _is_ascii_whitespace(character):
                raise XhtmlError("text-outside-root", index)
        else:
            _emit_text(ord(character), stack[-1], output, index, is_reference=False)
        index += 1

    if not root_seen:
        raise XhtmlError("root-not-div", 0)
    if stack:
        raise XhtmlError("unbalanced-tag", len(div))
    text = "".join(output)
    soft_hyphen = SOFT_HYPHEN_BEFORE_BREAK.search(text)
    if soft_hyphen is not None:
        raise XhtmlError("soft-hyphen-at-boundary", soft_hyphen.start())
    return text
