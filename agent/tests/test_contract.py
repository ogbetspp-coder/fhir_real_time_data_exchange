"""The published contract is the only thing this agent shares, so it is read, not assumed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifiable_answer_agent.contract import _OUTPUT_DEF as OUTPUT_DEF
from verifiable_answer_agent.contract import (
    AGENT_TURN_RESOURCE,
    CONTRACT_RESOURCE,
    VERIFY_QUOTE_MAX_UTF16,
    load_agent_turn_schema,
    load_schema,
    split_for_verification,
    validate_tool_output,
)

from .fake_query_service import REPOSITORY_ROOT, load_sections

VENDORED = Path(str(REPOSITORY_ROOT / "agent" / "src" / "verifiable_answer_agent" / "contracts"))


@pytest.mark.parametrize("name", [CONTRACT_RESOURCE, AGENT_TURN_RESOURCE])
def test_each_vendored_contract_is_the_published_one(name: str) -> None:
    published = (REPOSITORY_ROOT / "contracts" / "generated" / name).read_text(encoding="utf-8")
    vendored = (VENDORED / name).read_text(encoding="utf-8")
    assert vendored == published


def test_the_two_schemas_are_the_versions_the_agent_was_adapted_to() -> None:
    assert load_schema()["$id"].endswith("/query-tools/2.0.0/schema.json")
    assert load_agent_turn_schema()["$id"].endswith("/agent-turn/1.0.0/schema.json")


def test_not_entitled_is_no_longer_an_error_code_a_caller_can_see() -> None:
    # query-tools 2.0.0: outside the entitlement the service answers document-not-found. The
    # agent never branched on the code, so this pins the contract, not agent behaviour.
    assert "not-entitled" not in load_schema()["$defs"]["QueryErrorCode"]["enum"]


def test_each_tools_output_type_is_the_one_the_contract_names() -> None:
    tools = load_schema()["$defs"]["QueryTools"]["properties"]["tools"]["properties"]
    for tool, definition in OUTPUT_DEF.items():
        assert tools[tool]["properties"]["output"]["$ref"] == f"#/$defs/{definition}"
    assert sorted(OUTPUT_DEF) == sorted(tools)


def test_the_verify_quote_bound_is_the_contracts_bound() -> None:
    quote = load_schema()["$defs"]["VerifyQuoteInput"]["properties"]["quote"]
    assert quote["maxLength"] == VERIFY_QUOTE_MAX_UTF16


def test_a_valid_section_validates_and_an_altered_one_does_not() -> None:
    section = next(iter(load_sections().values())).payload
    assert validate_tool_output("get_section", section).available

    missing = {key: value for key, value in section.items() if key != "narrativeDivSha256"}
    refused = validate_tool_output("get_section", missing)
    assert not refused.available
    assert refused.reason == "schema-invalid"

    wrong_notice = dict(section) | {"contentNotice": "anything-else"}
    assert not validate_tool_output("get_section", wrong_notice).available

    extra = dict(section) | {"summary": "a field the contract forbids"}
    assert not validate_tool_output("get_section", extra).available


def test_find_product_requires_truncated_and_accepts_either_value() -> None:
    output = load_schema()["$defs"]["FindProductOutput"]
    assert "truncated" in output["required"]
    for truncated in (False, True):
        assert validate_tool_output(
            "find_product", {"products": [], "truncated": truncated}
        ).available
    refused = validate_tool_output("find_product", {"products": []})
    assert not refused.available
    assert refused.reason == "schema-invalid"


@pytest.mark.parametrize("payload", [None, "a string", 7, [1, 2, 3]])
def test_anything_that_is_not_an_object_is_unavailable(payload: object) -> None:
    result = validate_tool_output("get_section", payload)
    assert not result.available
    assert result.reason == "not-an-object"


def test_every_chunk_is_a_contiguous_substring_within_the_bound() -> None:
    text = " ".join(f"word{index:04d}" for index in range(900))
    chunks = split_for_verification(text, limit=100)
    assert len(chunks) > 1
    position = 0
    for chunk in chunks:
        assert len(chunk.encode("utf-16-le")) // 2 <= 100
        found = text.find(chunk, position)
        assert found >= 0, "a chunk is not a contiguous substring of the block"
        position = found
    assert "".join(chunks.__iter__()).replace(" ", "") == text.replace(" ", "")


def test_the_splitter_is_deterministic_and_never_emits_an_empty_chunk() -> None:
    text = " ".join(f"token{index}" for index in range(500))
    first = split_for_verification(text, limit=64)
    assert first == split_for_verification(text, limit=64)
    assert all(chunk for chunk in first)
    assert not any(chunk.startswith(" ") or chunk.endswith(" ") for chunk in first)


def test_a_token_longer_than_the_window_is_cut_at_the_bound() -> None:
    chunks = split_for_verification("x" * 25, limit=10)
    assert chunks == ("x" * 10, "x" * 10, "x" * 5)


def test_an_astral_character_is_measured_as_two_units_and_never_split() -> None:
    # U+1F600 costs two UTF-16 code units; a window of three fits one, not two.
    text = "\U0001f600\U0001f600\U0001f600"
    chunks = split_for_verification(text, limit=3)
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 3 for chunk in chunks)
    assert "".join(chunks) == text


def test_the_fixture_the_fake_service_is_built_from_is_the_repositorys_own() -> None:
    fixture = REPOSITORY_ROOT / "test" / "fixtures" / "contracts" / "canonical-submission.json"
    submission = json.loads(fixture.read_text(encoding="utf-8"))
    assert submission["approval"]["approverId"].startswith("urn:reviewer:synthetic")
