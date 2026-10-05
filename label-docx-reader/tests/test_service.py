"""The ingestion service: the same bytes give the same answer, and nothing kept is changed."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from wsgiref.simple_server import WSGIServer, make_server

import pytest

from label_docx import documents, epi_output, output
from label_docx.cli import service_main
from label_docx.output import canonical, read
from label_docx.service import (
    ERRORS,
    Service,
    _hosts,
    _QuietHandler,
    _ThreadingServer,
    check_environment,
    health,
)
from label_docx.store import Checker, Store, StoreError
from test_no_label_leak import sha256_hex

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
TEMPLATE = (
    CORPUS / "ema-qrd" / "qrd-product-information-template-version-104_en.docx"
).read_bytes()
REFUSED = (CORPUS / "numbering-cases" / "fields-stale.docx").read_bytes()
# Read whole: no header, footer or comment refused.
WHOLE = (CORPUS / "numbering-cases" / "bullets.docx").read_bytes()


def test_the_same_bytes_get_the_same_receipt_and_result(tmp_path: Path) -> None:
    kept = Store(tmp_path)
    first = kept.ingest(TEMPLATE)
    again = Store(tmp_path).ingest(TEMPLATE)
    assert (first.created, again.created) == (True, False)
    assert first.receipt == again.receipt
    assert sha256_hex(first.result) == sha256_hex(again.result) == sha256_hex(read(TEMPLATE)[0])
    document = hashlib.sha256(TEMPLATE).hexdigest()
    assert first.document == document
    receipt = json.loads(first.receipt)
    # Its footers' EQ fields are computed by Word, so those footers are refused on their own.
    assert receipt["outcome"] == "read-in-part"
    assert receipt["result"]["sha256"] == hashlib.sha256(first.result).hexdigest()
    assert sha256_hex(kept.source(document)) == sha256_hex(TEMPLATE)
    assert sha256_hex(kept.result(document)) == sha256_hex(first.result)


def test_a_refusal_is_kept_and_answered_like_a_read(tmp_path: Path) -> None:
    kept = Store(tmp_path)
    first, again = kept.ingest(REFUSED), kept.ingest(REFUSED)
    assert first.receipt == again.receipt
    receipt = json.loads(first.receipt)
    assert receipt["outcome"] == "refused"
    assert receipt["refusal"]["code"] == "stale-field"
    assert json.loads(kept.result(first.document) or b"")["refusal"]["code"] == "stale-field"


def test_a_pdf_is_refused_by_name(tmp_path: Path) -> None:
    receipt = json.loads(Store(tmp_path).ingest(b"%PDF-1.7\n%...").receipt)
    assert receipt["refusal"]["code"] == "invalid-package"
    assert "PDF" in receipt["refusal"]["detail"]


def _paths(root: Path, document: str) -> tuple[Path, Path]:
    folder = root / "documents" / document[:2] / document
    return folder / "source", next(folder.rglob("result.json"))


def test_bytes_changed_under_a_kept_name_are_never_served(tmp_path: Path) -> None:
    kept = Store(tmp_path)
    document = kept.ingest(TEMPLATE).document
    source, result = _paths(tmp_path, document)
    result.write_bytes(result.read_bytes().replace(b'"text":"', b'"text":"X', 1))
    with pytest.raises(StoreError):
        kept.result(document)
    with pytest.raises(StoreError):
        kept.ingest(TEMPLATE)
    source.write_bytes(TEMPLATE + b" ")
    with pytest.raises(StoreError):
        kept.source(document)


def test_verify_reads_every_source_again_and_catches_a_consistent_edit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kept = Store(tmp_path)
    document = kept.ingest(TEMPLATE).document
    kept.ingest(REFUSED)
    assert service_main(["verify", "--store", str(tmp_path)]) == 0
    assert "2 of 2 documents verified" in capsys.readouterr().out
    # Edit the result and its receipt together, so each still matches the other.
    _, result = _paths(tmp_path, document)
    edited = result.read_bytes().replace(b'"text":"', b'"text":"X', 1)
    result.write_bytes(edited)
    receipt = result.with_name("receipt.json")
    value = json.loads(receipt.read_bytes())
    value["result"]["sha256"] = hashlib.sha256(edited).hexdigest()
    receipt.write_bytes(canonical(value))
    assert sha256_hex(kept.result(document)) == sha256_hex(edited)
    with pytest.raises(StoreError):
        kept.verify(document)
    assert service_main(["verify", "--store", str(tmp_path)]) == 1
    assert "1 of 2 documents verified" in capsys.readouterr().out


def test_ingesting_one_document_at_once_from_many_threads_keeps_it_once(tmp_path: Path) -> None:
    kept = Store(tmp_path)
    receipts: list[bytes] = []
    created: list[bool] = []

    def ingest() -> None:
        ingested = kept.ingest(TEMPLATE)
        receipts.append(ingested.receipt)
        created.append(ingested.created)

    threads = [threading.Thread(target=ingest) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(receipts)) == 1
    # One of them wrote the receipt, so one says it created the document's answer.
    assert created.count(True) == 1
    assert len(list(tmp_path.rglob("source"))) == len(list(tmp_path.rglob("result.json"))) == 1
    assert len(list(tmp_path.rglob("receipt.json"))) == 1
    assert not list(tmp_path.rglob(".incoming-*"))


def test_a_new_reader_version_adds_its_result_and_leaves_the_old_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = Store(tmp_path)
    old = kept.ingest(TEMPLATE)
    _, old_path = _paths(tmp_path, old.document)
    monkeypatch.setattr(
        documents, "DOCX", dataclasses.replace(documents.DOCX, reader="docx-reader/99.0.0")
    )
    monkeypatch.setattr(output, "READER_VERSION", "docx-reader/99.0.0")
    new = kept.ingest(TEMPLATE)
    assert (new.created, json.loads(new.result)["reader"]) == (True, "docx-reader/99.0.0")
    assert sha256_hex(old_path.read_bytes()) == sha256_hex(old.result)
    assert len(list((tmp_path / "documents").rglob("result.json"))) == 2


def _call(
    service: Service,
    method: str,
    path: str,
    body: bytes = b"",
    length: str | None = None,
    **headers: str,
) -> tuple[str, dict[str, str], bytes]:
    answer: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        answer["status"], answer["headers"] = status, dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)) if length is None else length,
        "wsgi.input": io.BytesIO(body),
        **{f"HTTP_{name.upper()}": value for name, value in headers.items()},
    }
    payload = b"".join(service(environ, start_response))
    return str(answer["status"]), answer["headers"], payload  # type: ignore[return-value]


def test_the_api_answers_the_same_bytes_and_serves_what_it_keeps(tmp_path: Path) -> None:
    service = Service(Store(tmp_path))
    status, headers, receipt = _call(service, "POST", "/v1/documents", TEMPLATE)
    assert status.startswith("201")
    again_status, _, again = _call(service, "POST", "/v1/documents", TEMPLATE)
    assert again_status.startswith("200")
    assert again == receipt
    document = json.loads(receipt)["document"]
    assert headers["Location"] == f"/v1/documents/{document}"
    served = _call(service, "GET", f"/v1/documents/{document}")[2]
    assert sha256_hex(served) == sha256_hex(read(TEMPLATE)[0])
    source = _call(service, "GET", f"/v1/documents/{document}/source")[2]
    assert sha256_hex(source) == sha256_hex(TEMPLATE)
    assert _call(service, "GET", "/v1/health")[2] == health()


@pytest.mark.parametrize(
    ("method", "path", "body", "length", "status"),
    [
        ("GET", "/v1/documents/" + "0" * 64, b"", None, "404"),
        ("GET", "/nowhere", b"", None, "404"),
        ("PUT", "/v1/documents", b"x", None, "405"),
        ("POST", "/v1/health", b"", None, "405"),
        ("DELETE", "/v1/documents/" + "0" * 64, b"", None, "405"),
        ("POST", "/v1/documents", b"", "", "411"),
        ("POST", "/v1/documents", b"", "0", "400"),
        ("POST", "/v1/documents", b"abc", "10", "400"),
        ("POST", "/v1/documents", b"", str(64 * 1024 * 1024 + 1), "413"),
        # A digit to str.isdigit that int() cannot read, and more digits than any size has.
        ("POST", "/v1/documents", b"", "\u00b2", "411"),
        ("POST", "/v1/documents", b"", "9" * 20, "411"),
    ],
)
def test_the_api_refuses_what_it_cannot_answer(
    tmp_path: Path, method: str, path: str, body: bytes, length: str | None, status: str
) -> None:
    answer, _, payload = _call(Service(Store(tmp_path)), method, path, body, length)
    assert answer.startswith(status)
    assert "error" in json.loads(payload)


def test_a_store_that_holds_other_bytes_answers_500(tmp_path: Path) -> None:
    service = Service(Store(tmp_path))
    document = json.loads(_call(service, "POST", "/v1/documents", TEMPLATE)[2])["document"]
    _, result = _paths(tmp_path, document)
    result.write_bytes(b"{}")
    status, headers, body = _call(service, "GET", f"/v1/documents/{document}")
    assert status.startswith("500")
    assert headers["Content-Type"] == "application/json"
    # The store's paths go to the log, never to the client.
    assert str(tmp_path) not in body.decode()
    result.with_name("receipt.json").write_bytes(b"{")
    assert _call(service, "GET", f"/v1/documents/{document}")[0].startswith("500")


def test_any_failure_is_answered_in_json_and_its_detail_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path)

    def fails(_data: bytes) -> None:
        raise RuntimeError("a fault at /private/path")

    monkeypatch.setattr(store, "ingest", fails)
    status, headers, body = _call(Service(store), "POST", "/v1/documents", TEMPLATE)
    assert (status, headers["Content-Type"]) == ("500 Internal Server Error", "application/json")
    assert json.loads(body) == {"code": "internal", "error": "internal error"}
    assert "a fault at /private/path" in capsys.readouterr().err


def test_only_its_own_hosts_and_its_own_origin_are_answered(tmp_path: Path) -> None:
    service = Service(Store(tmp_path), hosts=frozenset({"127.0.0.1:8080"}))
    assert _call(service, "GET", "/v1/health", host="evil.test:8080")[0].startswith("421")
    assert _call(service, "GET", "/v1/health", host="127.0.0.1:8080")[0] == "200 OK"
    # A page elsewhere cannot post a document; the page served here and curl (no Origin) can.
    other = _call(
        service, "POST", "/v1/documents", TEMPLATE, host="127.0.0.1:8080", origin="http://evil.test"
    )
    assert other[0].startswith("403")
    same = _call(
        service,
        "POST",
        "/v1/documents",
        TEMPLATE,
        host="127.0.0.1:8080",
        origin="http://127.0.0.1:8080",
    )
    assert same[0] == "201 Created"
    assert (
        _call(service, "POST", "/v1/documents", REFUSED, host="127.0.0.1:8080")[0] == "201 Created"
    )


def test_a_document_kept_by_an_earlier_reader_is_read_again_when_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = Service(Store(tmp_path))
    document = json.loads(_call(service, "POST", "/v1/documents", TEMPLATE)[2])["document"]
    monkeypatch.setattr(
        documents, "DOCX", dataclasses.replace(documents.DOCX, reader="docx-reader/99.0.0")
    )
    monkeypatch.setattr(output, "READER_VERSION", "docx-reader/99.0.0")
    status, _, body = _call(service, "GET", f"/v1/documents/{document}")
    assert status == "200 OK"
    assert json.loads(body)["reader"] == "docx-reader/99.0.0"
    assert len(list((tmp_path / "documents").rglob("receipt.json"))) == 2


def test_nothing_is_served_while_its_application_is_asked(tmp_path: Path) -> None:
    store = Store(tmp_path)
    seen: list[bytes | None] = []

    def verify(data: bytes, _result: dict[str, object]) -> dict[str, object]:
        seen.append(store.result(hashlib.sha256(data).hexdigest()))
        return {"application": "Word 1.0", "differs": []}

    store.word = Checker(lambda: "Word 1.0", verify)
    ingested = store.ingest(TEMPLATE)
    assert [sha256_hex(result) for result in seen] == [sha256_hex(None)]
    assert sha256_hex(store.result(ingested.document)) == sha256_hex(ingested.result)


def test_an_application_that_fails_in_any_way_verifies_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_data: bytes, _result: dict[str, object]) -> dict[str, object]:
        raise ZeroDivisionError("a stand-in that fails")

    for checker in (Checker(lambda: "Word 1.0", broken), Checker(lambda: str(1 / 0), broken)):
        store = Store(tmp_path / str(id(checker)), word=checker)
        ingested = store.ingest(TEMPLATE)
        assert (ingested.verification, store.verifications(ingested.document)) == (None, [])
    assert "ZeroDivisionError" in capsys.readouterr().err


def test_verify_holds_receipts_and_verdicts_and_checks_every_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kept = Store(tmp_path)
    one, two = kept.ingest(TEMPLATE).document, kept.ingest(REFUSED).document
    _, result = _paths(tmp_path, one)
    receipt = result.with_name("receipt.json")
    forged = json.loads(receipt.read_bytes())
    forged["outcome"] = "refused"
    receipt.write_bytes(canonical(forged))
    with pytest.raises(StoreError):
        kept.verify(one)
    receipt.write_bytes(canonical({**forged, "outcome": "read-in-part"}))
    kept.verify(one)
    planted = result.parent / "word" / "Microsoft-Word-1.0.json"
    planted.parent.mkdir()
    planted.write_bytes(canonical({"application": "Microsoft Word 1.0"}))
    with pytest.raises(StoreError):
        kept.verify(one)
    # A document that cannot be read at all is reported, and the others are still verified.
    _, other = _paths(tmp_path, two)
    other.with_name("receipt.json").write_bytes(b"{")
    assert service_main(["verify", "--store", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "0 of 2 documents verified" in captured.out
    assert one in captured.err
    assert two in captured.err


def test_a_client_that_stops_sending_is_dropped_and_a_large_document_is_continued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_QuietHandler, "timeout", 1)
    with make_server(
        "127.0.0.1", 0, Service(Store(tmp_path)), _ThreadingServer, _QuietHandler
    ) as running:
        threading.Thread(target=running.serve_forever, daemon=True).start()
        address = ("127.0.0.1", running.server_port)
        head = f"POST /v1/documents HTTP/1.1\r\nHost: x\r\nContent-Length: {len(TEMPLATE)}\r\n"
        with socket.create_connection(address, timeout=10) as client:
            client.sendall((head + "Expect: 100-continue\r\n\r\n").encode())
            assert client.recv(64).startswith(b"HTTP/1.1 100 Continue")
            client.sendall(TEMPLATE)
            assert b" 201 Created" in _received(client).split(b"\r\n", 1)[0]
        with socket.create_connection(address, timeout=10) as client:
            client.sendall(
                b"POST /v1/documents HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\nabc"
            )
            assert b" 408 " in _received(client).split(b"\r\n", 1)[0]
        running.shutdown()


def _received(client: socket.socket) -> bytes:
    data = b""
    while chunk := client.recv(1 << 16):
        data += chunk
    return data


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    with make_server("127.0.0.1", 0, Service(Store(tmp_path)), server_class=WSGIServer) as running:
        thread = threading.Thread(target=running.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{running.server_port}"
        running.shutdown()


def test_over_http_the_same_document_gets_the_same_bytes(server: str) -> None:
    def post() -> bytes:
        request = urllib.request.Request(f"{server}/v1/documents", data=TEMPLATE, method="POST")
        with urllib.request.urlopen(request) as answer:
            body: bytes = answer.read()
            return body

    first, again = post(), post()
    assert first == again
    document = json.loads(first)["document"]
    with urllib.request.urlopen(f"{server}/v1/documents/{document}") as answer:
        assert sha256_hex(answer.read()) == sha256_hex(read(TEMPLATE)[0])
    with pytest.raises(urllib.error.HTTPError) as missing:
        urllib.request.urlopen(f"{server}/v1/documents/{'f' * 64}")
    assert missing.value.code == 404


def test_the_command_line_ingests_and_exits_by_outcome(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    good, bad, part = tmp_path / "good.docx", tmp_path / "bad.docx", tmp_path / "part.docx"
    good.write_bytes(WHOLE)
    bad.write_bytes(REFUSED)
    # Its footers are refused on their own: read in part, exit 3.
    part.write_bytes(TEMPLATE)
    store_root = tmp_path / "store"
    assert service_main(["ingest", "--store", str(store_root), str(good)]) == 0
    first = capsysbinary.readouterr().out
    assert service_main(["ingest", "--store", str(store_root), str(good), str(bad)]) == 2
    both = capsysbinary.readouterr().out
    assert both.startswith(first)
    assert json.loads(both[len(first) :])["outcome"] == "refused"
    assert service_main(["ingest", "--store", str(store_root), str(good), str(part)]) == 3
    assert json.loads(capsysbinary.readouterr().out[len(first) :])["outcome"] == "read-in-part"
    # A refusal outranks a read in part, whatever the order.
    assert service_main(["ingest", "--store", str(store_root), str(part), str(bad)]) == 2
    capsysbinary.readouterr()


def test_the_service_runs_only_on_the_pinned_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    check_environment()
    monkeypatch.setattr("label_docx.service.UNICODE", "15.1.0")
    with pytest.raises(SystemExit):
        check_environment()
    assert canonical(json.loads(health())) == health()


def test_a_result_kept_without_its_receipt_is_served_only_after_it_is_read_again(
    tmp_path: Path,
) -> None:
    kept = Store(tmp_path)
    document = kept.ingest(TEMPLATE).document
    _, result = _paths(tmp_path, document)
    result.with_name("receipt.json").unlink()
    assert sha256_hex(kept.result(document)) == sha256_hex(None)
    assert sha256_hex(kept.ingest(TEMPLATE).result) == sha256_hex(result.read_bytes())
    assert sha256_hex(kept.result(document)) == sha256_hex(result.read_bytes())
    # Without its receipt, a result that is not what the reader makes of the source is refused.
    result.with_name("receipt.json").unlink()
    result.write_bytes(result.read_bytes().replace(b'"text":"', b'"text":"X', 1))
    with pytest.raises(StoreError):
        kept.ingest(TEMPLATE)


def test_the_demonstration_page_is_served(tmp_path: Path) -> None:
    status, headers, page = _call(Service(Store(tmp_path)), "GET", "/")
    assert status.startswith("200")
    assert headers["Content-Type"].startswith("text/html")
    assert b"/v1/documents" in page
    assert _call(Service(Store(tmp_path)), "POST", "/", b"x")[0].startswith("405")


EPI = (CORPUS / "ema-epi" / "jentadueto-smpc-en.json").read_bytes()
EPI_IN_PART = (CORPUS / "ema-epi" / "brukinsa-smpc-en.json").read_bytes()


def test_an_epi_is_read_by_the_epi_reader_and_kept_like_a_docx(tmp_path: Path) -> None:
    kept = Store(tmp_path)
    first = kept.ingest(EPI)
    again = Store(tmp_path).ingest(EPI)
    assert (first.created, again.created) == (True, False)
    assert first.receipt == again.receipt
    assert sha256_hex(first.result) == sha256_hex(epi_output.read(EPI)[0])
    receipt = json.loads(first.receipt)
    assert (receipt["reader"], receipt["format"], receipt["outcome"]) == (
        documents.EPI.reader,
        documents.EPI.format,
        "read",
    )
    kept.verify(first.document)
    assert sha256_hex(kept.result(first.document)) == sha256_hex(first.result)


def test_an_epi_with_a_refused_section_is_read_in_part(tmp_path: Path) -> None:
    receipt = json.loads(Store(tmp_path).ingest(EPI_IN_PART).receipt)
    assert receipt["outcome"] == "read-in-part"
    result = json.loads(Store(tmp_path).result(receipt["document"]) or b"")
    assert result["refusedSections"] == 1


def test_the_reader_is_chosen_from_the_bytes_alone() -> None:
    # Chosen before the assertions, which would print the bytes they were chosen from.
    epi_kind, docx_kind = documents.kind(EPI), documents.kind(TEMPLATE)
    assert epi_kind is documents.EPI
    assert documents.kind(b" \r\n\t{}") is documents.EPI
    assert docx_kind is documents.DOCX
    assert documents.kind(b"\xef\xbb\xbf{}") is documents.DOCX
    assert json.loads(documents.kind(b"[]").read(b"[]")[0])["refusal"]["code"] == (
        "invalid-package"
    )


def test_an_epi_over_http_names_its_reader_and_its_source_type(tmp_path: Path) -> None:
    service = Service(Store(tmp_path))
    status, _, body = _call(service, "POST", "/v1/documents", EPI)
    assert status == "201 Created"
    document = json.loads(body)["document"]
    _, headers, source = _call(service, "GET", f"/v1/documents/{document}/source")
    assert (sha256_hex(source), headers["Content-Type"]) == (
        sha256_hex(EPI),
        "application/fhir+json",
    )
    _, _, docx_body = _call(service, "POST", "/v1/documents", TEMPLATE)
    _, headers, _ = _call(
        service, "GET", f"/v1/documents/{json.loads(docx_body)['document']}/source"
    )
    assert headers["Content-Type"].endswith("wordprocessingml.document")
    readers = json.loads(health())["readers"]
    assert readers["epi"] == {"format": documents.EPI.format, "reader": documents.EPI.reader}


def test_the_receipt_carries_the_certificate_of_the_read(tmp_path: Path) -> None:
    for data in (TEMPLATE, EPI):
        ingested = Store(tmp_path).ingest(data)
        receipt, result = json.loads(ingested.receipt), json.loads(ingested.result)
        assert receipt["certificate"] == result["certificate"]
        assert receipt["certificate"]["output"]["characters"] > 0
    refused = json.loads(Store(tmp_path).ingest(REFUSED).receipt)
    assert "certificate" not in refused


def test_a_loopback_address_however_spelled_answers_only_its_own_hosts(tmp_path: Path) -> None:
    for spelling in ("127.1", "127.0.0.1", "localhost"):
        assert _hosts(spelling, 8080) == frozenset({"127.0.0.1:8080", "localhost:8080"})
    service = Service(Store(tmp_path), hosts=_hosts("127.1", 8080))
    assert _call(service, "GET", "/v1/health", host="evil.test:8080")[0].startswith("421")
    assert _call(service, "GET", "/v1/health", host="localhost:8080")[0] == "200 OK"


def test_a_verdict_of_another_verifier_is_not_reused_nor_served_as_agreement(
    tmp_path: Path,
) -> None:
    asked: list[str] = []

    def agrees(_data: bytes, _result: dict[str, object]) -> dict[str, object]:
        asked.append("asked")
        return {"application": "Word 1.0", "differs": []}

    document = Store(tmp_path, word=Checker(lambda: "Word 1.0", agrees, "v1")).ingest(TEMPLATE)
    store = Store(tmp_path, word=Checker(lambda: "Word 1.0", agrees, "v2"))
    service = Service(store, require=frozenset({"docx"}))
    path = f"/v1/documents/{document.document}"
    # Agreement from the verifier as it was is not agreement from the verifier as it is.
    stale = _call(service, "GET", path)
    assert (stale[0], json.loads(stale[2])["code"]) == ("409 Conflict", "not-yet-verified")
    store.ingest(TEMPLATE)
    assert len(asked) == 2
    assert _call(service, "GET", path)[0] == "200 OK"
    kept = [json.loads(p.read_bytes()) for p in tmp_path.rglob("word/*.json")]
    assert sorted(v["verifier"] for v in kept) == ["v1", "v2"]
    store.verify(document.document)


@pytest.mark.parametrize(
    "answers",
    [
        [{"differs": []}, {"differs": [{"where": "paragraph 2"}]}],
        [{"differs": [], "note": "one"}, {"differs": [], "note": "two"}],
    ],
)
def test_two_answers_at_once_are_both_kept_and_a_difference_is_never_served(
    tmp_path: Path, answers: list[dict[str, Any]]
) -> None:
    both_asked = threading.Barrier(2)
    lock = threading.Lock()
    left = list(answers)

    def verify(_data: bytes, _result: dict[str, object]) -> dict[str, object]:
        both_asked.wait(timeout=60)
        with lock:
            return {"application": "Word 1.0", **left.pop(0)}

    store = Store(tmp_path, word=Checker(lambda: "Word 1.0", verify, "v1"))
    outcomes: list[bytes | None] = []
    threads = [
        threading.Thread(target=lambda: outcomes.append(store.ingest(TEMPLATE).verification))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # Each ingestion reports its own answer; both are kept; and the result is not served.
    own = [canonical({"application": "Word 1.0", "verifier": "v1", **a}) for a in answers]
    assert sorted(v or b"" for v in outcomes) == sorted(own)
    document = hashlib.sha256(TEMPLATE).hexdigest()
    assert len(store.verifications(document)) == 2
    assert store.disagreement(document) is not None
    service = Service(store, require=frozenset({"docx"}))
    differs = _call(service, "GET", f"/v1/documents/{document}")
    assert (differs[0], json.loads(differs[2])["code"]) == ("409 Conflict", "shown-otherwise")
    store.verify(document)


def test_every_error_names_a_code_a_program_can_use(tmp_path: Path) -> None:
    service = Service(Store(tmp_path), hosts=frozenset({"127.0.0.1:8080"}))
    here = "127.0.0.1:8080"
    missing = "0" * 64
    answers = {
        "wrong-host": _call(service, "GET", "/v1/health", host="evil.test:8080"),
        "wrong-origin": _call(
            service, "POST", "/v1/documents", TEMPLATE, origin="http://evil.test", host=here
        ),
        "wrong-method": _call(service, "DELETE", "/v1/health", host=here),
        "no-such-path": _call(service, "GET", "/v2/anything", host=here),
        "no-such-document": _call(service, "GET", f"/v1/documents/{missing}", host=here),
        "length-required": _call(service, "POST", "/v1/documents", TEMPLATE, length="x", host=here),
        "too-large": _call(service, "POST", "/v1/documents", b"x", length=str(2**30), host=here),
        "empty": _call(service, "POST", "/v1/documents", b"", host=here),
        "cut-short": _call(service, "POST", "/v1/documents", b"x", length="10", host=here),
    }
    for code, (status, headers, body) in answers.items():
        value = json.loads(body)
        assert (status[0], headers["Content-Type"], value["code"]) == (
            "4",
            "application/json",
            code,
        )
        assert sorted(value) == ["code", "error"]
    assert set(answers) <= ERRORS


def test_a_result_says_its_outcome_as_its_receipt_does(tmp_path: Path) -> None:
    store = Store(tmp_path)
    service = Service(store)
    for data in (TEMPLATE, REFUSED, EPI):
        receipt = json.loads(store.ingest(data).receipt)
        status, headers, _ = _call(service, "GET", f"/v1/documents/{receipt['document']}")
        assert (status, headers["Outcome"]) == ("200 OK", receipt["outcome"])
