"""Rendering: the only place an answer becomes something a person reads.

Every function here takes a ``CheckedAnswer`` and nothing else. A ``DraftAnswer`` has no
renderer, so there is no expression in this package that puts an unchecked quotation in front
of a user. That is the invariant, stated in the signatures.

Two surfaces:

- **Plain structured text**, which is what the deployed agent returns (``finish`` asks for
  ``text``): Gemini Enterprise receives the answer as the text of the turn's final event. No
  Markdown emphasis, because the quoted text must not be decorated.
- **A2UI** (v0.9.1), built and tested but not yet sent anywhere: one ``createSurface``
  envelope followed by one ``updateComponents`` envelope carrying a flat adjacency list,
  exactly as the specification describes them. Sending it needs an A2A surface that
  advertises the extension; nothing in the deployed path does yet.

Both put the citation next to the quotation and the verification status on the block itself,
and both label the assistant's own words as the assistant's own words. The assistant's words are
never checked, so they are made unable to pass for a checked block (``sanitise_assistant``):
lines opening with a label reserved for checked text are removed, however they are dressed;
checksums and document identifiers are removed, because only a checked block may carry them;
and label text repeated outside a checked block is pointed out. What was done is said in the
answer and recorded in the turn's audit record.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Final, Literal, final

from .postcheck import CheckedAnswer, CheckedBlock
from .quote_edge import is_gap

__all__ = [
    "A2UI_CATALOG_ID",
    "A2UI_EXTENSION_URI",
    "A2UI_VERSION",
    "AssistantFlag",
    "AssistantView",
    "Surface",
    "render",
    "render_a2ui",
    "render_text",
    "sanitise_assistant",
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

AssistantFlag = Literal[
    "reserved-label-removed", "checksum-removed", "identifier-removed", "label-text-repeated"
]
"""``AssistantFlag`` in the agent-turn contract."""

ASSISTANT_LABEL: Final = "The assistant's own words (not label content)"
ASSISTANT_END: Final = "End of the assistant's own words"
VERIFIED_LABEL: Final = "Verified against the approved label"
UNVERIFIED_LABEL: Final = "NOT VERIFIED"
READ_IN_TURN: Final = (
    "These are the sections read in this turn, each re-checked against the approved label. "
    "Which of them answers the question is for you to judge."
)
PRODUCT_NAMED: Final = "Product named for this version"
PRODUCT_UNCONFIRMED: Final = "Product and language not confirmed"

_FLAG_TEXT: Final[dict[str, str]] = {
    "no-match": "verify_quote returned no-match for part of this block",
    "section-mismatch": "the text matched a different section from the one cited",
    "document-mismatch": "the store answered about a different document version",
    "coverage-gap": "the store's matches do not cover this block exactly, end to end",
    "checksum-mismatch": "a checksum or normalisation version does not agree with this block",
    "verification-missing": "part of this block was never sent to verify_quote",
    "verification-unavailable": "verify_quote did not return a usable answer",
    "table-not-quotable": "part of this block is a table or picture, which cannot be checked yet",
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
                    f"{block.block_id}_product",
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
                "id": f"{block.block_id}_product",
                "component": "Text",
                "variant": "caption",
                "text": _product_line(block),
            }
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

    for index, line in enumerate(_after_blocks(answer)):
        children.append(f"read_note_{index}")
        components.append({"id": f"read_note_{index}", "component": "Text", "text": line})

    view = sanitise_assistant(answer)
    children.append("assistant_divider")
    components.append({"id": "assistant_divider", "component": "Divider", "axis": "horizontal"})
    children.append("assistant_label")
    components.append(
        {"id": "assistant_label", "component": "Text", "variant": "h5", "text": ASSISTANT_LABEL}
    )
    children.append("assistant_text")
    components.append({"id": "assistant_text", "component": "Text", "text": view.text})

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
    """The same answer where no structured surface exists. Labels, not decoration.

    What a reader needs first is the quotation and whether it was verified; what an auditor
    needs is the product, the version and the checksum, which follow it on their own lines. The
    block id is an internal handle and belongs in the audit record, not in a person's reading.
    The checksum is written in full: this is the surface Gemini Enterprise shows, and a checksum
    a reader cannot copy is not evidence. The assistant's words come last, between a label and
    an end line, so a reader can see where they stop.
    """
    lines: list[str] = []
    for block in answer.blocks:
        lines.append(_status_line(block))
        lines.append(block.text)
        lines.extend(_reading_lines(block))
        lines.append("")
    after = _after_blocks(answer)
    if after:
        lines.extend(after)
        lines.append("")
    lines.append(f"[{ASSISTANT_LABEL}]")
    lines.append(sanitise_assistant(answer).text)
    lines.append(f"[{ASSISTANT_END}]")
    return "\n".join(lines)


def _after_blocks(answer: CheckedAnswer) -> list[str]:
    """What is said of the blocks as a whole: that they are what was read, and what is not shown."""
    lines: list[str] = []
    if answer.blocks:
        lines.append(READ_IN_TURN)
    if answer.sections_not_shown:
        lines.append(
            f"{answer.sections_not_shown} further section(s) were read in this turn and are not "
            "shown; ask for one by name to see it."
        )
    return lines


@final
@dataclass(frozen=True, slots=True)
class AssistantView:
    """The assistant's words as they are shown, and what was done to them."""

    text: str
    flags: tuple[AssistantFlag, ...]


# The lines this module writes to say what is checked label content. The assistant may not write
# them: a draft that opens with the verified label and closes with a citation line reads, on a
# plain-text surface, exactly like a checked block (review of 2026-09-22).
_RESERVED_OPENINGS: Final = (
    VERIFIED_LABEL,
    UNVERIFIED_LABEL,
    ASSISTANT_LABEL,
    ASSISTANT_END,
    READ_IN_TURN,
    PRODUCT_NAMED,
    PRODUCT_UNCONFIRMED,
    "From section ",
    "Checksum of the approved narrative",
)

# A checksum-like run: 32 or more hexadecimal digits standing alone. Only a checked block may
# carry one; the model has been seen inventing them (deploy/README.md, 2026-09-22).
_HEX_RUN: Final = re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{32,}(?![0-9A-Za-z])")
# A document identifier named by its field, with the value after it.
_IDENTIFIER: Final = re.compile(
    r"\b(?:bundle|version|source|narrativeDiv|normalizedText|quote)[ _-]?"
    r"(?:id|key|sha256)\b[\s`'\"*]*[:=]?[\s`'\"*]*[A-Za-z0-9._:/+-]*",
    re.IGNORECASE,
)
# How many words in a row the assistant may share with a block before it is pointed out.
_REPEATED_WORDS: Final = 8


def _probe(text: str) -> str:
    """A line as a reader would take it in: compatibility-folded, casefolded, gaps removed.

    Every gap (whitespace, zero-width and default-ignorable code points) is removed, not
    collapsed, so "From  section", and "From" and "section" joined by a zero-width space, both
    read as "fromsection"; and every
    leading character that is not a letter goes, so no emoji, number, table bar or Markdown
    marker in front of a reserved label hides it.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    squeezed = "".join(character for character in folded if not is_gap(character))
    index = 0
    while index < len(squeezed) and not squeezed[index].isalpha():
        index += 1
    return squeezed[index:]


_RESERVED_PROBES: Final = tuple(_probe(opening) for opening in _RESERVED_OPENINGS)


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def _shingles(words: list[str]) -> set[tuple[str, ...]]:
    return {
        tuple(words[index : index + _REPEATED_WORDS])
        for index in range(len(words) - _REPEATED_WORDS + 1)
    }


def sanitise_assistant(answer: CheckedAnswer) -> AssistantView:
    """The assistant's words as shown, and the ``AssistantFlag`` values for the audit record.

    Pure, and a function of the answer alone, so every renderer and the audit record agree.
    """
    flags: set[AssistantFlag] = set()
    kept: list[str] = []
    removed_lines = 0
    for line in answer.assistant.text.split("\n"):
        if _probe(line).startswith(_RESERVED_PROBES):
            removed_lines += 1
            continue
        without_checksums, checksums = _HEX_RUN.subn("[checksum removed]", line)
        shown, identifiers = _IDENTIFIER.subn("[identifier removed]", without_checksums)
        if checksums:
            flags.add("checksum-removed")
        if identifiers:
            flags.add("identifier-removed")
        kept.append(shown)
    notes: list[str] = []
    if removed_lines:
        flags.add("reserved-label-removed")
        notes.append(
            f"(Lines removed from the assistant's words: {removed_lines}. Each opened with a "
            "label this answer reserves for checked label text.)"
        )
    if flags & {"checksum-removed", "identifier-removed"}:
        notes.append(
            "(Checksums and document identifiers were removed from the assistant's words: only "
            "a checked block above carries them.)"
        )
    block_shingles: set[tuple[str, ...]] = set()
    for block in answer.blocks:
        block_shingles |= _shingles(_words(block.text))
    if block_shingles & _shingles(_words(answer.assistant.text)):
        flags.add("label-text-repeated")
        notes.append(
            "(The assistant's words repeat label text. They are not checked: read the checked "
            "blocks above, not this.)"
        )
    return AssistantView(text="\n".join(kept + notes), flags=tuple(sorted(flags)))


def _status_line(block: CheckedBlock) -> str:
    if block.status == "verified":
        return VERIFIED_LABEL
    reasons = "; ".join(_FLAG_TEXT[flag] for flag in block.flags)
    return f"{UNVERIFIED_LABEL} — {reasons}" if reasons else UNVERIFIED_LABEL


def _product_line(block: CheckedBlock) -> str:
    """The product and language the store names for this document version, or that none was.

    ``verify_quote`` proves a span is in the document cited, not that it is the right product
    or language; this line is what lets the reader check that part.
    """
    citation = block.citation
    if citation.product_name is None or citation.language is None:
        return (
            f"{PRODUCT_UNCONFIRMED}: no product lookup for this document version in this turn. "
            "Check the document before relying on it."
        )
    return f"{PRODUCT_NAMED}: {citation.product_name}, language {citation.language}"


def _citation_line(block: CheckedBlock) -> str:
    """Where the quotation came from, in the fields a reader needs to check it."""
    citation = block.citation
    return (
        f"bundleId {citation.bundle_id} · versionId {citation.version_id} · "
        f"sourceKey {citation.source_key} · narrativeDivSha256 {citation.narrative_div_sha256}"
    )


def _reading_lines(block: CheckedBlock) -> list[str]:
    """The citation as a person reads it: the section and version, the product, the checksum."""
    citation = block.citation
    return [
        f"From section {citation.source_key} of document version {citation.version_id} "
        f"({citation.bundle_id})",
        _product_line(block),
        f"Checksum of the approved narrative: {citation.narrative_div_sha256}",
    ]
