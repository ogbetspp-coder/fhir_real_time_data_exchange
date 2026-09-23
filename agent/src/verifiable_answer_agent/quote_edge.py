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


def _at(text: str, index: int) -> str | None:
    return text[index] if 0 <= index < len(text) else None


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
        return not (_is_digit(beyond) and _is_digit(first))
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
        return not (_is_digit(_at(text, end + 1)) and _is_digit(last))
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
        if edge_before(text, found, quote[0]) and edge_after(text, found + len(quote), quote[-1]):
            return found
        found = text.find(quote, found + 1)
    return -1


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """``locateQuote``: the start and end offsets, in code points, of a match; or ``None``."""
    found = find_quote_occurrence(text, quote)
    return None if found < 0 else (found, found + len(quote))
