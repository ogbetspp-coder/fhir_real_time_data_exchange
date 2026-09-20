"""Layer 2: the mechanical post-check.

Every block of label content goes back through ``verify_quote`` before anything can show it.
A block that does not come back ``match`` is flagged, on the block, where the reader sees it.

The invariant is carried by the types, not by a comment. ``render`` accepts only a
``CheckedAnswer``; a ``CheckedAnswer`` can be constructed only with the module-private witness
held by ``post_check``; and ``post_check`` takes the verification results as an argument, so
there is no path from a ``DraftAnswer`` to a rendered answer that does not pass through here.
A block with no verification is *unverified*, not *assumed good*: fail closed.

``post_check`` is pure. ``run_post_check`` is the driver that performs the ``verify_quote``
calls and then calls it, so the decision procedure can be tested without a server and the
wiring can be tested without a model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast, final

from .answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from .contract import QuoteVerification, ToolResult, split_for_verification

__all__ = [
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
]

BlockStatus = Literal["verified", "unverified"]


@final
@dataclass(frozen=True, slots=True)
class ChunkCheck:
    """One ``verify_quote`` answer for one chunk of a block.

    ``verification`` is the validated ``QuoteVerification`` output, or ``None`` when the call
    did not produce one. It carries no quote text: the index is enough to say which chunk.
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

    __slots__ = ("assistant", "blocks")

    def __init__(
        self, witness: _PostCheckWitness, blocks: tuple[CheckedBlock, ...], assistant: AssistantPart
    ) -> None:
        if witness is not _WITNESS:
            raise TypeError("a CheckedAnswer comes from post_check(); it is not constructed")
        self.blocks = blocks
        self.assistant = assistant

    @property
    def verified_blocks(self) -> tuple[CheckedBlock, ...]:
        return tuple(block for block in self.blocks if block.status == "verified")

    @property
    def flagged_blocks(self) -> tuple[CheckedBlock, ...]:
        return tuple(block for block in self.blocks if block.status != "verified")


def post_check(
    draft: DraftAnswer, verifications: Mapping[str, Sequence[ChunkCheck]]
) -> CheckedAnswer:
    """Decide, per block, whether the store still says what the block says. Pure."""
    checked = tuple(
        _check_block(block, verifications.get(block.block_id, ())) for block in draft.blocks
    )
    return CheckedAnswer(_WITNESS, checked, draft.assistant)


def _check_block(block: QuotedBlock, checks: Sequence[ChunkCheck]) -> CheckedBlock:
    expected_chunks = len(split_for_verification(block.text))
    flags: list[VerificationFlag] = []
    if expected_chunks == 0:
        flags.append("empty-block")
    if len(checks) != expected_chunks:
        # Fewer answers than chunks means part of the block was never asked about.
        flags.append("verification-missing")
    for check in checks:
        flags.extend(_flags_for(block.citation, check))
    ordered = tuple(sorted(set(flags), key=_FLAG_ORDER.index))
    status: BlockStatus = "verified" if not ordered else "unverified"
    return CheckedBlock(
        block_id=block.block_id,
        citation=block.citation,
        text=block.text,
        status=status,
        flags=ordered,
        chunks_checked=len(checks),
    )


def _flags_for(citation: Citation, check: ChunkCheck) -> tuple[VerificationFlag, ...]:
    verification = check.verification
    if verification is None:
        return ("verification-unavailable",)
    flags: list[VerificationFlag] = []
    if verification["result"] != "match":
        flags.append("no-match")
    document = verification["document"]
    if document["bundleId"] != citation.bundle_id or document["versionId"] != citation.version_id:
        # The store answered about a different document version from the one cited.
        flags.append("document-mismatch")
    match = verification.get("match")
    if match is not None and match["sourceKey"] != citation.source_key:
        # The text is in the label, but not in the section the citation names.
        flags.append("section-mismatch")
    return tuple(flags)


_FLAG_ORDER: tuple[VerificationFlag, ...] = (
    "no-match",
    "section-mismatch",
    "document-mismatch",
    "verification-missing",
    "verification-unavailable",
    "empty-block",
)

VerifyQuote = Callable[[str, str, str, str], Awaitable[ToolResult]]
"""``(bundle_id, version_id, source_key, quote) -> ToolResult`` for the ``verify_quote`` tool."""


async def run_post_check(draft: DraftAnswer, verify_quote: VerifyQuote) -> CheckedAnswer:
    """Call ``verify_quote`` for every chunk of every block, then decide. The driver."""
    verifications: dict[str, list[ChunkCheck]] = {}
    for block in draft.blocks:
        chunk_checks: list[ChunkCheck] = []
        for index, chunk in enumerate(split_for_verification(block.text)):
            result = await verify_quote(
                block.citation.bundle_id,
                block.citation.version_id,
                block.citation.source_key,
                chunk,
            )
            value = cast(QuoteVerification, result.value) if result.available else None
            chunk_checks.append(ChunkCheck(index=index, verification=value))
        verifications[block.block_id] = chunk_checks
    return post_check(draft, verifications)
