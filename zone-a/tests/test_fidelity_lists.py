"""The closed lists fidelity-norm/3.0.0 states code point by code point.

The service holds the same lists in test/fidelity-lists.test.ts. Each is held here as the
specification writes it (docs/fidelity-normalization.md sections 2 and 6), so that dropping one
entry from the port fails a test.
"""

from __future__ import annotations

import pytest

from zone_a.fidelity.normalize import is_forbidden, is_gap

FORBIDDEN_FROM_3_0_0 = (
    0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605, 0x06DD, 0x070F, 0x0890, 0x0891, 0x08E2,
    0xFFF9, 0xFFFA, 0xFFFB, 0x110BD, 0x110CD,
)  # fmt: skip
BLANK_GLYPHS = (0x1878, 0x18AA, 0x2800, 0xA4A2, 0xA4A3, 0xA4B4, 0xA4C1, 0xA4C5)


@pytest.mark.parametrize("code_point", FORBIDDEN_FROM_3_0_0)
def test_section_2_refuses_what_3_0_0_adds(code_point: int) -> None:
    assert is_forbidden(code_point)


@pytest.mark.parametrize(
    "code_point", [0x05FF, 0x0606, 0x06DC, 0x06DE, 0x070E, 0x08E1, 0x08E3, 0x110BE]
)
def test_section_2_accepts_their_neighbours(code_point: int) -> None:
    assert not is_forbidden(code_point)


@pytest.mark.parametrize("code_point", BLANK_GLYPHS)
def test_every_blank_glyph_is_a_gap(code_point: int) -> None:
    assert is_gap(code_point)


@pytest.mark.parametrize("code_point", [0x1877, 0x1879, 0x2801, 0xA4A1, 0xA4C6])
def test_their_neighbours_are_not_gaps(code_point: int) -> None:
    assert not is_gap(code_point)
