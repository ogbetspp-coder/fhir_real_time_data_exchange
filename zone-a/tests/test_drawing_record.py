"""The drawing record's fields, ``python -m zone_a.drawing`` (certified-word-drawing.md, PR 1)."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from label_docx import browser

from zone_a import drawing, recompute
from zone_a.canonical_json import canonical_json, sha256_utf8
from zone_a.certified import Body

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "test" / "fixtures" / "certified-word" / "recompute"
CASES = {case["name"]: case for case in json.loads((FIXTURES / "cases.json").read_text("utf-8"))}


def _request(name: str) -> dict[str, Any]:
    data = (FIXTURES / f"{name}.docx").read_bytes()
    return {"docxSha256": hashlib.sha256(data).hexdigest(), "recompute": CASES[name]["request"]}


def _expected(name: str, chrome: str) -> dict[str, Any]:
    """The fields the label, its request and its committed recompute result give."""
    data = (FIXTURES / f"{name}.docx").read_bytes()
    committed = (FIXTURES / f"{name}.json").read_bytes()
    sections = json.loads(committed)["sections"]
    return {
        "recordVersion": drawing.RECORD_VERSION,
        "request": _request(name),
        "document": {"sha256": hashlib.sha256(data).hexdigest(), "byteLength": len(data)},
        "recompute": {"outputSha256": hashlib.sha256(committed).hexdigest()},
        "drawing": {"version": drawing.DRAWING_VERSION, "chrome": chrome},
        "sections": [
            {"key": s["key"], "narrativeDivSha256": sha256_utf8(s["narrative"])}
            for s in sections
            if s["narrative"]
        ],
    }


def _drawn(monkeypatch: pytest.MonkeyPatch, where: Mapping[str, str] | None = None) -> None:
    """Chrome stands in: every drawn section agrees, but those ``where`` names, which differ."""
    where = where or {}

    def check(_body: Body, built: Mapping[str, Any], _chrome: Path) -> dict[str, Any]:
        drawn = [s["key"] for s in built["sections"] if s["refusal"] is None and s["narrative"]]
        return {
            "checker": drawing.DRAWING_VERSION,
            "application": "Chrome 1",
            "sections": [
                {"key": key, "agrees": key not in where, "where": where.get(key)} for key in drawn
            ],
        }

    monkeypatch.setattr(drawing, "check", check)


def _main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str], raw: bytes
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw)))
    status = drawing.main(args)
    out, err = capsys.readouterr()
    return status, out, err


@pytest.mark.parametrize("name", ["smpc", "smpc-tracked", "smpc-assigned", "pl"])
def test_the_fields_are_the_labels_its_requests_and_its_recomputes(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _drawn(monkeypatch)
    data = (FIXTURES / f"{name}.docx").read_bytes()
    assert drawing.record(data, _request(name)) == _expected(name, "Chrome 1")


def test_a_request_that_does_not_fit_the_bytes_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _drawn(monkeypatch)
    data = (FIXTURES / "smpc.docx").read_bytes()
    request = _request("smpc")
    other = (FIXTURES / "pl.docx").read_bytes()
    refusals: dict[str, list[tuple[bytes, object]]] = {
        "document": [
            (other, request),
            (data, request | {"docxSha256": request["docxSha256"].upper()}),
        ],
        "request": [
            (data, {"recompute": request["recompute"]}),
            (data, request | {"submission": "x"}),
            (data, [request]),
        ],
        "versions": [(data, request | {"recompute": request["recompute"] | {"versions": {}}})],
    }
    for code, cases in refusals.items():
        for label, asked in cases:
            with pytest.raises(recompute.RefusedError) as refused:
                drawing.record(label, asked)
            assert refused.value.code == code
    # The label the recompute refuses (two tabs in 4.2) draws nothing.
    refused_label = (FIXTURES / "smpc-refused.docx").read_bytes()
    with pytest.raises(recompute.RefusedError) as refused:
        drawing.record(refused_label, _request("smpc-refused"))
    assert refused.value.code == "section"


def test_the_command_writes_the_fields_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(monkeypatch)
    label = str(FIXTURES / "smpc.docx")
    raw = canonical_json(_request("smpc")).encode("utf-8")
    assert _main(monkeypatch, capsys, [label], raw) == (
        0,
        canonical_json(_expected("smpc", "Chrome 1")) + "\n",
        "",
    )
    # The request's canonical JSON exactly: no other spacing, no repeated key, UTF-8, a value.
    for wrong in (
        json.dumps(_request("smpc"), indent=1).encode("utf-8"),
        raw[:-1] + b',"docxSha256":"' + _request("smpc")["docxSha256"].encode() + b'"}',
        raw + b"\n",
        b"\xff",
        b"{",
        b"NaN",
        # Nested too deep for the JSON parser, and for canonical JSON though not the parser.
        b"[" * 100_000 + b"]" * 100_000,
        b"[" * 2_000 + b"]" * 2_000,
    ):
        assert _main(monkeypatch, capsys, [label], wrong) == (1, "", "refused: request\n")
    assert _main(monkeypatch, capsys, [str(tmp_path / "none.docx")], raw) == (
        1,
        "",
        "refused: label\n",
    )
    assert _main(monkeypatch, capsys, [str(FIXTURES / "pl.docx")], raw) == (
        1,
        "",
        "refused: document\n",
    )
    assert _main(monkeypatch, capsys, [], raw)[0] == 2
    # The registry and mapping files are read where ZONE_A_ROOT names them, as the recompute's are.
    monkeypatch.setenv("ZONE_A_ROOT", str(tmp_path))
    assert _main(monkeypatch, capsys, [label], raw) == (1, "", "error: FileNotFoundError\n")
    monkeypatch.setenv("ZONE_A_ROOT", str(ROOT))
    assert _main(monkeypatch, capsys, [label], raw)[0] == 0


def test_a_section_drawn_otherwise_or_a_failing_chrome_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    label = str(FIXTURES / "smpc.docx")
    raw = canonical_json(_request("smpc")).encode("utf-8")
    _drawn(monkeypatch, {"smpc.4.1": "line 2: text differs at character 3"})
    assert _main(monkeypatch, capsys, [label], raw) == (
        1,
        "",
        "drawn-otherwise: smpc.4.1: line 2: text differs at character 3\n",
    )

    def fails(error: Exception) -> None:
        def check(*_: object) -> dict[str, Any]:
            raise error

        monkeypatch.setattr(drawing, "check", check)

    fails(browser.BrowserError("Chrome did not write the page"))
    assert _main(monkeypatch, capsys, [label], raw) == (1, "", "browser-failed\n")
    # Any other failure names its type only: its message might quote the label.
    fails(ValueError("a sentence of the label"))
    assert _main(monkeypatch, capsys, [label], raw) == (1, "", "error: ValueError\n")


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome is not installed")
def test_chrome_itself_draws_the_committed_labels_alike_twice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chrome = browser.chrome_version(browser.CHROME)
    for name in ("smpc", "pl"):
        raw = canonical_json(_request(name)).encode("utf-8")
        runs = [_main(monkeypatch, capsys, [str(FIXTURES / f"{name}.docx")], raw) for _ in range(2)]
        assert runs[0] == runs[1] == (0, canonical_json(_expected(name, chrome)) + "\n", "")


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome is not installed")
def test_the_parse_check_finds_where_html_and_xml_make_other_trees() -> None:
    spec = importlib.util.spec_from_file_location(
        "word_drawing_check", ROOT / "zone-a" / "scripts" / "word_drawing_check.py"
    )
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    root = '<div xmlns="http://www.w3.org/1999/xhtml">'
    cases = {
        "<p>a</p></div>": None,
        # The tbody the HTML parser inserts is no difference.
        "<table><tr><td>a</td></tr></table></div>": None,
        "<p>a<div>b</div></p></div>": "the root",
        "<pre>\na</pre></div>": "/0/0",
        "<p>a<![CDATA[b]]></p></div>": "/0/1",
        "<p>a&nbsp;b</p></div>": "not well-formed XML",
        "<p>a</p></div>\n": "the root",
    }
    divs = [root + rest for rest in cases]
    assert script.parsed_alike(divs) == list(cases.values())
    assert script.parsed_alike([]) == []
