"""Composition: a pure function from tool results to the answer's structure.

The model chooses which sections to fetch and what to say about them. It does not choose the
words of a quoted block, their order, or their citations — those are a function of the tool
results alone, computed here. Given the same ``get_section`` results and the same assistant
text, this returns the same answer, byte for byte, every time.

``ToolResult`` values that are unavailable (a malformed result, an error, a transport failure)
contribute nothing: they are dropped, and the caller learns how many were dropped so the audit
record can say so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast, final

from .answer import AssistantPart, Citation, DraftAnswer, QuotedBlock
from .contract import SectionContent, ToolResult

__all__ = ["Composition", "compose"]


@final
@dataclass(frozen=True, slots=True)
class Composition:
    """A draft answer plus the counts the audit record needs. Carries no narrative itself."""

    draft: DraftAnswer
    sections_used: int
    sections_dropped: int


def compose(section_results: Sequence[ToolResult], assistant_text: str) -> Composition:
    """Build the answer structure from ``get_section`` results and the assistant's own text."""
    blocks: list[QuotedBlock] = []
    seen: set[tuple[str, str, str]] = set()
    dropped = 0
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
        blocks.append(
            QuotedBlock(
                block_id=f"block-{len(blocks) + 1:02d}",
                citation=Citation(
                    bundle_id=document["bundleId"],
                    version_id=document["versionId"],
                    source_key=section["sourceKey"],
                    narrative_div_sha256=section["narrativeDivSha256"],
                ),
                # ``text`` is the section's normalised plain text: the same form ``verify_quote``
                # compares against, so the post-check asks the store about exactly what is shown.
                text=section["text"],
            )
        )
    draft = DraftAnswer(blocks=tuple(blocks), assistant=AssistantPart(text=assistant_text))
    return Composition(draft=draft, sections_used=len(blocks), sections_dropped=dropped)
