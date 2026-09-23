"""The golden vectors are the only oracle for the Python fidelity check.

ADR 0003: "Any re-implementation must reproduce every vector's output and ``reportHash``
byte-for-byte." This module drives all three vector families. Where a case does not yet pass it
is marked ``xfail(strict=True)`` by name, so it fails loudly the moment it starts passing and
the counts in ``zone-a/README.md`` cannot quietly drift.

Comparisons are made on the vector values themselves, exactly as the TypeScript tests do, but a
failure never reports the values: ``_assert_same`` raises with the vector name, lengths and
digests only. The vectors are synthetic, and the rule that narrative never reaches a log is
worth keeping true by construction rather than by the luck of the input being fake.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from zone_a.canonical_json import canonical_json
from zone_a.fidelity import (
    NormalizationError,
    XhtmlError,
    normalize_text,
    verify_narrative_fidelity,
    verify_report_hash,
    xhtml_to_text,
)

from .conftest import VECTORS_PATH, load_json

_VECTORS: Any = load_json(VECTORS_PATH)

# Vector names that this implementation does not yet reproduce, per module. Empty means every
# vector in that family passes. Adding a name here is a deliberate, reviewed admission; removing
# a passing one is forced by xfail(strict=True).
KNOWN_FAILURES: dict[str, frozenset[str]] = {
    "normalization": frozenset(),
    "xhtml": frozenset(),
    "verify": frozenset(),
}


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def _assert_same(actual: object, expected: object, name: str) -> None:
    """Assert equality without ever placing narrative text in the failure message."""
    if actual == expected:
        return
    raise AssertionError(
        f"vector {name}: mismatch "
        f"(expected digest {_digest(expected)}, actual digest {_digest(actual)}; "
        f"expected {len(canonical_json(expected))} canonical bytes, "
        f"actual {len(canonical_json(actual))})"
    )


def _cases(family: str) -> list[Any]:
    return [
        pytest.param(
            case,
            id=case["name"],
            marks=(
                [pytest.mark.xfail(strict=True, reason=f"{family} vector {case['name']}")]
                if case["name"] in KNOWN_FAILURES[family]
                else []
            ),
        )
        for case in _VECTORS[family]
    ]


def test_vectors_are_on_the_expected_normalization_version() -> None:
    from zone_a.fidelity import NORMALIZATION_VERSION

    assert _VECTORS["normalizationVersion"] == NORMALIZATION_VERSION


@pytest.mark.parametrize("case", _cases("normalization"))
def test_normalization_vector(case: Any) -> None:
    expected = case["expected"]
    if isinstance(expected, dict):
        # A section 2 rejection: the vector records the error code and nothing else.
        with pytest.raises(NormalizationError) as raised:
            normalize_text(case["input"])
        _assert_same({"error": raised.value.code}, expected, case["name"])
        return
    _assert_same(normalize_text(case["input"]), expected, case["name"])


@pytest.mark.parametrize("case", _cases("xhtml"))
def test_xhtml_vector(case: Any) -> None:
    expected = case["expected"]
    if isinstance(expected, dict):
        with pytest.raises(XhtmlError) as raised:
            xhtml_to_text(case["input"])
        _assert_same({"error": raised.value.code}, expected, case["name"])
        return
    _assert_same(xhtml_to_text(case["input"]), expected, case["name"])


@pytest.mark.parametrize("case", _cases("verify"))
def test_verify_vector(case: Any) -> None:
    report = verify_narrative_fidelity(case["input"])
    _assert_same(report, case["expected"], case["name"])
    # The report hash is the part Zone B re-derives, so it is checked on its own terms too.
    assert verify_report_hash(report)
    assert report["reportHash"] == case["expected"]["reportHash"]


def test_every_vector_family_is_covered() -> None:
    # A family that silently lost its vectors would make this suite pass vacuously.
    assert len(_VECTORS["normalization"]) > 0
    assert len(_VECTORS["xhtml"]) > 0
    assert len(_VECTORS["verify"]) > 0


def _div_from_json(inner_json: str) -> str:
    """A narrative root decoded from JSON text, with ``inner_json`` spliced in as escapes."""
    opening = json.dumps('<div xmlns="http://www.w3.org/1999/xhtml"><p>')
    return str(json.loads(opening[:-1] + inner_json + '</p></div>"'))


def test_an_escaped_surrogate_pair_in_json_is_one_code_point() -> None:
    """Section 2 applies to the div as decoded from JSON (RFC 8259, fidelity-norm/2.0.0).

    The golden vectors are written by ``JSON.stringify``, which never escapes a valid pair, so
    the escaped form is pinned here: ``\\ud835\\udefc`` is one code point and is accepted, while
    the same two halves split by markup, or a reference to either half, reject.
    """
    letter = chr(0x1D6FC)
    assert xhtml_to_text(_div_from_json("\\ud835\\udefc")) == f"\n\n{letter}\n\n"
    for rejected in ("\\ud835<b></b>\\udefc", "&#xD835;&#xDEFC;", "\\ud835"):
        with pytest.raises(XhtmlError) as raised:
            xhtml_to_text(_div_from_json(rejected))
        assert raised.value.code == "forbidden-character"


def test_a_grid_of_exactly_the_slot_limit_is_accepted() -> None:
    """The tables of one narrative cover at most 50 000 slots (fidelity-norm/3.0.0).

    The accepted side is pinned here rather than as a vector, whose text would be 150 000 code
    points long; the refused side is the vector ``rejects-table-over-slot-limit``.
    """
    row = '<tr><td colspan="1000">a</td></tr>'

    def table(rows: int, extra: str = "") -> str:
        return f'<div xmlns="http://www.w3.org/1999/xhtml"><table>{row * rows}</table>{extra}</div>'

    assert isinstance(xhtml_to_text(table(50)), str)
    with pytest.raises(XhtmlError) as raised:
        xhtml_to_text(table(50, "<table><tr><td>b</td></tr></table>"))
    assert raised.value.code == "table-size"
