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
from test_reader import W, docx, p, r

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


def test_a_paragraph_mark_joins_past_a_table_and_a_section_end() -> None:
    # Into the first cell's first paragraph, inside a nested table too (Word's answer).
    nested = "<w:tbl><w:tr><w:tc><w:tbl><w:tr><w:tc>" + p(t("in")) + "</w:tc></w:tr></w:tbl>"
    body = p(t("a"), mark("del")) + nested + p(t("out")) + "</w:tc></w:tr></w:tbl>" + p(t("z"))
    accepted, original, _ = tracked(docx(body))
    assert [(x.text, x.table) for x in read_document(accepted).body] == [
        ("ain", (0, 0, 0)),
        ("out", (0, 0, 0)),
        ("z", None),
    ]
    assert [x.text for x in read_document(original).body] == ["a", "in", "out", "z"]
    # A section's last paragraph joins the next section's first: the section ends no more.
    section = p(t("a"), f'<w:sectPr><w:pgSz w:w="1"/></w:sectPr>{mark("del")}') + p(t("b"))
    assert views(docx(section)) == {
        "accepted": [("ab", None)],
        "original": [("a", None), ("b", None)],
    }


def test_a_row_inserted_or_deleted_is_in_one_view_only() -> None:
    def row(text: str, marker: str = "") -> str:
        props = f'<w:trPr><w:{marker} w:id="3" {WHO}/></w:trPr>' if marker else ""
        return f"<w:tr>{props}<w:tc>{p(t(text))}</w:tc></w:tr>"

    data = docx("<w:tbl>" + row("kept") + row("new", "ins") + row("gone", "del") + "</w:tbl>")
    accepted, original, changes = tracked(data)
    assert [(x.text, x.table) for x in read_document(accepted).body] == [
        ("kept", (0, 0, 0)),
        ("new", (0, 1, 0)),
    ]
    assert [(x.text, x.table) for x in read_document(original).body] == [
        ("kept", (0, 0, 0)),
        ("gone", (0, 1, 0)),
    ]
    assert [c.kind for c in changes] == ["insert-row", "delete-row"]
    # The check drops the row whole too: a view that kept it is caught.
    with pytest.raises(CertificationError):
        certify_tracked(data, {"accepted": original})


def test_table_and_section_formatting_is_former_in_the_original() -> None:
    def changed(tag: str, now: str, before: str, own: str = "") -> str:
        former = f'<w:{tag}Change w:id="5" {WHO}><w:{tag}>{before}</w:{tag}></w:{tag}Change>'
        return f"<w:{tag}>{own}{now}{former}</w:{tag}>"

    body = (
        "<w:tbl>"
        + changed("tblPr", '<w:jc w:val="center"/>', '<w:jc w:val="left"/>')
        + '<w:tblGrid><w:gridCol w:w="2"/><w:tblGridChange w:id="6"><w:tblGrid>'
        + '<w:gridCol w:w="1"/></w:tblGrid></w:tblGridChange></w:tblGrid><w:tr>'
        + changed("trPr", "<w:cantSplit/>", "")
        + "<w:tc>"
        + changed("tcPr", '<w:shd w:val="clear" w:fill="D9D9D9"/>', "")
        + p(t("c"))
        + "</w:tc></w:tr></w:tbl>"
        + p(t("d"))
        + changed("sectPr", '<w:pgSz w:w="2"/>', '<w:pgSz w:w="1"/>')
    )
    accepted, original, changes = tracked(docx(body))
    assert [c.kind for c in changes] == [
        "format-table", "format-table", "format-row", "format-cell", "format-section",
    ]  # fmt: skip
    for view, width in ((accepted, "2"), (original, "1")):
        with zipfile.ZipFile(io.BytesIO(view)) as package:
            xml = package.read("word/document.xml").decode()
        assert "Change" not in xml
        assert f'pgSz ns0:w="{width}"' in xml
        assert f'gridCol ns0:w="{width}"' in xml
    assert views(docx(body)) == {
        "accepted": [("c", None), ("d", None)],
        "original": [("c", None), ("d", None)],
    }


def test_what_the_reader_cannot_undo_exactly_is_refused() -> None:
    headed = with_parts(
        docx(
            p(t("a"), f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>{mark('del')}") + p(t("b"))
        ),
        {"header1.xml": header(p(t("h")))},
        [("h1", "header", "header1.xml")],
    )
    cases = [
        # A paragraph mark at the end of a cell (Word then dissolves the table), of the body, or
        # of a section with headers of its own; a table whose first cell holds no paragraph.
        docx("<w:tbl><w:tr><w:tc>" + p(t("a"), mark("del")) + "</w:tc></w:tr></w:tbl>" + p(t("b"))),
        docx(p(t("a"), mark("del"))),
        headed,
        docx(p(t("a"), mark("del")) + "<w:tbl><w:tr><w:tc><w:tcPr/></w:tc></w:tr></w:tbl>"),
        # A revision the views do not undo: an inserted table cell.
        docx(
            '<w:tbl><w:tr><w:tc><w:tcPr><w:cellIns w:id="1"/></w:tcPr>'
            "<w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        ),
    ]
    for data in cases:
        with pytest.raises(DocxRefusedError) as caught:
            tracked(data)
        assert caught.value.code == "tracked-change"
        assert result(data)["refusal"]["code"] == "tracked-change"


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
        "checker": "conservation-check/1.9.0",
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
REFUSED = {
    "content-control-emptied",  # Word shows placeholder spaces the document does not hold
    "field-separator-deleted",  # Word drops the whole field result
    "list-definition-changed",  # Word's Reject All rewrites the styles instead
    "mark-deleted-before-table-first-row-deleted",  # Word cannot accept it
    "mark-deleted-last-in-body",  # Word cannot accept it
    "mark-deleted-last-in-cell",  # Word dissolves the table
}


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
        "checker": "conservation-check/1.9.0",
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


def _row(text: str, marker: str = "") -> str:
    properties = f'<w:trPr><w:{marker} w:id="3" {WHO}/></w:trPr>' if marker else ""
    return f"<w:tr>{properties}<w:tc>{p(t(text))}</w:tc></w:tr>"


def test_a_table_whose_every_row_a_view_drops_goes_with_them() -> None:
    body = (
        f"<w:tbl>{_row('gone', 'del')}</w:tbl>{p(t('mid'))}"
        f"<w:tbl>{_row('kept')}</w:tbl>{p(t('end'))}"
    )
    source = docx(body)
    accepted, original, _ = tracked(source)
    assert [(x.text, x.table) for x in read_document(accepted).body] == [
        ("mid", None),
        ("kept", (0, 0, 0)),
        ("end", None),
    ]
    assert [x.table for x in read_document(original).body] == [(0, 0, 0), None, (1, 0, 0), None]
    certify_tracked(source, {"accepted": accepted, "original": original})
    # An empty table left behind moves every later table: the check refuses it.
    left = _rewrite(
        accepted, "word/document.xml", lambda x: x.replace("<ns0:body>", "<ns0:body><ns0:tbl />")
    )
    with pytest.raises(CertificationError):
        certify_tracked(source, {"accepted": left})


def test_a_join_past_a_table_goes_into_its_first_paragraph_in_document_order() -> None:
    # The first cell wrapped in a content control is still the first cell.
    wrapped = (
        "<w:tbl><w:tr><w:sdt><w:sdtContent><w:tc>"
        + p(t("c1"))
        + "</w:tc></w:sdtContent></w:sdt><w:tc>"
        + p(t("c2"))
        + "</w:tc></w:tr></w:tbl>"
    )
    source = docx(p(t("a"), mark("del")) + wrapped + p(t("z")))
    accepted, original, _ = tracked(source)
    assert [(x.text, x.table) for x in read_document(accepted).body] == [
        ("ac1", (0, 0, 0)),
        ("c2", (0, 0, 1)),
        ("z", None),
    ]
    certify_tracked(source, {"accepted": accepted, "original": original})


def test_a_style_definition_changed_is_former_in_the_original_and_a_list_is_refused() -> None:
    former = f'<w:rPrChange w:id="1" {WHO}><w:rPr><w:b/></w:rPr></w:rPrChange>'
    styles = f'<w:style w:type="character" w:styleId="U"><w:rPr><w:i/>{former}</w:rPr></w:style>'
    styled = docx(p(r("<w:t>m</w:t>", '<w:rStyle w:val="U"/>')), styles=styles)
    accepted, original, changes = tracked(styled)
    assert [m.kind for m in read_document(accepted).body[0].marks] == ["italic"]
    assert [m.kind for m in read_document(original).body[0].marks] == ["bold"]
    assert [(c.part, c.kind) for c in changes] == [("word/styles.xml", "format")]
    listed = docx(
        p(t("item"), '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'),
        numbering='<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
        f'<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:rPr><w:b/>{former}</w:rPr>'
        '</w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
    )
    assert result(listed)["refusal"] == {
        "code": "tracked-change",
        "detail": "a change to a definition in word/numbering.xml",
    }


def _part_of(data: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return package.read(name).decode()


def test_a_note_goes_with_the_reference_a_view_drops() -> None:
    for kind in ("footnote", "endnote"):
        reference = f'<w:r><w:{kind}Reference w:id="1"/></w:r>'
        note = f'<w:{kind} w:id="1"><w:p><w:r><w:{kind}Ref/></w:r>{t(" a note")}</w:p></w:{kind}>'
        for marker, dropping in (("ins", "original"), ("del", "accepted")):
            changed = f'<w:{marker} w:id="2" {WHO}>{reference}</w:{marker}>'
            body = p(t("Body") + changed + t(" text."))
            source = docx(body, footnotes=note) if kind == "footnote" else docx(body, endnotes=note)
            accepted, original, _ = tracked(source)
            views = {"accepted": accepted, "original": original}
            keeping = "accepted" if dropping == "original" else "original"
            assert getattr(read_document(views[dropping]), f"{kind}s") == ()
            assert [n.id for n in getattr(read_document(views[keeping]), f"{kind}s")] == [1]
            certify_tracked(source, views)
            # The dropping view with its note put back, or the keeping one without it, is caught.
            notes = f"word/{kind}s.xml"
            stored = _part_of(source, notes)
            back = _rewrite(views[dropping], notes, lambda _x, s=stored: s)
            with pytest.raises(CertificationError):
                certify_tracked(source, {dropping: back})
            empty = f'<w:{kind}s xmlns:w="{W}"/>'
            without = _rewrite(views[keeping], notes, lambda _x, e=empty: e)
            with pytest.raises(CertificationError):
                certify_tracked(source, {keeping: without})


def test_a_join_into_a_row_the_view_drops_is_refused() -> None:
    # Word's Accept All cannot remove such a mark: it stays, so the reader refuses.
    body = (
        p(t("before"), mark("del"))
        + f"<w:tbl>{_row('first', 'del')}{_row('second')}</w:tbl>"
        + p(t("after"))
    )
    with pytest.raises(DocxRefusedError, match="a row the view drops"):
        tracked(docx(body))
    # The check refuses such a view on its own, whatever the view holds.
    with pytest.raises(CertificationError, match="meets a row the view drops"):
        certify_tracked(docx(body), {"accepted": docx(p(t("x")))})


def test_the_check_holds_nested_tables_to_their_outermost_cell() -> None:
    inner = f"<w:tbl>{_row('in1')}{_row('in2', 'del')}</w:tbl>"
    body = f"<w:tbl><w:tr><w:tc>{inner}{p(t('out'))}</w:tc></w:tr>{_row('second')}</w:tbl>"
    source = docx(body + p(t("z")))
    accepted, original, _ = tracked(source)
    assert [(x.text, x.table) for x in read_document(accepted).body] == [
        ("in1", (0, 0, 0)),
        ("out", (0, 0, 0)),
        ("second", (0, 1, 0)),
        ("z", None),
    ]
    certify_tracked(source, {"accepted": accepted, "original": original})
    # The dropped inner row put back, or the inner rows counted as the outer table's, is caught.
    back = _rewrite(
        accepted,
        "word/document.xml",
        lambda x: x.replace(
            "</ns0:tbl><ns0:p>", "<ns0:tr><ns0:tc><ns0:p /></ns0:tc></ns0:tr></ns0:tbl><ns0:p>", 1
        ),
    )
    with pytest.raises(CertificationError):
        certify_tracked(source, {"accepted": back})


def test_a_malformed_change_or_too_deep_a_part_is_refused_never_an_error() -> None:
    cases = {
        # A change without the former properties it records, or holding more than properties.
        "rPrChange without its properties": p(
            r("<w:t>x</w:t>", f'<w:b/><w:rPrChange w:id="4" {WHO}/>')
        ),
        "pPrChange without its properties": p(
            t("x"),
            f'<w:jc w:val="left"/><w:pPrChange w:id="5" {WHO}><w:pPr><w:sectPr/></w:pPr>'
            "</w:pPrChange>",
        ),
        # A move recorded on a row (no schema has one) is read or refused, never an error.
        "": "<w:tbl><w:tr><w:trPr>"
        f'<w:moveFrom w:id="3" {WHO}/><w:trPrChange w:id="4" {WHO}><w:trPr/></w:trPrChange>'
        f"</w:trPr><w:tc>{p(t('x'))}</w:tc></w:tr></w:tbl>{p(t('z'))}",
    }
    for detail, body in cases.items():
        value = result(docx(body))
        if detail:
            assert value["refusal"] == {"code": "tracked-change", "detail": detail}
        else:
            assert "tracked" in value
    deep = '<w:customXml w:element="x">' * 300 + r("<w:t>a</w:t>") + "</w:customXml>" * 300
    assert result(docx(p(deep + ins(t("b")))))["refusal"]["code"] == "invalid-package"


def test_a_content_control_a_view_empties_is_refused() -> None:
    # Word then shows the control's placeholder, which is not in the document.
    control = "<w:sdt><w:sdtContent>{}</w:sdtContent></w:sdt>" + p(t("z"))
    assert result(docx(control.format(p(ins(t("all new"))))))["refusal"] == {
        "code": "tracked-change",
        "detail": "a content control left empty",
    }
    assert "tracked" in result(docx(control.format(p(t("kept ") + ins(t("new"))))))


def test_every_change_is_listed_and_only_a_formatting_copy_once() -> None:
    # Two insertions with one id are two changes; a run split in two holds one change twice.
    _, _, changes = tracked(docx(p(ins(t("a"), key=0) + t("b") + ins(t("c"), key=0))))
    assert [c.kind for c in changes] == ["insert", "insert"]
