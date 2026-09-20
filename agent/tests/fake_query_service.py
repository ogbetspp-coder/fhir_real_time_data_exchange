"""A fake ePI query service: the four tools, canned outputs, real streamable HTTP.

Canned but not invented. Every output is built from ``test/fixtures/contracts/*.json`` — the
repository's own synthetic submission, exported and gate-checked on the Node side — and every
one is validated against ``contracts/generated/query-tools.schema.json`` before the server will
serve it. A fixture that stopped satisfying the contract would fail the tests here rather than
let the agent be tested against a shape the real service cannot produce.

The transport is the real one: ``mcp``'s ``FastMCP`` over streamable HTTP, on an ephemeral
loopback port, with the real ADK toolset on the other end. What is faked is the store, not the
protocol.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, final

import uvicorn
from mcp.server.fastmcp import FastMCP

from verifiable_answer_agent.contract import validate_tool_output

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
FIXTURES: Final = REPOSITORY_ROOT / "test" / "fixtures" / "contracts"

BUNDLE_ID: Final = "synthetic-smpc"
VERSION_ID: Final = "1"
LAST_UPDATED: Final = "2026-09-19T00:00:00Z"
NORMALIZATION_VERSION: Final = "fidelity-norm/1.1.1"
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


@final
@dataclass(slots=True)
class FakeQueryService:
    """The running fake. ``seen_authorization`` is what the agent actually put on the wire."""

    url: str
    sections: dict[str, Section]
    seen_authorization: list[str | None] = field(default_factory=list)
    corrupt_section: str | None = None
    break_schema_for: str | None = None


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
            ]
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
        result: dict[str, Any] = {
            "document": {
                "bundleId": BUNDLE_ID,
                "versionId": versionId,
                "lastUpdated": LAST_UPDATED,
            },
            "result": "no-match",
            "normalizationVersion": NORMALIZATION_VERSION,
            "quoteSha256": _sha256_hex(quote),
            "sectionsSearched": len(state.sections),
        }
        for key, section in state.sections.items():
            if key == state.corrupt_section:
                # This section is "changed in the store since composition": nothing matches it.
                continue
            start = section.text.find(quote)
            if start < 0:
                continue
            result["result"] = "match"
            result["match"] = {
                "sourceKey": key if sourceKey is None else sourceKey,
                "startOffset": start,
                "endOffset": start + len(quote),
                "normalizedTextSha256": section.payload["normalizedTextSha256"],
            }
            break
        return result

    app = mcp.streamable_http_app()

    class _Capture:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                headers = {key.decode(): value.decode() for key, value in scope["headers"]}
                state.seen_authorization.append(headers.get("authorization"))
            await self._inner(scope, receive, send)

    return _Capture(app)


@contextmanager
def running_query_service(
    *, corrupt_section: str | None = None, break_schema_for: str | None = None
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
