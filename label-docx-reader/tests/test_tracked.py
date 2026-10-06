"""Tracked changes: a document that holds two texts is read as both, and never as one.

Every change accepted is one view, every change rejected (the original) the other. The result
carries both, each certified, with the changes as stored, and no text of its own: the caller
names the view it takes. What the reader cannot undo exactly is refused.
"""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pytest

from label_docx import output
from label_docx.certify import CertificationError, certify_tracked
from label_docx.reader import DocxRefusedError, changed_drawing, read_document, tracked
from label_docx.store import Store
from label_docx.word import tracked_verdict
from test_headers_comments import header, reference, with_parts
from test_reader import VAULT, W, docx, p, r, variables

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


def test_a_deleted_docvariable_is_read_in_the_original_against_its_variable() -> None:
    # As DOCVARIABLE fields stand in EMA product-information files: deleted, their variable kept.
    code = f" DOCVARIABLE {VAULT} \\* MERGEFORMAT "
    field = (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:delInstrText xml:space="preserve">{code}</w:delInstrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r('<w:delText xml:space="preserve"> </w:delText>')
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    body = p(t("4.1") + f'<w:del w:id="3" {WHO}>{field}</w:del>' + t("b"))
    data = with_parts(
        docx(body),
        {"settings.xml": f'<w:settings xmlns:w="{W}">{variables((VAULT, " "))}</w:settings>'},
        [("s1", "settings", "settings.xml")],
    )
    assert views(data) == {"accepted": [("4.1b", None)], "original": [("4.1 b", None)]}
    assert "tracked" in result(data)


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
        ("ain", (1, 0, 0)),
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
        "checker": "conservation-check/1.19.0",
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
        # The source itself, its revisions and all.
        {"accepted": source},
    ]
    for tampered in wrong:
        with pytest.raises(CertificationError):
            certify_tracked(source, tampered)
    # An XML part with no revisions written differently.
    styled = docx(p(t("a") + ins(t("b"))), styles='<w:style w:type="paragraph" w:styleId="N"/>')
    accepted, _, _ = tracked(styled)
    with pytest.raises(CertificationError):
        certify_tracked(
            styled, {"accepted": _rewrite(accepted, "word/styles.xml", lambda x: x + " ")}
        )


BEGIN, SEPARATE, END = (
    r(f'<w:fldChar w:fldCharType="{k}"/>') for k in ("begin", "separate", "end")
)


def code(text: str) -> str:
    return r(f'<w:instrText xml:space="preserve">{text}</w:instrText>')


def _runs(view: bytes) -> list[tuple[str, str]]:
    """Each text and code element of a view's body, in order: (its name, its text)."""
    with zipfile.ZipFile(io.BytesIO(view)) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    names = {f"{{{W}}}t": "t", f"{{{W}}}instrText": "instrText"}
    return [(names[e.tag], e.text or "") for e in root.iter() if e.tag in names]


def test_a_field_is_kept_whole_or_every_mark_of_it_dropped() -> None:
    link = code(' HYPERLINK "https://x" ')
    # In pieces, each its own change: gone whole from the original (field-inserted-apart).
    apart = "".join(ins(x, key) for key, x in enumerate((BEGIN, link, SEPARATE, t("site"), END)))
    assert views(docx(p(t("See ") + apart))) == {
        "accepted": [("See site", None)],
        "original": [("See ", None)],
    }
    # Its marks inserted around code and text already there: Word's original has none of it,
    # code, text and all (field-wrapped-around-text).
    wrapped = ins(BEGIN, 5) + code(" x.eu") + ins(SEPARATE, 6) + t(" x") + ins(END, 7)
    accepted, original, _ = tracked(docx(p(t("See ") + wrapped + t("."))))
    assert _runs(original) == [("t", "See "), ("t", ".")]
    assert _runs(accepted) == [("t", "See "), ("instrText", " x.eu"), ("t", " x"), ("t", ".")]
    # In another field, past its paragraph, or holding a field the view keeps: not on record.
    inner = ins(BEGIN, 5) + code("y") + ins(END, 6)
    kept_field = BEGIN + code(" PAGE ") + SEPARATE + t("1") + END
    for body in (
        p(BEGIN + code(' HYPERLINK "') + inner + code('" ') + SEPARATE + t("site") + END),
        p(ins(BEGIN, 5) + code(" x ") + ins(SEPARATE, 6) + t("a")) + p(t("b") + ins(END, 7)),
        p(ins(BEGIN, 5) + code(" x ") + ins(SEPARATE, 6) + kept_field + ins(END, 7)),
    ):
        with pytest.raises(DocxRefusedError, match="a field dropped whole"):
            tracked(docx(body))
    # A view that keeps some marks of a field and drops others is refused: Word's answer for
    # each is not one rule (field-separator-deleted, field-end-inserted).
    value = code(" DOCPROPERTY Title ")
    for body in (
        BEGIN + value + ins(SEPARATE) + t("v") + END,
        BEGIN + value + SEPARATE + t("v") + ins(END),
        ins(BEGIN) + value + SEPARATE + t("v") + END,
    ):
        with pytest.raises(DocxRefusedError, match="part of a field"):
            tracked(docx(p(body)))


def test_a_mark_before_a_table_the_view_drops_whole_joins_the_paragraph_after_it() -> None:
    def table(*rows: str) -> str:
        return f"<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/></w:tblGrid>{''.join(rows)}</w:tbl>"

    gone = f'<w:tr><w:trPr><w:del w:id="4" {WHO}/></w:trPr><w:tc>{p(dele("x"))}</w:tc></w:tr>'
    kept = f"<w:tr><w:tc>{p(t('y'))}</w:tc></w:tr>"
    body = p(dele("heading"), mark("del")) + table(gone) + p(t("after"))
    assert views(docx(body)) == {
        "accepted": [("after", None)],
        "original": [("heading", None), ("x", None), ("after", None)],
    }
    # A table the view keeps a row of: Word leaves the mark, a revision still (refused).
    with pytest.raises(DocxRefusedError, match="joins a row the view drops"):
        tracked(docx(p(t("a"), mark("del")) + table(gone, kept) + p(t("after"))))
    # A paragraph keeping text: Word moves it into the table and leaves the rows' changes
    # (mark-inserted-before-table-inserted-whole).
    with pytest.raises(DocxRefusedError, match="with text joins a table"):
        tracked(docx(p(t("a"), mark("del")) + table(gone) + p(t("after"))))


def test_a_bookmark_end_between_joined_paragraphs_stands_after_what_is_joined() -> None:
    start, end = '<w:bookmarkStart w:id="5" w:name="B"/>', '<w:bookmarkEnd w:id="5"/>'
    data = docx(p(start + t("a"), mark("del")) + end + p(t("b")))
    accepted, original, _ = tracked(data)
    assert views(data) == {"accepted": [("ab", None)], "original": [("a", None), ("b", None)]}
    with zipfile.ZipFile(io.BytesIO(accepted)) as package:
        body = ET.fromstring(package.read("word/document.xml")).find(f"{{{W}}}body")
    assert body is not None
    (joined,) = body.findall(f"{{{W}}}p")
    assert [c.tag.rsplit("}", 1)[1] for c in joined] == ["bookmarkStart", "r", "bookmarkEnd", "r"]
    certify_tracked(data, {"accepted": accepted, "original": original})
    # Left between paragraphs, or put before what is joined: never certified.
    part = "word/document.xml"
    for tampered in (
        _rewrite(accepted, part, lambda x: x.replace('<ns0:bookmarkEnd ns0:id="5" />', "")),
        _rewrite(
            accepted,
            part,
            lambda x: x.replace('<ns0:bookmarkEnd ns0:id="5" />', "").replace(
                '<ns0:bookmarkStart ns0:id="5" ns0:name="B" />',
                '<ns0:bookmarkStart ns0:id="5" ns0:name="B" /><ns0:bookmarkEnd ns0:id="5" />',
            ),
        ),
    ):
        with pytest.raises(CertificationError):
            certify_tracked(data, {"accepted": tampered})


def test_what_joins_before_a_table_gone_whole_is_the_whole_chain_and_its_bookmark_ends() -> None:
    def table(*rows: str) -> str:
        return f"<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/></w:tblGrid>{''.join(rows)}</w:tbl>"

    gone = table(
        f'<w:tr><w:trPr><w:del w:id="4" {WHO}/></w:trPr><w:tc>{p(dele("x"))}</w:tc></w:tr>'
    )
    end = '<w:bookmarkEnd w:id="5"/>'
    start = '<w:bookmarkStart w:id="5" w:name="B"/>'
    # An empty paragraph joined to the next carries a bookmark's end after it there.
    data = docx(p(start, mark("del")) + end + p(t("b")))
    accepted, original, _ = tracked(data)
    certify_tracked(data, {"accepted": accepted, "original": original})
    # A bookmark's end before the chain is not joined: the empty paragraph goes with the table.
    data = docx(p(start + t("a")) + end + p("", mark("del")) + gone + p(t("after")))
    accepted, original, _ = tracked(data)
    certify_tracked(data, {"accepted": accepted, "original": original})
    # Text joined through an empty paragraph, or a bookmark's end, meets the table: refused.
    for body in (
        p(t("a"), mark("del")) + p("", mark("del")) + gone + p(t("after")),
        p("", mark("del")) + end + gone + p(t("after")),
    ):
        with pytest.raises(DocxRefusedError, match="with text joins a table"):
            tracked(docx(body))
        empty = docx(p("", mark("del")) + gone + p(t("after")))
        with pytest.raises(CertificationError, match="content meets a table dropped"):
            certify_tracked(docx(body), {"accepted": tracked(empty)[0]})


def test_the_check_refuses_a_field_dropped_whole_whose_content_is_not_on_record() -> None:
    inner = ins(BEGIN, 5) + code("y") + ins(END, 6)
    kept_field = BEGIN + code(" PAGE ") + SEPARATE + t("1") + END
    any_view = tracked(docx(p(t("a") + ins(t("b")))))[1]
    for body in (
        # In another field; holding a field the view keeps; past its paragraph.
        p(BEGIN + code(' HYPERLINK "') + inner + code('" ') + SEPARATE + t("site") + END),
        p(ins(BEGIN, 5) + code(" x ") + ins(SEPARATE, 6) + kept_field + ins(END, 7)),
        p(ins(BEGIN, 5) + code(" x ") + ins(SEPARATE, 6) + t("a")) + p(t("b") + ins(END, 7)),
    ):
        with pytest.raises(CertificationError, match="content is not on record"):
            certify_tracked(docx(body), {"original": any_view})
    # Past its paragraph, keeping nothing (an empty run aside): every view held as it is.
    empty = "<w:r><w:rPr><w:b/></w:rPr></w:r>"
    across = docx(
        p(ins(BEGIN, 5) + ins(code(" x "), 8) + ins(SEPARATE, 6) + empty)
        + p(ins(t("v"), 9) + ins(END, 7))
    )
    accepted, original, _ = tracked(across)
    certify_tracked(across, {"accepted": accepted, "original": original})
    # A bookmark's end opening the body, joined to nothing: outside the paragraphs, as stored.
    opening = docx('<w:bookmarkEnd w:id="5"/>' + p(t("a") + ins(t("b"))))
    accepted, original, _ = tracked(opening)
    certify_tracked(opening, {"accepted": accepted, "original": original})


def test_the_check_holds_a_field_dropped_whole_and_a_table_gone_whole() -> None:
    wrapped = docx(
        p(t("See ") + ins(BEGIN, 5) + code("x.eu") + ins(SEPARATE, 6) + t("x") + ins(END, 7))
    )
    accepted, original, _ = tracked(wrapped)
    certify_tracked(wrapped, {"accepted": accepted, "original": original})
    body = "word/document.xml"
    kept = '<ns0:r><ns0:t xml:space="preserve">x</ns0:t></ns0:r>'
    for tampered in (
        # What its changes keep, kept in the original: its result, or its code made text.
        _rewrite(original, body, lambda x: x.replace("</ns0:p>", kept + "</ns0:p>")),
        _rewrite(
            original,
            body,
            lambda x: x.replace("</ns0:p>", kept.replace("x<", "x.eu<") + "</ns0:p>"),
        ),
    ):
        with pytest.raises(CertificationError):
            certify_tracked(wrapped, {"original": tampered})
    # A view keeping part of a field is never certified.
    split = docx(p(BEGIN + code(" DOCPROPERTY T ") + ins(SEPARATE) + t("v") + END))
    whole = docx(p(BEGIN + code(" DOCPROPERTY T ") + SEPARATE + ins(t("v")) + END))
    with pytest.raises(CertificationError, match="part of a field"):
        certify_tracked(split, {"original": tracked(whole)[1]})
    # An empty paragraph joined past a table gone whole goes with it: placed anywhere else,
    # never certified; one keeping text, never.
    gone = f'<w:tr><w:trPr><w:del w:id="4" {WHO}/></w:trPr><w:tc>{p(dele("x"))}</w:tc></w:tr>'
    table = f"<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/></w:tblGrid>{gone}</w:tbl>"
    joined = docx(p(dele("a"), mark("del")) + table + p(t("b")) + p(t("c")))
    accepted, _, _ = tracked(joined)
    certify_tracked(joined, {"accepted": accepted})
    apart = _rewrite(accepted, body, lambda x: x.replace("<ns0:body>", "<ns0:body><ns0:p />"))
    with pytest.raises(CertificationError):
        certify_tracked(joined, {"accepted": apart})
    holding = docx(p(t("a"), mark("del")) + table + p(t("b")) + p(t("c")))
    with pytest.raises(CertificationError, match="content meets a table dropped"):
        certify_tracked(holding, {"accepted": accepted})


CASES = Path(__file__).resolve().parents[1] / "corpus" / "tracked-cases"
# The cases the reader refuses: what it cannot undo exactly (Word's views are on record).
REFUSED = {
    "content-control-emptied",  # Word shows placeholder spaces the document does not hold
    "field-end-inserted",  # the original keeps a field's begin and drops its end
    "field-separator-deleted",  # the view keeps a field's begin and end and drops its separator
    "list-definition-changed",  # Word's Reject All rewrites the styles instead
    "mark-deleted-before-table-first-row-deleted",  # Word cannot accept it
    "mark-deleted-last-in-body",  # Word cannot accept it
    "mark-deleted-last-in-cell",  # Word dissolves the table
    "mark-inserted-before-table-inserted-whole",  # Word moves the text in, leaving a revision
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
        "checker": "conservation-check/1.19.0",
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


def test_the_check_holds_run_properties_and_leaves_changed_ones_to_word() -> None:
    # Properties a change records the former set of are, in the original view, held to Word
    # (test_every_case_reads_as_word_makes_it); every other run's are the source's.
    changed = f'<w:b/><w:rPrChange w:id="4" {WHO}><w:rPr/></w:rPrChange>'
    source = docx(p(r("<w:t>a</w:t>", "<w:b/>") + r("<w:t>c</w:t>", changed) + ins(t("b"))))
    accepted, original, _ = tracked(source)
    body = "word/document.xml"
    restyled = _rewrite(
        original, body, lambda x: x.replace("<ns0:rPr />", "<ns0:rPr><ns0:i /></ns0:rPr>")
    )
    assert restyled != original
    assert certify_tracked(source, {"original": restyled})["original"] == {
        "characters": 2,
        "elements": 0,
        "paragraphsJoined": 0,
    }
    for view, data in (("accepted", accepted), ("original", original)):
        plain = _rewrite(data, body, lambda x: x.replace("<ns0:rPr><ns0:b /></ns0:rPr>", "", 1))
        assert plain != data
        with pytest.raises(CertificationError, match="does not hold its content"):
            certify_tracked(source, {view: plain})
    # In the accepted view, the changed run's properties are the current ones.
    unbolded = _rewrite(accepted, body, lambda x: x[::-1].replace(">/ b:0sn<", "", 1)[::-1])
    with pytest.raises(CertificationError, match="does not hold its content"):
        certify_tracked(source, {"accepted": unbolded})


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
    # The views' own walk places a paragraph in its outermost cell; the reader, in its own.
    inner = f"<w:tbl>{_row('in1')}{_row('in2', 'del')}</w:tbl>"
    body = f"<w:tbl><w:tr><w:tc>{inner}{p(t('out'))}</w:tc></w:tr>{_row('second')}</w:tbl>"
    source = docx(body + p(t("z")))
    accepted, original, _ = tracked(source)
    assert [(x.text, x.table) for x in read_document(accepted).body] == [
        ("in1", (1, 0, 0)),
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
    # Formatting changes sharing id, author and date are distinct unless one copies the last in
    # its paragraph, former properties and all: in other paragraphs, with other former
    # properties, or outside a paragraph (a style's), each is listed.
    bold = f'<w:b/><w:rPrChange w:id="0" {WHO}><w:rPr/></w:rPrChange>'
    italic = f'<w:b/><w:rPrChange w:id="0" {WHO}><w:rPr><w:i/></w:rPr></w:rPrChange>'
    body = p(r("<w:t>a</w:t>", bold)) + p(r("<w:t>b</w:t>", bold) + r("<w:t>c</w:t>", italic))
    _, _, changes = tracked(docx(body))
    assert [c.kind for c in changes] == ["format"] * 3
    styles = "".join(
        f'<w:style w:type="character" w:styleId="{k}"><w:rPr><w:i/>{bold[6:]}</w:rPr></w:style>'
        for k in "UV"
    )
    _, _, changes = tracked(docx(p(t("m")), styles=styles))
    assert [(c.part, c.kind) for c in changes] == [("word/styles.xml", "format")] * 2


@pytest.mark.parametrize("rows", ["first", "every"])
def test_a_join_into_a_nested_row_the_view_drops_is_refused(rows: str) -> None:
    # Word joins into the first paragraph in document order and cannot remove a row it joins
    # into; for a nested table its answer is not on record (tracked-cases to record it).
    second = _row("inner second", "del" if rows == "every" else "")
    inner = f"<w:tbl>{_row('inner first', 'del')}{second}</w:tbl>"
    outer = f"<w:tbl><w:tr><w:tc>{inner}{p(t('outer'))}</w:tc></w:tr></w:tbl>"
    body = p(t("before"), mark("del")) + outer + p(t("after"))
    with pytest.raises(DocxRefusedError, match="a row the view drops"):
        tracked(docx(body))
    with pytest.raises(CertificationError, match="meets a row the view drops"):
        certify_tracked(docx(body), {"accepted": docx(p(t("x")))})


@pytest.mark.parametrize(
    "first",
    [
        '<w:footnotePr><w:numFmt w:val="lowerRoman"/><w:numStart w:val="3"/></w:footnotePr>',
        '<w:endnotePr><w:numRestart w:val="eachSect"/></w:endnotePr>',
        "<w:titlePg/>",
    ],
)
def test_a_section_break_a_view_drops_between_sections_that_differ_in_what_is_read_is_refused(
    first: str,
) -> None:
    # Which section's note numbering the joined section keeps is not on record (tracked-cases
    # to record: section 1 lowerRoman from 3 with its break deleted, Accept All).
    body = p(t("first"), f"<w:sectPr>{first}</w:sectPr>{mark('del')}") + p(t("second"))
    with pytest.raises(DocxRefusedError, match="section"):
        tracked(docx(body + "<w:sectPr/>"))
    # The same settings on both sides: the join is read (mark-deleted-section-end).
    tracked(docx(body + f"<w:sectPr>{first}</w:sectPr>"))


def test_a_change_inside_a_floating_object_is_refused_and_one_outside_it_is_read() -> None:
    from test_reader import FLOATING_TEXT, IN_BOX

    drawing = FLOATING_TEXT["text-box"][0]
    # A change beside the object: both views read, each with the object set aside unread.
    value = result(docx(p(t("a") + ins(t("b")) + r(drawing))))
    for view in VIEWS:
        (paragraph,) = value["tracked"][view]["paragraphs"]
        assert paragraph["anchored"] == [
            {"kind": "text-box", "offset": len(paragraph["text"]), "read": False}
        ]
    # A change inside it: in neither view's text, yet listed; refused, and never certified.
    changed = docx(p(t("a") + r(drawing.replace(IN_BOX, p(ins(t("b")))))))
    assert changed_drawing(changed) == "word/document.xml"
    assert changed_drawing(docx(p(t("a") + ins(t("b")) + r(drawing)))) is None
    assert result(changed)["refusal"] == {
        "code": "tracked-change",
        "detail": "a change inside a drawing in word/document.xml",
    }
    # A view refused first keeps its own refusal, in the order the reader meets it.
    refused = docx(
        p(r('<w:sym w:font="Wingdings" w:char="F0A7"/>'))
        + p(t("a") + r(drawing.replace(IN_BOX, p(ins(t("b"))))))
    )
    assert result(refused)["refusal"]["detail"].startswith("accepted view: ")
    data = docx(p(t("a") + r(drawing)))
    with pytest.raises(CertificationError, match="a revision inside a drawing"):
        certify_tracked(changed, dict.fromkeys(VIEWS, data))
