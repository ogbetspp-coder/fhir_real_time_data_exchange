"""The ``codePoints`` vectors: every closed code-point list of the port, at every code point.

``test/fixtures/fidelity/code-points.ts`` writes, for each code point U+0000-U+10FFFF, which class
of which list it falls in, read through the TypeScript's own functions. This module reads the same
classes through the port's and requires the same table. The worked vectors reach a list only where
their author wrote an input for it: dropping U+180B-U+180F from ``DEFAULT_IGNORABLE`` or U+205F
from ``THIN_SPACES`` moved none of them (audit 2026-09-27, F-2). Here it fails at the code point.

A failure names the first code point whose classes differ and the classes, never any text.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from zone_a.fidelity.normalize import (
    NormalizationError,
    compose_text,
    is_default_ignorable,
    is_forbidden,
    is_gap,
    is_whitespace,
    is_word_character,
    normalize_text,
)
from zone_a.fidelity.xhtml import (
    _is_mark,
    is_grid_marker,
    is_invisible_break,
    is_reserved,
    script_code_point,
)

from .conftest import VECTORS_PATH, load_json

_TABLE: Any = load_json(VECTORS_PATH)["codePoints"]
LAST_CODE_POINT = 0x10FFFF


def _bullet(character: str) -> bool:
    try:
        return normalize_text(f"\n{character} x") == "x"
    except NormalizationError:
        return False


def _classes(code_point: int) -> dict[str, bool]:
    character = chr(code_point)
    composed = compose_text(character)
    removed = composed == ""
    sup = script_code_point("sup", code_point)
    sub = script_code_point("sub", code_point)
    return {
        "forbidden": is_forbidden(code_point),
        "whitespace": is_whitespace(code_point),
        "gap": is_gap(code_point),
        "defaultIgnorable": is_default_ignorable(code_point),
        "wordCharacter": is_word_character(character),
        "removedByStep1": removed,
        "expanded": not removed and composed != unicodedata.normalize("NFC", character),
        "bullet": not removed
        and not is_whitespace(code_point)
        and not is_forbidden(code_point)
        and _bullet(character),
        "reserved": is_reserved(code_point),
        "gridMarker": is_grid_marker(code_point),
        "invisibleBreak": is_invisible_break(code_point),
        "mark": _is_mark(character),
        "supFolded": sup is not None and sup != code_point,
        "supRefused": sup is None,
        "subFolded": sub is not None and sub != code_point,
        "subRefused": sub is None,
    }


def _bits(code_point: int, names: list[str]) -> int:
    classes = _classes(code_point)
    return sum(1 << bit for bit, name in enumerate(names) if classes[name])


def test_every_code_point_has_the_classes_the_typescript_gives_it() -> None:
    names: list[str] = _TABLE["classes"]
    runs: list[list[int]] = _TABLE["runs"]
    assert runs[0][0] == 0
    for position, (start, bits) in enumerate(runs):
        end = runs[position + 1][0] if position + 1 < len(runs) else LAST_CODE_POINT + 1
        for code_point in range(start, end):
            actual = _bits(code_point, names)
            if actual != bits:
                differing = [name for bit, name in enumerate(names) if (actual ^ bits) >> bit & 1]
                raise AssertionError(f"U+{code_point:04X}: classes {differing} differ")


def _folded(element: str, code_point: int) -> int | None:
    """The code point ``element`` folds ``code_point`` to, or None if it keeps or refuses it."""
    scripted = script_code_point(element, code_point)
    return None if scripted in (None, code_point) else scripted


def test_sup_and_sub_fold_to_the_same_code_points() -> None:
    folds = [
        [code_point, _folded("sup", code_point), _folded("sub", code_point)]
        for code_point in range(LAST_CODE_POINT + 1)
        if _folded("sup", code_point) is not None or _folded("sub", code_point) is not None
    ]
    assert folds == _TABLE["scriptFolds"]
