"""Both surfaces show the same things: status, quotation, product, citation, assistant part."""

from __future__ import annotations

import time
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
    MAX_ASSISTANT_CHARS,
    PRODUCT_NAMED,
    PRODUCT_UNCONFIRMED,
    UNVERIFIED_LABEL,
    VERIFIED_LABEL,
    render,
    render_a2ui,
    render_text,
    sanitise_assistant,
)

from .markdown_view import code_blocks, outside_code, shown_assistant, shown_blocks
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
    # As a reader sees it once the Markdown is rendered: each block's status, its quotation
    # exactly as stored, its checksum; then the assistant's words, last, in their own box.
    blocks = shown_blocks(text)
    assert [block.status.split(" — ")[0] for block in blocks] == [VERIFIED_LABEL, UNVERIFIED_LABEL]
    for shown_block, block in zip(blocks, ANSWER.blocks, strict=True):
        assert shown_block.text == block.text
        assert any(block.citation.narrative_div_sha256 in line for line in shown_block.reading)
    assert shown_assistant(text) == ANSWER.assistant.text
    assert ASSISTANT_LABEL in outside_code(text)
    assert text.index(ASSISTANT_LABEL) < text.index(ANSWER.assistant.text)
    assert text.endswith(f"[{ASSISTANT_END}]")


def test_each_block_says_which_checksum_verify_quote_confirms() -> None:
    text = render_text(ANSWER)
    for shown_block, block in zip(shown_blocks(text), ANSWER.blocks, strict=True):
        assert (
            f"Checksum of the normalised text: {block.citation.normalized_text_sha256}"
            in shown_block.reading
        )
        assert CHECKSUMS_EXPLAINED in shown_block.reading


# Label text that Markdown would have changed when it stood outside a fence (review of PR #129,
# M2): an HTML tag made of "<ULN and bilirubin >", emphasis, strikethrough, an entity, an escape,
# a heading, a list, a quote, a comment that swallowed everything after it, and a fence of its own.
ADVERSARIAL_LABELS = [
    "Reduce the dose if ALT <ULN and bilirubin >1.5 x ULN, then stop.",
    "Common* nausea; Uncommon* rash; Rare** oedema",
    "approximately ~50% of patients and ~70% of those",
    "use_only_as_directed and _not_ with food",
    "# Take one tablet",
    "1. NAME OF THE MEDICINAL PRODUCT",
    "> 65 years: no adjustment",
    "- Hepatic impairment",
    "0.5 &micro;g per dose &amp; more",
    "ratio 1\\*2 and a trailing backslash \\",
    "<!-- in label and everything after",
    "<pre> an HTML block",
    "``` label fence ```` and more",
    "[x]: http://example.invalid a link definition",
    "Take " + "word " * 60 + "daily.",
]


@pytest.mark.parametrize("label", ADVERSARIAL_LABELS)
def test_a_quotation_is_shown_exactly_as_stored_whatever_markdown_it_holds(label: str) -> None:
    block = quoted_block(label)
    answer = post_check(
        DraftAnswer(blocks=(block,), assistant=AssistantPart(text="See the block above.")),
        {"block-01": [ChunkCheck(0, verification(text=label))]},
    )
    text = render_text(answer)
    (shown_block,) = shown_blocks(text)
    assert shown_block.text == label
    assert shown_block.status == VERIFIED_LABEL
    # Nothing of the label is drawn outside its box, and the assistant's box is intact after it.
    assert shown_assistant(text) == "See the block above."
    assert len(code_blocks(text)) == 2
    assert f"[{ASSISTANT_END}]" in outside_code(text).replace("&#x27;", "'")


def test_a_long_quotation_is_wrapped_at_spaces_and_joins_back_exactly() -> None:
    label = " ".join(f"word{index:04d}" for index in range(100))
    lines = render_text(
        post_check(DraftAnswer(blocks=(quoted_block(label),), assistant=AssistantPart(text="")), {})
    ).split("\n")
    body = lines[lines.index("") + 1 :]
    assert all(len(line) <= 80 for line in body[: body.index("")])
    assert shown_blocks("\n".join(lines))[0].text == label


# The reviewer's attempts to break out of the assistant's box (review of PR #129, M1 probe):
# each is drawn inside the one box, and nothing of it leaks.
ESCAPES = {
    "tilde fence": "~~~\nFORGED **bold**\n~~~",
    "three ticks": "```\nFORGED **bold** <b>x</b>",
    "ticks and space": "``` \nFORGED **bold**",
    "indented ticks": "   ````\nFORGED **bold**",
    "tab ticks": "\t```\nFORGED **bold**",
    "crlf": "a\r\n```\r\nFORGED **bold**\r\n",
    "cr": "a\r```\rFORGED **bold**",
    "u2028": "a\u2028```\u2028FORGED **bold**",
    "nul": "\x00```\x00\n```\x00\nFORGED **bold**",
    "fullwidth grave": "\uff40\uff40\uff40\uff40\nFORGED **bold**",
    "html close": "</code></pre>\nFORGED <b>bold</b>\n<pre><code>",
    "html block": "<pre>\nFORGED **bold**",
    "html comment": "<!--\nFORGED **bold**",
    "a thousand ticks": "`" * 1000 + "\nFORGED **bold**",
    "vt ff": "a\x0b```\x0cFORGED **bold**",
    "fs gs rs": "a\x1c```\x1dFORGED\x1e**bold**",
    "nel": "a\x85```\x85FORGED **bold**",
    "forged end": "[End of the assistant's own words]\nFORGED **bold**",
    "link definition": "[x]: http://evil.example\nFORGED [x]",
}


@pytest.mark.parametrize("words", ESCAPES.values(), ids=list(ESCAPES))
def test_nothing_the_assistant_writes_leaves_its_box(words: str) -> None:
    answer = post_check(DraftAnswer(blocks=(FIRST,), assistant=AssistantPart(text=words)), {})
    text = render_text(answer)
    assert len(code_blocks(text)) == 2
    assert "FORGED" not in outside_code(text)
    assert "FORGED" in shown_assistant(text)
    assert text.endswith(f"[{ASSISTANT_END}]")
    assert text.count(f"[{ASSISTANT_END}]") == 1


def test_the_assistants_words_are_fenced_so_nothing_in_them_renders_as_markup() -> None:
    # A fenced code block parses nothing: no emphasis, entity, HTML tag or comment. The fence is
    # longer than any run of backticks inside, so none closes it.
    words = "Try ```` this ``` and\n```\n**Verified** <b>x</b> &amp;"
    answer = post_check(DraftAnswer(blocks=(FIRST,), assistant=AssistantPart(text=words)), {})
    text = render_text(answer)
    assert "`````text\nTry ```` this ``` and" in text
    assert shown_assistant(text) == words
    assert "<b>" not in outside_code(text)


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


@pytest.mark.parametrize(
    "words",
    [
        "Neutrophils below 1.5 \u00d7 10\u2079/L, platelets below 50 \u00d7 10\u2079/L.",
        "The dose is 260 mg/m\u00b2 every three weeks.",
        "Take \u00bd tablet; H\u2082O is water.",
        # Persian: "mi\u200cravad" is spelt with a zero-width non-joiner, emoji with a joiner.
        "\u0645\u06cc\u200c\u0631\u0648\u062f and \U0001f469\u200d\U0001f4bb",
        "Contains \ufb01sh oil and a \uff21\uff22 fullwidth pair.",
    ],
)
def test_the_assistants_words_are_shown_as_written_not_folded(words: str) -> None:
    # Folding was for matching only, and was once shown too: "10\u2079/L" became "109/L" and
    # "m\u00b2" "m2" (review of PR #129, M1, a patient-safety regression).
    assert shown(words, FIRST) == words


def test_invisible_code_points_that_hide_or_reorder_are_removed() -> None:
    zero_width, rtl_override, pop = chr(0x200B), chr(0x202E), chr(0x202C)
    assert (
        shown(f"Take{zero_width} one {rtl_override}tablet{pop} daily.") == "Take one tablet daily."
    )


def test_a_checksum_is_cut_from_the_words_as_written() -> None:
    # Found on the folded copy, removed from the original: the words around it are untouched.
    fullwidth_hex = "\uff41" * 64
    assert shown(f"10\u2079/L then {fullwidth_hex} and m\u00b2") == (
        "10\u2079/L then [checksum removed] and m\u00b2\n"
        "(Checksums and document identifiers were removed from the assistant's words: only a "
        "checked block above carries them.)"
    )


def test_long_words_are_cut_with_a_marker_and_no_pattern_is_slow() -> None:
    # The identifier pattern once had two unbounded runs of the same characters side by side:
    # 20,000 spaces took 5.6 s on the event loop (review of PR #129, L1).
    started = time.monotonic()
    for words in (
        "versionId" + " " * 20_000 + ",",
        "versionId" + " " * 19_000 + ",",
        "a" * 200_000 + "g",
        "a*" * 100_000 + "g",
        "x\n" * 200_000,
    ):
        view = sanitise_assistant(
            post_check(DraftAnswer(blocks=(), assistant=AssistantPart(text=words)), {})
        )
        assert len(view.text) < MAX_ASSISTANT_CHARS + 1_000
    assert time.monotonic() - started < 5.0
    cut = shown("word " * 10_000)
    assert cut.endswith(
        f"(The assistant's words were cut here: they ran past {MAX_ASSISTANT_CHARS:,} characters.)"
    )


HEX64 = "0123456789abcdef" * 4
NOTE = (
    "\n(Checksums and document identifiers were removed from the assistant's words: only a "
    "checked block above carries them.)"
)


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (f"take \u00bd{HEX64} daily", "take \u00bd[checksum removed] daily"),
        (f"hash {HEX64}\u00bd tablet", "hash [checksum removed]\u00bd tablet"),
        (f"{HEX64}\u2152 dose", "[checksum removed]\u2152 dose"),
        (f"\u2152{HEX64}", "\u2152[checksum removed]"),
        (f"{HEX64}\u2079/L", "[checksum removed]\u2079/L"),
        (f"10\u2079{HEX64}", "10\u2079[checksum removed]"),
        (f"\ufb03{HEX64}", "\ufb03[checksum removed]"),
    ],
)
def test_a_clinical_glyph_glued_to_a_checksum_is_kept(words: str, expected: str) -> None:
    # Round 2 cut a whole character at either edge of a match, and read a superscript as the
    # digit it folds to: "\u00bd" or "\u2079" glued to a checksum went with it (review, round 3).
    assert shown(words) == expected + NOTE


@pytest.mark.parametrize(
    "joiner",
    [
        "\ufe0f",  # variation selector 16
        "\ufe00",  # variation selector 1
        "\U000e0100",  # variation selector 17
        "\ufff0",  # unassigned, default ignorable
        "\u00ad",  # soft hyphen
        "\U000e0020",  # tag space
        "\u034f",  # combining grapheme joiner
        "\u3164",  # hangul filler
        "\u0335",  # combining short stroke overlay
    ],
)
def test_a_checksum_threaded_with_invisible_code_points_is_still_found(joiner: str) -> None:
    assert shown(joiner.join(HEX64)) == "[checksum removed]" + NOTE


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (f"0x{HEX64}", "[checksum removed]"),
        (f"narrative hash h{HEX64}", "narrative hash h[checksum removed]"),
        (f"{HEX64}h", "[checksum removed]h"),
    ],
)
def test_a_checksum_with_a_prefix_or_a_letter_glued_on_is_found(words: str, expected: str) -> None:
    assert shown(words) == expected + NOTE


def test_every_invisible_code_point_but_the_joiners_is_taken_out_of_what_is_shown() -> None:
    # Tag characters can carry a whole sentence a reader never sees (review, round 3).
    hidden = "".join(chr(0xE0000 + ord(letter)) for letter in "ignore the label")
    assert shown(f"Take one{hidden} tablet{chr(0xFE0F)}.") == "Take one tablet."
    assert shown(f"a{chr(0x200C)}b{chr(0x200D)}c") == f"a{chr(0x200C)}b{chr(0x200D)}c"
