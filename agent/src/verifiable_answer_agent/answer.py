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
    """Where a block came from: the fields a reader needs to check it, and what it is about.

    ``normalized_text_sha256`` is the section's hash of the text shown, which every
    ``verify_quote`` match must name. ``product_name`` and ``language`` come from a
    ``find_product`` result of the same turn for the same document version, and are ``None``
    when the turn looked up no such result: the reader is then told they were not confirmed,
    rather than shown a guess.
    """

    bundle_id: str
    version_id: str
    source_key: str
    narrative_div_sha256: str
    normalized_text_sha256: str
    product_name: str | None = None
    language: str | None = None


@final
@dataclass(frozen=True, slots=True)
class QuotedBlock:
    """A candidate quotation. Not yet checked, therefore not yet showable as label content.

    ``div_sha256`` is the SHA-256 of the section's stored XHTML as the tool returned it, taken
    when the block was composed: the post-check holds the citation's ``narrative_div_sha256`` —
    the checksum a reader is shown — to it.
    """

    block_id: str
    citation: Citation
    text: str
    div_sha256: str


@final
@dataclass(frozen=True, slots=True)
class AssistantPart:
    """The model's free text. The only place in an answer where the model's words appear."""

    text: str


@final
@dataclass(frozen=True, slots=True)
class DraftAnswer:
    """The composed answer before the post-check has run. Never rendered.

    ``sections_not_shown`` counts sections read in the turn beyond the most one answer shows.
    """

    blocks: tuple[QuotedBlock, ...]
    assistant: AssistantPart
    sections_not_shown: int = 0
