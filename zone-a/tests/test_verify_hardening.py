"""The verifier's and the scanner's behaviour on hostile inputs (audit 2026-09-27, F-1 and F-5).

The TypeScript twin of each test is in ``test/fidelity.test.ts``.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import pytest

from zone_a.fidelity import (
    NORMALIZATION_VERSION,
    FidelityError,
    XhtmlError,
    verify_narrative_fidelity,
    xhtml_to_text,
)


def _root(inner: str) -> str:
    return f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{inner}</p></div>'


def _payload(text: str, div: str, spans: list[tuple[int, int]]) -> dict[str, Any]:
    return {
        "normalizationVersion": NORMALIZATION_VERSION,
        "source": {
            "extractorVersion": "synthetic-linear/1.0.0",
            "pages": [{"page": 1, "text": text, "bodyStart": 0, "bodyEnd": len(text)}],
        },
        "sections": [{"sourceKey": "s", "path": "Composition.section[0]", "div": div}],
        "provenance": [
            {
                "sourceKey": "s",
                "spans": [
                    {
                        "page": 1,
                        "startOffset": start,
                        "endOffset": end,
                        "textSha256": hashlib.sha256(text[start:end].encode()).hexdigest(),
                    }
                    for start, end in spans
                ],
                "narrativeDivSha256": hashlib.sha256(div.encode()).hexdigest(),
                "normalizedTextSha256": "0" * 64,
            }
        ],
    }


def test_many_spans_on_one_long_line_verify_in_linear_time() -> None:
    """The line a gap is on used to be read to both its ends for every gap.

    Twenty thousand one-space spans across one whitespace line, and as many one-word spans on one
    line of words, were quadratic: minutes here.
    """
    count = 20_000
    words = " ".join(["a"] * count)
    blank = "x\n" + " " * (2 * count) + "\n"
    cases = [
        (words + "\n", _root(words), [(2 * i, 2 * i + 1) for i in range(count)]),
        (blank, _root("x"), [(0, 1)] + [(2 + 2 * i, 3 + 2 * i) for i in range(count)]),
    ]
    started = time.monotonic()
    for text, div, spans in cases:
        assert verify_narrative_fidelity(_payload(text, div, spans))["status"] == "passed"
    assert time.monotonic() - started < 10


def _issues(payload: dict[str, Any]) -> list[str]:
    with pytest.raises(FidelityError) as raised:
        verify_narrative_fidelity(payload)
    return raised.value.issues


def test_a_key_or_version_that_is_not_a_string_is_refused() -> None:
    """``True`` would be written ``True`` here and ``true`` there, and ``1`` and ``True`` merged."""
    payload = _payload("a\n", _root("a"), [(0, 1)])
    assert _issues({**payload, "normalizationVersion": True}) == [
        f"Expected {NORMALIZATION_VERSION}, received a value that is not a string"
    ]
    keyed = {
        **payload,
        "sections": [{**payload["sections"][0], "sourceKey": 1}],
        "provenance": [{**payload["provenance"][0], "sourceKey": True}],
    }
    assert _issues(keyed) == [
        "Source section 0 has no string key",
        "Provenance entry 0 has no string key",
    ]


def _offset(div: str) -> int:
    with pytest.raises(XhtmlError) as raised:
        xhtml_to_text(div)
    return raised.value.offset


def test_every_error_is_at_a_code_point_offset_into_the_div() -> None:
    letter = chr(0x1D6FC)
    tag_at = len(_root(letter)) - len("</p></div>")
    assert _offset(_root(f"{letter}<q>x</q>")) == tag_at
    assert _offset(_root(f"{letter}&bogus;")) == tag_at
    # The tag a combining mark follows, not the mark's place in the scanned text.
    assert _offset(_root(f"{letter}e<b>{chr(0x0301)}</b>")) == tag_at + 1
    assert _offset(_root(f"{letter}{chr(0xFFFE)}")) == tag_at
