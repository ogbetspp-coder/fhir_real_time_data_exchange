"""Cross-language canonical-hash parity: the test that decides whether the spec is neutral.

ADR 0002 says a Zone A implementation in another language must generate its models from
``contracts/generated/`` and reproduce the hashes. Every assertion below recomputes, in Python,
a digest that TypeScript wrote into a fixture. Nothing is copied between the two: the Python
canonical JSON encoder is an independent port of ``src/lib/hash.ts``, and if the two disagree
about key order, string escaping, or number formatting by so much as one byte, the digests
differ and these tests fail.

The round-trip half proves the other direction: that the generated pydantic models neither drop
a field nor invent one. A model that silently ignored an unknown key would still hash correctly
on the way in and wrongly on the way out, so the canonical JSON of the dumped model is compared
with the canonical JSON of the file.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from zone_a.canonical_json import CanonicalJsonError, canonical_json, sha256_json
from zone_a.contracts.canonical_submission import CanonicalSubmission
from zone_a.contracts.fidelity_report import FidelityReport
from zone_a.contracts.run_request import RunRequest
from zone_a.contracts.source_document_text import SourceDocumentText
from zone_a.fidelity import verify_report_hash


def _assert_no_float(value: Any, path: str = "$") -> None:
    """JavaScript and Python format non-integer numbers differently, so refuse to guess.

    ``canonical_json`` raises on a non-integral float rather than emitting one. This walk states
    the same thing about the fixtures themselves, so the reason a hash matches is documented:
    every number in this data is an integer, and integer formatting is identical in both
    languages. It also catches an integral float, which ``canonical_json`` *does* encode (it is
    the same JSON number as the integer) but which no fixture should be carrying.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        raise AssertionError(f"fixture carries a non-integer number at {path}")
    if isinstance(value, list):
        for position, item in enumerate(value):
            _assert_no_float(item, f"{path}[{position}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_no_float(item, f"{path}.{key}")


def test_fixtures_contain_no_floats(
    submission: Any, fidelity_report: Any, source_document_text: Any, run_request: Any
) -> None:
    for fixture in (submission, fidelity_report, source_document_text, run_request):
        _assert_no_float(fixture)


def test_canonical_json_refuses_floats() -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json({"value": 1.5})
    # Beyond Number.MAX_SAFE_INTEGER the two languages disagree about the digits themselves.
    with pytest.raises(CanonicalJsonError):
        canonical_json({"value": 9007199254740993})
    with pytest.raises(CanonicalJsonError):
        canonical_json({"value": 1e21})


def test_canonical_json_encodes_an_integral_float_as_the_integer() -> None:
    # `JSON.stringify({"page": 1.0})` is `{"page":1}`: JSON has one number type. A Zone B report
    # over a source whose page numbers were written `1.0` must reproduce here, hash included.
    assert canonical_json({"page": 1.0}) == '{"page":1}'
    assert sha256_json({"page": 1.0}) == sha256_json({"page": 1})


def test_object_keys_are_ordered_by_utf16_code_unit() -> None:
    """RFC 8785 key order is UTF-16, not code point: the two differ above the BMP.

    U+FFFD sorts *below* U+10000 by code point and *above* it by UTF-16 code unit (the astral
    character encodes as the surrogate pair D800 DC00). ``sorted()`` gets this backwards, which
    is why the encoder sorts on the UTF-16-BE encoding of each key.
    """
    astral = chr(0x10000)
    replacement = chr(0xFFFD)
    encoded = canonical_json({astral: 1, replacement: 2})
    assert encoded.index(f'"{astral}"') < encoded.index(f'"{replacement}"')
    assert sorted([astral, replacement]) == [replacement, astral]


def test_canonical_json_has_no_insignificant_whitespace() -> None:
    assert canonical_json({"b": [1, 2], "a": {"d": None, "c": True}}) == (
        '{"a":{"c":true,"d":null},"b":[1,2]}'
    )


# --- the four hashes TypeScript wrote --------------------------------------------------------


def test_bundle_hash_reproduces(submission: Any) -> None:
    assert sha256_json(submission["bundle"]) == submission["bundleSha256"]


def test_approved_content_hash_reproduces(submission: Any) -> None:
    # `approvedContent()` in src/contracts/canonical-submission.ts: exactly these three fields,
    # which is what a human approval is bound to.
    approved = {
        "schemaVersion": submission["schemaVersion"],
        "bundle": submission["bundle"],
        "provenance": submission["provenance"],
    }
    assert sha256_json(approved) == submission["approval"]["approvedContentSha256"]


def test_extracted_text_hash_reproduces(submission: Any, source_document_text: Any) -> None:
    declared = submission["provenance"]["sourceDocument"]["extractedText"]["sha256"]
    assert sha256_json(source_document_text) == declared


def test_report_hash_reproduces(fidelity_report: Any) -> None:
    # src/fidelity/verify.ts verifyReportHash: the report is hashed without its own reportHash.
    assert verify_report_hash(fidelity_report)
    body = {key: value for key, value in fidelity_report.items() if key != "reportHash"}
    assert sha256_json(body) == fidelity_report["reportHash"]


def test_report_binds_to_the_submission(submission: Any, fidelity_report: Any) -> None:
    fidelity = submission["provenance"]["fidelity"]
    assert fidelity["reportSha256"] == fidelity_report["reportHash"]
    assert fidelity["narrativeBindingSha256"] == fidelity_report["narrativeBindingSha256"]
    assert (
        fidelity_report["extractedTextSha256"]
        == (submission["provenance"]["sourceDocument"]["extractedText"]["sha256"])
    )


def test_run_request_names_the_submission(run_request: Any, submission: Any) -> None:
    assert run_request["source"] == "document"
    assert run_request["submissionRef"]["sha256"] == sha256_json(submission)


# --- the models neither drop nor invent fields -----------------------------------------------


def _round_trip(model: type[BaseModel], document: Any) -> Any:
    return model.model_validate(document).model_dump(by_alias=True, exclude_none=True, mode="json")


def _find_nulls(value: Any, path: str = "$") -> list[str]:
    if value is None:
        return [path]
    if isinstance(value, list):
        return [
            found
            for position, item in enumerate(value)
            for found in _find_nulls(item, f"{path}[{position}]")
        ]
    if isinstance(value, dict):
        return [
            found for key, item in value.items() for found in _find_nulls(item, f"{path}.{key}")
        ]
    return []


def test_no_fixture_carries_a_json_null(
    submission: Any, fidelity_report: Any, source_document_text: Any, run_request: Any
) -> None:
    """Bounds the round-trip claim below, which is not unconditional.

    ``exclude_none=True`` drops an *extra* field whose value is JSON ``null`` (it keeps a null
    nested inside a plain dict, which is not a model field). So a Bundle that carried an explicit
    null at an unmodelled position would round-trip to a different canonical JSON and a different
    Bundle hash. No fixture does, and Zod's `looseObject` does not require one, but the
    round-trip test proves the models are faithful *for data of this shape* — not for every
    conceivable Bundle. If this test ever fails, the dump options are what must change.
    """
    for fixture in (submission, fidelity_report, source_document_text, run_request):
        assert _find_nulls(fixture) == []


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("canonical-submission", CanonicalSubmission),
        ("fidelity-report", FidelityReport),
        ("source-document-text", SourceDocumentText),
        ("run-request", RunRequest),
    ],
)
def test_fixture_round_trips_through_its_model(
    name: str,
    model: type[BaseModel],
    submission: Any,
    fidelity_report: Any,
    source_document_text: Any,
    run_request: Any,
) -> None:
    document = {
        "canonical-submission": submission,
        "fidelity-report": fidelity_report,
        "source-document-text": source_document_text,
        "run-request": run_request,
    }[name]
    assert canonical_json(_round_trip(model, document)) == canonical_json(document)


def test_round_tripped_submission_still_hashes(submission: Any) -> None:
    """The strongest form of the claim: hashes survive a pass through the model layer.

    A model that dropped an unmodelled FHIR field inside the Bundle would still parse and still
    look right; only the digest notices.
    """
    dumped = _round_trip(CanonicalSubmission, submission)
    assert sha256_json(dumped["bundle"]) == submission["bundleSha256"]
    approved = {
        "schemaVersion": dumped["schemaVersion"],
        "bundle": dumped["bundle"],
        "provenance": dumped["provenance"],
    }
    assert sha256_json(approved) == submission["approval"]["approvedContentSha256"]
