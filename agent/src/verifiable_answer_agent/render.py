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

import html
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
CHECKSUMS_EXPLAINED: Final = (
    "Checksums: verify_quote confirms the normalised text's; the approved narrative's is "
    "the stored XHTML's, recomputed from what the store returned."
)

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
    needs is the product, the version and the checksums, which follow it on their own lines. The
    block id is an internal handle and belongs in the audit record, not in a person's reading.
    The checksums are written in full: this is the surface Gemini Enterprise shows, and a
    checksum a reader cannot copy is not evidence.

    The assistant's words come last, between a label and an end line, and inside a fenced code
    block (``_fenced``): Gemini Enterprise renders this text as Markdown, and inside a fence
    nothing is Markdown or HTML, so no emphasis, entity, comment or tag the model writes can make
    its words render as anything but its own words in a box.
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
    lines.extend(_fenced(sanitise_assistant(answer).text))
    lines.append(f"[{ASSISTANT_END}]")
    return "\n".join(lines)


def _fenced(text: str) -> list[str]:
    """``text`` as a fenced code block that nothing inside it can close.

    A fence is chosen over escaping every Markdown and HTML character. CommonMark and GitHub
    Flavored Markdown, which Gemini Enterprise's Markdown follows, parse nothing inside a fenced
    block — no emphasis, no link, no entity, no HTML tag or comment — and close it only at a line
    of at least as many backticks as opened it; the fence here is one longer than the longest
    run of backticks in the text, so no line of the text can close it (and a tilde fence never
    closes a backtick one). Escaping depends on the renderer honouring backslash escapes for
    every character that could matter, shows the backslashes wherever it does not, and draws no
    boundary; a fence is a visible box where Markdown is rendered and two plain marker lines
    where it is not. ``text`` has no line breaks but line feeds (``sanitise_assistant`` splits on
    every kind), so a carriage return cannot end a line early.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}text", text, fence]


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
# plain-text surface, exactly like a checked block (review of 2026-09-22). Each is compared as
# ``_probe`` reads a line: letters and digits only.
_RESERVED_OPENINGS: Final = (
    VERIFIED_LABEL,
    ASSISTANT_LABEL,
    ASSISTANT_END,
    READ_IN_TURN,
    PRODUCT_NAMED,
    PRODUCT_UNCONFIRMED,
    CHECKSUMS_EXPLAINED,
    "Checksum of the ",
)
# The status line of an unverified block, in its own capitals only: "Not verified by me" is the
# assistant's own words, and a forged "NOT VERIFIED" could only make label text look less
# trustworthy, never more.
_UNVERIFIED_PROBE: Final = "NOTVERIFIED"
# The citation line's whole shape, not its first two words: "From section 4.2 you can see" is
# ordinary speech.
_CITATION_SHAPE: Final = re.compile(r"fromsection.{1,200}?ofdocumentversion")

# A checksum-like run: 32 or more hexadecimal digits standing alone, however Markdown emphasis or
# code marks are threaded through it. Only a checked block may carry one; the model has been seen
# inventing them (deploy/README.md, 2026-09-22). Read after compatibility folding and with
# zero-width characters gone, so neither fullwidth digits nor a zero-width space hides one.
_HEX_RUN: Final = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Fa-f](?:[*_~`\\]*[0-9A-Fa-f]){31,}(?![0-9A-Za-z])"
)
# A document identifier named by its field, with the value after it. The value is removed only
# when it looks like an identifier (a digit or one of . _ : / + - in it); "the version ID shown
# with each block" is prose, and keeps its next word.
_IDENTIFIER: Final = re.compile(
    r"\b(?:bundle|version|source|narrativeDiv|normalizedText|quote)[ _-]?(?:id|key|sha256)\b"
    r"[\s`'\"*]*[:=]?[\s`'\"*]*(?P<value>[A-Za-z0-9._:/+-]+)",
    re.IGNORECASE,
)
_ID_LIKE: Final = re.compile(r"[0-9._:/+-]")
_HTML_COMMENT: Final = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_HTML_TAG: Final = re.compile(r"</?[A-Za-z][^>]*>")
# Latin look-alikes from Cyrillic and Greek, folded before a line is compared with a reserved
# label. Partial by design: the fence is the structural guarantee; this is defence in depth.
_CONFUSABLES: Final = str.maketrans(
    "аеорсухіјѕԁԛԝүһӏвкмнтАВЕКМНОРСТУХІЈЅαονικρτυχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ",
    "aeopcyxijsdqwyhlbkmhtABEKMHOPCTYXIJSaovikptuxABEZHIKMNOPTYX",
)
# How many words in a row the assistant may share with a block before it is pointed out.
_REPEATED_WORDS: Final = 8


def _shown(line: str) -> str:
    """A line of the assistant's words as it is shown: compatibility-folded, invisibles gone.

    NFKC folds fullwidth and other compatibility forms to their plain letters and digits;
    format characters and every other zero-width or default-ignorable code point are removed;
    any remaining gap (a no-break or thin space) becomes a plain space.
    """
    folded = unicodedata.normalize("NFKC", line)
    kept: list[str] = []
    for character in folded:
        if unicodedata.category(character) == "Cf":
            continue
        if is_gap(character):
            if character in _INVISIBLE_GAPS:
                continue
            kept.append(" ")
            continue
        kept.append(character)
    return "".join(kept)


# The gaps that draw nothing (zero-width and default-ignorable): removed, not turned to spaces.
_INVISIBLE_GAPS: Final = frozenset(
    chr(point)
    for point in (0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFFA0, *range(0x180B, 0x1810))
) | frozenset(chr(point) for point in (*range(0x200B, 0x2010), *range(0x2060, 0x2070)))


def _probe(line: str, *, keep_case: bool = False) -> str:
    """A shown line as a reader would take it in, letters and digits only.

    HTML entities are decoded and HTML comments and tags dropped (a Markdown surface renders them
    away); Cyrillic and Greek look-alikes are folded to Latin; then everything but letters and
    digits goes — spaces, Markdown emphasis, table bars, list markers, emoji — and any leading
    digits (a list number). Casefolded unless ``keep_case``.
    """
    text = _HTML_TAG.sub("", _HTML_COMMENT.sub("", html.unescape(line)))
    text = unicodedata.normalize("NFKC", text).translate(_CONFUSABLES)
    letters = "".join(character for character in text if character.isalnum())
    letters = letters.lstrip("0123456789")
    return letters if keep_case else letters.casefold()


_RESERVED_PROBES: Final = tuple(_probe(opening) for opening in _RESERVED_OPENINGS)


def _reserved(line: str) -> bool:
    """Whether a shown line opens with, or has the shape of, a label reserved for checked text."""
    probe = _probe(line)
    return (
        probe.startswith(_RESERVED_PROBES)
        or _CITATION_SHAPE.match(probe) is not None
        or _probe(line, keep_case=True).startswith(_UNVERIFIED_PROBE)
    )


def _without_identifiers(line: str) -> tuple[str, int]:
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        if _ID_LIKE.search(match.group("value")) is None:
            return match.group(0)
        removed += 1
        return "[identifier removed]"

    return _IDENTIFIER.sub(replace, line), removed


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def _shingles(words: list[str]) -> set[tuple[str, ...]]:
    return {
        tuple(words[index : index + _REPEATED_WORDS])
        for index in range(len(words) - _REPEATED_WORDS + 1)
    }


def sanitise_assistant(answer: CheckedAnswer) -> AssistantView:
    """The assistant's words as shown, and the ``AssistantFlag`` values for the audit record.

    Pure, and a function of the answer alone, so every renderer and the audit record agree. The
    words are split on every kind of line break (``str.splitlines``: carriage returns, U+0085,
    U+2028 and the rest, not only line feeds), each line is shown folded and without invisibles
    (``_shown``), and the filters run on what is shown. On the text surface the result is then
    fenced (``render_text``), which is the structural guarantee; the filters are defence in depth.
    """
    flags: set[AssistantFlag] = set()
    kept: list[str] = []
    removed_lines = 0
    for raw in answer.assistant.text.splitlines():
        line = _shown(raw)
        if _reserved(line):
            removed_lines += 1
            continue
        without_checksums, checksums = _HEX_RUN.subn("[checksum removed]", line)
        shown, identifiers = _without_identifiers(without_checksums)
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
    """The citation as a person reads it: section and version, product, and both checksums.

    Which checksum is confirmed by what (audit review, L5): ``verify_quote`` confirms the
    normalised text's hash — every match names it, and the post-check requires that it is this
    one; the approved narrative's hash is the stored XHTML's, recomputed by the agent from the
    XHTML ``get_section`` returned and required to agree with it, and not re-confirmed by the
    service at check time. The answer says so under each block.
    """
    citation = block.citation
    return [
        f"From section {citation.source_key} of document version {citation.version_id} "
        f"({citation.bundle_id})",
        _product_line(block),
        f"Checksum of the approved narrative: {citation.narrative_div_sha256}",
        f"Checksum of the normalised text: {citation.normalized_text_sha256}",
        CHECKSUMS_EXPLAINED,
    ]
