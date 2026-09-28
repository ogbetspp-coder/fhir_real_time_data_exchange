"""The quote-edge port's gap and word classes, at every code point.

``quote_edge`` copies ``src/fidelity/normalize.ts``'s whitespace, thin spaces, blank glyphs and
Default_Ignorable ranges into ``is_gap``, and its word characters into ``is_word_character``. The
worked quote-edge examples touch only a few of them. The fidelity vectors' ``codePoints`` family
(``test/fixtures/fidelity/code-points.ts``) records both classes for every code point, read
through the service's own functions, so a copy that drifts from the lists fails here at the code
point (audit 2026-09-27, F-2).
"""

from __future__ import annotations

import json
from typing import Any

from verifiable_answer_agent.quote_edge import is_gap, is_word_character

from .fake_query_service import REPOSITORY_ROOT

LAST_CODE_POINT = 0x10FFFF


def _table() -> Any:
    path = REPOSITORY_ROOT / "test" / "fixtures" / "fidelity" / "vectors.json"
    return json.loads(path.read_text(encoding="utf-8"))["codePoints"]


def test_every_code_point_is_a_gap_and_a_word_character_as_the_service_says() -> None:
    table = _table()
    gap = 1 << table["classes"].index("gap")
    word = 1 << table["classes"].index("wordCharacter")
    runs = table["runs"]
    for position, (start, bits) in enumerate(runs):
        end = runs[position + 1][0] if position + 1 < len(runs) else LAST_CODE_POINT + 1
        for code_point in range(start, end):
            character = chr(code_point)
            expected = (bool(bits & gap), bool(bits & word))
            actual = (is_gap(character), is_word_character(character))
            assert actual == expected, f"U+{code_point:04X}"
