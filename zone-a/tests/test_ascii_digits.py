"""Zone A refuses the non-ASCII digits Zone B refuses (audit B07 follow-up, Low-2).

Pydantic compiles a pattern with Rust's regex crate, where ``\\d`` is any Unicode decimal digit;
Zod, and the ECMA-262 dialect JSON Schema names, read it as ``[0-9]``. While the published patterns
said ``\\d``, the generated models accepted a package version, a normalisation version, a time, a
port or a section index written in Arabic-Indic or fullwidth digits that Zone B refuses. The
patterns now say ``[0-9]``. The cases are the ones test/contracts/ascii-digits.test.ts holds Zod to.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import RootModel, ValidationError

from zone_a.contracts import (
    canonical_submission,
    fidelity_report,
    ingestion_provenance,
    run_manifest,
)

CONTRACTS = Path(__file__).resolve().parents[1] / "src" / "zone_a" / "contracts"
ARABIC_INDIC_THREE = chr(0x0663)
FULLWIDTH_THREE = chr(0xFF13)


class SectionResultPath(RootModel[str]):
    """``SectionResult.path`` alone: the field's own annotation, pattern included."""

    root: Annotated[str, *fidelity_report.SectionResult.model_fields["path"].metadata]


CASES: list[tuple[type[RootModel[str]], str, str]] = [
    (run_manifest.PackageRef, "hl7.terminology.r5#7.3.0", "hl7.terminology.r5#7.3.D"),
    (run_manifest.NormalizationVersion, "fidelity-norm/3.1.0", "fidelity-norm/3.1.D"),
    (run_manifest.IsoDateTime, "2026-09-28T00:00:03Z", "2026-09-28T00:00:0DZ"),
    (run_manifest.HttpUrl, "https://example.org:8443/a", "https://example.org:844D/a"),
    (canonical_submission.TargetPath, "Composition.section[3]", "Composition.section[D]"),
    # Review L2-c: the ingestion provenance's paths and the fidelity report's section path.
    (ingestion_provenance.SectionPath, "Composition.section[3]", "Composition.section[D]"),
    (ingestion_provenance.SourcePath, "Bundle.entry[3].resource", "Bundle.entry[D].resource"),
    (SectionResultPath, "Composition.section[3].section[0]", "Composition.section[D].section[0]"),
]


@pytest.mark.parametrize(("model", "valid", "template"), CASES)
def test_accepts_ascii_digits(model: type[RootModel[str]], valid: str, template: str) -> None:
    del template
    model.model_validate(valid)


@pytest.mark.parametrize("digit", [ARABIC_INDIC_THREE, FULLWIDTH_THREE])
@pytest.mark.parametrize(("model", "valid", "template"), CASES)
def test_refuses_other_decimal_digits(
    model: type[RootModel[str]], valid: str, template: str, digit: str
) -> None:
    del valid
    with pytest.raises(ValidationError):
        model.model_validate(template.replace("D", digit))


def test_no_generated_pattern_uses_a_unicode_shorthand() -> None:
    patterns = [
        pattern
        for module in sorted(CONTRACTS.glob("*.py"))
        for pattern in re.findall(r'pattern="((?:[^"\\]|\\.)*)"', module.read_text("utf-8"))
    ]
    assert len(patterns) > 20
    offending = [p for p in patterns if re.search(r"(?<!\\)(?:\\\\)*\\\\[dDwWsSbB]", p)]
    assert offending == []
