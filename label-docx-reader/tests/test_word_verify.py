"""Every .docx ingested can be held to Microsoft Word, as every ePI is held to Chrome.

With ``--word on`` (macOS with Word) the store asks Word about every .docx it reads, keeps the
verdict beside the result once per Word version, and the service does not serve a result Word
shows otherwise. Word takes a minute or more a document, so it is off unless asked for. These
tests hold the store's rules with a stand-in for Word; ``LABEL_TEST_WORD=1`` also asks Word
itself about one document.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from label_docx import word
from label_docx.browser import BrowserError
from label_docx.service import Service
from label_docx.store import Checker, Store
from label_docx.word import WordError
from test_service import _call

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


def test_a_docx_is_held_to_word_and_an_epi_is_not(tmp_path: Path) -> None:
    store = Store(tmp_path, word=Checker(lambda: "Microsoft Word 0.0 (stand-in)", _word_agrees))
    ingested = store.ingest(DOCX)
    assert ingested.verification is not None
    assert json.loads(ingested.verification)["verdict"] == "agrees"
    assert store.verifications(ingested.document) == [json.loads(ingested.verification)]
    # Compared as a list: `is None` would print the ingestion, with its result.
    assert [store.ingest(EPI).verification] == [None]


def test_a_docx_word_shows_otherwise_is_never_served(tmp_path: Path) -> None:
    service = Service(
        Store(tmp_path, word=Checker(lambda: "Microsoft Word 0.0 (stand-in)", _word_differs))
    )
    status, headers, body = _call(service, "POST", "/v1/documents", DOCX)
    assert (status, headers["Verification"]) == ("201 Created", "differs")
    document = json.loads(body)["document"]
    assert _call(service, "GET", f"/v1/documents/{document}")[0] == "409 Conflict"


def test_an_application_that_cannot_be_asked_verifies_nothing_and_keeps_nothing(
    tmp_path: Path,
) -> None:
    store = Store(
        tmp_path,
        browser=Checker(lambda: "Stand-in 1.0", _browser_fails),
        word=Checker(lambda: "Microsoft Word 0.0 (stand-in)", _word_fails),
    )
    for data in (DOCX, EPI):
        ingested = store.ingest(data)
        assert (ingested.verification, store.verifications(ingested.document)) == (None, [])


@pytest.mark.skipif(
    os.environ.get("LABEL_TEST_WORD") != "1" or word.find_word() is None,
    reason="asks Microsoft Word itself (macOS, about a minute): set LABEL_TEST_WORD=1",
)
def test_word_itself_agrees_with_a_reading() -> None:  # pragma: no cover - needs Word
    verdict = word.verify_docx(DOCX, {})
    assert verdict["verdict"] == "agrees"
    assert verdict["differs"] == []
    assert verdict["application"].startswith("Microsoft Word")
    # A document with tracked changes: each view held to the one Word makes, and Word's to Word.
    tracked = (CORPUS / "tracked-cases" / "format-symbol-font.docx").read_bytes()
    assert word.verify_docx(tracked, {"tracked": {}})["verdict"] == "agrees"


def test_word_is_asked_about_a_file_named_for_its_document_and_only_agreement_agrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def ask(path: Path) -> dict[str, Any]:
        asked.append(path.name)
        return {}

    monkeypatch.setattr(word, "ask", ask)
    monkeypatch.setattr(word, "word_version", lambda: "Microsoft Word 0.0 (stand-in)")
    for outcome, differs in (
        ("agrees", []),
        ("reader refuses: stale-field", [{"where": "reader refuses: stale-field"}]),
        ("differs at paragraph 2", [{"where": "differs at paragraph 2"}]),
    ):
        monkeypatch.setattr(word, "judge", lambda _path, _answers, o=outcome: o)
        assert word.verify_docx(DOCX, {})["differs"] == differs
    # Two documents at once cannot be found under one name.
    assert set(asked) == {hashlib.sha256(DOCX).hexdigest()[:16] + ".docx"}
