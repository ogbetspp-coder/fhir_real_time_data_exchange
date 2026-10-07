"""The published tool contract, and the rule that an unvalidated result is not a result.

The agent shares nothing with the worker or with Zone A except two published schemas:
``contracts/generated/query-tools.schema.json``, the surface of the query service, and
``contracts/generated/agent-turn.schema.json``, the shape of the agent's own turn record
(ADR 0004, "pure libraries are shared, and versioned" — here it is not even code, it is a
schema). Both are vendored into the package by ``scripts/sync_contract.py`` so the deployable
is self-contained on Agent Engine, and CI fails if either copy has drifted.

query-tools 5.0.0. Since 2.0.0, ``FindProductOutput`` carries ``truncated``, and
``not-entitled`` is no longer an error code a caller can see — outside the caller's entitlement
the service answers ``document-not-found``. Nothing here ever matched on an error code (an
``isError`` result is unavailable whatever its code), so that change alters no behaviour at this
end. 2.0.1 changed descriptions and made ``verify_quote``'s ``match`` stricter. 3.0.0 is a major
(an output grammar widened and error delivery changed): a product identifier's value may carry
"/" (an EMA ePI id), which this module now accepts as the schema does, and an ``isError`` result
no longer carries ``structuredContent``, which nothing here read; this agent must be deployed
with 3.0.0 or later no later than the service. 4.0.0 makes ``QuoteVerification`` a union on
``result``: a ``match`` without its location, or over no section, no longer validates, so it is
unavailable here rather than a match. The schema cannot say that ``startOffset`` comes before
``endOffset``; ``postcheck`` holds every offset to the chunk's own, which is stricter. 4.1.0 adds an
optional ``contractVersion`` to the service's audit record, which the agent never reads; no tool
answer changes. 5.0.0 (signed approvals, ``docs/design/approval.md``) adds the error code
``not-approved``, which this module reads as it reads any ``isError`` result (unavailable), an
optional ``approval`` on ``get_section`` and ``get_provenance``, which nothing here reads yet, and
two optional audit fields. With the service's verification on, every answer carries ``approval``,
which a 4.1.0 copy refuses as schema-invalid: this agent must be deployed with 5.0.0 before the
service's verification is turned on.

Patterns are read as the schema's dialect reads them (``ecma_pattern``): Python's ``re``, which
``jsonschema`` uses unchanged, let ``$`` match before a final newline and a digit class match any
Unicode digit.

Every tool result is validated against the schema before anything reads it. A result that does
not validate is *unavailable*: it is never composed, never rendered, and never quoted. That is
the only safe reading of a malformed answer from a service whose whole purpose is fidelity.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Final, Literal, NotRequired, TypedDict, final

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError

from .quote_edge import SignIndex, edge_after, edge_before, is_gap, number_from

__all__ = [
    "AGENT_TURN_RESOURCE",
    "CONTRACT_RESOURCE",
    "VERIFY_QUOTE_MAX_UTF16",
    "DocumentRef",
    "EcmaDraft202012Validator",
    "FindProductOutput",
    "ProvenanceDetail",
    "QueryToolName",
    "QuoteMatch",
    "QuoteVerification",
    "SectionContent",
    "ToolResult",
    "UnavailableReason",
    "chunk_spans",
    "contract_version",
    "ecma_pattern",
    "load_agent_turn_schema",
    "load_schema",
    "sha256_hex",
    "split_for_verification",
    "utf16_length",
    "validate_tool_output",
]

CONTRACT_RESOURCE: Final = "query-tools.schema.json"
AGENT_TURN_RESOURCE: Final = "agent-turn.schema.json"

QueryToolName = Literal["find_product", "get_section", "get_provenance", "verify_quote"]

# Which ``$defs`` entry is the output of which tool. Taken from the schema's own
# ``tools.<name>.output`` reference; asserted against the schema in tests so a contract change
# that moves a tool's output type cannot pass silently.
_OUTPUT_DEF: Final[dict[QueryToolName, str]] = {
    "find_product": "FindProductOutput",
    "get_section": "SectionContent",
    "get_provenance": "ProvenanceDetail",
    "verify_quote": "QuoteVerification",
}

UnavailableReason = Literal["schema-invalid", "tool-error", "transport-error", "not-an-object"]


class DocumentRef(TypedDict):
    """One published version of a document Bundle in the validated store."""

    bundleId: str
    versionId: str
    lastUpdated: str


class SectionContent(TypedDict):
    """One QRD section, verbatim, as ``get_section`` answers.

    ``div`` is the stored XHTML and ``text`` its normalised plain text; the hashes are
    recomputable from ``div``. ``provenanceResourceId`` is given for the document's current
    version only.
    """

    document: DocumentRef
    sourceKey: str
    path: str
    title: str
    div: str
    text: str
    narrativeDivSha256: str
    normalizedTextSha256: str
    normalizationVersion: str
    provenanceResourceId: NotRequired[str]
    contentNotice: str


class QuoteMatch(TypedDict):
    """Where a quote matched: the section, and code-point offsets in its normalised text."""

    sourceKey: str
    startOffset: int
    endOffset: int
    normalizedTextSha256: str


class QuoteVerification(TypedDict):
    """``verify_quote``'s answer: ``match``, with where the quote matched, or ``no-match``.

    One TypedDict for both members of the contract's union: ``match`` is present exactly when
    ``result`` is ``match``, which the schema enforces before anything reads it.
    """

    document: DocumentRef
    result: Literal["match", "no-match"]
    normalizationVersion: str
    quoteSha256: str
    sectionsSearched: int
    match: NotRequired[QuoteMatch]


class FindProductOutput(TypedDict):
    """``find_product``'s answer: the products found, and whether the search was cut short."""

    products: list[dict[str, Any]]
    # True when the caller's entitlement holds more documents than the service searched in one
    # call. An empty ``products`` with ``truncated`` true is not "no such product"; the
    # instruction tells the model to say the search was cut short and ask for a narrower name.
    truncated: bool


class ProvenanceDetail(TypedDict):
    """``get_provenance``'s answer: who and what put the document in the store.

    The hashes of the source document, the fidelity report and the approved content; the
    extractor, model and approver; and, for a named section, its hashes recomputed live.
    """

    document: DocumentRef
    provenanceResourceId: str
    recorded: str
    sourceDocumentSha256: str
    fidelityReportSha256: str
    approvedContentSha256: str
    extractor: dict[str, str]
    approver: dict[str, str]
    model: NotRequired[dict[str, str]]
    section: NotRequired[dict[str, str]]


@final
@dataclass(frozen=True, slots=True)
class ToolResult:
    """A tool's answer, after validation. ``value`` is ``None`` exactly when unavailable."""

    tool: QueryToolName
    value: dict[str, Any] | None
    reason: UnavailableReason | None

    @property
    def available(self) -> bool:
        """Whether the tool answered with a result that validated."""
        return self.value is not None


def _vendored(name: str) -> dict[str, Any]:
    text = (
        resources.files("verifiable_answer_agent.contracts")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    schema: dict[str, Any] = json.loads(text)
    return schema


@cache
def load_schema() -> dict[str, Any]:
    """The vendored copy of ``contracts/generated/query-tools.schema.json``."""
    return _vendored(CONTRACT_RESOURCE)


@cache
def load_agent_turn_schema() -> dict[str, Any]:
    """The vendored copy of ``contracts/generated/agent-turn.schema.json``.

    Nothing at runtime validates against it: ``audit.TurnAuditRecord`` is built to its shape,
    and ``tests/test_audit.py`` is where an emitted record is checked against it.
    """
    return _vendored(AGENT_TURN_RESOURCE)


def contract_version(schema: dict[str, Any]) -> str:
    """A vendored contract's version, as its ``$id`` names it (``<name>/<version>/schema.json``)."""
    return str(schema["$id"]).rsplit("/", 2)[-2]


@cache
def ecma_pattern(pattern: str) -> re.Pattern[str]:
    r"""A contract pattern compiled to mean what it means in the schema's own dialect.

    JSON Schema 2020-12 reads ``pattern`` as an ECMA-262 regular expression, and Zod (the
    service) agrees. Python's ``re``, which ``jsonschema`` uses as it is, differs in three places,
    each of which let this agent accept an answer the service's own contract refuses (audit C-7;
    the #145 review, L2-d): ``$`` also matches before a final ``\n`` (a hash followed by a newline
    passed ``Sha256Hex``), ``.`` also matches ``\r``, U+2028 and U+2029, and ``\d`` is any Unicode
    digit. Outside a character class, an unescaped ``$`` is compiled as ``\Z`` and an unescaped
    ``.`` as ``[^\n\r\u2028\u2029]``; and the pattern is compiled with ``re.ASCII``, so a shorthand
    class, which the generator refuses to publish, would still mean ASCII.
    """
    out: list[str] = []
    in_class = False
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            out.append(pattern[index : index + 2])
            index += 2
            continue
        if in_class:
            in_class = character != "]"
        elif character == "[":
            in_class = True
        elif character == "$":
            character = r"\Z"
        elif character == ".":
            character = "[^\n\r\u2028\u2029]"
        out.append(character)
        index += 1
    return re.compile("".join(out), re.ASCII)


def _pattern(
    validator: Any, pattern: str, instance: object, schema: dict[str, Any]
) -> Iterator[ValidationError]:
    """The ``pattern`` keyword, by ``ecma_pattern``. The error never quotes the instance."""
    del schema
    if validator.is_type(instance, "string") and not ecma_pattern(pattern).search(str(instance)):
        yield ValidationError("does not match the contract's pattern")


# jsonschema types `extend` loosely; what it returns is a Draft 2020-12 validator class.
EcmaDraft202012Validator: Final[type[Draft202012Validator]] = validators.extend(  # type: ignore[no-untyped-call]
    Draft202012Validator, {"pattern": _pattern}
)
"""Draft 2020-12 with the ``pattern`` keyword read as ECMA-262 reads it (``ecma_pattern``).

Every schema the agent validates against, its own turn record's in the tests included, is read
with this class, so a value passes here exactly when it passes the service's Zod contract:
``tests/test_contract_verdicts.py`` holds it to every verdict of the contract verdict corpus.
"""


@cache
def _validator(tool: QueryToolName) -> Draft202012Validator:
    schema = load_schema()
    # A ``$ref`` into the contract's own ``$defs``, so every nested reference still resolves and
    # the agent validates against exactly the published definitions, not a paraphrase of them.
    subschema = {"$ref": f"#/$defs/{_OUTPUT_DEF[tool]}", "$defs": schema["$defs"]}
    EcmaDraft202012Validator.check_schema(subschema)
    # No format checker: every ``format`` in this contract is accompanied by a ``pattern`` that
    # says the same thing, so format assertion would add a dependency and no strictness.
    return EcmaDraft202012Validator(subschema)


def validate_tool_output(tool: QueryToolName, payload: object) -> ToolResult:
    """Validate one tool result. Anything that does not validate is unavailable, not content."""
    if not isinstance(payload, dict):
        return ToolResult(tool=tool, value=None, reason="not-an-object")
    try:
        _validator(tool).validate(payload)
    except ValidationError:
        # The exception carries the offending instance, which for ``get_section`` is narrative.
        # It is dropped here and never re-raised, logged, or attached to the result.
        return ToolResult(tool=tool, value=None, reason="schema-invalid")
    return ToolResult(tool=tool, value=dict(payload), reason=None)


def utf16_length(text: str) -> int:
    """A string's length as TypeScript counts it: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


VERIFY_QUOTE_MAX_UTF16: Final = 2000
"""Upper bound on one ``verify_quote`` argument, counted the way the service counts it.

``VerifyQuoteInput.quote`` is ``maxLength: 2000``. JSON Schema defines ``maxLength`` over code
points; the service that enforces it is TypeScript, where a string length is UTF-16 code units.
For anything outside the Basic Multilingual Plane the two disagree, and the smaller of the two
is the safe one — so the splitter below measures UTF-16 units. Recorded in ``README.md`` as a
contract ambiguity rather than resolved by guessing.
"""


def split_for_verification(text: str, limit: int = VERIFY_QUOTE_MAX_UTF16) -> tuple[str, ...]:
    """Split a block into contiguous substrings that ``verify_quote`` can each confirm.

    The substrings of ``text`` at the spans ``chunk_spans`` finds; see there for the rule.
    """
    return tuple(text[start:end] for start, end in chunk_spans(text, limit))


def chunk_spans(text: str, limit: int = VERIFY_QUOTE_MAX_UTF16) -> tuple[tuple[int, int], ...]:
    """Where to split a block for ``verify_quote``: ``(start, end)`` code-point offsets in ``text``.

    Deterministic, and every chunk is a contiguous substring of ``text``, so ``verify_quote``
    can find each one in the stored section — at exactly these offsets, which is what the
    post-check holds each match to. Cuts fall only on a U+0020 where the service's quote-edge
    rule (``quote_edge``) holds on both sides: the chunk before it must end on a boundary and the
    chunk after it must begin on one. So a cut never falls between the groups of a space-grouped
    number ("1 000" | "000 IU") or after a comparator or sign set off by a space ("CrCl ≥" |
    "30 ml/min"), either of which the service answers ``no-match`` although the block is the
    label's own text. The separator is dropped rather than carried into either chunk, because a
    leading or trailing space is exactly what normalisation would remove.

    Within a window of ``limit`` UTF-16 units the last acceptable cut is taken. The bound: a
    chunk is longer than ``limit`` only when its first ``limit`` units hold no acceptable cut at
    all — a single token longer than the window, or an unbroken run of space-grouped digits —
    and it then ends at the first acceptable cut after the window, or at the end of the block.
    A cut the rule refuses would be a certain ``no-match``, presented as a finding about the
    text; a chunk over the bound is not sent (``postcheck.run_post_check``) and the block is
    flagged ``verification-unavailable``. Either way the block is not verified; only the second
    says why truthfully.

    The chunks are then evened out (review of PR #129): with as many chunks as the window of
    ``limit`` needs, the smallest window that needs no more is used, so a block just over one
    window is two halves rather than a full chunk and a sliver — a sliver of a few words is the
    chunk most likely to occur elsewhere in the section and so to match at other offsets. Not
    done when a chunk is over the bound, where the greedy split is kept as it is.

    Each pass is linear in the block: the signs index is built once for the whole text (the
    service reads a cut's sign over the whole section too), and each window is measured once;
    evening out takes about a dozen passes.
    """
    if limit < 1:
        raise ValueError("limit must be at least one UTF-16 code unit")
    start, end = 0, len(text)
    while start < end and text[start] == " ":
        start += 1
    while end > start and text[end - 1] == " ":
        end -= 1
    signs = SignIndex(text)
    greedy = _greedy_spans(text, start, end, limit, signs)
    if len(greedy) < 2 or not _within(text, greedy, limit):
        return greedy
    low, high = -(-utf16_length(text[start:end]) // len(greedy)), limit
    best = greedy
    while low < high:
        window = (low + high) // 2
        candidate = _greedy_spans(text, start, end, window, signs)
        if len(candidate) <= len(greedy) and _within(text, candidate, limit):
            best, high = candidate, window
        else:
            low = window + 1
    return best


def _within(text: str, spans: tuple[tuple[int, int], ...], limit: int) -> bool:
    return all(utf16_length(text[start:end]) <= limit for start, end in spans)


def _greedy_spans(
    text: str, start: int, end: int, limit: int, signs: SignIndex
) -> tuple[tuple[int, int], ...]:
    """Each chunk to the last acceptable cut inside a window of ``limit`` units."""
    spans: list[tuple[int, int]] = []
    while start < end:
        fits = _window_end(text, start, end, limit)
        if fits == end:
            spans.append((start, end))
            break
        cut = _last_cut(text, start, end, fits, signs)
        if cut is None:
            cut = _first_cut(text, start, end, fits, signs)
        if cut is None:
            spans.append((start, end))
            break
        spans.append((start, cut))
        start = cut
        while start < end and text[start] == " ":
            start += 1
    return tuple(spans)


def _acceptable_cut(text: str, low: int, end: int, index: int, signs: SignIndex) -> bool:
    """A cut at the run of spaces starting at ``index``, leaving both new edges on a boundary.

    ``low`` is where the chunk before the cut starts and ``end`` where the block's text ends. The
    number before the cut is read within that chunk, as the service reads a quote's last number
    within the quote; beyond each new edge the service reads the section, so the whole text is
    read there, signs from the index built once for it. The block's own two ends are not cuts:
    the service reads the section beyond them, so a block that itself begins or ends inside a
    number or before a sign, or whose last chunk reaches its end past an opening mark before a
    sign, is refused at that chunk; and so is a cut inside a table cell, where the service also
    reads the cells beside it and the splitter sees no grid (each a false failure, never a false
    pass).
    """
    if index <= low or text[index] != " " or text[index - 1] == " ":
        return False
    resume = index
    while resume < end and text[resume] == " ":
        resume += 1
    if resume >= end:
        return False
    return edge_after(text, index, _number_before_within(text, index, low)) and edge_before(
        text, resume, number_from(text, resume), signs=signs
    )


def _number_before_within(text: str, index: int, low: int) -> str | None:
    """``number_before`` that stops at ``low``: the last number of the chunk ``text[low:index]``."""
    index -= 1
    while index >= low and (is_gap(text[index]) or unicodedata.category(text[index])[0] == "M"):
        index -= 1
    return text[index] if index >= low else None


def _last_cut(text: str, low: int, end: int, fits: int, signs: SignIndex) -> int | None:
    """The last acceptable cut whose chunk before it ends at or before ``fits``."""
    index = text.rfind(" ", low, fits + 1)
    while index > low:
        if _acceptable_cut(text, low, end, index, signs):
            return index
        index = text.rfind(" ", low, index)
    return None


def _first_cut(text: str, low: int, end: int, fits: int, signs: SignIndex) -> int | None:
    """The first acceptable cut after the window: where a chunk that cannot fit ends."""
    index = text.find(" ", fits + 1, end)
    while index >= 0:
        if _acceptable_cut(text, low, end, index, signs):
            return index
        index = text.find(" ", index + 1, end)
    return None


def _window_end(text: str, start: int, end: int, limit: int) -> int:
    """The end of the longest run of ``text[start:end]`` that is at most ``limit`` UTF-16 units.

    ``end`` when the whole run fits. Never splits a surrogate pair: a character costing two units
    is left out whole.
    """
    units = 0
    for index in range(start, end):
        units += 2 if ord(text[index]) > 0xFFFF else 1
        if units > limit:
            return index
    return end


def sha256_hex(text: str) -> str:
    """The SHA-256 of ``text``'s UTF-8 bytes, as the service writes its hashes.

    Not normalisation and not a fidelity check: every hash the agent compares is one the service
    computed over the very string it returned beside it (``sha256Utf8`` in ``src/query/tools.ts``).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
