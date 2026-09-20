"""The audit record is counts and identities. It is never a way to read a label."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from verifiable_answer_agent.audit import (
    FORBIDDEN_KEYS,
    MAX_TOOL_CALLS,
    PRINCIPAL_PATTERN,
    TOKEN_PATTERN,
    UUID_PATTERN,
    NarrativeLeakError,
    ToolCallRecord,
    emit,
    turn_record,
)
from verifiable_answer_agent.contract import load_agent_turn_schema
from verifiable_answer_agent.postcheck import ChunkCheck, post_check

from .test_postcheck import verification
from .test_render import CHECKS, DRAFT

ANSWER = post_check(DRAFT, CHECKS)
TOOLS = (
    ToolCallRecord(tool="get_section", outcome="ok", duration_ms=12, result_count=1),
    ToolCallRecord(tool="verify_quote", outcome="ok", duration_ms=8, result_count=1),
)
TURN_ID = "0f6b3a2e-4c1d-4e8f-9a7b-1c2d3e4f5a6b"


def record(**overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "service_version": "agent/0.1.0",
        "principal": "urn:reviewer:synthetic-01",
        "turn_id": TURN_ID,
        "answer": ANSWER,
        "tools": TOOLS,
        "sections_dropped": 1,
        "duration_ms": 42,
        "at": datetime(2026, 9, 20, 10, 30, 0, tzinfo=UTC),
    }
    return turn_record(**(arguments | overrides))


def agent_turn_validator() -> Draft202012Validator:
    schema = load_agent_turn_schema()
    Draft202012Validator.check_schema(schema)
    # No format checker, as in contract.py: every ``format`` here has a ``pattern`` beside it.
    return Draft202012Validator(schema)


def emitted() -> dict[str, Any]:
    stream = io.StringIO()
    emit(record(), stream)
    line = stream.getvalue()
    assert line.endswith("\n")
    assert line.count("\n") == 1
    parsed: dict[str, Any] = json.loads(line)
    return parsed


def test_an_emitted_record_validates_against_the_published_agent_turn_contract() -> None:
    validator = agent_turn_validator()
    errors = sorted(validator.iter_errors(emitted()), key=lambda error: list(error.path))
    assert not errors, [error.message for error in errors]


def test_a_record_with_no_tool_calls_and_no_flags_also_validates() -> None:
    clean = post_check(
        DRAFT,
        {
            block.block_id: [ChunkCheck(0, verification(source_key=block.citation.source_key))]
            for block in DRAFT.blocks
        },
    )
    payload = record(answer=clean, tools=(), sections_dropped=0).model_dump(
        by_alias=True, mode="json"
    )
    assert not list(agent_turn_validator().iter_errors(payload))


def test_the_record_carries_exactly_the_contracts_fields() -> None:
    contract = load_agent_turn_schema()["$defs"]["AgentTurnRecord"]
    payload = emitted()
    assert sorted(payload) == sorted(contract["required"])
    assert sorted(payload) == sorted(contract["properties"])
    tool_call = load_agent_turn_schema()["$defs"]["AgentToolCall"]
    for tool in payload["tools"]:
        assert sorted(tool) == sorted(tool_call["required"])


def test_the_patterns_the_model_enforces_are_the_contracts() -> None:
    defs = load_agent_turn_schema()["$defs"]
    assert defs["Token"]["pattern"] == TOKEN_PATTERN
    assert defs["PrincipalId"]["pattern"] == PRINCIPAL_PATTERN
    assert defs["Uuid"]["pattern"] == UUID_PATTERN
    assert defs["AgentTurnRecord"]["properties"]["tools"]["maxItems"] == MAX_TOOL_CALLS


def test_a_turn_id_that_is_not_a_uuid_makes_no_record() -> None:
    with pytest.raises(ValidationError, match="turn_id"):
        record(turn_id="synthetic-turn")


def test_a_tool_outside_the_four_makes_no_record() -> None:
    with pytest.raises(ValidationError, match="tool"):
        ToolCallRecord(tool="write_section", outcome="ok", duration_ms=1, result_count=1)  # type: ignore[arg-type]


def test_a_flag_outside_the_six_makes_no_record() -> None:
    from verifiable_answer_agent.audit import TurnAuditRecord

    with pytest.raises(ValidationError, match="flags"):
        TurnAuditRecord(
            service_version="agent/0.1.0",
            at="2026-09-20T10:30:00Z",
            principal="urn:reviewer:synthetic-01",
            turn_id=TURN_ID,
            tools=(),
            spans_verified=0,
            spans_flagged=0,
            sections_dropped=0,
            flags=("looks-fine",),  # type: ignore[arg-type]
            duration_ms=0,
        )


def test_more_tool_calls_than_the_contract_allows_makes_no_record_rather_than_a_cut_one() -> None:
    too_many = tuple(
        ToolCallRecord(tool="verify_quote", outcome="ok", duration_ms=1, result_count=1)
        for _ in range(MAX_TOOL_CALLS + 1)
    )
    with pytest.raises(ValidationError, match="tools"):
        record(tools=too_many)
    assert record(tools=too_many[:-1]).model_dump(by_alias=True, mode="json")["tools"]


def test_the_record_counts_what_happened() -> None:
    payload = emitted()
    assert payload["service"] == "ema-flow-agent"
    assert payload["principal"] == "urn:reviewer:synthetic-01"
    assert payload["spansVerified"] == 1
    assert payload["spansFlagged"] == 1
    assert payload["sectionsDropped"] == 1
    assert payload["flags"] == ["no-match"]
    assert payload["durationMs"] == 42
    assert [tool["tool"] for tool in payload["tools"]] == ["get_section", "verify_quote"]


def test_the_record_carries_no_narrative_from_the_answer_it_describes() -> None:
    line = json.dumps(emitted())
    for block in ANSWER.blocks:
        assert block.text not in line
        assert block.citation.source_key not in line
    assert ANSWER.assistant.text not in line


def test_no_key_at_any_depth_is_a_forbidden_one() -> None:
    def keys(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [*value, *(key for nested in value.values() for key in keys(nested))]
        if isinstance(value, list):
            return [key for item in value for key in keys(item)]
        return []

    assert not [key for key in keys(emitted()) if key.lower() in FORBIDDEN_KEYS]


def test_emit_refuses_a_record_that_has_grown_a_forbidden_key() -> None:
    class Leaky:
        def model_dump(self, **_: Any) -> dict[str, Any]:
            return {"service": "ema-flow-agent", "tools": [{"tool": "get_section", "quote": "x"}]}

    with pytest.raises(NarrativeLeakError, match="quote"):
        emit(Leaky(), io.StringIO())  # type: ignore[arg-type]


def test_a_tool_record_cannot_carry_the_arguments_it_was_called_with() -> None:
    with pytest.raises(ValidationError):
        ToolCallRecord(
            tool="verify_quote",
            outcome="ok",
            duration_ms=1,
            result_count=1,
            arguments={"quote": "a sentence"},  # type: ignore[call-arg]
        )


def test_a_fully_verified_turn_reports_no_flags() -> None:
    clean = post_check(
        DRAFT,
        {
            block.block_id: [ChunkCheck(0, verification(source_key=block.citation.source_key))]
            for block in DRAFT.blocks
        },
    )
    payload = turn_record(
        service_version="agent/0.1.0",
        principal="urn:reviewer:synthetic-01",
        turn_id=TURN_ID,
        answer=clean,
        tools=(),
        sections_dropped=0,
        duration_ms=1,
    ).model_dump(by_alias=True, mode="json")
    assert payload["spansFlagged"] == 0
    assert payload["flags"] == []
    assert payload["at"].endswith("Z")
