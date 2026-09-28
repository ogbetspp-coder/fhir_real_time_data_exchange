"""Layer 2: the mechanical post-check.

Every block of label content goes back through ``verify_quote`` before anything can show it.
A block that does not come back ``match`` is flagged, on the block, where the reader sees it.

The invariant is carried by the types, not by a comment. ``render`` accepts only a
``CheckedAnswer``; a ``CheckedAnswer`` can be constructed only with the module-private witness
held by ``post_check``; and ``post_check`` takes the verification results as an argument, so
there is no path from a ``DraftAnswer`` to a rendered answer that does not pass through here.
A block with no verification is *unverified*, not *assumed good*: fail closed.

A ``match`` is not taken on its word (audit AG-4). A block is verified only when its chunks'
matches tile it exactly — each chunk matched in the cited section at the very offsets the
splitter cut it from, so together they run from the block's first code point to its last with
nothing between them but the spaces the cuts dropped — and every hash agrees: each match names
the section's own ``normalizedTextSha256``, each answer hashes the chunk that was sent, the
block's text and XHTML hash to the values the citation shows, and the answer was computed under
the normalisation version the agent was built against.

``post_check`` is pure. ``run_post_check`` is the driver that performs the ``verify_quote``
calls and then calls it, so the decision procedure can be tested without a server and the
wiring can be tested without a model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast, final

from .answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from .contract import (
    VERIFY_QUOTE_MAX_UTF16,
    QuoteVerification,
    ToolResult,
    chunk_spans,
    sha256_hex,
    utf16_length,
)
from .quote_edge import NORMALIZATION_VERSION, has_scanner_marker

__all__ = [
    "MAX_CONCURRENT_CHECKS",
    "CheckedAnswer",
    "CheckedBlock",
    "ChunkCheck",
    "VerificationFlag",
    "VerifyQuote",
    "post_check",
    "run_post_check",
]

VerificationFlag = Literal[
    "no-match",
    "verification-missing",
    "verification-unavailable",
    "section-mismatch",
    "document-mismatch",
    "empty-block",
    "coverage-gap",
    "checksum-mismatch",
    "table-not-quotable",
]

BlockStatus = Literal["verified", "unverified"]

MAX_CONCURRENT_CHECKS: Final = 4
"""How many ``verify_quote`` calls one turn has in flight at once. A 14,000 code-point section is
eight calls; one after another they were eight round trips end to end (audit AG-10)."""


@final
@dataclass(frozen=True, slots=True)
class ChunkCheck:
    """One ``verify_quote`` answer for one chunk of a block.

    ``verification`` is the validated ``QuoteVerification`` output, or ``None`` when the call
    did not produce one or the chunk was not sent. It carries no quote text: the index is enough
    to say which chunk, because the chunks are a function of the block's text.
    """

    index: int
    verification: QuoteVerification | None


@final
@dataclass(frozen=True, slots=True)
class CheckedBlock:
    """A block after the post-check. ``flags`` is empty exactly when ``status`` is verified."""

    block_id: str
    citation: Citation
    text: str
    status: BlockStatus
    flags: tuple[VerificationFlag, ...]
    chunks_checked: int


class _PostCheckWitness:
    """Unforgeable outside this module: the accepted instance is module-private.

    Constructing another ``_PostCheckWitness`` produces a different object, and
    ``CheckedAnswer`` compares by identity — so the only way to obtain a ``CheckedAnswer`` is
    to call ``post_check``.
    """

    __slots__ = ()


_WITNESS = _PostCheckWitness()


@final
class CheckedAnswer:
    """The only answer type the renderers accept. Produced by ``post_check``, never built."""

    __slots__ = ("assistant", "blocks", "sections_not_shown")

    def __init__(
        self,
        witness: _PostCheckWitness,
        blocks: tuple[CheckedBlock, ...],
        assistant: AssistantPart,
        sections_not_shown: int = 0,
    ) -> None:
        if witness is not _WITNESS:
            raise TypeError("a CheckedAnswer comes from post_check(); it is not constructed")
        self.blocks = blocks
        self.assistant = assistant
        self.sections_not_shown = sections_not_shown

    @property
    def verified_blocks(self) -> tuple[CheckedBlock, ...]:
        """The blocks whose status is ``verified``."""
        return tuple(block for block in self.blocks if block.status == "verified")

    @property
    def flagged_blocks(self) -> tuple[CheckedBlock, ...]:
        """Every block whose status is not ``verified``."""
        return tuple(block for block in self.blocks if block.status != "verified")


def post_check(
    draft: DraftAnswer, verifications: Mapping[str, Sequence[ChunkCheck]]
) -> CheckedAnswer:
    """Decide, per block, whether the store still says what the block says. Pure."""
    checked = tuple(
        _check_block(block, verifications.get(block.block_id, ())) for block in draft.blocks
    )
    return CheckedAnswer(_WITNESS, checked, draft.assistant, draft.sections_not_shown)


def _check_block(block: QuotedBlock, checks: Sequence[ChunkCheck]) -> CheckedBlock:
    spans = chunk_spans(block.text)
    flags: list[VerificationFlag] = []
    if not spans:
        flags.append("empty-block")
    citation = block.citation
    # The checksums the reader is shown must be the ones of the text shown: a section whose own
    # hashes disagree with its text or its XHTML is not the section the citation describes.
    if (
        sha256_hex(block.text) != citation.normalized_text_sha256
        or block.div_sha256 != citation.narrative_div_sha256
    ):
        flags.append("checksum-mismatch")
    by_index: dict[int, ChunkCheck] = {}
    for check in checks:
        if check.index in by_index or not 0 <= check.index < len(spans):
            # An answer for no chunk, or a second answer for one: the answers do not describe
            # this block's chunks, so they cannot cover it.
            flags.append("coverage-gap")
            continue
        by_index[check.index] = check
    for index, span in enumerate(spans):
        chunk = block.text[span[0] : span[1]]
        if has_scanner_marker(chunk):
            # A table or a picture: verify_quote refuses its markers, so it was never sent.
            flags.append("table-not-quotable")
            continue
        found = by_index.get(index)
        if found is None:
            # Part of the block was never asked about.
            flags.append("verification-missing")
            continue
        flags.extend(_flags_for(block, found, span, chunk))
    ordered = tuple(sorted(set(flags), key=_FLAG_ORDER.index))
    status: BlockStatus = "verified" if not ordered else "unverified"
    return CheckedBlock(
        block_id=block.block_id,
        citation=citation,
        text=block.text,
        status=status,
        flags=ordered,
        chunks_checked=len(checks),
    )


def _flags_for(
    block: QuotedBlock, check: ChunkCheck, span: tuple[int, int], chunk: str
) -> tuple[VerificationFlag, ...]:
    verification = check.verification
    if verification is None:
        return ("verification-unavailable",)
    citation = block.citation
    flags: list[VerificationFlag] = []
    document = verification["document"]
    if document["bundleId"] != citation.bundle_id or document["versionId"] != citation.version_id:
        # The store answered about a different document version from the one cited.
        flags.append("document-mismatch")
    if (
        verification["quoteSha256"] != sha256_hex(chunk)
        or verification["normalizationVersion"] != NORMALIZATION_VERSION
    ):
        # The answer is about some other quote, or was decided under rules this agent does not
        # hold: either way it says nothing about this chunk.
        flags.append("checksum-mismatch")
    if verification["result"] != "match":
        flags.append("no-match")
        return tuple(flags)
    match = verification.get("match")
    if match is None or verification["sectionsSearched"] < 1:
        # A match that does not say where: the contract refuses one since 4.0.0; refused here too.
        flags.append("coverage-gap")
        return tuple(flags)
    if match["sourceKey"] != citation.source_key:
        # The text is in the label, but not in the section the citation names.
        flags.append("section-mismatch")
    elif (match["startOffset"], match["endOffset"]) != span:
        # Found, but not where this chunk sits in the block: the matches do not tile the block,
        # so a gap between them (a dropped sentence, a truncated end) would go unseen.
        flags.append("coverage-gap")
    if match["normalizedTextSha256"] != citation.normalized_text_sha256:
        flags.append("checksum-mismatch")
    return tuple(flags)


_FLAG_ORDER: tuple[VerificationFlag, ...] = (
    "no-match",
    "section-mismatch",
    "document-mismatch",
    "coverage-gap",
    "checksum-mismatch",
    "verification-missing",
    "verification-unavailable",
    "table-not-quotable",
    "empty-block",
)

VerifyQuote = Callable[[str, str, str, str], Awaitable[ToolResult]]
"""``(bundle_id, version_id, source_key, quote) -> ToolResult`` for the ``verify_quote`` tool."""


async def run_post_check(
    draft: DraftAnswer, verify_quote: VerifyQuote, concurrency: int = MAX_CONCURRENT_CHECKS
) -> CheckedAnswer:
    """Call ``verify_quote`` for every chunk of every block, then decide. The driver.

    At most ``concurrency`` calls are in flight at once; each answer is kept at its chunk's
    index, so the order they come back in changes nothing.
    """
    gate = asyncio.Semaphore(concurrency)

    async def check(block: QuotedBlock, index: int, chunk: str) -> ChunkCheck:
        if utf16_length(chunk) > VERIFY_QUOTE_MAX_UTF16 or has_scanner_marker(chunk):
            # Over the bound (the splitter found no cut the quote-edge rule accepts inside it,
            # and the contract refuses a longer quote), or a table's or a picture's markers,
            # which the service refuses: nothing is sent, and the block says why.
            return ChunkCheck(index=index, verification=None)
        async with gate:
            result = await verify_quote(
                block.citation.bundle_id,
                block.citation.version_id,
                block.citation.source_key,
                chunk,
            )
        value = cast(QuoteVerification, result.value) if result.available else None
        return ChunkCheck(index=index, verification=value)

    # A task group, not gather: when one call raises, the others are cancelled rather than left
    # running against the service after the turn has already ended.
    async with asyncio.TaskGroup() as group:
        pending = [
            (block.block_id, group.create_task(check(block, index, block.text[start:end])))
            for block in draft.blocks
            for index, (start, end) in enumerate(chunk_spans(block.text))
        ]
    verifications: dict[str, list[ChunkCheck]] = {block.block_id: [] for block in draft.blocks}
    for block_id, task in pending:
        verifications[block_id].append(task.result())
    return post_check(draft, verifications)
