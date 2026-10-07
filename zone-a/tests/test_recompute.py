"""Zone B's recompute of a certified Word source (``zone_a.recompute``)."""

from __future__ import annotations

import io
import json
import zipfile
from html import escape
from pathlib import Path
from typing import Any

import pytest

from zone_a import leaflet, recompute, structure, word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import read_body

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ROOT = Path(__file__).resolve().parents[2]


def _docx(*paragraphs: str) -> bytes:
    """A minimal .docx whose body is these paragraphs, as plain text (a tab as Word's tab)."""
    tab = '</w:t><w:tab/><w:t xml:space="preserve">'
    body = "".join(
        '<w:p><w:r><w:t xml:space="preserve">'
        + escape(text, quote=False).replace("\t", tab)
        + "</w:t></w:r></w:p>"
        for text in paragraphs
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        package.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )
        package.writestr(
            "word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
        )
    return out.getvalue()


def _smpc(text: str = "Text.") -> list[str]:
    """An SmPC with every required section's heading, each followed by a line of text."""
    registry, mapping = recompute._load("smpc", ROOT)
    out = ["ANNEX I"]
    for node in structure._nodes(registry, mapping):
        if node["required"]:
            out += [node["title"], text]
    return out


def _leaflet() -> list[str]:
    """A leaflet with every required section's heading in the template's words, X a name."""
    registry, mapping = recompute._load("pl", ROOT)
    out = ["B. PACKAGE LEAFLET", "Package leaflet: Information for the patient"]
    for node in leaflet._nodes(registry, mapping)[1:]:
        lines, prefix = leaflet.forms(node["head"])
        heading = prefix if prefix is not None else sorted(lines, key=len)[-1]
        out += [heading.replace("X", "Zorvex"), "Text."]
    return out


def _request(document: str = "smpc", **change: Any) -> dict[str, Any]:
    return {
        "document": document,
        "view": None,
        "part": 0,
        "assignments": {},
        "versions": recompute.versions(document),
    } | change


def test_an_smpc_is_recomputed_as_zone_a_builds_it() -> None:
    data = _docx(*_smpc())
    result = recompute.recompute(data, _request())
    body = read_body(data)
    registry, mapping = recompute._load("smpc", ROOT)
    structured = structure.structure(body.paragraphs, registry, mapping)
    assert result["structure"] == structured
    assert result["sections"] == word_epi.sections(body, structured, registry)["sections"]
    assert all(s["refusal"] is None and s["page"] for s in result["sections"] if s["parent"])
    assert result["source"]["bytes"] == len(data)
    assert result["versions"]["builder"] == word_epi.WORD_EPI_VERSION
    assert (result["view"], result["changes"], result["span"]) == (None, 0, [0, len(_smpc())])
    # The same bytes and request, the same result, byte for byte.
    assert canonical_json(recompute.recompute(data, _request())) == canonical_json(result)


def test_a_leaflet_is_recomputed() -> None:
    result = recompute.recompute(_docx(*_leaflet()), _request("pl"))
    assert result["structure"]["name"] == "Zorvex"
    assert result["structure"]["ready"]
    assert {s["key"] for s in result["sections"]} >= {"pl", "pl.6.revised"}
    assert result["versions"]["structurer"] == leaflet.LEAFLET_VERSION


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"versions": {}}, "versions"),
        ({"part": 1}, "request"),
        ({"part": -1}, "request"),
        ({"part": True}, "request"),
        ({"view": "accepted"}, "request"),
        ({"view": "both"}, "request"),
        ({"document": "labelling"}, "request"),
        ({"assignments": {"smpc.4.1": "3"}}, "request"),
        ({"assignments": {"smpc.99": 3}}, "assignments"),
        ({"extra": 1}, "request"),
    ],
)
def test_a_request_that_does_not_fit_is_refused(change: dict[str, Any], code: str) -> None:
    with pytest.raises(recompute.RefusedError) as refused:
        recompute.recompute(_docx(*_smpc()), _request() | change)
    assert refused.value.code == code


def test_what_cannot_be_made_whole_is_refused() -> None:
    with pytest.raises(recompute.RefusedError) as refused:
        recompute.recompute(b"not a zip", _request())
    assert refused.value.code == "invalid-package"
    # A required section missing: the structure is for a person.
    with pytest.raises(recompute.RefusedError) as refused:
        recompute.recompute(_docx(*_smpc()[:-2]), _request())
    assert refused.value.code == "structure"
    # One section the builder refuses (a tab Word draws as a jump) refuses the whole.
    with pytest.raises(recompute.RefusedError) as refused:
        recompute.recompute(_docx(*_smpc("a\tb")), _request())
    assert refused.value.code == "section"
    # A document with no leaflet.
    with pytest.raises(recompute.RefusedError) as refused:
        recompute.recompute(_docx(*_smpc()), _request("pl"))
    assert refused.value.code == "parts"


def test_the_command_writes_the_result_or_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    label = tmp_path / "label.docx"
    label.write_bytes(_docx(*_smpc()))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_request())))
    assert recompute.main([str(label)]) == 0
    written = capsys.readouterr().out
    assert written == canonical_json(recompute.recompute(label.read_bytes(), _request())) + "\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_request(part=3))))
    assert recompute.main([str(label)]) == 1
    assert json.loads(capsys.readouterr().out)["refusal"]["code"] == "request"
    monkeypatch.setattr("sys.stdin", io.StringIO("{"))
    assert recompute.main([str(label)]) == 1
    assert json.loads(capsys.readouterr().out)["refusal"]["code"] == "request"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_request())))
    assert recompute.main([str(tmp_path / "missing.docx")]) == 1
    assert json.loads(capsys.readouterr().out)["refusal"]["detail"] == "the label cannot be read"
    assert recompute.main([]) == 2
