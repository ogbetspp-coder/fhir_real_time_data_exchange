"""The published contract is the only thing this agent shares, so it is read, not assumed."""

from __future__ import annotations

import itertools
import json
import time
from pathlib import Path

import pytest

from verifiable_answer_agent.contract import _OUTPUT_DEF as OUTPUT_DEF
from verifiable_answer_agent.contract import (
    AGENT_TURN_RESOURCE,
    CONTRACT_RESOURCE,
    VERIFY_QUOTE_MAX_UTF16,
    chunk_spans,
    load_agent_turn_schema,
    load_schema,
    split_for_verification,
    utf16_length,
    validate_tool_output,
)
from verifiable_answer_agent.quote_edge import (
    edge_after,
    edge_before,
    has_scanner_marker,
    locate_quote,
    number_before,
    number_from,
)

from .fake_query_service import REPOSITORY_ROOT, load_sections, long_section, quote_edge_cases

VENDORED = Path(str(REPOSITORY_ROOT / "agent" / "src" / "verifiable_answer_agent" / "contracts"))


@pytest.mark.parametrize("name", [CONTRACT_RESOURCE, AGENT_TURN_RESOURCE])
def test_each_vendored_contract_is_the_published_one(name: str) -> None:
    published = (REPOSITORY_ROOT / "contracts" / "generated" / name).read_text(encoding="utf-8")
    vendored = (VENDORED / name).read_text(encoding="utf-8")
    assert vendored == published


def test_the_two_schemas_are_the_versions_the_agent_was_adapted_to() -> None:
    assert load_schema()["$id"].endswith("/query-tools/4.1.0/schema.json")
    assert load_agent_turn_schema()["$id"].endswith("/agent-turn/1.2.0/schema.json")


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


def test_a_token_longer_than_the_window_is_one_longer_chunk_not_a_cut_inside_it() -> None:
    # Any cut inside a word is a cut under the quote-edge rule, so a certain no-match. The bound:
    # the chunk runs to the first acceptable cut after the window, and no further.
    assert split_for_verification("x" * 25, limit=10) == ("x" * 25,)
    assert split_for_verification("a " + "x" * 25 + " b", limit=10) == ("a", "x" * 25, "b")


def test_an_astral_character_is_measured_as_two_units_and_never_split() -> None:
    # U+1D400 MATHEMATICAL BOLD CAPITAL A costs two UTF-16 code units; a window of three fits one
    # and the space after it, not two. (A letter: an emoji is a symbol, which the quote-edge
    # rule reads as a sign binding what follows.)
    text = "\U0001d400 \U0001d400 \U0001d400"
    chunks = split_for_verification(text, limit=3)
    assert chunks == ("\U0001d400", "\U0001d400", "\U0001d400")
    assert all(utf16_length(chunk) <= 3 for chunk in chunks)


def test_a_cut_never_falls_inside_a_space_grouped_number() -> None:
    # The last space in a window of 19 is inside "1 000 000": "Give up to 1 000" | "000 IU
    # daily." is what the splitter cut before 2026-09-22, and the service refuses both halves.
    text = "Give up to 1 000 000 IU daily."
    chunks = split_for_verification(text, limit=19)
    assert chunks == ("Give up to", "1 000 000 IU daily.")
    assert all(locate_quote(text, chunk) is not None for chunk in chunks)


def test_the_spans_are_where_the_chunks_sit_and_tile_the_block() -> None:
    # The post-check holds each match to these offsets, so they must be exact: code points into
    # the block, in order, nothing between them but the spaces the cuts dropped.
    text = "  " + " ".join(f"word{index:04d}" for index in range(300)) + " "
    spans = chunk_spans(text, limit=100)
    assert spans[0][0] == 2
    assert spans[-1][1] == len(text) - 1
    for (_, end), (start, _) in itertools.pairwise(spans):
        assert end < start
        assert set(text[end:start]) == {" "}
    assert tuple(text[start:end] for start, end in spans) == split_for_verification(text, 100)


def test_the_chunks_are_evened_out_rather_than_ending_in_a_sliver() -> None:
    # A block just over one window used to be one full chunk and a few words; a few words are the
    # chunk most likely to occur earlier in the section too (review of PR #129, L7).
    text = " ".join(f"word{index:04d}" for index in range(24))
    assert utf16_length(text) == 215
    # Cut at the last space of a 200-unit window, the second chunk was "word0022 word0023".
    chunks = split_for_verification(text, limit=200)
    assert len(chunks) == 2
    assert min(len(chunk) for chunk in chunks) > len("word0022 word0023")
    assert abs(len(chunks[0]) - len(chunks[1])) <= 10
    long_chunks = split_for_verification(long_section().text)
    lengths = [utf16_length(chunk) for chunk in long_chunks]
    assert max(lengths) - min(lengths) < VERIFY_QUOTE_MAX_UTF16 // 4


def test_at_the_real_bound_every_chunk_is_found_where_it_was_cut() -> None:
    # The exact-offset rule fails closed when a chunk's text also occurs earlier in its section
    # (review of PR #129). Over every text the fixtures hold, at the contract's bound, no sendable
    # chunk does. (At windows of a few hundred units some would: short chunks repeat.)
    texts = [section["text"] for section in quote_edge_cases()["sections"]]
    texts.append(long_section().text)
    texts.extend(section.text for section in load_sections().values())
    checked = 0
    for text in texts:
        for start, end in chunk_spans(text):
            chunk = text[start:end]
            if utf16_length(chunk) > VERIFY_QUOTE_MAX_UTF16 or has_scanner_marker(chunk):
                continue
            checked += 1
            assert locate_quote(text, chunk) == (start, end), chunk[:60]
    assert checked > 50


def test_a_pathological_block_splits_in_linear_time() -> None:
    # 200,000 code points of "( ". The splitter used to walk back up to 256 steps from every
    # space, give up, count the walk as a sign, and so refuse every cut and re-measure the rest
    # each time (21.9 s, audit AG-12). Read once, exactly, no sign stands before any of these
    # spaces — which is what the service reads too — so each window is cut at its last space.
    text = "( " * 100_000
    started = time.monotonic()
    spans = chunk_spans(text)
    assert time.monotonic() - started < 5.0
    assert len(spans) == 100
    assert all(end - start <= VERIFY_QUOTE_MAX_UTF16 for start, end in spans)


def test_a_cut_never_parts_a_spaced_comparator_from_its_number() -> None:
    # "…CrCl ≥" | "30 ml/min." was the other: the last space in a window of 29 is after "≥",
    # and the second half has lost its comparator.
    text = "Reduce the dose when CrCl ≥ 30 ml/min."
    chunks = split_for_verification(text, limit=29)
    # Evened out, the cut moves to before "CrCl"; it still never falls after the comparator.
    assert chunks == ("Reduce the dose when", "CrCl ≥ 30 ml/min.")
    assert not any(chunk.endswith("≥") for chunk in chunks)
    assert all(locate_quote(text, chunk) is not None for chunk in chunks)


def test_every_chunk_of_every_worked_example_is_one_the_service_confirms() -> None:
    # Every text the query service's own rule was exported over, split at every window width:
    # each chunk within the bound is a match under that rule, and a chunk over the bound exists
    # only where the window held no acceptable cut.
    # A text carrying a table's grid markers is left out: the service refuses any quote holding
    # one (``invalid-request``), so ``postcheck.run_post_check`` does not send such a chunk and
    # flags the block ``table-not-quotable`` (tests/test_postcheck.py); quoting a table is the
    # publishing step's work (roadmap 3a, PR 5).
    texts = [
        section["text"]
        for section in quote_edge_cases()["sections"]
        if not any(0xFDD0 <= ord(character) <= 0xFDEF for character in section["text"])
    ]
    texts.append(long_section().text)
    for text in texts:
        for limit in [*range(4, min(len(text), 120)), VERIFY_QUOTE_MAX_UTF16]:
            position = 0
            for chunk in split_for_verification(text, limit=limit):
                start = text.index(chunk, position)
                position = start + len(chunk)
                if utf16_length(chunk) <= limit:
                    assert locate_quote(text, chunk) is not None, (limit, chunk)
                    continue
                # Over the bound: no space inside the window was a cut both sides accept.
                for offset in range(1, min(limit + 1, len(chunk) - 1)):
                    at = start + offset
                    if text[at] == " ":
                        assert not (
                            edge_after(text, at, number_before(text, at))
                            and edge_before(text, at + 1, number_from(text, at + 1))
                        ), (limit, chunk, offset)


def test_the_fixture_the_fake_service_is_built_from_is_the_repositorys_own() -> None:
    fixture = REPOSITORY_ROOT / "test" / "fixtures" / "contracts" / "canonical-submission.json"
    submission = json.loads(fixture.read_text(encoding="utf-8"))
    assert submission["approval"]["approverId"].startswith("urn:reviewer:synthetic")
