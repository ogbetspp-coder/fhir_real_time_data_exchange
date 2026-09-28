"""The standards rules the run manifest's schema describes, enforced by Zone A's verifier.

The generated model accepts a manifest whose named packages are not pinned, or which pins a
package twice: JSON Schema cannot state either rule. ``VerifiedRunManifest`` refuses both, as Zone
B's Zod schema does, and the published schema's description states them word for word. Since 5.0.0
the published schema also states, as ``if``/``then``, that a document run and only one carries an
ingestion block and an authority import and only one records its fetch; the generator does not
carry those into the model, so the verifier holds them too (audit C-8).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.conftest import run_manifest_fixtures
from tests.test_run_manifest_status import _manifest
from zone_a.contracts.run_manifest import RunManifest
from zone_a.run_manifest_rules import VerifiedRunManifest

SCHEMA = (
    Path(__file__).resolve().parents[2] / "contracts" / "generated" / "run-manifest.schema.json"
)
HASH = "b" * 64


def _with_packages(packages: list[str], **standards: str) -> dict[str, Any]:
    manifest = _manifest()
    manifest["standards"] = {
        **manifest["standards"],
        "packages": [{"package": ref, "sha256": HASH} for ref in packages],
        **standards,
    }
    return manifest


GOOD = [
    "hl7.fhir.uv.emedicinal-product-info#1.0.0",
    "EUePI#1.0.0",
    "hl7.terminology.r5#5.0.0",
    "hl7.terminology.r5#7.3.0",
]


def test_accepts_every_package_once_one_id_at_several_versions() -> None:
    VerifiedRunManifest.model_validate(_with_packages(GOOD))


@pytest.mark.parametrize(
    ("manifest", "reason"),
    [
        (_with_packages(GOOD[1:]), "globalEpiPackage is not among the pinned packages"),
        (_with_packages([GOOD[0], GOOD[2]]), "emaPackage is not among the pinned packages"),
        (
            _with_packages(GOOD, globalEpiPackage="hl7.fhir.uv.emedicinal-product-info#9.9.9"),
            "globalEpiPackage is not among the pinned packages",
        ),
        (_with_packages([*GOOD, GOOD[2]]), "a package is pinned more than once"),
    ],
)
def test_refuses_what_the_worker_refuses(manifest: dict[str, Any], reason: str) -> None:
    RunManifest.model_validate(manifest)  # the generated model alone accepts it
    with pytest.raises(ValidationError, match=reason):
        VerifiedRunManifest.model_validate(manifest)


def test_the_published_schema_states_the_rules() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    description = schema["$defs"]["ManifestStandards"]["description"]
    assert "globalEpiPackage and emaPackage are each the package of an entry of packages" in (
        description
    )
    assert "no two entries of packages name the same package (id#version)" in description


def _emitted(kind: str) -> dict[str, Any]:
    """What the worker's own code emitted for a run of this kind, at the published version."""
    version = json.loads(SCHEMA.read_text(encoding="utf-8"))["$id"].split("/")[-2]
    return copy.deepcopy(run_manifest_fixtures()[f"{version}-{kind}.json"])


def test_refuses_what_the_published_if_then_states() -> None:
    document = _emitted("document")
    fixture = _emitted("fixture")
    VerifiedRunManifest.model_validate(document)
    VerifiedRunManifest.model_validate(fixture)

    without = {key: value for key, value in document.items() if key != "ingestion"}
    fetched = copy.deepcopy(document)
    fetched["ingestion"]["authority"] = {
        "importerVersion": "2.2.0",
        "fetched": [
            {
                "url": "https://epi.example/a",
                "sha256": HASH,
                "byteLength": 1,
                "fetchedAt": "2026-09-28T00:00:00Z",
            },
            {
                "url": "https://epi.example/b",
                "sha256": HASH,
                "byteLength": 1,
                "fetchedAt": "2026-09-28T00:00:00Z",
            },
        ],
    }
    for manifest, reason in (
        (without, "a document run, and only one, carries an ingestion block"),
        ({**fixture, "ingestion": document["ingestion"]}, "a document run, and only one"),
        (fetched, "an authority import, and only one, records what Zone B fetched"),
    ):
        RunManifest.model_validate(manifest)  # the generated model alone accepts it
        with pytest.raises(ValidationError, match=reason):
            VerifiedRunManifest.model_validate(manifest)


def test_the_published_schema_states_the_rules_between_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    manifest = schema["$defs"]["RunManifest"]
    assert manifest["then"] == {"required": ["ingestion"]}
    assert manifest["else"] == {"not": {"required": ["ingestion"]}}
    evidence = schema["$defs"]["IngestionEvidence"]
    assert evidence["then"] == {"required": ["authority"]}
