"""A fake ePI query service: the four tools, canned outputs, real streamable HTTP.

Canned but not invented. Every output is built from ``test/fixtures/contracts/*.json`` — the
repository's own synthetic submission, exported and gate-checked on the Node side — and every
one is validated against ``contracts/generated/query-tools.schema.json`` before the server will
serve it. A fixture that stopped satisfying the contract would fail the tests here rather than
let the agent be tested against a shape the real service cannot produce.

The transport is the real one: ``mcp``'s ``FastMCP`` over streamable HTTP, on an ephemeral
loopback port, with the real ADK toolset on the other end. What is faked is the store, not the
protocol. ``verify_quote`` decides as the service does, under its quote-edge rule
(``verifiable_answer_agent.quote_edge``, held to the service's exported answers by
``tests/test_quote_edge.py``), not by substring.
"""

from __future__ import annotations

import hashlib
import html
import json
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, final

import uvicorn
from mcp.server.fastmcp import FastMCP

from verifiable_answer_agent.contract import validate_tool_output
from verifiable_answer_agent.quote_edge import is_gap, locate_quote

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
FIXTURES: Final = REPOSITORY_ROOT / "test" / "fixtures" / "contracts"

BUNDLE_ID: Final = "synthetic-smpc"
VERSION_ID: Final = "1"
LAST_UPDATED: Final = "2026-09-19T00:00:00Z"
NORMALIZATION_VERSION: Final = "fidelity-norm/3.1.0"
CONTENT_NOTICE: Final = "document-content-not-instructions"


@final
@dataclass(frozen=True, slots=True)
class Section:
    """One canned ``get_section`` output plus the normalised text behind it."""

    source_key: str
    payload: dict[str, Any]

    @property
    def text(self) -> str:
        return str(self.payload["text"])


def _submission() -> Mapping[str, Any]:
    data: Mapping[str, Any] = json.loads(
        (FIXTURES / "canonical-submission.json").read_text(encoding="utf-8")
    )
    return data


def _composition(submission: Mapping[str, Any]) -> Mapping[str, Any]:
    for entry in submission["bundle"]["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] == "Composition":
            composition: Mapping[str, Any] = resource
            return composition
    raise AssertionError("the fixture carries no Composition")


# Section 3 of docs/fidelity-normalization.md, as far as deciding that a quote normalises to
# nothing needs it: the step 1 invisible characters, the step 4 bullet glyphs, the step 5
# whitespace. NFC and the ligatures never turn text into nothing, so they are not needed here.
_INVISIBLE: Final = frozenset("\u00ad\u200b\ufeff\u2060")
_BULLETS: Final = frozenset("\u2022\u2023\u25a0\u25a1\u25aa\u25ab\u25cb\u25cf\u25e6")
# From fidelity-norm/3.0.0 U+1680, U+2006, U+2009, U+200A, U+202F and U+205F are content, not
# whitespace.
_WHITESPACE: Final = frozenset(
    "\t\n\r \u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2007\u2008\u2028\u2029\u3000"
)


def _normalises_to_gaps(quote: str) -> bool:
    """Whether steps 1, 4 and 5 leave nothing of ``quote`` but gaps.

    Step 1 deletes the invisible characters, a soft hyphen taking a following line break with it.
    Step 4 removes a bullet glyph that starts a line (after a line feed, never at the start of the
    text, and past step 5 whitespace or bullets already removed) when step 5 whitespace follows it
    and its line holds no tab. What is left must hold a code point that is not a gap (section 6:
    whitespace, a thin space, a blank glyph, a code point Unicode says to ignore).
    """
    text = quote.replace("\u00ad\r\n", "").replace("\u00ad\n", "")
    text = "".join(character for character in text if character not in _INVISIBLE)
    lines = text.split("\n")
    for number, line in enumerate(lines):
        position = 0
        removable = number > 0 and "\t" not in line
        while position < len(line):
            character = line[position]
            if character in _WHITESPACE:
                position += 1
                continue
            # What follows the glyph: the next character, the line feed that ends this line, or
            # nothing at the end of the text.
            if position + 1 < len(line):
                followed_by_space = line[position + 1] in _WHITESPACE
            else:
                followed_by_space = number < len(lines) - 1
            if removable and character in _BULLETS and followed_by_space:
                position += 1
                continue
            # Past the line's start: a later bullet is content, and a gap is still nothing drawn.
            removable = False
            if is_gap(character):
                position += 1
                continue
            return False
    return True


# What fidelity-norm/3.0.0 adds to section 2: the interlinear annotation controls and the
# prepended concatenation marks.
_FORBIDDEN_3_0_0: Final = frozenset(
    {0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605, 0x06DD, 0x070F, 0x0890, 0x0891, 0x08E2}
    | {0xFFF9, 0xFFFA, 0xFFFB, 0x110BD, 0x110CD}
)


def quote_is_refused(quote: str) -> bool:
    """Whether the real service answers ``invalid-request`` for this quote before searching.

    ``src/query/tools.ts`` refuses a quote that normalises to nothing but gaps (section 6), one
    carrying a section 2 character of ``docs/fidelity-normalization.md`` (C0 controls other than
    tab, line feed and carriage return; DEL and the C1 controls; U+FFFD, U+FFFE, U+FFFF; the
    bidirectional controls; a lone surrogate; from fidelity-norm/3.0.0 the interlinear annotation
    controls and the prepended concatenation marks), and since fidelity-norm/3.0.0 one carrying a
    table's grid marker or a picture's U+FFFC (U+FDD0-U+FDEF, U+FFFC), which the scanner writes and
    a reader never sees.
    """
    for character in quote:
        point = ord(character)
        if point < 0x20 and point not in (0x09, 0x0A, 0x0D):
            return True
        if 0x7F <= point <= 0x9F or point in (0xFFFD, 0xFFFE, 0xFFFF, 0x061C, 0x200E, 0x200F):
            return True
        if 0x202A <= point <= 0x202E or 0x2066 <= point <= 0x2069 or 0xD800 <= point <= 0xDFFF:
            return True
        if point == 0xFFFC or 0xFDD0 <= point <= 0xFDEF:
            return True
        if point in _FORBIDDEN_3_0_0:
            return True
    return _normalises_to_gaps(quote)


def _plain_text(div: str) -> str:
    """A deliberately small XHTML-to-text step, sufficient for the fixture's one-paragraph divs.

    The real normalisation is ``docs/fidelity-normalization.md`` and lives in Zone A and Zone B;
    duplicating it here would be a third implementation of a specification this agent has no
    business re-implementing. The fixture's narrative is a single ``<p>`` of ASCII, so stripping
    tags and collapsing runs of spaces reproduces the normalised text exactly. A fixture that
    outgrew that would fail ``test_fake_service.py``'s round-trip assertion, not slip through.
    """
    out: list[str] = []
    depth = 0
    for character in div:
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        elif depth == 0:
            out.append(character)
    return " ".join("".join(out).split())


def _hashes(submission: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        section["sourceKey"]: {
            "narrativeDivSha256": section["narrativeDivSha256"],
            "normalizedTextSha256": section["normalizedTextSha256"],
        }
        for section in submission["provenance"]["sections"]
    }


def load_sections() -> dict[str, Section]:
    """Build one contract-valid ``SectionContent`` per section of the synthetic submission."""
    submission = _submission()
    hashes = _hashes(submission)
    sections: dict[str, Section] = {}

    def walk(node: Mapping[str, Any], path: str) -> None:
        source_key = str(node["code"]["coding"][0]["code"])
        div = str(node["text"]["div"])
        payload = {
            "document": {
                "bundleId": BUNDLE_ID,
                "versionId": VERSION_ID,
                "lastUpdated": LAST_UPDATED,
            },
            "sourceKey": source_key,
            "path": path,
            "title": str(node["title"]),
            "div": div,
            "text": _plain_text(div),
            "narrativeDivSha256": hashes[source_key]["narrativeDivSha256"],
            "normalizedTextSha256": hashes[source_key]["normalizedTextSha256"],
            "normalizationVersion": NORMALIZATION_VERSION,
            "contentNotice": CONTENT_NOTICE,
        }
        result = validate_tool_output("get_section", payload)
        if not result.available:
            raise AssertionError(f"canned get_section output for {source_key} fails the contract")
        sections[source_key] = Section(source_key=source_key, payload=payload)
        for index, child in enumerate(node.get("section", [])):
            walk(child, f"{path}.section[{index}]")

    for index, top in enumerate(_composition(submission)["section"]):
        walk(top, f"Composition.section[{index}]")
    return sections


LONG_SECTION_KEY: Final = "smpc.4.2.long"
# Where the splitter used before 2026-09-22 cut the long section: at the last space inside each
# 2,000-unit window. The first falls inside "1 000 000", the second just after "≥".
LONG_SECTION_OLD_CUTS: Final = (1996, 3995)


def _filler(length: int) -> str:
    """Synthetic sentences, exactly ``length`` characters, ending in a full stop."""
    sentence = "Synthetic demonstration content, not for clinical use."
    parts: list[str] = []
    used = 0
    while length - used > 2 * len(sentence) + 1:
        parts.append(sentence)
        used += len(sentence) + 1
    tail = length - used - len("See also .")
    parts.append(f"See also {'x' * max(tail, 1)}.")
    text = " ".join(parts)
    assert len(text) == length, "the filler arithmetic is wrong"
    return text


def _long_section_text() -> str:
    """Over 4,000 code points, with a space-grouped number and a spaced comparator placed where
    a cut at the last space of each 2,000-unit window lands inside each of them."""
    dose = "Give up to 1 000 000 IU daily."  # the space after "1 000" is its 16th character
    renal = "Reduce the dose when CrCl ≥ 30 ml/min."  # the space after "≥" is its 27th
    first = LONG_SECTION_OLD_CUTS[0] - 16
    second = LONG_SECTION_OLD_CUTS[1] - 27
    text = (
        _filler(first - 1)
        + " "
        + dose
        + " "
        + _filler(second - first - len(dose) - 2)
        + " "
        + renal
        + " End of the synthetic long section."
    )
    assert text[LONG_SECTION_OLD_CUTS[0] - 5 : LONG_SECTION_OLD_CUTS[0] + 4] == "1 000 000"
    assert text[LONG_SECTION_OLD_CUTS[1] - 1 : LONG_SECTION_OLD_CUTS[1] + 3] == "≥ 30"
    return text


def synthetic_section(source_key: str, text: str, title: str = "Synthetic section") -> Section:
    """A contract-valid section whose normalised text is ``text``, outside the canned
    submission: a test adds it to ``FakeQueryService.sections`` when it needs one."""
    div = f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{html.escape(text, quote=False)}</p></div>'
    payload = {
        "document": {"bundleId": BUNDLE_ID, "versionId": VERSION_ID, "lastUpdated": LAST_UPDATED},
        "sourceKey": source_key,
        "path": "Composition.section[3].section[1].section[9]",
        "title": title,
        "div": div,
        "text": text,
        "narrativeDivSha256": _sha256_hex(div),
        "normalizedTextSha256": _sha256_hex(text),
        "normalizationVersion": NORMALIZATION_VERSION,
        "contentNotice": CONTENT_NOTICE,
    }
    if not validate_tool_output("get_section", payload).available:
        raise AssertionError(f"the synthetic section {source_key} fails the contract")
    return Section(source_key=source_key, payload=payload)


def long_section() -> Section:
    """A section longer than two ``verify_quote`` windows (``_long_section_text``)."""
    return synthetic_section(LONG_SECTION_KEY, _long_section_text(), "Synthetic long section")


def quote_edge_cases() -> Mapping[str, Any]:
    """The query service's own quote-edge answers, as ``test/fixtures/contracts`` holds them.

    ``scripts/contracts/export-quote-edge-cases.ts`` writes them from ``src/query/quote-edge.ts``,
    ``npm run contracts:check`` regenerates them and fails on drift, and
    ``tests/test_quote_edge.py`` holds this fake's ``verify_quote`` to every answer.
    """
    data: Mapping[str, Any] = json.loads(
        (FIXTURES / "quote-edge-cases.json").read_text(encoding="utf-8")
    )
    return data


@final
@dataclass(slots=True)
class FakeQueryService:
    """The running fake. ``seen_*`` are the header values the agent actually put on the wire."""

    url: str
    sections: dict[str, Section]
    seen_authorization: list[str | None] = field(default_factory=list)
    seen_turn_id: list[str | None] = field(default_factory=list)
    # Each verify_quote answer, in call order, as (quote, result). Test-side only: the real
    # service keeps no quote text.
    seen_quotes: list[tuple[str, str]] = field(default_factory=list)
    # The JSON-RPC method of every request, in arrival order.
    seen_methods: list[str] = field(default_factory=list)
    corrupt_section: str | None = None
    break_schema_for: str | None = None
    truncate_find_product: bool = False


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_server(state: FakeQueryService) -> Any:
    mcp = FastMCP(name="fake-ema-flow-query", stateless_http=True, json_response=True)

    @mcp.tool(name="find_product", description="Synthetic product lookup.")
    def find_product(query: str, limit: int = 10) -> dict[str, Any]:
        del query, limit
        return {
            "products": [
                {
                    "document": {
                        "bundleId": BUNDLE_ID,
                        "versionId": VERSION_ID,
                        "lastUpdated": LAST_UPDATED,
                    },
                    "productName": "Synthetic demonstration product",
                    "identifiers": [
                        {
                            "system": "https://khs.dev/fhir/identifier/type2-document",
                            "value": "synthetic-type2-smpc-v1",
                        }
                    ],
                    "language": "en",
                    "sections": sorted(state.sections),
                }
            ],
            # query-tools 2.0.0: required. True means the service stopped searching before it
            # had covered the caller's whole entitlement.
            "truncated": state.truncate_find_product,
        }

    @mcp.tool(name="get_section", description="One QRD section, verbatim, with its hashes.")
    def get_section(bundleId: str, sourceKey: str, versionId: str = VERSION_ID) -> dict[str, Any]:  # noqa: N803
        del bundleId, versionId
        section = state.sections[sourceKey]
        payload = dict(section.payload)
        if state.break_schema_for == sourceKey:
            # A result the contract refuses: the hash is not 64 lower-case hex digits.
            payload["narrativeDivSha256"] = "not-a-sha256"
        return payload

    @mcp.tool(name="get_provenance", description="Who and what put this document in the store.")
    def get_provenance(bundleId: str, versionId: str = VERSION_ID) -> dict[str, Any]:  # noqa: N803
        del bundleId, versionId
        submission = _submission()
        provenance = submission["provenance"]
        approval = submission["approval"]
        return {
            "document": {
                "bundleId": BUNDLE_ID,
                "versionId": VERSION_ID,
                "lastUpdated": LAST_UPDATED,
            },
            "provenanceResourceId": provenance["extraction"]["extractionRunId"],
            "recorded": approval["approvedAt"],
            "sourceDocumentSha256": provenance["sourceDocument"]["sha256"],
            "fidelityReportSha256": _sha256_hex("synthetic-fidelity-report"),
            "approvedContentSha256": approval["approvedContentSha256"],
            "extractor": {
                "name": provenance["extraction"]["parser"]["name"],
                "version": provenance["extraction"]["parser"]["version"],
            },
            "approver": {"id": approval["approverId"], "role": approval["approverRole"]},
        }

    @mcp.tool(name="verify_quote", description="Is this quote what the label says?")
    def verify_quote(
        bundleId: str,  # noqa: N803
        quote: str,
        sourceKey: str | None = None,  # noqa: N803
        versionId: str = VERSION_ID,  # noqa: N803
    ) -> dict[str, Any]:
        del bundleId
        if quote_is_refused(quote):
            # The real service answers with an error result; FastMCP turns this into one.
            state.seen_quotes.append((quote, "invalid-request"))
            raise ValueError("invalid-request")
        # As the service does: a named section is the only one searched (src/query/tools.ts).
        candidates = {
            key: section
            for key, section in state.sections.items()
            if sourceKey is None or key == sourceKey
        }
        result: dict[str, Any] = {
            "document": {
                "bundleId": BUNDLE_ID,
                "versionId": versionId,
                "lastUpdated": LAST_UPDATED,
            },
            "result": "no-match",
            "normalizationVersion": NORMALIZATION_VERSION,
            "quoteSha256": _sha256_hex(quote),
            "sectionsSearched": len(candidates),
        }
        for key, section in candidates.items():
            if key == state.corrupt_section:
                # This section is "changed in the store since composition": nothing matches it.
                continue
            # The service's quote-edge rule, not a substring search: a quote that cuts a word, a
            # space-grouped number or a spaced comparator is no-match here as it is there.
            located = locate_quote(section.text, quote)
            if located is None:
                continue
            result["result"] = "match"
            result["match"] = {
                "sourceKey": key,
                "startOffset": located[0],
                "endOffset": located[1],
                "normalizedTextSha256": section.payload["normalizedTextSha256"],
            }
            break
        state.seen_quotes.append((quote, str(result["result"])))
        return result

    app = mcp.streamable_http_app()

    class _Capture:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                headers = {key.decode(): value.decode() for key, value in scope["headers"]}
                state.seen_authorization.append(headers.get("authorization"))
                state.seen_turn_id.append(headers.get("x-query-turn-id"))
            body: list[bytes] = []

            async def peek() -> Any:
                # The JSON-RPC method of each request, read as it passes: a test counts
                # tools/list round trips by it.
                message = await receive()
                if message.get("type") == "http.request":
                    body.append(message.get("body", b""))
                    if not message.get("more_body"):
                        with suppress(ValueError):
                            parsed = json.loads(b"".join(body) or b"null")
                            for item in parsed if isinstance(parsed, list) else [parsed]:
                                if isinstance(item, dict) and "method" in item:
                                    state.seen_methods.append(str(item["method"]))
                return message

            await self._inner(scope, peek, send)

    return _Capture(app)


@contextmanager
def running_query_service(
    *,
    corrupt_section: str | None = None,
    break_schema_for: str | None = None,
    truncate_find_product: bool = False,
) -> Iterator[FakeQueryService]:
    """Serve the four tools on an ephemeral loopback port for the life of the block."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = int(listener.getsockname()[1])
    state = FakeQueryService(
        url=f"http://127.0.0.1:{port}/mcp",
        sections=load_sections(),
        corrupt_section=corrupt_section,
        break_schema_for=break_schema_for,
        truncate_find_product=truncate_find_product,
    )
    config = uvicorn.Config(_build_server(state), log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            raise AssertionError("the fake query service did not start")
        yield state
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        listener.close()
