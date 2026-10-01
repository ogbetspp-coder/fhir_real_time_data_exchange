"""The ingestion service: an HTTP API over the store, as a WSGI application.

    GET  /                             a page to read a document by hand (a demonstration)
    POST /v1/documents                 a .docx or an ePI Bundle (JSON); answers the receipt (201
                                       first, 200 after)
    GET  /v1/documents/<id>            the reader's result (canonical JSON), read or refused
    GET  /v1/documents/<id>/source     the document's bytes as ingested
    GET  /v1/health                    the reader, format and runtime the service answers with

``<id>`` is the SHA-256 of the document's bytes. The same bytes always get the same receipt and
the same result, byte for byte, whoever sends them and however often: the store keeps both once
(``label_docx.store``). A refusal is a result like any other, with its code and detail. Bodies
are canonical JSON, and every result names the reader and format versions that made it.

The service runs only on the interpreter the results are pinned to: the reader's notion of
whitespace comes from its Unicode database (``check_environment``).
"""

from __future__ import annotations

import importlib.resources
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from label_docx import documents
from label_docx.output import FORMAT_VERSION, canonical
from label_docx.reader import READER_VERSION
from label_docx.store import Store, StoreError

# The largest document accepted, in bytes; the reader caps each part at 20 MiB.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
PYTHON = (3, 14)
UNICODE = "16.0.0"
JSON = "application/json"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FHIR_JSON = "application/fhir+json"

type StartResponse = Callable[[str, list[tuple[str, str]]], object]
type Environ = dict[str, object]

_DOCUMENT = re.compile(r"/v1/documents/([0-9a-f]{64})(/source)?")


def check_environment() -> None:
    """Refuse to run on an interpreter whose results would not be the pinned ones."""
    if sys.version_info[:2] != PYTHON or unicodedata.unidata_version != UNICODE:
        raise SystemExit(
            f"label-docx needs Python {PYTHON[0]}.{PYTHON[1]} with Unicode {UNICODE}; this is "
            f"Python {sys.version_info[0]}.{sys.version_info[1]} with Unicode "
            f"{unicodedata.unidata_version}"
        )


def health() -> bytes:
    """What the service answers with: versions, and the runtime they are pinned to."""
    return canonical(
        {
            "format": FORMAT_VERSION,
            "python": f"{PYTHON[0]}.{PYTHON[1]}",
            "reader": READER_VERSION,
            "readers": {
                k.name: {"format": k.format, "reader": k.reader}
                for k in (documents.DOCX, documents.EPI)
            },
            "unicode": UNICODE,
        }
    )


def _error(status: str, message: str) -> tuple[str, str, bytes]:
    return status, JSON, canonical({"error": message})


class Service:
    """The WSGI application over a store."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def __call__(self, environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        """Answer one request."""
        try:
            status, content_type, body, headers = self._route(environ)
        except StoreError as failure:
            status, content_type, body = _error("500 Internal Server Error", str(failure))
            headers = []
        start_response(
            status, [("Content-Type", content_type), ("Content-Length", str(len(body))), *headers]
        )
        return [body]

    def _route(self, environ: Environ) -> tuple[str, str, bytes, list[tuple[str, str]]]:
        method, path = str(environ.get("REQUEST_METHOD", "")), str(environ.get("PATH_INFO", ""))
        if path == "/":
            if method != "GET":
                return (*_error("405 Method Not Allowed", "use GET"), [("Allow", "GET")])
            page = importlib.resources.files("label_docx").joinpath("demo.html").read_bytes()
            return "200 OK", "text/html; charset=utf-8", page, []
        if path == "/v1/health":
            if method != "GET":
                return (*_error("405 Method Not Allowed", "use GET"), [("Allow", "GET")])
            return "200 OK", JSON, health(), []
        if path == "/v1/documents":
            if method != "POST":
                return (*_error("405 Method Not Allowed", "use POST"), [("Allow", "POST")])
            return self._ingest(environ)
        match = _DOCUMENT.fullmatch(path)
        if match is None:
            return (*_error("404 Not Found", f"no such path: {path}"), [])
        if method != "GET":
            return (*_error("405 Method Not Allowed", "use GET"), [("Allow", "GET")])
        document, source = match.group(1), match.group(2) is not None
        body = self.store.source(document) if source else self.store.result(document)
        if body is None:
            return (*_error("404 Not Found", f"no document {document}"), [])
        if not source:
            return "200 OK", JSON, body, []
        return "200 OK", FHIR_JSON if documents.kind(body) is documents.EPI else DOCX, body, []

    def _ingest(self, environ: Environ) -> tuple[str, str, bytes, list[tuple[str, str]]]:
        length = str(environ.get("CONTENT_LENGTH") or "")
        if not length.isdigit():
            return (*_error("411 Length Required", "send the document with a length"), [])
        if int(length) > MAX_DOCUMENT_BYTES:
            return (*_error("413 Content Too Large", f"over {MAX_DOCUMENT_BYTES} bytes"), [])
        if int(length) == 0:
            return (*_error("400 Bad Request", "no document"), [])
        stream = environ["wsgi.input"]
        data = stream.read(int(length))  # type: ignore[attr-defined]
        if len(data) != int(length):
            return (*_error("400 Bad Request", "the document was cut short"), [])
        ingested = self.store.ingest(data)
        status = "201 Created" if ingested.created else "200 OK"
        return status, JSON, ingested.receipt, [("Location", f"/v1/documents/{ingested.document}")]


class _ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # One line per request on standard error, as wsgiref writes it; never a document's text.
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def serve(root: Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Serve the store at ``root`` until interrupted."""
    check_environment()
    with make_server(
        host, port, Service(Store(root)), server_class=_ThreadingServer, handler_class=_QuietHandler
    ) as server:
        sys.stderr.write(f"label-docx {READER_VERSION} serving {root} on http://{host}:{port}\n")
        server.serve_forever()
