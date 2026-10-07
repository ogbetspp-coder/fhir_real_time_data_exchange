"""The run manifest's status rule holds in the generated model, not only in Zone B's Zod schema.

A manifest (3.0.0 on) is signed before its FHIR transaction: a dry run is ``validated`` and names no
transaction; a persist-mode run is ``authorised``, is not a dry run, and names the transaction it
authorises. The published schema states the rule as one shape per status, so the generated model
refuses every other combination.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from zone_a.contracts.run_manifest import RunManifest

HASH = "a" * 64


def _manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schemaVersion": "5.1.0",
        "source": {"kind": "fixture", "resource": "fixture:test", "hash": HASH},
        "runId": "33333333-3333-4333-a333-333333333333",
        "startedAt": "2026-09-27T00:00:00Z",
        "completedAt": "2026-09-27T00:00:05Z",
        "standards": {
            "fhir": "5.0.0",
            "globalEpiPackage": "hl7.fhir.uv.emedicinal-product-info#1.0.0",
            "emaPackage": "EUePI#1.0.0",
            "qrdTemplate": "10.4",
            "mappingVersion": "1.3.0",
            "packages": [
                {"package": "hl7.fhir.uv.emedicinal-product-info#1.0.0", "sha256": HASH},
                {"package": "EUePI#1.0.0", "sha256": HASH},
            ],
        },
        "validation": {
            "preflightErrors": 0,
            "officialValidationExecuted": True,
            "officialProfileErrors": 0,
            "cloudValidationExecuted": True,
            "cloudProfileErrors": 0,
            "profiles": ["https://example.org/StructureDefinition/p"],
        },
        "transformation": {"inputHash": HASH, "outputHash": HASH, "decisions": 1},
        "runtime": {
            "sourceCommit": "0123456789abcdef0123456789abcdef01234567",
            "imageDigest": "sha256:" + "d" * 64,
            "workflowRevision": "ema-flow-worker-00001-abc",
            "validatorImageDigest": "development",
        },
        "status": "authorised",
        "dryRun": False,
        "persistence": {"targetStore": "store", "transactionSha256": HASH},
    }
    manifest.update(overrides)
    return {key: value for key, value in manifest.items() if value is not None}


def test_accepts_an_authorised_run_and_a_dry_run() -> None:
    RunManifest.model_validate(_manifest())
    RunManifest.model_validate(_manifest(status="validated", dryRun=True, persistence=None))


@pytest.mark.parametrize(
    "overrides",
    [
        {"persistence": None},
        {"dryRun": True},
        {"status": "validated", "dryRun": True},
        {"status": "validated", "dryRun": False, "persistence": None},
        {"status": "persisted"},
    ],
)
def test_refuses_any_other_combination(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RunManifest.model_validate(_manifest(**overrides))


def test_names_every_package_with_its_hash_and_the_validator_image() -> None:
    """4.0.0 names the packages that validated the run, each hashed, and the validator's image."""
    manifest = _manifest()
    standards = {k: v for k, v in manifest["standards"].items() if k != "packages"}
    with pytest.raises(ValidationError):
        RunManifest.model_validate({**manifest, "standards": standards})
    runtime = {k: v for k, v in manifest["runtime"].items() if k != "validatorImageDigest"}
    with pytest.raises(ValidationError):
        RunManifest.model_validate({**manifest, "runtime": runtime})
    unhashed = [{"package": "EUePI#1.0.0"}]
    with pytest.raises(ValidationError):
        RunManifest.model_validate(
            {**manifest, "standards": {**manifest["standards"], "packages": unhashed}}
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sourceCommit", "local"),
        ("sourceCommit", "0123456"),
        ("imageDigest", "latest"),
        ("validatorImageDigest", "sha256:" + "A" * 64),
        ("workflowRevision", "a revision"),
    ],
)
def test_names_the_code_and_the_images_in_their_own_grammars(field: str, value: str) -> None:
    """5.0.0: a commit id, image digests or ``development``, and a token; no longer free text."""
    manifest = _manifest()
    RunManifest.model_validate(manifest)
    with pytest.raises(ValidationError):
        RunManifest.model_validate({**manifest, "runtime": {**manifest["runtime"], field: value}})
