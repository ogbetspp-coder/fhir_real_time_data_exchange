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
CONTRACT_FIXTURES = REPOSITORY_ROOT / "test" / "fixtures" / "contracts"
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
