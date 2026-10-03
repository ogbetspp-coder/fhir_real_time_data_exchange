"""The ingestion service: an HTTP API over the store, as a WSGI application.

    GET  /                             a page to read a document by hand (a demonstration)
    POST /v1/documents                 a .docx or an ePI Bundle (JSON); answers the receipt (201
                                       first, 200 after)
    GET  /v1/documents/<id>            the reader's result (canonical JSON), read or refused
    GET  /v1/documents/<id>/source     the document's bytes as ingested
    GET  /v1/documents/<id>/verification   Chrome's or Word's verdicts on the result
    GET  /v1/health                    the reader, format and runtime the service answers with

``<id>`` is the SHA-256 of the document's bytes. The same bytes always get the same receipt and
the same result, byte for byte, whoever sends them and however often: the store keeps both once
(``label_docx.store``). A refusal is a result like any other, with its code and detail. Bodies
are canonical JSON, and every result names the reader and format versions that made it.

The service runs only on the interpreter the results are pinned to: the reader's notion of
whitespace comes from its Unicode database (``check_environment``). It answers only requests
for the host it serves (bound to a loopback address, only loopback names) and refuses a POST from
another web origin, so a web page cannot fill the store. A client that stops sending is dropped
after ``_QuietHandler.timeout`` seconds; documents are read at most ``READING`` at a time.
"""

from __future__ import annotations

import importlib.resources
import json
import re
import sys
import threading
import traceback
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from label_docx import browser, documents, word
from label_docx.output import FORMAT_VERSION, Json, canonical
from label_docx.reader import READER_VERSION
from label_docx.store import Checker, Store, StoreError

# The largest document accepted, in bytes; the reader caps each part at 20 MiB.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
PYTHON = (3, 14)
UNICODE = "16.0.0"
JSON = "application/json"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FHIR_JSON = "application/fhir+json"

type StartResponse = Callable[[str, list[tuple[str, str]]], object]
type Environ = dict[str, object]

_DOCUMENT = re.compile(r"/v1/documents/([0-9a-f]{64})(/source|/verification)?")
# Reading is bound to one core (the reader is pure Python), so more at once only costs memory.
READING = threading.BoundedSemaphore(2)
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


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

    def __init__(
        self,
        store: Store,
        require: frozenset[str] = frozenset(),
        hosts: frozenset[str] | None = None,
    ) -> None:
        self.store = store
        # The kinds of document ("epi", "docx") whose reads are served only once their
        # application (Chrome, Word) has checked them and agrees.
        self.require = require
        # The Host headers answered (None: any), against a page reaching a local service
        # through a name of its own.
        self.hosts = hosts

    def __call__(self, environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        """Answer one request."""
        try:
            status, content_type, body, headers = self._guarded(environ)
        except Exception as failure:  # noqa: BLE001 - every answer is JSON, never a traceback
            # The log keeps the detail (a store path, a traceback); the client gets none of it.
            sys.stderr.write("".join(traceback.format_exception(failure)))
            message = (
                "the store holds other bytes than it wrote"
                if isinstance(failure, StoreError)
                else "internal error"
            )
            status, content_type, body = _error("500 Internal Server Error", message)
            headers = []
        start_response(
            status, [("Content-Type", content_type), ("Content-Length", str(len(body))), *headers]
        )
        return [body]

    def _guarded(self, environ: Environ) -> tuple[str, str, bytes, list[tuple[str, str]]]:
        host = str(environ.get("HTTP_HOST", ""))
        if self.hosts is not None and host not in self.hosts:
            return (*_error("421 Misdirected Request", "not a host this service answers"), [])
        origin = environ.get("HTTP_ORIGIN")
        if (
            environ.get("REQUEST_METHOD") == "POST"
            and origin is not None
            and origin != (f"http://{host}")
        ):
            return (*_error("403 Forbidden", "a document is posted from this origin only"), [])
        return self._route(environ)

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
        document, part = match.group(1), match.group(2)
        if part == "/verification":
            if self.store.kind_of(document) is None:
                return (*_error("404 Not Found", f"no document {document}"), [])
            verdicts = self.store.verifications(document)
            return "200 OK", JSON, canonical({"verifications": verdicts}), []
        if part == "/source":
            body = self.store.source(document)
            if body is None:
                return (*_error("404 Not Found", f"no document {document}"), [])
            return "200 OK", FHIR_JSON if documents.kind(body) is documents.EPI else DOCX, body, []
        body = self.store.result(document)
        if body is None:
            source = self.store.source(document)
            if source is None:
                return (*_error("404 Not Found", f"no document {document}"), [])
            # Kept, but not read by this reader version (or stopped before its receipt): read it.
            with READING:
                self.store.ingest(source)
            body = self.store.result(document)
            if body is None:  # pragma: no cover - ingest always leaves a result and receipt
                raise StoreError(f"no result for {document} after reading it")
        disagreement = self.store.disagreement(document)
        if disagreement is not None:
            # The application shows the document otherwise than the result reads: not served.
            message: Json = {
                "error": "the application shows it otherwise",
                "verification": disagreement,
            }
            return "409 Conflict", JSON, canonical(message), []
        reading = self.store.kind_of(document)
        strict = reading is not None and reading.name in self.require
        if strict and "refusal" not in json.loads(body):
            agreed = [
                v
                for v in self.store.verifications(document)
                if isinstance(v, dict) and v.get("differs") == []
            ]
            if not agreed:
                # Strict: a read no application has checked is not served.
                application = "Chrome" if reading is documents.EPI else "Microsoft Word"
                message = {
                    "error": f"not yet checked by {application}, which this service requires"
                }
                return "409 Conflict", JSON, canonical(message), []
        return "200 OK", JSON, body, []

    def _ingest(self, environ: Environ) -> tuple[str, str, bytes, list[tuple[str, str]]]:
        length = str(environ.get("CONTENT_LENGTH") or "")
        # ASCII digits only ("²" is a digit to str.isdigit), and no more than a size can need.
        if not (length.isascii() and length.isdigit() and len(length) <= 12):
            return (*_error("411 Length Required", "send the document with a length"), [])
        if int(length) > MAX_DOCUMENT_BYTES:
            return (*_error("413 Content Too Large", f"over {MAX_DOCUMENT_BYTES} bytes"), [])
        if int(length) == 0:
            return (*_error("400 Bad Request", "no document"), [])
        stream = environ["wsgi.input"]
        try:
            data = stream.read(int(length))  # type: ignore[attr-defined]
        except OSError:  # the client stopped sending (the handler's timeout)
            return (*_error("408 Request Timeout", "the document was not sent in time"), [])
        if len(data) != int(length):
            return (*_error("400 Bad Request", "the document was cut short"), [])
        with READING:
            ingested = self.store.ingest(data)
        status = "201 Created" if ingested.created else "200 OK"
        if ingested.verification is None:
            verified = "not-verified"
        else:
            agreed = json.loads(ingested.verification).get("differs") == []
            verified = "agrees" if agreed else "differs"
        headers = [("Location", f"/v1/documents/{ingested.document}"), ("Verification", verified)]
        return status, JSON, ingested.receipt, headers


class _ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    # A client that sends nothing for this long is dropped, not waited on with a thread.
    timeout = 30
    # Answers "Expect: 100-continue" (curl sends it for a large document) at once.
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # One line per request on standard error, as wsgiref writes it; never a document's text.
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def browser_verifier(choice: str) -> Checker | None:
    """The browser to hold ePIs to, by the ``--browser`` choice.

    Chrome where installed (``auto``), always (``on`` and ``require``), or none (``off``).
    """
    if choice == "off":
        return None
    chrome = browser.find_chrome()
    if chrome is None:
        if choice in ("on", "require"):
            raise SystemExit("label-docx: --browser on, but Chrome is not installed")
        return None
    return Checker(
        lambda: browser.chrome_version(chrome),
        lambda data, result: browser.verify_epi(data, result, chrome),
    )


def word_verifier(choice: str) -> Checker | None:
    """Word to hold .docx reads to, by the ``--word`` choice.

    Where installed (``auto``), always (``on``, ``require``), never (``off``).
    """
    if choice == "off":
        return None
    if word.find_word() is None:
        if choice in ("on", "require"):
            raise SystemExit("label-docx: --word on, but Microsoft Word is not installed")
        return None
    return Checker(word.word_version, word.verify_docx)


def serve(
    root: Path,
    host: str,
    port: int,
    verifier: Checker | None,
    word_check: Checker | None,
    require: frozenset[str],
) -> None:
    """Serve the store at ``root`` until interrupted."""
    check_environment()
    hosts = (
        frozenset(f"{name}:{port}" for name in ("127.0.0.1", "localhost", "[::1]"))
        if host in _LOOPBACK
        else None
    )
    service = Service(Store(root, browser=verifier, word=word_check), require, hosts)
    with make_server(
        host, port, service, server_class=_ThreadingServer, handler_class=_QuietHandler
    ) as server:
        held = ", ".join(
            [
                "every ePI held to Chrome" if verifier else "ePIs not held to a browser",
                "every .docx held to Word" if word_check else ".docx not held to Word",
            ]
        )
        sys.stderr.write(
            f"label-docx {READER_VERSION} serving {root} on http://{host}:{port} ({held})\n"
        )
        server.serve_forever()
