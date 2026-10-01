"""The ingestion service: the same bytes give the same answer, and nothing kept is changed."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

import pytest

from label_docx import documents, epi_output, output
from label_docx.cli import service_main
from label_docx.output import canonical, read
from label_docx.service import Service, check_environment, health
from label_docx.store import Store, StoreError

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
TEMPLATE = (
    CORPUS / "ema-qrd" / "qrd-product-information-template-version-104_en.docx"
).read_bytes()
REFUSED = (CORPUS / "numbering-cases" / "fields-stale.docx").read_bytes()


def test_the_same_bytes_get_the_same_receipt_and_result(tmp_path: Path) -> None:
    kept = Store(tmp_path)
    first = kept.ingest(TEMPLATE)
    again = Store(tmp_path).ingest(TEMPLATE)
    assert (first.created, again.created) == (True, False)
    assert first.receipt == again.receipt
    assert first.result == again.result == read(TEMPLATE)[0]
    document = hashlib.sha256(TEMPLATE).hexdigest()
    assert first.document == document
    receipt = json.loads(first.receipt)
    assert receipt["outcome"] == "read"
    assert receipt["result"]["sha256"] == hashlib.sha256(first.result).hexdigest()
    assert kept.source(document) == TEMPLATE
    assert kept.result(document) == first.result


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
    assert kept.result(document) == edited
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
    # The source, result and receipt are each created once; a thread that created any says so.
    assert 1 <= created.count(True) <= 3
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
    assert new.created
    assert json.loads(new.result)["reader"] == "docx-reader/99.0.0"
    assert old_path.read_bytes() == old.result
    assert len(list((tmp_path / "documents").rglob("result.json"))) == 2


def _call(
    service: Service, method: str, path: str, body: bytes = b"", length: str | None = None
) -> tuple[str, dict[str, str], bytes]:
    answer: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        answer["status"], answer["headers"] = status, dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)) if length is None else length,
        "wsgi.input": io.BytesIO(body),
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
    assert _call(service, "GET", f"/v1/documents/{document}")[2] == read(TEMPLATE)[0]
    assert _call(service, "GET", f"/v1/documents/{document}/source")[2] == TEMPLATE
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
    assert _call(service, "GET", f"/v1/documents/{document}")[0].startswith("500")


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
        assert answer.read() == read(TEMPLATE)[0]
    with pytest.raises(urllib.error.HTTPError) as missing:
        urllib.request.urlopen(f"{server}/v1/documents/{'f' * 64}")
    assert missing.value.code == 404


def test_the_command_line_ingests_and_exits_by_outcome(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    good, bad = tmp_path / "good.docx", tmp_path / "bad.docx"
    good.write_bytes(TEMPLATE)
    bad.write_bytes(REFUSED)
    store_root = tmp_path / "store"
    assert service_main(["ingest", "--store", str(store_root), str(good)]) == 0
    first = capsysbinary.readouterr().out
    assert service_main(["ingest", "--store", str(store_root), str(good), str(bad)]) == 2
    both = capsysbinary.readouterr().out
    assert both.startswith(first)
    assert json.loads(both[len(first) :])["outcome"] == "refused"


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
    assert kept.result(document) is None
    assert kept.ingest(TEMPLATE).result == result.read_bytes()
    assert kept.result(document) == result.read_bytes()
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
    assert first.result == epi_output.read(EPI)[0]
    receipt = json.loads(first.receipt)
    assert (receipt["reader"], receipt["format"], receipt["outcome"]) == (
        documents.EPI.reader,
        documents.EPI.format,
        "read",
    )
    kept.verify(first.document)
    assert kept.result(first.document) == first.result


def test_an_epi_with_a_refused_section_is_read_in_part(tmp_path: Path) -> None:
    receipt = json.loads(Store(tmp_path).ingest(EPI_IN_PART).receipt)
    assert receipt["outcome"] == "read-in-part"
    result = json.loads(Store(tmp_path).result(receipt["document"]) or b"")
    assert result["refusedSections"] == 1


def test_the_reader_is_chosen_from_the_bytes_alone() -> None:
    assert documents.kind(EPI) is documents.EPI
    assert documents.kind(b" \r\n\t{}") is documents.EPI
    assert documents.kind(TEMPLATE) is documents.DOCX
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
    assert (source, headers["Content-Type"]) == (EPI, "application/fhir+json")
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
