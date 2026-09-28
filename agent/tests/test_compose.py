"""Composition is a function of the tool results. The model contributes one labelled part."""

from __future__ import annotations

from verifiable_answer_agent.compose import MAX_BLOCKS, compose, product_facts
from verifiable_answer_agent.contract import ToolResult, sha256_hex, validate_tool_output

from .fake_query_service import BUNDLE_ID, VERSION_ID, load_sections

SECTIONS = load_sections()
KEYS = ["smpc.4.3", "smpc.4.4", "smpc.4.2.posology"]


def results_for(keys: list[str]) -> list[ToolResult]:
    return [validate_tool_output("get_section", SECTIONS[key].payload) for key in keys]


def test_the_same_results_compose_to_the_same_answer() -> None:
    first = compose(results_for(KEYS), "some remark")
    second = compose(results_for(KEYS), "some remark")
    assert first == second


def test_block_order_follows_result_order_and_ids_are_positional() -> None:
    composition = compose(results_for(KEYS), "")
    assert [block.citation.source_key for block in composition.draft.blocks] == KEYS
    assert [block.block_id for block in composition.draft.blocks] == [
        "block-01",
        "block-02",
        "block-03",
    ]


def test_every_block_is_verbatim_from_its_tool_result_with_its_citation() -> None:
    composition = compose(results_for(KEYS), "")
    for key, block in zip(KEYS, composition.draft.blocks, strict=True):
        payload = SECTIONS[key].payload
        assert block.text == payload["text"]
        assert block.citation.source_key == payload["sourceKey"]
        assert block.citation.narrative_div_sha256 == payload["narrativeDivSha256"]
        assert block.citation.bundle_id == payload["document"]["bundleId"]
        assert block.citation.version_id == payload["document"]["versionId"]


def test_the_assistants_words_go_only_into_the_assistant_part() -> None:
    remark = "A remark the model wrote."
    composition = compose(results_for(KEYS), remark)
    assert composition.draft.assistant.text == remark
    assert all(remark not in block.text for block in composition.draft.blocks)


def test_the_same_section_twice_is_one_block() -> None:
    composition = compose(results_for(["smpc.4.3", "smpc.4.3", "smpc.4.4"]), "")
    assert [block.citation.source_key for block in composition.draft.blocks] == [
        "smpc.4.3",
        "smpc.4.4",
    ]
    assert composition.sections_used == 2


def test_an_unavailable_result_is_dropped_and_counted_never_rendered() -> None:
    unavailable = ToolResult(tool="get_section", value=None, reason="schema-invalid")
    composition = compose([*results_for(["smpc.4.3"]), unavailable], "")
    assert composition.sections_used == 1
    assert composition.sections_dropped == 1
    assert len(composition.draft.blocks) == 1


def test_a_result_from_another_tool_never_becomes_a_block() -> None:
    provenance = ToolResult(tool="get_provenance", value={"anything": True}, reason=None)
    composition = compose([provenance], "")
    assert composition.draft.blocks == ()
    assert composition.sections_dropped == 1


def test_each_block_carries_the_hash_of_its_own_xhtml_and_its_text_hash() -> None:
    # The post-check holds the checksum a reader is shown to the XHTML the tool returned.
    for key, block in zip(KEYS, compose(results_for(KEYS), "").draft.blocks, strict=True):
        payload = SECTIONS[key].payload
        assert block.div_sha256 == sha256_hex(payload["div"])
        assert block.citation.normalized_text_sha256 == payload["normalizedTextSha256"]


def _find_product(name: str, language: str, version_id: str = VERSION_ID) -> ToolResult:
    return ToolResult(
        tool="find_product",
        value={
            "products": [
                {
                    "document": {
                        "bundleId": BUNDLE_ID,
                        "versionId": version_id,
                        "lastUpdated": "2026-09-19T00:00:00Z",
                    },
                    "productName": name,
                    "identifiers": [],
                    "language": language,
                    "sections": [],
                }
            ],
            "truncated": False,
        },
        reason=None,
    )


def test_the_product_and_language_come_from_this_turns_lookup_of_the_same_version() -> None:
    facts = product_facts([_find_product("Synthetic 10 mg tablets", "en")])
    (block,) = compose(results_for(["smpc.4.3"]), "", facts).draft.blocks
    assert (block.citation.product_name, block.citation.language) == (
        "Synthetic 10 mg tablets",
        "en",
    )


def test_a_lookup_of_another_version_or_two_disagreeing_lookups_name_nothing() -> None:
    other_version = product_facts([_find_product("Synthetic 10 mg tablets", "en", "99")])
    disagreeing = product_facts(
        [_find_product("Synthetic 10 mg tablets", "en"), _find_product("Other", "de")]
    )
    for facts in (other_version, disagreeing, {}):
        (block,) = compose(results_for(["smpc.4.3"]), "", facts).draft.blocks
        assert (block.citation.product_name, block.citation.language) == (None, None)


def test_at_most_max_blocks_are_shown_and_the_rest_are_counted() -> None:
    keys = sorted(SECTIONS)[: MAX_BLOCKS + 3]
    composition = compose(results_for(keys), "")
    assert len(composition.draft.blocks) == MAX_BLOCKS
    assert composition.sections_not_shown == 3
    assert composition.draft.sections_not_shown == 3
