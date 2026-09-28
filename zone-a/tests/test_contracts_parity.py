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
from zone_a.run_manifest_rules import VerifiedRunManifest

from .conftest import CONTRACT_SCHEMAS, load_json, run_manifest_fixtures


def _floats(value: Any, path: str = "$") -> list[str]:
    """Where a document carries a non-integer number."""
    if isinstance(value, float):
        return [path]
    if isinstance(value, list):
        return [
            found for index, item in enumerate(value) for found in _floats(item, f"{path}[{index}]")
        ]
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in _floats(item, f"{path}.{key}")]
    return []


def test_the_decimal_fixture_carries_a_decimal(decimal_submission: Any) -> None:
    """The smoke product's strength is 2.5 mg, so a non-integer number is hashed across languages.

    Until 2026-09-28 every synthetic number was an integer and ``canonical_json`` refused any
    other (audit C-10): the parity below held only because no fixture tested it.
    """
    found = _floats(decimal_submission)
    assert found != []
    assert all(".strength[" in path and path.startswith("$.bundle.") for path in found)


def test_canonical_json_writes_a_float_as_javascript_does() -> None:
    assert canonical_json({"value": 2.5}) == '{"value":2.5}'
    assert canonical_json({"value": 1e21}) == '{"value":1e+21}'
    assert canonical_json({"value": 1e-7}) == '{"value":1e-7}'
    assert canonical_json({"value": 0.000001}) == '{"value":0.000001}'
    assert canonical_json({"value": -0.0}) == '{"value":0}'


def test_canonical_json_refuses_what_javascript_would_hold_differently() -> None:
    # Beyond Number.MAX_SAFE_INTEGER, JSON gives Python the exact integer and JavaScript the
    # nearest double: two values, refused rather than guessed.
    with pytest.raises(CanonicalJsonError):
        canonical_json({"value": 9007199254740993})
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalJsonError):
            canonical_json({"value": value})


def test_canonical_json_refuses_what_json_has_no_form_for() -> None:
    with pytest.raises(CanonicalJsonError, match="string object keys"):
        canonical_json({1: "a"})
    with pytest.raises(CanonicalJsonError, match="cannot encode set"):
        canonical_json({"value": {1, 2}})


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
    # `approvedContent()` in src/contracts/canonical-submission.ts: exactly these four fields,
    # which is what a human approval is bound to.
    approved = {
        "schemaVersion": submission["schemaVersion"],
        "graphType": submission["graphType"],
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


# --- the other submissions TypeScript wrote (audit C-8, C-10) --------------------------------


def _approved_content(document: Any) -> Any:
    return {
        "schemaVersion": document["schemaVersion"],
        "graphType": document["graphType"],
        "bundle": document["bundle"],
        "provenance": document["provenance"],
    }


def test_an_authority_import_hashes_alike(
    type1_submission: Any, type1_fidelity_report: Any, type1_source_document_text: Any
) -> None:
    """A Type 1 record, an ``authority-publication`` source and approval, and their pictures."""
    assert type1_submission["graphType"] == "type1"
    assert type1_submission["provenance"]["sourceDocument"]["kind"] == "authority-publication"
    assert type1_submission["approval"]["method"] == "authority-publication"
    assert sha256_json(type1_submission["bundle"]) == type1_submission["bundleSha256"]
    assert (
        sha256_json(_approved_content(type1_submission))
        == type1_submission["approval"]["approvedContentSha256"]
    )
    declared = type1_submission["provenance"]["sourceDocument"]["extractedText"]["sha256"]
    assert sha256_json(type1_source_document_text) == declared
    assert verify_report_hash(type1_fidelity_report)
    assert (
        type1_submission["provenance"]["fidelity"]["reportSha256"]
        == (type1_fidelity_report["reportHash"])
    )


def test_a_decimal_hashes_alike(decimal_submission: Any) -> None:
    assert sha256_json(decimal_submission["bundle"]) == decimal_submission["bundleSha256"]
    assert (
        sha256_json(_approved_content(decimal_submission))
        == decimal_submission["approval"]["approvedContentSha256"]
    )


def _published_version(name: str) -> str:
    """The version ``contracts/generated/index.json`` publishes a contract at."""
    index = load_json(CONTRACT_SCHEMAS / "index.json")
    return str(next(entry["version"] for entry in index["contracts"] if entry["name"] == name))


def test_the_emitted_manifests_verify_and_round_trip() -> None:
    """The current version's manifests, as the worker's own code emitted them, verify here.

    A fixture run and a document run, so the ingestion block, its approval union and the rules
    between fields (``VerifiedRunManifest``) are all read, and each dumps back to its own bytes.
    """
    version = _published_version("run-manifest")
    manifests = {
        name: manifest
        for name, manifest in run_manifest_fixtures().items()
        if manifest["schemaVersion"] == version
    }
    assert sorted(manifests) == [f"{version}-document.json", f"{version}-fixture.json"]
    for document in manifests.values():
        parsed = VerifiedRunManifest.model_validate(document)
        dumped = parsed.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert canonical_json(dumped) == canonical_json(document)


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


def test_the_other_submissions_round_trip_and_still_hash(
    type1_submission: Any, type1_fidelity_report: Any, decimal_submission: Any
) -> None:
    for document in (type1_submission, decimal_submission):
        assert _find_nulls(document) == []
        dumped = _round_trip(CanonicalSubmission, document)
        assert canonical_json(dumped) == canonical_json(document)
        assert sha256_json(dumped["bundle"]) == document["bundleSha256"]
    assert canonical_json(_round_trip(FidelityReport, type1_fidelity_report)) == canonical_json(
        type1_fidelity_report
    )


def test_round_tripped_submission_still_hashes(submission: Any) -> None:
    """The strongest form of the claim: hashes survive a pass through the model layer.

    A model that dropped an unmodelled FHIR field inside the Bundle would still parse and still
    look right; only the digest notices.
    """
    dumped = _round_trip(CanonicalSubmission, submission)
    assert sha256_json(dumped["bundle"]) == submission["bundleSha256"]
    approved = {
        "schemaVersion": dumped["schemaVersion"],
        "graphType": dumped["graphType"],
        "bundle": dumped["bundle"],
        "provenance": dumped["provenance"],
    }
    assert sha256_json(approved) == submission["approval"]["approvedContentSha256"]
