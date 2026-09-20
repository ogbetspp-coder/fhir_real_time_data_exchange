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
"""

from __future__ import annotations

import re
from typing import Final

XHTML_NAMESPACE: Final = "http://www.w3.org/1999/xhtml"

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
        "pre",
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

NAMED_ENTITIES: Final[dict[str, str]] = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}

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
# text: each allowed attribute is restricted to a short token alphabet or a safe link form.
# These are whole-value grammars and are applied with `fullmatch`, so they carry no `^`/`$`:
# Python's `$` would also match before a trailing newline, which is text this must not carry.
TOKEN_VALUE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,32}")
TOKEN_LIST_VALUE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,32}(?: [A-Za-z0-9_.:-]{1,32}){0,2}")
SPAN_VALUE: Final = re.compile(r"[1-9][0-9]{0,2}")
HREF_VALUE: Final = re.compile(
    r"(?:https://[A-Za-z0-9.-]{1,64}(?:/[A-Za-z0-9._~-]{0,32}){0,8}/?|#[A-Za-z0-9_.:-]{1,32})"
)

ATTRIBUTE_VALUE_RULES: Final[dict[str, re.Pattern[str]]] = {
    "xml:lang": TOKEN_VALUE,
    "lang": TOKEN_VALUE,
    "id": TOKEN_VALUE,
    "class": TOKEN_LIST_VALUE,
    "colspan": SPAN_VALUE,
    "rowspan": SPAN_VALUE,
    "scope": TOKEN_VALUE,
    "href": HREF_VALUE,
}

SOFT_HYPHEN: Final = chr(0x00AD)


class XhtmlError(ValueError):
    """Section 5 rejection. ``code`` is what a report records; it never carries text."""

    def __init__(self, code: str, offset: int) -> None:
        super().__init__(f"XHTML narrative rejected ({code}) at offset {offset}")
        self.code = code
        self.offset = offset


class _TableState:
    __slots__ = ("body", "caption", "foot", "head", "rows")

    def __init__(self) -> None:
        self.caption = False
        self.head = False
        self.body = False
        self.foot = False
        self.rows = False


def _is_ascii_whitespace(character: str) -> bool:
    return character in (" ", "\t", "\n", "\r")


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
        if name == "href" and element != "a":
            raise XhtmlError("forbidden-attribute", offset)
        rule = ATTRIBUTE_VALUE_RULES.get(name)
        # `fullmatch`, never `match`: the TypeScript anchors the grammar at both ends and
        # Python's `$` would let a trailing U+000A (and anything after it) through.
        if rule is None or rule.fullmatch(value) is None:
            raise XhtmlError("forbidden-attribute", offset)
    if is_root and not saw_namespace:
        raise XhtmlError("root-not-div", offset)


def _decode_entity(match: re.Match[str], offset: int) -> str:
    named = match.group(1)
    if named is not None:
        decoded = NAMED_ENTITIES.get(named)
        if decoded is None:
            raise XhtmlError("unknown-entity", offset)
        return decoded
    decimal = match.group(2)
    code_point = int(decimal, 10) if decimal is not None else int(match.group(3) or "", 16)
    if code_point > 0x10FFFF:
        raise XhtmlError("unknown-entity", offset)
    return chr(code_point)


def _enter_table_element(
    name: str, stack: list[str], tables: list[_TableState], offset: int
) -> None:
    """Only the one document order that renders as written is accepted.

    Renderers place table parts by role, not by document position: a caption always renders
    first and sections render head then body then foot, so displayed text order must equal the
    order the source was verified in.
    """
    parent = stack[-1] if stack else None
    if name in ("td", "th"):
        if parent != "tr":
            raise XhtmlError("misnested-tag", offset)
        return
    if name == "tr":
        if parent in ("thead", "tbody", "tfoot"):
            return
        state = tables[-1] if tables else None
        if parent != "table" or state is None:
            raise XhtmlError("misnested-tag", offset)
        if state.head or state.body or state.foot:
            raise XhtmlError("table-structure", offset)
        state.rows = True
        return
    if name not in ("caption", "thead", "tbody", "tfoot"):
        return
    state = tables[-1] if tables else None
    if parent != "table" or state is None:
        raise XhtmlError("misnested-tag", offset)
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


def xhtml_to_text(div: str) -> str:
    """Convert a FHIR narrative ``div`` to text.

    Block boundaries become U+000A and inline markup is dropped; the result still needs
    ``normalize_text()`` before comparison.
    """
    output: list[str] = []
    stack: list[str] = []
    tables: list[_TableState] = []
    root_seen = False
    root_closed = False
    index = 0

    def boundary(offset: int) -> None:
        # A structural line break directly after U+00AD would let normalisation step 1 join a
        # word across markup that renders as a hyphenated line break.
        if output and output[-1] == SOFT_HYPHEN:
            raise XhtmlError("soft-hyphen-at-boundary", offset)
        output.append("\n")

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
                    tables.pop()
                if name in BLOCK_ELEMENTS or name == "br":
                    boundary(index)
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
            _enter_table_element(name, stack, tables, index)

            if name in BLOCK_ELEMENTS or name == "br":
                boundary(index)
            if (start.group(3) or "") == "/":
                if name in BLOCK_ELEMENTS:
                    boundary(index)
                if is_root:
                    root_closed = True
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
            output.append(_decode_entity(entity, index))
            index = entity.end()
            continue

        if not stack:
            if not _is_ascii_whitespace(character):
                raise XhtmlError("text-outside-root", index)
        else:
            output.append(character)
        index += 1

    if not root_seen:
        raise XhtmlError("root-not-div", 0)
    if stack:
        raise XhtmlError("unbalanced-tag", len(div))
    return "".join(output)
