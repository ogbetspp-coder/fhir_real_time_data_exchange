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
from dataclasses import dataclass, field
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
SPACED_SIGNS: Final = (
    frozenset("<>~")
    | frozenset(
        map(chr, (0xB1, 0x2212, 0x2213, 0x223C, 0x2248, 0x2264, 0x2265, 0x2266, 0x2267, 0x2A7D))
    )
    | frozenset(map(chr, (0x2A7E,)))
    # Look-alikes drawn as a comparator, and the negated and combined comparators.
    | frozenset(map(chr, (0x02C2, 0x02C3, 0xFE64, 0xFE65, 0xFF1C, 0xFF1E, 0xFF5E)))
    | frozenset(map(chr, (0x226E, 0x226F, 0x2270, 0x2271, 0x2272, 0x2273, 0x2276, 0x2277, 0x2260)))
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
# ``src/query/tools.ts``, "across table cells": a renderer draws a row's cells side by side about a
# space apart and centres each cell's lines, so any line of a cell can sit level with any line of
# another cell in the row. A quote beginning at a word boundary inside a cell is held to the digit
# and sign rules against every word of every cell to its left, and one ending at a word boundary
# inside a cell against every word of every cell to its right, in each row its cell covers. A word
# is a run of code points that are not gaps. The grid is read once per search.

_TABLE_START: Final = "﷐"
_TABLE_END: Final = "﷑"
_ROW_START: Final = "﷒"
_CELL_START: Final = "﷓"
_COVERED_LEFT: Final = "﷔"
_COVERED_ABOVE: Final = "﷕"
_SLOT_MARKERS: Final = frozenset((_CELL_START, _COVERED_LEFT, _COVERED_ABOVE))

_ENDS_SIGN: Final = 1
_ENDS_DIGIT: Final = 2
_STARTS_DIGIT: Final = 4


@dataclass
class _Tables:
    """For each index inside a cell's text, the cell's number; per cell, per row it covers, the
    bits of every cell to its left and to its right."""

    cell_at: list[int]
    left: list[list[int]] = field(default_factory=list)
    right: list[list[int]] = field(default_factory=list)


def _word_bits(text: str) -> int:
    """``wordBits``: a word ends in a sign when its last code point that is not opening
    punctuation is a spaced sign."""
    bits = 0
    for word in _words(text):
        if _is_digit(word[0]):
            bits |= _STARTS_DIGIT
        if _is_digit(word[-1]):
            bits |= _ENDS_DIGIT
        signed = [character for character in word if character not in QUOTE_OPENERS]
        if signed and signed[-1] in SPACED_SIGNS:
            bits |= _ENDS_SIGN
    return bits


def _words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for character in text:
        if is_gap(character):
            if current:
                words.append("".join(current))
            current = []
        else:
            current.append(character)
    if current:
        words.append("".join(current))
    return words


def _index_table(text: str, open_at: int, tables: _Tables) -> int:
    close = text.find(_TABLE_END, open_at)
    stop = len(text) if close < 0 else close
    rows: list[list[tuple[str, int, int]]] = []
    current: tuple[str, int] | None = None
    for at in range(open_at + 1, stop + 1):
        point = text[at] if at < stop else ""
        if (at == stop or point == _ROW_START or point in _SLOT_MARKERS) and current is not None:
            rows[-1].append((current[0], current[1], at))
            current = None
        if at == stop:
            break
        if point == _ROW_START:
            rows.append([])
        elif point in _SLOT_MARKERS and rows:
            current = (point, at + 1)
    owner: list[list[int]] = [[] for _ in rows]
    placed: list[tuple[int, int, int, int, int]] = []  # row, column, columns, rows, bits
    for row, slots in enumerate(rows):
        for column, (marker, start, end) in enumerate(slots):
            if marker == _CELL_START:
                cell = len(placed)
                columns = 1
                while _marker(rows, row, column + columns) == _COVERED_LEFT:
                    columns += 1
                spanned = 1
                while _marker(rows, row + spanned, column) == _COVERED_ABOVE:
                    spanned += 1
                placed.append((row, column, columns, spanned, _word_bits(text[start:end])))
                for at in range(start, end):
                    tables.cell_at[at] = len(tables.left) + cell
            elif marker == _COVERED_LEFT:
                cell = owner[row][column - 1] if column > 0 else -1
            else:
                above = owner[row - 1] if row > 0 else []
                cell = above[column] if column < len(above) else -1
            owner[row].append(cell)
    before: list[list[int]] = []
    after: list[list[int]] = []
    for ids in owner:
        running = [0]
        for cell in ids:
            running.append(running[-1] | (placed[cell][4] if cell >= 0 else 0))
        before.append(running)
        trailing = [0] * (len(ids) + 1)
        for column in range(len(ids) - 1, -1, -1):
            cell = ids[column]
            trailing[column] = trailing[column + 1] | (placed[cell][4] if cell >= 0 else 0)
        after.append(trailing)
    for row, column, columns, spanned, _ in placed:
        covered = range(row, min(row + spanned, len(rows)))
        tables.left.append([_bits_at(before[r], column) for r in covered])
        tables.right.append([_bits_at(after[r], column + columns) for r in covered])
    return stop


def _marker(rows: list[list[tuple[str, int, int]]], row: int, column: int) -> str:
    if 0 <= row < len(rows) and 0 <= column < len(rows[row]):
        return rows[row][column][0]
    return ""


def _bits_at(bits: list[int], column: int) -> int:
    return bits[column] if 0 <= column < len(bits) else 0


def _index_tables(text: str) -> _Tables | None:
    open_at = text.find(_TABLE_START)
    if open_at < 0:
        return None
    tables = _Tables(cell_at=[-1] * (len(text) + 1))
    while open_at >= 0:
        stop = _index_table(text, open_at, tables)
        open_at = text.find(_TABLE_START, stop)
    return tables


def _cut_across_cell_before(tables: _Tables | None, start: int, first: str | None) -> bool:
    if tables is None or tables.cell_at[start] < 0:
        return False
    return any(
        bits & _ENDS_SIGN or (bits & _ENDS_DIGIT and _is_digit(first))
        for bits in tables.left[tables.cell_at[start]]
    )


def _cut_across_cell_after(tables: _Tables | None, end: int, last: str | None) -> bool:
    if tables is None or tables.cell_at[end] < 0 or not _is_digit(last):
        return False
    return any(bits & _STARTS_DIGIT for bits in tables.right[tables.cell_at[end]])


def _sign_before(text: str, index: int) -> str | None:
    """``signBefore``: the first code point from ``index`` back that is neither a gap nor opening
    punctuation; a sign binds a number across both, a digit does not."""
    while index >= 0 and (is_gap(text[index]) or text[index] in QUOTE_OPENERS):
        index -= 1
    return text[index] if index >= 0 else None


def _cut_after_space(
    text: str, space: int, start: int, first: str | None, tables: _Tables | None
) -> bool:
    """``cutAfterSpace``: a sign before the space, read past gaps; a grouped number; a table."""
    beyond = non_gap(text, space - 1, -1)
    if _sign_before(text, space - 1) in SPACED_SIGNS:
        return True
    if _is_digit(beyond) and _is_digit(first):
        return True
    return _cut_across_cell_before(tables, start, first)


def edge_before(text: str, start: int, first: str | None, tables: _Tables | None = None) -> bool:
    """Does a quote whose first character is ``first`` begin on a boundary at ``start``?"""
    before = _at(text, start - 1)
    if before is None:
        return True
    if is_word_character(before):
        return False
    if before == " ":
        return not _cut_after_space(text, start - 1, start, first, tables)
    index = start
    while before is not None and before in QUOTE_OPENERS:
        index -= 1
        before = _at(text, index - 1)
    if index == start:
        return False
    if before is None:
        return True
    return before == " " and not _cut_after_space(text, index - 1, start, first, tables)


def edge_after(text: str, end: int, last: str | None, tables: _Tables | None = None) -> bool:
    """Does a quote whose last character is ``last`` end on a boundary at ``end``?"""
    after = _at(text, end)
    if after is None:
        return True
    if is_word_character(after):
        return False
    if after == " ":
        if _is_digit(non_gap(text, end + 1, 1)) and _is_digit(last):
            return False
        return not _cut_across_cell_after(tables, end, last)
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
    tables = _index_tables(text)
    first = non_gap(quote, 0, 1)
    last = non_gap(quote, len(quote) - 1, -1)
    found = text.find(quote)
    while found >= 0:
        if edge_before(text, found, first, tables) and edge_after(
            text, found + len(quote), last, tables
        ):
            return found
        found = text.find(quote, found + 1)
    return -1


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """``locateQuote``: the start and end offsets, in code points, of a match; or ``None``."""
    found = find_quote_occurrence(text, quote)
    return None if found < 0 else (found, found + len(quote))
