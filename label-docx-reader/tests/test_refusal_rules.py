"""Refusal rules each reached on their own, by an input no earlier rule refuses.

Each case is the one minimal .docx that only its rule stands between and a wrong read or a
crash: without the rule the document is read and certified, or the reader fails.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from label_docx import output, reader
from label_docx.reader import DocxRefusedError, read_document
from numbering_cases import abstract, lvl, num
from test_headers_comments import header, reference, with_parts
from test_reader import (
    CONTENT_TYPES,
    NUMBERING,
    RELATIONSHIP,
    ROOT_RELS,
    SYMBOL_BULLET,
    W,
    document_xml,
    docx,
    fnote,
    li,
    p,
    r,
    ref,
)


def _refusal(data: bytes) -> tuple[str, str]:
    with pytest.raises(DocxRefusedError) as caught:
        read_document(data)
    return caught.value.code, caught.value.detail


def _field(code: str, stored: str) -> str:
    return (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve"> {code} </w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r(f'<w:t xml:space="preserve">{stored}</w:t>')
        + r('<w:fldChar w:fldCharType="end"/>')
    )


def _marked(name: str, inner: str) -> str:
    return f'<w:bookmarkStart w:id="1" w:name="{name}"/>{inner}<w:bookmarkEnd w:id="1"/>'


def _with_header(content: str, numbering: str | None = None) -> bytes:
    body = p(r("<w:t>Body</w:t>")) + f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>"
    return with_parts(
        docx(body, numbering=numbering),
        {"header1.xml": header(content)},
        [("h1", "header", "header1.xml")],
    )


def _updating_fields(body: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + RELATIONSHIP.format(kind="settings", target="settings.xml")
            + "</Relationships>",
        )
        package.writestr("word/document.xml", document_xml(body))
        package.writestr(
            "word/settings.xml",
            f'<w:settings xmlns:w="{W}"><w:updateFields w:val="true"/></w:settings>',
        )
    return buffer.getvalue()


# --- fields -----------------------------------------------------------------------------

H1 = '<w:style w:type="paragraph" w:styleId="H1"><w:name w:val="heading 1"/></w:style>'
HEADING = '<w:pStyle w:val="H1"/>'


def test_a_seq_field_in_a_note_is_refused() -> None:
    note = p(r("<w:footnoteRef/>") + _field("SEQ Table", "7"))
    data = docx(p(r("<w:t>a</w:t>") + ref(1)), footnotes=fnote(1, note))
    code, detail = _refusal(data)
    assert code == "computed-field"
    assert detail.endswith("in a footnote")


@pytest.mark.parametrize(
    ("content", "code", "fragment"),
    [
        (p(_field("SEQ Table", "7")), "computed-field", "a computed field or PAGEREF in a header"),
        (li(3), "unsupported-numbering", "a list in a header"),
    ],
    ids=["seq", "list"],
)
def test_a_computed_field_or_list_in_a_header_refuses_the_header(
    content: str, code: str, fragment: str
) -> None:
    (story,) = read_document(_with_header(content, NUMBERING)).headers
    assert story.refusal is not None
    assert story.refusal[0] == code
    assert fragment in story.refusal[1]
    assert story.paragraphs == ()


@pytest.mark.parametrize(
    "body",
    [
        p(_field("DOCPROPERTY Title", "Old title")),
        p(
            r('<w:fldChar w:fldCharType="begin"/>')
            + r('<w:instrText xml:space="preserve"> TOC \\o "1-3" </w:instrText>')
            + r('<w:fldChar w:fldCharType="separate"/>')
            + r("<w:t>Stale entry</w:t>")
        )
        + p(r('<w:fldChar w:fldCharType="end"/>')),
    ],
    ids=["docproperty", "toc"],
)
def test_a_complex_field_in_a_document_that_updates_fields_on_open_is_refused(body: str) -> None:
    assert _refusal(_updating_fields(body)) == (
        "computed-field",
        "the document updates fields on open",
    )


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (p(_field("SEQ T \\* roman \\* ARABIC", "1")), "two number formats"),
        (
            p(_marked("t", r("<w:t>abc</w:t>"))) + p(_field("REF t \\* Upper", "abc")),
            "REF field the reader cannot compute",
        ),
        (
            p(r("<w:t>a</w:t>") + _marked("fn", ref(1) + ref(2))) + p(_field("NOTEREF fn", "1")),
            "NOTEREF to a bookmark without one note",
        ),
        (
            p(_field("SEQ T", "1"))
            + p(_marked("b", _field("SEQ T", "2")))
            + p(_field("SEQ T b", "3")),
            "SEQ field without one identifier",
        ),
        (p(_field("SEQ T \\c", "0")), "SEQ \\c field before any count"),
        (p(_field("SEQ T \\* Ordinal", "1st")), "SEQ number format"),
        (
            p(r("<w:t>Intro</w:t>"), HEADING) + p(_field("STYLEREF 1 x", "Intro")),
            "STYLEREF field without one style",
        ),
        (
            p(r("<w:t>Intro</w:t>"), HEADING) + p(_field("STYLEREF 1", "Intro"), HEADING),
            "in a paragraph of its style",
        ),
        (p(_field("STYLEREF Caption", "x")), "which no paragraph has"),
        (
            p(r("<w:t>Intro</w:t>") + ref(1), HEADING) + p(_field("STYLEREF 1", "Intro")),
            "a paragraph with a note mark",
        ),
        (
            p(r("<w:t>Intro</w:t>"), HEADING) + p(_field("STYLEREF 1 \\s", "Intro")),
            "STYLEREF \\s to a label",
        ),
        (
            p(
                r('<w:fldChar w:fldCharType="begin"/>')
                + r('<w:instrText xml:space="preserve"> SEQ T </w:instrText>')
                + r('<w:fldChar w:fldCharType="separate"/>')
                + r("<w:t>1</w:t>")
            )
            + p(r("<w:t>2</w:t>") + r('<w:fldChar w:fldCharType="end"/>')),
            "result runs past its paragraph",
        ),
    ],
    ids=[
        "seq-two-formats",
        "ref-format-switch",
        "noteref-two-notes",
        "seq-bookmark-argument",
        "seq-c-first",
        "seq-ordinal",
        "styleref-two-arguments",
        "styleref-in-own-style",
        "styleref-no-such-style",
        "styleref-to-note-mark",
        "styleref-s-no-label",
        "seq-result-past-paragraph",
    ],
)
def test_seq_styleref_ref_and_noteref_forms_word_computes_otherwise_are_refused(
    body: str, fragment: str
) -> None:
    code, detail = _refusal(docx(body, H1, footnotes=fnote(1) + fnote(2)))
    assert code == ("unbalanced-field" if "past its paragraph" in fragment else "computed-field")
    assert fragment in detail


def _read_text(data: bytes) -> list[str]:
    return [paragraph.text for paragraph in read_document(data).body]


def test_a_styleref_with_a_format_switch_is_refused() -> None:
    # As REF: Word's print of "Intro" under \* Upper is not on record; MERGEFORMAT changes no text.
    intro = p(r("<w:t>Intro</w:t>"), HEADING)
    for switch in ("Upper", "Lower", "Caps", "roman"):
        body = intro + p(_field(f"STYLEREF 1 \\* {switch}", "Intro"))
        assert _refusal(docx(body, H1)) == (
            "computed-field",
            "a STYLEREF field with a format switch",
        )
    assert _read_text(docx(intro + p(_field("STYLEREF 1 \\* MERGEFORMAT", "Intro")), H1)) == [
        "Intro",
        "Intro",
    ]


def test_a_cross_reference_to_text_holding_a_page_number_is_refused() -> None:
    # The stored result matches the text, which leaves the page number Word prints out.
    page = _field("PAGE", "3")
    marked = p(_marked("bm", r('<w:t xml:space="preserve">Page </w:t>') + page))
    assert _refusal(docx(marked + p(_field("REF bm \\h", "Page ")))) == (
        "computed-field",
        "a REF to a bookmark holding a page number",
    )
    heading = p(r('<w:t xml:space="preserve">Chapter </w:t>') + page, HEADING)
    assert _refusal(docx(heading + p(_field("STYLEREF 1", "Chapter ")), H1)) == (
        "computed-field",
        "a STYLEREF to a paragraph with a page number",
    )


@pytest.mark.parametrize(
    ("properties", "paragraph", "element"),
    [
        ("<w:color/>", "", "color"),
        ('<w:color w:themeColor="background1"/>', "", "color"),
        ("<w:vertAlign/>", "", "vertAlign"),
        ("<w:highlight/>", "", "highlight"),
        ("<w:position/>", "", "position"),
        ('<w:shd w:fill="000000"/>', "", "shd"),
        ("<w:sz/>", "", "sz"),
        ("", '<w:shd w:fill="000000"/>', "shd"),
    ],
    ids=["color", "theme-color", "vert-align", "highlight", "position", "shd", "sz", "p-shd"],
)
def test_a_property_without_its_value_is_refused(
    properties: str, paragraph: str, element: str
) -> None:
    # Skipped, a style's value would show through it; read, it would be a guess at Word's.
    styles = (
        '<w:style w:type="character" w:styleId="C"><w:rPr><w:vertAlign w:val="superscript"/>'
        '<w:u w:val="single"/><w:color w:val="FFFFFF"/><w:position w:val="6"/></w:rPr></w:style>'
    )
    body = p(r("<w:t>9</w:t>", '<w:rStyle w:val="C"/>' + properties), paragraph)
    assert _refusal(docx(body, styles)) == ("unsupported-formatting", f"w:{element} without w:val")


@pytest.mark.parametrize(
    ("body", "footnotes"),
    [
        (
            p(_marked("store", r("<w:t>below 25 C</w:t>")))
            + p(_marked("STORE", r("<w:t>below 30 C</w:t>")).replace('"1"', '"2"'))
            + p(_field("REF STORE \\h", "below 30 C")),
            None,
        ),
        (
            p(_marked("STORE", r("<w:t>below 30 C</w:t>")) + ref(1))
            + p(_field("REF STORE \\h", "below 30 C")),
            fnote(
                1,
                p(
                    r("<w:footnoteRef/>")
                    + _marked("store", r("<w:t>x</w:t>")).replace('"1"', '"9"')
                ),
            ),
        ),
        (
            p(
                _marked("bm", r("<w:t>below</w:t>"))
                + r("<w:t>25 C</w:t>")
                + '<w:bookmarkEnd w:id="1"/>'
            )
            + p(_field("REF bm", "below")),
            None,
        ),
        (
            p('<w:bookmarkStart w:id="1" w:name="bm"/>' + _marked("other", r("<w:t>below</w:t>")))
            + p(_field("REF other", "below")),
            None,
        ),
        (
            p(
                '<w:bookmarkEnd w:id="1"/>'
                + r("<w:t>below</w:t>")
                + '<w:bookmarkStart w:id="1" w:name="bm"/>'
            )
            + p(_field("REF bm \\h", "")),
            None,
        ),
        (
            p(_marked("bm", r("<w:t>below</w:t>")))
            + p(_marked("BM", r("<w:t>x</w:t>")).replace('"1"', '"2"'))
            + p(_field("PAGEREF bm \\h", "1")),
            None,
        ),
    ],
    ids=[
        "names-differ-in-case",
        "case-twin-in-a-note",
        "two-ends",
        "two-starts",
        "end-first",
        "pageref",
    ],
)
def test_a_bookmark_word_may_find_otherwise_is_refused(body: str, footnotes: str | None) -> None:
    assert _refusal(docx(body, footnotes=footnotes)) == (
        "computed-field",
        f"a {'PAGEREF' if 'PAGEREF' in body else 'REF'} to a bookmark it cannot read",
    )


def test_seq_identifiers_that_differ_only_in_case_are_refused() -> None:
    body = p(_field("SEQ Table", "1")) + p(_field("SEQ table", "1"))
    assert _refusal(docx(body)) == ("computed-field", "SEQ identifiers that differ only in case")


@pytest.mark.parametrize(
    "space", ["\u00a0", "&#10;", "\u2003", "\u3000"], ids=["nbsp", "lf", "em", "ideographic"]
)
def test_a_field_code_with_whitespace_other_than_spaces_is_refused(space: str) -> None:
    marked = p(_marked("bm", r("<w:t>below</w:t>")))
    for code in (f"REF{space}bm", f'HYPERLINK{space}"https://example.org"'):
        assert _refusal(docx(marked + p(_field(code, "below")))) == (
            "computed-field",
            "a field code with whitespace other than spaces",
        )
    assert _read_text(docx(marked + p(_field("REF\tbm", "below")))) == ["below", "below"]
    # Inside quotes it parts no words: a hyperlink's tooltip.
    tooltip = f'HYPERLINK "https://example.org" \\o "see{space}here"'
    assert _read_text(docx(p(_field(tooltip, "link")))) == ["link"]


@pytest.mark.parametrize(
    ("styles", "props", "paragraph"),
    [
        (
            '<w:style w:type="character" w:styleId="HB"><w:rPr><w:highlight w:val="black"/>'
            "</w:rPr></w:style>",
            '<w:rStyle w:val="HB"/><w:color w:val="FFFFFF"/>',
            "",
        ),
        (
            '<w:style w:type="paragraph" w:styleId="HB"><w:rPr><w:highlight w:val="yellow"/>'
            "</w:rPr></w:style>",
            "",
            '<w:pStyle w:val="HB"/>',
        ),
        (
            '<w:docDefaults><w:rPrDefault><w:rPr><w:highlight w:val="none"/></w:rPr>'
            "</w:rPrDefault></w:docDefaults>",
            "",
            "",
        ),
    ],
    ids=["character-style", "paragraph-style", "defaults"],
)
def test_a_highlight_set_by_a_style_is_refused(styles: str, props: str, paragraph: str) -> None:
    body = p(r("<w:t>not for IV use</w:t>", props), paragraph)
    assert _refusal(docx(body, styles)) == ("unsupported-formatting", "a highlight set by a style")
    # Set on the run itself, over the style's, it is read and marked.
    direct = p(r("<w:t>x</w:t>", props + '<w:highlight w:val="green"/>'), paragraph)
    (read,) = read_document(docx(direct, styles)).body
    assert [m.kind for m in read.marks if m.kind.startswith("highlight")] == ["highlight-green"]


# --- numbering --------------------------------------------------------------------------


def test_a_picture_bullet_is_refused_and_one_no_level_names_is_set_aside() -> None:
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    # As Word writes one: a VML picture, a DrawingML one for later readers.
    written = (
        f'<w:numPicBullet w:numPicBulletId="0"><mc:AlternateContent xmlns:mc="{mc}">'
        '<mc:Choice Requires="v"><w:pict/></mc:Choice><mc:Fallback><w:drawing/></mc:Fallback>'
        "</mc:AlternateContent></w:numPicBullet>"
    )
    named = abstract(1, lvl(0, "bullet", "", '<w:lvlPicBulletId w:val="0"/>' + SYMBOL_BULLET))
    for definition in ('<w:numPicBullet w:numPicBulletId="0"><w:pict/></w:numPicBullet>', written):
        assert _refusal(docx(li(1), numbering=definition + named + num(1, 1))) == (
            "unsupported-numbering",
            "lvlPicBulletId in a list level",
        )
    # Defined and named by no level, as labels made from a template keep one: nothing draws it.
    plain = abstract(1, lvl(0, "bullet", "", SYMBOL_BULLET)) + num(1, 1)
    unnamed = docx(li(1), numbering=written + plain)
    (item,) = read_document(unnamed).body
    assert item.numbering is not None
    assert output.read(unnamed)[1]


def test_a_symbol_bullet_under_conditional_table_fonts_is_refused() -> None:
    banded = (
        '<w:style w:type="table" w:styleId="Banded"><w:tblStylePr w:type="firstRow">'
        '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr></w:tblStylePr></w:style>'
    )
    bullet = abstract(1, lvl(0, "bullet", "", SYMBOL_BULLET)) + num(1, 1)
    table = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="Banded"/></w:tblPr>'
        f"<w:tr><w:tc>{li(1)}</w:tc></w:tr></w:tbl>"
    )
    code, detail = _refusal(docx(table, banded, numbering=bullet))
    assert code == "symbol-font"
    assert detail.endswith("under conditional table fonts")


def _start_at(level: int, value: int) -> str:
    return f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="{value}"/></w:lvlOverride>'


@pytest.mark.parametrize(
    ("body", "numbering", "fragment"),
    [
        (
            # Level 2 restarts after level 0 only; a level-3 item counts it first.
            li(1) + li(1, 3),
            abstract(
                1,
                lvl(0, text="%1."),
                lvl(1, text="%1.%2."),
                lvl(2, text="%1.%2.%3.", extra='<w:lvlRestart w:val="1"/>'),
                lvl(3, text="%1.%2.%3.%4."),
            )
            + num(1, 1),
            "level 2, which has lvlRestart, never counted",
        ),
        (
            # List 12 restarts level 1 at 5; list 10's level-2 item counts level 1 first.
            li(12) + li(10, 2),
            abstract(7, lvl(0, text="%1."), lvl(1, text="%1.%2."), lvl(2, text="%1.%2.%3."))
            + num(10, 7)
            + num(12, 7, _start_at(1, 5)),
            "level 1, restarted by another list, never counted",
        ),
    ],
    ids=["own-lvlrestart", "restarted-by-another-list"],
)
def test_a_restarted_level_a_deeper_item_counts_first_is_ambiguous(
    body: str, numbering: str, fragment: str
) -> None:
    assert _refusal(docx(body, numbering=numbering)) == ("ambiguous-numbering", fragment)


# --- package ----------------------------------------------------------------------------

COMMENT = (
    f'<w:comments xmlns:w="{W}"><w:comment w:id="0" w:author="A">'
    "<w:p><w:r><w:t>c</w:t></w:r></w:p></w:comment></w:comments>"
)
ANCHORED = p(
    '<w:commentRangeStart w:id="0"/>'
    + r("<w:t>a</w:t>")
    + '<w:commentRangeEnd w:id="0"/>'
    + r('<w:commentReference w:id="0"/>')
)
NOTED = p(r("<w:t>a</w:t>") + ref(1))


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (
            docx(NOTED, footnotes=fnote(1, p(r("<w:t>one</w:t>"))) + fnote(1)),
            "footnote 1 is defined twice",
        ),
        (
            with_parts(
                docx(NOTED, footnotes=fnote(1)),
                {"footnotes2.xml": f'<w:footnotes xmlns:w="{W}">{fnote(1)}</w:footnotes>'},
                [("rX", "footnotes", "footnotes2.xml")],
            ),
            "more than one footnote part",
        ),
        (
            with_parts(docx(p(r("<w:t>a</w:t>"))), {}, [("rX", "footnotes", "nowhere.xml")]),
            "no word/nowhere.xml",
        ),
        (
            with_parts(
                docx(ANCHORED),
                {"comments.xml": COMMENT, "comments2.xml": COMMENT},
                [("c1", "comments", "comments.xml"), ("c2", "comments", "comments2.xml")],
            ),
            "more than one comments part",
        ),
        (
            with_parts(docx(p(r("<w:t>a</w:t>"))), {}, [("c1", "comments", "nowhere.xml")]),
            "no word/nowhere.xml",
        ),
        (
            with_parts(
                docx(p(r("<w:t>a</w:t>")) + f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>"),
                {},
                [("h1", "header", "nowhere.xml")],
            ),
            "no word/nowhere.xml",
        ),
    ],
    ids=[
        "note-defined-twice",
        "two-notes-parts",
        "notes-part-missing",
        "two-comments-parts",
        "comments-part-missing",
        "header-part-missing",
    ],
)
def test_a_duplicate_or_missing_part_or_note_is_refused(data: bytes, detail: str) -> None:
    assert _refusal(data) == ("invalid-package", detail)


def test_a_damaged_part_that_is_not_xml_refuses_the_package() -> None:
    # The picture's bytes change after its checksum was written; nothing parses a picture, so
    # only the checksum rule sees it.
    out = io.BytesIO()
    source = io.BytesIO(docx(p(r("<w:t>a</w:t>"))))
    with zipfile.ZipFile(source) as whole, zipfile.ZipFile(out, "w") as target:
        for info in whole.infolist():
            target.writestr(info, whole.read(info))
        target.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"IMAGEDATA" * 4)
    data = out.getvalue()
    at = data.index(b"IMAGEDATA")
    damaged = data[:at] + b"IMAGEDATX" + data[at + 9 :]
    assert _refusal(damaged) == (
        "invalid-package",
        "word/media/image1.png is damaged (bad checksum)",
    )


def test_a_local_entry_no_directory_names_before_the_archive_is_refused() -> None:
    # The data starts with a local file header and ends with the end record, so only the
    # central directory's offsets show the entry in front.
    orphan = io.BytesIO()
    with zipfile.ZipFile(orphan, "w") as package:
        package.writestr("word/document.xml", document_xml(p(r("<w:t>other</w:t>"))))
    local = orphan.getvalue()[: orphan.getvalue().index(b"PK\x01\x02")]
    data = local + docx(p(r("<w:t>a</w:t>")))
    assert _refusal(data) == ("invalid-package", "bytes before the zip archive")


def test_a_part_over_the_part_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "MAX_PART_BYTES", 64)
    code, detail = _refusal(docx(p(r("<w:t>a</w:t>"))))
    assert code == "invalid-package"
    assert detail.endswith("is over 64 bytes")


_OPEN_FIELD = (
    r('<w:fldChar w:fldCharType="begin"/>')
    + r('<w:instrText xml:space="preserve"> DOCPROPERTY Title </w:instrText>')
    + r('<w:fldChar w:fldCharType="separate"/>')
    + r("<w:t>a</w:t>")
)
_STORY_END = ("unbalanced-field", "a field still open where its story ends")


def test_a_field_still_open_where_its_story_ends_is_refused() -> None:
    # Its result has no end: what of the story is the field's, and what Word shows, is unknown.
    assert _refusal(docx(p(_OPEN_FIELD))) == _STORY_END
    in_cell = f"<w:tbl><w:tr><w:tc>{p(_OPEN_FIELD)}</w:tc></w:tr></w:tbl>"
    assert _refusal(docx(in_cell)) == _STORY_END
    note = fnote(1, p(r("<w:footnoteRef/>") + _OPEN_FIELD))
    assert _refusal(docx(p(r("<w:t>x</w:t>") + ref(1)), footnotes=note)) == _STORY_END
    (story,) = read_document(_with_header(p(_OPEN_FIELD))).headers
    assert story.refusal is not None
    assert (story.refusal[0], story.refusal[1]) == _STORY_END
    # A stored result may run on past its paragraph, closed in a later one.
    closed = p(_OPEN_FIELD) + p(r("<w:t>b</w:t>") + r('<w:fldChar w:fldCharType="end"/>'))
    assert _read_text(docx(closed)) == ["a", "b"]


@pytest.mark.parametrize("showing", ["<w:showingPlcHdr/>", '<w:showingPlcHdr w:val="true"/>'])
def test_an_empty_content_control_showing_its_placeholder_is_refused(showing: str) -> None:
    # Word shows a placeholder, which is not in the content, whether or not it names one.
    body = p(r("<w:t>a</w:t>") + f"<w:sdt><w:sdtPr>{showing}</w:sdtPr><w:sdtContent/></w:sdt>")
    assert _refusal(docx(body)) == ("unsupported-element", "an empty content control's placeholder")
    off = body.replace(showing, '<w:showingPlcHdr w:val="0"/>')
    assert _read_text(docx(off)) == ["a"]
    # A placeholder kept in the content is the content's text.
    kept = body.replace("<w:sdtContent/>", f"<w:sdtContent>{r('<w:t>b</w:t>')}</w:sdtContent>")
    assert _read_text(docx(kept)) == ["ab"]


@pytest.mark.parametrize(
    "character", ["\u200b", "\u2060", "\ufeff"], ids=["zero-width-space", "word-joiner", "bom"]
)
def test_a_field_code_with_an_invisible_format_character_is_refused(character: str) -> None:
    for code in (f'HYPERLINK "https://example.org"{character}', f"DOCPROPERTY Title{character}"):
        assert _refusal(docx(p(_field(code, "link")))) == (
            "computed-field",
            "a field code with an invisible format character",
        )
    # Inside quotes it parts no words: a hyperlink's tooltip.
    tooltip = f'HYPERLINK "https://example.org" \\o "see{character}here"'
    assert _read_text(docx(p(_field(tooltip, "link")))) == ["link"]


def test_a_reference_must_name_its_bookmark_in_the_case_written() -> None:
    # Word's bookmark names are case-insensitive, but what it prints for another case is not on
    # record: the name must be the bookmark's as written.
    marked = p(_marked("_Ref1", r("<w:t>below</w:t>")))
    assert _read_text(docx(marked + p(_field("REF _Ref1 \\h", "below")))) == ["below", "below"]
    assert _refusal(docx(marked + p(_field("REF _ref1 \\h", "below")))) == (
        "computed-field",
        "a REF to a bookmark that is not there",
    )
    assert _refusal(docx(marked + p(_field("PAGEREF _ref1 \\h", "4")))) == (
        "computed-field",
        "a PAGEREF to a bookmark it cannot read",
    )
