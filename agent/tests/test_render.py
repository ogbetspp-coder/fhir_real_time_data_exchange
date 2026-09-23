"""Both surfaces show the same four things: status, quotation, citation, assistant part."""

from __future__ import annotations

from typing import Any

from verifiable_answer_agent.answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from verifiable_answer_agent.postcheck import ChunkCheck, post_check
from verifiable_answer_agent.render import (
    A2UI_CATALOG_ID,
    A2UI_VERSION,
    ASSISTANT_LABEL,
    UNVERIFIED_LABEL,
    VERIFIED_LABEL,
    render,
    render_a2ui,
    render_text,
)

from .test_postcheck import CITATION, verification

BLOCKS = (
    QuotedBlock(block_id="block-01", citation=CITATION, text="a first synthetic span"),
    QuotedBlock(
        block_id="block-02",
        citation=Citation(
            bundle_id="synthetic-smpc",
            version_id="1",
            source_key="smpc.4.4",
            narrative_div_sha256="3" * 64,
        ),
        text="a second synthetic span",
    ),
)
DRAFT = DraftAnswer(blocks=BLOCKS, assistant=AssistantPart(text="the assistant's remark"))
CHECKS = {
    "block-01": [ChunkCheck(0, verification())],
    "block-02": [ChunkCheck(0, verification(result="no-match", source_key="smpc.4.4"))],
}
ANSWER = post_check(DRAFT, CHECKS)


def components(envelopes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    update = next(envelope for envelope in envelopes if "updateComponents" in envelope)
    return {item["id"]: item for item in update["updateComponents"]["components"]}


def test_the_envelopes_are_the_two_the_specification_defines() -> None:
    envelopes = render_a2ui(ANSWER)
    assert [sorted(set(envelope) - {"version"}) for envelope in envelopes] == [
        ["createSurface"],
        ["updateComponents"],
    ]
    assert {envelope["version"] for envelope in envelopes} == {A2UI_VERSION}
    create = envelopes[0]["createSurface"]
    assert create["catalogId"] == A2UI_CATALOG_ID
    assert create["surfaceId"] == envelopes[1]["updateComponents"]["surfaceId"]


def test_the_component_tree_has_a_root_and_every_child_reference_resolves() -> None:
    by_id = components(render_a2ui(ANSWER))
    assert "root" in by_id
    for component in by_id.values():
        for child in component.get("children", []):
            assert child in by_id, f"{component['id']} references a component that does not exist"
        if "child" in component:
            assert component["child"] in by_id


def test_every_block_is_a_card_carrying_its_quote_status_and_citation() -> None:
    by_id = components(render_a2ui(ANSWER))
    for block in ANSWER.blocks:
        card = by_id[f"{block.block_id}_card"]
        assert card["component"] == "Card"
        body = by_id[card["child"]]
        assert body["component"] == "Column"
        assert by_id[f"{block.block_id}_quote"]["text"] == block.text
        citation = by_id[f"{block.block_id}_citation"]["text"]
        for field in (
            block.citation.bundle_id,
            block.citation.version_id,
            block.citation.source_key,
            block.citation.narrative_div_sha256,
        ):
            assert field in citation


def test_a_flagged_block_says_so_on_the_card() -> None:
    by_id = components(render_a2ui(ANSWER))
    assert by_id["block-01_status"]["text"] == VERIFIED_LABEL
    flagged = by_id["block-02_status"]["text"]
    assert flagged.startswith(UNVERIFIED_LABEL)
    assert "no-match" in flagged


def test_the_assistants_words_are_a_separate_labelled_part() -> None:
    by_id = components(render_a2ui(ANSWER))
    assert by_id["assistant_label"]["text"] == ASSISTANT_LABEL
    assert by_id["assistant_text"]["text"] == ANSWER.assistant.text
    quote_ids = {f"{block.block_id}_quote" for block in ANSWER.blocks}
    assert "assistant_text" not in quote_ids


def test_the_plain_text_surface_carries_the_same_four_things() -> None:
    text = render_text(ANSWER)
    for block in ANSWER.blocks:
        assert block.text in text
        assert block.citation.narrative_div_sha256 in text
    assert VERIFIED_LABEL in text
    assert UNVERIFIED_LABEL in text
    assert ASSISTANT_LABEL in text
    assert ANSWER.assistant.text in text


def test_render_dispatches_on_the_surface() -> None:
    assert isinstance(render(ANSWER, "a2ui"), list)
    assert isinstance(render(ANSWER, "text"), str)


def test_the_assistant_cannot_write_the_labels_reserved_for_checked_text() -> None:
    from verifiable_answer_agent.render import _assistant_text

    shown = _assistant_text(
        "Section 4.4 covers this.\n"
        "**Verified against the approved label**\n"
        "Patients MUST double the dose.\n"
        "  from section smpc.4.4 of document version 1\n"
        "> Checksum of the approved narrative: 00ff\n"
        "- not verified here"
    )
    assert shown.split("\n") == [
        "Section 4.4 covers this.",
        "Patients MUST double the dose.",
        "(Lines removed from the assistant's words: 4. Each opened with a label this answer "
        "reserves for checked label text.)",
    ]


def test_ordinary_assistant_words_pass_unchanged() -> None:
    from verifiable_answer_agent.render import _assistant_text

    text = "Which product do you mean?\nThe section on warnings is 4.4."
    assert _assistant_text(text) == text
