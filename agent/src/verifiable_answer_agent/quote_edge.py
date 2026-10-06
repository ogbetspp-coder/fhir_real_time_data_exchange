r"""The query service's quote-edge rule, so the agent never asks it about a quote it must refuse.

``verify_quote`` answers ``match`` only when both edges of the quote fall on boundaries
(``docs/design/epi-mcp-query-service.md``, "The quote-edge rule"; ``src/query/quote-edge.ts``). A
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

Everything is in code points, as the service's offsets are. The gap, Default_Ignorable and
word-character classes are the service's own, read from ``contracts/code-points.json``, which
``scripts/sync_contract.py`` writes from the fidelity vectors and ``--check`` holds to them.
Python has no ``\p{..}`` classes; for the rest, ``unicodedata`` categories are the same Unicode
properties, at Python's Unicode version rather than Node's ICU — a difference only for
characters assigned between the two.
"""

from __future__ import annotations

import bisect
import json
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from typing import Final

__all__ = [
    "NORMALIZATION_VERSION",
    "SignIndex",
    "drawn_from",
    "edge_after",
    "edge_before",
    "find_quote_occurrence",
    "has_scanner_marker",
    "is_default_ignorable",
    "is_gap",
    "is_word_character",
    "locate_quote",
    "number_before",
    "number_from",
]

NORMALIZATION_VERSION: Final = "fidelity-norm/3.3.0"
"""The normalisation version this port was made against, and the one the post-check accepts.

``verify_quote`` names the version its answer was computed under. An answer under any other
version was decided by rules this port does not hold, so ``postcheck`` flags it rather than
trusting it. ``tests/test_quote_edge.py`` holds this to the version the service's exported
decisions carry.
"""

# src/query/quote-edge.ts QUOTE_OPENERS, QUOTE_CLOSERS and PLAIN_PUNCTUATION, character for
# character, by code point: several are look-alikes of the ASCII characters they must not be
# confused with.
QUOTE_OPENERS: Final = frozenset(
    map(chr, (0x28, 0x5B, 0x7B, 0x22, 0x27, 0x2018, 0x201C, 0x201E, 0xAB, 0x2039, 0xBF, 0xA1))
)
QUOTE_CLOSERS: Final = frozenset(".,;:!?)]}\"'") | frozenset(
    map(chr, (0x2019, 0x201D, 0xBB, 0x203A, 0x2026))
)
# What may stand between a number and a quote's edge without binding them (``isSpacedSign``):
# plain punctuation, and dashes (category Pd). Every other code point that is not a letter (other
# than a modifier letter), a number, a gap, a mark, a skipped opener or a scanner marker is a sign.
PLAIN_PUNCTUATION: Final = (
    frozenset(".,;:!?)]}\"'")
    | frozenset(map(chr, (0x2019, 0x201D, 0xBB, 0x2026, 0xAE, 0x2122, 0xA9)))
    # Reference marks: a footnote's mark binds neither side.
    | frozenset("*#")
    | frozenset(map(chr, (0x2020, 0x2021, 0xA7, 0xB6)))
)
# Signs that bind the number before them only: read after a number, never before a quote.
POSTFIX_SIGNS: Final = frozenset("%") | frozenset(
    map(chr, (0x2030, 0x2031, 0xB0, 0x2032, 0x2033, 0x2103, 0x2109))
)

# The service's ``isGap``, ``isDefaultIgnorable`` and ``isWordCharacter`` at every code point,
# vendored from the fidelity vectors by scripts/sync_contract.py: per class, the code points where
# membership flips, starting outside at U+0000.
_CODE_POINTS: Final[dict[str, list[int]]] = json.loads(
    resources.files("verifiable_answer_agent.contracts")
    .joinpath("code-points.json")
    .read_text(encoding="utf-8")
)


def _in_class(name: str, character: str) -> bool:
    return bisect.bisect_right(_CODE_POINTS[name], ord(character)) % 2 == 1


def is_word_character(character: str) -> bool:
    """``isWordCharacter``: a letter, a number, a combining mark, or an invisible joiner."""
    return _in_class("wordCharacter", character)


def _is_digit(character: str | None) -> bool:
    """``isDigit``: any code point of category N, so "½", "¹" and "₂" are numbers too."""
    return character is not None and unicodedata.category(character)[0] == "N"


def _letter_or_number(character: str) -> bool:
    category = unicodedata.category(character)
    return (category[0] == "L" and category != "Lm") or category[0] == "N"


def _scanner_marker(character: str) -> bool:
    """The scanner's grid markers and picture token delimiters: structure, never a sign."""
    point = ord(character)
    return point == 0xFFFC or 0xFDD0 <= point <= 0xFDEF


def has_scanner_marker(text: str) -> bool:
    """Whether ``text`` holds a table grid marker or a picture's U+FFFC.

    ``verify_quote`` refuses any quote carrying one (``invalid-request``): they are the
    scanner's, never a reader's, so a chunk of a table or a picture cannot be checked.
    """
    return any(_scanner_marker(character) for character in text)


def _skipped_opener(character: str) -> bool:
    """``isSkippedOpener``: the opening marks reading back skips, all but U+2039 (drawn "<")."""
    return character in QUOTE_OPENERS and character != "\u2039"


def _is_sign(character: str | None) -> bool:
    """``isSpacedSign``: whether the character is a sign.

    A sign is anything but a letter, a number, a gap, a mark, a skipped opener, a scanner
    marker, plain punctuation or a dash.
    """
    if character is None or _letter_or_number(character) or is_gap(character):
        return False
    category = unicodedata.category(character)
    return not (
        category[0] == "M"
        or category == "Pd"
        or _scanner_marker(character)
        or _skipped_opener(character)
        or character in PLAIN_PUNCTUATION
    )


def _sign_before(character: str) -> bool:
    """``isSignBefore``: a sign before a quote binds the number after it; a postfix one does not."""
    return _is_sign(character) and character not in POSTFIX_SIGNS


def _skipped_before_sign(character: str) -> bool:
    """What reading back for a sign skips: gaps, marks, and the opening marks."""
    return (
        is_gap(character) or unicodedata.category(character)[0] == "M" or _skipped_opener(character)
    )


def _signs_before(text: str) -> list[bool]:
    """``signsBefore``: for each index, whether reading back from it reaches a sign's run."""
    reached = [False] * (len(text) + 1)
    run_has_sign = False
    current = False
    for index, character in enumerate(text):
        if is_gap(character):
            run_has_sign = False
        elif _letter_or_number(character) or _scanner_marker(character):
            run_has_sign = False
            current = False
        else:
            run_has_sign = run_has_sign or _sign_before(character)
            if not _skipped_before_sign(character):
                current = run_has_sign
        reached[index + 1] = current
    return reached


# How far a single reading back goes before the one-pass index takes over (``_Signs``), or, for
# a caller without the whole text, before the reading counts as a sign: refusing a cut there is
# the safe side, and only a pathological run of brackets and spaces reaches it.
_WALK_LIMIT: Final = 256


def _sign_walk(text: str, index: int) -> bool | None:
    """``signsBefore`` at one index read back directly, or None past ``_WALK_LIMIT`` steps."""
    steps = 0
    index -= 1
    while index >= 0 and _skipped_before_sign(text[index]):
        index -= 1
        steps += 1
        if steps > _WALK_LIMIT:
            return None
    while (
        index >= 0
        and not is_gap(text[index])
        and not _letter_or_number(text[index])
        and not _scanner_marker(text[index])
    ):
        if _sign_before(text[index]):
            return True
        index -= 1
        steps += 1
        if steps > _WALK_LIMIT:
            return None
    return False


def _sign_reached(text: str, index: int) -> bool:
    """``_sign_walk`` for a caller without the whole text: a walk that runs too long is a sign."""
    reached = _sign_walk(text, index)
    return True if reached is None else reached


def is_gap(character: str) -> bool:
    """Whether ``character`` is a gap (section 6): drawn as space or as nothing."""
    return _in_class("gap", character)


def is_default_ignorable(character: str) -> bool:
    """Whether ``character`` is a Default_Ignorable_Code_Point (Unicode 16.0): drawn as nothing."""
    return _in_class("defaultIgnorable", character)


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
# ``src/query/quote-edge.ts``, "across table cells": a renderer draws a row's cells side by side
# about a space apart and centres each cell's lines, so any line of a cell can sit level with any
# line of another cell in the row. A quote beginning at a word boundary inside a cell is held to
# the digit and sign rules against every word of every cell to its left, and one ending at a word
# boundary inside a cell against every word of every cell to its right, in each row its cell
# covers. A word is a run of code points that are not gaps. The grid is read once per search.

_TABLE_START: Final = "\ufdd0"
_TABLE_END: Final = "\ufdd1"
_ROW_START: Final = "\ufdd2"
_CELL_START: Final = "\ufdd3"
_COVERED_LEFT: Final = "\ufdd4"
_COVERED_ABOVE: Final = "\ufdd5"
_SLOT_MARKERS: Final = frozenset((_CELL_START, _COVERED_LEFT, _COVERED_ABOVE))

_ENDS_SIGN: Final = 1
_ENDS_DIGIT: Final = 2
_STARTS_DIGIT: Final = 4
_STARTS_SIGN: Final = 8


@dataclass
class _Tables:
    """The cell each index of a text lies in, and each cell's neighbours in the rows it covers.

    For each index inside a cell's text, the cell's number; per cell, per row it covers, the
    bits of every cell to its left and to its right.
    """

    cell_at: list[int]
    left: list[list[int]] = field(default_factory=list)
    right: list[list[int]] = field(default_factory=list)


def _word_bits(text: str) -> int:
    """``wordBits``: a word ends in a sign when reading back from its end reaches a sign's run."""
    bits = 0
    for word in _words(text):
        if _is_digit(number_from(word, 0)):
            bits |= _STARTS_DIGIT
        if _is_sign(drawn_from(word, 0)):
            bits |= _STARTS_SIGN
        if _is_digit(number_before(word, len(word))):
            bits |= _ENDS_DIGIT
        # Exact, however long the word: it is read whole (``_sign_reached`` is for the splitter).
        if _signs_before(word)[len(word)]:
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
    return any(bits & (_STARTS_DIGIT | _STARTS_SIGN) for bits in tables.right[tables.cell_at[end]])


class _Signs:
    """``signsBefore`` over one text, computed on first use: most searches never need it."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._reached: list[bool] | None = None

    def at(self, index: int) -> bool:
        if self._reached is None:
            walked = _sign_walk(self._text, index)
            if walked is not None:
                return walked
            self._reached = _signs_before(self._text)
        return self._reached[index]


SignIndex = _Signs
"""``signsBefore`` over one whole text, exact at every index: what the splitter reads cuts with.

Built once per block, so a block of a hundred thousand spaces between brackets costs one pass
rather than a bounded walk back from every space (audit AG-12: 21.9 s for 200,000 code points).
"""


def number_before(text: str, index: int) -> str | None:
    """``numberBefore``: the first code point before ``index`` neither a gap nor a mark."""
    index -= 1
    while index >= 0 and (is_gap(text[index]) or unicodedata.category(text[index])[0] == "M"):
        index -= 1
    return text[index] if index >= 0 else None


def number_from(text: str, index: int) -> str | None:
    """``numberFrom``: the first code point from ``index`` neither a gap nor a mark."""
    while index < len(text) and (
        is_gap(text[index]) or unicodedata.category(text[index])[0] == "M"
    ):
        index += 1
    return text[index] if 0 <= index < len(text) else None


def drawn_from(text: str, index: int) -> str | None:
    """``drawnFrom``: the first code point from ``index`` not skipped when reading for a sign."""
    while index < len(text) and _skipped_before_sign(text[index]):
        index += 1
    return text[index] if 0 <= index < len(text) else None


def _cut_after_space(
    text: str,
    space: int,
    start: int,
    first: str | None,
    tables: _Tables | None,
    signs: _Signs | None,
) -> bool:
    """``cutAfterSpace``: whether a quote's start after the space at ``space`` is a cut.

    It is a cut when a sign stands before the space (read past gaps, marks and openers), when a
    number stands before it and the quote starts with a number (read past gaps and marks only),
    or when the space lies between cells of a table.
    """
    if signs.at(space) if signs is not None else _sign_reached(text, space):
        return True
    if _is_digit(number_before(text, space)) and _is_digit(first):
        return True
    return _cut_across_cell_before(tables, start, first)


def edge_before(
    text: str,
    start: int,
    first: str | None,
    tables: _Tables | None = None,
    signs: _Signs | None = None,
) -> bool:
    """Does a quote whose first character is ``first`` begin on a boundary at ``start``?"""
    before = _at(text, start - 1)
    if before is None:
        return True
    if is_word_character(before):
        return False
    if before == " ":
        return not _cut_after_space(text, start - 1, start, first, tables, signs)
    index = start
    while before is not None and before in QUOTE_OPENERS:
        if not _skipped_opener(before):
            # An opening mark drawn like a comparator (U+2039 before "30") is a sign joined to
            # the quote.
            return False
        index -= 1
        before = _at(text, index - 1)
    if index == start:
        return False
    if before is None:
        return True
    return before == " " and not _cut_after_space(text, index - 1, start, first, tables, signs)


def edge_after(text: str, end: int, last: str | None, tables: _Tables | None = None) -> bool:
    """Does a quote whose last character is ``last`` end on a boundary at ``end``?"""
    after = _at(text, end)
    if after is None:
        return True
    if is_word_character(after):
        return False
    if after == " ":
        # A number read past gaps and marks, or a sign read past opening marks too, after a
        # number binds it ("10 000", "30 %", "100" and a multiplication sign).
        if _is_digit(last) and (
            _is_digit(number_from(text, end + 1)) or _is_sign(drawn_from(text, end + 1))
        ):
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
    signs = _Signs(text)
    first = number_from(quote, 0)
    last = number_before(quote, len(quote))
    found = text.find(quote)
    while found >= 0:
        if edge_before(text, found, first, tables, signs) and edge_after(
            text, found + len(quote), last, tables
        ):
            return found
        found = text.find(quote, found + 1)
    return -1


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """``locateQuote``: the start and end offsets, in code points, of a match; or ``None``."""
    found = find_quote_occurrence(text, quote)
    return None if found < 0 else (found, found + len(quote))
