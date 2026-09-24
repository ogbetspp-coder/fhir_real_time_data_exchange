"""Layer 2. The tests that decide whether the product's promise is true.

An altered quotation must be flagged, a quotation that was never checked must be flagged, and
there must be no way to put a quotation in front of a reader without checking it.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from verifiable_answer_agent import postcheck, render
from verifiable_answer_agent.answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from verifiable_answer_agent.contract import QuoteVerification, ToolResult, split_for_verification
from verifiable_answer_agent.postcheck import CheckedAnswer, ChunkCheck, post_check, run_post_check

CITATION = Citation(
    bundle_id="synthetic-smpc", version_id="1", source_key="smpc.4.3", narrative_div_sha256="0" * 64
)
BLOCK = QuotedBlock(block_id="block-01", citation=CITATION, text="a short synthetic span")
DRAFT = DraftAnswer(blocks=(BLOCK,), assistant=AssistantPart(text="a remark"))


def verification(
    *,
    result: str = "match",
    source_key: str = "smpc.4.3",
    bundle_id: str = "synthetic-smpc",
    version_id: str = "1",
) -> QuoteVerification:
    payload: QuoteVerification = {
        "document": {
            "bundleId": bundle_id,
            "versionId": version_id,
            "lastUpdated": "2026-09-19T00:00:00Z",
        },
        "result": "match" if result == "match" else "no-match",
        "normalizationVersion": "fidelity-norm/3.1.0",
        "quoteSha256": "1" * 64,
        "sectionsSearched": 32,
    }
    if result == "match":
        payload["match"] = {
            "sourceKey": source_key,
            "startOffset": 0,
            "endOffset": 22,
            "normalizedTextSha256": "2" * 64,
        }
    return payload


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
    long_block = QuotedBlock(
        block_id="block-01",
        citation=CITATION,
        text=" ".join(f"word{index:04d}" for index in range(900)),
    )
    draft = DraftAnswer(blocks=(long_block,), assistant=AssistantPart(text=""))
    chunks = split_for_verification(long_block.text)
    checks = [ChunkCheck(index, verification()) for index in range(len(chunks))]
    assert post_check(draft, {"block-01": checks}).blocks[0].status == "verified"
    checks[-1] = ChunkCheck(len(chunks) - 1, verification(result="no-match"))
    flagged = post_check(draft, {"block-01": checks}).blocks[0]
    assert flagged.status == "unverified"
    assert "no-match" in flagged.flags


# --- the structural guarantee -------------------------------------------------------------


def test_a_checked_answer_cannot_be_constructed_outside_post_check() -> None:
    with pytest.raises(TypeError):
        CheckedAnswer(postcheck._PostCheckWitness(), (), AssistantPart(text=""))
    with pytest.raises(TypeError):
        CheckedAnswer(object(), (), AssistantPart(text=""))  # type: ignore[arg-type]


def test_every_renderer_accepts_only_a_checked_answer() -> None:
    """The invariant is in the signatures, so it is asserted against the signatures."""
    renderers: list[Any] = [render.render, render.render_a2ui, render.render_text]
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
        del quote
        return ToolResult(tool="verify_quote", value=dict(verification()), reason=None)

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

    async def verify(bundle_id: str, version_id: str, source_key: str, quote: str) -> ToolResult:
        del bundle_id, version_id, source_key
        asked.append(quote)
        return ToolResult(tool="verify_quote", value=dict(verification()), reason=None)

    block = QuotedBlock(block_id="block-01", citation=CITATION, text=f"Take {'x' * 2500} daily.")
    draft = DraftAnswer(blocks=(block,), assistant=AssistantPart(text=""))
    answer = await run_post_check(draft, verify)
    assert asked == ["Take", "daily."]
    (checked,) = answer.blocks
    assert checked.status == "unverified"
    assert checked.flags == ("verification-unavailable",)
    assert checked.chunks_checked == 3
