"""Composition: a pure function from tool results to the answer's structure.

The model chooses which sections to fetch and what to say about them. It does not choose the
words of a quoted block, their order, or their citations — those are a function of the tool
results alone, computed here. Given the same ``get_section`` and ``find_product`` results and the
same assistant text, this returns the same answer, byte for byte, every time.

``ToolResult`` values that are unavailable (a malformed result, an error, a transport failure)
contribute nothing: they are dropped, and the caller learns how many were dropped so the audit
record can say so.

Every section the model fetched in the turn is shown, whether or not the model went on to rely
on it: the blocks are what was read, not a selection the model vouches for, and the answer says
so. At most ``MAX_BLOCKS`` are shown, in the order they were fetched; the answer says how many
more were read and not shown.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast, final

from .answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from .contract import SectionContent, ToolResult, sha256_hex

__all__ = ["MAX_BLOCKS", "Composition", "ProductFacts", "compose", "product_facts"]

MAX_BLOCKS: Final = 8
"""The most sections one answer shows. A turn that read more says how many it did not show."""

ProductFacts = Mapping[tuple[str, str], tuple[str, str]]
"""``(bundleId, versionId) -> (productName, language)``, from a turn's ``find_product`` results."""


@final
@dataclass(frozen=True, slots=True)
class Composition:
    """A draft answer plus the counts the audit record needs. Carries no narrative itself.

    ``sections_not_shown`` counts distinct sections read beyond ``MAX_BLOCKS``.
    """

    draft: DraftAnswer
    sections_used: int
    sections_dropped: int
    sections_not_shown: int = 0


def product_facts(
    find_product_results: Sequence[ToolResult],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Product name and language per document version, from this turn's ``find_product`` answers.

    A document version two answers describe differently is left out: the reader is told the
    product was not confirmed rather than shown one of two.
    """
    facts: dict[tuple[str, str], tuple[str, str]] = {}
    conflicting: set[tuple[str, str]] = set()
    for result in find_product_results:
        if result.tool != "find_product" or result.value is None:
            continue
        for product in cast(list[dict[str, Any]], result.value.get("products", [])):
            document = product["document"]
            key = (document["bundleId"], document["versionId"])
            value = (product["productName"], product["language"])
            if facts.get(key, value) != value:
                conflicting.add(key)
            facts[key] = value
    return {key: value for key, value in facts.items() if key not in conflicting}


def compose(
    section_results: Sequence[ToolResult], assistant_text: str, products: ProductFacts | None = None
) -> Composition:
    """Build the answer structure from ``get_section`` results and the assistant's own text."""
    blocks: list[QuotedBlock] = []
    seen: set[tuple[str, str, str]] = set()
    dropped = 0
    not_shown = 0
    for result in section_results:
        if result.tool != "get_section" or result.value is None:
            dropped += 1
            continue
        section = cast(SectionContent, result.value)
        document = section["document"]
        key = (document["bundleId"], document["versionId"], section["sourceKey"])
        if key in seen:
            # The same section fetched twice is one block, not two. Order of first appearance.
            continue
        seen.add(key)
        if len(blocks) >= MAX_BLOCKS:
            not_shown += 1
            continue
        facts = (products or {}).get((document["bundleId"], document["versionId"]))
        blocks.append(
            QuotedBlock(
                block_id=f"block-{len(blocks) + 1:02d}",
                citation=Citation(
                    bundle_id=document["bundleId"],
                    version_id=document["versionId"],
                    source_key=section["sourceKey"],
                    narrative_div_sha256=section["narrativeDivSha256"],
                    normalized_text_sha256=section["normalizedTextSha256"],
                    product_name=facts[0] if facts is not None else None,
                    language=facts[1] if facts is not None else None,
                ),
                # ``text`` is the section's normalised plain text: the same form ``verify_quote``
                # compares against, so the post-check asks the store about exactly what is shown.
                text=section["text"],
                div_sha256=sha256_hex(section["div"]),
            )
        )
    draft = DraftAnswer(
        blocks=tuple(blocks),
        assistant=AssistantPart(text=assistant_text),
        sections_not_shown=not_shown,
    )
    return Composition(
        draft=draft,
        sections_used=len(blocks),
        sections_dropped=dropped,
        sections_not_shown=not_shown,
    )
