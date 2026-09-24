"""What an underline can change: ADR 0005, "Underlines are not unwrapped blindly".

A renderer's underline turns a sign into another sign ("<" underlined is drawn "≤", "+" "±", "="
"≡", "-" nearly "="; U+02C2 exactly "≤") and a letter after a number into an ordinal indicator ("1"
and an underlined "a" read "1ª"). An underline changes nothing only over the closed allowlist below,
judged on the drawn text around it: letters and decimal digits of the Latin, Greek and Cyrillic
scripts, spaces, and plain punctuation, with no underlined lower-case letter directly after a number
(read past code points drawn as nothing, not past a space), and no underlined "o" after an "N"
("Nº"), look-alikes included. A hyphen between two letters ("Breast-feeding") cannot read as "=" and
is allowed where a caller says so.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from zone_a.fidelity.normalize import is_default_ignorable

__all__ = ["underline_changes"]

# Space, no-break space, plain punctuation and the curly quotation marks: none of them changes
# under a line (an e-mail address and a link's text are often underlined).
_PUNCTUATION: Final = frozenset(" .,;:()[]/'\"%@_&#!?*") | frozenset(
    map(chr, (0xA0, 0x2018, 0x2019, 0x201C, 0x201D))
)
# An underlined lower-case letter after a number is drawn as an ordinal indicator ("1" and "a"
# read "1ª", "20", "o" and "C" read "20ºC"), whatever the letter's script (CYRILLIC SMALL LETTER
# A, GREEK SMALL LETTER OMICRON, LATIN LETTER SMALL CAPITAL O). An underlined "o" (or a
# look-alike) after "N" (or a look-alike) is drawn as the numero sign "Nº".
_NUMERO: Final = frozenset("Nn") | frozenset(map(chr, (0x039D, 0xFF2E)))
_O_LETTERS: Final = frozenset("o") | frozenset(map(chr, (0x043E, 0x03BF, 0x1D0F)))
_SCRIPTS: Final = ("LATIN ", "GREEK ", "CYRILLIC ")


def _letter(character: str) -> bool:
    return character.isalpha() and unicodedata.name(character, "").startswith(_SCRIPTS)


def _digit(character: str) -> bool:
    return "0" <= character <= "9"


def _at(text: str, index: int) -> str:
    return text[index] if 0 <= index < len(text) else ""


def _drawn_before(text: str, index: int) -> str:
    """The first code point before ``index`` that a renderer draws (a Default_Ignorable code
    point such as U+2063 is drawn as nothing); a space is drawn, and stops the reading."""
    index -= 1
    while index >= 0 and is_default_ignorable(ord(text[index])):
        index -= 1
    return _at(text, index)


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
        before = _drawn_before(text, index)
        if (
            unicodedata.category(character) == "Ll"
            and unicodedata.category(before or " ")[0] == "N"
        ):
            return True
        # "N" not underlined and an underlined "o" first after it.
        if index == start and before in _NUMERO and character in _O_LETTERS:
            return True
    return False
