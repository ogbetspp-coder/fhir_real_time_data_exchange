"""The .docx reader: exact text, or a refusal with a reason.

Most cases build a minimal .docx in memory, so each rule is tested in isolation. The last group
reads the four pinned EMA files in corpus/ema-qrd, which is where the rules were found.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from label_docx.reader import DocxRefusedError, Numbering, read_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SOURCES = Path(__file__).resolve().parents[1] / "corpus" / "ema-qrd"


ROOT_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="{target}"/></Relationships>'
)
RELATIONSHIP = (
    '<Relationship Id="{kind}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/{kind}" Target="{target}"/>'
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
    body: str,
    styles: str | None = None,
    doctype: bool = False,
    minor_font: str | None = None,
    fonts: str | None = None,
    numbering: str | None = None,
) -> bytes:
    parts: dict[str, tuple[str, str]] = {}
    if numbering is not None:
        parts["numbering"] = (
            "numbering.xml",
            f'<w:numbering xmlns:w="{W}">{numbering}</w:numbering>',
        )
    if styles is not None:
        parts["styles"] = ("styles.xml", f'<w:styles xmlns:w="{W}">{styles}</w:styles>')
    if minor_font is not None:
        parts["theme"] = ("theme/theme1.xml", THEME.replace("{minor}", minor_font))
    if fonts is not None:
        parts["fontTable"] = ("fontTable.xml", f'<w:fonts xmlns:w="{W}">{fonts}</w:fonts>')
    rels = "".join(
        RELATIONSHIP.format(kind=kind, target=target) for kind, (target, _) in parts.items()
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + rels
            + "</Relationships>",
        )
        package.writestr("word/document.xml", document_xml(body, doctype))
        for target, content in parts.values():
            package.writestr("word/" + target, content)
    return buffer.getvalue()


def text_of(body: str, styles: str | None = None) -> list[str]:
    return [paragraph.text for paragraph in read_docx(docx(body, styles))]


def refusal(body: str, styles: str | None = None, numbering: str | None = None) -> str:
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(body, styles, numbering=numbering))
    return caught.value.code


def lvl(
    level: int,
    fmt: str = "decimal",
    text: str | None = None,
    extra: str = "",
    start: int | None = 1,
) -> str:
    shown = f"%{level + 1}." if text is None else text
    first = "" if start is None else f'<w:start w:val="{start}"/>'
    return (
        f'<w:lvl w:ilvl="{level}">{first}<w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{shown}"/>{extra}</w:lvl>'
    )


def abstract(key: int, *levels: str) -> str:
    return f'<w:abstractNum w:abstractNumId="{key}">{"".join(levels)}</w:abstractNum>'


def num(key: int, abstract_id: int, overrides: str = "") -> str:
    return f'<w:num w:numId="{key}"><w:abstractNumId w:val="{abstract_id}"/>{overrides}</w:num>'


# Lists 3, 4 and 7 share one definition: "1.", then "1)", then "1]" a level down.
NUMBERING = (
    abstract(0, lvl(0), lvl(1, text="%2)"), lvl(2, text="%3]")) + num(3, 0) + num(4, 0) + num(7, 0)
)


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
        + r("<w:instrText>REF </w:instrText><w:t>not_shown</w:t><w:tab/>")
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>shown</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    assert text_of(body) == ["shown"]


def test_simple_fields_hyperlinks_and_content_controls_are_read_through() -> None:
    body = p(
        '<w:fldSimple w:instr=" REF x ">' + r("<w:t>1</w:t>") + "</w:fldSimple>"
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
    styles_run = '<w:style w:type="character" w:styleId="H"><w:rPr><w:vanish/></w:rPr></w:style>'
    assert refusal(p(r("<w:t>x</w:t>", '<w:rStyle w:val="H"/>')), styles_run) == "hidden-text"
    # A paragraph style named as a run style, or the reverse, is refused, not guessed at.
    assert (
        refusal(p(r("<w:t>x</w:t>", '<w:rStyle w:val="Base"/>')), styles) == "unsupported-element"
    )
    assert (
        refusal(p(r("<w:t>x</w:t>"), '<w:pStyle w:val="H"/>'), styles_run) == "unsupported-element"
    )


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
        + r('<w:instrText xml:space="preserve">HYPERLINK "</w:instrText>')
        + r('<w:fldChar w:fldCharType="begin"/>')
        + r("<w:instrText>MERGEFIELD G</w:instrText>")
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>F</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
        + r('<w:instrText>"</w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>she</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    assert text_of(body) == ["she"]
    # The same shape with IF, whose result Word recomputes, is refused.
    assert refusal(body.replace("HYPERLINK", "IF")) == "computed-field"


@pytest.mark.parametrize("code", ["PAGE", ' DATE \\@ "d MMMM yyyy"', "SEQ Table", "NUMPAGES", ""])
def test_fields_word_recomputes_are_refused(code: str) -> None:
    complex_field = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve">{code}</w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>1</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    assert refusal(complex_field) == "computed-field"
    simple = p(
        f'<w:fldSimple w:instr="{code.replace(chr(34), "&quot;")}">'
        + r("<w:t>1</w:t>")
        + "</w:fldSimple>"
    )
    assert refusal(simple) == "computed-field"


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
        (6, 10, "highlight-lightGray"),
        (10, 15, "shading-D9D9D9"),
        (15, 17, "position"),
    ]


def test_underline_is_marked_and_none_is_not() -> None:
    # An underlined "<" is how "≤" is often typed in Word.
    body = p(
        r("<w:t>&lt;</w:t>", '<w:u w:val="single"/>')
        + r("<w:t>x</w:t>", '<w:u w:val="none"/>')
        + r("<w:t>=</w:t>", '<w:u w:val="double"/>')
    )
    marks = read_docx(docx(body))[0].marks
    assert [(m.start, m.end, m.kind) for m in marks] == [(0, 1, "underline"), (2, 3, "underline")]


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
    body = p(r("<w:t>x</w:t>"), '<w:pStyle w:val="List"/>')
    paragraph = read_docx(docx(body, styles, numbering=NUMBERING))[0]
    assert paragraph.numbering == Numbering(7, 0, "1.", "tab")


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
    paragraphs = read_docx(docx(body, numbering=NUMBERING))
    assert [(x.text, x.table) for x in paragraphs] == [
        ("before", None),
        ("a", (0, 0, 0)),
        ("b", (0, 0, 1)),
        ("c", (0, 1, 0)),
    ]
    assert paragraphs[0].numbering == Numbering(3, 1, "1)", "tab")


# --- second review: styles Word falls back to ----------------------------------------------


def test_an_unknown_paragraph_style_falls_back_to_the_default_one() -> None:
    default = (
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        "<w:rPr><w:vanish/></w:rPr></w:style>"
    )
    assert refusal(p(r("<w:t>x</w:t>"), '<w:pStyle w:val="Missing"/>'), default) == "hidden-text"


def test_the_default_character_and_table_styles_apply() -> None:
    character = (
        '<w:style w:type="character" w:default="1" w:styleId="Font">'
        "<w:rPr><w:vanish/></w:rPr></w:style>"
    )
    assert refusal(p(r("<w:t>x</w:t>")), character) == "hidden-text"
    table = (
        '<w:style w:type="table" w:default="1" w:styleId="Grid">'
        "<w:rPr><w:vanish/></w:rPr></w:style>"
    )
    body = "<w:tbl><w:tr><w:tc>" + p(r("<w:t>x</w:t>")) + "</w:tc></w:tr></w:tbl>"
    assert refusal(body, table) == "hidden-text"


def test_conditional_table_formatting_in_force_is_refused_and_unused_is_not() -> None:
    style = (
        '<w:style w:type="table" w:styleId="Banded"><w:tblStylePr w:type="firstRow">'
        '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:tblStylePr></w:style>'
    )
    cell = "<w:tr><w:tc>" + p(r("<w:t>10</w:t>")) + "</w:tc></w:tr>"
    used = '<w:tbl><w:tblPr><w:tblStyle w:val="Banded"/></w:tblPr>' + cell + "</w:tbl>"
    assert refusal(used, style) == "unsupported-element"
    as_default = style.replace('w:styleId="Banded"', 'w:default="1" w:styleId="Banded"')
    assert refusal("<w:tbl>" + cell + "</w:tbl>", as_default) == "unsupported-element"
    # The EMA template defines such a style and never applies it.
    assert text_of("<w:tbl>" + cell + "</w:tbl>", style) == ["10"]


# --- second review: Symbol in some font slots only -------------------------------------------


@pytest.mark.parametrize(
    "fonts",
    [
        '<w:rFonts w:ascii="Symbol"/>',
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Times New Roman"/>',
        '<w:rFonts w:hAnsi="Symbol"/>',
        '<w:rFonts w:cs="Symbol"/>',
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="eastAsia"/>',
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/><w:cs/>',
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/><w:rtl/>',
    ],
)
def test_symbol_in_only_some_font_slots_is_refused(fonts: str) -> None:
    assert refusal(p(r("<w:t>b</w:t>", fonts))) == "symbol-font"


def test_the_complex_script_theme_attribute_is_read() -> None:
    run = r("<w:t>b</w:t>", '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cstheme="minorHAnsi"/>')
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(p(run), minor_font="Symbol"))
    assert caught.value.code == "symbol-font"


def test_a_theme_font_the_theme_does_not_define_is_refused() -> None:
    run = r("<w:t>b</w:t>", '<w:rFonts w:asciiTheme="minorFoo" w:hAnsiTheme="minorFoo"/>')
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(p(run), minor_font="Calibri"))
    assert caught.value.code == "symbol-font"


def test_a_font_the_font_table_declares_symbol_encoded_is_refused() -> None:
    fonts = '<w:font w:name="Monotype Sorts"><w:charset w:val="02"/></w:font>'
    run = r("<w:t>n</w:t>", '<w:rFonts w:ascii="Monotype Sorts" w:hAnsi="Monotype Sorts"/>')
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(p(run), fonts=fonts))
    assert caught.value.code == "symbol-font"
    plain = '<w:font w:name="Monotype Sorts"><w:charset w:val="00"/></w:font>'
    assert read_docx(docx(p(run), fonts=plain))[0].text == "n"


# --- second review: fields with no stored result ---------------------------------------------


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (
            p(
                r('<w:fldChar w:fldCharType="begin"/>')
                + r("<w:instrText>SYMBOL 179 \\f Symbol</w:instrText>")
                + r('<w:fldChar w:fldCharType="end"/>')
            ),
            "field-without-result",
        ),
        (
            p(
                r(
                    '<w:fldChar w:fldCharType="begin"><w:ffData><w:checkBox><w:default w:val="1"/>'
                    "</w:checkBox></w:ffData></w:fldChar>"
                )
                + r("<w:instrText>FORMCHECKBOX</w:instrText>")
                + r('<w:fldChar w:fldCharType="end"/>')
            ),
            "unsupported-element",
        ),
        (
            p(
                r('<w:fldChar w:fldCharType="begin" w:dirty="true"/>')
                + r("<w:instrText>DATE</w:instrText>")
                + r('<w:fldChar w:fldCharType="separate"/>')
                + r("<w:t>1 January</w:t>")
                + r('<w:fldChar w:fldCharType="end"/>')
            ),
            "stale-field",
        ),
        (p('<w:fldSimple w:instr="REF x"/>'), "field-without-result"),
        (
            p('<w:fldSimple w:instr="REF x" w:dirty="1">' + r("<w:t>1</w:t>") + "</w:fldSimple>"),
            "stale-field",
        ),
    ],
)
def test_fields_whose_display_is_computed_are_refused(body: str, code: str) -> None:
    assert refusal(body) == code


# --- second review: text the reader cannot vouch for -----------------------------------------


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (p(r("<w:t>a\ufffcb</w:t>")), "reserved-character"),
        (p(r('<w:t xml:space="preserve">a\tb</w:t>')), "unpreserved-whitespace"),
        (p(r('<w:t xml:space="preserve">a\nb</w:t>')), "unpreserved-whitespace"),
        (p(r('<w:sym w:font="Symbol" w:char="F0_B"/>')), "unmapped-symbol"),
        (
            p(
                '<w:sdt><w:sdtPr><w:dataBinding w:xpath="/a" w:storeItemID="{0}"/></w:sdtPr>'
                "<w:sdtContent>" + r("<w:t>cached</w:t>") + "</w:sdtContent></w:sdt>"
            ),
            "unsupported-element",
        ),
        (
            "<w:tbl><w:tr><w:tc>"
            + p(r("<w:t>top</w:t>"))
            + "</w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr>"
            + p(r("<w:t>hidden by the merge</w:t>"))
            + "</w:tc></w:tr></w:tbl>",
            "unsupported-element",
        ),
    ],
)
def test_text_the_reader_cannot_vouch_for_is_refused(body: str, code: str) -> None:
    assert refusal(body) == code


# --- second review: marks -------------------------------------------------------------------


def _kinds(body: str, styles: str | None = None) -> list[tuple[int, int, str]]:
    return [(m.start, m.end, m.kind) for m in read_docx(docx(body, styles))[0].marks]


def test_highlight_carries_its_colour_and_pattern_shading_is_marked() -> None:
    body = p(
        r("<w:t>y</w:t>", '<w:highlight w:val="yellow"/>')
        + r("<w:t>s</w:t>", '<w:shd w:val="pct50" w:color="000000" w:fill="auto"/>')
        + r("<w:t>n</w:t>", '<w:shd w:val="clear" w:fill="auto"/>')
    )
    assert _kinds(body) == [(0, 1, "highlight-yellow"), (1, 2, "shading-pct50-000000-AUTO")]


def test_paragraph_shading_and_right_to_left_cover_the_paragraph() -> None:
    body = p(r("<w:t>abc</w:t>"), '<w:shd w:val="clear" w:fill="D9D9D9"/><w:bidi/>')
    assert _kinds(body) == [(0, 3, "rtl"), (0, 3, "shading-D9D9D9")]
    styles = (
        '<w:style w:type="paragraph" w:styleId="Grey"><w:pPr>'
        '<w:shd w:val="clear" w:fill="D9D9D9"/></w:pPr></w:style>'
    )
    assert _kinds(p(r("<w:t>ab</w:t>"), '<w:pStyle w:val="Grey"/>'), styles) == [
        (0, 2, "shading-D9D9D9")
    ]
    assert _kinds(p('<w:bdo w:val="rtl">' + r("<w:t>ab</w:t>") + "</w:bdo>")) == [(0, 2, "rtl")]
    assert _kinds(p(r("<w:t>ab</w:t>", "<w:rtl/>"))) == [(0, 2, "rtl")]


@pytest.mark.parametrize(
    "props", ['<w:color w:val="FFFFFF"/>', '<w:sz w:val="2"/>', '<w:w w:val="10"/>']
)
def test_text_that_is_hard_to_see_is_marked_faint(props: str) -> None:
    assert _kinds(p(r("<w:t>ab</w:t>", props))) == [(0, 2, "faint")]


def test_a_hidden_paragraph_mark_through_a_style_or_spec_vanish_is_reported() -> None:
    styles = '<w:style w:type="paragraph" w:styleId="RunOn"><w:rPr><w:vanish/></w:rPr></w:style>'
    styled = p(r("<w:t>a</w:t>"), '<w:pStyle w:val="RunOn"/>')
    # The run inherits the style's vanish too; an empty run shows the mark alone.
    assert read_docx(docx(p("", '<w:pStyle w:val="RunOn"/>'), styles))[0].mark_hidden
    assert refusal(styled, styles) == "hidden-text"
    spec = p(r("<w:t>a</w:t>"), "<w:rPr><w:specVanish/></w:rPr>")
    assert read_docx(docx(spec))[0].mark_hidden


def test_numbering_takes_each_of_list_and_level_from_the_nearest_level_setting_it() -> None:
    styles = (
        '<w:style w:type="paragraph" w:styleId="List"><w:pPr><w:numPr><w:ilvl w:val="2"/>'
        '<w:numId w:val="7"/></w:numPr></w:pPr></w:style>'
    )
    body = p(r("<w:t>x</w:t>"), '<w:pStyle w:val="List"/><w:numPr><w:ilvl w:val="1"/></w:numPr>')
    numbering = read_docx(docx(body, styles, numbering=NUMBERING))[0].numbering
    assert numbering == Numbering(7, 1, "1)", "tab")


def test_part_names_that_differ_only_in_case_are_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr("word/document.xml", document_xml(p(r("<w:t>x</w:t>"))))
        package.writestr("Word/Document.xml", document_xml(p(r("<w:t>y</w:t>"))))
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(buffer.getvalue())
    assert caught.value.code == "invalid-package"


# --- third review -------------------------------------------------------------------------


def test_the_default_table_style_applies_only_inside_a_table() -> None:
    styles = (
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="table" w:default="1" w:styleId="TN"><w:rPr>'
        '<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr></w:style>'
    )
    assert text_of(p(r("<w:t>a</w:t>")), styles) == ["\u03b1"]
    table = "<w:tbl><w:tr><w:tc>" + p(r("<w:t>a</w:t>")) + "</w:tc></w:tr></w:tbl>"
    assert text_of(table, styles) == ["a"]


def test_the_last_of_two_default_styles_is_used() -> None:
    styles = (
        '<w:style w:type="paragraph" w:default="1" w:styleId="A"/>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="B"><w:rPr><w:vanish/></w:rPr>'
        "</w:style>"
    )
    assert refusal(p(r("<w:t>abc</w:t>")), styles) == "hidden-text"


@pytest.mark.parametrize("block", [True, False])
def test_a_content_control_bound_in_the_word_2013_namespace_is_refused(block: bool) -> None:
    control = (
        '<w:sdt xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"><w:sdtPr>'
        '<w15:dataBinding w:xpath="/a" w:storeItemID="{1}"/></w:sdtPr><w:sdtContent>'
        + (p(r("<w:t>cached</w:t>")) if block else r("<w:t>cached</w:t>"))
        + "</w:sdtContent></w:sdt>"
    )
    assert refusal(control if block else p(control)) == "unsupported-element"


@pytest.mark.parametrize(
    "props",
    [
        '<w:color w:val="000000" w:themeColor="background1"/>',
        '<w:sz w:val="1pt"/>',
        '<w:sz w:val="0.5mm"/>',
        '<w:w w:val="10%"/>',
        '<w:sz w:val="big"/>',
    ],
)
def test_faint_text_is_found_in_every_form_word_writes(props: str) -> None:
    assert _kinds(p(r("<w:t>ab</w:t>", props))) == [(0, 2, "faint")]


def test_normal_sizes_and_scales_are_not_faint() -> None:
    for props in ('<w:sz w:val="22"/>', '<w:sz w:val="11pt"/>', '<w:w w:val="90%"/>'):
        assert _kinds(p(r("<w:t>ab</w:t>", props))) == []


def test_paragraph_defaults_and_table_style_paragraph_properties_are_marked() -> None:
    defaults = (
        '<w:docDefaults><w:pPrDefault><w:pPr><w:shd w:val="clear" w:fill="D9D9D9"/><w:bidi/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"
    )
    assert _kinds(p(r("<w:t>ab</w:t>")), defaults) == [(0, 2, "rtl"), (0, 2, "shading-D9D9D9")]
    table_style = (
        '<w:style w:type="table" w:styleId="T"><w:pPr><w:shd w:val="clear" w:fill="FFFF00"/>'
        "</w:pPr></w:style>"
    )
    table = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr><w:tr><w:tc>'
        + p(r("<w:t>ab</w:t>"))
        + "</w:tc></w:tr></w:tbl>"
    )
    assert _kinds(table, table_style) == [(0, 2, "shading-FFFF00")]


def test_malformed_input_is_refused_not_raised() -> None:
    good = docx(p(r("<w:t>x</w:t>")))
    at = good.index(b"<w:t>x")
    corrupt = good[:at] + b"<w:t>y" + good[at + 6 :]
    for data in (corrupt,):
        with pytest.raises(DocxRefusedError) as caught:
            read_docx(data)
        assert caught.value.code == "invalid-package"
    number = p(r("<w:t>x</w:t>"), '<w:numPr><w:numId w:val="x"/></w:numPr>')
    assert refusal(number) == "invalid-package"


def test_a_part_that_declares_another_encoding_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="windows-1252"?>'
            + document_xml(p(r("<w:t>caf\u00e9</w:t>"))),
        )
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(buffer.getvalue())
    assert caught.value.code == "invalid-package"


# --- fourth review ------------------------------------------------------------------------


def test_a_damaged_zip_directory_is_refused_not_raised() -> None:
    good = docx(p(r("<w:t>hello</w:t>")))
    directory = good.rindex(b"PK\x01\x02")
    version = bytearray(good)
    version[directory + 6] = 0xFF
    offset = bytearray(good)
    end = good.rindex(b"PK\x05\x06")
    offset[end + 16 : end + 20] = (directory + 0x100000).to_bytes(4, "little")
    for data in (bytes(version), bytes(offset)):
        with pytest.raises(DocxRefusedError) as caught:
            read_docx(data)
        assert caught.value.code == "invalid-package"


def test_a_field_nested_before_the_code_hides_the_code_and_is_refused() -> None:
    begin = r('<w:fldChar w:fldCharType="begin"/>')
    separate = r('<w:fldChar w:fldCharType="separate"/>')
    end = r('<w:fldChar w:fldCharType="end"/>')
    body = p(
        begin
        + begin
        + r('<w:instrText xml:space="preserve"> DOCPROPERTY Kind </w:instrText>')
        + separate
        + r("<w:t>DATE</w:t>")
        + end
        + r('<w:instrText xml:space="preserve"> REF bm </w:instrText>')
        + separate
        + r("<w:t>stale</w:t>")
        + end
    )
    assert refusal(body) == "computed-field"


def test_field_code_outside_an_instruction_is_refused() -> None:
    assert refusal(p(r("<w:instrText>PAGE</w:instrText><w:t>1</w:t>"))) == "unbalanced-field"


def test_a_style_defined_twice_is_refused() -> None:
    styles = (
        '<w:style w:type="paragraph" w:styleId="S"><w:rPr><w:vanish/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="S"/>'
    )
    assert refusal(p(r("<w:t>x</w:t>"), '<w:pStyle w:val="S"/>'), styles) == "invalid-package"


def test_numbering_from_the_paragraph_defaults_or_a_table_style_is_reported() -> None:
    numbered = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/></w:numPr></w:pPr>'
    defaults = f"<w:docDefaults><w:pPrDefault>{numbered}</w:pPrDefault></w:docDefaults>"
    paragraph = read_docx(docx(p(r("<w:t>x</w:t>")), defaults, numbering=NUMBERING))[0]
    assert paragraph.numbering == Numbering(4, 0, "1.", "tab")
    table_style = f'<w:style w:type="table" w:styleId="T">{numbered}</w:style>'
    table = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr><w:tr><w:tc>'
        + p(r("<w:t>x</w:t>"))
        + "</w:tc></w:tr></w:tbl>"
    )
    labelled = read_docx(docx(table, table_style, numbering=NUMBERING))[0]
    assert labelled.numbering == Numbering(4, 0, "1.", "tab")


def test_fields_in_a_document_that_updates_them_on_open_are_refused() -> None:
    field = p('<w:fldSimple w:instr=" REF x ">' + r("<w:t>1</w:t>") + "</w:fldSimple>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("_rels/.rels", ROOT_RELS.format(target="word/document.xml"))
        package.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + RELATIONSHIP.format(kind="settings", target="settings.xml")
            + "</Relationships>",
        )
        package.writestr("word/document.xml", document_xml(field))
        package.writestr(
            "word/settings.xml", f'<w:settings xmlns:w="{W}"><w:updateFields/></w:settings>'
        )
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(buffer.getvalue())
    assert caught.value.code == "computed-field"


def test_tiny_complex_script_text_is_faint() -> None:
    assert _kinds(p(r("<w:t>ab</w:t>", '<w:sz w:val="22"/><w:szCs w:val="2"/>'))) == [
        (0, 2, "faint")
    ]


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


# --- accounting: no text passed over (docx-reader/1.2.0) -----------------------------------
#
# Every body below was read by docx-reader/1.1.0 without a word about the text it lost.


@pytest.mark.parametrize(
    "body",
    [
        # An element inside w:t: the text after it was dropped.
        p(r("<w:t>kept<w:x/>lost</w:t>")),
        p(
            r('<w:fldChar w:fldCharType="begin"/>')
            + r("<w:instrText>REF<w:x/> lost</w:instrText>")
            + r('<w:fldChar w:fldCharType="separate"/>')
            + r("<w:t>kept</w:t>")
            + r('<w:fldChar w:fldCharType="end"/>')
        ),
        # Character data in a run, a paragraph, the body, a row and a property.
        p(r("<w:t>kept</w:t>lost")),
        p(r("<w:t>kept</w:t>") + "lost"),
        p(r("<w:t>kept</w:t>")) + "lost",
        "<w:tbl><w:tr><w:tc>" + p(r("<w:t>kept</w:t>")) + "</w:tc>lost</w:tr></w:tbl>",
        p(r("<w:t>kept</w:t>"), '<w:jc w:val="left">lost</w:jc>'),
    ],
)
def test_character_data_outside_the_text_elements_is_refused(body: str) -> None:
    assert refusal(body) == "stray-text"


def test_whitespace_between_elements_is_not_text() -> None:
    body = "\n  " + p("\n    " + r("\n      <w:t>a</w:t>\n    ") + "\n  ", "\n") + "\n"
    assert text_of(body) == ["a"]


@pytest.mark.parametrize(
    "body",
    [
        # A run in the section, paragraph, cell or content-control properties.
        p(r("<w:t>kept</w:t>")) + "<w:sectPr>" + r("<w:t>lost</w:t>") + "</w:sectPr>",
        p(r("<w:t>kept</w:t>"), r("<w:t>lost</w:t>")),
        "<w:tbl><w:tr><w:tc><w:tcPr>"
        + r("<w:t>lost</w:t>")
        + "</w:tcPr>"
        + p(r("<w:t>kept</w:t>"))
        + "</w:tc></w:tr></w:tbl>",
        p(
            "<w:sdt><w:sdtPr>"
            + r("<w:t>lost</w:t>")
            + "</w:sdtPr><w:sdtContent>"
            + r("<w:t>kept</w:t>")
            + "</w:sdtContent></w:sdt>"
        ),
        # Run content outside a run.
        p(r("<w:t>kept</w:t>"), "<w:rPr><w:t>lost</w:t></w:rPr>"),
        p(r("<w:t>kept</w:t>"), '<w:rPr><w:sym w:font="Symbol" w:char="F0B3"/></w:rPr>'),
    ],
)
def test_text_where_the_reader_does_not_walk_is_refused(body: str) -> None:
    assert refusal(body) == "unread-content"


def test_mark_offsets_count_code_points() -> None:
    # U+1D400 is two UTF-16 code units and one code point; the mark starts at 1, not 2.
    body = p(r("<w:t>\U0001d400</w:t>") + r("<w:t>2</w:t>", '<w:vertAlign w:val="superscript"/>'))
    assert _kinds(body) == [(1, 2, "superscript")]


# --- list labels (docx-reader/1.3.0) --------------------------------------------------------


def li(num_id: int, level: int = 0, props: str = "") -> str:
    numbered = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'
    return p(r("<w:t>x</w:t>"), props + numbered)


def labels(body: str, numbering: str, styles: str | None = None) -> list[str | None]:
    paragraphs = read_docx(docx(body, styles, numbering=numbering))
    return [x.numbering.text if x.numbering is not None else None for x in paragraphs]


def override(level: int, inner: str) -> str:
    return f'<w:lvlOverride w:ilvl="{level}">{inner}</w:lvlOverride>'


RESTART = override(0, '<w:startOverride w:val="1"/>')
SECTIONS = abstract(1, lvl(0, text="%1."), lvl(1, text="%1.%2"), lvl(2, text="%1.%2.%3")) + num(
    1, 1
)
SYMBOL_BULLET = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr>'


def test_a_multilevel_list_counts_and_restarts_deeper_levels() -> None:
    body = li(1) + li(1, 1) + li(1, 1) + li(1) + li(1, 1) + li(1, 2) + li(1, 1)
    assert labels(body, SECTIONS) == ["1.", "1.1", "1.2", "2.", "2.1", "2.1.1", "2.2"]


@pytest.mark.parametrize(
    ("fmt", "start", "label"),
    [
        ("decimal", 0, "0."),
        ("decimalZero", 7, "07."),
        ("decimalZero", 12, "12."),
        ("upperRoman", 1994, "MCMXCIV."),
        ("lowerRoman", 4, "iv."),
        ("upperLetter", 1, "A."),
        ("lowerLetter", 26, "z."),
        ("lowerLetter", 27, "aa."),
        ("lowerLetter", 53, "aaa."),
        ("none", 5, "."),
    ],
)
def test_number_formats(fmt: str, start: int, label: str) -> None:
    assert labels(li(1), abstract(1, lvl(0, fmt, start=start)) + num(1, 1)) == [label]


@pytest.mark.parametrize(
    ("fmt", "start"),
    [
        ("upperRoman", 0),
        ("upperRoman", 4000),
        ("lowerLetter", 781),
        ("decimal", -1),
        ("ordinal", 1),
        ("cardinalText", 1),
        ("chicago", 1),
    ],
)
def test_numbers_and_formats_the_reader_cannot_draw_are_refused(fmt: str, start: int) -> None:
    numbering = abstract(1, lvl(0, fmt, start=start)) + num(1, 1)
    assert refusal(li(1), numbering=numbering) == "unsupported-numbering"


def test_legal_numbering_shows_every_level_in_decimal() -> None:
    numbering = abstract(
        1, lvl(0, "upperRoman", "%1"), lvl(1, text="%1.%2", extra="<w:isLgl/>")
    ) + num(1, 1)
    assert labels(li(1) + li(1, 1), numbering) == ["I", "1.1"]


def test_lvl_restart_zero_never_restarts_and_n_restarts_after_level_n_minus_1() -> None:
    never = abstract(1, lvl(0, text="%1"), lvl(1, text="%2", extra='<w:lvlRestart w:val="0"/>'))
    body = li(1) + li(1, 1) + li(1, 1) + li(1) + li(1, 1)
    assert labels(body, never + num(1, 1)) == ["1", "1", "2", "2", "3"]
    after_first = abstract(
        1,
        lvl(0, text="%1"),
        lvl(1, text="%2"),
        lvl(2, text="%3", extra='<w:lvlRestart w:val="1"/>'),
    )
    body = li(1) + li(1, 2) + li(1, 1) + li(1, 2) + li(1) + li(1, 2)
    assert labels(body, after_first + num(1, 1)) == ["1", "1", "1", "2", "2", "1"]


@pytest.mark.parametrize(
    ("extra", "suffix"),
    [
        ("", "tab"),
        ('<w:suff w:val="space"/>', "space"),
        ('<w:suff w:val="nothing"/>', "nothing"),
        ('<w:legacy w:legacy="1" w:legacySpace="0" w:legacyIndent="360"/>', "legacy"),
    ],
)
def test_the_suffix_is_reported(extra: str, suffix: str) -> None:
    numbering = abstract(1, lvl(0, extra=extra)) + num(1, 1)
    assert read_docx(docx(li(1), numbering=numbering))[0].numbering == Numbering(1, 0, "1.", suffix)


def test_bullets_are_drawn_in_their_font() -> None:
    symbol = abstract(1, lvl(0, "bullet", "", extra=SYMBOL_BULLET)) + num(1, 1)
    assert labels(li(1), symbol) == ["•"]
    courier = '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/></w:rPr>'
    assert labels(li(1), abstract(1, lvl(0, "bullet", "o", extra=courier)) + num(1, 1)) == ["o"]
    # With no font of its own, the label is drawn in the paragraph mark's.
    mark = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>'
    plain = abstract(1, lvl(0, "bullet", "")) + num(1, 1)
    assert labels(li(1, props=mark), plain) == ["•"]


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ('<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr>', "symbol-font"),
        ("", "private-use-character"),
        (
            '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="eastAsia"/></w:rPr>',
            "symbol-font",
        ),
    ],
)
def test_bullets_in_fonts_the_reader_cannot_place_are_refused(extra: str, code: str) -> None:
    numbering = abstract(1, lvl(0, "bullet", "", extra=extra)) + num(1, 1)
    assert refusal(li(1), numbering=numbering) == code


def test_a_level_override_replaces_the_level_whole() -> None:
    # The EMA template's shape: a Word 6 dash bullet over a decimal level.
    legacy = override(
        0,
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/>'
        '<w:legacy w:legacy="1" w:legacySpace="0" w:legacyIndent="360"/></w:lvl>',
    )
    paragraph = read_docx(docx(li(6), numbering=NUMBERING + num(6, 0, legacy)))[0]
    assert paragraph.numbering == Numbering(6, 0, "-", "legacy")


SHARED = (
    abstract(7, lvl(0))
    + num(10, 7)
    + num(11, 7)
    + num(12, 7, RESTART)
    + num(13, 7, override(0, '<w:startOverride w:val="5"/>'))
)


def test_lists_that_share_a_definition_continue_and_a_start_override_restarts() -> None:
    assert labels(li(10) + li(11) + li(10), SHARED) == ["1.", "2.", "3."]
    assert labels(li(10) + li(10) + li(12) + li(12), SHARED) == ["1.", "2.", "1.", "2."]
    assert labels(li(13) + li(13), SHARED) == ["5.", "6."]


@pytest.mark.parametrize(
    "body",
    [
        # Back to a list after another restarted the count.
        li(10) + li(12) + li(10),
        # A list that restarts nothing after one that did.
        li(12) + li(11),
        # A level first counted below a level never counted.
        li(1, 1),
    ],
)
def test_a_label_whose_count_is_not_certain_is_refused(body: str) -> None:
    assert refusal(body, numbering=SHARED + SECTIONS) == "ambiguous-numbering"


def test_a_bullet_shows_no_count_and_is_never_ambiguous() -> None:
    bullets = abstract(8, lvl(0, "bullet", "-")) + num(20, 8) + num(21, 8, RESTART)
    assert labels(li(20) + li(21) + li(20), bullets) == ["-", "-", "-"]


def test_a_level_with_no_start_is_counted_only_when_it_is_not_shown() -> None:
    assert labels(li(1), abstract(1, lvl(0, "bullet", "-", start=None)) + num(1, 1)) == ["-"]
    numbering = abstract(1, lvl(0, start=None)) + num(1, 1)
    assert refusal(li(1), numbering=numbering) == "ambiguous-numbering"


@pytest.mark.parametrize(
    ("body", "numbering"),
    [
        (li(99), SECTIONS),
        (li(1), None),
        (li(1, 5), SECTIONS),
        (li(1, 9), SECTIONS),
        (li(1), abstract(1, lvl(0, extra='<w:lvlPicBulletId w:val="0"/>')) + num(1, 1)),
        (
            li(1),
            abstract(
                1,
                '<w:lvl w:ilvl="0"><w:start w:val="1"/>'
                '<w:numFmt w:val="custom" w:format="001, 002, 003, ..."/>'
                '<w:lvlText w:val="%1"/></w:lvl>',
            )
            + num(1, 1),
        ),
        (
            li(1),
            abstract(
                1,
                lvl(
                    0,
                    extra='<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/'
                    'markup-compatibility/2006"/>',
                ),
            )
            + num(1, 1),
        ),
        (li(1), abstract(1, '<w:lvl w:ilvl="0"><w:start w:val="1"/></w:lvl>') + num(1, 1)),
        (li(1), abstract(1, lvl(0, text="%2")) + num(1, 1)),
        (li(1), abstract(1, lvl(0, "bullet", "%1")) + num(1, 1)),
        (li(1), abstract(1, lvl(0, text="%1%")) + num(1, 1)),
        (li(1), abstract(1, lvl(0, extra='<w:suff w:val="dots"/>')) + num(1, 1)),
        (
            li(1),
            abstract(1, lvl(0, "lowerLetter", "%1)", extra="<w:rPr><w:caps/></w:rPr>")) + num(1, 1),
        ),
    ],
)
def test_labels_the_reader_cannot_draw_are_refused(body: str, numbering: str | None) -> None:
    assert refusal(body, numbering=numbering) == "unsupported-numbering"


def test_capitals_do_not_matter_to_a_label_without_letters() -> None:
    numbering = abstract(1, lvl(0, text="%1)", extra="<w:rPr><w:caps/></w:rPr>")) + num(1, 1)
    assert labels(li(1), numbering) == ["1)"]


@pytest.mark.parametrize(
    ("body", "numbering"),
    [
        (li(1, props="<w:rPr><w:vanish/></w:rPr>"), SECTIONS),
        (p(r("<w:t>a</w:t>"), "<w:rPr><w:vanish/></w:rPr>") + li(1), SECTIONS),
        (li(1), abstract(1, lvl(0, extra="<w:rPr><w:vanish/></w:rPr>")) + num(1, 1)),
    ],
)
def test_hidden_labels_and_items_run_on_after_a_hidden_mark_are_refused(
    body: str, numbering: str
) -> None:
    assert refusal(body, numbering=numbering) == "ambiguous-numbering"


LINKED_STYLES = (
    '<w:style w:type="numbering" w:styleId="Outline"><w:pPr><w:numPr><w:numId w:val="31"/>'
    "</w:numPr></w:pPr></w:style>"
)
LINKED = (
    abstract(30, lvl(0, text="Section %1"))
    + '<w:abstractNum w:abstractNumId="32"><w:numStyleLink w:val="Outline"/></w:abstractNum>'
    + num(31, 30)
    + num(33, 32)
)


def test_a_numbering_style_link_is_followed_and_shares_the_count() -> None:
    assert labels(li(33) + li(31) + li(33), LINKED, LINKED_STYLES) == [
        "Section 1",
        "Section 2",
        "Section 3",
    ]
    assert refusal(li(33), numbering=LINKED) == "unsupported-numbering"


def test_a_style_tied_to_a_deeper_level_than_the_paragraph_sets_is_refused() -> None:
    style = (
        '<w:style w:type="paragraph" w:styleId="H2"><w:pPr><w:numPr><w:numId w:val="1"/>'
        "</w:numPr></w:pPr></w:style>"
    )
    body = p(r("<w:t>x</w:t>"), '<w:pStyle w:val="H2"/>')
    tied = '<w:pStyle w:val="H2"/>'
    at_one = abstract(1, lvl(0), lvl(1, text="%2)", extra=tied)) + num(1, 1)
    assert refusal(body, style, at_one) == "ambiguous-numbering"
    at_zero = abstract(1, lvl(0, extra=tied), lvl(1, text="%2)")) + num(1, 1)
    assert labels(body, at_zero, style) == ["1."]


def test_a_symbol_run_with_the_default_font_hint_is_mapped() -> None:
    run = r("<w:t>b</w:t>", '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/>')
    assert text_of(p(run)) == ["β"]


def test_the_template_draws_its_bullets_and_dashes() -> None:
    data = (SOURCES / "qrd-product-information-template-version-104_en.docx").read_bytes()
    drawn = [
        (x.numbering.text, x.numbering.suffix)
        for x in read_docx(data)
        if x.numbering is not None and x.numbering.num_id
    ]
    # Symbol U+F0B7 bullets, and Word 6 dash bullets from the lists that override a level.
    assert sorted(set(drawn)) == [("-", "legacy"), ("•", "tab")]
    assert (drawn.count(("•", "tab")), drawn.count(("-", "legacy"))) == (7, 9)


@pytest.mark.parametrize(
    "numbering",
    [
        abstract(1, lvl(0)) + abstract(1, lvl(0)) + num(1, 1),
        abstract(1, lvl(0), lvl(0)) + num(1, 1),
        abstract(1, lvl(0)) + num(1, 1) + num(1, 1),
        abstract(1, lvl(0)) + num(1, 1, RESTART + RESTART),
        abstract(1, lvl(9)) + num(1, 1),
    ],
)
def test_a_numbering_part_that_defines_something_twice_is_refused(numbering: str) -> None:
    # Refused even when no paragraph is in the list, as a style defined twice is.
    assert refusal(p(r("<w:t>x</w:t>")), numbering=numbering) == "invalid-package"


def test_a_link_to_a_list_that_overrides_a_level_is_refused() -> None:
    restarted = LINKED.replace(num(31, 30), num(31, 30, RESTART))
    assert refusal(li(33), LINKED_STYLES, restarted) == "unsupported-numbering"


@pytest.mark.parametrize(
    "numbering",
    [
        # A level the reader cannot draw, shown in a deeper level's label.
        abstract(1, lvl(0, "chicago"), lvl(1, text="%1.%2")) + num(1, 1),
        # Legal numbering of a level that shows no number.
        abstract(1, lvl(0, "none"), lvl(1, text="%1.%2", extra="<w:isLgl/>")) + num(1, 1),
    ],
)
def test_a_shown_level_the_reader_cannot_draw_is_refused(numbering: str) -> None:
    assert refusal(li(1, 1), numbering=numbering) == "unsupported-numbering"
