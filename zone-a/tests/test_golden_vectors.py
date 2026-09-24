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
import time
from typing import Any

import pytest

from zone_a.canonical_json import canonical_json
from zone_a.fidelity import (
    NORMALIZATION_VERSION,
    NormalizationError,
    XhtmlError,
    normalize_text,
    verify_narrative_fidelity,
    verify_report_hash,
    xhtml_to_text,
)
from zone_a.fidelity.xhtml import has_drawn_text

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
    points long; the refused side is the vector ``rejects-table-over-slot-limit``. One row of
    1000 single cells gives every column one; 49 rows then span them all.
    """
    grid = "<tr>" + "<td>a</td>" * 1000 + "</tr>" + '<tr><td colspan="1000">a</td></tr>' * 49

    def div(extra: str = "") -> str:
        return f'<div xmlns="http://www.w3.org/1999/xhtml"><table>{grid}</table>{extra}</div>'

    assert isinstance(xhtml_to_text(div()), str)
    with pytest.raises(XhtmlError) as raised:
        xhtml_to_text(div("<table><tr><td>b</td></tr></table>"))
    assert raised.value.code == "table-size"


def test_empty_rows_under_a_wide_row_scan_in_linear_time() -> None:
    """The grid is sparse, so a row costs only the slots it covers (review round 3).

    Twenty thousand empty rows under a 50 000-slot row took 79 s here before.
    """
    wide = "<tr>" + '<td colspan="1000">a</td>' * 50 + "</tr>"
    empty = "<tr></tr>" * 20_000
    div = f'<div xmlns="http://www.w3.org/1999/xhtml"><table>{wide}{empty}</table></div>'
    started = time.monotonic()
    with pytest.raises(XhtmlError) as raised:
        xhtml_to_text(div)
    assert raised.value.code == "table-shape"
    assert time.monotonic() - started < 5


def test_marks_after_many_tags_and_many_lowered_halves_scan_in_linear_time() -> None:
    """The mark rule reads each run of ignorables once (fidelity-norm/3.1.0 review round 2).

    The lowered-half rule looks only at adjacent pieces. A 37 KB div of tags before word joiners
    took 50 s here before.
    """

    def root(body: str) -> str:
        return f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{body}</p></div>'

    started = time.monotonic()
    assert isinstance(xhtml_to_text(root("t" + "<b></b>" * 20_000 + "\u2060" * 20_000 + "x")), str)
    assert isinstance(xhtml_to_text(root("t<sub>½</sub> " * 20_000)), str)
    assert time.monotonic() - started < 8


# A structured source (section 7): one page per section, the page exactly the scanner's text for
# the section's div, the whole page the body and the span. The accepted vectors that draw text and
# hold no section 3 step 1 invisible character (the extractor refuses those) must verify one
# section at a time and all together, a page each. The TypeScript twin is in test/fidelity.test.ts.
_INVISIBLE = frozenset("\u00ad\u200b\ufeff\u2060")


def _structured_source(divs: list[str]) -> dict[str, Any]:
    texts = [xhtml_to_text(div) for div in divs]
    return {
        "normalizationVersion": NORMALIZATION_VERSION,
        "source": {
            "extractorVersion": "structured-source-property/1.0.0",
            "pages": [
                {"page": index + 1, "text": text, "bodyStart": 0, "bodyEnd": len(text)}
                for index, text in enumerate(texts)
            ],
        },
        "sections": [
            {
                "sourceKey": f"section.{index + 1}",
                "path": f"Composition.section[{index}]",
                "div": div,
            }
            for index, div in enumerate(divs)
        ],
        "provenance": [
            {
                "sourceKey": f"section.{index + 1}",
                "spans": [
                    {
                        "page": index + 1,
                        "startOffset": 0,
                        "endOffset": len(text),
                        "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    }
                ],
                "narrativeDivSha256": hashlib.sha256(divs[index].encode("utf-8")).hexdigest(),
                "normalizedTextSha256": "0" * 64,
            }
            for index, text in enumerate(texts)
        ],
    }


def _structured_divs() -> list[str]:
    divs: list[str] = []
    for case in _VECTORS["xhtml"]:
        if not isinstance(case["expected"], str):
            continue
        text = xhtml_to_text(case["input"])
        if _INVISIBLE.isdisjoint(text) and has_drawn_text(normalize_text(text)):
            divs.append(case["input"])
    return divs


def test_a_structured_source_verifies_every_accepted_vector() -> None:
    divs = _structured_divs()
    assert len(divs) > 50
    for div in divs:
        assert verify_narrative_fidelity(_structured_source([div]))["status"] == "passed", div
    together = verify_narrative_fidelity(_structured_source(divs))
    assert [s for s in together["sections"] if s["status"] != "verified"] == []
    assert together["status"] == "passed"


def test_a_narrative_verifies_against_another_page_only_when_both_read_the_same() -> None:
    divs = _structured_divs()
    for page in divs:
        source = _structured_source([page])
        expected = normalize_text(xhtml_to_text(page))
        for narrative in divs:
            source["sections"][0]["div"] = narrative
            passed = verify_narrative_fidelity(source)["status"] == "passed"
            assert passed == (normalize_text(xhtml_to_text(narrative)) == expected), (
                narrative,
                page,
            )
