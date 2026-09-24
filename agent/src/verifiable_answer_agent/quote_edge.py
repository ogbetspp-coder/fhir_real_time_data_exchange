"""The query service's quote-edge rule, so the agent never asks it about a quote it must refuse.

``verify_quote`` answers ``match`` only when both edges of the quote fall on boundaries
(``docs/design/epi-mcp-query-service.md``, "The quote-edge rule"; ``src/query/tools.ts``). A
block longer than the tool's 2,000-unit bound is checked in chunks, and a chunk edge the rule
calls a cut — inside ``1 000 000``, or between ``≥`` and ``30`` — is ``no-match`` although every
word of the block is the label's. The splitter in ``contract`` therefore cuts only where this
rule holds on both sides.

This is a port, not a source of truth. The service's own answers over the design's worked
examples are exported by ``scripts/contracts/export-quote-edge-cases.ts`` to
``test/fixtures/contracts/quote-edge-cases.json`` (regenerated and compared by ``npm run
contracts:check``), and ``tests/test_quote_edge.py`` holds this module to every one of them,
answer and offsets. The agent's fake query service decides ``verify_quote`` with it, so the
tests exercise the rule the real service applies rather than a plain substring search.

Everything is in code points, as the service's offsets are. Python has no ``\\p{..}`` classes;
``unicodedata`` categories are the same Unicode properties, at Python's Unicode version rather
than Node's ICU — a difference only for characters assigned between the two.
"""

from __future__ import annotations

import unicodedata
from typing import Final

__all__ = [
    "edge_after",
    "edge_before",
    "find_quote_occurrence",
    "is_word_character",
    "locate_quote",
]

# src/query/tools.ts QUOTE_OPENERS, QUOTE_CLOSERS and SPACED_SIGNS, character for character, by
# code point: several are look-alikes of the ASCII characters they must not be confused with.
QUOTE_OPENERS: Final = frozenset(
    map(chr, (0x28, 0x5B, 0x7B, 0x22, 0x27, 0x2018, 0x201C, 0x201E, 0xAB, 0x2039, 0xBF, 0xA1))
)
QUOTE_CLOSERS: Final = frozenset(".,;:!?)]}\"'") | frozenset(
    map(chr, (0x2019, 0x201D, 0xBB, 0x203A, 0x2026))
)
SPACED_SIGNS: Final = frozenset("<>~") | frozenset(
    map(chr, (0xB1, 0x2212, 0x2213, 0x223C, 0x2248, 0x2264, 0x2265, 0x2266, 0x2267, 0x2A7D, 0x2A7E))
)

# src/fidelity/normalize.ts: the invisible formatting characters of step 1 (soft hyphen, zero
# width space, byte order mark, word joiner) and the zero-width (non-)joiners belong to a word.
_WORD_JOINERS: Final = frozenset(map(chr, (0xAD, 0x200B, 0xFEFF, 0x2060, 0x200C, 0x200D)))


def is_word_character(character: str) -> bool:
    """``isWordCharacter``: a letter, a number, a combining mark, or an invisible joiner."""
    return unicodedata.category(character)[0] in "LNM" or character in _WORD_JOINERS


def _is_digit(character: str | None) -> bool:
    return character is not None and unicodedata.category(character) == "Nd"


# src/fidelity/normalize.ts ``isGap`` (fidelity-norm/3.0.0 section 6): section 3 whitespace, the
# spaces narrower than a quarter of an em, the blank glyphs (U+2800 BRAILLE PATTERN BLANK and the
# Mongolian and Yi letters Chrome's default serif face draws blank), and
# Default_Ignorable_Code_Point (Unicode 16.0).
_WHITESPACE: Final = frozenset(
    {0x09, 0x0A, 0x0D, 0x20, 0xA0, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2007, 0x2008}
    | {0x2028, 0x2029, 0x3000}
)
_THIN_SPACES: Final = frozenset((0x2006, 0x2009, 0x200A, 0x202F, 0x205F))
_BLANK_GLYPHS: Final = frozenset((0x1878, 0x18AA, 0x2800, 0xA4A2, 0xA4A3, 0xA4B4, 0xA4C1, 0xA4C5))
_DEFAULT_IGNORABLE: Final = (
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


def is_gap(character: str) -> bool:
    """Whether ``character`` is a gap (section 6): drawn as space or as nothing."""
    point = ord(character)
    return (
        point in _WHITESPACE
        or point in _THIN_SPACES
        or point in _BLANK_GLYPHS
        or any(low <= point <= high for low, high in _DEFAULT_IGNORABLE)
    )


def non_gap(text: str, index: int, step: int) -> str | None:
    """The first code point from ``index`` in direction ``step`` that is not a gap."""
    while 0 <= index < len(text):
        if not is_gap(text[index]):
            return text[index]
        index += step
    return None


def _at(text: str, index: int) -> str | None:
    return text[index] if 0 <= index < len(text) else None


# --- across table cells ------------------------------------------------------------------------
#
# ``src/query/tools.ts``, "across table cells": a renderer draws a row's cells side by side with a
# gap about as wide as a space, so a number or a sign split across cells reads as one, also with
# an empty cell between or beside a cell spanning rows. The grid is rebuilt from the markers the
# scanner writes (fidelity-norm/3.0.0 section 5), and a quote beginning or ending at a cell's edge
# is held to the digit and sign rules against the nearest cell with text on that side, in every
# row its cell covers.

_TABLE_START: Final = "\ufdd0"
_TABLE_END: Final = "\ufdd1"
_ROW_START: Final = "\ufdd2"
_CELL_START: Final = "\ufdd3"
_COVERED_LEFT: Final = "\ufdd4"
_COVERED_ABOVE: Final = "\ufdd5"
_SLOT_MARKERS: Final = frozenset((_CELL_START, _COVERED_LEFT, _COVERED_ABOVE))
_CELL_EDGE_MARKERS: Final = _SLOT_MARKERS | {_ROW_START, _TABLE_END}


# A row is a list of slots: (marker, start, end), the slot's text being ``text[start:end]``.
def _table_rows(text: str, open_at: int) -> list[list[tuple[str, int, int]]]:
    rows: list[list[tuple[str, int, int]]] = []
    current: tuple[str, int] | None = None
    for index in range(open_at + 1, len(text)):
        point = text[index]
        is_marker = point in (_TABLE_END, _ROW_START) or point in _SLOT_MARKERS
        if is_marker and current is not None:
            rows[-1].append((current[0], current[1], index))
            current = None
        if point == _TABLE_END:
            break
        if point == _ROW_START:
            rows.append([])
        elif point in _SLOT_MARKERS and rows:
            current = (point, index + 1)
    return rows


def _slot(rows: list[list[tuple[str, int, int]]], row: int, column: int) -> tuple[str, int, int]:
    if 0 <= row < len(rows) and 0 <= column < len(rows[row]):
        return rows[row][column]
    return ("", 0, 0)


def _owning_cell(
    rows: list[list[tuple[str, int, int]]], row: int, column: int
) -> tuple[int, int] | None:
    while _slot(rows, row, column)[0] == _COVERED_ABOVE:
        row -= 1
    while _slot(rows, row, column)[0] == _COVERED_LEFT:
        column -= 1
    return (row, column) if _slot(rows, row, column)[0] == _CELL_START else None


def _cell_span(rows: list[list[tuple[str, int, int]]], row: int, column: int) -> tuple[int, int]:
    columns = 1
    while _slot(rows, row, column + columns)[0] == _COVERED_LEFT:
        columns += 1
    spanned = 1
    while _slot(rows, row + spanned, column)[0] == _COVERED_ABOVE:
        spanned += 1
    return columns, spanned


def _drawn_edge(text: str, start: int, end: int, last: bool) -> str | None:
    indices = range(end - 1, start - 1, -1) if last else range(start, end)
    return next((text[i] for i in indices if not is_gap(text[i])), None)


def _neighbour(
    text: str, rows: list[list[tuple[str, int, int]]], row: int, column: int, step: int
) -> str | None:
    width = len(rows[row]) if 0 <= row < len(rows) else 0
    while 0 <= column < width:
        cell = _owning_cell(rows, row, column)
        if cell is None:
            return None
        _, start, end = _slot(rows, *cell)
        edge = _drawn_edge(text, start, end, step < 0)
        if edge is not None:
            return edge
        column = cell[1] - 1 if step < 0 else cell[1] + _cell_span(rows, *cell)[0]
    return None


def _cell_at(
    text: str, at: int
) -> tuple[list[list[tuple[str, int, int]]], int, int, tuple[int, int]] | None:
    marker = at - 1
    while marker >= 0 and text[marker] != _CELL_START:
        if text[marker] in (_TABLE_START, _TABLE_END, _ROW_START, _COVERED_LEFT, _COVERED_ABOVE):
            return None
        marker -= 1
    open_at = marker
    while open_at >= 0 and text[open_at] != _TABLE_START:
        open_at -= 1
    if marker < 0 or open_at < 0:
        return None
    rows = _table_rows(text, open_at)
    for row, slots in enumerate(rows):
        for column, (_, start, _end) in enumerate(slots):
            if start == marker + 1:
                return rows, row, column, _cell_span(rows, row, column)
    return None


def _cut_across_cell_before(text: str, start: int, first: str | None) -> bool:
    if non_gap(text, start - 1, -1) != _CELL_START:
        return False
    cell = _cell_at(text, start)
    if cell is None:
        return False
    rows, row, column, (_, spanned) = cell
    for current in range(row, row + spanned):
        edge = _neighbour(text, rows, current, column - 1, -1)
        if edge is not None and (edge in SPACED_SIGNS or (_is_digit(edge) and _is_digit(first))):
            return True
    return False


def _cut_across_cell_after(text: str, end: int, last: str | None) -> bool:
    following = non_gap(text, end, 1)
    if following is None or following not in _CELL_EDGE_MARKERS or not _is_digit(last):
        return False
    cell = _cell_at(text, end)
    if cell is None:
        return False
    rows, row, column, (columns, spanned) = cell
    return any(
        _is_digit(_neighbour(text, rows, current, column + columns, 1))
        for current in range(row, row + spanned)
    )


def edge_before(text: str, start: int, first: str | None) -> bool:
    """Does a quote whose first character is ``first`` begin on a boundary at ``start``?"""
    before = _at(text, start - 1)
    if before is None:
        return True
    if is_word_character(before):
        return False
    if before == " ":
        beyond = _at(text, start - 2)
        if beyond is not None and beyond in SPACED_SIGNS:
            return False
        if _is_digit(non_gap(text, start - 2, -1)) and _is_digit(first):
            return False
        return not _cut_across_cell_before(text, start, first)
    index = start
    while before is not None and before in QUOTE_OPENERS:
        index -= 1
        before = _at(text, index - 1)
    return index < start and (before is None or before == " ")


def edge_after(text: str, end: int, last: str | None) -> bool:
    """Does a quote whose last character is ``last`` end on a boundary at ``end``?"""
    after = _at(text, end)
    if after is None:
        return True
    if is_word_character(after):
        return False
    if after == " ":
        if _is_digit(non_gap(text, end + 1, 1)) and _is_digit(last):
            return False
        return not _cut_across_cell_after(text, end, last)
    index = end
    while after is not None and after in QUOTE_CLOSERS:
        index += 1
        after = _at(text, index)
    return index > end and (after is None or after == " ")


def find_quote_occurrence(text: str, quote: str) -> int:
    """``findQuoteOccurrence``: the first occurrence whose edges both hold, or -1.

    A cut occurrence does not end the search. An empty quote is never found: the service refuses
    one as an invalid request before it searches.
    """
    if not quote:
        return -1
    found = text.find(quote)
    while found >= 0:
        first = non_gap(quote, 0, 1)
        last = non_gap(quote, len(quote) - 1, -1)
        if edge_before(text, found, first) and edge_after(text, found + len(quote), last):
            return found
        found = text.find(quote, found + 1)
    return -1


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """``locateQuote``: the start and end offsets, in code points, of a match; or ``None``."""
    found = find_quote_occurrence(text, quote)
    return None if found < 0 else (found, found + len(quote))
