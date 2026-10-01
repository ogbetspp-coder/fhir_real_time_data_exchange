"""Every ePI ingested is held to Chrome where it is installed; a disagreement is never served.

The store asks a verifier (``label_docx.browser.verify_epi`` in the service) about every ePI it
reads and keeps the verdict beside the result, once per Chrome version: the same Chrome must give
the same verdict byte for byte. A result Chrome disagrees with is kept, as everything is, but
the service will not serve it. Most tests use a stand-in verifier, so the store's rules are held
without a browser; the last ones ask Chrome itself, where it is installed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from label_docx import browser, epi_output
from label_docx.service import Service
from label_docx.store import Store, StoreError

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
EPI = (CORPUS / "ema-epi" / "jentadueto-smpc-en.json").read_bytes()
DOCX = (CORPUS / "ema-qrd" / "qrd-product-information-template-version-104_en.docx").read_bytes()
CHROME = browser.find_chrome()


def _agreeing(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    return {"application": "Stand-in 1.0", "agrees": 1, "differs": [], "refused": 0, "sections": 1}


def _differing(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    differs = [{"section": 3, "where": "line 1: marks differ at character 2"}]
    return {
        "application": "Stand-in 1.0",
        "agrees": 0,
        "differs": differs,
        "refused": 0,
        "sections": 1,
    }


def _call(
    service: Service, method: str, path: str, body: bytes = b""
) -> tuple[str, dict[str, str], bytes]:
    import io

    answer: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        answer["status"], answer["headers"] = status, dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    payload = b"".join(service(environ, start_response))
    return answer["status"], answer["headers"], payload


def test_an_epi_is_verified_and_the_verdict_kept_once_per_browser(tmp_path: Path) -> None:
    store = Store(tmp_path, browser=_agreeing)
    first = store.ingest(EPI)
    again = Store(tmp_path, browser=_agreeing).ingest(EPI)
    assert first.verification is not None
    assert first.verification == again.verification
    assert json.loads(first.verification)["application"] == "Stand-in 1.0"
    assert store.verifications(first.document) == [json.loads(first.verification)]
    assert store.disagreement(first.document) is None


def test_a_word_document_and_a_refused_epi_are_not_sent_to_the_browser(tmp_path: Path) -> None:
    store = Store(tmp_path, browser=_differing)
    assert store.ingest(DOCX).verification is None
    assert store.ingest(b'{"resourceType": "Bundle"}').verification is None


def test_the_same_browser_must_give_the_same_verdict(tmp_path: Path) -> None:
    Store(tmp_path, browser=_agreeing).ingest(EPI)
    with pytest.raises(StoreError):
        Store(tmp_path, browser=lambda d, r: {**_agreeing(d, r), "agrees": 2}).ingest(EPI)


def test_a_result_the_browser_disagrees_with_is_never_served(tmp_path: Path) -> None:
    service = Service(Store(tmp_path, browser=_differing))
    status, headers, body = _call(service, "POST", "/v1/documents", EPI)
    assert status == "201 Created"
    assert headers["Verification"] == "differs"
    document = json.loads(body)["document"]
    status, _, body = _call(service, "GET", f"/v1/documents/{document}")
    assert status == "409 Conflict"
    assert json.loads(body)["verification"]["differs"][0]["section"] == 3
    status, _, body = _call(service, "GET", f"/v1/documents/{document}/verification")
    assert status == "200 OK"
    assert json.loads(body)["verifications"][0]["differs"]


def test_without_a_browser_a_result_is_served_and_says_it_was_not_verified(tmp_path: Path) -> None:
    service = Service(Store(tmp_path))
    _, headers, body = _call(service, "POST", "/v1/documents", EPI)
    assert headers["Verification"] == "not-verified"
    document = json.loads(body)["document"]
    assert _call(service, "GET", f"/v1/documents/{document}")[0] == "200 OK"
    status, _, body = _call(service, "GET", f"/v1/documents/{document}/verification")
    assert (status, json.loads(body)) == ("200 OK", {"verifications": []})
    agreeing = Service(Store(tmp_path, browser=_agreeing))
    assert _call(agreeing, "POST", "/v1/documents", EPI)[1]["Verification"] == "agrees"
    assert _call(agreeing, "POST", "/v1/documents", DOCX)[1]["Verification"] == "not-verified"


@pytest.mark.skipif(CHROME is None, reason="Chrome is not installed")
def test_chrome_agrees_with_an_epi_result_and_sees_a_changed_mark() -> None:
    assert CHROME is not None
    result = json.loads(epi_output.read(EPI)[0])
    verdict = browser.verify_epi(EPI, result, CHROME)
    assert verdict["differs"] == []
    assert verdict["agrees"] == verdict["sections"] - verdict["refused"] > 0
    changed = copy.deepcopy(result)
    # One character made italic in a paragraph Chrome shows without any italic.
    paragraph = next(
        p
        for s in changed["sections"]
        for p in [*s["paragraphs"], *(q for c in s["sections"] for q in c["paragraphs"])]
        if p["text"] and not any(m["kind"] == "italic" for m in p["marks"])
    )
    paragraph["marks"].append({"start": 0, "end": 1, "kind": "italic"})
    assert browser.verify_epi(EPI, changed, CHROME)["differs"]


@pytest.mark.skipif(CHROME is None, reason="Chrome is not installed")
def test_chrome_draws_the_list_markers_the_reader_reads_and_sees_a_changed_one() -> None:
    assert CHROME is not None
    result = json.loads(epi_output.read(EPI)[0])

    def items(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            p
            for s in sections
            for p in [*s["paragraphs"], *items(s["sections"])]
            if p["numbering"] is not None and p["numbering"]["text"] is not None
        ]

    assert len(items(result["sections"])) > 10
    assert browser.verify_epi(EPI, result, CHROME)["differs"] == []
    changed = copy.deepcopy(result)
    first = items(changed["sections"])[0]
    first["numbering"]["text"] = "\u25e6" if first["numbering"]["text"] != "\u25e6" else "\u2022"
    assert any(
        "list marker" in d["where"] for d in browser.verify_epi(EPI, changed, CHROME)["differs"]
    )


def test_strict_service_serves_no_read_its_application_has_not_checked(tmp_path: Path) -> None:
    unchecked = Service(Store(tmp_path / "a"), require=frozenset({"epi"}))
    _, headers, body = _call(unchecked, "POST", "/v1/documents", EPI)
    assert headers["Verification"] == "not-verified"
    document = json.loads(body)["document"]
    status, _, body = _call(unchecked, "GET", f"/v1/documents/{document}")
    assert status == "409 Conflict"
    assert "not yet checked by Chrome" in json.loads(body)["error"]
    # A .docx is not held to it, and a refusal is served: nothing was read.
    _, _, body = _call(unchecked, "POST", "/v1/documents", DOCX)
    assert _call(unchecked, "GET", f"/v1/documents/{json.loads(body)['document']}")[0] == "200 OK"
    _, _, body = _call(unchecked, "POST", "/v1/documents", b'{"resourceType": "Bundle"}')
    assert _call(unchecked, "GET", f"/v1/documents/{json.loads(body)['document']}")[0] == "200 OK"
    checked = Service(Store(tmp_path / "b", browser=_agreeing), require=frozenset({"epi"}))
    _, _, body = _call(checked, "POST", "/v1/documents", EPI)
    assert _call(checked, "GET", f"/v1/documents/{json.loads(body)['document']}")[0] == "200 OK"
