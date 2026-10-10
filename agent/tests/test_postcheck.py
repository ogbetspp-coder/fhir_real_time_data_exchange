"""Layer 2. The tests that decide whether the product's promise is true.

An altered quotation must be flagged, a quotation that was never checked must be flagged, and
there must be no way to put a quotation in front of a reader without checking it. A ``match``
is not taken on its word: it must cover the block exactly, and every hash must agree.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from verifiable_answer_agent import postcheck, render
from verifiable_answer_agent.answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from verifiable_answer_agent.contract import (
    QuoteVerification,
    ToolResult,
    chunk_spans,
    sha256_hex,
    split_for_verification,
)
from verifiable_answer_agent.postcheck import CheckedAnswer, ChunkCheck, post_check, run_post_check

SPAN_TEXT = "a short synthetic span"


def quoted_block(
    text: str = SPAN_TEXT,
    *,
    block_id: str = "block-01",
    source_key: str = "smpc.4.3",
    bundle_id: str = "synthetic-smpc",
    version_id: str = "1",
) -> QuotedBlock:
    """A block whose citation's hashes are those of its own text and XHTML, as compose makes."""
    div = f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{text}</p></div>'
    return QuotedBlock(
        block_id=block_id,
        citation=Citation(
            bundle_id=bundle_id,
            version_id=version_id,
            source_key=source_key,
            narrative_div_sha256=sha256_hex(div),
            normalized_text_sha256=sha256_hex(text),
        ),
        text=text,
        div_sha256=sha256_hex(div),
    )


BLOCK = quoted_block()
CITATION = BLOCK.citation
DRAFT = DraftAnswer(blocks=(BLOCK,), assistant=AssistantPart(text="a remark"))


def verification(
    *,
    text: str = SPAN_TEXT,
    span: tuple[int, int] | None = None,
    result: str = "match",
    source_key: str = "smpc.4.3",
    bundle_id: str = "synthetic-smpc",
    version_id: str = "1",
) -> QuoteVerification:
    """What an honest service answers for the chunk ``text[span]`` of a block of ``text``."""
    start, end = span if span is not None else (0, len(text))
    payload: QuoteVerification = {
        "document": {
            "bundleId": bundle_id,
            "versionId": version_id,
            "lastUpdated": "2026-09-19T00:00:00Z",
        },
        "result": "match" if result == "match" else "no-match",
        "normalizationVersion": "fidelity-norm/3.8.0",
        "quoteSha256": sha256_hex(text[start:end]),
        "sectionsSearched": 1,
    }
    if result == "match":
        payload["match"] = {
            "sourceKey": source_key,
            "startOffset": start,
            "endOffset": end,
            "normalizedTextSha256": sha256_hex(text),
        }
    return payload


def honest_checks(block: QuotedBlock) -> list[ChunkCheck]:
    return [
        ChunkCheck(index, verification(text=block.text, span=span))
        for index, span in enumerate(chunk_spans(block.text))
    ]


def only_block(verdict: QuoteVerification, block: QuotedBlock = BLOCK) -> Any:
    draft = DraftAnswer(blocks=(block,), assistant=AssistantPart(text=""))
    (checked,) = post_check(draft, {block.block_id: [ChunkCheck(0, verdict)]}).blocks
    return checked


def test_a_matching_block_is_verified_and_carries_no_flag() -> None:
    answer = post_check(DRAFT, {"block-01": [ChunkCheck(0, verification())]})
    (block,) = answer.blocks
    assert block.status == "verified"
    assert block.flags == ()
    assert answer.flagged_blocks == ()


def test_an_altered_block_comes_back_no_match_and_is_flagged() -> None:
    answer = post_check(DRAFT, {"block-01": [ChunkCheck(0, verification(result="no-match"))]})
    (block,) = answer.blocks
    assert block.status == "unverified"
    assert "no-match" in block.flags
    assert answer.verified_blocks == ()


def test_a_block_that_was_never_checked_is_unverified_not_assumed_good() -> None:
    answer = post_check(DRAFT, {})
    (block,) = answer.blocks
    assert block.status == "unverified"
    assert "verification-missing" in block.flags
    assert block.chunks_checked == 0


def test_a_verify_quote_call_that_failed_leaves_the_block_unverified() -> None:
    answer = post_check(DRAFT, {"block-01": [ChunkCheck(0, None)]})
    (block,) = answer.blocks
    assert block.status == "unverified"
    assert "verification-unavailable" in block.flags


def test_text_found_in_a_different_section_does_not_verify_the_citation() -> None:
    answer = post_check(DRAFT, {"block-01": [ChunkCheck(0, verification(source_key="smpc.4.4"))]})
    (block,) = answer.blocks
    assert block.status == "unverified"
    assert "section-mismatch" in block.flags


def test_an_answer_about_a_different_version_does_not_verify_the_citation() -> None:
    answer = post_check(DRAFT, {"block-01": [ChunkCheck(0, verification(version_id="2"))]})
    (block,) = answer.blocks
    assert block.status == "unverified"
    assert "document-mismatch" in block.flags


def test_one_bad_chunk_in_a_long_block_flags_the_whole_block() -> None:
    long_block = quoted_block(" ".join(f"word{index:04d}" for index in range(900)))
    draft = DraftAnswer(blocks=(long_block,), assistant=AssistantPart(text=""))
    checks = honest_checks(long_block)
    assert len(checks) > 1
    assert post_check(draft, {"block-01": checks}).blocks[0].status == "verified"
    last = len(checks) - 1
    checks[-1] = ChunkCheck(last, verification(text=long_block.text, result="no-match"))
    flagged = post_check(draft, {"block-01": checks}).blocks[0]
    assert flagged.status == "unverified"
    assert "no-match" in flagged.flags


# --- a match is not taken on its word (audit AG-4) ----------------------------------------


def test_the_audits_loose_match_is_not_verified() -> None:
    # The repro: "match" with no location, a quote hash of zeros, a normalisation version the
    # agent does not hold, and no section searched. Before 2026-09-27 this came back verified.
    loose: Any = {
        "document": {
            "bundleId": "synthetic-smpc",
            "versionId": "1",
            "lastUpdated": "2026-09-19T00:00:00Z",
        },
        "result": "match",
        "quoteSha256": "0" * 64,
        "normalizationVersion": "fidelity-norm/9.9.9",
        "sectionsSearched": 0,
    }
    checked = only_block(loose)
    assert checked.status == "unverified"
    assert set(checked.flags) == {"coverage-gap", "checksum-mismatch"}


def test_a_match_over_no_section_is_a_coverage_gap() -> None:
    verdict = verification()
    verdict["sectionsSearched"] = 0
    assert only_block(verdict).flags == ("coverage-gap",)


def test_a_prefix_of_the_block_does_not_verify_the_block() -> None:
    # The service answered for the first 37 code points of a longer block: every word it
    # confirmed is the label's, and the rest of the block was never confirmed.
    text = "Take one tablet daily with food. Do not crush or chew the tablet."
    block = quoted_block(text)
    verdict = verification(text=text, span=(0, 37))
    checked = only_block(verdict, block)
    assert checked.status == "unverified"
    assert "coverage-gap" in checked.flags


def test_chunks_that_do_not_tile_the_block_do_not_verify_it() -> None:
    # A sentence dropped between two chunks: each chunk is the label's text, and they match at
    # offsets that leave a gap, so the block as shown is not the section.
    text = " ".join(f"word{index:04d}" for index in range(900))
    block = quoted_block(text)
    spans = chunk_spans(text)
    assert len(spans) > 1
    shifted = [
        ChunkCheck(index, verification(text=text, span=span)) for index, span in enumerate(spans)
    ]
    start, end = spans[1]
    moved = verification(text=text, span=(start, end))
    assert "match" in moved
    moved["match"]["startOffset"] += 9
    moved["match"]["endOffset"] += 9
    shifted[1] = ChunkCheck(1, moved)
    draft = DraftAnswer(blocks=(block,), assistant=AssistantPart(text=""))
    checked = post_check(draft, {"block-01": shifted}).blocks[0]
    assert checked.status == "unverified"
    assert checked.flags == ("coverage-gap",)


def test_a_citation_whose_checksum_is_not_its_texts_is_flagged() -> None:
    wrong = QuotedBlock(
        block_id="block-01",
        citation=Citation(
            bundle_id="synthetic-smpc",
            version_id="1",
            source_key="smpc.4.3",
            narrative_div_sha256="f" * 64,
            normalized_text_sha256=sha256_hex(SPAN_TEXT),
        ),
        text=SPAN_TEXT,
        div_sha256=BLOCK.div_sha256,
    )
    assert only_block(verification(), wrong).flags == ("checksum-mismatch",)


def test_a_match_naming_another_text_hash_is_flagged() -> None:
    verdict = verification()
    assert "match" in verdict
    verdict["match"]["normalizedTextSha256"] = "e" * 64
    assert only_block(verdict).flags == ("checksum-mismatch",)


def test_an_answer_about_another_quote_is_flagged() -> None:
    verdict = verification()
    verdict["quoteSha256"] = sha256_hex("a different quote")
    assert only_block(verdict).flags == ("checksum-mismatch",)


def test_an_answer_under_another_normalisation_version_is_flagged() -> None:
    verdict = verification()
    verdict["normalizationVersion"] = "fidelity-norm/3.1.0"
    assert only_block(verdict).flags == ("checksum-mismatch",)


def test_answers_that_are_not_this_blocks_chunks_are_a_coverage_gap() -> None:
    twice = [ChunkCheck(0, verification()), ChunkCheck(0, verification())]
    assert post_check(DRAFT, {"block-01": twice}).blocks[0].flags == ("coverage-gap",)
    elsewhere = [ChunkCheck(0, verification()), ChunkCheck(5, verification())]
    assert post_check(DRAFT, {"block-01": elsewhere}).blocks[0].flags == ("coverage-gap",)


# --- the structural guarantee -------------------------------------------------------------


def test_a_checked_answer_cannot_be_constructed_outside_post_check() -> None:
    with pytest.raises(TypeError):
        CheckedAnswer(postcheck._PostCheckWitness(), (), AssistantPart(text=""))
    with pytest.raises(TypeError):
        CheckedAnswer(object(), (), AssistantPart(text=""))  # type: ignore[arg-type]


def test_every_renderer_accepts_only_a_checked_answer() -> None:
    """The invariant is in the signatures, so it is asserted against the signatures."""
    renderers: list[Any] = [render.render_text, render.sanitise_assistant]
    for function in renderers:
        first = next(iter(inspect.signature(function, eval_str=True).parameters.values()))
        assert first.annotation is CheckedAnswer, (
            f"{function.__name__} takes {first.annotation}, which is not a CheckedAnswer"
        )


def _returns_a_checked_answer(member: object) -> bool:
    try:
        signature = inspect.signature(member, eval_str=True)  # type: ignore[arg-type]
    except TypeError, ValueError, NameError:
        return False
    return signature.return_annotation is CheckedAnswer


def test_the_only_public_producer_of_a_checked_answer_is_the_post_check() -> None:
    producers = sorted(
        name
        for name, member in vars(postcheck).items()
        if not name.startswith("_") and callable(member) and _returns_a_checked_answer(member)
    )
    assert producers == ["post_check", "run_post_check"]


# --- the driver ---------------------------------------------------------------------------


async def test_the_driver_asks_about_every_chunk_of_every_block() -> None:
    asked: list[tuple[str, str, str]] = []

    async def verify(bundle_id: str, version_id: str, source_key: str, quote: str) -> ToolResult:
        asked.append((bundle_id, version_id, source_key))
        return ToolResult(tool="verify_quote", value=dict(verification(text=quote)), reason=None)

    answer = await run_post_check(DRAFT, verify)
    assert len(asked) == 1
    assert asked[0] == ("synthetic-smpc", "1", "smpc.4.3")
    assert answer.blocks[0].status == "verified"


async def test_the_driver_treats_an_unavailable_verification_as_unverified() -> None:
    async def verify(bundle_id: str, version_id: str, source_key: str, quote: str) -> ToolResult:
        del bundle_id, version_id, source_key, quote
        return ToolResult(tool="verify_quote", value=None, reason="tool-error")

    answer = await run_post_check(DRAFT, verify)
    assert answer.blocks[0].status == "unverified"
    assert "verification-unavailable" in answer.blocks[0].flags


async def test_a_chunk_over_the_quote_bound_is_not_sent_and_the_block_is_unavailable() -> None:
    # 2,500 units with no cut the quote-edge rule accepts: the splitter keeps the run whole
    # rather than cut it where the service would answer no-match, and the contract refuses a
    # quote that long, so nothing is asked about it and the block says why it is not verified.
    asked: list[str] = []
    text = f"Take {'x' * 2500} daily."
    draft = DraftAnswer(blocks=(quoted_block(text),), assistant=AssistantPart(text=""))

    async def verify(bundle_id: str, version_id: str, source_key: str, quote: str) -> ToolResult:
        del bundle_id, version_id, source_key
        asked.append(quote)
        start = text.index(quote)
        value = verification(text=text, span=(start, start + len(quote)))
        return ToolResult(tool="verify_quote", value=dict(value), reason=None)

    answer = await run_post_check(draft, verify)
    assert sorted(asked) == ["Take", "daily."]
    (checked,) = answer.blocks
    assert checked.status == "unverified"
    assert checked.flags == ("verification-unavailable",)
    assert checked.chunks_checked == 3


async def test_a_table_is_not_sent_and_the_block_says_it_cannot_be_checked_yet() -> None:
    # A grid marker (U+FDD0-U+FDEF) or a picture's U+FFFC: verify_quote answers invalid-request
    # for any quote holding one, so sending it would only produce a misleading "unavailable".
    table = "".join(map(chr, (0xFDD0, 0xFDD2, 0xFDD3))) + "dose" + chr(0xFDD3) + "daily"
    table += chr(0xFDD1)
    text = f"Take one tablet. {table}"
    asked: list[str] = []

    async def verify(bundle_id: str, version_id: str, source_key: str, quote: str) -> ToolResult:
        del bundle_id, version_id, source_key
        asked.append(quote)
        start = text.index(quote)
        value = verification(text=text, span=(start, start + len(quote)))
        return ToolResult(tool="verify_quote", value=dict(value), reason=None)

    draft = DraftAnswer(blocks=(quoted_block(text),), assistant=AssistantPart(text=""))
    (checked,) = (await run_post_check(draft, verify)).blocks
    assert not any(chr(0xFDD0) in quote for quote in asked)
    assert checked.status == "unverified"
    assert checked.flags == ("table-not-quotable",)


async def test_the_driver_runs_a_few_checks_at_once_and_keeps_their_order() -> None:
    long_block = quoted_block(" ".join(f"word{index:04d}" for index in range(900)))
    draft = DraftAnswer(blocks=(long_block,), assistant=AssistantPart(text=""))
    in_flight = 0
    most = 0

    async def verify(bundle_id: str, version_id: str, source_key: str, quote: str) -> ToolResult:
        nonlocal in_flight, most
        del bundle_id, version_id, source_key
        in_flight += 1
        most = max(most, in_flight)
        # Later chunks answer first: the order they come back in must not matter.
        await asyncio.sleep(0.0001 * (1000 - long_block.text.index(quote) % 1000))
        in_flight -= 1
        start = long_block.text.index(quote)
        value = verification(text=long_block.text, span=(start, start + len(quote)))
        return ToolResult(tool="verify_quote", value=dict(value), reason=None)

    answer = await run_post_check(draft, verify, concurrency=3)
    assert len(split_for_verification(long_block.text)) > 3
    assert most == 3
    assert answer.blocks[0].status == "verified"
