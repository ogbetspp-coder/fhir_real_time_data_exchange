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
    assert not underline_changes("1 a", 2, 3)
    assert not underline_changes("1ab", 1, 3)


def test_a_hyphen_between_letters_only_where_the_caller_allows_it() -> None:
    assert _whole("Breast-feeding")
    assert not _whole("Breast-feeding", hyphens_in_words=True)
    assert _whole("2-3", hyphens_in_words=True)
    assert underline_changes("2-3", 1, 2, hyphens_in_words=True)


def test_a_caller_can_name_code_points_it_reads_as_markup() -> None:
    assert not _whole("<Traceability>", also=frozenset("<>"))
