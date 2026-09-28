"""The standards rules the run manifest's schema describes, enforced by Zone A's verifier.

The generated model accepts a 4.0.0 manifest whose named packages are not pinned, or which pins a
package twice: JSON Schema cannot state either rule. ``VerifiedRunManifest`` refuses both, as Zone
B's Zod schema does, and the published schema's description states them word for word.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

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
