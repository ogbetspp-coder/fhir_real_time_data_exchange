"""The canonical JSON result and the command line."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from label_docx.cli import main
from label_docx.output import FORMAT_VERSION, Json, canonical, read
from label_docx.reader import READER_VERSION

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "corpus"
    / "ema-qrd"
    / "qrd-product-information-template-version-104_en.docx"
)


def test_a_read_is_canonical_and_names_its_source_and_versions() -> None:
    data = TEMPLATE.read_bytes()
    result, ok = read(data)
    assert ok
    value = json.loads(result)
    assert result == canonical(value)
    assert sorted(value) == ["endnotes", "footnotes", "format", "paragraphs", "reader", "source"]
    assert (value["format"], value["reader"]) == (FORMAT_VERSION, READER_VERSION)
    assert value["source"] == {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    first = value["paragraphs"][0]
    assert sorted(first) == ["markHidden", "marks", "notes", "numbering", "style", "table", "text"]
    listed = next(x for x in value["paragraphs"] if x["numbering"] and x["numbering"]["numId"])
    assert listed["numbering"] == {"level": 0, "numId": 21, "suffix": "tab", "text": "\u2022"}


def test_a_refusal_names_its_code_and_carries_no_paragraphs() -> None:
    result, ok = read(b"not a zip")
    assert not ok
    value = json.loads(result)
    assert result == canonical(value)
    assert "paragraphs" not in value
    assert value["refusal"]["code"] == "invalid-package"


def test_the_canonical_form_is_rfc_8785_for_the_values_the_reader_produces() -> None:
    # RFC 8785 3.2.2.2: '"' and '\' escaped; U+0008, U+0009, U+000A, U+000C and U+000D as \b \t
    # \n \f \r; other controls as lowercase \u00xx; every other character as itself, in UTF-8.
    value: Json = {
        "b": '\x00\x08\t\n\x0b\x0c\r\x1f"\\/\x7f\u2028\U0001f600\u00e9',
        "a": [1, True, None],
    }
    assert canonical(value) == (
        '{"a":[1,true,null],"b":"\\u0000\\b\\t\\n\\u000b\\f\\r\\u001f\\"\\\\/'
        '\x7f\u2028\U0001f600\u00e9"}\n'
    ).encode("utf-8")


def test_the_command_line_writes_the_same_bytes_and_exits_by_outcome(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    out = tmp_path / "out.json"
    assert main([str(TEMPLATE), "--output", str(out)]) == 0
    assert out.read_bytes() == read(TEMPLATE.read_bytes())[0]
    assert main([str(TEMPLATE)]) == 0
    assert capsysbinary.readouterr().out == out.read_bytes()
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    assert main([str(broken)]) == 2
    assert json.loads(capsysbinary.readouterr().out)["refusal"]["code"] == "invalid-package"
    assert main([str(tmp_path / "missing.docx")]) == 1
    assert b"cannot read" in capsysbinary.readouterr().err


def test_footnotes_are_written_with_their_marks_and_paragraphs() -> None:
    cover = (
        Path(__file__).resolve().parents[1]
        / "corpus"
        / "ema-templates"
        / "qrd-appendix-iii-quality-review-documents-templates-human-medicinal-products"
        "-cover-page_en.docx"
    )
    value = json.loads(read(cover.read_bytes())[0])
    marked = [n for p in value["paragraphs"] for n in p["notes"]]
    assert [(n["kind"], n["mark"]) for n in marked] == [("footnote", "1")]
    (footnote,) = value["footnotes"]
    assert (footnote["id"], footnote["mark"]) == (marked[0]["id"], "1")
    echo = footnote["paragraphs"][0]["notes"][0]
    assert (echo["offset"], echo["mark"]) == (0, "1")
    assert value["endnotes"] == []
