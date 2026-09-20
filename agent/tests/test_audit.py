"""The audit record is counts and identities. It is never a way to read a label."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from verifiable_answer_agent.audit import (
    FORBIDDEN_KEYS,
    NarrativeLeakError,
    ToolCallRecord,
    emit,
    turn_record,
)
from verifiable_answer_agent.postcheck import ChunkCheck, post_check

from .test_postcheck import verification
from .test_render import CHECKS, DRAFT

ANSWER = post_check(DRAFT, CHECKS)
TOOLS = (
    ToolCallRecord(tool="get_section", outcome="ok", duration_ms=12, result_count=1),
    ToolCallRecord(tool="verify_quote", outcome="ok", duration_ms=8, result_count=1),
)


def record() -> Any:
    return turn_record(
        service_version="agent/0.1.0",
        principal="urn:reviewer:synthetic-01",
        turn_id="synthetic-turn",
        answer=ANSWER,
        tools=TOOLS,
        sections_dropped=1,
        duration_ms=42,
        at=datetime(2026, 9, 20, 10, 30, 0, tzinfo=UTC),
    )


def emitted() -> dict[str, Any]:
    stream = io.StringIO()
    emit(record(), stream)
    line = stream.getvalue()
    assert line.endswith("\n")
    assert line.count("\n") == 1
    parsed: dict[str, Any] = json.loads(line)
    return parsed


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
        turn_id="t",
        answer=clean,
        tools=(),
        sections_dropped=0,
        duration_ms=1,
    ).model_dump(by_alias=True, mode="json")
    assert payload["spansFlagged"] == 0
    assert payload["flags"] == []
    assert payload["at"].endswith("Z")
