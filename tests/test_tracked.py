"""Tracked changes: a document that holds two texts is read as both, and never as one.

Every change accepted is one view, every change rejected (the original) the other. The result
carries both, each certified, with the changes as stored, and no text of its own: the caller
names the view it takes. What the reader cannot undo exactly is refused.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from label_docx import output
from label_docx.certify import CertificationError, certify_tracked
from label_docx.reader import DocxRefusedError, read_document, tracked
from label_docx.store import Store
from label_docx.word import tracked_verdict
from test_headers_comments import header, reference, with_parts
from test_reader import docx, p, r

VIEWS = ("accepted", "original")
WHO = 'w:author="A" w:date="2026-01-01T00:00:00Z"'


def ins(runs: str, key: int = 1) -> str:
    return f'<w:ins w:id="{key}" {WHO}>{runs}</w:ins>'


def dele(text: str, key: int = 2) -> str:
    deleted = r(f'<w:delText xml:space="preserve">{text}</w:delText>')
    return f'<w:del w:id="{key}" {WHO}>{deleted}</w:del>'


def t(text: str) -> str:
    return r(f'<w:t xml:space="preserve">{text}</w:t>')


def mark(kind: str) -> str:
    """Paragraph properties whose mark is inserted or deleted."""
    return f'<w:rPr><w:{kind} w:id="9" {WHO}/></w:rPr>'


def views(data: bytes) -> dict[str, list[tuple[str, str | None]]]:
    accepted, original, _ = tracked(data)
    return {
        name: [(x.text, x.style) for x in read_document(view).body]
        for name, view in (("accepted", accepted), ("original", original))
    }


def result(data: bytes) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(output.read(data)[0])
    return value


def test_inserted_and_deleted_text_is_in_one_view_each() -> None:
    body = p(t("Store below ") + dele("25") + ins(t("30")) + t(" °C"))
    assert views(docx(body)) == {
        "accepted": [("Store below 30 °C", None)],
        "original": [("Store below 25 °C", None)],
    }
    # A deleted field's instruction is an instruction again in the original.
    field = (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r('<w:delInstrText xml:space="preserve"> DOCPROPERTY X </w:delInstrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:delText>v</w:delText>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    assert views(docx(p(t("a") + f'<w:del w:id="3" {WHO}>{field}</w:del>'))) == {
        "accepted": [("a", None)],
        "original": [("av", None)],
    }


def test_a_paragraph_mark_a_view_drops_joins_the_paragraph_to_the_next() -> None:
    inserted = p(t("one"), mark("ins")) + p(ins(t(" two")), '<w:pStyle w:val="X"/>')
    assert views(docx(inserted)) == {
        "accepted": [("one", None), (" two", "X")],
        "original": [("one", "X")],
    }
    deleted = p(t("one"), mark("del")) + p(t("two"), '<w:pStyle w:val="X"/>')
    assert views(docx(deleted)) == {
        "accepted": [("onetwo", "X")],
        "original": [("one", None), ("two", "X")],
    }
    # Two in a row join all three.
    chain = p(t("a"), mark("del")) + p(t("b"), mark("del")) + p(t("c"))
    assert views(docx(chain))["accepted"] == [("abc", None)]


def test_moved_text_stands_where_each_view_has_it() -> None:
    moved = p(
        f'<w:moveFromRangeStart w:id="5" {WHO} w:name="m"/>'
        f'<w:moveFrom w:id="6" {WHO}>{t("x")}</w:moveFrom>'
        '<w:moveFromRangeEnd w:id="5"/>' + t("y")
    ) + p(
        t("z") + f'<w:moveToRangeStart w:id="7" {WHO} w:name="m"/>'
        f'<w:moveTo w:id="8" {WHO}>{t("x")}</w:moveTo><w:moveToRangeEnd w:id="7"/>'
    )
    assert views(docx(moved)) == {
        "accepted": [("y", None), ("zx", None)],
        "original": [("xy", None), ("z", None)],
    }


def test_changed_formatting_is_current_when_accepted_and_former_when_rejected() -> None:
    bold = p(r("<w:t>B</w:t>", f'<w:b/><w:rPrChange w:id="4" {WHO}><w:rPr/></w:rPrChange>'))
    accepted, original, changes = tracked(docx(bold))
    assert [m.kind for m in read_document(accepted).body[0].marks] == ["bold"]
    assert read_document(original).body[0].marks == ()
    assert [c.kind for c in changes] == ["format"]
    styled = p(
        t("s"),
        f'<w:pStyle w:val="New"/><w:pPrChange w:id="5" {WHO}><w:pPr>'
        '<w:pStyle w:val="Old"/></w:pPr></w:pPrChange>',
    )
    assert views(docx(styled)) == {"accepted": [("s", "New")], "original": [("s", "Old")]}


def test_what_the_reader_cannot_undo_exactly_is_refused() -> None:
    table = "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    cases = [
        # A paragraph mark joining a table, or ending a section, or the body's last paragraph.
        p(t("a"), mark("del")) + table,
        p(t("a"), f"<w:sectPr/>{mark('del')}") + p(t("b")),
        p(t("a"), mark("del")),
        # A revision the views do not undo: an inserted table cell.
        '<w:tbl><w:tr><w:tc><w:tcPr><w:cellIns w:id="1"/></w:tcPr>'
        "<w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
    ]
    for body in cases:
        with pytest.raises(DocxRefusedError) as caught:
            tracked(docx(body))
        assert caught.value.code == "tracked-change"
        assert result(docx(body))["refusal"]["code"] == "tracked-change"


def test_the_result_holds_both_views_certified_and_no_text_of_its_own(tmp_path: Path) -> None:
    data = docx(p(t("Store below ") + dele("25") + ins(t("30"))))
    value = result(data)
    assert "paragraphs" not in value
    assert set(value["tracked"]) == {"accepted", "changes", "original"}
    assert [x["text"] for x in value["tracked"]["accepted"]["paragraphs"]] == ["Store below 30"]
    assert [x["text"] for x in value["tracked"]["original"]["paragraphs"]] == ["Store below 25"]
    assert [(c["kind"], c["id"], c["author"]) for c in value["tracked"]["changes"]] == [
        ("delete", "2", "A"),
        ("insert", "1", "A"),
    ]
    assert {(c["part"], c["date"]) for c in value["tracked"]["changes"]} == {
        ("word/document.xml", "2026-01-01T00:00:00Z")
    }
    certificate = value["certificate"]
    assert certificate["views"]["accepted"] == {
        "characters": 14,
        "elements": 0,
        "paragraphsJoined": 0,
    }
    assert certificate["accepted"]["output"] == {"characters": 14}
    assert certificate["original"]["output"] == {"characters": 14}
    assert output.read(data)[0] == output.read(data)[0]
    receipt = json.loads(Store(tmp_path).ingest(data).receipt)
    assert (receipt["outcome"], receipt["trackedChanges"]) == ("read", 2)


def test_a_change_in_a_header_alone_reads_the_document_as_two() -> None:
    data = with_parts(
        docx(p(t("body")) + f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>"),
        {"header1.xml": header(p(ins(t("new")) + t("old")))},
        [("h1", "header", "header1.xml")],
    )
    value = result(data)
    texts = {
        view: [x["text"] for h in value["tracked"][view]["headers"] for x in h["paragraphs"]]
        for view in ("accepted", "original")
    }
    assert texts == {"accepted": ["newold"], "original": ["old"]}
    assert value["tracked"]["changes"][0]["part"] == "word/header1.xml"


def _rewrite(data: bytes, name: str, change: Any) -> bytes:
    """``data`` with its part ``name`` passed through ``change``."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == name:
                content = change(content.decode()).encode()
            target.writestr(info, content)
    return out.getvalue()


def test_the_check_holds_each_view_to_the_source_on_its_own() -> None:
    source = docx(p(t("a"), mark("ins")) + p(t("b") + dele("c") + ins(t("d"))))
    accepted, original, _ = tracked(source)
    assert certify_tracked(source, {"accepted": accepted, "original": original}) == {
        "checker": "conservation-check/1.6.0",
        "accepted": {"characters": 3, "elements": 0, "paragraphsJoined": 0},
        "original": {"characters": 3, "elements": 0, "paragraphsJoined": 1},
    }
    body = "word/document.xml"
    wrong = [
        # The views swapped.
        {"accepted": original, "original": accepted},
        # A character changed.
        {"accepted": _rewrite(accepted, body, lambda x: x.replace(">d<", ">e<"))},
        # The joined paragraph split again where it was joined.
        {
            "original": _rewrite(
                original,
                body,
                lambda x: x.replace("a</ns0:t></ns0:r>", "a</ns0:t></ns0:r></ns0:p><ns0:p>"),
            )
        },
        # A revision left behind.
        {
            "accepted": _rewrite(
                accepted,
                body,
                lambda x: x.replace("</ns0:body>", '<ns0:del ns0:id="1"/></ns0:body>'),
            )
        },
        # A part with no revisions written differently.
        {"accepted": _rewrite(accepted, "word/_rels/document.xml.rels", lambda x: x + " ")},
        # A part missing.
        {"accepted": _rewrite(accepted, body, lambda x: x).replace(b"word/_rels", b"word/_relz")},
    ]
    for tampered in wrong:
        with pytest.raises(CertificationError):
            certify_tracked(source, tampered)


CASES = Path(__file__).resolve().parents[1] / "corpus" / "tracked-cases"
# The cases the reader refuses: what it cannot undo exactly (Word's views are on record).
REFUSED = {"field-separator-deleted", "mark-deleted-before-table", "row-inserted"}


@pytest.mark.parametrize("path", sorted(CASES.glob("*.docx")), ids=lambda p: p.stem)
def test_every_case_reads_as_word_makes_it(path: Path) -> None:
    # Each view the reader makes is the one it makes of Word's own file for that view
    # (scripts/tracked_cases.py --word): text, marks, list labels, notes, headers, footers.
    word = {v: (CASES / "word" / f"{path.stem}.{v}.docx").read_bytes() for v in VIEWS}
    expected = "reader refuses: tracked-change" if path.stem in REFUSED else "agrees"
    assert tracked_verdict(path, word) == expected


def test_a_change_stored_twice_is_listed_once() -> None:
    # A run split in two carries its formatting change in each half: one change.
    changed = f'<w:b/><w:rPrChange w:id="4" {WHO}><w:rPr/></w:rPrChange>'
    _, _, changes = tracked(docx(p(r("<w:t>a</w:t>", changed) + r("<w:t>b</w:t>", changed))))
    assert [c.kind for c in changes] == ["format"]


# Every element that records a revision, named here apart from the check's own list.
REVISIONS = [
    "ins", "del", "moveFrom", "moveTo", "delText", "delInstrText", "moveFromRangeStart",
    "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd", "rPrChange", "pPrChange",
    "sectPrChange", "tblPrChange", "tblPrExChange", "tblGridChange", "trPrChange", "tcPrChange",
    "numberingChange", "cellIns", "cellDel", "cellMerge", "customXmlInsRangeStart",
    "customXmlInsRangeEnd", "customXmlDelRangeStart", "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart",
    "customXmlMoveToRangeEnd",
]  # fmt: skip


@pytest.mark.parametrize("name", REVISIONS)
def test_the_check_finds_every_kind_of_revision_left_in_a_view(name: str) -> None:
    source = docx(p(t("a") + ins(t("b"))))
    accepted, _, _ = tracked(source)
    left = _rewrite(
        accepted, "word/document.xml", lambda x: x.replace("</ns0:p>", f"<ns0:{name}/></ns0:p>")
    )
    with pytest.raises(CertificationError, match="a revision is left"):
        certify_tracked(source, {"accepted": left})


def _with_part(data: bytes, name: str, content: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr(name, content)
    return out.getvalue()


def test_the_check_counts_what_each_view_holds_and_only_the_revised_parts() -> None:
    body = p(
        r("<w:tab/>")
        + t("x")
        + r('<w:instrText xml:space="preserve"> X </w:instrText>')
        + ins(t("y") + r("<w:br/>"))
    )
    # A footnotes part without revisions, and a picture: neither is counted, both must be kept.
    note = f'<w:footnote w:id="1"><w:p>{t("note")}</w:p></w:footnote>'
    source = _with_part(docx(body, footnotes=note), "word/media/image1.png", b"\x89PNG\r\n")
    accepted, original, _ = tracked(source)
    assert certify_tracked(source, {"accepted": accepted, "original": original}) == {
        "checker": "conservation-check/1.6.0",
        "accepted": {"characters": 5, "elements": 2, "paragraphsJoined": 0},
        "original": {"characters": 4, "elements": 1, "paragraphsJoined": 0},
    }
    # A part without revisions must be the source's, byte for byte.
    restyled = _rewrite(accepted, "word/footnotes.xml", lambda x: x.replace("note", "nota"))
    with pytest.raises(CertificationError, match=r"footnotes\.xml is changed"):
        certify_tracked(source, {"accepted": restyled})


def test_the_check_refuses_a_joined_paragraph_with_none_after_it() -> None:
    source = docx(p(t("a"), mark("del")))
    with pytest.raises(CertificationError, match="no paragraph after it"):
        certify_tracked(source, {"accepted": docx(p(t("a")))})


def test_the_check_leaves_run_properties_to_word() -> None:
    # Formatting is held to Word (test_every_case_reads_as_word_makes_it), not counted here: a
    # view whose run lost its properties holds the same content.
    source = docx(p(r("<w:t>a</w:t>", "<w:b/>") + ins(t("b"))))
    accepted, _, _ = tracked(source)
    plain = _rewrite(
        accepted, "word/document.xml", lambda x: x.replace("<ns0:rPr><ns0:b /></ns0:rPr>", "")
    )
    assert plain != accepted
    assert certify_tracked(source, {"accepted": plain})["accepted"] == {
        "characters": 2,
        "elements": 0,
        "paragraphsJoined": 0,
    }
