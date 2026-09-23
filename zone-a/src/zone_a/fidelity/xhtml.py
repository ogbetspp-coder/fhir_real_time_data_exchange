"""Fail-closed XHTML narrative scanner, ported from ``src/fidelity/xhtml.ts``.

The executable form of ``docs/fidelity-normalization.md`` section 5. Not a general HTML parser:
it accepts only the closed lists below so that no element or attribute can hide, carry, or
reorder narrative text, and rejects everything else.

Four Python-specific care points. Every one of them is a place where the two regex dialects
agree on the *syntax* and disagree on the *meaning*, which is exactly the kind of divergence a
golden vector the TypeScript author wrote will never catch:

* JavaScript's ``\\s`` and Python's ``\\s`` are different sets. JavaScript includes U+FEFF and
  excludes U+001C-U+001F and U+0085; Python is the reverse. Every ``\\s`` in the TypeScript
  regexes is therefore spelled out as ``_WS`` below, so the scanner accepts and rejects exactly
  what the TypeScript does.
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
"""

from __future__ import annotations

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
    {"span", "b", "i", "u", "em", "strong", "sup", "sub", "small", "a", "abbr", "cite", "code"}
)

# `ol` and `q` are excluded on purpose: their renderers generate list numbers and quotation
# marks that the source may not contain.

# The only elements that may be, and must be, self-closing. An HTML parser ignores the `/` of
# `<sup/>`, so any other element written that way opens around the text that follows it; and a
# `<br>` without `/` is a start tag whose content an XML renderer does not draw.
VOID_ELEMENTS: Final = frozenset({"br", "hr"})

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
PLUS_SIGNS: Final = (0x002B, 0xFE62, 0xFF0B)
MINUS_SIGNS: Final = (0x002D, 0x2212, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0xFE63, 0xFF0D)


def _script_table(
    digits: tuple[int, ...], plus: int, minus: int, equals: int, open_: int, close: int
) -> dict[int, int]:
    table = {0x0030 + digit: target for digit, target in enumerate(digits)}
    table.update(dict.fromkeys(PLUS_SIGNS, plus))
    table.update(dict.fromkeys(MINUS_SIGNS, minus))
    table[0x003D] = equals
    table[0x0028] = open_
    table[0x0029] = close
    return table


SCRIPT_FOLDING: Final[dict[str, dict[int, int]]] = {
    "sup": _script_table(SUPERSCRIPT_DIGITS, 0x207A, 0x207B, 0x207C, 0x207D, 0x207E),
    "sub": _script_table(SUBSCRIPT_DIGITS, 0x208A, 0x208B, 0x208C, 0x208D, 0x208E),
}

# Numbers already written as script digits are kept; every other number (general category N:
# a non-ASCII digit, a fraction, a numeral) and a plus-minus sign has no script form and rejects.
SCRIPT_DIGIT_TARGETS: Final = frozenset(SUPERSCRIPT_DIGITS + SUBSCRIPT_DIGITS)
UNMAPPABLE_SIGNS: Final = frozenset({0x00B1, 0x2213})

# JavaScript's \s, written out. See the module docstring. The class is assembled from code
# points rather than typed as literals, so no invisible character hides in this file.
_JS_WHITESPACE: Final = (
    0x0009,
    0x000A,
    0x000B,
    0x000C,
    0x000D,
    0x0020,
    0x00A0,
    0x1680,
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
    0xFEFF,
)
_WS: Final = "[" + "".join(map(chr, _JS_WHITESPACE)) + chr(0x2000) + "-" + chr(0x200A) + "]"

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

SOFT_HYPHEN: Final = chr(0x00AD)
# U+00AD followed by U+000A, or by U+000D U+000A, anywhere in the emitted text.
SOFT_HYPHEN_BEFORE_BREAK: Final = re.compile(SOFT_HYPHEN + "\r?\n")


class XhtmlError(ValueError):
    """Section 5 rejection. ``code`` is what a report records; it never carries text."""

    def __init__(self, code: str, offset: int) -> None:
        super().__init__(f"XHTML narrative rejected ({code}) at offset {offset}")
        self.code = code
        self.offset = offset


class _TableState:
    __slots__ = ("body", "caption", "foot", "head", "rows", "widths")

    def __init__(self) -> None:
        self.caption = False
        self.head = False
        self.body = False
        self.foot = False
        self.rows = False
        # Cell count (`td` and `th`) of every row of this table, in document order.
        self.widths: list[int] = []


def _is_ascii_whitespace(character: str) -> bool:
    return character in (" ", "\t", "\n", "\r")


def _attribute_allowed(name: str, value: str, element: str, is_root: bool) -> bool:
    # `fullmatch`, never `match`: the TypeScript anchors each grammar at both ends and Python's
    # `$` would let a trailing U+000A (and anything after it) through.
    if name in ("xml:lang", "lang"):
        return is_root and TOKEN_VALUE.fullmatch(value) is not None
    if name == "href":
        return element == "a" and HREF_VALUE.fullmatch(value) is not None
    if name == "scope":
        return element == "th" and TOKEN_VALUE.fullmatch(value) is not None
    return False


def _check_attributes(element: str, attribute_source: str, is_root: bool, offset: int) -> None:
    saw_namespace = False
    seen: set[str] = set()
    for match in ATTRIBUTE.finditer(attribute_source):
        name = match.group(1) or ""
        value = match.group(2) if match.group(2) is not None else (match.group(3) or "")
        if name in seen:
            raise XhtmlError("forbidden-attribute", offset)
        seen.add(name)
        if name == "xmlns":
            if not is_root or value != XHTML_NAMESPACE:
                raise XhtmlError("forbidden-attribute", offset)
            saw_namespace = True
            continue
        if not _attribute_allowed(name, value, element, is_root):
            raise XhtmlError("forbidden-attribute", offset)
    if is_root and not saw_namespace:
        raise XhtmlError("root-not-div", offset)


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
    return code_point


def _check_parent(name: str, parent: str | None, offset: int) -> None:
    """Nothing but text in `sup`/`sub`; each table part in its parent; only parts in a table."""
    if parent in ("sup", "sub"):
        raise XhtmlError("script-content", offset)
    allowed_parents = TABLE_PART_PARENTS.get(name)
    if allowed_parents is not None:
        if parent is None or parent not in allowed_parents:
            raise XhtmlError("misnested-tag", offset)
        return
    if parent is not None and parent in TABLE_CONTAINERS:
        raise XhtmlError("table-content", offset)


def _enter_table_element(
    name: str, parent: str | None, tables: list[_TableState], offset: int
) -> None:
    """Only the one document order that renders as written is accepted.

    Renderers place table parts by role, not by document position: a caption always renders
    first and sections render head then body then foot, so displayed text order must equal the
    order the source was verified in. Parents are already checked; this also records rows and
    cells for the shape check at ``</table>``.
    """
    state = tables[-1] if tables else None
    if state is None:
        return
    if name in ("td", "th"):
        if state.widths:
            state.widths[-1] += 1
        return
    if name == "tr":
        if parent == "table":
            if state.head or state.body or state.foot:
                raise XhtmlError("table-structure", offset)
            state.rows = True
        state.widths.append(0)
        return
    if name not in ("caption", "thead", "tbody", "tfoot"):
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


def _emit_text(
    code_point: int, parent: str | None, output: list[str], offset: int, is_reference: bool
) -> None:
    """One code point of text inside the root, raw or decoded, as the scanner emits it.

    Rejected directly inside a table container unless it is raw whitespace; folded or rejected
    inside ``sup`` and ``sub``; otherwise kept as it is. General category N is read from
    ``unicodedata`` because ``re`` has no ``\\p{N}``.
    """
    character = chr(code_point)
    if parent is not None and parent in TABLE_CONTAINERS:
        if is_reference or not _is_ascii_whitespace(character):
            raise XhtmlError("table-content", offset)
        output.append(character)
        return
    folding = SCRIPT_FOLDING.get(parent) if parent is not None else None
    if folding is not None:
        folded = folding.get(code_point)
        if folded is not None:
            output.append(chr(folded))
            return
        if code_point in UNMAPPABLE_SIGNS or (
            unicodedata.category(character)[0] == "N" and code_point not in SCRIPT_DIGIT_TARGETS
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

    output: list[str] = []
    stack: list[str] = []
    tables: list[_TableState] = []
    root_seen = False
    root_closed = False
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
                if name == "table":
                    widths = tables.pop().widths if tables else []
                    if any(width != widths[0] for width in widths):
                        raise XhtmlError("table-shape", index)
                if name in BLOCK_ELEMENTS:
                    output.append("\n")
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
            _check_attributes(name, start.group(2) or "", is_root, index)
            self_closing = (start.group(3) or "") == "/"
            if (name in VOID_ELEMENTS) != self_closing:
                raise XhtmlError("void-element", index)
            parent = stack[-1] if stack else None
            _check_parent(name, parent, index)
            _enter_table_element(name, parent, tables, index)

            if name in BLOCK_ELEMENTS or name == "br":
                output.append("\n")
            # A self-closing element is `br` or `hr`; `hr`, a block, also emits its closing break.
            if self_closing:
                if name in BLOCK_ELEMENTS:
                    output.append("\n")
            else:
                stack.append(name)
                if name == "table":
                    tables.append(_TableState())
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
