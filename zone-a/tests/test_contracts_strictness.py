"""Strictness is proven per model, not assumed from the generator's reputation.

ADR 0002: "Objects are strict (unknown keys reject) so content cannot be smuggled in unnamed
fields." The Type 2 Bundle and the FHIR resources inside it are the deliberate exception — FHIR
resources are open by nature, and ``src/contracts/canonical-bundle.ts`` models them with
``z.looseObject`` — and those must accept and *preserve* an unknown field, or Zone A would
silently drop content that the Bundle hash was taken over.

Both halves are checked behaviourally (does the model actually reject / accept?) and by count
(does the number of strict and open models equal the number of closed and open objects in the
schema?), so a schema object that lost its model, or a model that lost its config, is visible.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, RootModel, ValidationError

from .conftest import CONTRACT_SCHEMAS, load_json

# Every contract the index publishes that Zone A reads (scripts/generate_models.py), derived rather
# than listed, so a contract added to the index is checked here without an edit (audit C-8).
NOT_ZONE_A = frozenset({"agent-turn", "query-tools"})
CONTRACTS = [
    (entry["name"], entry["name"].replace("-", "_"))
    for entry in load_json(CONTRACT_SCHEMAS / "index.json")["contracts"]
    if entry["name"] not in NOT_ZONE_A
]

UNKNOWN_FIELD = "zoneAUnknownField"


def test_every_contract_zone_a_reads_has_its_generated_module() -> None:
    generated = Path(__file__).resolve().parents[1] / "src" / "zone_a" / "contracts"
    modules = sorted(path.stem for path in generated.glob("*.py") if path.stem != "__init__")
    assert modules == sorted(module for _, module in CONTRACTS)


def _count_objects(node: Any) -> tuple[int, int]:
    """Closed and open object schemas anywhere in a JSON Schema document.

    Closed is ``additionalProperties: false``; open is any other object schema (the generator
    emits ``additionalProperties: {}`` for a Zod ``looseObject``).
    """
    closed = 0
    open_ = 0
    if isinstance(node, dict):
        if node.get("type") == "object":
            if node.get("additionalProperties") is False:
                closed += 1
            else:
                open_ += 1
        for value in node.values():
            more_closed, more_open = _count_objects(value)
            closed += more_closed
            open_ += more_open
    elif isinstance(node, list):
        for value in node:
            more_closed, more_open = _count_objects(value)
            closed += more_closed
            open_ += more_open
    return closed, open_


def _models(module_name: str) -> list[type[BaseModel]]:
    module = importlib.import_module(f"zone_a.contracts.{module_name}")
    return [
        member
        for _, member in inspect.getmembers(module, inspect.isclass)
        if issubclass(member, BaseModel)
        and not issubclass(member, RootModel)
        and member.__module__ == module.__name__
    ]


def _forbids_extra(model: type[BaseModel]) -> bool:
    """Probe the model with nothing but an unknown field.

    No valid instance is needed: a strict model reports ``extra_forbidden`` for the unknown key
    alongside whatever ``missing`` errors the required fields produce, and an open model reports
    only the ``missing`` ones.
    """
    try:
        model.model_validate({UNKNOWN_FIELD: 1})
    except ValidationError as error:
        return any(
            issue["type"] == "extra_forbidden" and issue["loc"] == (UNKNOWN_FIELD,)
            for issue in error.errors()
        )
    return False


@pytest.mark.parametrize(("contract", "module_name"), CONTRACTS)
def test_model_strictness_matches_the_schema(contract: str, module_name: str) -> None:
    schema = load_json(CONTRACT_SCHEMAS / f"{contract}.schema.json")
    closed, open_ = _count_objects(schema)
    models = _models(module_name)
    strict = [model for model in models if _forbids_extra(model)]
    lenient = [model for model in models if not _forbids_extra(model)]
    assert len(strict) == closed, (
        f"{contract}: {closed} closed object schemas but {len(strict)} models reject an "
        f"unknown field: {sorted(model.__name__ for model in strict)}"
    )
    assert len(lenient) == open_, (
        f"{contract}: {open_} open object schemas but {len(lenient)} models accept an "
        f"unknown field: {sorted(model.__name__ for model in lenient)}"
    )


@pytest.mark.parametrize(("_contract", "module_name"), CONTRACTS)
def test_every_model_declares_its_extra_policy(_contract: str, module_name: str) -> None:
    # An inherited default would make strictness an accident of pydantic's settings rather than
    # a property the schema forced.
    for model in _models(module_name):
        assert model.model_config.get("extra") in ("forbid", "allow"), model.__name__


def test_the_open_models_are_only_the_fhir_ones() -> None:
    """The exception list is named, so a new open object cannot appear unnoticed."""
    open_models = {
        model.__name__
        for model in _models("canonical_submission")
        if model.model_config.get("extra") == "allow"
    }
    assert open_models == {"CanonicalBundle", "EntryItem", "Resource"}


def test_an_open_model_preserves_the_unknown_field() -> None:
    """Accepting is not enough: the Bundle hash is taken over fields Zone A never models."""
    from zone_a.contracts.canonical_submission import Resource

    parsed = Resource.model_validate({"resourceType": "Composition", UNKNOWN_FIELD: [1, 2]})
    dumped = parsed.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert dumped[UNKNOWN_FIELD] == [1, 2]


def test_a_strict_model_rejects_a_smuggled_field(submission: Any) -> None:
    from zone_a.contracts.canonical_submission import CanonicalSubmission

    with pytest.raises(ValidationError):
        CanonicalSubmission.model_validate({**submission, UNKNOWN_FIELD: "x"})
