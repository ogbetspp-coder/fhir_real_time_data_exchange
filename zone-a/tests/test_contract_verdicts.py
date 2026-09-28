"""The generated models give Zod's verdict on every case of the contract verdict corpus.

``test/fixtures/contracts/contract-verdicts.json`` (``scripts/contracts/contract-verdicts.ts``)
carries values and documents with the verdict Zod gives each. The models accepted, until
2026-09-28, what Zod refuses in three ways a reader of the schema could not see (audit C-7): a
string ``"1"`` or ``true`` for an integer, and ``null`` for an absent field (pydantic's lax
mode, now ``--strict-types`` and ``zone_a.contract_model.ContractModel``), and a pattern's
``\\d`` as any Unicode digit (Rust's ``regex``; the published patterns now say ``[0-9]``). Every
case here must agree, for the contracts Zone A reads.

A case is reported by its contract, definition or base, and variant or mutation, never by its
value: the values are synthetic, but the rule is the one every test here keeps.
"""

from __future__ import annotations

import copy
import importlib
from typing import Any

from pydantic import BaseModel, RootModel, ValidationError

from zone_a.contracts.canonical_submission import CanonicalSubmission
from zone_a.contracts.fidelity_report import FidelityReport
from zone_a.contracts.ingestion_provenance import IngestionProvenance
from zone_a.contracts.run_request import RunRequest
from zone_a.contracts.source_document_text import SourceDocumentText
from zone_a.run_manifest_rules import VerifiedRunManifest

from .conftest import CONTRACT_FIXTURES, FIXTURES, load_json

CORPUS = load_json(CONTRACT_FIXTURES / "contract-verdicts.json")

# The contracts Zone A neither writes nor reads (scripts/generate_models.py, NOT_ZONE_A).
NOT_ZONE_A = frozenset({"agent-turn", "query-tools"})

# Each Zone A contract's reader: its generated root model, or the verifier on top of it where the
# contract has rules between fields (zone_a.run_manifest_rules).
READERS: dict[str, type[BaseModel]] = {
    "canonical-submission": CanonicalSubmission,
    "fidelity-report": FidelityReport,
    "ingestion-provenance": IngestionProvenance,
    "run-manifest": VerifiedRunManifest,
    "run-request": RunRequest,
    "source-document-text": SourceDocumentText,
}


def _accepts(model: type[BaseModel], value: Any) -> bool:
    try:
        model.model_validate(value)
    except ValidationError:
        return False
    return True


def _base(name: str) -> Any:
    file, _, key = name.partition("#")
    document = load_json(FIXTURES / file)
    return document[key] if key else document


def _mutated(case: dict[str, Any]) -> Any:
    document = copy.deepcopy(_base(case["base"]))
    path = case["path"]
    if not path:
        return document
    node = document
    for key in path[:-1]:
        node = node[key]
    if case.get("delete"):
        del node[path[-1]]
    else:
        node[path[-1]] = case["value"]
    return document


def test_the_corpus_covers_every_zone_a_contract() -> None:
    primitives = {case["contract"] for case in CORPUS["primitives"]}
    documents = {case["contract"] for case in CORPUS["documents"]}
    assert set(READERS) <= primitives | {"source-document-text"}
    assert set(READERS) == documents


def test_every_named_primitive_agrees_with_zod() -> None:
    disagreements: list[str] = []
    checked = 0
    for case in CORPUS["primitives"]:
        if case["contract"] in NOT_ZONE_A:
            continue
        module = importlib.import_module(f"zone_a.contracts.{case['contract'].replace('-', '_')}")
        model = getattr(module, case["definition"])
        assert issubclass(model, RootModel), case["definition"]
        checked += 1
        if _accepts(model, case["value"]) != case["accepted"]:
            disagreements.append(f"{case['contract']} {case['definition']} {case['variant']}")
    assert checked > 300
    assert disagreements == []


def test_every_document_agrees_with_zod() -> None:
    disagreements = [
        f"{case['contract']} {case['base']} {case['mutation']}"
        for case in CORPUS["documents"]
        if _accepts(READERS[case["contract"]], _mutated(case)) != case["accepted"]
    ]
    assert disagreements == []
    assert any(not case["accepted"] for case in CORPUS["documents"])
