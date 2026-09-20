"""Rendering: the only place an answer becomes something a person reads.

Every function here takes a ``CheckedAnswer`` and nothing else. A ``DraftAnswer`` has no
renderer, so there is no expression in this package that puts an unchecked quotation in front
of a user. That is the invariant, stated in the signatures.

Two surfaces:

- **A2UI** (v0.9.1) where the surface renders structured UI — Gemini Enterprise does. One
  ``createSurface`` envelope followed by one ``updateComponents`` envelope carrying a flat
  adjacency list, exactly as the specification describes them.
- **Plain structured text** everywhere else. Same content, same order, same labels; no
  Markdown emphasis, because the quoted text must not be decorated.

Both put the citation next to the quotation and the verification status on the block itself,
and both label the assistant's own words as the assistant's own words.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from .postcheck import CheckedAnswer, CheckedBlock

__all__ = [
    "A2UI_CATALOG_ID",
    "A2UI_EXTENSION_URI",
    "A2UI_VERSION",
    "Surface",
    "render",
    "render_a2ui",
    "render_text",
]

A2UI_VERSION: Final = "v0.9.1"

A2UI_CATALOG_ID: Final = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
"""The basic catalog's own declared ``catalogId``.

Note it is ``v0_9``, not ``v0_9_1``: the catalog document served from the v0.9.1 path declares
the v0.9 identifier, while the v0.9.1 specification's ``createSurface`` example writes
``v0_9_1``. The catalog document is the thing a renderer matches against, so its own value is
used here. Recorded in ``README.md``; re-check when A2UI reaches 1.0, which also renames
``theme`` to ``surfaceProperties``.
"""

A2UI_EXTENSION_URI: Final = "https://a2ui.org/a2a-extension/a2ui/v0.9.1"
"""Advertised in ``AgentCapabilities.extensions``; A2UI travels as an ``application/a2ui+json``
DataPart. A surface that does not advertise it gets ``render_text`` instead."""

Surface = Literal["a2ui", "text"]

ASSISTANT_LABEL: Final = "The assistant's own words (not label content)"
VERIFIED_LABEL: Final = "Verified against the store after composition"
UNVERIFIED_LABEL: Final = "NOT VERIFIED"

_FLAG_TEXT: Final[dict[str, str]] = {
    "no-match": "verify_quote returned no-match for part of this block",
    "section-mismatch": "the text matched a different section from the one cited",
    "document-mismatch": "the store answered about a different document version",
    "verification-missing": "part of this block was never sent to verify_quote",
    "verification-unavailable": "verify_quote did not return a usable answer",
    "empty-block": "the block carried no text to check",
}


def render(answer: CheckedAnswer, surface: Surface) -> list[dict[str, Any]] | str:
    """A2UI envelopes where the surface renders them, plain structured text where it does not."""
    if surface == "a2ui":
        return render_a2ui(answer)
    return render_text(answer)


def render_a2ui(
    answer: CheckedAnswer, surface_id: str = "verifiable_answer"
) -> list[dict[str, Any]]:
    """The two envelopes that make one answer card, per the A2UI v0.9.1 envelope structure."""
    components: list[dict[str, Any]] = []
    children: list[str] = []

    for block in answer.blocks:
        card_id = f"{block.block_id}_card"
        body_id = f"{block.block_id}_body"
        components.append({"id": card_id, "component": "Card", "child": body_id})
        components.append(
            {
                "id": body_id,
                "component": "Column",
                "children": [
                    f"{block.block_id}_status",
                    f"{block.block_id}_quote",
                    f"{block.block_id}_citation",
                ],
            }
        )
        components.append(
            {
                "id": f"{block.block_id}_status",
                "component": "Text",
                "variant": "h5",
                "text": _status_line(block),
            }
        )
        # The quotation itself: body text, undecorated, exactly as the tool returned it.
        components.append(
            {"id": f"{block.block_id}_quote", "component": "Text", "text": block.text}
        )
        components.append(
            {
                "id": f"{block.block_id}_citation",
                "component": "Text",
                "variant": "caption",
                "text": _citation_line(block),
            }
        )
        children.append(card_id)

    children.append("assistant_divider")
    components.append({"id": "assistant_divider", "component": "Divider", "axis": "horizontal"})
    children.append("assistant_label")
    components.append(
        {"id": "assistant_label", "component": "Text", "variant": "h5", "text": ASSISTANT_LABEL}
    )
    children.append("assistant_text")
    components.append({"id": "assistant_text", "component": "Text", "text": answer.assistant.text})

    # "One of the components in one of the component lists MUST have an id of root."
    components.insert(0, {"id": "root", "component": "Column", "children": children})

    return [
        {
            "version": A2UI_VERSION,
            "createSurface": {"surfaceId": surface_id, "catalogId": A2UI_CATALOG_ID},
        },
        {
            "version": A2UI_VERSION,
            "updateComponents": {"surfaceId": surface_id, "components": components},
        },
    ]


def render_text(answer: CheckedAnswer) -> str:
    """The same answer where no structured surface exists. Labels, not decoration."""
    lines: list[str] = []
    for block in answer.blocks:
        lines.append(f"[{block.block_id}] {_status_line(block)}")
        lines.append(block.text)
        lines.append(_citation_line(block))
        lines.append("")
    lines.append(f"[{ASSISTANT_LABEL}]")
    lines.append(answer.assistant.text)
    return "\n".join(lines)


def _status_line(block: CheckedBlock) -> str:
    if block.status == "verified":
        return VERIFIED_LABEL
    reasons = "; ".join(_FLAG_TEXT[flag] for flag in block.flags)
    return f"{UNVERIFIED_LABEL} — {reasons}" if reasons else UNVERIFIED_LABEL


def _citation_line(block: CheckedBlock) -> str:
    citation = block.citation
    return (
        f"bundleId {citation.bundle_id} · versionId {citation.version_id} · "
        f"sourceKey {citation.source_key} · narrativeDivSha256 {citation.narrative_div_sha256}"
    )
