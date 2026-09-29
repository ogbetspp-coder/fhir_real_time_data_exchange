"""Shared fixture and vector loading.

The oracles this test suite loads here live in the repository, one directory up: the golden
vectors and the exported contract fixtures. Other tests read the pinned EMA files and labels
(``qrd/``, ``labels/ema-epi/``) and the section mapping directly, and the generators they check
write the importer's shared cases (README, "Set-up"). No test prints what it reads — see
``test_no_narrative_leak.py``, which enforces that mechanically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VECTORS_PATH = REPOSITORY_ROOT / "test" / "fixtures" / "fidelity" / "vectors.json"
FIXTURES = REPOSITORY_ROOT / "test" / "fixtures"
CONTRACT_FIXTURES = FIXTURES / "contracts"
# Each run-manifest version's manifests, as its own code emitted them; never regenerated.
RUN_MANIFESTS = FIXTURES / "run-manifest"
CONTRACT_SCHEMAS = REPOSITORY_ROOT / "contracts" / "generated"


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise AssertionError(f"Oracle is missing: {path}. Run `npm run contracts:check` first.")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def vectors() -> Any:
    return load_json(VECTORS_PATH)


@pytest.fixture(scope="session")
def submission() -> Any:
    return load_json(CONTRACT_FIXTURES / "canonical-submission.json")


@pytest.fixture(scope="session")
def fidelity_report() -> Any:
    return load_json(CONTRACT_FIXTURES / "fidelity-report.json")


@pytest.fixture(scope="session")
def source_document_text() -> Any:
    return load_json(CONTRACT_FIXTURES / "source-document-text.json")


@pytest.fixture(scope="session")
def run_request() -> Any:
    return load_json(CONTRACT_FIXTURES / "run-request.json")


@pytest.fixture(scope="session")
def type1_submission() -> Any:
    """An authority import of the synthetic publication: a Type 1 record (audit C-8)."""
    return load_json(CONTRACT_FIXTURES / "canonical-submission-type1.json")


@pytest.fixture(scope="session")
def type1_fidelity_report() -> Any:
    return load_json(CONTRACT_FIXTURES / "fidelity-report-type1.json")


@pytest.fixture(scope="session")
def type1_source_document_text() -> Any:
    return load_json(CONTRACT_FIXTURES / "source-document-text-type1.json")


@pytest.fixture(scope="session")
def decimal_submission() -> Any:
    """The smoke product's submission, whose strength is a decimal (audit C-10)."""
    return load_json(CONTRACT_FIXTURES / "canonical-submission-decimal.json")


def run_manifest_fixtures() -> dict[str, Any]:
    """The manifests each run-manifest version's own code emitted, by file name."""
    return {path.name: load_json(path) for path in sorted(RUN_MANIFESTS.glob("*.json"))}
