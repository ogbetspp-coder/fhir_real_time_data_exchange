"""Every .docx ingested can be held to Microsoft Word, as every ePI is held to Chrome.

With ``--word on`` (macOS with Word) the store asks Word about every .docx it reads, keeps the
verdict beside the result once per Word version, and the service does not serve a result Word
shows otherwise. Word takes about a minute a document, so it is off unless asked for. These
tests hold the store's rules with a stand-in for Word; ``LABEL_TEST_WORD=1`` also asks Word
itself about one document.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from label_docx import word
from label_docx.browser import BrowserError
from label_docx.service import Service
from label_docx.store import Store
from label_docx.word import WordError

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
DOCX = (CORPUS / "numbering-cases" / "bullets.docx").read_bytes()
EPI = (CORPUS / "ema-epi" / "jentadueto-smpc-en.json").read_bytes()


def _word_agrees(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    return {"application": "Microsoft Word 0.0 (stand-in)", "differs": [], "verdict": "agrees"}


def _word_differs(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    where = "differs: list item 2: Word draws 'b.' where the reader draws 'a.'"
    return {
        "application": "Microsoft Word 0.0 (stand-in)",
        "differs": [{"where": where}],
        "verdict": where,
    }


def _word_fails(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    raise WordError("Word did not answer")


def _browser_fails(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    raise BrowserError("Chrome did not write the page")


def _call(
    service: Service, method: str, path: str, body: bytes = b""
) -> tuple[str, dict[str, str], bytes]:
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


def test_a_docx_is_held_to_word_and_an_epi_is_not(tmp_path: Path) -> None:
    store = Store(tmp_path, word=_word_agrees)
    ingested = store.ingest(DOCX)
    assert ingested.verification is not None
    assert json.loads(ingested.verification)["verdict"] == "agrees"
    assert store.verifications(ingested.document) == [json.loads(ingested.verification)]
    assert store.ingest(EPI).verification is None


def test_a_docx_word_shows_otherwise_is_never_served(tmp_path: Path) -> None:
    service = Service(Store(tmp_path, word=_word_differs))
    status, headers, body = _call(service, "POST", "/v1/documents", DOCX)
    assert (status, headers["Verification"]) == ("201 Created", "differs")
    document = json.loads(body)["document"]
    assert _call(service, "GET", f"/v1/documents/{document}")[0] == "409 Conflict"


def test_an_application_that_cannot_be_asked_verifies_nothing_and_keeps_nothing(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path, browser=_browser_fails, word=_word_fails)
    for data in (DOCX, EPI):
        ingested = store.ingest(data)
        assert ingested.verification is None
        assert store.verifications(ingested.document) == []


@pytest.mark.skipif(
    os.environ.get("LABEL_TEST_WORD") != "1" or word.find_word() is None,
    reason="asks Microsoft Word itself (macOS, about a minute): set LABEL_TEST_WORD=1",
)
def test_word_itself_agrees_with_a_reading() -> None:  # pragma: no cover - needs Word
    verdict = word.verify_docx(DOCX, {})
    assert verdict["verdict"] == "agrees"
    assert verdict["differs"] == []
    assert verdict["application"].startswith("Microsoft Word")
