"""Differential test: the Python implementation against a corpus of TypeScript outputs.

The golden vectors are the executable specification, and 130 of them passing proves agreement on
130 inputs the TypeScript author chose. "Language-neutral" is a claim about the inputs nobody
chose, so this module tests it as a property: ``scripts/fidelity/differential.ts`` generates a
seeded corpus of synthetic inputs together with what ``src/fidelity/`` produced for each one, and
every case here is re-run through ``zone_a.fidelity`` and compared.

The corpus path comes from ``DIFFERENTIAL_CORPUS``. With the variable unset the small committed
smoke corpus is used, so ``pytest`` runs offline and without Node; CI generates a 2000-case
corpus first and points the variable at it. The smoke corpus is a subset of the same generator
at the same default seed, so a local failure is reproducible in CI and the reverse.

Three things the corpus carries that the vectors cannot:

* **class tags.** Every case records the input *classes* it was assembled from
  (``combining-after-invisible``, ``soft-hyphen-crlf``, ``table-section-order``, ...). A
  divergence is reported by class, which is what makes it possible to write the finding down in
  ``README.md`` without ever quoting the input.
* **integral float offsets.** ``JSON.stringify`` writes ``1`` for ``1.0``, so a JSON corpus
  cannot carry an integral float. The generator marks such a case with ``floatOffsets`` instead
  and ``_with_float_offsets`` rewrites the numbers here. In JavaScript ``1`` and ``1.0`` are one
  value, so the recorded expectation is exactly what the TypeScript would have produced.
* **volume.** Two thousand cases at three seeds, rather than one worked example per rule.

Nothing in here prints an input. A mismatch reports the family, the tag, the seed, the index, the
input classes, and two digests — the same discipline as ``_assert_same`` in
``test_golden_vectors.py``, and for the same reason: a failing differential run is exactly the
moment when someone is tempted to dump the input into the log.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from zone_a.canonical_json import canonical_json
from zone_a.fidelity import (
    FidelityError,
    NormalizationError,
    XhtmlError,
    normalize_text,
    verify_narrative_fidelity,
    xhtml_to_text,
)

SMOKE_CORPUS = Path(__file__).resolve().parent / "fixtures" / "differential-smoke.jsonl"

# Offsets the generator marks as integral floats; see the module docstring.
PAGE_NUMBER_FIELDS = ("page", "bodyStart", "bodyEnd")
SPAN_NUMBER_FIELDS = ("page", "startOffset", "endOffset")


def _corpus_path() -> Path:
    configured = os.environ.get("DIFFERENTIAL_CORPUS")
    return SMOKE_CORPUS if configured is None or configured == "" else Path(configured)


def _load_corpus() -> list[Any]:
    path = _corpus_path()
    if not path.is_file():
        raise AssertionError(
            f"Differential corpus is missing: {path}. Generate one with "
            "`npx tsx scripts/fidelity/differential.ts --out <file>` and set DIFFERENTIAL_CORPUS."
        )
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


_CORPUS = _load_corpus()


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def _identify(case: Any) -> str:
    return (
        f"{case['family']} case {case['index']} (seed {case['seed']}, tag {case['tag']}, "
        f"classes {','.join(case['classes'])})"
    )


def _assert_same(actual: object, expected: object, case: Any) -> None:
    """Assert equality without ever placing the generated input or output in the message."""
    if actual == expected:
        return
    raise AssertionError(
        f"{_identify(case)}: expected digest {_digest(expected)}, actual digest {_digest(actual)}"
    )


def _with_float_offsets(payload: Any) -> Any:
    """Rewrite every page and span offset as an integral float.

    ``1.0`` is the same JSON number as ``1`` and the same JavaScript value, so this must not
    change a single byte of the report. It is the one input shape the corpus cannot express,
    because ``JSON.stringify`` writes ``1`` for both.
    """
    pages = [
        {key: (float(value) if key in PAGE_NUMBER_FIELDS else value) for key, value in page.items()}
        for page in payload["source"]["pages"]
    ]
    provenance = [
        {
            **entry,
            "spans": [
                {
                    key: (float(value) if key in SPAN_NUMBER_FIELDS else value)
                    for key, value in span.items()
                }
                for span in entry["spans"]
            ],
        }
        for entry in payload["provenance"]
    ]
    return {**payload, "source": {**payload["source"], "pages": pages}, "provenance": provenance}


def _run_normalize(case: Any) -> Any:
    try:
        return {"text": normalize_text(case["input"])}
    except NormalizationError as error:
        return {"error": error.code}


def _run_xhtml(case: Any) -> Any:
    try:
        return {"text": xhtml_to_text(case["input"])}
    except XhtmlError as error:
        return {"error": error.code}


def _run_verify(case: Any) -> Any:
    payload = case["input"]
    if case.get("floatOffsets") is True:
        payload = _with_float_offsets(payload)
    try:
        report = verify_narrative_fidelity(payload)
    except FidelityError as error:
        return {"error": "FidelityError", "issues": error.issues}
    return {
        "reportHash": report["reportHash"],
        "status": report["status"],
        "sections": [
            {
                "sourceKey": section["sourceKey"],
                "status": section["status"],
                "reason": section.get("reason"),
            }
            for section in report["sections"]
        ],
        "issues": report["issues"],
    }


RUNNERS = {"normalize": _run_normalize, "xhtml": _run_xhtml, "verify": _run_verify}


def _cases() -> list[Any]:
    return [pytest.param(case, id=f"{case['family']}-{case['index']}") for case in _CORPUS]


@pytest.mark.parametrize("case", _cases())
def test_differential_case(case: Any) -> None:
    runner = RUNNERS.get(case["family"])
    assert runner is not None, f"unknown corpus family in {_identify(case)}"
    _assert_same(runner(case), case["expected"], case)


def test_the_corpus_is_not_empty_and_covers_every_family() -> None:
    # A corpus that lost a family, or an env var pointing at an empty file, would make every
    # test above pass vacuously.
    families = {case["family"] for case in _CORPUS}
    assert families == {"normalize", "xhtml", "verify"}, sorted(families)
    assert len(_CORPUS) >= 12


def test_the_corpus_exercises_the_classes_the_review_named() -> None:
    """The corpus is only as good as its alphabet; this states what the alphabet must reach.

    These are the input classes the adversarial review named as unexamined. If a future edit to
    the generator stops producing one of them, the differential run would go on passing while
    covering less, which is the failure mode this whole module exists to prevent. The smoke
    corpus is small, so the requirement is checked against the full corpus only.
    """
    if len(_CORPUS) < 500:
        pytest.skip("smoke corpus: too small to require full class coverage")
    seen = {name for case in _CORPUS for name in case["classes"]}
    required = {
        "invisible",
        "ligature",
        "bullet",
        "whitespace-class",
        "combining-after-invisible",
        "nfc-singleton",
        "hangul-jamo",
        "soft-hyphen-lf",
        "soft-hyphen-crlf",
        "forbidden-character",
        "ascii-word",
        "entity",
        "entity-non-ascii-digits",
        "forbidden-attribute-value",
        "table-section-order",
        "self-closing-block",
        "soft-hyphen-at-boundary",
        "span-exact",
        "span-shifted-left",
        "span-cut-mid-word",
        "span-crossing-pages",
        "cross-section-overlap",
        "integral-float-offsets",
        # fidelity-norm/2.0.0: every construct the change gave a rule.
        "forbidden-character-2-0-0",
        "near-forbidden",
        "soft-hyphen-space",
        "soft-hyphen-before-break",
        "soft-hyphen-accepted",
        "supplementary-character",
        "script-element",
        "script-ascii-digit",
        "script-dash",
        "script-letter",
        "script-unmappable",
        "script-reference",
        "script-child-element",
        "table-whitespace",
        "table-uneven-row",
        "table-stray-content",
        "table-empty-rows",
        "void-element",
        "void-element-and-parent",
        "spanned-cell",
        "forbidden-root-attribute",
        "surrogate-split-markup",
        "surrogate-by-reference",
        "forbidden-reference",
        "forbidden-raw-character",
        "page-blank-head",
        "page-hyphen-end",
        "page-no-final-lf",
        "page-no-final-lf-at-end",
        "page-missing",
        "page-misnumbered",
        "span-page-start",
        "span-page-end",
        "span-ends-after-soft-hyphen-space",
        # The review findings folded into fidelity-norm/2.0.0 (C1-C3, L2).
        "tag-non-ascii-whitespace",
        "tag-ascii-whitespace",
        "span-punctuation-edge",
        "number-with-punctuation",
        "bullet-line-start",
        "bullet-mid-line",
        "script-dash-review",
        "span-non-integer",
        "page-number-boolean",
        # The second review's round 2.
        "text-line-break-then-bullet",
        "number-grouped",
        "span-number-group-edge",
        "bullet-on-tab-line",
        "table-cell-bullet",
        "script-letter-or-symbol",
        # Round 3.
        "span-row-cut-before-tab",
        # fidelity-norm/3.0.0: numbered lists, table grids and pictures.
        "ordered-list",
        "ordered-list-type",
        "ordered-list-start",
        "ordered-list-long",
        "list-content",
        "li-outside-list",
        "list-attribute",
        "table-colspan",
        "table-rowspan",
        "table-span-perturbed",
        "table-span-clipped",
        "table-span-shape",
        "table-span-overlap",
        "table-span-hole",
        "picture-source",
        "reserved-reference",
        "picture-markup",
        "ordered-list-boundaries",
        "attribute-limits",
        "ordered-list-edge",
        "nested-table",
        "picture",
        "picture-data-body",
        "picture-then-combining",
        "table-in-caption",
        "table-zero-size",
        "invisible-in-narrative",
        "nesting-bounds",
        "cdata-end-in-text",
        "rule-in-table",
        "combining-across-markup",
        "underline-or-link",
        "content-space",
        "precedence-3-0-0",
        "table-size",
        "picture-violation",
        "reserved-character",
        "near-reserved",
    }
    assert required <= seen, sorted(required - seen)
