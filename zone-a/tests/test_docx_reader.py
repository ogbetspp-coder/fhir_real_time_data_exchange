"""The .docx reader: exact text, or a refusal with a reason.

Most cases build a minimal .docx in memory, so each rule is tested in isolation. The last group
reads the four pinned EMA files, which is where the rules were found.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from zone_a.docx.reader import DocxRefusedError, read_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SOURCES = Path(__file__).resolve().parents[2] / "qrd" / "sources"


ROOT_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="{target}"/></Relationships>'
)
DOCUMENT_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="s" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="t" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/theme" Target="theme/theme1.xml"/></Relationships>'
)
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
THEME = (
    f'<a:theme xmlns:a="{A}"><a:themeElements><a:fontScheme>'
    '<a:majorFont><a:latin typeface="Cambria"/></a:majorFont>'
    '<a:minorFont><a:latin typeface="{minor}"/></a:minorFont>'
    "</a:fontScheme></a:themeElements></a:theme>"
)


def document_xml(body: str, doctype: bool = False) -> str:
    return (
        ("<!DOCTYPE x>" if doctype else "")
        + f'<w:document xmlns:w="{W}" '
        + 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        + f"<w:body>{body}</w:body></w:document>"
    )


def docx(
    body: str, styles: str | None = None, doctype: bool = False, minor_font: str | None = None
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        package.writestr("word/document.xml", document_xml(body, doctype))
        if styles is not None:
            package.writestr("word/styles.xml", f'<w:styles xmlns:w="{W}">{styles}</w:styles>')
        if minor_font is not None:
            package.writestr("word/theme/theme1.xml", THEME.replace("{minor}", minor_font))
    return buffer.getvalue()


def text_of(body: str, styles: str | None = None) -> list[str]:
    return [paragraph.text for paragraph in read_docx(docx(body, styles))]


def refusal(body: str, styles: str | None = None) -> str:
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(body, styles))
    return caught.value.code


def r(inner: str, props: str = "") -> str:
    return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{inner}</w:r>"


def p(inner: str, props: str = "") -> str:
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{inner}</w:p>"


# --- characters -------------------------------------------------------------------------


def test_text_is_copied_without_normalisation() -> None:
    body = p(r('<w:t xml:space="preserve">  A\u00a0\u2019b  </w:t>'))
    assert text_of(body) == ["  A\u00a0\u2019b  "]


def test_tabs_breaks_and_hyphens() -> None:
    body = p(
        r("<w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t><w:cr/><w:t>d</w:t>")
        + r('<w:br w:type="page"/><w:t>e</w:t><w:noBreakHyphen/><w:t>f</w:t><w:softHyphen/>')
    )
    assert text_of(body) == ["a\tb\nc\nde\u2011f\u00ad"]


def test_symbol_font_glyphs_become_their_unicode_characters() -> None:
    body = p(
        r("<w:t>(</w:t>")
        + r('<w:sym w:font="Symbol" w:char="F0B3"/>')
        + r("<w:t>1/10) 25</w:t>")
        + r('<w:sym w:font="Symbol" w:char="B0"/>')
        + r("<w:t>C</w:t>")
    )
    assert text_of(body) == ["(\u22651/10) 25\u00b0C"]


def test_private_use_character_in_a_symbol_run_is_mapped() -> None:
    body = p(r("<w:t>\uf0b1</w:t>", '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>'))
    assert text_of(body) == ["\u00b1"]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (p(r('<w:sym w:font="Symbol" w:char="F0E5"/>')), "unmapped-symbol"),
        (p(r('<w:sym w:font="Wingdings" w:char="F0FC"/>')), "unmapped-symbol"),
        (p(r("<w:t>\uf0b3</w:t>")), "private-use-character"),
        (p(r("<w:t>\uf0b3</w:t>", '<w:rFonts w:ascii="Wingdings"/>')), "symbol-font"),
    ],
)
def test_a_symbol_the_table_does_not_hold_is_refused(body: str, code: str) -> None:
    assert refusal(body) == code


# --- fields -----------------------------------------------------------------------------


def test_a_complex_field_keeps_its_result_and_drops_its_instruction() -> None:
    body = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r("<w:instrText> DOCPROPERTY DM_ref \\* MERGEFORMAT </w:instrText>")
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>EMA/1</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
        + r("<w:t>, Version 1</w:t>")
    )
    assert text_of(body) == ["EMA/1, Version 1"]


def test_text_inside_a_field_instruction_is_dropped() -> None:
    body = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r("<w:t>not shown</w:t><w:tab/>")
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>shown</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    assert text_of(body) == ["shown"]


def test_simple_fields_hyperlinks_and_content_controls_are_read_through() -> None:
    body = p(
        '<w:fldSimple w:instr="PAGE">' + r("<w:t>1</w:t>") + "</w:fldSimple>"
        '<w:hyperlink w:anchor="x">' + r("<w:t>2</w:t>") + "</w:hyperlink>"
        "<w:sdt><w:sdtContent>" + r("<w:t>3</w:t>") + "</w:sdtContent></w:sdt>"
    )
    assert text_of(body) == ["123"]


# --- refusals ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        p('<w:ins w:id="1" w:author="a">' + r("<w:t>x</w:t>") + "</w:ins>"),
        p('<w:del w:id="1" w:author="a">' + r("<w:delText>x</w:delText>") + "</w:del>"),
        p('<w:moveFrom w:id="1" w:author="a">' + r("<w:t>x</w:t>") + "</w:moveFrom>"),
        '<w:ins w:id="1" w:author="a">' + p(r("<w:t>x</w:t>")) + "</w:ins>",
    ],
)
def test_tracked_changes_are_refused(body: str) -> None:
    assert refusal(body) == "tracked-change"


def test_hidden_text_is_refused_directly_and_through_styles() -> None:
    assert refusal(p(r("<w:t>x</w:t>", "<w:vanish/>"))) == "hidden-text"
    styles = (
        '<w:style w:styleId="Base"><w:rPr><w:vanish/></w:rPr></w:style>'
        '<w:style w:styleId="Derived"><w:basedOn w:val="Base"/></w:style>'
    )
    assert refusal(p(r("<w:t>x</w:t>"), '<w:pStyle w:val="Derived"/>'), styles) == "hidden-text"
    styles_run = '<w:style w:styleId="H"><w:rPr><w:vanish/></w:rPr></w:style>'
    assert refusal(p(r("<w:t>x</w:t>", '<w:rStyle w:val="H"/>')), styles_run) == "hidden-text"


def test_visible_text_and_hidden_paragraph_marks_are_read() -> None:
    assert text_of(p(r("<w:t>x</w:t>", '<w:vanish w:val="0"/>'))) == ["x"]
    assert text_of(p(r("<w:t>x</w:t>"), "<w:rPr><w:vanish/></w:rPr>")) == ["x"]
    # A hidden run with nothing in it hides nothing.
    assert text_of(p(r("<w:t>x</w:t>") + r("", "<w:vanish/>"))) == ["x"]


@pytest.mark.parametrize(
    "body",
    [
        p(r('<w:footnoteReference w:id="1"/>')),
        p(r("<w:object/>")),
        p(r("<w:pict/>")),
        p(r("<w:drawing><w:txbxContent>" + p(r("<w:t>x</w:t>")) + "</w:txbxContent></w:drawing>")),
        p(r("<mc:AlternateContent/>")),
        p(r("<w:pgNum/>")),
        p("<w:unknownThing/>"),
        '<w:altChunk r:id="x" xmlns:r="urn:x"/>',
    ],
)
def test_elements_that_may_carry_text_elsewhere_are_refused(body: str) -> None:
    assert refusal(body) == "unsupported-element"


PICTURE = (
    "<w:drawing><wp:inline xmlns:wp='urn:wp'><a:graphic xmlns:a='" + A + "'>"
    "<a:graphicData uri='http://schemas.openxmlformats.org/drawingml/2006/picture'/>"
    "</a:graphic></wp:inline></w:drawing>"
)


def test_a_picture_is_an_object_replacement_character_where_it_stands() -> None:
    paragraphs = read_docx(docx(p(r("<w:t>a</w:t>") + r(PICTURE) + r("<w:t>b</w:t>"))))
    assert [paragraph.text for paragraph in paragraphs] == ["a\ufffcb"]
    assert paragraphs[0].has_drawing


@pytest.mark.parametrize(
    "drawing",
    [
        PICTURE.replace("/>", "><a:t>text</a:t></a:graphicData>", 1).replace(
            "</a:graphicData></a:graphicData>", "</a:graphicData>"
        ),
        PICTURE.replace("drawingml/2006/picture", "drawingml/2006/chart"),
    ],
)
def test_a_drawing_with_text_or_that_is_not_a_picture_is_refused(drawing: str) -> None:
    assert refusal(p(r(drawing))) == "unsupported-element"


@pytest.mark.parametrize("data", [b"not a zip", docx(p(r("<w:t>x</w:t>")), doctype=True)])
def test_invalid_packages_are_refused(data: bytes) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(data)
    assert caught.value.code == "invalid-package"


def test_a_package_without_a_document_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr("word/styles.xml", "<x/>")
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(buffer.getvalue())
    assert caught.value.code == "invalid-package"


def test_a_field_nested_in_an_instruction_does_not_leak_its_result() -> None:
    body = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r("<w:instrText>IF </w:instrText>")
        + r('<w:fldChar w:fldCharType="begin"/>')
        + r("<w:instrText>MERGEFIELD G</w:instrText>")
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>F</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
        + r('<w:instrText> = "F" "she" "he"</w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>she</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    assert text_of(body) == ["she"]


def test_a_paragraph_that_ends_inside_a_field_instruction_is_refused() -> None:
    body = p(r("<w:t xml:space='preserve'>Keep </w:t>") + r('<w:fldChar w:fldCharType="begin"/>'))
    body += p(r("<w:t>dropped</w:t>"))
    assert refusal(body) == "unbalanced-field"


# --- marks ------------------------------------------------------------------------------


def test_superscript_and_subscript_are_marked_not_lost() -> None:
    body = p(
        r("<w:t xml:space='preserve'>x 10</w:t>")
        + r("<w:t>9</w:t>", '<w:vertAlign w:val="superscript"/>')
        + r("<w:t>/l CO</w:t>")
        + r("<w:t>2</w:t>", '<w:vertAlign w:val="subscript"/>')
    )
    paragraph = read_docx(docx(body))[0]
    assert paragraph.text == "x 109/l CO2"
    assert [(m.start, m.end, m.kind) for m in paragraph.marks] == [
        (4, 5, "superscript"),
        (10, 11, "subscript"),
    ]


def test_capitals_strike_highlight_and_shading_are_marked() -> None:
    body = p(
        r("<w:t>up</w:t>", "<w:caps/>")
        + r("<w:t>gone</w:t>", "<w:strike/>")
        + r("<w:t>grey</w:t>", '<w:highlight w:val="lightGray"/>')
        + r("<w:t>shade</w:t>", '<w:shd w:val="clear" w:fill="D9D9D9"/>')
        + r("<w:t>up</w:t>", '<w:position w:val="6"/>')
    )
    marks = read_docx(docx(body))[0].marks
    assert [(m.start, m.end, m.kind) for m in marks] == [
        (0, 2, "caps"),
        (2, 6, "strike"),
        (6, 10, "highlight"),
        (10, 15, "shading"),
        (15, 17, "position"),
    ]


def test_a_hidden_paragraph_mark_is_reported() -> None:
    paragraph = read_docx(docx(p(r("<w:t>x</w:t>"), "<w:rPr><w:vanish/></w:rPr>")))[0]
    assert paragraph.mark_hidden


# --- fonts ------------------------------------------------------------------------------


def test_every_character_of_a_symbol_run_is_mapped() -> None:
    body = p(
        r("<w:t xml:space='preserve'>5 </w:t>") + r("<w:t>m</w:t>", SYMBOL) + r("<w:t>g</w:t>")
    )
    assert text_of(body) == ["5 \u03bcg"]


SYMBOL = '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>'


def test_a_symbol_font_set_by_style_or_theme_is_applied() -> None:
    styles = f'<w:style w:type="character" w:styleId="Sym"><w:rPr>{SYMBOL}</w:rPr></w:style>'
    assert text_of(p(r("<w:t>b</w:t>", '<w:rStyle w:val="Sym"/>')), styles) == ["\u03b2"]
    theme_run = r("<w:t>b</w:t>", '<w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/>')
    assert read_docx(docx(p(theme_run), minor_font="Symbol"))[0].text == "\u03b2"
    assert read_docx(docx(p(theme_run), minor_font="Calibri"))[0].text == "b"


@pytest.mark.parametrize(
    ("run", "code"),
    [
        (r("<w:t>q</w:t>", SYMBOL), "unmapped-symbol"),
        (
            r("<w:t>\u00fc</w:t>", '<w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/>'),
            "symbol-font",
        ),
        (r("<w:t>b</w:t>", '<w:rFonts w:eastAsia="Symbol"/>'), "symbol-font"),
        (r('<w:sym w:font="Symbol" w:char="ZZ"/>'), "unmapped-symbol"),
        (r("<w:t>\U000f00b3</w:t>"), "private-use-character"),
    ],
)
def test_fonts_the_reader_cannot_place_are_refused(run: str, code: str) -> None:
    assert refusal(p(run)) == code


def test_a_theme_font_without_a_theme_is_refused() -> None:
    run = r("<w:t>b</w:t>", '<w:rFonts w:asciiTheme="minorHAnsi"/>')
    assert refusal(p(run)) == "symbol-font"


def test_unpreserved_whitespace_is_refused() -> None:
    assert refusal(p(r("<w:t> x</w:t>"))) == "unpreserved-whitespace"
    assert text_of(p(r("<w:t>a\u00a0b</w:t>"))) == ["a\u00a0b"]


# --- hidden text through defaults ---------------------------------------------------------


def test_hidden_text_through_the_default_paragraph_style_or_a_table_style_is_refused() -> None:
    default = (
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        "<w:rPr><w:vanish/></w:rPr></w:style>"
    )
    assert refusal(p(r("<w:t>x</w:t>")), default) == "hidden-text"
    table_style = '<w:style w:type="table" w:styleId="T"><w:rPr><w:vanish/></w:rPr></w:style>'
    table = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr><w:tr><w:tc>'
        + p(r("<w:t>x</w:t>"))
        + "</w:tc></w:tr></w:tbl>"
    )
    assert refusal(table, table_style) == "hidden-text"
    defaults = (
        "<w:docDefaults><w:rPrDefault><w:rPr><w:vanish/></w:rPr></w:rPrDefault></w:docDefaults>"
    )
    assert refusal(p(r("<w:t>x</w:t>")), defaults) == "hidden-text"


# --- containers ---------------------------------------------------------------------------


def test_wrapped_paragraphs_rows_and_cells_are_read_not_dropped() -> None:
    cell = "<w:tc>" + p(r("<w:t>{}</w:t>")) + "</w:tc>"
    body = (
        "<w:customXml w:element='x'>" + p(r("<w:t>block</w:t>")) + "</w:customXml>"
        "<w:tbl>"
        "<w:sdt><w:sdtContent><w:tr>" + cell.format("row-sdt") + "</w:tr></w:sdtContent></w:sdt>"
        "<w:customXml w:element='y'><w:tr>" + cell.format("row-xml") + "</w:tr></w:customXml>"
        "<w:tr><w:sdt><w:sdtContent>" + cell.format("cell-sdt") + "</w:sdtContent></w:sdt></w:tr>"
        "</w:tbl>"
    )
    paragraphs = read_docx(docx(body))
    assert [(x.text, x.table) for x in paragraphs] == [
        ("block", None),
        ("row-sdt", (0, 0, 0)),
        ("row-xml", (0, 1, 0)),
        ("cell-sdt", (0, 2, 0)),
    ]


@pytest.mark.parametrize(
    "body",
    ["<w:unknownBlock/>", "<w:tbl><w:unknownRow/></w:tbl>", "<w:tbl><w:tr><w:x/></w:tr></w:tbl>"],
)
def test_unknown_containers_are_refused(body: str) -> None:
    assert refusal(body) == "unsupported-element"


@pytest.mark.parametrize(
    "body",
    [
        p(r("<w:t>x</w:t>"), '<w:rPr><w:del w:id="1" w:author="a"/></w:rPr>'),
        p(r("<w:t>x</w:t>", '<w:rPrChange w:id="1" w:author="a"><w:rPr/></w:rPrChange>')),
        p(r("<w:t>x</w:t>"), '<w:pPrChange w:id="1" w:author="a"><w:pPr/></w:pPrChange>'),
    ],
)
def test_formatting_revisions_and_deleted_paragraph_marks_are_refused(body: str) -> None:
    assert refusal(body) == "tracked-change"


# --- the package --------------------------------------------------------------------------


def test_the_main_part_is_found_through_the_relationships() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/main.xml"))
        package.writestr("word/document.xml", document_xml(p(r("<w:t>DECOY</w:t>"))))
        package.writestr("word/main.xml", document_xml(p(r("<w:t>REAL</w:t>"))))
    assert [x.text for x in read_docx(buffer.getvalue())] == ["REAL"]


def test_duplicate_part_names_and_non_utf8_parts_are_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr("word/document.xml", document_xml(p(r("<w:t>FIRST</w:t>"))))
        with pytest.warns(UserWarning, match="Duplicate name"):
            package.writestr("word/document.xml", document_xml(p(r("<w:t>SECOND</w:t>"))))
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(buffer.getvalue())
    assert caught.value.code == "invalid-package"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr("word/document.xml", document_xml(p(r("<w:t>x</w:t>"))).encode("utf-16"))
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(buffer.getvalue())
    assert caught.value.code == "invalid-package"


# --- structure --------------------------------------------------------------------------


def test_numbering_inherited_from_a_style_is_reported() -> None:
    styles = (
        '<w:style w:type="paragraph" w:styleId="List"><w:pPr><w:numPr><w:ilvl w:val="0"/>'
        '<w:numId w:val="7"/></w:numPr></w:pPr></w:style>'
    )
    paragraph = read_docx(docx(p(r("<w:t>x</w:t>"), '<w:pStyle w:val="List"/>'), styles))[0]
    assert paragraph.numbering is not None
    assert paragraph.numbering.num_id == 7


def test_tables_carry_their_position_and_numbering_is_metadata() -> None:
    cell = "<w:tc>{}</w:tc>"
    body = (
        p(r("<w:t>before</w:t>"), '<w:numPr><w:ilvl w:val="1"/><w:numId w:val="3"/></w:numPr>')
        + "<w:tbl><w:tr>"
        + cell.format(p(r("<w:t>a</w:t>")))
        + cell.format(p(r("<w:t>b</w:t>")))
        + "</w:tr><w:tr>"
        + cell.format(p(r("<w:t>c</w:t>")))
        + "</w:tr></w:tbl>"
    )
    paragraphs = read_docx(docx(body))
    assert [(x.text, x.table) for x in paragraphs] == [
        ("before", None),
        ("a", (0, 0, 0)),
        ("b", (0, 0, 1)),
        ("c", (0, 1, 0)),
    ]
    numbering = paragraphs[0].numbering
    assert numbering is not None
    assert (numbering.num_id, numbering.level) == (3, 1)


# --- the pinned EMA files -----------------------------------------------------------------


def _read(name: str) -> list[str]:
    return [paragraph.text for paragraph in read_docx((SOURCES / name).read_bytes())]


def test_every_pinned_source_reads_without_refusal() -> None:
    for path in sorted(SOURCES.glob("*.docx")):
        assert read_docx(path.read_bytes()), path.name


def test_appendix_ii_keeps_the_greater_than_or_equal_signs() -> None:
    texts = _read(
        "qrd-appendix-ii-medical-dictionary-regulatory-activities-terminology-be-used-section-48"
        "-undesirable-effects-summary-product-characteristics_en.docx"
    )
    assert "<Very common (\u2265\u00a01/10)>" in texts
    assert sum(text.count("\u2265") for text in texts) == 4


def test_appendix_iii_keeps_the_degree_signs() -> None:
    texts = _read(
        "qrd-appendix-iii-quality-review-documents-templates-human-medicinal-products_en.docx"
    )
    assert "<Store below <25\u00a0\u00b0C> <30\u00a0\u00b0C>.>" in texts
    assert sum(text.count("\u00b0") for text in texts) == 21


def test_the_template_keeps_its_symbol_font_braces_and_its_own_drift() -> None:
    data = (SOURCES / "qrd-product-information-template-version-104_en.docx").read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        xml = package.read("word/document.xml").decode("utf-8")
    # Four opening and four closing braces are stored as Symbol-font glyphs, not as text.
    assert xml.count('w:char="F07B"') == 4
    assert xml.count('w:char="F07D"') == 4
    texts = [paragraph.text for paragraph in read_docx(data)]
    # They are the Czech local representative's placeholders; a text-only reader shows "Nazev".
    assert "{N\u00e1zev}" in texts
    assert "CZ {m\u011bsto}>" in texts
    assert "Tel: +{telefonn\u00ed \u010d\u00edslo}" in texts
    # EMA's own drift: the Polish representative's "<{Adres:" never closes its brace.
    assert "<{Adres:" in texts
    # "5.1" is followed by a space and then a tab in EMA's file; the reader keeps both.
    assert "5.1 \tPharmacodynamic properties" in texts
    # The black triangle of the additional-monitoring statement is a picture.
    triangle = [text for text in texts if "\ufffc" in text]
    assert len(triangle) == 2
    assert triangle[0].startswith("<\ufffcThis medicinal product is subject to additional")
    assert triangle[1].startswith("<\ufffcThis medicine is subject to additional monitoring")
