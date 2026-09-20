"""The shape of an answer, before the post-check.

Two kinds of part, and the distinction is the product:

- a **quoted block** is label content. It is verbatim from a tool result and carries the
  citation that lets a reader recompute its hash against the store.
- the **assistant part** is the model's own words. It is labelled as such wherever it is shown
  and it is never presented as label content.

Nothing in this module can be rendered. ``postcheck.CheckedAnswer`` is the only type the
renderers accept, and ``postcheck.post_check`` is the only way to obtain one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

__all__ = ["AssistantPart", "Citation", "DraftAnswer", "QuotedBlock"]


@final
@dataclass(frozen=True, slots=True)
class Citation:
    """Where a block came from, in the four fields a reader needs to check it."""

    bundle_id: str
    version_id: str
    source_key: str
    narrative_div_sha256: str


@final
@dataclass(frozen=True, slots=True)
class QuotedBlock:
    """A candidate quotation. Not yet checked, therefore not yet showable as label content."""

    block_id: str
    citation: Citation
    text: str


@final
@dataclass(frozen=True, slots=True)
class AssistantPart:
    """The model's free text. The only place in an answer where the model's words appear."""

    text: str


@final
@dataclass(frozen=True, slots=True)
class DraftAnswer:
    """The composed answer before the post-check has run. Never rendered."""

    blocks: tuple[QuotedBlock, ...]
    assistant: AssistantPart
