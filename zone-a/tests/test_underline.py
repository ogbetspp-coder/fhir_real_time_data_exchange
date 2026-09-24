"""What an underline can change (zone_a.underline, ADR 0005)."""

from __future__ import annotations

import pytest

from zone_a.underline import underline_changes


def _whole(text: str, **options: object) -> bool:
    return underline_changes(text, 0, len(text), **options)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "see section 4.4",
        "Strong CYP3A inhibitors",
        "CYP2C19",
        "bg.ireland@beigene.com",
        "“Pregnancy”",
    ],
)
def test_words_digits_and_plain_punctuation_do_not_change(text: str) -> None:
    assert not _whole(text)


@pytest.mark.parametrize(
    "text", ["<", ">", "+", "=", "-", "~", *map(chr, (0x2013, 0x2212, 0x02C2, 0x1438, 0x2265))]
)
def test_a_sign_or_a_look_alike_changes(text: str) -> None:
    assert _whole(text)


def test_a_lone_a_or_o_after_a_digit_reads_as_an_ordinal() -> None:
    assert underline_changes("1a", 1, 2)
    assert underline_changes("40o", 2, 3)
    assert not underline_changes("3A", 1, 2)
    # A space is drawn, and stops the reading: an underlined "mg" after "10 " is a unit.
    assert not underline_changes("1 a", 2, 3)
    assert not underline_changes("10 mg", 3, 5)
    # A letter after the "a" does not stop it reading "1ª" ("20" and an underlined "o" before
    # "C" reads "20ºC").
    assert underline_changes("1ab", 1, 3)


def test_a_hyphen_between_letters_only_where_the_caller_allows_it() -> None:
    assert _whole("Breast-feeding")
    assert not _whole("Breast-feeding", hyphens_in_words=True)
    assert _whole("2-3", hyphens_in_words=True)
    assert underline_changes("2-3", 1, 2, hyphens_in_words=True)


def test_a_caller_can_name_code_points_it_reads_as_markup() -> None:
    assert not _whole("<Traceability>", also=frozenset("<>"))


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        # Cyrillic and Greek look-alikes of "a" and "o", drawn as the same ordinal.
        ("1" + chr(0x0430), 1, 2),
        ("20" + chr(0x043E) + "C", 2, 3),
        ("20" + chr(0x03BF) + "C", 2, 3),
        # A code point drawn as nothing between the digit and the underline.
        ("1" + chr(0x2063) + "a", 2, 3),
        ("1" + chr(0x200B) + "a", 2, 3),
        # "N" and an underlined "o" read "Nº".
        ("No 5", 1, 2),
    ],
)
def test_an_ordinal_is_judged_on_the_drawn_text(text: str, start: int, end: int) -> None:
    assert underline_changes(text, start, end)


def test_a_whole_underlined_word_after_n_is_not_a_numero_sign() -> None:
    assert not underline_changes("No dose adjustment", 0, 18)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        # Any lower-case letter after a number of any script, and its look-alikes.
        ("1" + chr(0x0251), 1, 2),
        ("1" + chr(0x1D0F), 1, 2),
        ("20" + chr(0x1D0F) + "C", 2, 3),
        ("1" + chr(0x03B1), 1, 2),
        (chr(0xFF11) + "a", 1, 2),
        (chr(0x1D7D9) + "a", 1, 2),
        (chr(0x0663) + "o", 1, 2),
        # The numero sign, with "N" and "o" look-alikes and a lower-case "n".
        ("no 5", 1, 2),
        (chr(0x039D) + "o 5", 1, 2),
        (chr(0xFF2E) + "o 5", 1, 2),
        ("N" + chr(0x043E) + " 5", 1, 2),
        ("N" + chr(0x03BF) + " 5", 1, 2),
        ("N" + chr(0x1D0F) + " 5", 1, 2),
    ],
)
def test_ordinal_and_numero_look_alikes_change(text: str, start: int, end: int) -> None:
    assert underline_changes(text, start, end)
