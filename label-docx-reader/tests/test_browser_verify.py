"""Every ePI ingested is held to Chrome where it is installed; a disagreement is never served.

The store asks a verifier (``label_docx.browser.verify_epi`` in the service) about every ePI it
reads and keeps the verdict beside the result, once per Chrome version: a version already kept is
not asked again. A result Chrome disagrees with is kept, as everything is, but
the service will not serve it. Most tests use a stand-in verifier, so the store's rules are held
without a browser; the last ones ask Chrome itself, where it is installed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from label_docx import browser, epi, epi_output
from label_docx.service import Service
from label_docx.store import Checker, Store
from test_service import _call

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
EPI = (CORPUS / "ema-epi" / "jentadueto-smpc-en.json").read_bytes()
DOCX = (CORPUS / "ema-qrd" / "qrd-product-information-template-version-104_en.docx").read_bytes()
CHROME = browser.find_chrome()


def _agreeing(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    return {"application": "Stand-in 1.0", "agrees": 1, "differs": [], "refused": 0, "sections": 1}


def _checker(verify: Any) -> Checker:
    return Checker(lambda: "Stand-in 1.0", verify)


def _differing(_data: bytes, _result: dict[str, Any]) -> dict[str, Any]:
    differs = [{"section": 3, "where": "line 1: marks differ at character 2"}]
    return {
        "application": "Stand-in 1.0",
        "agrees": 0,
        "differs": differs,
        "refused": 0,
        "sections": 1,
    }


def test_an_epi_is_verified_and_the_verdict_kept_once_per_browser(tmp_path: Path) -> None:
    store = Store(tmp_path, browser=_checker(_agreeing))
    first = store.ingest(EPI)
    again = Store(tmp_path, browser=_checker(_agreeing)).ingest(EPI)
    assert first.verification is not None
    assert first.verification == again.verification
    assert json.loads(first.verification)["application"] == "Stand-in 1.0"
    assert store.verifications(first.document) == [json.loads(first.verification)]
    assert store.disagreement(first.document) is None


def test_a_word_document_and_a_refused_epi_are_not_sent_to_the_browser(tmp_path: Path) -> None:
    store = Store(tmp_path, browser=_checker(_differing))
    assert store.ingest(DOCX).verification is None
    assert store.ingest(b'{"resourceType": "Bundle"}').verification is None


def test_a_kept_verdict_is_not_asked_again_and_a_new_version_is(tmp_path: Path) -> None:
    asked: list[str] = []

    def verify(data: bytes, result: dict[str, Any]) -> dict[str, Any]:
        asked.append("asked")
        return _agreeing(data, result)

    first = Store(tmp_path, browser=_checker(verify)).ingest(EPI)
    # The same version is not asked again, even if it would now answer otherwise.
    again = Store(tmp_path, browser=_checker(lambda d, r: {**_agreeing(d, r), "agrees": 2}))
    assert again.ingest(EPI).verification == first.verification
    assert asked == ["asked"]
    newer = Checker(
        lambda: "Stand-in 2.0", lambda d, r: {**_agreeing(d, r), "application": "Stand-in 2.0"}
    )
    assert json.loads(Store(tmp_path, browser=newer).ingest(EPI).verification or b"")[
        "application"
    ] == ("Stand-in 2.0")
    assert len(Store(tmp_path).verifications(first.document)) == 2


def test_a_result_the_browser_disagrees_with_is_never_served(tmp_path: Path) -> None:
    service = Service(Store(tmp_path, browser=_checker(_differing)))
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
    agreeing = Service(Store(tmp_path, browser=_checker(_agreeing)))
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
    checked = Service(
        Store(tmp_path / "b", browser=_checker(_agreeing)), require=frozenset({"epi"})
    )
    _, _, body = _call(checked, "POST", "/v1/documents", EPI)
    assert _call(checked, "GET", f"/v1/documents/{json.loads(body)['document']}")[0] == "200 OK"


@pytest.mark.parametrize("span", [(-1, 1), (1, 1), (2, 1), (0, 3), ("0", 1), (True, 2)])
def test_a_mark_that_is_no_span_of_its_text_is_never_compared(span: tuple[Any, Any]) -> None:
    def lines(start: Any, end: Any) -> list[browser.Line]:
        mark = {"kind": "bold", "start": start, "end": end}
        return browser.result_lines([{"text": "ab", "marks": [mark]}])

    assert lines(0, 2) != lines(1, 2)
    with pytest.raises(ValueError, match="no span"):
        lines(*span)


# --- the oracle's own rules, on facts as the page reports them (sweep 2) -----------------------


def _facts(**changes: Any) -> dict[str, Any]:
    plain: dict[str, Any] = {
        "weight": "400",
        "style": "normal",
        "color": "rgb(0, 0, 0)",
        "size": "16px",
        "decorations": [],
        "background": None,
        "align": [],
        "shift": 0,
        "borders": [],
    }
    return {**plain, **changes}


def test_the_oracle_marks_only_the_lines_that_can_be_seen() -> None:
    # S9: a line is drawn in its own colour, over the background under it.
    white, black, clear = "rgb(255, 255, 255)", "rgb(0, 0, 0)", "rgba(0, 0, 0, 0)"
    assert browser._kinds(_facts(decorations=[["underline", white]])) == frozenset()
    assert browser._kinds(_facts(decorations=[["line-through", clear]])) == frozenset()
    assert browser._kinds(_facts(decorations=[["underline", "rgb(255, 0, 0)"]])) == {"underline"}
    assert browser._kinds(_facts(decorations=[["line-through", black]])) == {"strike"}
    assert browser._kinds(_facts(borders=[["bottom", white, None]])) == frozenset()
    assert browser._kinds(_facts(borders=[["left", black, black]])) == frozenset()
    assert browser._kinds(_facts(borders=[["left", black, None]])) == {"border"}
    assert browser._kinds(_facts(borders=[["bottom", black, None]])) == {"underline"}


def test_the_oracle_tells_any_other_alignment_apart() -> None:
    # S11: text aligned top, middle or bottom is moved; the reader has no mark for it.
    assert browser._kinds(_facts(align=["top"])) == {"vertical-align-top"}
    assert browser._kinds(_facts(align=["super"])) == {"superscript"}


def test_the_oracles_offsets_count_utf16_units() -> None:
    # S51: the page counts a character outside the BMP as two.
    text = "\U0001d6fc a"
    runs = [[0, 3, _facts()], [3, 4, _facts(weight="700")]]
    lines = browser.browser_lines({"text": text, "runs": runs, "error": None})
    assert lines == [(text, (frozenset(), frozenset(), frozenset({"bold"})))]
    with pytest.raises(browser.BrowserError):
        browser.browser_lines({"text": text, "runs": [[0, 1, _facts()]], "error": None})


def test_the_oracle_draws_a_faint_marker_as_none() -> None:
    # S33: the marker is drawn in the item's colour and size, on the background outside it.
    assert browser._drawn_marker("1. ", ["rgb(255, 255, 255)", "16px", None]) == ""
    assert browser._drawn_marker("1. ", ["rgb(0, 0, 0)", "2px", None]) == ""
    assert browser._drawn_marker("1. ", ["rgb(255, 255, 255)", "16px", "rgb(0, 0, 0)"]) == "1. "


def test_the_oracle_reads_the_bundle_as_strictly_as_the_reader() -> None:
    # S34, S35: one reading of each name, and the first entry's Composition, or nothing.
    good = json.loads(EPI)
    repeated = EPI.decode().replace('"title":', '"title": "decoy", "title":', 1).encode()
    moved = copy.deepcopy(good)
    moved["entry"].insert(0, {"resource": {"resourceType": "Patient"}})
    for data in (repeated, json.dumps(moved).encode(), EPI[:-1] + b', "x": NaN}'):
        with pytest.raises(epi.EpiRefusedError):
            browser.verify_epi(data, {}, Path("/nonexistent"))


@pytest.mark.skipif(CHROME is None, reason="Chrome is not installed")
def test_chrome_facts_see_line_colours_block_backgrounds_alignment_and_markers() -> None:
    assert CHROME is not None
    divs = [
        '<div><u style="color:white"><span style="color:black">x</span></u></div>',
        '<div><span style="border-bottom:1px solid white">x</span></div>',
        '<div><span style="background:black;color:white"><p>x</p></span></div>',
        '<div><span style="vertical-align:top">x</span></div>',
    ]
    shown = browser.browser_sections(divs, CHROME)
    assert [browser.browser_lines(s)[0][1][0] for s in shown] == [
        frozenset(),
        frozenset(),
        frozenset({"faint"}),
        frozenset({"vertical-align-top"}),
    ]
    markers = ['<ul><li style="color:white">x</li></ul>', "<ul><li>x</li></ul>"]
    assert browser.browser_markers(markers, CHROME) == [[""], ["\u2022 "]]
