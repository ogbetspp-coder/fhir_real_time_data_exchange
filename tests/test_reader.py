"""The .docx reader: exact text, or a refusal with a reason.

Most cases build a minimal .docx in memory, so each rule is tested in isolation. The last group
reads the four pinned EMA files in corpus/ema-qrd, which is where the rules were found.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from label_docx.certify import CertificationError, DocxSource
from label_docx.reader import (
    Document,
    DocxRefusedError,
    NoteReference,
    Numbering,
    read_document,
    read_docx,
)
from numbering_cases import abstract, lvl, num

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
CONTENT_TYPES = (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
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
    footnotes: str | None = None,
    endnotes: str | None = None,
) -> bytes:
    parts: dict[str, tuple[str, str]] = {}
    for kind, content in (("footnotes", footnotes), ("endnotes", endnotes)):
        if content is not None:
            parts[kind] = (f"{kind}.xml", f'<w:{kind} xmlns:w="{W}">{content}</w:{kind}>')
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
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
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
        # Codes the Symbol table holds, in another font: the font decides.
        (p(r('<w:sym w:font="Wingdings" w:char="F0B7"/>')), "unmapped-symbol"),
        (p(r('<w:sym w:font="Times New Roman" w:char="F0B3"/>')), "unmapped-symbol"),
        (p(r("<w:t>\uf0b3</w:t>")), "private-use-character"),
        (p(r("<w:t>\uf0b3</w:t>", '<w:rFonts w:ascii="Wingdings"/>')), "symbol-font"),
        *(
            (p(r("<w:t>a</w:t>", f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>')), "symbol-font")
            for font in ("Zapf Dingbats", "ITC Zapf Dingbats", "Marlett", "MT Extra")
        ),
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
        + r("<w:instrText>HYPERLINK </w:instrText><w:t>not_shown</w:t><w:tab/>")
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>shown</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    assert text_of(body) == ["shown"]


def test_simple_fields_hyperlinks_and_content_controls_are_read_through() -> None:
    body = p(
        '<w:fldSimple w:instr=" HYPERLINK x ">' + r("<w:t>1</w:t>") + "</w:fldSimple>"
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


def _alternate(choice: str, fallback: str = "", requires: str = "wps") -> str:
    return (
        f'<mc:AlternateContent><mc:Choice Requires="{requires}">{choice}</mc:Choice>'
        f"<mc:Fallback>{fallback}</mc:Fallback></mc:AlternateContent>"
    )


WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
SHAPE = (
    f"<w:drawing><wp:anchor xmlns:wp='{WP}'><a:graphic xmlns:a='{A}'>"
    f"<a:graphicData uri='{WPS}'><wps:wsp xmlns:wps='{WPS}'><wps:spPr/></wps:wsp>"
    "</a:graphicData></a:graphic></wp:anchor></w:drawing>"
)
LINE = "<w:pict><v:line xmlns:v='urn:schemas-microsoft-com:vml'/></w:pict>"


def test_a_drawn_shape_with_no_text_reads_as_a_picture_does() -> None:
    # Anchored, as the FDA's Prescribing Information template draws its rules: not in the text.
    assert text_of(p(r("<w:t>a</w:t>") + r(_alternate(SHAPE, LINE)) + r("<w:t>b</w:t>"))) == ["ab"]
    in_line = SHAPE.replace("wp:anchor", "wp:inline")
    assert text_of(p(r("<w:t>a</w:t>") + r(_alternate(in_line, LINE)) + r("<w:t>b</w:t>"))) == [
        "a\ufffcb"
    ]
    textbox = SHAPE.replace("<wps:spPr/>", "<wps:txbx><w:txbxContent/></wps:txbx>")
    for body in (
        p(r(_alternate(SHAPE.replace(f"xmlns:wps='{WPS}'", "xmlns:wps='urn:wps'"), LINE))),
        p(r(_alternate(textbox, LINE))),  # a text box
        p(r(_alternate(SHAPE, "<w:pict><v:textbox xmlns:v='urn:v'/></w:pict>"))),  # in VML
        p(r(_alternate(SHAPE, "<w:sym w:font='Symbol' w:char='F0B7'/>"))),  # run content
        p(r(_alternate(SHAPE, "<w:tab/>"))),
        p(r(_alternate(SHAPE, LINE, requires="wpg"))),  # another choice Word may draw
        p(r(_alternate("<w:t>x</w:t>"))),  # not a drawing
        p(r(_alternate(SHAPE + SHAPE))),  # two
        _alternate(p(r("<w:t>x</w:t>"))),  # paragraphs, not in a run
    ):
        assert refusal(body) == "unsupported-element"


PICTURE = (
    f"<w:drawing><wp:inline xmlns:wp='{WP}'><a:graphic xmlns:a='{A}'>"
    "<a:graphicData uri='http://schemas.openxmlformats.org/drawingml/2006/picture'/>"
    "</a:graphic></wp:inline></w:drawing>"
)


def test_a_picture_is_an_object_replacement_character_where_it_stands() -> None:
    paragraphs = read_docx(docx(p(r("<w:t>a</w:t>") + r(PICTURE) + r("<w:t>b</w:t>"))))
    assert [paragraph.text for paragraph in paragraphs] == ["a\ufffcb"]
    assert paragraphs[0].has_drawing


def test_a_floating_picture_is_not_in_the_text_as_word_shows_it() -> None:
    anchored = PICTURE.replace("wp:inline", "wp:anchor")
    absolute = VML_PICTURE.replace('style="', 'style="Position : Absolute;')
    for floating in (anchored, absolute):
        assert text_of(p(r("<w:t>a</w:t>") + r(floating) + r("<w:t>b</w:t>"))) == ["ab"]
    for body in (
        PICTURE.replace("</wp:inline>", f"</wp:inline><wp:anchor xmlns:wp='{WP}'/>"),  # two
        PICTURE.replace(f"xmlns:wp='{WP}'", "xmlns:wp='urn:wp'"),  # not a drawing's frame
        VML_PICTURE.replace('style="', 'style="position:relative;'),
        VML_PICTURE.replace('style="', 'style="visibility:hidden;'),
    ):
        assert refusal(p(r(body))) == "unsupported-element"


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


@pytest.mark.parametrize(
    "code", [' DATE \\@ "d MMMM yyyy"', "SEQ Table \\# 00", "IF 1 = 1", "MERGEFIELD Name", ""]
)
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
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
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


def _with_entry(data: bytes, info: zipfile.ZipInfo, content: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for kept in source.infolist():
            target.writestr(kept, source.read(kept))
        target.writestr(info, content)
    return out.getvalue()


def _refused_by_both(data: bytes) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(data)
    assert caught.value.code == "invalid-package"
    with pytest.raises(CertificationError):
        DocxSource(data)


def test_a_part_renamed_by_a_unicode_path_field_is_refused() -> None:
    # Stored as word/decoy.xml, with an Info-ZIP Unicode Path field (0x7075) naming it
    # word/document.xml: zipfile reads it under that name; unzip, and the stored name, do not.
    good = docx(p(r("<w:t>5 mg</w:t>")))
    with zipfile.ZipFile(io.BytesIO(good)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    stored, shown = b"word/decoy.xml", b"word/document.xml"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name, content in parts.items():
            target.writestr(name, content)
        info = zipfile.ZipInfo(stored.decode())
        info.extra = struct.pack("<HHBL", 0x7075, 5 + len(shown), 1, zlib.crc32(stored)) + shown
        target.writestr(info, document_xml(p(r("<w:t>50 mg</w:t>"))))
    data = out.getvalue()
    assert zipfile.ZipFile(io.BytesIO(data)).namelist().count("word/document.xml") == 2
    _refused_by_both(data)


@pytest.mark.parametrize(
    "name",
    [
        "word\\document.xml",
        "/word/document.xml",
        "word//document.xml",
        "./word/document.xml",
        "word/../word/document.xml",
        "../document.xml",
        "word/",
    ],
)
def test_a_part_name_that_is_not_a_canonical_part_name_is_refused(name: str) -> None:
    # Each may be word/document.xml to another zip reader (or to zipfile on Windows), holding a
    # second body; none is a part name.
    good = docx(p(r("<w:t>5 mg</w:t>")))
    second = document_xml(p(r("<w:t>50 mg</w:t>"))).encode()
    _refused_by_both(_with_entry(good, zipfile.ZipInfo(name), second))


def test_a_package_without_content_types_or_in_another_compression_is_refused() -> None:
    good = docx(p(r("<w:t>5 mg</w:t>")))
    assert [x.text for x in read_docx(good)] == ["5 mg"]
    with zipfile.ZipFile(io.BytesIO(good)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", method) as target:
            for name, content in parts.items():
                target.writestr(name, content)
        _refused_by_both(out.getvalue())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name, content in parts.items():
            if name != "[Content_Types].xml":
                target.writestr(name, content)
    _refused_by_both(out.getvalue())


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


def test_the_default_table_style_applies_and_the_default_character_style_does_not() -> None:
    # Word applies no default character style to text [default-character-style].
    character = (
        '<w:style w:type="character" w:default="1" w:styleId="Font">'
        "<w:rPr><w:vanish/><w:b/></w:rPr></w:style>"
    )
    (paragraph,) = read_docx(docx(p(r("<w:t>x</w:t>")), character))
    assert (paragraph.text, paragraph.marks) == ("x", ())
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
    fonts = '<w:font w:name="Acme Pi"><w:charset w:val="02"/></w:font>'
    run = r("<w:t>n</w:t>", '<w:rFonts w:ascii="Acme Pi" w:hAnsi="Acme Pi"/>')
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(p(run), fonts=fonts))
    assert caught.value.code == "symbol-font"
    plain = '<w:font w:name="Acme Pi"><w:charset w:val="00"/></w:font>'
    assert read_docx(docx(p(run), fonts=plain))[0].text == "n"


# --- second review: fields with no stored result ---------------------------------------------


def _bare(code: str) -> str:
    return (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve"> {code} </w:instrText>')
        + r('<w:fldChar w:fldCharType="end"/>')
    )


def _seq(stored: str) -> str:
    return (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r('<w:instrText xml:space="preserve"> SEQ Table </w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r(f"<w:t>{stored}</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )


def test_a_hidden_seq_with_no_stored_result_shows_nothing_and_counts() -> None:
    # As WordPerfect conversions leave it ("SEQ CHAPTER \h \r 1"); Word's answer is on record
    # (corpus/numbering-cases, fields-seq-hidden-no-result).
    body = p(_bare("SEQ CHAPTER \\h \\r 1") + r("<w:t>a</w:t>")) + p(_bare("SEQ Table \\h \\r 3"))
    assert text_of(body + p(_seq("4"))) == ["a", "", "4"]
    # It counts: a caption after it stored as 1 is stale.
    assert refusal(body + p(_seq("1"))) == "stale-field"
    # One with no stored result that would show a number is still refused.
    assert refusal(p(_bare("SEQ Table"))) == "field-without-result"
    assert refusal(p(_bare("SEQ Table \\r 3"))) == "field-without-result"


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
        (p('<w:fldSimple w:instr="HYPERLINK x"/>'), "field-without-result"),
        (
            p(
                '<w:fldSimple w:instr="HYPERLINK x" w:dirty="1">'
                + r("<w:t>1</w:t>")
                + "</w:fldSimple>"
            ),
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
    assert _kinds(p('<w:dir w:val="rtl">' + r("<w:t>ab</w:t>") + "</w:dir>")) == [(0, 2, "rtl")]
    assert _kinds(p(r("<w:t>ab</w:t>", "<w:rtl/>"))) == [(0, 2, "rtl")]


def test_paragraph_marks_merge_with_run_marks_of_the_same_kind() -> None:
    # One kind's marks are merged where they overlap or touch, as the Mark contract says.
    shaded = '<w:shd w:val="clear" w:fill="D9D9D9"/>'
    text = r('<w:t xml:space="preserve">Text </w:t>')
    body = p(text + r("<w:t>(4)</w:t>", shaded + "<w:rtl/>"), shaded + "<w:bidi/>")
    assert _kinds(body) == [(0, 8, "rtl"), (0, 8, "shading-D9D9D9")]


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


def test_a_damaged_part_the_reader_does_not_read_refuses_the_package() -> None:
    # A header the reader never opens, its bytes changed after its checksum was written.
    good = docx(p(r("<w:t>hello</w:t>")))
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("word/header1.xml", f'<w:hdr xmlns:w="{W}"/>')
    data = out.getvalue()
    at = data.index(b"<w:hdr")
    damaged = data[:at] + b"<w:hdX" + data[at + 6 :]
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(damaged)
    assert caught.value.code == "invalid-package"
    assert "header1.xml" in caught.value.detail


def test_parts_over_the_package_cap_are_refused_before_unpacking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import label_docx.reader as module

    monkeypatch.setattr(module, "MAX_PACKAGE_BYTES", 100)
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(p(r("<w:t>hello</w:t>"))))
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
        + r('<w:instrText xml:space="preserve"> HYPERLINK bm </w:instrText>')
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


def test_numbering_from_the_paragraph_defaults_is_read_and_from_a_table_style_refused() -> None:
    # Word draws "1." for a list set in the paragraph defaults.
    numbered = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/></w:numPr></w:pPr>'
    defaults = f"<w:docDefaults><w:pPrDefault>{numbered}</w:pPrDefault></w:docDefaults>"
    paragraph = read_docx(docx(p(r("<w:t>x</w:t>")), defaults, numbering=NUMBERING))[0]
    assert paragraph.numbering == Numbering(4, 0, "1.", "tab")
    # A table style's list is not on record, nor one that covers the defaults' list.
    table_style = f'<w:style w:type="table" w:styleId="T">{numbered}</w:style>'
    table = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr><w:tr><w:tc>'
        + p(r("<w:t>x</w:t>"))
        + "</w:tc></w:tr></w:tbl>"
    )
    assert refusal(table, table_style, NUMBERING) == "unsupported-numbering"
    level = table_style.replace('<w:numId w:val="4"/>', "").replace('"0"', '"1"')
    assert refusal(table, defaults + level, NUMBERING) == "unsupported-numbering"


def test_fields_in_a_document_that_updates_them_on_open_are_refused() -> None:
    field = p('<w:fldSimple w:instr=" HYPERLINK x ">' + r("<w:t>1</w:t>") + "</w:fldSimple>")
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
            + r("<w:instrText>HYPERLINK<w:x/> lost</w:instrText>")
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
    # The level 2 item counts level 1 as started, so the level 1 item after it is 2 (Word,
    # corpus/numbering-cases restart-after-first).
    body = li(1) + li(1, 2) + li(1, 1) + li(1, 2) + li(1) + li(1, 2)
    assert labels(body, after_first + num(1, 1)) == ["1", "1", "2", "2", "2", "1"]


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


WINGDINGS = '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr>'


def test_bullets_are_drawn_in_their_font() -> None:
    symbol = abstract(1, lvl(0, "bullet", "", extra=SYMBOL_BULLET)) + num(1, 1)
    assert labels(li(1), symbol) == ["•"]
    courier = '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/></w:rPr>'
    assert labels(li(1), abstract(1, lvl(0, "bullet", "o", extra=courier)) + num(1, 1)) == ["o"]
    # With no font of its own, the label is drawn in the paragraph mark's.
    mark = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>'
    plain = abstract(1, lvl(0, "bullet", "")) + num(1, 1)
    assert labels(li(1, props=mark), plain) == ["•"]
    # Word's default third-level bullet, the Wingdings square, at its code or in the U+F000 range.
    for square in ("\uf0a7", "\u00a7"):
        wingdings = abstract(1, lvl(0, "bullet", square, extra=WINGDINGS)) + num(1, 1)
        assert labels(li(1), wingdings) == ["\u25aa"]


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        # A Wingdings code outside the table, Wingdings in one Latin slot only, another dingbat.
        ('<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr>', "unmapped-symbol"),
        ('<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Arial"/></w:rPr>', "symbol-font"),
        *(
            (f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}"/></w:rPr>', "symbol-font")
            for font in ("Webdings", "Zapf Dingbats", "ITC Zapf Dingbats", "Marlett", "MT Extra")
        ),
        # Another spelling of the name: how Word draws it is not on record.
        ('<w:rPr><w:rFonts w:ascii="wingdings" w:hAnsi="wingdings"/></w:rPr>', "symbol-font"),
        ('<w:rPr><w:rFonts w:ascii="Wing dings" w:hAnsi="Wing dings"/></w:rPr>', "symbol-font"),
        ("", "private-use-character"),
        (
            '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="eastAsia"/></w:rPr>',
            "symbol-font",
        ),
    ],
)
def test_bullets_in_fonts_the_reader_cannot_place_are_refused(extra: str, code: str) -> None:
    # The Wingdings arrowhead, a bullet the closed table does not hold.
    numbering = abstract(1, lvl(0, "bullet", "\uf0d8", extra=extra)) + num(1, 1)
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
    ("body", "drawn"),
    [
        # Back to a list after another restarted the shared count: it continues.
        (li(10) + li(12) + li(10), ["1.", "1.", "2."]),
        # A startOverride applies once, the first time its list reaches the level.
        (li(12) + li(10) + li(12) + li(10), ["1.", "2.", "3.", "4."]),
        # A list that restarts nothing after one that did continues the count.
        (li(12) + li(11), ["1.", "2."]),
        # A level counted before its higher level counts that level as started.
        (li(1, 1) + li(1, 1) + li(1) + li(1, 1), ["1.1", "1.2", "2.", "2.1"]),
    ],
)
def test_counts_across_lists_and_levels_follow_word(body: str, drawn: list[str]) -> None:
    # Word's answers for corpus/numbering-cases return-after-restart, override-return,
    # plain-after-restart and ancestor-never-counted.
    assert labels(body, SHARED + SECTIONS) == drawn


def test_a_bullet_shows_no_count_and_is_never_ambiguous() -> None:
    bullets = abstract(8, lvl(0, "bullet", "-")) + num(20, 8) + num(21, 8, RESTART)
    assert labels(li(20) + li(21) + li(20), bullets) == ["-", "-", "-"]


def test_a_level_with_no_start_starts_at_zero() -> None:
    assert labels(li(1) + li(1), abstract(1, lvl(0, start=None)) + num(1, 1)) == ["0.", "1."]


def test_a_level_only_a_level_override_defines_has_no_start_to_show() -> None:
    numbering = abstract(1, lvl(0)) + num(1, 1, override(1, lvl(1, text="%1.%2")))
    assert refusal(li(1) + li(1, 1), numbering=numbering) == "ambiguous-numbering"
    # Not shown, it is not refused.
    bullet = abstract(1, lvl(0)) + num(1, 1, override(1, lvl(1, "bullet", "-")))
    assert labels(li(1) + li(1, 1), bullet) == ["1.", "-"]


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
    '<w:abstractNum w:abstractNumId="30"><w:styleLink w:val="Outline"/>'
    + lvl(0, text="Section %1")
    + "</w:abstractNum>"
    + '<w:abstractNum w:abstractNumId="32"><w:numStyleLink w:val="Outline"/></w:abstractNum>'
    + num(31, 30)
    + num(33, 32)
)


def test_a_numbering_style_link_takes_the_levels_but_not_the_count() -> None:
    # Lists 33 and 31 count apart: 33 names abstractNum 32, which only borrows 30's levels.
    assert labels(li(33) + li(31) + li(33), LINKED, LINKED_STYLES) == [
        "Section 1",
        "Section 1",
        "Section 2",
    ]
    assert refusal(li(33), numbering=LINKED) == "unsupported-numbering"
    # With no styleLink back, Word draws an empty label; the reader refuses.
    one_way = LINKED.replace('<w:styleLink w:val="Outline"/>', "")
    assert refusal(li(33), LINKED_STYLES, one_way) == "unsupported-numbering"


def test_a_paragraph_that_sets_no_level_is_at_level_zero_whatever_level_names_its_style() -> None:
    style = (
        '<w:style w:type="paragraph" w:styleId="H2"><w:pPr><w:numPr><w:numId w:val="1"/>'
        "</w:numPr></w:pPr></w:style>"
    )
    body = p(r("<w:t>x</w:t>"), '<w:pStyle w:val="H2"/>')
    tied = '<w:pStyle w:val="H2"/>'
    at_one = abstract(1, lvl(0), lvl(1, text="%2)", extra=tied)) + num(1, 1)
    assert labels(body, at_one, style) == ["1."]
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


# --- found in public regulator templates (docx-reader/1.5.0) ---------------------------------


def _banded(conditional: str) -> str:
    return (
        '<w:style w:type="table" w:styleId="Banded"><w:tblStylePr w:type="firstRow">'
        f"{conditional}</w:tblStylePr></w:style>"
    )


def _in_banded(run: str) -> str:
    return (
        '<w:tbl><w:tblPr><w:tblStyle w:val="Banded"/></w:tblPr><w:tr><w:tc>'
        + p(run)
        + "</w:tc></w:tr></w:tbl>"
    )


def test_conditional_table_formatting_that_cannot_change_the_text_is_read() -> None:
    # What the WHO, SAHPRA and EMA ATMP templates set: bold, italic, fonts, sizes, colours.
    harmless = _banded(
        '<w:pPr><w:spacing w:after="0"/><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:i/>'
        '<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="18"/><w:color w:val="1F497D"/>'
        '</w:rPr><w:tcPr><w:shd w:val="clear" w:fill="D9D9D9"/></w:tcPr>'
    )
    # As in the ATMP template, the table turns its first row off (tblLook 0000), so Word
    # shows no bold or italic there; with it on, or no look, it would (refused above).
    off = _in_banded(r("<w:t>10</w:t>")).replace(
        "</w:tblPr>", '<w:tblLook w:val="0000"/></w:tblPr>'
    )
    (paragraph,) = read_docx(docx(off, harmless))
    assert (paragraph.text, paragraph.marks) == ("10", ())
    assert refusal(_in_banded(r("<w:t>10</w:t>")), harmless) == "unsupported-element"


@pytest.mark.parametrize(
    "conditional",
    [
        '<w:rPr><w:color w:val="FFFFFF"/></w:rPr>',
        '<w:rPr><w:sz w:val="2"/></w:rPr>',
        '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>',
        '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr>',
        "<w:rPr><w:caps/></w:rPr>",
        '<w:pPr><w:shd w:val="clear" w:fill="FFFF00"/></w:pPr>',
    ],
)
def test_conditional_table_formatting_that_could_change_the_text_is_refused(
    conditional: str,
) -> None:
    assert refusal(_in_banded(r("<w:t>10</w:t>")), _banded(conditional)) == "unsupported-element"


def test_symbol_text_under_conditional_table_fonts_is_refused() -> None:
    # The conditional font, which the reader does not apply, could replace the Symbol font.
    fonts = _banded('<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr>')
    assert refusal(_in_banded(r("<w:t>b</w:t>", SYMBOL)), fonts) == "symbol-font"


VML_PICTURE = (
    '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml" style="width:16pt;height:13pt">'
    '<v:imagedata xmlns:r="urn:r" r:id="rId8"/></v:shape></w:pict>'
)


def test_a_vml_picture_is_an_object_replacement_character() -> None:
    # The Estonian EMA templates draw the black triangle this way.
    assert text_of(p(r("<w:t>a</w:t>") + r(VML_PICTURE) + r("<w:t>b</w:t>"))) == ["a￼b"]


@pytest.mark.parametrize(
    "pict",
    [
        VML_PICTURE.replace("<v:imagedata", "<v:textbox><w:txbxContent/></v:textbox><v:imagedata"),
        '<w:pict><v:rect xmlns:v="urn:schemas-microsoft-com:vml"/></w:pict>',
        '<w:pict><o:OLEObject xmlns:o="urn:schemas-microsoft-com:office:office"/></w:pict>',
        VML_PICTURE.replace("</w:pict>", VML_PICTURE[len("<w:pict>") :]),  # two pictures
        VML_PICTURE.replace(
            "<v:shape", '<v:group xmlns:v="urn:schemas-microsoft-com:vml"><v:shape'
        ).replace("</v:shape>", "</v:shape></v:group>"),
        # An ActiveX control bound to the picture (a check box's caption, a text box's value).
        VML_PICTURE.replace("</w:pict>", '<w:control xmlns:r="urn:r" r:id="rId9"/></w:pict>'),
    ],
)
def test_vml_that_holds_text_or_is_not_a_picture_is_refused(pict: str) -> None:
    assert refusal(p(r(pict))) == "unsupported-element"


def test_smart_tag_and_custom_xml_properties_are_not_text() -> None:
    body = p(
        '<w:smartTag w:element="place"><w:smartTagPr><w:attr w:name="x" w:val="y"/>'
        "</w:smartTagPr>" + r("<w:t>Oslo</w:t>") + "</w:smartTag>"
        '<w:customXml w:element="z"><w:customXmlPr/>' + r("<w:t>!</w:t>") + "</w:customXml>"
    )
    assert text_of(body) == ["Oslo!"]


def test_a_word_97_2003_document_under_a_docx_name_is_refused() -> None:
    # EMA serves qrd-annex-iv-standard-positive-template_lv.docx as a .doc. A .doc holds zips
    # of its own; one holding a .docx must not be read as the document.
    inner = docx(p(r("<w:t>not this document</w:t>")))
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504 + inner
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(ole)
    assert caught.value.code == "invalid-package"
    assert ".doc" in caught.value.detail


@pytest.mark.parametrize(
    "data",
    [b"junk before" + docx(p(r("<w:t>x</w:t>"))), docx(p(r("<w:t>x</w:t>"))) + b"junk after"],
)
def test_a_zip_archive_that_is_not_the_whole_file_is_refused(data: bytes) -> None:
    # zipfile itself would read both.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "word/document.xml" in archive.namelist()
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(data)
    assert caught.value.code == "invalid-package"


# --- footnotes and endnotes (docx-reader/1.6.0) ------------------------------------------------


def ref(key: int, kind: str = "footnote", custom: bool = False, props: str = "") -> str:
    follows = ' w:customMarkFollows="1"' if custom else ""
    return r(f'<w:{kind}Reference w:id="{key}"{follows}/>', props)


def fnote(key: int, body: str | None = None, kind: str = "footnote") -> str:
    inner = (
        body
        if body is not None
        else p(r(f"<w:{kind}Ref/>") + r("<w:t xml:space='preserve'> n</w:t>"))
    )
    return f'<w:{kind} w:id="{key}">{inner}</w:{kind}>'


SEPARATORS = (
    '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
)


def read(body: str, footnotes: str | None = None, endnotes: str | None = None) -> Document:
    return read_document(docx(body, footnotes=footnotes, endnotes=endnotes))


def test_a_footnote_mark_stands_at_its_offset_and_the_note_is_read() -> None:
    document = read(p(r("<w:t>ab</w:t>") + ref(1) + r("<w:t>c</w:t>")), SEPARATORS + fnote(1))
    (paragraph,) = document.body
    assert paragraph.text == "abc"
    assert paragraph.notes == (NoteReference(2, "footnote", 1, "1"),)
    (note,) = document.footnotes
    assert (note.id, note.mark) == (1, "1")
    assert note.paragraphs[0].text == " n"
    assert note.paragraphs[0].notes == (NoteReference(0, "footnote", 1, "1"),)
    # read_docx gives the body alone.
    assert read_docx(docx(p(r("<w:t>ab</w:t>") + ref(1)), footnotes=fnote(1)))[0].text == "ab"


def test_notes_are_listed_in_the_order_the_body_refers_to_them() -> None:
    document = read(
        p(ref(7) + ref(3) + ref(1, "endnote")), fnote(3) + fnote(7), fnote(1, kind="endnote")
    )
    assert [(n.id, n.mark) for n in document.footnotes] == [(7, "1"), (3, "2")]
    assert [(n.id, n.mark) for n in document.endnotes] == [(1, "i")]


def test_a_custom_mark_is_read_as_text_and_takes_no_number() -> None:
    plain = p(r("<w:t xml:space='preserve'> n</w:t>"))
    document = read(
        p(ref(1) + ref(2, custom=True) + r("<w:t>\u2020</w:t>") + ref(3)),
        fnote(1) + fnote(2, plain) + fnote(3),
    )
    assert document.body[0].text == "\u2020"
    assert [n.mark for n in document.body[0].notes] == ["1", None, "2"]


@pytest.mark.parametrize(
    ("body", "footnotes", "code"),
    [
        # A reference to a note that is not there, or to one referred to twice.
        (p(ref(1)), None, "invalid-package"),
        (p(ref(1) + ref(1)), fnote(1), "invalid-package"),
        # A note nothing refers to: Word does not show it, but its text is in the file.
        (p(ref(1)), fnote(1) + fnote(2), "unread-content"),
        # A hidden mark, a mark in a field code, a mark in a note, an echo in the body.
        (p(ref(1, props="<w:vanish/>")), fnote(1), "hidden-text"),
        (
            p(
                r('<w:fldChar w:fldCharType="begin"/>')
                + r('<w:instrText>REF x</w:instrText><w:footnoteReference w:id="1"/>')
                + r('<w:fldChar w:fldCharType="separate"/>')
                + r("<w:t>1</w:t>")
                + r('<w:fldChar w:fldCharType="end"/>')
            ),
            fnote(1),
            "unsupported-element",
        ),
        (
            p(ref(1)),
            fnote(1, p(r('<w:footnoteReference w:id="2"/>'))) + fnote(2),
            "unsupported-element",
        ),
        (p(r("<w:footnoteRef/>")), None, "unsupported-element"),
        # A list in a note: whether it counts with the body's lists is not on record.
        (
            p(ref(1)),
            fnote(1, p(r("<w:t>x</w:t>"), '<w:numPr><w:numId w:val="3"/></w:numPr>')),
            "unsupported-numbering",
        ),
        # The echo of a custom mark, where Word draws the next note's number.
        (p(ref(1, custom=True) + r("<w:t>*</w:t>")), fnote(1), "ambiguous-numbering"),
    ],
)
def test_note_marks_the_reader_cannot_vouch_for_are_refused(
    body: str, footnotes: str | None, code: str
) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read(body, footnotes)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("footnote_pr", "code"),
    [
        ('<w:numRestart w:val="eachPage"/>', "ambiguous-numbering"),
        ('<w:numFmt w:val="ordinal"/>', "unsupported-numbering"),
        ('<w:numFmt w:val="decimal" w:format="01"/>', "unsupported-numbering"),
    ],
)
def test_note_numbering_that_depends_on_layout_or_language_is_refused(
    footnote_pr: str, code: str
) -> None:
    body = p(ref(1)) + f"<w:sectPr><w:footnotePr>{footnote_pr}</w:footnotePr></w:sectPr>"
    with pytest.raises(DocxRefusedError) as caught:
        read(body, fnote(1))
    assert caught.value.code == code


def test_a_table_in_a_note_is_read() -> None:
    table = "<w:tbl><w:tr><w:tc>" + p(r("<w:t>cell</w:t>")) + "</w:tc></w:tr></w:tbl>"
    document = read(p(ref(1)), fnote(1, p(r("<w:footnoteRef/>")) + table))
    assert [(x.text, x.table) for x in document.footnotes[0].paragraphs] == [
        ("", None),
        ("cell", (0, 0, 0)),
    ]


@pytest.mark.parametrize(
    "character", ["\u03b4", "\u2264", "\u2022", "\u2265", "\u2212", "\u223c", "\u00b5", "\u00d7"]
)
def test_unicode_text_set_in_the_symbol_font_is_refused(character: str) -> None:
    # PDF converters write Unicode characters in runs set in Symbol, as pdf2docx does for the HL7
    # PQ IG's Module 3 examples. Word 16.113.3, asked to draw them (saved as PDF, the page
    # rendered and read), drew delta as a trademark sign, less-than-or-equal as a double prime,
    # micro as proportional-to, the multiplication sign as a dot, and the bullet as a missing
    # glyph, while greater-than-or-equal, minus and tilde came out as themselves. The text says
    # one thing and Word shows another, so neither is read.
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(p(r(f"<w:t>{character}</w:t>", SYMBOL))))
    assert caught.value.code == "unmapped-symbol"


# --- cross-references (docx-reader/1.9.0) ------------------------------------------------------


def _field(code: str, stored: str) -> str:
    return (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve"> {code} </w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + (r(f'<w:t xml:space="preserve">{stored}</w:t>') if stored else "")
        + r('<w:fldChar w:fldCharType="end"/>')
    )


def _marked(name: str, inner: str, key: int = 1) -> str:
    return f'<w:bookmarkStart w:id="{key}" w:name="{name}"/>{inner}<w:bookmarkEnd w:id="{key}"/>'


def test_a_cross_reference_that_prints_its_stored_text_is_read() -> None:
    body = p(_marked("t", r("<w:t>below 25 C</w:t>"))) + p(_field("REF t \\h", "below 25 C"))
    assert text_of(body) == ["below 25 C", "below 25 C"]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        # Stored as other text than the bookmark's: Word prints the bookmark's.
        (
            p(_marked("t", r("<w:t>below 25 C</w:t>"))) + p(_field("REF t", "below 30 C")),
            "stale-field",
        ),
        # A bookmark that is not there: Word prints an error.
        (p(_field("REF gone", "old")), "computed-field"),
        # A bookmark across paragraphs, or between them.
        (
            p('<w:bookmarkStart w:id="1" w:name="t"/>' + r("<w:t>a</w:t>"))
            + p(r("<w:t>b</w:t>") + '<w:bookmarkEnd w:id="1"/>')
            + p(_field("REF t", "a")),
            "computed-field",
        ),
        # A switch whose effect is not on record.
        (p(_marked("t", r("<w:t>x</w:t>"))) + p(_field("REF t \\p", "x")), "computed-field"),
    ],
)
def test_cross_references_the_reader_cannot_vouch_for_are_refused(body: str, code: str) -> None:
    assert refusal(body) == code


def test_a_note_reference_prints_its_notes_mark_or_is_refused() -> None:
    body = p(r("<w:t>a</w:t>") + _marked("fn", ref(1))) + p(_field("NOTEREF fn \\h", "1"))
    assert read(body, fnote(1)).body[1].text == "1"
    stale = p(r("<w:t>a</w:t>") + _marked("fn", ref(1))) + p(_field("NOTEREF fn", "7"))
    with pytest.raises(DocxRefusedError) as caught:
        read(stale, fnote(1))
    assert caught.value.code == "stale-field"
    # A REF over a note mark would print the mark, which is not in the text.
    over = p(_marked("fn", r("<w:t>a</w:t>") + ref(1))) + p(_field("REF fn", "a"))
    with pytest.raises(DocxRefusedError) as caught:
        read(over, fnote(1))
    assert caught.value.code == "computed-field"


# --- tables of contents and page numbers (docx-reader/1.10.0) ----------------------------------


def test_a_table_of_contents_is_read_as_stored_and_its_page_numbers_placed() -> None:
    entry = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r('<w:instrText xml:space="preserve"> TOC \\o "1-2" </w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>1</w:t><w:tab/><w:t>Stability</w:t><w:tab/>")
        + _field("PAGEREF _Toc1 \\h", "4")
    )
    end = p(r('<w:fldChar w:fldCharType="end"/>') + _marked("_Toc1", r("<w:t>Stability</w:t>")))
    paragraphs = read_docx(docx(entry + end))
    # The entry is what Word prints; the page number, set by the layout, is not in the text.
    assert [(x.text, x.pages) for x in paragraphs] == [("1\tStability\t", (12,)), ("Stability", ())]


def test_page_fields_are_placed_not_read() -> None:
    simple = p(
        r("<w:t xml:space='preserve'>Page </w:t>")
        + '<w:fldSimple w:instr=" PAGE ">'
        + r("<w:t>7</w:t>")
        + "</w:fldSimple>"
    )
    paragraph = read_docx(docx(simple))[0]
    assert (paragraph.text, paragraph.pages) == ("Page ", (5,))
    hidden = p(
        '<w:fldSimple w:instr=" PAGE ">' + r("<w:t>7</w:t>", "<w:vanish/>") + "</w:fldSimple>"
    )
    assert refusal(hidden) == "hidden-text"
    across = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r("<w:instrText>PAGE</w:instrText>")
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>7</w:t>")
    )
    assert refusal(across + p(r('<w:fldChar w:fldCharType="end"/>'))) == "unbalanced-field"


# --- emphasis (docx-reader/1.11.0) -------------------------------------------------------------


TOGGLE_STYLES = (
    '<w:style w:type="paragraph" w:styleId="PB"><w:rPr><w:b/><w:i/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="CB"><w:rPr><w:b/><w:i/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="PBchild"><w:basedOn w:val="PB"/></w:style>'
)


@pytest.mark.parametrize(
    ("body", "shown"),
    [
        (p(r("<w:t>x</w:t>", "<w:b/><w:i/>")), True),
        (p(r("<w:t>x</w:t>"), '<w:pStyle w:val="PB"/>'), True),
        (p(r("<w:t>x</w:t>", '<w:rStyle w:val="CB"/>')), True),
        # Two kinds of style that both set it cancel; the run's own setting wins.
        (p(r("<w:t>x</w:t>", '<w:rStyle w:val="CB"/>'), '<w:pStyle w:val="PB"/>'), False),
        (p(r("<w:t>x</w:t>", '<w:b w:val="0"/><w:i w:val="0"/>'), '<w:pStyle w:val="PB"/>'), False),
        (
            p(r("<w:t>x</w:t>", '<w:rStyle w:val="CB"/><w:b/><w:i/>'), '<w:pStyle w:val="PB"/>'),
            True,
        ),
        # Inherited through basedOn, not cancelled.
        (p(r("<w:t>x</w:t>"), '<w:pStyle w:val="PBchild"/>'), True),
    ],
)
def test_bold_and_italic_are_marked_as_word_shows_them(body: str, shown: bool) -> None:
    kinds = {m.kind for m in read_docx(docx(body, TOGGLE_STYLES))[0].marks}
    assert ({"bold", "italic"} <= kinds) is shown
    assert ({"bold", "italic"} & kinds == set()) is not shown


def test_document_defaults_turn_a_toggle_on_whatever_the_styles_give() -> None:
    defaults = "<w:docDefaults><w:rPrDefault><w:rPr><w:b/></w:rPr></w:rPrDefault></w:docDefaults>"
    styles = defaults + TOGGLE_STYLES
    cancelled = p(r("<w:t>x</w:t>", '<w:rStyle w:val="CB"/>'), '<w:pStyle w:val="PB"/>')
    assert "bold" in {m.kind for m in read_docx(docx(cancelled, styles))[0].marks}
    off = p(r("<w:t>x</w:t>", '<w:b w:val="0"/>'))
    assert "bold" not in {m.kind for m in read_docx(docx(off, styles))[0].marks}


# --- lists, styles and fonts held to Word or refused (docx-reader audit) ------------------------

MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _plus(data: bytes, name: str, content: str, kind: str | None = None) -> bytes:
    """``data`` with part ``name`` added or replaced, and related from the document as ``kind``."""
    out = io.BytesIO()
    rels = "word/_rels/document.xml.rels"
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            if info.filename == rels and kind is not None:
                extra = RELATIONSHIP.format(kind=kind, target=name.removeprefix("word/"))
                text = source.read(info).decode().replace("</Relationships>", extra + "</")
                target.writestr(info, text + "Relationships>")
            elif info.filename != name:
                target.writestr(info, source.read(info))
        target.writestr(name, content)
    return out.getvalue()


def _wrapped(inner: str) -> str:
    """``inner`` in alternate content, the same in both branches."""
    return (
        f'<mc:AlternateContent xmlns:mc="{MC}"><mc:Choice Requires="w14">{inner}</mc:Choice>'
        f"<mc:Fallback>{inner}</mc:Fallback></mc:AlternateContent>"
    )


_STYLED_X = p(r("<w:t>a</w:t>"), '<w:pStyle w:val="S"/>')


@pytest.mark.parametrize(
    ("body", "styles", "numbering"),
    [
        # A style's vanish, Symbol font or list, wrapped: Word applies a branch, a reader of the
        # plain form applies neither.
        (
            _STYLED_X,
            f'<w:style w:styleId="S"><w:rPr>{_wrapped("<w:vanish/>")}</w:rPr></w:style>',
            None,
        ),
        (
            _STYLED_X,
            '<w:style w:styleId="S"><w:rPr>'
            + _wrapped('<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>')
            + "</w:rPr></w:style>",
            None,
        ),
        (
            _STYLED_X,
            '<w:style w:styleId="S"><w:pPr>'
            + _wrapped('<w:numPr><w:numId w:val="1"/></w:numPr>')
            + "</w:pPr></w:style>",
            abstract(1, lvl(0)) + num(1, 1),
        ),
        (
            li(1),
            None,
            abstract(1, lvl(0)) + num(1, 1, _wrapped(override(0, '<w:startOverride w:val="5"/>'))),
        ),
        # In a list level, but not one of its own children.
        (
            li(1),
            None,
            abstract(1, lvl(0, extra=f"<w:rPr>{_wrapped('<w:caps/>')}</w:rPr>")) + num(1, 1),
        ),
    ],
    ids=["style-vanish", "style-symbol", "style-list", "start-override", "level-properties"],
)
def test_alternate_content_in_styles_or_lists_is_refused(
    body: str, styles: str | None, numbering: str | None
) -> None:
    assert refusal(body, styles, numbering) == "unsupported-element"


@pytest.mark.parametrize(
    ("name", "kind", "content"),
    [
        (
            "word/settings.xml",
            "settings",
            f'<w:settings xmlns:w="{W}">{_wrapped("<w:updateFields/>")}</w:settings>',
        ),
        (
            "word/fontTable.xml",
            "fontTable",
            f'<w:fonts xmlns:w="{W}">{_wrapped("<w:font w:name='x'/>")}</w:fonts>',
        ),
        (
            "word/theme/theme1.xml",
            "theme",
            f'<a:theme xmlns:a="{A}">{_wrapped("<a:themeElements/>")}</a:theme>',
        ),
    ],
    ids=["settings", "font-table", "theme"],
)
def test_alternate_content_in_settings_fonts_or_theme_is_refused(
    name: str, kind: str, content: str
) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(_plus(docx(p(r("<w:t>a</w:t>"))), name, content, kind))
    assert caught.value.code == "unsupported-element"


@pytest.mark.parametrize("attribute", ["ProcessContent", "MustUnderstand"])
def test_markup_compatibility_processing_is_refused(attribute: str) -> None:
    body = f'<w:p mc:{attribute}="w"><w:r><w:t>a</w:t></w:r></w:p>'
    assert refusal(body) == "unsupported-element"
    styles = f'<w:style w:styleId="S" mc:{attribute}="w"/>'.replace(
        "<w:style ", f'<w:style xmlns:mc="{MC}" '
    )
    assert refusal(p(r("<w:t>a</w:t>")), styles) == "unsupported-element"


HIDDEN_CHARACTER = (
    '<w:style w:type="character" w:styleId="Hid"><w:rPr><w:vanish/></w:rPr></w:style>'
)


@pytest.mark.parametrize(
    ("body", "numbering"),
    [
        # Hidden by the mark's character style; and hidden, under a level that says visible.
        (li(1, props='<w:rPr><w:rStyle w:val="Hid"/></w:rPr>'), SECTIONS),
        (
            li(1, props="<w:rPr><w:vanish/></w:rPr>"),
            abstract(1, lvl(0, extra='<w:rPr><w:vanish w:val="0"/></w:rPr>')) + num(1, 1),
        ),
    ],
    ids=["mark-character-style", "level-visible"],
)
def test_a_numbered_paragraph_whose_mark_is_hidden_is_refused(body: str, numbering: str) -> None:
    assert refusal(body, HIDDEN_CHARACTER, numbering) == "ambiguous-numbering"


def test_a_negative_lvl_restart_is_refused() -> None:
    # corpus/numbering-cases restart-never-shown-deeper with -1 for 0.
    shown_deeper = (
        abstract(
            1,
            lvl(0, "decimalZero", "%1)", start=None),
            lvl(1, "upperLetter", "%2)", '<w:lvlRestart w:val="-1"/>'),
            lvl(2, "upperLetter", "%1.%2.%3."),
        )
        + num(1, 1, override(2, '<w:startOverride w:val="2"/>'))
        + num(2, 1, override(0, '<w:startOverride w:val="2"/>'))
    )
    assert refusal(li(1, 1) + li(2) + li(2, 2), numbering=shown_deeper) == "unsupported-numbering"
    # List 1's level 1 never restarts by -1; list 2 draws level 1 in a look of its own.
    across = (
        abstract(1, lvl(0), lvl(1, text="%2", extra='<w:lvlRestart w:val="-1"/>'))
        + num(1, 1)
        + num(2, 1, override(1, lvl(1, text="%2")))
    )
    assert refusal(li(2, 1) + li(1) + li(2, 1), numbering=across) == "unsupported-numbering"


@pytest.mark.parametrize("name", ["symbol", "SYMBOL", "SymbolMT", "Symbol MT", "Sym bol"])
def test_the_symbol_font_by_another_spelling_is_refused(name: str) -> None:
    run = r("<w:t>a</w:t>", f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}"/>')
    assert refusal(p(run)) == "symbol-font"
    assert refusal(p(r(f'<w:sym w:font="{name}" w:char="F061"/>'))) == "unmapped-symbol"


MARK_STYLES = (
    '<w:style w:type="character" w:styleId="Sym"><w:rPr>'
    '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="Caps"><w:rPr><w:caps/></w:rPr></w:style>'
)


def test_the_paragraph_marks_character_style_draws_the_label() -> None:
    # Word: the mark's character style Sym draws the label in Symbol; Caps in capitals.
    letters = abstract(1, lvl(0, "lowerLetter", "%1)")) + num(1, 1)
    symbol = li(1, props='<w:rPr><w:rStyle w:val="Sym"/></w:rPr>')
    assert labels(symbol, letters, MARK_STYLES) == ["\u03b1)"]
    caps = li(1, props='<w:rPr><w:rStyle w:val="Caps"/></w:rPr>')
    assert refusal(caps, MARK_STYLES, letters) == "unsupported-numbering"


def test_legal_numbering_keeps_a_decimal_zero_level() -> None:
    # Word: "1.01" for a decimalZero level under isLgl.
    numbering = abstract(
        1, lvl(0, text="%1."), lvl(1, "decimalZero", "%1.%2", extra="<w:isLgl/>")
    ) + num(1, 1)
    assert labels(li(1) + li(1, 1), numbering) == ["1.", "1.01"]


def test_a_based_on_naming_a_style_of_another_kind() -> None:
    # Word ignores a paragraph style's basedOn that names a character style: plain "a".
    character = (
        '<w:style w:type="character" w:styleId="C"><w:rPr><w:b/>'
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:style>'
    )
    styles = (
        character + '<w:style w:type="paragraph" w:styleId="S"><w:basedOn w:val="C"/></w:style>'
    )
    (paragraph,) = read_docx(docx(_STYLED_X, styles))
    assert (paragraph.text, paragraph.marks) == ("a", ())
    # Any other kind of style under another: what Word does is not on record.
    numbering_style = (
        '<w:style w:type="numbering" w:styleId="N"><w:pPr><w:numPr><w:numId w:val="1"/>'
        "</w:numPr></w:pPr></w:style>"
    )
    based = (
        numbering_style
        + '<w:style w:type="paragraph" w:styleId="S"><w:basedOn w:val="N"/></w:style>'
    )
    assert refusal(_STYLED_X, based, abstract(1, lvl(0)) + num(1, 1)) == "unsupported-element"
    bold = '<w:style w:type="paragraph" w:styleId="Q"><w:rPr><w:b/></w:rPr></w:style>'
    run = p(r("<w:t>a</w:t>", '<w:rStyle w:val="D"/>'))
    under = bold + '<w:style w:type="character" w:styleId="D"><w:basedOn w:val="Q"/></w:style>'
    assert refusal(run, under) == "unsupported-element"


def test_a_list_level_text_past_the_bound_is_refused() -> None:
    assert labels(li(1), abstract(1, lvl(0, text="x" * 255)) + num(1, 1)) == ["x" * 255]
    assert refusal(li(1), numbering=abstract(1, lvl(0, text="x" * 256)) + num(1, 1)) == (
        "unsupported-numbering"
    )


@pytest.mark.parametrize("start", [7, 9])
def test_note_symbols_past_those_word_drew_are_refused(start: int) -> None:
    rules = f'<w:numFmt w:val="chicago"/><w:numStart w:val="{start}"/>'
    body = p(ref(1)) + f"<w:sectPr><w:footnotePr>{rules}</w:footnotePr></w:sectPr>"
    with pytest.raises(DocxRefusedError) as caught:
        read(body, fnote(1))
    assert caught.value.code == "unsupported-numbering"
    six = rules.replace(f'"{start}"', '"6"')
    body = p(ref(1)) + f"<w:sectPr><w:footnotePr>{six}</w:footnotePr></w:sectPr>"
    assert read(body, fnote(1)).footnotes[0].mark == "††"


@pytest.mark.parametrize(
    "kind",
    [
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/x/numbering",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/numbering",
    ],
)
def test_a_relationship_of_a_type_word_does_not_write_is_refused(kind: str) -> None:
    data = docx(li(1), numbering=abstract(1, lvl(0)) + num(1, 1))
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        rels = package.read("word/_rels/document.xml.rels").decode()
    standard = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
    changed = _plus(data, "word/_rels/document.xml.rels", rels.replace(standard, kind))
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(changed)
    assert caught.value.code == "invalid-package"


@pytest.mark.parametrize(
    ("fonts", "name"),
    [
        # A font Word replaces by a symbol font when it is missing.
        ('<w:font w:name="Foo"><w:altName w:val="Symbol"/></w:font>', "Foo"),
        ('<w:font w:name="Foo"><w:altName w:val="Wingdings"/></w:font>', "Foo"),
        # A font the document embeds, whose glyphs may be any.
        ('<w:font w:name="Foo"><w:embedRegular r:id="x" xmlns:r="urn:r"/></w:font>', "Foo"),
        ('<w:font w:name="Symbol"><w:embedRegular r:id="x" xmlns:r="urn:r"/></w:font>', "Symbol"),
        # Symbol-encoded by its code pages, or by name with no font table entry.
        ('<w:font w:name="Foo"><w:sig w:csb0="80000001"/></w:font>', "Foo"),
        ("", "Monotype Sorts"),
        ("", "Bookshelf Symbol 7"),
    ],
)
def test_a_font_word_may_draw_as_symbols_is_refused(fonts: str, name: str) -> None:
    run = p(r("<w:t>a</w:t>", f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}"/>'))
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(docx(run, fonts=fonts))
    assert caught.value.code == "symbol-font"


# --- fields and text (sweep 2) -----------------------------------------------------------------

BEGIN = r('<w:fldChar w:fldCharType="begin"/>')
SEPARATE = r('<w:fldChar w:fldCharType="separate"/>')
END = r('<w:fldChar w:fldCharType="end"/>')
HIDDEN_SPACE = '<w:rPr><w:vanish/></w:rPr><w:t xml:space="preserve"> </w:t>'


def _code(code: str) -> str:
    return r(f'<w:instrText xml:space="preserve"> {code} </w:instrText>')


def _after_hidden_space(inner: str, same_run: bool) -> str:
    """A hidden space, then ``inner`` in the same run or a run of its own."""
    if same_run:
        return f"<w:r>{HIDDEN_SPACE}{inner}</w:r>"
    return f"<w:r>{HIDDEN_SPACE}</w:r><w:r>{inner}</w:r>"


@pytest.mark.parametrize("same_run", [True, False])
def test_hidden_whitespace_before_a_field_character_does_not_shift_the_field(
    same_run: bool,
) -> None:
    # The hidden space is dropped, so nothing after it moves: in its own run or not, one result.
    figure = r('<w:t xml:space="preserve">Figure </w:t>')
    end = _after_hidden_space('<w:fldChar w:fldCharType="end"/>', same_run)
    seq = p(figure + BEGIN + _code("SEQ Figure") + SEPARATE + end + r("<w:t>1</w:t>"))
    assert refusal(seq) == "stale-field"
    separate = _after_hidden_space(
        '<w:fldChar w:fldCharType="begin"/><w:instrText> SEQ Figure </w:instrText>'
        '<w:fldChar w:fldCharType="separate"/>',
        same_run,
    )
    assert text_of(p(figure + separate + r("<w:t>1</w:t>") + END)) == ["Figure 1"]
    stale_ref = p(_marked("t", r("<w:t>12</w:t>"))) + p(
        BEGIN + _code("REF t") + SEPARATE + r("<w:t>1</w:t>") + end + r("<w:t>2</w:t>")
    )
    assert refusal(stale_ref) == "stale-field"


@pytest.mark.parametrize(
    "body",
    [
        # Two separators: the text between them would be shown and never checked.
        p(
            BEGIN
            + _code("SEQ Table")
            + SEPARATE
            + r("<w:t>WRONG</w:t>")
            + SEPARATE
            + r("<w:t>1</w:t>")
            + END
        ),
        p(_marked("t", r("<w:t>abc</w:t>")))
        + p(
            BEGIN
            + _code("REF t")
            + SEPARATE
            + r("<w:t>STALE</w:t>")
            + SEPARATE
            + r("<w:t>abc</w:t>")
            + END
        ),
        p(r("<w:t>a</w:t>") + SEPARATE),
        p(r("<w:t>a</w:t>") + END),
        p(
            BEGIN
            + _code("DOCPROPERTY x")
            + r('<w:fldChar w:fldCharType="other"/>')
            + SEPARATE
            + r("<w:t>x</w:t>")
            + END
        ),
    ],
    ids=["seq-separate-twice", "ref-separate-twice", "stray-separate", "stray-end", "unknown"],
)
def test_field_characters_out_of_place_are_refused(body: str) -> None:
    assert refusal(body) == "unbalanced-field"


def _nested(outer: str, inner: str, stored: str, after: str = "") -> str:
    """A field whose code holds another field (``inner``, showing ``stored``)."""
    return (
        BEGIN
        + _code(outer)
        + BEGIN
        + _code(inner)
        + SEPARATE
        + r(f"<w:t>{stored}</w:t>")
        + END
        + (_code(after) if after else "")
    )


@pytest.mark.parametrize(
    "body",
    [
        # The identifier is a field: which counter Word counts is not the reader's to guess.
        p(_field("SEQ Figure", "1"))
        + p(_nested("SEQ", 'QUOTE "Figure"', "Figure") + SEPARATE + r("<w:t>1</w:t>") + END),
        p(_nested("SEQ", "QUOTE Figure", "Figure", "\\h") + END),
        # A SEQ in another field's code is neither shown nor counted by the reader.
        p(_nested('HYPERLINK "x" \\o "', "SEQ Figure", "5", '"') + SEPARATE)
        + p(r("<w:t>link</w:t>") + END)
        + p(_field("SEQ Figure", "1")),
        p(
            BEGIN
            + _code("IF 1 = 1")
            + '<w:fldSimple w:instr=" SEQ Figure ">'
            + r("<w:t>1</w:t>")
            + "</w:fldSimple>"
            + SEPARATE
            + r("<w:t>x</w:t>")
            + END
        ),
    ],
    ids=["nested-identifier", "nested-identifier-hidden", "in-a-tooltip", "simple-in-a-code"],
)
def test_a_computed_field_with_a_field_in_its_code_or_in_another_code_is_refused(body: str) -> None:
    assert refusal(body) == "computed-field"


@pytest.mark.parametrize(
    ("code", "placed"),
    [
        ("PAGEREF _Ref1 \\h", True),
        ("PAGE \\* MERGEFORMAT", True),
        # On record: Word shows the page number (ema-templates, a footer).
        ("PAGE \\* Arabic \\* MERGEFORMAT", True),
        ("NUMPAGES", True),
        # Word shows "above" or "below" for \p, the picture's text for \#, words for CardText.
        ("PAGEREF _Ref1 \\p \\h", False),
        ("PAGE \\# \"'Page '0\"", False),
        ("PAGE \\* CardText", False),
        ("NUMPAGES \\* roman", False),
        ("PAGE x", False),
        ("PAGEREF", False),
    ],
)
def test_page_fields_with_switches_word_has_not_answered_are_refused(
    code: str, placed: bool
) -> None:
    body = p(_marked("_Ref1", r("<w:t>Table 1</w:t>"))) + p(
        r('<w:t xml:space="preserve">See </w:t>') + _field(code, "above")
    )
    if placed:
        assert [x.pages for x in read_docx(docx(body))] == [(), (4,)]
    else:
        assert refusal(body) == "computed-field"


def test_a_page_reference_to_a_bookmark_it_cannot_find_is_refused() -> None:
    # Word shows the stored number; what it prints is not on record.
    entry = r("<w:t>2</w:t><w:tab/><w:t>Stability</w:t><w:tab/>")
    assert refusal(p(entry + _field("PAGEREF _Toc999 \\h", "9"))) == "computed-field"
    across = (
        p('<w:bookmarkStart w:id="1" w:name="_Toc1"/>' + r("<w:t>a</w:t>"))
        + p(r("<w:t>b</w:t>") + '<w:bookmarkEnd w:id="1"/>')
        + p(entry + _field("PAGEREF _Toc1 \\h", "9"))
    )
    assert refusal(across) == "computed-field"
    in_note = fnote(1, p(r("<w:footnoteRef/>") + _field("PAGEREF _Toc1 \\h", "9")))
    with pytest.raises(DocxRefusedError) as caught:
        read(p(_marked("_Toc1", r("<w:t>a</w:t>")) + ref(1)), in_note)
    assert caught.value.code == "computed-field"


@pytest.mark.parametrize(
    "marked",
    [
        _marked("_Ref1", r("<w:t>abc</w:t>")) + ref(1),
        ref(1) + _marked("_Ref1", r("<w:t>abc</w:t>")),
        r("<w:t>abc</w:t>") + _marked("_Ref1", "") + ref(1),
    ],
    ids=["mark-after-end", "mark-before-start", "empty-bookmark-before-mark"],
)
def test_a_note_reference_counts_only_a_note_inside_its_bookmark(marked: str) -> None:
    # Word prints "Error! Bookmark not defined." for the mark just after the bookmark.
    body = p(marked) + p(
        r('<w:t xml:space="preserve">see note </w:t>') + _field("NOTEREF _Ref1 \\h", "1")
    )
    with pytest.raises(DocxRefusedError) as caught:
        read(body, fnote(1))
    assert caught.value.code == "computed-field"
    inside = p(_marked("_Ref1", ref(1))) + p(_field("NOTEREF _Ref1 \\h", "1"))
    assert read(inside, fnote(1)).body[1].text == "1"


def test_a_locked_page_field_is_refused() -> None:
    # Word shows a locked field's stored number, which the reader would set aside as a page.
    locked = p(_marked("_Ref1", r("<w:t>x</w:t>"))) + p(
        r('<w:fldChar w:fldCharType="begin" w:fldLock="1"/>')
        + _code("PAGEREF _Ref1 \\h")
        + SEPARATE
        + r("<w:t>5</w:t>")
        + END
    )
    assert refusal(locked) == "computed-field"
    simple = p(
        '<w:fldSimple w:instr=" PAGE " w:fldLock="1">' + r("<w:t>7</w:t>") + "</w:fldSimple>"
    )
    assert refusal(simple) == "computed-field"


@pytest.mark.parametrize(
    ("character", "code"),
    [
        ("\u202e", "format-character"),  # RIGHT-TO-LEFT OVERRIDE: "10\u202e9" draws "109" reversed
        ("\u200b", "format-character"),
        ("\u200e", "format-character"),
        ("\ufeff", "format-character"),
        ("\u00ad", "format-character"),  # Word writes a soft hyphen as w:softHyphen
        ("\u0085", "format-character"),  # a C1 control
        ("\ufe0f", "format-character"),  # a variation selector: default-ignorable
        ("\U000e0041", "format-character"),
        ("\u0378", "unassigned-character"),
        ("\ufdd0", "unassigned-character"),
    ],
)
def test_invisible_control_and_unassigned_characters_are_refused(character: str, code: str) -> None:
    assert refusal(p(r(f"<w:t>10{character}9 mg</w:t>"))) == code


def test_the_ignorable_table_is_the_epi_readers() -> None:
    from label_docx import epi, reader

    assert reader.DEFAULT_IGNORABLE == epi.DEFAULT_IGNORABLE


def test_a_hidden_inline_picture_is_refused() -> None:
    # Word does not draw it: an additional-monitoring triangle that is hidden is not there.
    hidden = PICTURE.replace("<a:graphic ", "<wp:docPr id='1' name='p' hidden='1'/><a:graphic ")
    assert refusal(p(r("<w:t>a</w:t>") + r(hidden))) == "unsupported-element"
    shown = PICTURE.replace("<a:graphic ", "<wp:docPr id='1' name='p' hidden='0'/><a:graphic ")
    assert text_of(p(r(shown))) == ["\ufffc"]


@pytest.mark.parametrize("where", ["rPr", "pPr"])
def test_properties_in_an_unknown_namespace_are_refused(where: str) -> None:
    unknown = '<x:hide xmlns:x="urn:x"/>'
    body = p(r("<w:t>a</w:t>", unknown)) if where == "rPr" else p(r("<w:t>a</w:t>"), unknown)
    assert refusal(body) == "unsupported-element"
    # Word's own extensions (w14 and later) are known to it and change no text.
    w14 = '<w14:ligatures xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"/>'
    body = p(r("<w:t>a</w:t>", w14)) if where == "rPr" else p(r("<w:t>a</w:t>"), w14)
    assert text_of(body) == ["a"]


def _merged_away(inner: str) -> str:
    """A table whose second row's cell is merged into the first's (vMerge continue)."""
    return (
        "<w:tbl><w:tr><w:tc><w:tcPr><w:vMerge w:val='restart'/></w:tcPr>"
        + p(r("<w:t>top</w:t>"))
        + "</w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr>"
        + inner
        + "</w:tc></w:tr></w:tbl>"
    )


@pytest.mark.parametrize(
    "inner",
    [
        # Word draws "1.", "1.", "2.": the merged-away label is not drawn nor counted.
        p("", '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>'),
        p(r('<w:footnoteReference w:id="2"/>')),
        p(r('<w:commentReference w:id="0"/>')),
        p(r("<w:t>\u00a0</w:t>")),
        p(r("<w:tab/>")),
    ],
    ids=["numbered", "note-mark", "comment-mark", "no-break-space", "tab"],
)
def test_anything_but_an_empty_unnumbered_paragraph_in_a_merged_away_cell_is_refused(
    inner: str,
) -> None:
    body = p(r("<w:t>before</w:t>")) + _merged_away(inner) + p(r("<w:t>after</w:t>"))
    footnotes = '<w:footnote w:id="2">' + p(r("<w:t>note</w:t>")) + "</w:footnote>"
    with pytest.raises(DocxRefusedError) as caught:
        read_document(docx(body, numbering=NUMBERING, footnotes=footnotes))
    assert caught.value.code == "unsupported-element"
    # An empty, unnumbered paragraph there is read: Word shows nothing for it either.
    assert text_of(_merged_away(p(""))) == ["top", ""]


def test_a_horizontally_merged_away_cell_is_read_as_its_own_cell() -> None:
    # Word shows and prints the text of a legacy hMerge continue cell as its own paragraph.
    body = (
        "<w:tbl><w:tr><w:tc><w:tcPr><w:hMerge w:val='restart'/></w:tcPr>"
        + p(r("<w:t>left</w:t>"))
        + "</w:tc><w:tc><w:tcPr><w:hMerge/></w:tcPr>"
        + p(r("<w:t>merged away</w:t>"))
        + "</w:tc></w:tr></w:tbl>"
        + p(r("<w:t>after</w:t>"))
    )
    assert [(x.text, x.table) for x in read_docx(docx(body))] == [
        ("left", (0, 0, 0)),
        ("merged away", (0, 0, 1)),
        ("after", None),
    ]


_PLACEHOLDER = (
    '<w:sdtPr><w:placeholder><w:docPart w:val="X"/></w:placeholder><w:showingPlcHdr/></w:sdtPr>'
)


@pytest.mark.parametrize(
    "body",
    [
        p(
            r('<w:t xml:space="preserve">Strength: </w:t>')
            + f"<w:sdt>{_PLACEHOLDER}<w:sdtContent/></w:sdt>"
        ),
        p(r("<w:t>a</w:t>") + f"<w:sdt>{_PLACEHOLDER}<w:sdtContent><w:r/></w:sdtContent></w:sdt>"),
        p(r("<w:t>a</w:t>") + f"<w:sdt>{_PLACEHOLDER}</w:sdt>"),
        f"<w:sdt>{_PLACEHOLDER}<w:sdtContent><w:p/></w:sdtContent></w:sdt>"
        + p(r("<w:t>after</w:t>")),
    ],
    ids=["inline-empty", "inline-empty-run", "inline-no-content", "block-empty-paragraph"],
)
def test_an_empty_content_control_with_a_placeholder_part_is_refused(body: str) -> None:
    # Word shows the placeholder's building block, which the reader does not read.
    assert refusal(body) == "unsupported-element"
    # With no placeholder part Word shows nothing there, as the reader reads it.
    plain = body.replace(_PLACEHOLDER, "<w:sdtPr><w:showingPlcHdr/></w:sdtPr>")
    assert "".join(text_of(plain)) in ("Strength: ", "a", "after")


_HIDING = (
    '<w:style w:type="paragraph" w:styleId="HiddenBase"><w:rPr><w:vanish/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Shown"><w:basedOn w:val="HiddenBase"/>'
    '<w:rPr><w:vanish w:val="0"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="PS"><w:rPr><w:vanish/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="CS"><w:rPr><w:vanish/></w:rPr></w:style>'
)

# The paragraph mark says it is shown, so only the run is in question.
_SHOWN_MARK = '<w:rPr><w:vanish w:val="0"/></w:rPr>'


@pytest.mark.parametrize(
    "body",
    [
        # A nearer style turns vanish off: Word's toggle rule shows the tab.
        p(
            r("<w:t>1</w:t>", '<w:vanish w:val="0"/>') + r("<w:tab/>"),
            '<w:pStyle w:val="Shown"/>' + _SHOWN_MARK,
        ),
        # Paragraph and character styles both hide: they cancel, and Word shows the space.
        p(
            r("<w:t>10</w:t>", '<w:vanish w:val="0"/>')
            + r('<w:t xml:space="preserve"> </w:t>', '<w:rStyle w:val="CS"/>')
            + r("<w:t>mg</w:t>", '<w:vanish w:val="0"/>'),
            '<w:pStyle w:val="PS"/>' + _SHOWN_MARK,
        ),
        # The paragraph mark: hidden by any level, shown by Word's rule.
        p("", '<w:pStyle w:val="Shown"/>'),
    ],
    ids=["nearer-style-off", "two-kinds-cancel", "paragraph-mark"],
)
def test_hiding_that_words_toggle_rule_cancels_is_refused(body: str) -> None:
    # Word's case: show the shown text with hidden text off, and whether paragraphs run on.
    assert refusal(body, _HIDING) == "hidden-text"


def test_spec_vanish_on_a_text_run_does_not_hide_it() -> None:
    # Word shows and prints it: "Take not more than 2".
    body = p(
        r('<w:t xml:space="preserve">Take </w:t>')
        + r('<w:t xml:space="preserve">not </w:t>', "<w:specVanish/>")
        + r("<w:t>more than 2</w:t>")
    )
    assert text_of(body) == ["Take not more than 2"]


_RTL_STYLES = (
    '<w:style w:type="paragraph" w:styleId="RtlPara"><w:rPr><w:rtl/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="RtlChar"><w:rPr><w:rtl/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="BoldPara"><w:rPr><w:b/></w:rPr></w:style>'
)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        # An override reverses every character ("10 mg" drawn "gm 01"); an embedding does not.
        (p('<w:bdo w:val="rtl">' + r("<w:t>10 mg</w:t>") + "</w:bdo>"), "unsupported-element"),
        (p('<w:bdo w:val="ltr">' + r("<w:t>10 mg</w:t>") + "</w:bdo>"), "unsupported-element"),
        (p("<w:dir>" + r("<w:t>10 mg</w:t>") + "</w:dir>"), "unsupported-element"),
        (p('<w:dir w:val="up">' + r("<w:t>10 mg</w:t>") + "</w:dir>"), "unsupported-element"),
        # Word does not allow rtl in styles (MS-OI29500 17.7.5.4): not on record.
        (p(r("<w:t>a !? b</w:t>"), '<w:pStyle w:val="RtlPara"/>'), "unsupported-formatting"),
        (p(r("<w:t>a !? b</w:t>", '<w:rStyle w:val="RtlChar"/>')), "unsupported-formatting"),
    ],
)
def test_bidi_overrides_and_right_to_left_from_a_style_are_refused(body: str, code: str) -> None:
    assert refusal(body, _RTL_STYLES) == code


@pytest.mark.parametrize(
    "body",
    [
        p(r("<w:t>abc</w:t>", "<w:rtl/><w:b/>")),
        p(r("<w:t>abc</w:t>", "<w:rtl/><w:bCs/>")),
        p(r("<w:t>abc</w:t>", "<w:cs/><w:i/><w:bCs/>")),  # ECMA's own example
        p(r("<w:t>\u0645\u0631\u062d\u0628\u0627</w:t>", "<w:b/>")),  # Arabic, no rtl
        p(r("<w:t>\u05e9\u05dc\u05d5\u05dd</w:t>", "<w:iCs/>")),  # Hebrew
        p(r("<w:t>abc</w:t>", "<w:rtl/>"), '<w:pStyle w:val="BoldPara"/>'),
        p('<w:dir w:val="rtl">' + r("<w:t>abc</w:t>", "<w:b/>") + "</w:dir>"),
    ],
)
def test_complex_script_text_whose_two_emphasis_settings_differ_is_refused(body: str) -> None:
    # Word draws complex script with bCs and iCs, and its Font object reports b and i:
    # which is drawn is not on record.
    assert refusal(body, _RTL_STYLES) == "unsupported-formatting"


def test_complex_script_text_whose_emphasis_settings_agree_is_read() -> None:
    arabic = "\u0645\u0631\u062d\u0628\u0627"
    assert _kinds(p(r(f"<w:t>{arabic}</w:t>", "<w:b/><w:bCs/>"))) == [(0, 5, "bold")]
    assert _kinds(p(r("<w:t>abc</w:t>", "<w:rtl/><w:i/><w:iCs/>"))) == [
        (0, 3, "italic"),
        (0, 3, "rtl"),
    ]
    # Latin and Greek text is not complex script: w:b alone is bold.
    assert _kinds(p(r("<w:t>abc \u03b1</w:t>", "<w:b/>"))) == [(0, 5, "bold")]


_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"


@pytest.mark.parametrize(
    "fill", ["<w14:noFill/>", '<w14:solidFill><w14:srgbClr w14:val="FFFFFF"/></w14:solidFill>']
)
def test_text_filled_by_word_2010_text_effects_is_refused(fill: str) -> None:
    # Word 2010 and later draw such text with no fill, or white; ask Word whether it is seen.
    props = f'<w14:textFill xmlns:w14="{_W14}">{fill}</w14:textFill>'
    assert refusal(p(r("<w:t>do not exceed 4 tablets</w:t>", props))) == "unsupported-formatting"
    styles = f'<w:style w:type="character" w:styleId="Fx"><w:rPr>{props}</w:rPr></w:style>'
    styled = p(r("<w:t>do not exceed 4 tablets</w:t>", '<w:rStyle w:val="Fx"/>'))
    assert refusal(styled, styles) == "unsupported-formatting"


def _styled_table(look: str, rows: list[list[str]], props: str = "<w:b/><w:i/>") -> tuple[str, str]:
    """A table in style T, whose first row's formatting sets ``props``, and its styles."""
    style = (
        '<w:style w:type="table" w:styleId="T"><w:tblStylePr w:type="firstRow">'
        f"<w:rPr>{props}</w:rPr></w:tblStylePr></w:style>"
    )
    cells = "".join(
        "<w:tr>"
        + "".join(f"<w:tc>{p(r(f'<w:t>{c}</w:t>') if c else '')}</w:tc>" for c in row)
        + "</w:tr>"
        for row in rows
    )
    return f'<w:tbl><w:tblPr><w:tblStyle w:val="T"/>{look}</w:tblPr>{cells}</w:tbl>', style


@pytest.mark.parametrize(
    "look",
    [
        '<w:tblLook w:val="04A0"/>',  # Word's default: first row and first column on
        '<w:tblLook w:val="0000" w:firstRow="1"/>',
        "",  # no tblLook: what Word turns on is not on record
    ],
)
def test_conditional_emphasis_in_a_region_the_table_turns_on_is_refused(look: str) -> None:
    # Word applies firstRow b+i where tblLook turns the first row on, and nothing where off.
    body, styles = _styled_table(look, [["Frequency"], ["Very common"]])
    assert refusal(body, styles) == "unsupported-element"


def test_conditional_emphasis_is_read_where_the_table_turns_it_off_or_holds_no_text() -> None:
    body, styles = _styled_table('<w:tblLook w:val="0000"/>', [["Frequency"], ["Very common"]])
    assert [x.marks for x in read_docx(docx(body, styles))] == [(), ()]
    attributes = '<w:tblLook w:firstRow="0" w:lastRow="1" w:firstColumn="1" w:noHBand="1"/>'
    body, styles = _styled_table(attributes, [["Frequency"], ["Very common"]])
    assert text_of(body, styles) == ["Frequency", "Very common"]
    # Turned on over an empty first row: nothing there to mark.
    body, styles = _styled_table('<w:tblLook w:val="04A0"/>', [[""], ["Very common"]])
    assert text_of(body, styles) == ["", "Very common"]
    # A row's own look that turns the first row on counts.
    body, styles = _styled_table('<w:tblLook w:val="0000"/>', [["Frequency"], ["Very common"]])
    exception = '<w:tr><w:tblPrEx><w:tblLook w:val="0020"/></w:tblPrEx>'
    assert refusal(body.replace("<w:tr>", exception, 1), styles) == "unsupported-element"


def _cell(inner: str, shading: str = "") -> str:
    shd = f'<w:tcPr><w:shd w:val="clear" w:fill="{shading}"/></w:tcPr>' if shading else ""
    return f"<w:tbl><w:tr><w:tc>{shd}{inner}</w:tc></w:tr></w:tbl>"


@pytest.mark.parametrize(
    ("body", "faint"),
    [
        (p(r("<w:t>ab</w:t>", '<w:color w:val="FEFEFE"/>')), True),
        (p(r("<w:t>ab</w:t>", '<w:color w:val="FFFF00"/>')), True),  # Word's standard Yellow
        (p(r("<w:t>ab</w:t>", '<w:color w:val="FFF2CC"/>')), True),  # Gold, Lighter 80%
        # White, Background 1, Darker 50%: drawn mid-grey.
        (
            p(
                r(
                    "<w:t>ab</w:t>",
                    '<w:color w:val="7F7F7F" w:themeColor="background1" w:themeShade="80"/>',
                )
            ),
            False,
        ),
        (
            p(
                r("<w:t>ab</w:t>", '<w:color w:val="000000"/>'),
                '<w:shd w:val="clear" w:fill="000000"/>',
            ),
            True,
        ),
        (
            p(
                r(
                    "<w:t>ab</w:t>",
                    '<w:color w:val="000000"/><w:shd w:val="solid" w:color="111111"/>',
                )
            ),
            True,
        ),
        (p(r("<w:t>ab</w:t>", '<w:color w:val="FFFF00"/><w:highlight w:val="yellow"/>')), True),
        (p(r("<w:t>ab</w:t>", '<w:color w:val="FFFFFF"/><w:highlight w:val="black"/>')), False),
        (_cell(p(r("<w:t>ab</w:t>", '<w:color w:val="FFFFFF"/>')), "1F3864"), False),  # navy cell
        (_cell(p(r("<w:t>ab</w:t>", '<w:color w:val="000000"/>')), "000000"), True),
        (_cell(p(r("<w:t>ab</w:t>")), "000000"), False),  # automatic colour: Word draws it white
    ],
)
def test_faint_is_decided_by_contrast_with_what_is_under_the_text(body: str, faint: bool) -> None:
    assert ("faint" in [m.kind for m in read_docx(docx(body))[0].marks]) == faint


def test_faint_under_conditional_or_unknown_colours_is_refused() -> None:
    # A table style's first row shades black: under black text in it, or not, by tblLook.
    style = (
        '<w:style w:type="table" w:styleId="Dark"><w:tblStylePr w:type="firstRow">'
        '<w:tcPr><w:shd w:val="clear" w:fill="000000"/></w:tcPr></w:tblStylePr></w:style>'
    )
    cell = _cell(p(r("<w:t>ab</w:t>", '<w:color w:val="000000"/>')))
    table = cell.replace("<w:tbl>", '<w:tbl><w:tblPr><w:tblStyle w:val="Dark"/></w:tblPr>')
    assert refusal(table, style) == "unsupported-formatting"
    # A conditional colour faint on the page, by contrast as any other.
    near_white = _banded('<w:rPr><w:color w:val="FEFEFE"/></w:rPr>')
    assert refusal(_in_banded(r("<w:t>10</w:t>")), near_white) == "unsupported-element"
    for props in ('<w:color w:val="white"/>', '<w:color w:themeColor="accent1"/>'):
        assert refusal(p(r("<w:t>ab</w:t>", props))) == "unsupported-formatting"


def _row(height: str, inner: str) -> str:
    return f"<w:tbl><w:tr><w:trPr>{height}</w:trPr><w:tc>{inner}</w:tc></w:tr></w:tbl>"


TEN_POINTS = '<w:sz w:val="20"/>'


@pytest.mark.parametrize(
    "body",
    [
        # A row of exact height lower than its text: Word clips it.
        _row('<w:trHeight w:hRule="exact" w:val="20"/>', p(r("<w:t>2 tablets</w:t>"))),
        _row(
            '<w:trHeight w:hRule="exact" w:val="240"/>',
            p(r("<w:t>2 tablets</w:t>", TEN_POINTS)) + p(r("<w:t>daily</w:t>", TEN_POINTS)),
        ),
        # Exact line spacing lower than the text, and lines set to overprint.
        p(r("<w:t>2 tablets</w:t>"), '<w:spacing w:line="20" w:lineRule="exact"/>'),
        p(r("<w:t>2 tablets</w:t>"), '<w:spacing w:line="100"/>'),
        # A frame far off the page, or too low for its text.
        p(r("<w:t>2 tablets</w:t>"), '<w:framePr w:x="-20000" w:y="0"/>'),
        p(r("<w:t>2 tablets</w:t>"), '<w:framePr w:h="40" w:hRule="exact"/>'),
        # A floating table off the page; a table or paragraph indented far outward.
        '<w:tbl><w:tblPr><w:tblpPr w:tblpX="-30000"/></w:tblPr><w:tr><w:tc>'
        + p(r("<w:t>2 tablets</w:t>"))
        + "</w:tc></w:tr></w:tbl>",
        '<w:tbl><w:tblPr><w:tblInd w:w="-4000" w:type="dxa"/></w:tblPr><w:tr><w:tc>'
        + p(r("<w:t>2 tablets</w:t>"))
        + "</w:tc></w:tr></w:tbl>",
        p(r("<w:t>2 tablets</w:t>"), '<w:ind w:left="-40000"/>'),
        p(r("<w:t>2 tablets</w:t>"), '<w:ind w:left="0" w:hanging="3000"/>'),
        # Characters drawn over one another.
        p(r("<w:t>2 tablets</w:t>", TEN_POINTS + '<w:spacing w:val="-400"/>')),
        p(r("<w:t>2 tablets</w:t>", '<w:fitText w:val="100" w:id="1"/>')),
    ],
)
def test_layout_that_may_clip_or_overdraw_text_is_refused(body: str) -> None:
    # Word's cases: each body above, saved as PDF; is every character drawn whole and apart?
    assert refusal(body) == "unsupported-formatting"


def test_layout_within_bounds_is_read() -> None:
    for body in (
        _row('<w:trHeight w:hRule="exact" w:val="240"/>', p(r("<w:t>2 tablets</w:t>", TEN_POINTS))),
        _row('<w:trHeight w:hRule="exact" w:val="20"/>', p("")),  # nothing to clip
        p(r("<w:t>2 tablets</w:t>", TEN_POINTS), '<w:spacing w:line="240" w:lineRule="exact"/>'),
        p(r("<w:t>2 tablets</w:t>"), '<w:spacing w:line="120" w:lineRule="atLeast"/>'),
        p(r("<w:t>2 tablets</w:t>"), '<w:framePr w:wrap="around" w:hAnchor="margin" w:y="273"/>'),
        p(r("<w:t>2 tablets</w:t>"), '<w:ind w:left="-18" w:right="-595"/>'),
        p(r("<w:t>2 tablets</w:t>", TEN_POINTS + '<w:spacing w:val="-31"/>')),
    ):
        assert text_of(body) in (["2 tablets"], [""])
