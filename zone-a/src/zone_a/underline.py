"""What an underline can change: ADR 0005, "Underlines are not unwrapped blindly".

A renderer's underline turns a sign into another sign ("<" underlined is drawn "≤", "+" "±", "="
"≡", "-" nearly "="; U+02C2 exactly "≤") and a letter after a number into an ordinal indicator
("1" and an underlined "a" read "1ª"). An underline changes nothing only over the closed allowlist
below, judged on the drawn text around it: letters and decimal digits of the Latin, Greek and
Cyrillic scripts, spaces, and plain punctuation, with no lone "a" or "o" directly after a
digit. A hyphen between two letters ("Breast-feeding") cannot read as "=" and is allowed where a
caller says so.
"""

from __future__ import annotations

import unicodedata
from typing import Final

__all__ = ["underline_changes"]

# Space, no-break space, plain punctuation and the curly quotation marks: none of them changes
# under a line (an e-mail address and a link's text are often underlined).
_PUNCTUATION: Final = frozenset(" .,;:()[]/'\"%@_&#!?*") | frozenset(
    map(chr, (0xA0, 0x2018, 0x2019, 0x201C, 0x201D))
)
# The letters an underline turns into an ordinal indicator after a number: "a" into "ª", "o"
# into "º".
_ORDINAL_LETTERS: Final = frozenset("ao")
_SCRIPTS: Final = ("LATIN ", "GREEK ", "CYRILLIC ")


def _letter(character: str) -> bool:
    return character.isalpha() and unicodedata.name(character, "").startswith(_SCRIPTS)


def _digit(character: str) -> bool:
    return "0" <= character <= "9"


def _at(text: str, index: int) -> str:
    return text[index] if 0 <= index < len(text) else ""


def underline_changes(
    text: str,
    start: int,
    end: int,
    *,
    also: frozenset[str] = frozenset(),
    hyphens_in_words: bool = False,
) -> bool:
    """Whether an underline over ``text[start:end]`` can change what the drawn text says.

    ``also`` adds code points a caller reads as markup rather than text (the QRD template's own
    brackets); ``hyphens_in_words`` allows a dash between two letters.
    """
    for index in range(start, end):
        character = text[index]
        if _letter(character) or _digit(character) or character in _PUNCTUATION:
            continue
        if character in also:
            continue
        if (
            hyphens_in_words
            and unicodedata.category(character) == "Pd"
            and _letter(_at(text, index - 1))
            and _letter(_at(text, index + 1))
        ):
            continue
        return True
    for index in range(start, end):
        character = text[index]
        if (
            character in _ORDINAL_LETTERS
            and _digit(_at(text, index - 1))
            and not _letter(_at(text, index + 1))
        ):
            return True
    return False
