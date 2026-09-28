"""The agent's schema validator gives Zod's verdict on every value of the contract verdict corpus.

The agent validates every tool answer against its vendored ``query-tools`` schema, and its own
turn record against ``agent-turn``, with ``jsonschema``, whose ``pattern`` is Python's ``re``.
Read as it is, ``re`` let ``$`` match before a final newline and ``\\d`` any Unicode digit, so a
hash with a newline after it, or a timestamp, passed here and failed the service's own contract
(audit C-7; the #145 review, L2-d). ``contract.ecma_pattern`` reads a pattern as ECMA-262 does,
and ``test/fixtures/contracts/contract-verdicts.json`` (``scripts/contracts/contract-verdicts.ts``)
carries Zod's verdict on each named definition of the two contracts the agent vendors, bent the
ways the languages have disagreed. Every verdict must be reproduced. A mismatch is reported by
contract, definition and variant, never by value.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from verifiable_answer_agent.contract import (
    EcmaDraft202012Validator,
    ecma_pattern,
    load_agent_turn_schema,
    load_schema,
)

from .fake_query_service import REPOSITORY_ROOT

CORPUS = json.loads(
    (REPOSITORY_ROOT / "test" / "fixtures" / "contracts" / "contract-verdicts.json").read_text(
        encoding="utf-8"
    )
)

VENDORED = {"query-tools": load_schema, "agent-turn": load_agent_turn_schema}


def _accepts(contract: str, definition: str, value: Any) -> bool:
    schema = VENDORED[contract]()
    subschema = {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]}
    return not list(EcmaDraft202012Validator(subschema).iter_errors(value))


def test_every_value_of_the_vendored_contracts_agrees_with_zod() -> None:
    cases = [case for case in CORPUS["primitives"] if case["contract"] in VENDORED]
    assert {case["contract"] for case in cases} == set(VENDORED)
    disagreements = [
        f"{case['contract']} {case['definition']} {case['variant']}"
        for case in cases
        if _accepts(case["contract"], case["definition"], case["value"]) != case["accepted"]
    ]
    assert disagreements == []


# The two values the review of #145 named (L2-d), each accepted by the plain validator.
@pytest.mark.parametrize(
    ("definition", "value"),
    [("Sha256Hex", "a" * 64 + "\n"), ("IsoDateTime", "2026-09-28T12:00:00Z\n")],
)
def test_a_trailing_newline_is_refused(definition: str, value: str) -> None:
    assert _accepts("query-tools", definition, value.rstrip("\n"))
    assert not _accepts("query-tools", definition, value)


def test_the_pattern_is_read_as_ecma_262_reads_it() -> None:
    assert ecma_pattern("^a$").search("a")
    assert not ecma_pattern("^a$").search("a\n")
    # Escaped or inside a class, `$` and `.` are the characters themselves.
    assert ecma_pattern(r"^[$.]\$\.$").search("$$.")
    assert not ecma_pattern("^.$").search("\r")
    assert not ecma_pattern("^.$").search("\u2028")
    assert ecma_pattern("^.$").search("x")
    assert not ecma_pattern(r"^\d$").search("\u0663")
