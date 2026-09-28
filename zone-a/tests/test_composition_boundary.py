"""The scanner skips its composition window before a code point below U+0300.

``_check_composition`` composes a window on each side of an inline tag and compares. Before a code
point below U+0300 (U+00AD aside, which never reaches the text) the boundary is stable, so the
comparison could only agree, and it is skipped (audit 2026-09-27, F-3). This is the proof, over
the interpreter's own Unicode data, as ``test/fidelity-composition.test.ts`` is over Node's.
"""

from __future__ import annotations

import unicodedata

import pytest

from zone_a.fidelity import XhtmlError, xhtml, xhtml_to_text
from zone_a.fidelity.normalize import compose_text

STABLE_BELOW = 0x0300


def test_every_code_point_below_u0300_is_a_starter_nfc_leaves_alone() -> None:
    for code_point in range(STABLE_BELOW):
        character = chr(code_point)
        assert unicodedata.combining(character) == 0, hex(code_point)
        assert unicodedata.normalize("NFC", character) == character, hex(code_point)


def test_no_canonical_composition_has_one_as_its_second_part() -> None:
    # NFC composes a starter only with what comes second in some code point's canonical
    # decomposition; every such second part (or the first code point of its own decomposition)
    # appears after the first code point of that code point's NFD form.
    seconds = [
        ord(part)
        for code_point in range(0x110000)
        if not 0xD800 <= code_point <= 0xDFFF
        for part in unicodedata.normalize("NFD", chr(code_point))[1:]
        if ord(part) < STABLE_BELOW
    ]
    assert seconds == []


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the scanner's calls of ``compose_text``: what its window comparison calls."""
    calls = [0]
    original = compose_text

    def counted(text: str) -> str:
        calls[0] += 1
        return original(text)

    monkeypatch.setattr(xhtml, "compose_text", counted)
    return calls


def _root(inner: str) -> str:
    return f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{inner}</p></div>'


def test_a_stable_boundary_is_not_composed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The window was composed three times at every inline tag (22 s for 600 000 empty tags)."""
    calls = _counting(monkeypatch)
    text = xhtml_to_text(_root("a<b></b>" * 1_000 + f"e<b>e</b>{chr(0xE9)}<i>A</i>"))
    assert text == "\n\n" + "a" * 1_000 + f"ee{chr(0xE9)}A\n\n"
    assert calls[0] == 0


def test_the_window_is_composed_where_it_can_matter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hangul jamo compose without being marks, so they still take the window."""
    calls = _counting(monkeypatch)
    with pytest.raises(XhtmlError):
        xhtml_to_text(_root(f"{chr(0x1100)}<b>{chr(0x1161)}</b>"))
    assert calls[0] == 3
