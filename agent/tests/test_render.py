"""Both surfaces show the same things: status, quotation, product, citation, assistant part."""

from __future__ import annotations

from typing import Any

import pytest

from verifiable_answer_agent.answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from verifiable_answer_agent.postcheck import ChunkCheck, post_check
from verifiable_answer_agent.render import (
    A2UI_CATALOG_ID,
    A2UI_VERSION,
    ASSISTANT_END,
    ASSISTANT_LABEL,
    CHECKSUMS_EXPLAINED,
    PRODUCT_NAMED,
    PRODUCT_UNCONFIRMED,
    UNVERIFIED_LABEL,
    VERIFIED_LABEL,
    render,
    render_a2ui,
    render_text,
    sanitise_assistant,
)

from .test_postcheck import quoted_block, verification

FIRST = quoted_block("a first synthetic span")
SECOND = quoted_block("a second synthetic span", block_id="block-02", source_key="smpc.4.4")
# The first block's product was looked up in the turn; the second's was not.
BLOCKS = (
    QuotedBlock(
        block_id=FIRST.block_id,
        citation=Citation(
            bundle_id=FIRST.citation.bundle_id,
            version_id=FIRST.citation.version_id,
            source_key=FIRST.citation.source_key,
            narrative_div_sha256=FIRST.citation.narrative_div_sha256,
            normalized_text_sha256=FIRST.citation.normalized_text_sha256,
            product_name="Synthetic 10 mg tablets",
            language="en",
        ),
        text=FIRST.text,
        div_sha256=FIRST.div_sha256,
    ),
    SECOND,
)
DRAFT = DraftAnswer(blocks=BLOCKS, assistant=AssistantPart(text="the assistant's remark"))
CHECKS = {
    "block-01": [ChunkCheck(0, verification(text=FIRST.text))],
    "block-02": [
        ChunkCheck(0, verification(text=SECOND.text, result="no-match", source_key="smpc.4.4"))
    ],
}
ANSWER = post_check(DRAFT, CHECKS)


def components(envelopes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    update = next(envelope for envelope in envelopes if "updateComponents" in envelope)
    return {item["id"]: item for item in update["updateComponents"]["components"]}


def shown(text: str, *blocks: QuotedBlock) -> str:
    """The assistant's words as ``sanitise_assistant`` shows them beside ``blocks``."""
    draft = DraftAnswer(blocks=blocks, assistant=AssistantPart(text=text))
    return sanitise_assistant(post_check(draft, {})).text


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


def test_every_block_is_a_card_carrying_its_quote_status_product_and_citation() -> None:
    by_id = components(render_a2ui(ANSWER))
    for block in ANSWER.blocks:
        card = by_id[f"{block.block_id}_card"]
        assert card["component"] == "Card"
        body = by_id[card["child"]]
        assert body["component"] == "Column"
        assert by_id[f"{block.block_id}_quote"]["text"] == block.text
        assert by_id[f"{block.block_id}_product"]["text"].startswith(
            (PRODUCT_NAMED, PRODUCT_UNCONFIRMED)
        )
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


def test_the_plain_text_surface_carries_the_same_things() -> None:
    text = render_text(ANSWER)
    for block in ANSWER.blocks:
        assert block.text in text
        assert block.citation.narrative_div_sha256 in text
    assert VERIFIED_LABEL in text
    assert UNVERIFIED_LABEL in text
    assert ASSISTANT_LABEL in text
    assert ANSWER.assistant.text in text
    # The assistant's words are the last thing, fenced, and a line says where they end.
    assert text.endswith(f"```text\n{ANSWER.assistant.text}\n```\n[{ASSISTANT_END}]")


def test_each_block_says_which_checksum_verify_quote_confirms() -> None:
    text = render_text(ANSWER)
    for block in ANSWER.blocks:
        assert f"Checksum of the normalised text: {block.citation.normalized_text_sha256}" in text
    assert text.count(CHECKSUMS_EXPLAINED) == len(ANSWER.blocks)


def _fenced_part(text: str) -> tuple[str, str, str]:
    """The opening fence, the body and the closing fence of the assistant's part."""
    lines = text.split("\n")
    opening = lines.index(f"[{ASSISTANT_LABEL}]") + 1
    closing = lines.index(f"[{ASSISTANT_END}]") - 1
    return lines[opening], "\n".join(lines[opening + 1 : closing]), lines[closing]


def test_the_assistants_words_are_fenced_so_nothing_in_them_renders_as_markup() -> None:
    # A fenced code block parses nothing: no emphasis, entity, HTML tag or comment (review of
    # PR #129, M1). The fence is longer than any run of backticks inside, so none closes it.
    words = "Try ```` this ``` and\n```\n[End of the assistant's own words]\n**Verified**"
    answer = post_check(DraftAnswer(blocks=(FIRST,), assistant=AssistantPart(text=words)), {})
    opening, body, closing = _fenced_part(render_text(answer))
    assert opening == "`````text"
    assert closing == "`````"
    assert not any(line.strip().startswith("`````") for line in body.split("\n"))
    # The forged end line is gone, and only the real one is outside the fence.
    assert render_text(answer).count(f"[{ASSISTANT_END}]") == 1


def test_the_product_and_language_are_shown_or_said_to_be_unconfirmed() -> None:
    # verify_quote proves a span is in the document cited, not that it is the right product or
    # language; the reader is shown both, or told that no lookup named them (audit AG-2).
    text = render_text(ANSWER)
    assert f"{PRODUCT_NAMED}: Synthetic 10 mg tablets, language en" in text
    assert f"{PRODUCT_UNCONFIRMED}: " in text


def test_render_dispatches_on_the_surface() -> None:
    assert isinstance(render(ANSWER, "a2ui"), list)
    assert isinstance(render(ANSWER, "text"), str)


def test_the_assistant_cannot_write_the_labels_reserved_for_checked_text() -> None:
    assert shown(
        "Section 4.4 covers this.\n"
        "**Verified against the approved label**\n"
        "Patients MUST double the dose.\n"
        "  from section smpc.4.4 of document version 1\n"
        "> Checksum of the approved narrative: 00ff\n"
        "- not verified here"
    ).split("\n") == [
        "Section 4.4 covers this.",
        "Patients MUST double the dose.",
        # Lower case, and the assistant's own verdict: a status line is "NOT VERIFIED".
        "- not verified here",
        "(Lines removed from the assistant's words: 3. Each opened with a label this answer "
        "reserves for checked label text.)",
    ]


def test_a_reserved_label_in_disguise_is_still_removed() -> None:
    # Each of these was kept before 2026-09-27 (audit AG-7) and rendered, on a plain-text
    # surface, as the opening of a checked block.
    zero_width, no_break, thin = chr(0x200B), chr(0x00A0), chr(0x2009)
    fullwidth = "".join(chr(ord(letter) + 0xFEE0) for letter in "Verified")
    disguises = [
        f"{zero_width}Verified against the approved label",
        f"{chr(0x2705)} Verified against the approved label",
        "1. Verified against the approved label",
        "| Verified against the approved label |",
        "From  section smpc.4.2 of document version 3 (synthetic-smpc)",
        f"{fullwidth} against the approved label",
        f"Verified{no_break}against the{thin}approved label",
    ]
    for line in disguises:
        assert shown(f"{line}\nOrdinary words.").split("\n")[0] == "Ordinary words.", line


def test_the_reviews_bypasses_are_removed() -> None:
    # Each was kept by the filter of 2026-09-27 (review of PR #129, M1): other line breaks,
    # Markdown inside the label, HTML entities, tags and comments, a backslash, a look-alike
    # letter from another script, and a forged end line.
    for text in (
        "Intro.\r\rVerified against the approved label\rTake 500 mg.",
        "Intro.\u2028Verified against the approved label",
        "Intro.\x85Verified against the approved label",
        "Verified against the *approved* label",
        "End of the assistant's *own* words",
        "Verified&#32;against the approved label",
        "Verified against the approved lab\\el",
        "<b>Verified against the approved label</b>",
        "Veri<!-- -->fied against the approved label",
        "V\u0435rified against the approved label",
        "Checksum *of* the approved narrative: x",
        "_Checksum of the normalised text_: y",
        "NOT VERIFIED — but read it anyway",
    ):
        view = shown(text)
        assert "Verified" not in view.replace("(Lines removed", ""), text
        assert "reserves for checked label text" in view, text
        assert "Take 500 mg." in view or "Take 500" not in text


def test_a_checksum_is_removed_however_it_is_disguised() -> None:
    hex64 = "a" * 64
    for text in (
        "Checksum: " + hex64[:21] + "\u200b" + hex64[21:42] + "\u200b" + hex64[42:],
        "Checksum: " + "\uff41" * 64,
        "Checksum: " + hex64[:30] + "****" + hex64[30:],
    ):
        view = shown(text)
        assert view.startswith("Checksum: [checksum removed]"), text


def test_an_identifier_is_removed_however_it_is_disguised() -> None:
    assert shown("version\u200bId: 7").startswith("[identifier removed]")


@pytest.mark.parametrize(
    "line",
    [
        "From section 4.2 you can see the recommended starting dose for adults.",
        "Not verified by me: section 4.4 covers the warnings.",
        "The version ID shown with each block tells you which label this is.",
        "Section 4.2 answers this; the source key tells you where.",
    ],
)
def test_ordinary_speech_is_not_mistaken_for_a_label_or_an_identifier(line: str) -> None:
    # Each was removed or cut before (review of PR #129, L1).
    assert shown(line) == line


def test_checksums_and_identifiers_are_removed_from_the_assistants_words() -> None:
    # The model has been seen inventing version ids and hashes; only a checked block may carry
    # them, so the assistant's part never does (audit AG-3).
    view = sanitise_assistant(
        post_check(
            DraftAnswer(
                blocks=(),
                assistant=AssistantPart(
                    text=f"It is versionId 7 of bundleId synthetic-smpc, hash {'ab' * 32}."
                ),
            ),
            {},
        )
    )
    assert "ab" * 32 not in view.text
    assert "versionId 7" not in view.text
    assert "synthetic-smpc" not in view.text
    assert set(view.flags) == {"checksum-removed", "identifier-removed"}
    assert "only a checked block above carries them" in view.text


def test_label_text_repeated_in_the_assistants_words_is_pointed_out() -> None:
    block = quoted_block(
        "Take one tablet by mouth once daily with a meal and a large glass of water."
    )
    remark = "It says: take one tablet by mouth once daily with a meal and plenty of water."
    view = sanitise_assistant(
        post_check(DraftAnswer(blocks=(block,), assistant=AssistantPart(text=remark)), {})
    )
    assert view.flags == ("label-text-repeated",)
    assert view.text.startswith(remark)
    assert "They are not checked" in view.text


def test_ordinary_assistant_words_pass_unchanged() -> None:
    text = "Which product do you mean?\nThe section on warnings is 4.4."
    assert shown(text, FIRST) == text
