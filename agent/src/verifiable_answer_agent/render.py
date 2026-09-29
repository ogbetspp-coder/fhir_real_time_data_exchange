"""Rendering: the only place an answer becomes something a person reads.

Every function here takes a ``CheckedAnswer`` and nothing else. A ``DraftAnswer`` has no
renderer, so there is no expression in this package that puts an unchecked quotation in front
of a user. That is the invariant, stated in the signatures.

One surface, structured text, which is what the deployed agent returns: Gemini Enterprise
receives the answer as the text of the turn's final event and renders it as Markdown. Every block
and the assistant's words are fenced code blocks, so the verbatim display of a quotation depends
on the fence, not on what the label happens to contain (``render_text``). An A2UI renderer was
built and never sent anywhere; it was removed in refactor R1 (in history at ``250d8a2``), until a
surface that renders A2UI is on the roadmap.

The citation stands next to the quotation and the verification status on the block itself, and
the assistant's own words are labelled as the assistant's own words. The assistant's words are
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
from typing import Final, Literal, final

from .postcheck import CheckedAnswer, CheckedBlock
from .quote_edge import is_default_ignorable, is_gap

__all__ = ["AssistantFlag", "AssistantView", "render_text", "sanitise_assistant"]

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


def render_text(answer: CheckedAnswer) -> str:
    """The same answer where no structured surface exists: Markdown that shows only literal text.

    Gemini Enterprise renders this text as Markdown, so nothing in it is left for Markdown to
    read (review of PR #129, M2). Each block — its status, its quotation and its reading lines —
    is one fenced code block (``_fenced``), in which nothing is parsed: a label's own "<", "*",
    "_", "~", "&micro;", a backslash, or a line opening "#", "1." or ">", is shown as the store
    holds it, and a "<!--" in a label cannot swallow what follows. So is the assistant's part,
    last, between
    a label and an end line. Between the fences are only this module's own fixed sentences. Every
    element is its own paragraph, a blank line apart, so none runs into the next.

    What a reader needs first is whether the block was verified and the quotation; what an
    auditor needs is the product, the version and the checksums, which follow it. The quotation is
    wrapped at spaces to ``_WRAP`` characters a line — a normalised section is one line, and a
    code block does not wrap it — so joining its lines with single spaces gives back the stored
    text exactly. The block id is an internal handle and belongs in the audit record, not in a
    person's reading. The checksums are written in full: a checksum a reader cannot copy is not
    evidence.
    """
    parts: list[str] = []
    for block in answer.blocks:
        body = [_status_line(block), "", *_wrapped(block.text), "", *_reading_lines(block)]
        parts.append("\n".join(_fenced("\n".join(body))))
    parts.extend(_after_blocks(answer))
    parts.append(f"[{ASSISTANT_LABEL}]")
    parts.append("\n".join(_fenced(sanitise_assistant(answer).text)))
    parts.append(f"[{ASSISTANT_END}]")
    return "\n\n".join(parts)


# The widest line a quotation is wrapped to inside its fence.
_WRAP: Final = 80


def _wrapped(text: str) -> list[str]:
    """``text`` broken at single spaces into lines of at most ``_WRAP`` characters.

    A word longer than the width is a line of its own. Each break replaces exactly one space, so
    ``" ".join`` of the lines is ``text`` again. A normalised section has no line breaks; a block
    whose text has one is not the store's normalised text, so it never verifies, and its breaks
    are kept as they are rather than joined away.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        current: list[str] = []
        width = 0
        for word in line.split(" "):
            added = len(word) + (1 if current else 0)
            if current and width + added > _WRAP:
                lines.append(" ".join(current))
                current, width = [word], len(word)
            else:
                current.append(word)
                width += added
        lines.append(" ".join(current))
    return lines


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
    where it is not. The text's line breaks of every kind are made line feeds first, so a
    carriage return cannot end a line where it was not counted. A render through a CommonMark
    parser is in ``tests/test_render.py``.
    """
    body = "\n".join(text.splitlines())
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}text", body, fence]


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

# A checksum-like run: 32 or more hexadecimal digits in a row, however Markdown emphasis or code
# marks are threaded through it, with a "0x" before it if there is one. Only a checked block may
# carry one; the model has been seen inventing them (deploy/README.md, 2026-09-22). Matched on the
# folded copy (``_folded``), so neither fullwidth digits nor any invisible code point hides one;
# removed from the line as written. No letter boundary is asked for: "h<64 hex>" and "<64 hex>h"
# are a checksum with a letter glued on (review of PR #129, round 3), and 32 hexadecimal digits in
# a row are not an English word.
_HEX_RUN: Final = re.compile(r"(?:0[xX])?[0-9A-Fa-f](?:[*_~`\\]*[0-9A-Fa-f]){31,}")
# A document identifier named by its field, with the value after it. The value is removed only
# when it looks like an identifier (a digit or one of . _ : / + - in it); "the version ID shown
# with each block" is prose, and keeps its next word. The separators are bounded, and the colon
# group optional as a whole: two unbounded runs of the same characters side by side made the
# match quadratic (20,000 spaces took 5.6 s, review of PR #129).
_IDENTIFIER: Final = re.compile(
    r"\b(?:bundle|version|source|narrativeDiv|normalizedText|quote)[ _-]?(?:id|key|sha256)\b"
    r"[\s`'\"*]{0,16}(?:[:=][\s`'\"*]{0,16})?(?P<value>[A-Za-z0-9._:/+-]+)",
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
# The longest the assistant's words are shown: more is cut, and the answer says so. The model has
# no reason to write more, and the filters below are linear but not free.
MAX_ASSISTANT_CHARS: Final = 20_000

# The joiners: kept in the words as shown, because Persian and other scripts spell words with
# them and emoji sequences are built with them.
_JOINERS: Final = frozenset(("\u200c", "\u200d"))
# The C0 and C1 controls but tab: taken out of the words as shown.
_CONTROLS: Final = frozenset(
    chr(point) for point in (*range(0x09), *range(0x0A, 0x20), *range(0x7F, 0xA0))
)


def _hidden(character: str) -> bool:
    """Whether a code point is taken out of the assistant's words as shown.

    Every Default_Ignorable_Code_Point but the joiners — code points that draw nothing and can
    hide or reorder what is drawn: zero-width space, word joiner and the invisible operators, the
    byte order mark, the bidirectional marks, embeddings, overrides and isolates, the tag
    characters (U+E0000 to U+E007F, which can carry a whole sentence nobody sees), and the variation
    selectors (an emoji may lose its colour presentation) — and the C0 and C1 controls but tab.
    Nothing else is changed: "10⁹/L", "m²" and "½" are shown as written (review of PR #129, M1).
    """
    return character in _CONTROLS or (is_default_ignorable(character) and character not in _JOINERS)


def _visible(line: str) -> str:
    """The line as it is shown: only the code points ``_hidden`` names removed."""
    return "".join(character for character in line if not _hidden(character))


def _folded(line: str) -> tuple[str, list[int]]:
    """The copy of a shown line the patterns read, and where each of its code points came from.

    Each code point is folded on its own when it draws as the plain form it folds to (fullwidth,
    ligatures: ``_fold``); format characters, every Default_Ignorable_Code_Point (variation
    selectors, tag characters and U+FFF0 to U+FFF8 among them) and combining marks are dropped,
    so none threaded
    through a checksum hides it; any remaining gap becomes a space. ``origin[i]`` is the index in
    ``line`` of the code point that gave the folded copy's ``i``th, so a match found here is
    removed from the line as written, and a folded line is never shown.
    """
    folded: list[str] = []
    origin: list[int] = []
    for index, character in enumerate(line):
        if unicodedata.category(character)[0] in "M" or _dropped_when_folded(character):
            continue
        for piece in _fold(character):
            folded.append(" " if is_gap(piece) else piece)
            origin.append(index)
    return "".join(folded), origin


# The compatibility forms that draw as the plain letters and digits they fold to — fullwidth,
# halfwidth, ligatures and the like, mathematical letters — and so can spell a checksum or an
# identifier. A superscript, subscript, fraction or circled number does not draw as a plain digit,
# and is left as itself: "⁹" after a checksum is the exponent of "10⁹/L", not its 65th digit.
_FOLDED_FORMS: Final = frozenset(("<wide>", "<narrow>", "<compat>", "<font>"))


def _fold(character: str) -> str:
    decomposition = unicodedata.decomposition(character)
    tag = decomposition.split(" ", 1)[0] if decomposition.startswith("<") else None
    if tag is not None and tag not in _FOLDED_FORMS:
        return character
    return unicodedata.normalize("NFKC", character)


def _dropped_when_folded(character: str) -> bool:
    return unicodedata.category(character) == "Cf" or is_default_ignorable(character)


def _probe(line: str, *, keep_case: bool = False) -> str:
    """A line as a reader would take it in, letters and digits only.

    HTML entities are decoded and HTML comments and tags dropped (a Markdown surface renders them
    away); the line is compatibility-folded and Cyrillic and Greek look-alikes are folded to
    Latin; then everything but letters and digits goes — spaces, zero-width characters, Markdown
    emphasis, table bars, list markers, emoji — and any leading digits (a list number).
    Casefolded unless ``keep_case``. Used only to decide; never shown.
    """
    text = _HTML_TAG.sub("", _HTML_COMMENT.sub("", html.unescape(line)))
    text = unicodedata.normalize("NFKC", text).translate(_CONFUSABLES)
    letters = "".join(character for character in text if character.isalnum())
    letters = letters.lstrip("0123456789")
    return letters if keep_case else letters.casefold()


_RESERVED_PROBES: Final = tuple(_probe(opening) for opening in _RESERVED_OPENINGS)


def _reserved(line: str) -> bool:
    """Whether a line opens with, or has the shape of, a label reserved for checked text."""
    probe = _probe(line)
    return (
        probe.startswith(_RESERVED_PROBES)
        or _CITATION_SHAPE.match(probe) is not None
        or _probe(line, keep_case=True).startswith(_UNVERIFIED_PROBE)
    )


def _removals(line: str) -> tuple[str, bool, bool]:
    """``line`` with every checksum and identifier the folded copy shows cut out of it as written.

    Returns the line and whether a checksum and whether an identifier was removed. Overlapping
    finds are removed as one span, marked as an identifier if either was one.
    """
    folded, origin = _folded(line)
    spans: list[tuple[int, int, bool]] = [
        (match.start(), match.end(), False) for match in _HEX_RUN.finditer(folded)
    ]
    spans.extend(
        (match.start(), match.end(), True)
        for match in _IDENTIFIER.finditer(folded)
        if _ID_LIKE.search(match.group("value")) is not None
    )
    if not spans:
        return line, False, False
    merged: list[list[int]] = []
    for start, end, identifier in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] |= identifier
        else:
            merged.append([start, end, int(identifier)])
    shown = line
    for start, end, named in reversed(merged):
        marker = "[identifier removed]" if named else "[checksum removed]"
        low, high = _whole_characters(origin, start, end)
        shown = shown[:low] + marker + shown[high:]
    return (
        shown,
        any(not identifier for _, _, identifier in spans),
        any(identifier for _, _, identifier in spans),
    )


def _whole_characters(origin: list[int], start: int, end: int) -> tuple[int, int]:
    """The span of ``line`` to cut for the folded match ``[start, end)``: whole characters only.

    A character that folds to several code points ("½" to three) and lies only partly inside the
    match is kept: the cut shrinks inward to the characters wholly inside it. Round 2
    cut the whole character at either edge, so "½" or "⁹" glued to a checksum went with it
    (review of PR #129, round 3).
    """
    low = origin[start]
    if start > 0 and origin[start - 1] == low:
        low += 1
    high = origin[end - 1] + 1
    if end < len(origin) and origin[end] == high - 1:
        high -= 1
    return low, max(low, high)


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
    words are cut at ``MAX_ASSISTANT_CHARS``, split on every kind of line break
    (``str.splitlines``: carriage returns, U+0085, U+2028 and the rest, not only line feeds), and
    each line is shown as written less the code points that draw nothing (``_visible``). The
    patterns read a folded copy (``_probe``, ``_folded``) and act on the line as written: a line
    with a reserved label is dropped whole, a checksum or identifier is cut out where it stands.
    On the text surface the result is then fenced (``render_text``), which is the structural
    guarantee; the filters are defence in depth.
    """
    flags: set[AssistantFlag] = set()
    kept: list[str] = []
    removed_lines = 0
    words = answer.assistant.text
    cut = len(words) > MAX_ASSISTANT_CHARS
    for raw in words[:MAX_ASSISTANT_CHARS].splitlines():
        line = _visible(raw)
        if _reserved(line):
            removed_lines += 1
            continue
        shown, checksum, identifier = _removals(line)
        if checksum:
            flags.add("checksum-removed")
        if identifier:
            flags.add("identifier-removed")
        kept.append(shown)
    notes: list[str] = []
    if cut:
        notes.append(
            f"(The assistant's words were cut here: they ran past {MAX_ASSISTANT_CHARS:,} "
            "characters.)"
        )
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
    if block_shingles & _shingles(_words(words[:MAX_ASSISTANT_CHARS])):
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
