"""Text normalisation for the narrative fidelity check.

The executable form of ``docs/fidelity-normalization.md`` sections 2-4, re-implemented in
Python. The golden vectors in ``test/fixtures/fidelity/vectors.json`` are the only oracle: where
this module and the specification appear to disagree, the vectors decide and the disagreement is
recorded in ``zone-a/README.md``.

Python's own notions of "whitespace" and "word" are deliberately never used. ``str.split()``,
``str.strip()`` and ``str.isspace()`` all operate on a set that is wider than the closed list in
section 3 (they include U+001C-U+001F, which section 2 forbids outright), so each step below
tests membership of the explicit list.
"""

from __future__ import annotations

import unicodedata
from typing import Final

NORMALIZATION_VERSION: Final = "fidelity-norm/2.0.0"

# ADR 0003: NFC output depends on the Unicode Character Database of the runtime, so the UCD is
# pinned as tightly as the code. Zone B runs node:22.22.0 (ICU 77.1, Unicode 16.0).
REQUIRED_UNICODE_VERSION: Final = "16.0.0"

INVISIBLE_FORMATTING: Final = frozenset({0x00AD, 0x200B, 0xFEFF, 0x2060})

LIGATURES: Final[dict[int, str]] = {
    0xFB00: "ff",
    0xFB01: "fi",
    0xFB02: "fl",
    0xFB03: "ffi",
    0xFB04: "ffl",
    0xFB06: "st",
}

BULLET_GLYPHS: Final = frozenset(
    {0x2022, 0x2023, 0x2043, 0x2219, 0x25A0, 0x25A1, 0x25AA, 0x25AB, 0x25CB, 0x25CF, 0x25E6}
)

# Closed list, section 3 step 5. U+2000-U+200A is a range, handled in is_whitespace(). U+000B,
# U+000C and U+0085 are not here: since fidelity-norm/2.0.0 section 2 rejects them.
WHITESPACE: Final = frozenset(
    {0x0009, 0x000A, 0x000D, 0x0020, 0x00A0, 0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
)

SOFT_HYPHEN: Final = chr(0x00AD)


class NormalizationError(ValueError):
    """Section 2 rejection: the input carries a character normalisation refuses to see."""

    def __init__(self, code: str, offset: int) -> None:
        super().__init__(f"Forbidden character at code point offset {offset}")
        self.code = code
        self.offset = offset


def is_whitespace(code_point: int) -> bool:
    """True for the closed whitespace list of section 3 step 5, and nothing else."""
    return code_point in WHITESPACE or 0x2000 <= code_point <= 0x200A


def is_word_character(character: str) -> bool:
    """Section 6: letters, digits, combining marks, the step 1 invisibles, ZWNJ and ZWJ.

    ``\\p{L}``, ``\\p{N}`` and ``\\p{M}`` in the TypeScript are the Unicode general categories
    whose first letter is L, N or M; ``re`` has no ``\\p{}``, so the category is read directly.
    """
    if not character:
        return False
    first = character[0]
    code_point = ord(first)
    return (
        unicodedata.category(first)[0] in ("L", "N", "M")
        or code_point in INVISIBLE_FORMATTING
        or code_point in (0x200C, 0x200D)
    )


def is_forbidden(code_point: int) -> bool:
    """Section 2's closed rejection list.

    C1 controls (U+0080-U+009F) because a renderer remaps them through windows-1252; U+000B and
    U+000C because they are not XML characters; the bidirectional controls because their reach
    differs between a narrative block and page text.
    """
    if code_point in (0xFFFD, 0xFFFE, 0xFFFF):
        return True
    if 0x007F <= code_point <= 0x009F:
        return True
    if 0xD800 <= code_point <= 0xDFFF:
        return True
    if code_point in (0x061C, 0x200E, 0x200F):
        return True
    if 0x202A <= code_point <= 0x202E or 0x2066 <= code_point <= 0x2069:
        return True
    if code_point < 0x0020:
        return code_point not in (0x0009, 0x000A, 0x000D)
    return False


def find_forbidden_character(text: str) -> int | None:
    """Code point offset of the first character section 2 rejects, or None."""
    for offset, character in enumerate(text):
        if is_forbidden(ord(character)):
            return offset
    return None


def normalize_text(text: str) -> str:
    """Apply section 3's five ordered steps. Raises NormalizationError on a section 2 character."""
    forbidden = find_forbidden_character(text)
    if forbidden is not None:
        raise NormalizationError("forbidden-character", forbidden)

    # Steps 1 and 2 run before NFC so that a composition an invisible character or a ligature
    # would otherwise block ("e" + ZWSP + combining acute) is applied in the first pass; that is
    # what makes the whole procedure idempotent.
    expanded: list[str] = []
    position = 0
    length = len(text)
    while position < length:
        character = text[position]
        code_point = ord(character)
        if code_point in INVISIBLE_FORMATTING:
            # A soft hyphen at a line end marks a word broken across lines: the break goes with it.
            if code_point == 0x00AD:
                if text[position + 1 : position + 3] == "\r\n":
                    position += 2
                elif text[position + 1 : position + 2] == "\n":
                    position += 1
            position += 1
            continue
        expanded.append(LIGATURES.get(code_point, character))
        position += 1

    # Steps 4 and 5, with the space collapse folded into the same pass: a space is emitted only
    # when the previous emitted character was not one, which drops runs and the leading space.
    output: list[str] = []
    for character in unicodedata.normalize("NFC", "".join(expanded)):
        code_point = ord(character)
        if code_point in BULLET_GLYPHS or is_whitespace(code_point):
            if output and output[-1] != " ":
                output.append(" ")
            continue
        output.append(character)
    if output and output[-1] == " ":
        output.pop()
    return "".join(output)


def count_words(normalized: str) -> int:
    """Word count of already-normalised text: single U+0020 separators, so a plain split."""
    return 0 if not normalized else len(normalized.split(" "))
