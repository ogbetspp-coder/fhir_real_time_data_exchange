"""Headers, footers and comments: read by the body's rules, or refused on their own.

Each header and footer part the sections refer to is read once, with the (section, type) uses
that name it; each comment as stored, with its author, initials and date and where its mark
stands. A part the reader cannot read exactly is refused on its own and the rest is read; the
result counts it (``refusedParts``) and the receipt says ``read-in-part``. The conservation
check covers them all.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

from label_docx import output
from label_docx.certify import CertificationError, DocxSource
from label_docx.reader import DocxRefusedError, read_document
from test_reader import W, docx, p, r

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
KIND = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def with_parts(
    data: bytes, parts: dict[str, str], relationships: list[tuple[str, str, str]]
) -> bytes:
    """``data`` with ``parts`` added under ``word/`` and the document's relationships to them."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "word/_rels/document.xml.rels":
                extra = "".join(
                    f'<Relationship Id="{i}" Type="{KIND}{kind}" Target="{t}"/>'
                    for i, kind, t in relationships
                )
                content = content.replace(b"</Relationships>", extra.encode() + b"</Relationships>")
            target.writestr(info, content)
        for name, xml in parts.items():
            target.writestr("word/" + name, xml)
    return out.getvalue()


def reference(kind: str, relationship: str, type_: str = "default") -> str:
    return f'<w:{kind}Reference xmlns:r="{R}" w:type="{type_}" r:id="{relationship}"/>'


def header(content: str, kind: str = "hdr") -> str:
    return f'<w:{kind} xmlns:w="{W}">{content}</w:{kind}>'


PAGE = (
    r('<w:fldChar w:fldCharType="begin"/>')
    + r('<w:instrText xml:space="preserve"> PAGE </w:instrText>')
    + r('<w:fldChar w:fldCharType="separate"/>')
    + r("<w:t>3</w:t>")
    + r('<w:fldChar w:fldCharType="end"/>')
)


def _document(body_sections: str, parts: dict[str, str], rels: list[tuple[str, str, str]]) -> bytes:
    return with_parts(docx(body_sections), parts, rels)


def test_headers_and_footers_are_read_once_each_with_their_uses() -> None:
    body = (
        p(
            r("<w:t>One</w:t>"),
            f"<w:sectPr>{reference('header', 'h1')}{reference('footer', 'f1')}</w:sectPr>",
        )
        + p(r("<w:t>Two</w:t>"))
        + f"<w:sectPr>{reference('header', 'h1')}{reference('header', 'h2', 'first')}</w:sectPr>"
    )
    parts = {
        "header1.xml": header(p(r("<w:t>Product name</w:t>"))),
        "header2.xml": header(p(r("<w:t>First page</w:t>"))),
        "footer1.xml": header(p(r('<w:t xml:space="preserve">Page </w:t>') + PAGE), "ftr"),
    }
    rels = [
        ("h1", "header", "header1.xml"),
        ("h2", "header", "header2.xml"),
        ("f1", "footer", "footer1.xml"),
    ]
    data = _document(body, parts, rels)
    document = read_document(data)
    assert [(h.part, h.uses, [x.text for x in h.paragraphs]) for h in document.headers] == [
        ("word/header1.xml", ((0, "default"), (1, "default")), ["Product name"]),
        ("word/header2.xml", ((1, "first"),), ["First page"]),
    ]
    (footer,) = document.footers
    assert (footer.paragraphs[0].text, footer.paragraphs[0].pages) == ("Page ", (5,))
    value = json.loads(output.read(data)[0])
    assert value["refusedParts"] == 0
    assert value["certificate"]["notRead"] == {}
    assert "word/header2.xml" in value["certificate"]["scope"]


def test_a_footer_the_reader_cannot_read_is_refused_on_its_own() -> None:
    date = PAGE.replace(" PAGE ", " DATE ")
    body = p(r("<w:t>Body</w:t>")) + f"<w:sectPr>{reference('footer', 'f1')}</w:sectPr>"
    data = _document(
        body, {"footer1.xml": header(p(date), "ftr")}, [("f1", "footer", "footer1.xml")]
    )
    value = json.loads(output.read(data)[0])
    assert [p_["text"] for p_ in value["paragraphs"]] == ["Body"]
    assert value["footers"][0]["refusal"]["code"] == "computed-field"
    assert value["footers"][0]["paragraphs"] == []
    assert value["refusedParts"] == 1
    assert value["certificate"]["refused"] == ["word/footer1.xml"]


@pytest.mark.parametrize(
    ("relationships", "code"),
    [([], "invalid-package"), ([("h1", "styles", "header1.xml")], "invalid-package")],
    ids=["no-relationship", "relationship-of-another-kind"],
)
def test_a_header_reference_must_name_a_header_part(
    relationships: list[tuple[str, str, str]], code: str
) -> None:
    body = p(r("<w:t>x</w:t>")) + f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>"
    data = _document(body, {"header1.xml": header(p(""))}, relationships)
    with pytest.raises(DocxRefusedError) as caught:
        read_document(data)
    assert caught.value.code == code


COMMENT = (
    '<w:comment w:id="0" w:author="Reviewer" w:initials="RV" w:date="2026-01-02T03:04:05Z">'
    + p(r("<w:annotationRef/>") + r("<w:t>Check this dose.</w:t>"))
    + "</w:comment>"
)


def _commented(body: str, comments: str = COMMENT) -> bytes:
    return _document(
        body, {"comments.xml": header(comments, "comments")}, [("c", "comments", "comments.xml")]
    )


def test_a_comment_is_read_with_its_author_date_and_where_it_stands() -> None:
    body = p(
        r("<w:t>Take 10 mg</w:t>")
        + r('<w:commentReference w:id="0"/>')
        + r('<w:t xml:space="preserve"> daily</w:t>')
    )
    data = _commented(body)
    document = read_document(data)
    (comment,) = document.comments
    assert (comment.id, comment.author, comment.initials, comment.date) == (
        0,
        "Reviewer",
        "RV",
        "2026-01-02T03:04:05Z",
    )
    assert [x.text for x in comment.paragraphs] == ["Check this dose."]
    assert document.body[0].text == "Take 10 mg daily"
    assert [(c.offset, c.id) for c in document.body[0].comments] == [(10, 0)]
    value = json.loads(output.read(data)[0])
    assert value["comments"][0]["author"] == "Reviewer"
    assert value["paragraphs"][0]["comments"] == [{"id": 0, "offset": 10}]


@pytest.mark.parametrize(
    ("body", "comments", "code"),
    [
        (p(r("<w:t>x</w:t>")), COMMENT, "unread-content"),
        (p(r('<w:commentReference w:id="9"/>')), COMMENT, "invalid-package"),
        (
            p(r('<w:commentReference w:id="0"/><w:commentReference w:id="0"/>')),
            COMMENT,
            "invalid-package",
        ),
        (p(r("<w:annotationRef/>")), COMMENT, "unsupported-element"),
        (p(r('<w:commentReference w:id="0"/>')), COMMENT + COMMENT, "invalid-package"),
        (p(r('<w:commentReference w:id="0"/>')), "<w:p/>", "unsupported-element"),
    ],
    ids=[
        "anchored-nowhere",
        "mark-of-no-comment",
        "mark-twice",
        "echo-in-the-body",
        "defined-twice",
        "not-a-comment",
    ],
)
def test_comments_and_their_marks_must_match_one_to_one(
    body: str, comments: str, code: str
) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read_document(_commented(body, comments))
    assert caught.value.code == code


def test_a_comment_the_reader_cannot_read_is_refused_on_its_own() -> None:
    hidden = COMMENT.replace(
        "<w:t>Check this dose.</w:t>", "<w:rPr><w:vanish/></w:rPr><w:t>x</w:t>"
    )
    data = _commented(p(r("<w:t>Body</w:t>") + r('<w:commentReference w:id="0"/>')), hidden)
    value = json.loads(output.read(data)[0])
    assert value["comments"][0]["refusal"]["code"] == "hidden-text"
    assert value["refusedParts"] == 1
    assert value["certificate"]["refused"] == ["word/comments.xml#0"]
    in_comment = COMMENT.replace("<w:annotationRef/>", '<w:commentReference w:id="0"/>')
    value = json.loads(
        output.read(_commented(p(r('<w:commentReference w:id="0"/>')), in_comment))[0]
    )
    assert value["comments"][0]["refusal"]["code"] == "unsupported-element"


# --- the conservation check over headers, footers and comments ---------------------------------


def _source_and_value(data: bytes) -> tuple[DocxSource, dict[str, Any]]:
    return DocxSource(data), json.loads(output.read(data)[0])


def test_the_check_holds_headers_footers_and_comments_to_the_document() -> None:
    body = (
        p(r("<w:t>Take 10 mg</w:t>") + r('<w:commentReference w:id="0"/>'))
        + f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>"
    )
    data = with_parts(
        docx(body),
        {
            "header1.xml": header(p(r("<w:t>Product</w:t>"))),
            "comments.xml": header(COMMENT, "comments"),
        },
        [("h1", "header", "header1.xml"), ("c", "comments", "comments.xml")],
    )
    source, value = _source_and_value(data)
    certificate = source.certify(value)
    assert certificate["output"]["characters"] == len("Take 10 mgProductCheck this dose.")

    def changed(edit: str) -> dict[str, Any]:
        other: dict[str, Any] = json.loads(json.dumps(value))
        if edit == "header-text":
            other["headers"][0]["paragraphs"][0]["text"] = "Produkt"
        elif edit == "header-use":
            other["headers"][0]["uses"][0]["type"] = "first"
        elif edit == "comment-author":
            other["comments"][0]["author"] = "Someone else"
        elif edit == "comment-text":
            other["comments"][0]["paragraphs"][0]["text"] = "Check the dose."
        elif edit == "comment-mark":
            other["paragraphs"][0]["comments"][0]["offset"] = 0
        elif edit == "header-hidden-as-refused":
            other["headers"][0]["refusal"] = {"code": "x", "detail": "x"}
            other["headers"][0]["paragraphs"] = []
        elif edit == "refused-with-text":
            other["headers"][0]["refusal"] = {"code": "x", "detail": "x"}
            other["refusedParts"] = 1
        return other

    for edit in (
        "header-text",
        "header-use",
        "comment-author",
        "comment-text",
        "comment-mark",
        "header-hidden-as-refused",
        "refused-with-text",
    ):
        with pytest.raises(CertificationError):
            source.certify(changed(edit))


def test_the_check_reads_a_comment_echo_only_in_a_comment() -> None:
    with pytest.raises(CertificationError):
        DocxSource(docx(p(r("<w:annotationRef/>"))))
    in_comment = COMMENT.replace("<w:annotationRef/>", '<w:commentReference w:id="0"/>')
    data = _commented(p(r('<w:commentReference w:id="0"/>')), in_comment)
    source, value = _source_and_value(data)
    # The reader refused that comment; the check could not read it either, and says so if told
    # it was read.
    assert source.certify(value)["refused"] == ["word/comments.xml#0"]
    claimed = json.loads(json.dumps(value))
    claimed["comments"][0]["refusal"] = None
    claimed["refusedParts"] = 0
    with pytest.raises(CertificationError):
        source.certify(claimed)


@pytest.mark.parametrize(
    "relationships",
    [
        [],
        [("h1", "styles", "header1.xml")],
        # The target's closing quote opens the attribute that makes it external.
        [("h1", "header", 'http://example.org/h.xml" TargetMode="External')],
    ],
    ids=["no-relationship", "relationship-of-another-kind", "external"],
)
def test_the_check_refuses_a_header_reference_to_no_header_part(
    relationships: list[tuple[str, str, str]],
) -> None:
    body = p(r("<w:t>x</w:t>")) + f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>"
    data = _document(body, {"header1.xml": header(p(""))}, relationships)
    with pytest.raises(CertificationError):
        DocxSource(data)


def test_a_header_by_absolute_name_and_of_no_stated_type() -> None:
    body = (
        p(r("<w:t>x</w:t>")) + f'<w:sectPr><w:headerReference xmlns:r="{R}" r:id="h1"/></w:sectPr>'
    )
    data = _document(
        body, {"header1.xml": header(p(r("<w:t>H</w:t>")))}, [("h1", "header", "/word/header1.xml")]
    )
    source, value = _source_and_value(data)
    assert value["headers"][0]["uses"] == [{"section": 0, "type": "default"}]
    assert "word/header1.xml" in source.certify(value)["scope"]


def test_a_comment_mark_after_text_in_the_same_run_stands_after_it() -> None:
    body = p('<w:r><w:t>Take</w:t><w:commentReference w:id="0"/></w:r>')
    source, value = _source_and_value(_commented(body))
    assert value["paragraphs"][0]["comments"] == [{"id": 0, "offset": 4}]
    certificate = source.certify(value)
    assert "word/comments.xml" in certificate["scope"]
    value["paragraphs"][0]["comments"][0]["offset"] = 0
    with pytest.raises(CertificationError):
        source.certify(value)


def test_the_check_never_reads_a_comment_mark_inside_a_comment() -> None:
    in_comment = COMMENT.replace(
        "<w:annotationRef/>", '<w:annotationRef/></w:r><w:r><w:commentReference w:id="0"/>'
    )
    source, value = _source_and_value(
        _commented(p(r('<w:commentReference w:id="0"/>')), in_comment)
    )
    # Claimed read, with the mark placed where it stands: still never certified.
    value["comments"][0]["refusal"] = None
    value["comments"][0]["paragraphs"] = [
        {
            "comments": [{"id": 0, "offset": 0}],
            "notes": [],
            "pages": [],
            "table": None,
            "text": "Check this dose.",
        }
    ]
    value["refusedParts"] = 0
    with pytest.raises(CertificationError):
        source.certify(value)
