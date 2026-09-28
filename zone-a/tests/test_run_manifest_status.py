"""The run manifest's status rule holds in the generated model, not only in Zone B's Zod schema.

A 3.0.0 manifest is signed before its FHIR transaction: a dry run is ``validated`` and names no
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
        "schemaVersion": "3.0.0",
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
        "runtime": {"sourceCommit": "c", "imageDigest": "d", "workflowRevision": "r"},
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
