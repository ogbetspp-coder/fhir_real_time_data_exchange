"""The published tool contract, and the rule that an unvalidated result is not a result.

The agent shares nothing with the worker or with Zone A except two published schemas:
``contracts/generated/query-tools.schema.json``, the surface of the query service, and
``contracts/generated/agent-turn.schema.json``, the shape of the agent's own turn record
(ADR 0004, "pure libraries are shared, and versioned" — here it is not even code, it is a
schema). Both are vendored into the package by ``scripts/sync_contract.py`` so the deployable
is self-contained on Agent Engine, and CI fails if either copy has drifted.

query-tools 2.0.0: ``FindProductOutput`` carries ``truncated``, and ``not-entitled`` is no
longer an error code a caller can see — outside the caller's entitlement the service answers
``document-not-found``. Nothing here ever matched on an error code (an ``isError`` result is
unavailable whatever its code), so the second change alters no behaviour at this end.

Every tool result is validated against the schema before anything reads it. A result that does
not validate is *unavailable*: it is never composed, never rendered, and never quoted. That is
the only safe reading of a malformed answer from a service whose whole purpose is fidelity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Final, Literal, NotRequired, TypedDict, final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

__all__ = [
    "AGENT_TURN_RESOURCE",
    "CONTRACT_RESOURCE",
    "VERIFY_QUOTE_MAX_UTF16",
    "DocumentRef",
    "FindProductOutput",
    "ProvenanceDetail",
    "QueryToolName",
    "QuoteMatch",
    "QuoteVerification",
    "SectionContent",
    "ToolResult",
    "UnavailableReason",
    "load_agent_turn_schema",
    "load_schema",
    "split_for_verification",
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
    bundleId: str
    versionId: str
    lastUpdated: str


class SectionContent(TypedDict):
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
    sourceKey: str
    startOffset: int
    endOffset: int
    normalizedTextSha256: str


class QuoteVerification(TypedDict):
    document: DocumentRef
    result: Literal["match", "no-match"]
    normalizationVersion: str
    quoteSha256: str
    sectionsSearched: int
    match: NotRequired[QuoteMatch]


class FindProductOutput(TypedDict):
    products: list[dict[str, Any]]
    # True when the caller's entitlement holds more documents than the service searched in one
    # call. An empty ``products`` with ``truncated`` true is not "no such product"; the
    # instruction tells the model to say the search was cut short and ask for a narrower name.
    truncated: bool


class ProvenanceDetail(TypedDict):
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


@cache
def _validator(tool: QueryToolName) -> Draft202012Validator:
    schema = load_schema()
    # A ``$ref`` into the contract's own ``$defs``, so every nested reference still resolves and
    # the agent validates against exactly the published definitions, not a paraphrase of them.
    subschema = {"$ref": f"#/$defs/{_OUTPUT_DEF[tool]}", "$defs": schema["$defs"]}
    Draft202012Validator.check_schema(subschema)
    # No format checker: every ``format`` in this contract is accompanied by a ``pattern`` that
    # says the same thing, so format assertion would add a dependency and no strictness.
    return Draft202012Validator(subschema)


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


def _utf16_length(text: str) -> int:
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
    """Split a block into contiguous substrings each short enough to pass to ``verify_quote``.

    Deterministic, and every chunk is a contiguous substring of ``text``, so ``verify_quote``
    can find each one in the stored section. Cuts fall on a U+0020 where one exists inside the
    window — the separator is dropped rather than carried into either chunk, because a leading
    or trailing space is exactly what normalisation would remove — and at the hard limit when a
    single token is longer than the whole window.
    """
    if limit < 1:
        raise ValueError("limit must be at least one UTF-16 code unit")
    remainder = text.strip(" ")
    chunks: list[str] = []
    while remainder:
        if _utf16_length(remainder) <= limit:
            chunks.append(remainder)
            break
        window = _window(remainder, limit)
        cut = window.rfind(" ")
        if cut <= 0:
            chunks.append(window)
            remainder = remainder[len(window) :].lstrip(" ")
            continue
        chunks.append(window[:cut])
        remainder = remainder[cut:].lstrip(" ")
    return tuple(chunk for chunk in chunks if chunk)


def _window(text: str, limit: int) -> str:
    """The longest prefix of ``text`` that is at most ``limit`` UTF-16 code units.

    Never splits a surrogate pair: a character costing two units is dropped whole.
    """
    units = 0
    for index, character in enumerate(text):
        cost = 2 if ord(character) > 0xFFFF else 1
        if units + cost > limit:
            return text[:index]
        units += cost
    return text
