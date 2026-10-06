"""The .docx reader: exact text, or a refusal with a reason.

Most cases build a minimal .docx in memory, so each rule is tested in isolation. The last group
reads the four pinned EMA files in corpus/ema-qrd, which is where the rules were found.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest

from label_docx import read as served
from label_docx.certify import CertificationError, DocxSource
from label_docx.reader import (
    REASONS,
    Anchored,
    Document,
    DocxRefusedError,
    NoteReference,
    Numbering,
    Table,
    TableCell,
    TableGrid,
    TableRow,
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
        p(r(_alternate(textbox.replace("wp:anchor", "wp:inline"), LINE))),  # a text box in line
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
    # It states no grid (tblGrid): it is read, and its grid is not reported.
    assert read_document(docx(body, numbering=NUMBERING)).tables == (Table(None, None, "no-grid"),)


# --- table grids (docx-reader/1.27.0) ---------------------------------------------------------

# The grid of a one-column table.
GRID1 = '<w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid>'


def tbl(columns: int | None, rows: str) -> str:
    """A table of ``columns`` grid columns (None: no ``tblGrid``) holding ``rows``."""
    grid = (
        "" if columns is None else f"<w:tblGrid>{'<w:gridCol w:w="1000"/>' * columns}</w:tblGrid>"
    )
    return f"<w:tbl>{grid}{rows}</w:tbl>"


def tc(text: str, props: str = "") -> str:
    """A cell of one paragraph (empty for ``text`` ""), with ``props`` as its ``tcPr``."""
    return (
        f"<w:tc>{f'<w:tcPr>{props}</w:tcPr>' if props else ''}"
        f"{p(r(f'<w:t>{text}</w:t>')) if text else p('')}</w:tc>"
    )


def test_a_body_tables_grid_is_reported_with_spans_merges_and_columns_left_out() -> None:
    restart = '<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/>'
    continued = '<w:gridSpan w:val="2"/><w:vMerge/>'
    body = tbl(
        3,
        f"<w:tr>{tc('wide', restart)}{tc('b')}</w:tr>"
        f"<w:tr>{tc('', continued)}{tc('c')}</w:tr>"
        '<w:tr><w:trPr><w:gridBefore w:val="1"/><w:gridAfter w:val="1"/></w:trPr>'
        f"{tc('d')}</w:tr>",
    )
    document = read_document(docx(body + p(r("<w:t>after</w:t>"))))
    assert [(x.text, x.table) for x in document.body] == [
        ("wide", (0, 0, 0)),
        ("b", (0, 0, 1)),
        ("", (0, 1, 0)),
        ("c", (0, 1, 1)),
        ("d", (0, 2, 0)),
        ("after", None),
    ]
    rows = (
        TableRow(0, 0, (TableCell(0, 2, "restart"), TableCell(2, 1, None))),
        TableRow(0, 0, (TableCell(0, 2, "continue"), TableCell(2, 1, None))),
        TableRow(1, 1, (TableCell(1, 1, None),)),
    )
    assert document.tables == (Table(None, TableGrid(3, rows), None),)


def test_a_nested_table_is_its_own_table_and_its_paragraphs_carry_it() -> None:
    inner = tbl(2, f"<w:tr>{tc('x')}{tc('y')}</w:tr>")
    outer = tbl(
        1, f"<w:tr><w:tc>{p(r('<w:t>out</w:t>'))}{inner}{p(r('<w:t>back</w:t>'))}</w:tc></w:tr>"
    )
    document = read_document(docx(outer + tbl(1, f"<w:tr>{tc('next')}</w:tr>")))
    assert [(x.text, x.table) for x in document.body] == [
        ("out", (0, 0, 0)),
        ("x", (1, 0, 0)),
        ("y", (1, 0, 1)),
        ("back", (0, 0, 0)),
        ("next", (2, 0, 0)),
    ]
    assert [(t.parent, t.grid and t.grid.columns) for t in document.tables] == [
        (None, 1),
        ((0, 0, 0), 2),
        (None, 1),
    ]


def _span(value: str) -> str:
    return f'<w:gridSpan w:val="{value}"/>'


_BEFORE = '<w:trPr><w:gridBefore w:val="-1"/></w:trPr>'
_AFTER = '<w:trPr><w:gridAfter w:val="{}"/></w:trPr>'
_H_MERGE = '<w:hMerge w:val="restart"/>'
# Each reason a grid is not reported, the first one found, and a table that has it.
_NO_GRID = {
    "no-grid": ("no-grid", tbl(None, f"<w:tr>{tc('a')}</w:tr>")),
    "two-grids": ("two-grids", tbl(1, f"{GRID1}<w:tr>{tc('a')}</w:tr>")),
    "not-a-number": ("bad-number", tbl(1, f"<w:tr>{tc('a', _span('one'))}</w:tr>")),
    "negative": ("bad-number", tbl(2, f"<w:tr>{_BEFORE}{tc('a', _span('3'))}</w:tr>")),
    # Two cells that would fill the grid: where Word draws a legacy merge is not on record.
    "h-merge": ("h-merge", tbl(2, f"<w:tr>{tc('a', _H_MERGE)}{tc('', '<w:hMerge/>')}</w:tr>")),
    "bad-merge": ("bad-merge", tbl(1, f"<w:tr>{tc('a', '<w:vMerge w:val="down"/>')}</w:tr>")),
    "bad-span": ("bad-span", tbl(1, f"<w:tr>{tc('a', _span('0'))}{tc('b')}</w:tr>")),
    "short": ("row-off-grid", tbl(2, f"<w:tr>{tc('a')}</w:tr>")),
    "long": ("row-off-grid", tbl(1, f"<w:tr>{tc('a')}{tc('b')}</w:tr>")),
    "after": ("row-off-grid", tbl(2, f"<w:tr>{_AFTER.format(2)}{tc('a')}</w:tr>")),
    # The first in order: the row's counts before its cells, a cell's merge before its span.
    "row-count-first": (
        "bad-number",
        tbl(1, f"<w:tr>{_AFTER.format('x')}{tc('a', _H_MERGE)}</w:tr>"),
    ),
    "merge-first": ("h-merge", tbl(1, f"<w:tr>{tc('a', _H_MERGE + _span('0'))}</w:tr>")),
}


@pytest.mark.parametrize(("reason", "table"), _NO_GRID.values(), ids=_NO_GRID.keys())
def test_a_grid_word_lays_out_by_rules_not_on_record_is_not_reported_and_the_text_is_read(
    reason: str, table: str
) -> None:
    document = read_document(docx(table + p(r("<w:t>after</w:t>"))))
    assert (document.body[0].text, document.body[-1].text) == ("a", "after")
    assert document.tables == (Table(None, None, reason),)
    assert reason in REASONS


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
#
# Their text is checked through any(...) and counts, whose failures pytest shows as an outcome
# alone: a failed `x in texts` would print every paragraph (tests/test_no_label_leak.py).


def _read(name: str) -> list[str]:
    return [paragraph.text for paragraph in read_docx((SOURCES / name).read_bytes())]


def test_every_pinned_source_reads_without_refusal() -> None:
    for path in sorted(SOURCES.glob("*.docx")):
        paragraphs = read_docx(path.read_bytes())
        assert paragraphs, path.name


def test_appendix_ii_keeps_the_greater_than_or_equal_signs() -> None:
    texts = _read(
        "qrd-appendix-ii-medical-dictionary-regulatory-activities-terminology-be-used-section-48"
        "-undesirable-effects-summary-product-characteristics_en.docx"
    )
    assert any(text == "<Very common (\u2265\u00a01/10)>" for text in texts)
    assert sum(text.count("\u2265") for text in texts) == 4


def test_appendix_iii_keeps_the_degree_signs() -> None:
    texts = _read(
        "qrd-appendix-iii-quality-review-documents-templates-human-medicinal-products_en.docx"
    )
    assert any(text == "<Store below <25\u00a0\u00b0C> <30\u00a0\u00b0C>.>" for text in texts)
    assert sum(text.count("\u00b0") for text in texts) == 21


def test_the_template_keeps_its_symbol_font_braces_and_its_own_drift() -> None:
    data = (SOURCES / "qrd-product-information-template-version-104_en.docx").read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        xml = package.read("word/document.xml").decode("utf-8")
    # Four opening and four closing braces are stored as Symbol-font glyphs, not as text.
    assert (xml.count('w:char="F07B"'), xml.count('w:char="F07D"')) == (4, 4)
    texts = [paragraph.text for paragraph in read_docx(data)]
    # They are the Czech local representative's placeholders; a text-only reader shows "Nazev".
    assert any(text == "{N\u00e1zev}" for text in texts)
    assert any(text == "CZ {m\u011bsto}>" for text in texts)
    assert any(text == "Tel: +{telefonn\u00ed \u010d\u00edslo}" for text in texts)
    # EMA's own drift: the Polish representative's "<{Adres:" never closes its brace.
    assert any(text == "<{Adres:" for text in texts)
    # "5.1" is followed by a space and then a tab in EMA's file; the reader keeps both.
    assert any(text == "5.1 \tPharmacodynamic properties" for text in texts)
    # The black triangle of the additional-monitoring statement is a picture: two paragraphs,
    # each starting so (zip(strict=True) refuses any other number).
    triangle = [text for text in texts if "\ufffc" in text]
    starts = (
        "<\ufffcThis medicinal product is subject to additional",
        "<\ufffcThis medicine is subject to additional monitoring",
    )
    assert [t.startswith(s) for t, s in zip(triangle, starts, strict=True)] == [True, True]


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
    # shows no bold or italic there.
    off = _in_banded(r("<w:t>10</w:t>")).replace(
        "</w:tblPr>", '<w:tblLook w:val="0000"/></w:tblPr>'
    )
    (paragraph,) = read_docx(docx(off, harmless))
    assert (paragraph.text, paragraph.marks) == ("10", ())
    # With no look, Word turns the first row on and applies its bold and italic there.
    (paragraph,) = read_docx(docx(_in_banded(r("<w:t>10</w:t>")), harmless))
    assert [m.kind for m in paragraph.marks] == ["bold", "italic"]


@pytest.mark.parametrize(
    "conditional",
    [
        '<w:rPr><w:color w:val="FFFFFF"/></w:rPr>',
        '<w:rPr><w:sz w:val="2"/></w:rPr>',
        '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>',
        '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr>',
        "<w:rPr><w:smallCaps/></w:rPr>",
        '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>',
        '<w:rPr><w:u w:val="single"/></w:rPr>',
        "<w:rPr><w:dstrike/></w:rPr>",
        '<w:rPr><w:highlight w:val="yellow"/></w:rPr>',
        "<w:rPr><w:vanish/></w:rPr>",
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


# --- what a picture stands for ("Pictures" in the reader's docstring) ------------------------

PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
DPI = "{28A0092B-C50C-407E-A947-70E740481C1C}"
A14 = "http://schemas.microsoft.com/office/drawing/2010/main"


def chunk(kind: bytes, body: bytes) -> bytes:
    """A PNG chunk with its length and CRC."""
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


# The samples a pixel of each PNG colour type has: grey, RGB, palette index, grey and alpha,
# RGB and alpha.
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def png(
    width: int = 2,
    height: int = 1,
    extra: bytes = b"",
    depth: int = 8,
    colour: int = 2,
    interlace: int = 0,
    end: bool = True,
) -> bytes:
    """A real PNG of ``width`` by ``height`` black pixels of colour type ``colour`` (a palette
    image needs a PLTE in ``extra``), ``extra`` chunks after IHDR, IEND unless not ``end``."""
    row = (width * depth * CHANNELS.get(colour, 1) + 7) // 8
    rows = b"".join(b"\x00" + bytes(row) for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + extra
        + chunk(b"IDAT", zlib.compress(rows))
        + (chunk(b"IEND", b"") if end else b"")
    )


def segment(marker: int, body: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body


def jpeg(
    width: int = 1,
    height: int = 1,
    frame: int = 0xC0,
    extra: bytes = b"",
    precision: int = 8,
    parts: int = 1,
    specs: int | None = None,
) -> bytes:
    """A real baseline JPEG of one grey 8x8 block, ``width`` by ``height`` (``extra`` first),
    its frame naming ``parts`` components and holding ``specs`` of them (all by default)."""
    huffman = bytes([1] + [0] * 15) + b"\x00"  # one code of one bit, for symbol 0
    count = parts if specs is None else specs
    components = b"".join(bytes((i + 1, 0x11, 0)) for i in range(count))
    return (
        b"\xff\xd8"
        + extra
        + segment(0xDB, b"\x00" + b"\x01" * 64)
        + segment(frame, struct.pack(">BHHB", precision, height, width, parts) + components)
        + segment(0xC4, b"\x00" + huffman)
        + segment(0xC4, b"\x10" + huffman)
        + segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
        + b"\x3f\xff\xd9"
    )


def exif(orientation: int, order: str = "MM", before: int = 0, tight: bool = False) -> bytes:
    """An APP1 Exif body: one IFD with ``before`` other tags and then the orientation tag, and
    the next IFD's offset after it unless ``tight``."""
    pack = ">" if order == "MM" else "<"
    tiff = order.encode() + struct.pack(pack + "HI", 42, 8) + struct.pack(pack + "H", before + 1)
    tiff += struct.pack(pack + "HHIHH", 0x0100, 3, 1, 9, 0) * before
    tiff += struct.pack(pack + "HHIHH", 0x0112, 3, 1, orientation, 0)
    return b"Exif\x00\x00" + tiff + (b"" if tight else struct.pack(pack + "I", 0))


def tiff_png(tiff: bytes) -> bytes:
    """A PNG whose eXIf chunk holds ``tiff``."""
    return png(extra=chunk(b"eXIf", tiff))


def picture(
    blip: str = 'r:embed="rIdImg"',
    inside: str = "",
    crop: str = "",
    shape: str = '<a:xfrm><a:off x="0" y="0"/><a:ext cx="19050" cy="9525"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln>',
    extent: str = 'cx="19050" cy="9525"',
    effect: str = 'l="0" t="0" r="0" b="0"',
    fill: str = "<a:stretch><a:fillRect/></a:stretch>",
) -> str:
    """An in-line DrawingML picture as Word writes it, each part replaceable."""
    return (
        f"<w:drawing><wp:inline xmlns:wp='{WP}'><wp:extent {extent}/><wp:effectExtent {effect}/>"
        f"<wp:docPr id='1' name='Picture 1'/><a:graphic xmlns:a='{A}'>"
        f"<a:graphicData uri='{PIC}'><pic:pic xmlns:pic='{PIC}' xmlns:r='{REL}'>"
        "<pic:nvPicPr><pic:cNvPr id='1' name='p'/><pic:cNvPicPr/></pic:nvPicPr>"
        f"<pic:blipFill><a:blip {blip}>{inside}</a:blip>{crop}{fill}</pic:blipFill>"
        f"<pic:spPr bwMode='auto'>{shape}</pic:spPr></pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing>"
    )


def relationship(
    target: str = "media/image1.png", rid: str = "rIdImg", kind: str = "image", mode: str = ""
) -> str:
    return (
        f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{target}"'
        f"{f' TargetMode={chr(34)}{mode}{chr(34)}' if mode else ''}/>"
    )


def with_media(data: bytes, media: dict[str, bytes], rels: dict[str, str] | None = None) -> bytes:
    """``data`` with ``media`` parts added, and ``rels`` (by .rels part) added or made."""
    rels = {"word/_rels/document.xml.rels": relationship()} if rels is None else rels
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename in rels:
                added = rels[info.filename].encode()
                content = content.replace(b"</Relationships>", added + b"</Relationships>")
            target.writestr(info, content)
        for name, made in rels.items():
            if name not in source.namelist():
                target.writestr(
                    name, f'<Relationships xmlns="{PACKAGE_RELS}">{made}</Relationships>'
                )
        for name, image in media.items():
            target.writestr(name, image)
    return out.getvalue()


def read_pictures(run: str, media: dict[str, bytes], rels: dict[str, str] | None = None) -> Any:
    """The served result of "a", ``run``, "b" with ``media``: certified, so never refused."""
    value = json.loads(
        served(with_media(docx(p(r("<w:t>a</w:t>") + r(run) + r("<w:t>b</w:t>"))), media, rels))[0]
    )
    assert "refusal" not in value, value.get("refusal")
    assert [q["text"] for q in value["paragraphs"]] == ["a￼b"]
    return value


PNG = png()
FILLED = picture()
IMAGE = "word/media/image1.png"
MEDIA = {IMAGE: PNG}
IHDR = struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)
PALETTE = png(colour=3)
PLTE = chunk(b"PLTE", bytes(3))
VML = VML_PICTURE.replace('xmlns:r="urn:r" r:id="rId8"', f'xmlns:r="{REL}" r:id="rIdImg"')
TURNED = jpeg(extra=segment(0xE1, exif(6)))
# Each picture the reader cannot vouch for, its media and relationships, and the reason it gives.
PICTURE_CASES: list[tuple[str, str, dict[str, bytes], dict[str, str] | None, str | None]] = [
    ("plain", picture(), MEDIA, None, None),
    ("jpeg", picture(), {"word/media/image1.png": jpeg()}, None, None),
    (
        "upright-exif",
        picture(),
        {"word/media/image1.png": jpeg(extra=segment(0xE1, exif(1, "II")))},
        None,
        None,
    ),
    (
        "useLocalDpi",
        picture(
            inside=f'<a:extLst><a:ext uri="{DPI}"><a14:useLocalDpi xmlns:a14="{A14}" val="0"/>'
            "</a:ext></a:extLst>"
        ),
        MEDIA,
        None,
        None,
    ),
    ("uncropped", picture(crop="<a:srcRect/>"), MEDIA, None, None),
    ("shape", _alternate(SHAPE.replace("wp:anchor", "wp:inline"), LINE), {}, {}, "shape"),
    ("vml", VML, MEDIA, None, "vml"),
    ("linked", picture(blip='r:embed="rIdImg" r:link="rIdImg"'), MEDIA, None, "linked"),
    ("no-embed", picture(blip=""), MEDIA, None, "no-part"),
    ("no-relationship", picture(), MEDIA, {}, "no-part"),
    (
        "external",
        picture(),
        MEDIA,
        {"word/_rels/document.xml.rels": relationship(mode="External")},
        "no-part",
    ),
    (
        "not-an-image",
        picture(),
        MEDIA,
        {"word/_rels/document.xml.rels": relationship(kind="oleObject")},
        "no-part",
    ),
    ("no-such-part", picture(), {}, None, "no-part"),
    ("other-case", picture(), {"word/media/Image1.png": PNG}, None, "no-part"),
    ("no-pic", PICTURE, MEDIA, None, "no-part"),
    (
        "gif",
        picture(),
        {"word/media/image1.png": b"GIF89a\x01\x00\x01\x00"},
        None,
        "not-png-or-jpeg",
    ),
    ("bad-crc", picture(), {"word/media/image1.png": PNG[:-1] + b"\x00"}, None, "bad-image-header"),
    ("truncated", picture(), {"word/media/image1.png": PNG[:40]}, None, "bad-image-header"),
    ("bad-depth", picture(), {"word/media/image1.png": png(depth=4)}, None, "bad-image-header"),
    (
        "no-idat",
        picture(),
        {"word/media/image1.png": PNG[:33] + chunk(b"IEND", b"")},
        None,
        "bad-image-header",
    ),
    (
        "lossless-jpeg",
        picture(),
        {"word/media/image1.png": jpeg(frame=0xC3)},
        None,
        "bad-image-header",
    ),
    (
        "no-frame",
        picture(),
        {"word/media/image1.png": b"\xff\xd8\xff\xda\x00\x02"},
        None,
        "bad-image-header",
    ),
    (
        "bad-exif",
        picture(),
        {"word/media/image1.png": jpeg(extra=segment(0xE1, b"Exif\x00\x00XX"))},
        None,
        "bad-image-header",
    ),
    (
        "animated",
        picture(),
        {"word/media/image1.png": png(extra=chunk(b"acTL", bytes(8)))},
        None,
        "animated",
    ),
    ("turned-jpeg", picture(), {"word/media/image1.png": TURNED}, None, "orientation"),
    (
        "turned-png",
        picture(),
        {"word/media/image1.png": png(extra=chunk(b"eXIf", exif(3)[6:]))},
        None,
        "orientation",
    ),
    ("bad-extent", picture(extent='cx="1.5" cy="9525"'), MEDIA, None, "bad-number"),
    ("bad-crop", picture(crop='<a:srcRect l="10%"/>'), MEDIA, None, "bad-number"),
    ("bad-flip", picture(shape='<a:xfrm flipH="yes"/>'), MEDIA, None, "bad-number"),
    ("cropped", picture(crop='<a:srcRect b="25000"/>'), MEDIA, None, "cropped"),
    ("rotated", picture(shape='<a:xfrm rot="5400000"/>'), MEDIA, None, "rotated"),
    ("flipped", picture(shape='<a:xfrm flipV="1"/>'), MEDIA, None, "flipped"),
    ("recoloured", picture(inside="<a:grayscl/>"), MEDIA, None, "effects"),
    ("transparent", picture(inside='<a:alphaModFix amt="50000"/>'), MEDIA, None, "effects"),
    (
        "svg",
        picture(
            inside='<a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}"/></a:extLst>'
        ),
        MEDIA,
        None,
        "effects",
    ),
    ("tiled", picture(fill="<a:tile/>"), MEDIA, None, "effects"),
    (
        "outlined",
        picture(shape='<a:ln w="9525"><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:ln>'),
        MEDIA,
        None,
        "effects",
    ),
    ("ellipse", picture(shape='<a:prstGeom prst="ellipse"/>'), MEDIA, None, "effects"),
    ("shadow", picture(shape="<a:effectLst><a:outerShdw/></a:effectLst>"), MEDIA, None, "effects"),
    ("moved", picture(shape='<a:xfrm><a:off x="5" y="0"/></a:xfrm>'), MEDIA, None, "effects"),
    ("resized", picture(shape='<a:xfrm><a:ext cx="1" cy="1"/></a:xfrm>'), MEDIA, None, "effects"),
    ("effect-extent", picture(effect='l="0" t="0" r="9525" b="0"'), MEDIA, None, "effects"),
    ("hidden", picture().replace("name='p'/>", "name='p' hidden='1'/>"), MEDIA, None, "effects"),
    # Headers browsers and Word may read otherwise, or not at all.
    ("palette-without-plte", picture(), {IMAGE: PALETTE}, None, "bad-image-header"),
    ("palette", picture(), {IMAGE: png(extra=chunk(b"PLTE", bytes(3)), colour=3)}, None, None),
    (
        "unknown-critical",
        picture(),
        {IMAGE: png(extra=chunk(b"ABCD", b"x"))},
        None,
        "bad-image-header",
    ),
    ("second-ihdr", picture(), {IMAGE: png(extra=chunk(b"IHDR", IHDR))}, None, "bad-image-header"),
    ("not-letters", picture(), {IMAGE: png(extra=chunk(b"ab1d", b""))}, None, "bad-image-header"),
    ("after-iend", picture(), {IMAGE: PNG + b"x"}, None, "bad-image-header"),
    ("too-wide", picture(), {IMAGE: png(width=10_001)}, None, "bad-image-header"),
    ("jpeg-12-bit", picture(), {IMAGE: jpeg(precision=12)}, None, "bad-image-header"),
    ("jpeg-2-components", picture(), {IMAGE: jpeg(parts=2)}, None, "bad-image-header"),
    ("jpeg-too-high", picture(), {IMAGE: jpeg(height=10_001)}, None, "bad-image-header"),
    (
        "jpeg-two-frames",
        picture(),
        {IMAGE: jpeg(extra=segment(0xC0, bytes(9)))},
        None,
        "bad-image-header",
    ),
    # Colour Chrome manages and Word's handling of is not on record.
    ("iccp", picture(), {IMAGE: png(extra=chunk(b"iCCP", b"p\x00\x00x"))}, None, "colour"),
    ("chrm", picture(), {IMAGE: png(extra=chunk(b"cHRM", bytes(32)))}, None, "colour"),
    ("gama-alone", picture(), {IMAGE: png(extra=chunk(b"gAMA", bytes(4)))}, None, "colour"),
    (
        "gama-srgb",
        picture(),
        {IMAGE: png(extra=chunk(b"sRGB", b"\x00") + chunk(b"gAMA", bytes(4)))},
        None,
        None,
    ),
    (
        "jpeg-icc",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE2, b"ICC_PROFILE\x00\x01\x01"))},
        None,
        "colour",
    ),
    ("jpeg-cmyk", picture(), {IMAGE: jpeg(parts=4)}, None, "colour"),
    # One check at a time, each alone in a picture: what each part of the closed list holds.
    (
        "fill-attribute",
        FILLED.replace("<pic:blipFill>", '<pic:blipFill dpi="96">'),
        MEDIA,
        None,
        "effects",
    ),
    (
        "fill-extra-child",
        picture(fill="<a:stretch><a:fillRect/></a:stretch><a:tile/>"),
        MEDIA,
        None,
        "effects",
    ),
    (
        "stretch-attribute",
        picture(fill='<a:stretch x="1"><a:fillRect/></a:stretch>'),
        MEDIA,
        None,
        "effects",
    ),
    (
        "fill-rect-inset",
        picture(fill='<a:stretch><a:fillRect l="1"/></a:stretch>'),
        MEDIA,
        None,
        "effects",
    ),
    (
        "fill-rect-child",
        picture(fill="<a:stretch><a:fillRect><a:x/></a:fillRect></a:stretch>"),
        MEDIA,
        None,
        "effects",
    ),
    ("blip-attribute", picture(blip='r:embed="rIdImg" x="1"'), MEDIA, None, "effects"),
    ("blip-cstate", picture(blip='r:embed="rIdImg" cstate="print"'), MEDIA, None, None),
    (
        "other-extension",
        picture(
            inside=f'<a:extLst><a:ext uri="{{X}}"><a14:useLocalDpi xmlns:a14="{A14}"/>'
            "</a:ext></a:extLst>"
        ),
        MEDIA,
        None,
        "effects",
    ),
    (
        "empty-dpi-extension",
        picture(inside=f'<a:extLst><a:ext uri="{DPI}"/></a:extLst>'),
        MEDIA,
        None,
        "effects",
    ),
    ("crop-child", picture(crop="<a:srcRect><a:x/></a:srcRect>"), MEDIA, None, "effects"),
    ("crop-attribute", picture(crop='<a:srcRect x="1"/>'), MEDIA, None, "effects"),
    ("crop-all-zero", picture(crop='<a:srcRect l="0" t="0" r="0" b="0"/>'), MEDIA, None, None),
    (
        "crop-each-side",
        picture(crop='<a:srcRect l="1" t="2" r="3" b="4"/>'),
        MEDIA,
        None,
        "cropped",
    ),
    ("crop-outward", picture(crop='<a:srcRect l="-500"/>'), MEDIA, None, "cropped"),
    (
        "no-shape-properties",
        re.sub("<pic:spPr.*</pic:spPr>", "", picture()),
        MEDIA,
        None,
        "effects",
    ),
    (
        "pic-extension",
        picture().replace("</pic:spPr>", "</pic:spPr><pic:extLst/>"),
        MEDIA,
        None,
        "effects",
    ),
    (
        "hidden-true",
        picture().replace("name='p'/>", "name='p' hidden='true'/>"),
        MEDIA,
        None,
        "effects",
    ),
    ("no-blip", FILLED.replace('<a:blip r:embed="rIdImg"></a:blip>', ""), MEDIA, None, "no-part"),
    ("xfrm-attribute", picture(shape='<a:xfrm x="1"/>'), MEDIA, None, "effects"),
    (
        "xfrm-two-offsets",
        picture(shape='<a:xfrm><a:off x="0" y="0"/><a:off x="0" y="0"/></a:xfrm>'),
        MEDIA,
        None,
        "effects",
    ),
    ("xfrm-all-zero", picture(shape='<a:xfrm rot="0" flipH="0" flipV="0"/>'), MEDIA, None, None),
    (
        "xfrm-child",
        picture(shape='<a:xfrm><a:chOff x="0" y="0"/></a:xfrm>'),
        MEDIA,
        None,
        "effects",
    ),
    ("rotated-back", picture(shape='<a:xfrm rot="-5400000"/>'), MEDIA, None, "rotated"),
    ("flipped-true", picture(shape='<a:xfrm flipH="true"/>'), MEDIA, None, "flipped"),
    ("unflipped-false", picture(shape='<a:xfrm flipH="false" flipV="false"/>'), MEDIA, None, None),
    ("moved-back", picture(shape='<a:xfrm><a:off x="-1" y="0"/></a:xfrm>'), MEDIA, None, "effects"),
    (
        "negative-size",
        picture(shape='<a:xfrm><a:ext cx="-1" cy="9525"/></a:xfrm>'),
        MEDIA,
        None,
        "bad-number",
    ),
    (
        "adjusted-rect",
        picture(shape='<a:prstGeom prst="rect"><a:avLst><a:gd name="a"/></a:avLst></a:prstGeom>'),
        MEDIA,
        None,
        "effects",
    ),
    ("no-fill-child", picture(shape="<a:noFill><a:x/></a:noFill>"), MEDIA, None, "effects"),
    (
        "line-no-fill-attribute",
        picture(shape='<a:ln><a:noFill x="1"/></a:ln>'),
        MEDIA,
        None,
        "effects",
    ),
    ("negative-extent", picture(extent='cx="-1" cy="9525"'), MEDIA, None, "bad-number"),
    ("negative-effect-extent", picture(effect='l="-1" t="0" r="0" b="0"'), MEDIA, None, "effects"),
    (
        "absolute-target",
        picture(),
        MEDIA,
        {"word/_rels/document.xml.rels": relationship("/word/media/image1.png")},
        None,
    ),
    # Every colour type and bit depth PNG allows, and some it does not.
    *(
        (
            f"png-{colour}-{depth}",
            picture(),
            {IMAGE: png(1, 1, PLTE if colour == 3 else b"", depth, colour)},
            None,
            None,
        )
        for colour, depths in (
            (0, (1, 2, 4, 8, 16)),
            (2, (8, 16)),
            (3, (1, 2, 4, 8)),
            (4, (8, 16)),
            (6, (8, 16)),
        )
        for depth in depths
    ),
    *(
        (
            f"png-{colour}-{depth}",
            picture(),
            {IMAGE: png(1, 1, PLTE if colour == 3 else b"", depth, colour)},
            None,
            "bad-image-header",
        )
        for colour, depth in ((0, 3), (2, 4), (3, 16), (4, 4), (5, 8), (6, 1))
    ),
    ("png-widest", picture(), {IMAGE: png(10_000, 1)}, None, None),
    ("png-highest", picture(), {IMAGE: png(1, 10_000)}, None, None),
    ("png-interlaced", picture(), {IMAGE: png(1, 1, interlace=1)}, None, None),
    ("png-interlace-2", picture(), {IMAGE: png(1, 1, interlace=2)}, None, "bad-image-header"),
    (
        "png-long-ihdr",
        picture(),
        {IMAGE: PNG.replace(chunk(b"IHDR", IHDR), chunk(b"IHDR", IHDR + b"\x00"))},
        None,
        "bad-image-header",
    ),
    (
        "png-critical-z",
        picture(),
        {IMAGE: png(extra=chunk(b"Zzzz", b""))},
        None,
        "bad-image-header",
    ),
    ("png-ancillary-a", picture(), {IMAGE: png(extra=chunk(b"abcd", b""))}, None, None),
    (
        "png-two-exif",
        picture(),
        {IMAGE: png(extra=chunk(b"eXIf", exif(1)[6:]) * 2)},
        None,
        "bad-image-header",
    ),
    ("png-exif-tight", picture(), {IMAGE: tiff_png(exif(3, tight=True)[6:])}, None, "orientation"),
    (
        "png-exif-short-entry",
        picture(),
        {IMAGE: tiff_png(exif(3, tight=True)[6:-4])},
        None,
        "bad-image-header",
    ),
    ("jpeg-restarts", picture(), {IMAGE: jpeg(extra=b"\xff\xd1\xff\x01")}, None, None),
    (
        "jpeg-scan-at-end",
        picture(),
        {IMAGE: jpeg()[: jpeg().index(b"\xff\xda")] + b"\xff\xda\x00\x02"},
        None,
        None,
    ),
    (
        "jpeg-second-soi",
        picture(),
        {IMAGE: jpeg(extra=b"\xff\xd8\x00\x02")},
        None,
        "bad-image-header",
    ),
    ("jpeg-extended", picture(), {IMAGE: jpeg(frame=0xC1)}, None, None),
    ("jpeg-progressive", picture(), {IMAGE: jpeg(frame=0xC2)}, None, None),
    ("jpeg-arithmetic", picture(), {IMAGE: jpeg(frame=0xC9)}, None, "bad-image-header"),
    ("jpeg-jpg-marker", picture(), {IMAGE: jpeg(extra=segment(0xC8, b""))}, None, None),
    ("jpeg-short-frame", picture(), {IMAGE: jpeg(parts=3, specs=1)}, None, "bad-image-header"),
    ("jpeg-widest", picture(), {IMAGE: jpeg(10_000, 1)}, None, None),
    ("jpeg-highest", picture(), {IMAGE: jpeg(1, 10_000)}, None, None),
    (
        "exif-little-endian",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, exif(8, "II")))},
        None,
        "orientation",
    ),
    (
        "exif-orientation-second",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, exif(6, before=1)))},
        None,
        "orientation",
    ),
    (
        "exif-orientation-tight",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, exif(6, tight=True)))},
        None,
        "orientation",
    ),
    (
        "exif-entry-cut-short",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, exif(6, tight=True)[:-2]))},
        None,
        "bad-image-header",
    ),
    (
        "exif-eight-bytes",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, b"Exif\x00\x00MM\x00*\x00\x00\x00\x04"))},
        None,
        None,
    ),
    (
        "exif-empty-ifd-at-end",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x00"))},
        None,
        None,
    ),
    (
        "exif-ifd-past-end",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, b"Exif\x00\x00MM\x00*\x00\x00\x00\x09\x00\x00"))},
        None,
        "bad-image-header",
    ),
    (
        "exif-many-entries",
        picture(),
        {IMAGE: jpeg(extra=segment(0xE1, b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x64"))},
        None,
        "bad-image-header",
    ),
    (
        "exif-wrong-type",
        picture(),
        {
            IMAGE: jpeg(
                extra=segment(0xE1, exif(6).replace(b"\x01\x12\x00\x03", b"\x01\x12\x00\x04"))
            )
        },
        None,
        "bad-image-header",
    ),
]


def test_a_picture_reports_its_part_bytes_type_pixels_and_extent() -> None:
    value = read_pictures(picture(), MEDIA)
    assert value["paragraphs"][0]["pictures"] == [
        {
            "crop": None,
            "extent": [19050, 9525],
            "kind": "picture",
            "offset": 1,
            "part": "word/media/image1.png",
            "pixels": [2, 1],
            "reason": None,
            "sha256": hashlib.sha256(PNG).hexdigest(),
            "type": "png",
        }
    ]
    # Two in one paragraph, one a JPEG, each where it stands; one in a table cell.
    media = {"word/media/image1.png": PNG, "word/media/photo.jpg": jpeg(3, 2)}
    rels = relationship() + relationship("/word/media/photo.jpg", "rIdJpg")
    two = p(r(picture()) + r("<w:t>x</w:t>") + r(picture(blip='r:embed="rIdJpg"')))
    body = two + tbl(1, f"<w:tr><w:tc>{p(r(picture(crop='<a:srcRect l="-500"/>')))}</w:tc></w:tr>")
    document = read_document(with_media(docx(body), media, {"word/_rels/document.xml.rels": rels}))
    first, second = document.body
    assert [(x.offset, x.part, x.type, x.pixels) for x in first.pictures] == [
        (0, "word/media/image1.png", "png", (2, 1)),
        (2, "word/media/photo.jpg", "jpeg", (3, 2)),
    ]
    assert second.table == (0, 0, 0)
    assert (second.pictures[0].crop, second.pictures[0].reason) == ((-500, 0, 0, 0), "cropped")
    # A note's picture is found through the notes' own relationships.
    note = f'<w:footnote w:id="1">{p(r(picture()))}</w:footnote>'
    body = p(r('<w:footnoteReference w:id="1"/>'))
    data = with_media(
        docx(body, footnotes=note), MEDIA, {"word/_rels/footnotes.xml.rels": relationship()}
    )
    assert (
        read_document(data).footnotes[0].paragraphs[0].pictures[0].part == "word/media/image1.png"
    )
    assert (
        read_document(with_media(docx(body, footnotes=note), MEDIA))
        .footnotes[0]
        .paragraphs[0]
        .pictures[0]
        .reason
        == "no-part"
    )


@pytest.mark.parametrize(
    ("run", "media", "rels", "reason"),
    [case[1:] for case in PICTURE_CASES],
    ids=[case[0] for case in PICTURE_CASES],
)
def test_a_picture_its_bytes_cannot_stand_for_has_the_first_reason_and_is_read(
    run: str, media: dict[str, bytes], rels: dict[str, str] | None, reason: str | None
) -> None:
    (found,) = read_pictures(run, media, rels)["paragraphs"][0]["pictures"]
    assert (found["offset"], found["reason"]) == (1, reason)
    assert found["kind"] == ("shape" if reason == "shape" else "picture")
    if found["part"] is not None:
        assert found["sha256"] == hashlib.sha256(media[found["part"]]).hexdigest()


def field(instruction: str, result: str) -> str:
    """A complex field with ``instruction`` showing ``result`` (runs)."""
    return (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve"> {instruction} </w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + result
        + r('<w:fldChar w:fldCharType="end"/>')
    )


EXACT = '<w:spacing w:line="240" w:lineRule="exact"/>'
BORDER = '<w:bdr w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
ROW = '<w:trPr><w:trHeight w:val="2000" w:hRule="exact"/></w:trPr>'
INNER = f"<w:tr><w:tc>{p(r(picture()))}</w:tc></w:tr>"
STYLED = '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr>'
# Where a picture stands, and the reason its place gives: Word prints a field's result again
# (a REF to a bookmark round another picture prints that one), and clips a picture to an exact
# line or row; a border on the run is drawn round it.
PLACED_CASES: list[tuple[str, str, str | None, str | None]] = [
    ("plain", p(r(picture())), None, None),
    ("complex-field", p(field("DOCPROPERTY Title", r(picture()))), None, "field"),
    (
        "simple-field",
        p(f'<w:fldSimple w:instr=" DOCPROPERTY Title ">{r(picture())}</w:fldSimple>'),
        None,
        "field",
    ),
    (
        "ref-to-another",
        p('<w:bookmarkStart w:id="0" w:name="logo"/>' + r(picture()) + '<w:bookmarkEnd w:id="0"/>')
        + p(field("REF logo \\h", r(picture()))),
        None,
        "field",
    ),
    (
        "carried-field",
        p(
            r('<w:fldChar w:fldCharType="begin"/>')
            + r('<w:instrText xml:space="preserve"> DOCPROPERTY T </w:instrText>')
            + r('<w:fldChar w:fldCharType="separate"/>')
            + r("<w:t>x</w:t>")
        )
        + p(r(picture()) + r('<w:fldChar w:fldCharType="end"/>')),
        None,
        "field",
    ),
    ("exact-line", p(r(picture()), EXACT), None, "line-height"),
    ("at-least-line", p(r(picture()), EXACT.replace("exact", "atLeast")), None, None),
    (
        "exact-line-by-style",
        p(r(picture())),
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:pPr>'
        + EXACT
        + "</w:pPr></w:style>",
        "line-height",
    ),
    (
        "exact-line-by-default",
        p(r(picture())),
        f"<w:docDefaults><w:pPrDefault><w:pPr>{EXACT}</w:pPr></w:pPrDefault></w:docDefaults>",
        "line-height",
    ),
    (
        "nearest-rule-wins",
        p(r(picture()), EXACT.replace("exact", "auto")),
        f"<w:docDefaults><w:pPrDefault><w:pPr>{EXACT}</w:pPr></w:pPrDefault></w:docDefaults>",
        None,
    ),
    ("exact-row", tbl(1, f"<w:tr>{ROW}<w:tc>{p(r(picture()))}</w:tc></w:tr>"), None, "row-height"),
    (
        "at-least-row",
        tbl(1, f"<w:tr>{ROW.replace('exact', 'atLeast')}<w:tc>{p(r(picture()))}</w:tc></w:tr>"),
        None,
        None,
    ),
    (
        "nested-in-exact-row",
        tbl(1, f"<w:tr>{ROW}<w:tc>{tbl(1, INNER)}{p('')}</w:tc></w:tr>"),
        None,
        "row-height",
    ),
    (
        "exact-row-by-style",
        tbl(1, f"<w:tr><w:tc>{p(r(picture()))}</w:tc></w:tr>").replace(
            "<w:tbl>", '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr>'
        ),
        f'<w:style w:type="table" w:styleId="T">{ROW}</w:style>',
        "row-height",
    ),
    ("border", p(r(picture(), BORDER)), None, "border"),
    ("no-border", p(r(picture(), '<w:bdr w:val="none"/>')), None, None),
    (
        "border-by-style",
        p(r(picture(), '<w:rStyle w:val="B"/>')),
        f'<w:style w:type="character" w:styleId="B"><w:rPr>{BORDER}</w:rPr></w:style>',
        "border",
    ),
    ("border-nil", p(r(picture(), '<w:bdr w:val="nil"/>')), None, None),
    (
        "after-a-simple-field",
        p(
            f'<w:fldSimple w:instr=" DOCPROPERTY T ">{r("<w:t>x</w:t>")}</w:fldSimple>'
            + r(picture())
        ),
        None,
        None,
    ),
    ("first-spacing-only", p(r(picture()), '<w:spacing w:after="0"/>' + EXACT), None, None),
    (
        "exact-line-by-table-style",
        tbl(1, f"<w:tr><w:tc>{p(r(picture()))}</w:tc></w:tr>").replace("<w:tbl>", STYLED),
        f'<w:style w:type="table" w:styleId="T"><w:pPr>{EXACT}</w:pPr></w:style>',
        "line-height",
    ),
    (
        "exact-line-by-default-table-style",
        tbl(1, f"<w:tr><w:tc>{p(r(picture()))}</w:tc></w:tr>"),
        f'<w:style w:type="table" w:default="1" w:styleId="T"><w:pPr>{EXACT}</w:pPr></w:style>',
        "line-height",
    ),
    (
        "exact-line-of-a-table-style-outside",
        p(r(picture())) + tbl(1, f"<w:tr><w:tc>{p('')}</w:tc></w:tr>"),
        f'<w:style w:type="table" w:default="1" w:styleId="T"><w:pPr>{EXACT}</w:pPr></w:style>',
        None,
    ),
    (
        "exact-row-by-default-table-style",
        tbl(1, f"<w:tr><w:tc>{p(r(picture()))}</w:tc></w:tr>"),
        f'<w:style w:type="table" w:default="1" w:styleId="T">{ROW}</w:style>',
        "row-height",
    ),
    (
        "row-after-an-exact-row",
        tbl(
            1, f"<w:tr>{ROW}<w:tc>{p('')}</w:tc></w:tr><w:tr><w:tc>{p(r(picture()))}</w:tc></w:tr>"
        ),
        None,
        None,
    ),
]


@pytest.mark.parametrize(
    ("body", "styles", "reason"),
    [case[1:] for case in PLACED_CASES],
    ids=[case[0] for case in PLACED_CASES],
)
def test_a_picture_word_may_print_otherwise_or_clip_says_so_and_is_read(
    body: str, styles: str | None, reason: str | None
) -> None:
    value = json.loads(served(with_media(docx(body, styles), MEDIA))[0])
    assert "refusal" not in value, value.get("refusal")
    found = [x for q in value["paragraphs"] for x in q["pictures"]]
    assert found[-1]["reason"] == reason
    assert found[-1]["sha256"] == hashlib.sha256(PNG).hexdigest()


def test_a_tables_rows_say_whether_their_height_is_exact() -> None:
    rows = f"<w:tr>{ROW}<w:tc>{p('')}</w:tc></w:tr><w:tr><w:tc>{p('')}</w:tc></w:tr>"
    styled = '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr>'
    part = f'<w:tblStylePr w:type="firstRow">{ROW}</w:tblStylePr>'
    for table, styles, exact in (
        (tbl(1, rows), None, [True, False]),
        (tbl(1, rows.replace("exact", "atLeast")), None, [False, False]),
        # A style's row height, its own or a part's, may apply to any row: how is not on record.
        (
            tbl(1, rows).replace("<w:tbl>", styled),
            f'<w:style w:type="table" w:styleId="T">{part}</w:style>',
            [True, True],
        ),
    ):
        (found,) = read_document(docx(table, styles)).tables
        assert found.grid is not None
        assert [row.exact for row in found.grid.rows] == exact


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
        # Past the doubled signs (numStart 9 would be "***"): Word's answer is not on record.
        ('<w:numFmt w:val="chicago"/><w:numStart w:val="9"/>', "unsupported-numbering"),
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
    # Its grid is not reported: a note's table, nested ones too, keeps its outermost cell.
    inner = "<w:tbl><w:tr><w:tc>" + p(r("<w:t>inner</w:t>")) + "</w:tc></w:tr></w:tbl>"
    table = "<w:tbl><w:tr><w:tc>" + p(r("<w:t>cell</w:t>")) + inner + "</w:tc></w:tr></w:tbl>"
    document = read(p(ref(1)), fnote(1, p(r("<w:footnoteRef/>")) + table))
    assert [(x.text, x.table) for x in document.footnotes[0].paragraphs] == [
        ("", None),
        ("cell", (0, 0, 0)),
        ("inner", (0, 0, 0)),
    ]
    assert document.tables == ()


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


# --- floating objects holding text ("Anchored" in the reader's docstring) -----------------------

WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPC = "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
VML = "urn:schemas-microsoft-com:vml"
IN_BOX = p(r("<w:t>in the box</w:t>"))


def box(inner: str = IN_BOX) -> str:
    """A ``wps`` shape holding a text box of ``inner``."""
    return (
        f"<wps:wsp xmlns:wps='{WPS}'><wps:spPr/><wps:txbx><w:txbxContent>{inner}"
        "</w:txbxContent></wps:txbx></wps:wsp>"
    )


def floating(graphic: str, uri: str = WPS, frame: str = "anchor") -> str:
    """A DrawingML drawing of ``graphic``, anchored to its paragraph (or ``frame``)."""
    return (
        f"<w:drawing><wp:{frame} xmlns:wp='{WP}'><a:graphic xmlns:a='{A}'>"
        f"<a:graphicData uri='{uri}'>{graphic}</a:graphicData></a:graphic></wp:{frame}></w:drawing>"
    )


def vml_box(inner: str = IN_BOX, style: str = "position:absolute;margin-left:9pt") -> str:
    """A VML shape holding a text box of ``inner``."""
    return (
        f"<w:pict><v:shapetype xmlns:v='{VML}'/><v:shape xmlns:v='{VML}' style='{style}'>"
        f"<v:textbox><w:txbxContent>{inner}</w:txbxContent></v:textbox></v:shape></w:pict>"
    )


def group(inner: str = IN_BOX) -> str:
    """Word's text box group, a ``wpg`` choice with its VML group as fallback."""
    shapes = f"<wpg:wgp xmlns:wpg='{WPG}'>{box(inner)}<wps:wsp xmlns:wps='{WPS}'/></wpg:wgp>"
    fallback = (
        f"<w:pict><v:group xmlns:v='{VML}' style='position:absolute'><v:shape>"
        f"<v:textbox><w:txbxContent>{inner}</w:txbxContent></v:textbox></v:shape></v:group></w:pict>"
    )
    return _alternate(floating(shapes, WPG), fallback, requires="wpg")


# Each floating object holding text the reader sets aside, as Word writes it, and its kind.
FLOATING_TEXT = {
    "text-box": (_alternate(floating(box()), vml_box()), "text-box"),
    "text-box-drawing": (floating(box()), "text-box"),
    # A picture in the box's text: a graphic Word draws there, holding no text.
    "text-box-picture": (floating(box(IN_BOX + p(r(PICTURE)))), "text-box"),
    "group": (group(), "shapes"),
    "canvas": (
        _alternate(floating(f"<wpc:wpc xmlns:wpc='{WPC}'>{box()}</wpc:wpc>", WPC), requires="wpc"),
        "shapes",
    ),
    "vml-text-box": (vml_box(), "text-box"),
    "vml-group": (
        f"<w:pict><v:group xmlns:v='{VML}' style='Position: Absolute'>"
        f"{vml_box()[len('<w:pict>') : -len('</w:pict>')]}</v:group></w:pict>",
        "shapes",
    ),
}


@pytest.mark.parametrize("name", FLOATING_TEXT)
def test_a_floating_object_holding_text_is_set_aside_unread_where_it_is_anchored(name: str) -> None:
    drawing, kind = FLOATING_TEXT[name]
    data = docx(p(r("<w:t>a</w:t>") + r(drawing) + r("<w:t>b</w:t>")))
    (paragraph,) = read_docx(data)
    assert (paragraph.text, paragraph.anchored) == ("ab", (Anchored(1, kind),))
    # Certified as such: the check finds it on its own (tests/test_certify.py).
    value = json.loads(served(data)[0])
    assert value["paragraphs"][0]["anchored"] == [{"kind": kind, "offset": 1, "read": False}]


@pytest.mark.parametrize("name", ["text-box", "vml-text-box", "picture"])
def test_a_floating_object_in_a_hidden_run_is_refused(name: str) -> None:
    # Whether Word draws it is not on record.
    drawing = PICTURE.replace("wp:inline", "wp:anchor") if name == "picture" else None
    hidden = r(drawing or FLOATING_TEXT[name][0], "<w:vanish/>")
    assert refusal(p(r("<w:t>a</w:t>") + hidden)) == "hidden-text"


def test_every_floating_object_is_placed_where_it_is_anchored() -> None:
    line = _alternate(SHAPE, LINE)
    picture = PICTURE.replace("wp:inline", "wp:anchor")
    absolute = VML_PICTURE.replace('style="', 'style="position:absolute;')
    body = p(r("<w:t>ab</w:t>" + line) + r(picture + "<w:t>c</w:t>" + absolute) + r(vml_box()))
    (paragraph,) = read_docx(docx(body))
    assert paragraph.text == "abc"
    kinds = [(a.offset, a.kind, a.read) for a in paragraph.anchored]
    assert kinds == [
        (2, "shape", False),
        (2, "picture", False),
        (3, "picture", False),
        (3, "text-box", False),
    ]


# What, in a floating object, Word refers to or counts elsewhere: each keeps it refused whole.
COUNTED_INSIDE = {
    "field": p(_field("DOCPROPERTY Title", "x")),
    "field-marks": p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>x</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    ),
    "field-code": p(r("<w:instrText> PAGE </w:instrText>")),
    "seq": p(_field("SEQ Figure", "1")),
    "simple-field": p('<w:fldSimple w:instr=" SEQ Figure "><w:r><w:t>1</w:t></w:r></w:fldSimple>'),
    "footnote": p(r('<w:footnoteReference w:id="1"/>')),
    "endnote": p(r('<w:endnoteReference w:id="1"/>')),
    "list-item": p(r("<w:t>x</w:t>"), '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'),
    "list-by-style": p(r("<w:t>x</w:t>"), '<w:pStyle w:val="L"/>'),
    "bookmark": p(_marked("b", r("<w:t>x</w:t>"))),
    "bookmark-start": p('<w:bookmarkStart w:id="1" w:name="b"/>' + r("<w:t>x</w:t>")),
    "bookmark-end": p(r("<w:t>x</w:t>") + '<w:bookmarkEnd w:id="1"/>'),
    "comment-range": p('<w:commentRangeStart w:id="0"/>' + r("<w:t>x</w:t>")),
    "comment-range-end": p(r("<w:t>x</w:t>") + '<w:commentRangeEnd w:id="0"/>'),
    "comment-mark": p(r('<w:commentReference w:id="0"/>')),
    "comment-echo": p(r("<w:annotationRef/>")),
    "section": p(r("<w:t>x</w:t>"), "<w:sectPr/>"),
    "bound": '<w:sdt><w:sdtPr><w:dataBinding w:xpath="/a"/></w:sdtPr><w:sdtContent>'
    + IN_BOX
    + "</w:sdtContent></w:sdt>",
}
LIST_STYLE = (
    '<w:style w:type="paragraph" w:styleId="L"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr>'
    "</w:pPr></w:style>"
)


@pytest.mark.parametrize("inside", COUNTED_INSIDE)
@pytest.mark.parametrize("name", ["text-box", "group", "vml-text-box"])
def test_a_floating_object_holding_what_is_counted_elsewhere_is_refused_as_before(
    name: str, inside: str
) -> None:
    drawing = FLOATING_TEXT[name][0].replace(IN_BOX, COUNTED_INSIDE[inside])
    detail = {"text-box": "AlternateContent", "group": "AlternateContent", "vml-text-box": "pict"}
    with pytest.raises(DocxRefusedError, match=detail[name]) as caught:
        read_docx(docx(p(r(drawing)), LIST_STYLE))
    assert caught.value.code == "unsupported-element"


def test_a_list_set_by_the_defaults_or_a_table_style_keeps_a_floating_object_refused() -> None:
    defaults = '<w:docDefaults><w:pPrDefault><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr>'
    defaults += "</w:pPr></w:pPrDefault></w:docDefaults>"
    assert refusal(p(r(vml_box())), defaults) == "unsupported-element"
    table_style = LIST_STYLE.replace('w:type="paragraph"', 'w:type="table"')
    in_table = vml_box(
        f'<w:tbl><w:tblPr><w:tblStyle w:val="L"/></w:tblPr><w:tr><w:tc>{IN_BOX}</w:tc></w:tr>'
        "</w:tbl>"
    )
    assert refusal(p(r(in_table)), table_style) == "unsupported-element"
    assert text_of(p(r(in_table))) == [""]  # no list there: set aside


_CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_END = r('<w:fldChar w:fldCharType="end"/>')
# Floating objects holding text that are not set aside: each is refused as before.
NOT_SET_ASIDE = {
    "in-line": p(r(_alternate(floating(box(), frame="inline"), vml_box()))),
    "vml-in-line": p(r(vml_box(style="margin-left:9pt"))),
    "vml-relative": p(r(vml_box(style="position:relative"))),
    # In a field's result, which Word may print again.
    "in-a-field": p(_field('HYPERLINK "https://x"', "x").replace(_END, r(vml_box()) + _END)),
    "ink": p(r(_alternate(floating(box()), vml_box(), requires="wpi"))),
    "two-choices": p(
        r(
            _alternate(floating(box()), vml_box()).replace(
                "<mc:Fallback>",
                '<mc:Choice Requires="wps">' + floating(box()) + "</mc:Choice><mc:Fallback>",
            )
        )
    ),
    "choice-not-one-drawing": p(r(_alternate(floating(box()) + floating(box())))),
    "two-frames": p(
        r(floating(box()).replace("</wp:anchor>", f"</wp:anchor><wp:anchor xmlns:wp='{WP}'/>"))
    ),
    "chart-in-group": p(
        r(
            _alternate(
                floating(
                    f"<wpg:wgp xmlns:wpg='{WPG}'>{box()}<wpg:graphicFrame><a:graphic xmlns:a='{A}'>"
                    f"<a:graphicData uri='{_CHART}'/></a:graphic></wpg:graphicFrame></wpg:wgp>",
                    WPG,
                ),
                requires="wpg",
            )
        )
    ),
    "chart": p(r(floating(box(), _CHART))),
    "wordart": p(r(vml_box().replace("<v:textbox>", "<v:textpath string='x'/><v:textbox>"))),
    "activex": p(r(vml_box().replace("</w:pict>", '<w:control w:name="x"/></w:pict>'))),
    "two-shapes": p(r(vml_box().replace("</w:pict>", f"<v:shape xmlns:v='{VML}'/></w:pict>"))),
    "run-outside-the-box": p(r(_alternate(floating(box()), "<w:t>x</w:t>"))),
}


@pytest.mark.parametrize("name", NOT_SET_ASIDE)
def test_a_text_box_in_line_in_a_field_or_of_another_drawing_is_refused_as_before(
    name: str,
) -> None:
    assert refusal(NOT_SET_ASIDE[name]) == "unsupported-element"


def test_a_styleref_or_a_seq_restarting_at_headings_beside_unread_text_is_refused() -> None:
    heading = '<w:style w:type="paragraph" w:styleId="H1"><w:name w:val="heading 1"/></w:style>'
    titled = p(r("<w:t>Title</w:t>"), '<w:pStyle w:val="H1"/>')
    for field, stored in (("STYLEREF 1", "Title"), ("SEQ Figure \\s 1", "1")):
        body = titled + p(_field(field, stored)) + p(r(vml_box()))
        with pytest.raises(DocxRefusedError, match="beside text that is not read"):
            read_docx(docx(body, heading))
        assert text_of(titled + p(_field(field, stored)), heading)[1] == stored


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
        ("\u007f", "format-character"),  # DEL: a control (Cc), neither C0 nor C1
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
    # With no placeholder part, and not showing one, Word shows nothing there, as read.
    plain = body.replace(_PLACEHOLDER, "<w:sdtPr/>")
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
        "",  # no tblLook: Word turns the first row on (table-style-no-look)
    ],
)
def test_conditional_emphasis_in_a_region_the_table_turns_on_is_applied(look: str) -> None:
    # Word applies firstRow b+i where tblLook turns the first row on, and nothing where off.
    body, styles = _styled_table(look, [["Frequency"], ["Very common"]])
    marks = [[m.kind for m in x.marks] for x in read_docx(docx(body, styles))]
    assert marks == [["bold", "italic"], []]


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


def test_segoe_ui_symbol_is_a_unicode_font_read_as_stored() -> None:

    # A Unicode font whose name holds "Symbol" (the FDA SPL rendering uses it): read as stored.
    name = "Segoe UI Symbol"
    run = p(r("<w:t>☐ a</w:t>", f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}"/>'))
    assert text_of(run) == ["☐ a"]
    # Any other name holding "Symbol" stays refused.
    other = p(r("<w:t>a</w:t>", '<w:rFonts w:ascii="Segoe Symbol" w:hAnsi="Segoe Symbol"/>'))
    assert refusal(other) == "symbol-font"
    value = json.loads(served(docx(run))[0])
    assert value["paragraphs"][0]["text"] == "☐ a"


def test_symbol_text_in_a_bidirectional_embedding_is_refused() -> None:
    run = r("<w:t>a</w:t>", '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>')
    for direction in ("rtl", "ltr"):
        assert refusal(p(f'<w:dir w:val="{direction}">{run}</w:dir>')) == "symbol-font"
    assert text_of(p(run)) == ["\u03b1"]


# --- table styles' conditional formatting, as Word applies it (docx-reader/1.24.0) -------------
# Each grid is Word's answer to the same table in corpus/numbering-cases (table-style-*): each
# cell's bold (B), italic (I), capitals (C) and strike (S), rows top to bottom.

ALL_LOOKS = (
    '<w:tblLook w:val="06A0" w:firstRow="1" w:lastRow="1" w:firstColumn="1" w:lastColumn="1" '
    'w:noHBand="0" w:noVBand="0"/>'
)
NO_LOOKS = (
    '<w:tblLook w:val="0000" w:firstRow="0" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" '
    'w:noHBand="0" w:noVBand="0"/>'
)
SIZE_ONE = '<w:tblPr><w:tblStyleRowBandSize w:val="1"/><w:tblStyleColBandSize w:val="1"/></w:tblPr>'
OFF = '<w:b w:val="0"/>'


def t_style(*parts: tuple[str, str], base: str = "", extra: str = "") -> str:
    """Table style T with conditional parts (type, run properties); ``base`` goes before them."""
    body = "".join(
        f'<w:tblStylePr w:type="{k}"><w:rPr>{v}</w:rPr></w:tblStylePr>' for k, v in parts
    )
    return f'<w:style w:type="table" w:styleId="T"><w:name w:val="T"/>{base}{body}</w:style>{extra}'


def t_table(look: str = ALL_LOOKS, size: int = 3, props: str = "", rows: str = "") -> str:
    """A size-by-size table in style T, one run in each cell; ``rows`` opens every row."""
    return (
        f'<w:tbl><w:tblPr><w:tblStyle w:val="T"/>{look}</w:tblPr>'
        + "".join(
            f"<w:tr>{rows}"
            + "".join(f"<w:tc>{p(r(f'<w:t>r{i}c{j}</w:t>', props))}</w:tc>" for j in range(size))
            + "</w:tr>"
            for i in range(size)
        )
        + "</w:tbl>"
    )


def grid(body: str, styles: str) -> str:
    """Each cell's bold, italic, capitals and strike, as read and certified (rows split by |)."""
    value = json.loads(served(docx(body, styles))[0])
    assert "refusal" not in value, value.get("refusal")
    letters = {"bold": "B", "italic": "I", "caps": "C", "strike": "S"}
    cells = [
        "".join(letters[m["kind"]] for m in x["marks"] if m["kind"] in letters) or "."
        for x in value["paragraphs"]
    ]
    size = round(len(cells) ** 0.5)
    return "|".join(" ".join(cells[i * size : (i + 1) * size]) for i in range(size))


HEADERS = "<w:trPr><w:tblHeader/></w:trPr>"
CHARACTER_B = '<w:style w:type="character" w:styleId="C"><w:rPr><w:b/></w:rPr></w:style>'
NORMAL = '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:rPr>{}</w:rPr></w:style>'
APPLIED = {
    # Each part where all looks are on; the whole-table part never.
    "type-firstRow": (t_table(), t_style(("firstRow", "<w:b/>")), "B B B|. . .|. . ."),
    "type-lastRow": (t_table(), t_style(("lastRow", "<w:b/>")), ". . .|. . .|B B B"),
    "type-firstCol": (t_table(), t_style(("firstCol", "<w:b/>")), "B . .|B . .|B . ."),
    "type-lastCol": (t_table(), t_style(("lastCol", "<w:b/>")), ". . B|. . B|. . B"),
    "type-nwCell": (t_table(), t_style(("nwCell", "<w:b/>")), "B . .|. . .|. . ."),
    "type-neCell": (t_table(), t_style(("neCell", "<w:b/>")), ". . B|. . .|. . ."),
    "type-swCell": (t_table(), t_style(("swCell", "<w:b/>")), ". . .|. . .|B . ."),
    "type-seCell": (t_table(), t_style(("seCell", "<w:b/>")), ". . .|. . .|. . B"),
    "type-wholeTable": (t_table(), t_style(("wholeTable", "<w:b/>")), ". . .|. . .|. . ."),
    # Looks: an attribute over the val bits, else the bit; no look turns the first row on.
    "look-off": (
        t_table(NO_LOOKS.replace('noHBand="0" w:noVBand="0"', 'noHBand="1" w:noVBand="1"')),
        t_style(("firstRow", "<w:b/>"), ("band1Horz", "<w:i/>")),
        ". . .|. . .|. . .",
    ),
    "attr-vs-val-row": (
        t_table('<w:tblLook w:val="0000" w:firstRow="1"/>'),
        t_style(("firstRow", "<w:b/>")),
        "B B B|. . .|. . .",
    ),
    "attr-off-val-on": (
        t_table('<w:tblLook w:val="0020" w:firstRow="0"/>'),
        t_style(("firstRow", "<w:b/>")),
        ". . .|. . .|. . .",
    ),
    "look-val-only": (
        t_table('<w:tblLook w:val="0020"/>'),
        t_style(("firstRow", "<w:b/>")),
        "B B B|. . .|. . .",
    ),
    "no-look": (t_table(""), t_style(("firstRow", "<w:b/>")), "B B B|. . .|. . ."),
    # A row's own look that says what the table's does (as the EMA ATMP template has it).
    "row-look-agrees": (
        t_table(
            '<w:tblLook w:val="0020"/>', rows='<w:tblPrEx><w:tblLook w:val="0020"/></w:tblPrEx>'
        ),
        t_style(("firstRow", "<w:b/>")),
        "B B B|. . .|. . .",
    ),
    # Bands: only with a band size, n rows or columns to a band, unless the look turns them off.
    "bands-on-1H": (t_table(NO_LOOKS), t_style(("band1Horz", "<w:b/>")), ". . .|. . .|. . ."),
    "sized-1H": (
        t_table(NO_LOOKS),
        t_style(("band1Horz", "<w:b/>"), base=SIZE_ONE),
        "B B B|. . .|B B B",
    ),
    "sized-2H": (
        t_table(NO_LOOKS),
        t_style(("band2Horz", "<w:b/>"), base=SIZE_ONE),
        ". . .|B B B|. . .",
    ),
    "sized-1V": (
        t_table(NO_LOOKS),
        t_style(("band1Vert", "<w:b/>"), base=SIZE_ONE),
        "B . B|B . B|B . B",
    ),
    "sized-row2": (
        t_table(NO_LOOKS),
        t_style(
            ("band1Horz", "<w:b/>"), base='<w:tblPr><w:tblStyleRowBandSize w:val="2"/></w:tblPr>'
        ),
        "B B B|B B B|. . .",
    ),
    "sized-hband-off-attr": (
        t_table('<w:tblLook w:val="0000" w:noHBand="1" w:noVBand="0"/>'),
        t_style(("band1Horz", "<w:b/>"), base=SIZE_ONE),
        ". . .|. . .|. . .",
    ),
    "sized-hband-off-val": (
        t_table('<w:tblLook w:val="0200"/>'),
        t_style(("band1Horz", "<w:b/>"), base=SIZE_ONE),
        ". . .|. . .|. . .",
    ),
    # Banding counts past the first row (column) where its look is on and the style defines it,
    # from it where the style does not; a last row (column) the style does not define is banded.
    "sized-row-vs-band": (
        t_table('<w:tblLook w:val="0000" w:firstRow="1" w:noHBand="0" w:noVBand="1"/>'),
        t_style(("band1Horz", "<w:b/>"), ("firstRow", OFF), base=SIZE_ONE),
        ". . .|B B B|. . .",
    ),
    "hband-first-last": (
        t_table(
            '<w:tblLook w:val="0000" w:firstRow="1" w:lastRow="1" w:noHBand="0" w:noVBand="1"/>', 4
        ),
        t_style(("band1Horz", "<w:b/>"), base=SIZE_ONE),
        "B B B B|. . . .|B B B B|. . . .",
    ),
    "col-vs-vband": (
        t_table('<w:tblLook w:val="0000" w:firstColumn="1" w:noVBand="0" w:noHBand="1"/>', 4),
        t_style(("band1Vert", "<w:b/>"), ("firstCol", OFF), base=SIZE_ONE),
        "|".join([". B . B"] * 4),
    ),
    "vband-firstcol": (
        t_table('<w:tblLook w:val="0000" w:firstColumn="1" w:noVBand="0" w:noHBand="1"/>', 4),
        t_style(("band1Vert", "<w:b/>"), base=SIZE_ONE),
        "|".join(["B . B ."] * 4),
    ),
    "hband-lastrow": (
        t_table('<w:tblLook w:val="0000" w:lastRow="1" w:noHBand="0" w:noVBand="1"/>', 4),
        t_style(("band2Horz", "<w:b/>"), base=SIZE_ONE),
        ". . . .|B B B B|. . . .|B B B B",
    ),
    # Precedence: horizontal band < vertical band < column < row < corner.
    "corner-vs-row": (
        t_table(),
        t_style(("firstRow", "<w:b/>"), ("nwCell", OFF)),
        ". B B|. . .|. . .",
    ),
    "row-vs-col": (
        t_table(),
        t_style(("firstRow", "<w:b/>"), ("firstCol", OFF)),
        "B B B|. . .|. . .",
    ),
    "col-vs-row": (
        t_table(),
        t_style(("firstCol", "<w:b/>"), ("firstRow", OFF)),
        ". . .|B . .|B . .",
    ),
    "lastcol-vs-row": (
        t_table(
            '<w:tblLook w:val="0000" w:lastColumn="1" w:lastRow="1" w:noHBand="1" w:noVBand="1"/>',
            4,
        ),
        t_style(("lastCol", "<w:b/>"), ("lastRow", OFF), base=SIZE_ONE),
        ". . . B|. . . B|. . . B|. . . .",
    ),
    "sized-v-vs-h": (
        t_table(NO_LOOKS),
        t_style(("band1Vert", "<w:b/>"), ("band1Horz", OFF), base=SIZE_ONE),
        "B . B|B . B|B . B",
    ),
    # Over the table style's own properties at its level; a toggle across levels; direct wins.
    "base-and-row": (
        t_table(),
        t_style(("firstRow", "<w:b/>"), base="<w:rPr><w:b/></w:rPr>"),
        "B B B|B B B|B B B",
    ),
    "charstyle-and-row": (
        t_table(props='<w:rStyle w:val="C"/>'),
        t_style(("firstRow", "<w:b/>"), extra=CHARACTER_B),
        ". . .|B B B|B B B",
    ),
    "parastyle-and-row": (
        t_table(),
        t_style(("firstRow", "<w:b/>"), extra=NORMAL.format("<w:b/>")),
        ". . .|B B B|B B B",
    ),
    "italic-toggle-para": (
        t_table('<w:tblLook w:val="0000" w:firstRow="1"/>'),
        t_style(("firstRow", "<w:i/>"), extra=NORMAL.format("<w:i/>")),
        ". . .|I I I|I I I",
    ),
    "direct-off": (t_table(props=OFF), t_style(("firstRow", "<w:b/>")), ". . .|. . .|. . ."),
    "direct-on": (t_table(props="<w:b/>"), t_style(("firstRow", "<w:b/>")), "B B B|B B B|B B B"),
    # Capitals and strike as bold; header rows at the top are first rows.
    "italic-caps": (
        t_table(),
        t_style(("firstRow", "<w:i/>"), ("lastRow", "<w:caps/>"), ("firstCol", "<w:strike/>")),
        "IS I I|S . .|CS C C",
    ),
    "header-rows": (
        t_table().replace("<w:tr>", f"<w:tr>{HEADERS}", 2),
        t_style(("firstRow", "<w:b/>")),
        "B B B|B B B|. . .",
    ),
}


@pytest.mark.parametrize(("body", "styles", "shown"), APPLIED.values(), ids=APPLIED.keys())
def test_table_style_parts_are_applied_as_word_applies_them(
    body: str, styles: str, shown: str
) -> None:
    assert grid(body, styles) == shown


def _nested_table(look: str = ALL_LOOKS) -> str:
    inner = t_table(look, 1)
    return t_table().replace("<w:t>r0c0</w:t></w:r></w:p>", "<w:t>r0c0</w:t></w:r></w:p>" + inner)


# Banding leaves out the last row (column) where the style defines that part and its look is on
# (table-style-row2-lastrow-part, -row1-lastrow-part): only the last row's own part applies there.
_LAST_ROW_ON = '<w:tblLook w:val="0000" w:lastRow="1" w:noHBand="0" w:noVBand="1"/>'
_LAST_COL_ON = '<w:tblLook w:val="0000" w:lastColumn="1" w:noHBand="1" w:noVBand="0"/>'
LAST_LEFT_OUT = {
    "banded-defined-last-row": (
        t_table(ALL_LOOKS),
        t_style(("band1Horz", "<w:b/>"), ("lastRow", "<w:i/>"), base=SIZE_ONE),
        "B B B|. . .|I I I",
    ),
    "banded-defined-last-column": (
        t_table(ALL_LOOKS),
        t_style(("band1Vert", "<w:b/>"), ("lastCol", "<w:i/>"), base=SIZE_ONE),
        "B . I|B . I|B . I",
    ),
    "row-bands-of-two": (
        t_table(_LAST_ROW_ON, 5),
        t_style(
            ("lastRow", "<w:i/>"),
            ("band1Horz", "<w:b/>"),
            base='<w:tblPr><w:tblStyleRowBandSize w:val="2"/></w:tblPr>',
        ),
        "|".join(["B B B B B"] * 2 + [". . . . ."] * 2 + ["I I I I I"]),
    ),
    "row-bands-of-one": (
        t_table(_LAST_ROW_ON, 5),
        t_style(("lastRow", "<w:i/>"), ("band2Horz", "<w:b/>"), base=SIZE_ONE),
        "|".join([". . . . .", "B B B B B"] * 2 + ["I I I I I"]),
    ),
    "column-bands-of-two": (
        t_table(_LAST_COL_ON, 5),
        t_style(
            ("lastCol", "<w:i/>"),
            ("band1Vert", "<w:b/>"),
            base='<w:tblPr><w:tblStyleColBandSize w:val="2"/></w:tblPr>',
        ),
        "|".join(["B B . . I"] * 5),
    ),
}


@pytest.mark.parametrize(
    ("body", "styles", "shown"), LAST_LEFT_OUT.values(), ids=LAST_LEFT_OUT.keys()
)
def test_banding_leaves_out_a_last_row_or_column_the_style_defines(
    body: str, styles: str, shown: str
) -> None:
    assert grid(body, styles) == shown


NOT_ASKED = {
    # The last row under banding, where the style defines it and its look is not on record.
    "banded-last-row-look-unsaid": (
        t_table('<w:tblLook w:noHBand="0" w:noVBand="1"/>'),
        t_style(("band1Horz", "<w:b/>"), base=SIZE_ONE).replace(
            "</w:style>",
            '<w:tblStylePr w:type="lastRow"><w:tcPr><w:shd w:val="clear" '
            'w:fill="D9D9D9"/></w:tcPr></w:tblStylePr></w:style>',
        ),
    ),
    # A corner without both its looks on; on a header row past the first.
    "corner-one-look": (
        t_table('<w:tblLook w:val="0000" w:firstRow="1" w:firstColumn="0"/>'),
        t_style(("nwCell", "<w:b/>")),
    ),
    "corner-no-look": (t_table('<w:tblLook w:val="0000"/>'), t_style(("nwCell", "<w:b/>"))),
    "corner-header-row": (
        t_table().replace("<w:tr>", f"<w:tr>{HEADERS}", 2),
        t_style(("nwCell", "<w:b/>")),
    ),
    # Looks not on record: other than the first row with no look; neither attribute nor bit;
    # a row's own look that says otherwise.
    "no-look-last-row": (t_table(""), t_style(("lastRow", "<w:b/>"))),
    "look-unsaid": (t_table('<w:tblLook w:firstRow="1"/>'), t_style(("lastRow", "<w:b/>"))),
    "row-look-differs": (
        t_table(
            '<w:tblLook w:val="0000"/>', rows='<w:tblPrEx><w:tblLook w:val="0020"/></w:tblPrEx>'
        ),
        t_style(("firstRow", "<w:b/>")),
    ),
    # Band sizes not on record: zero, set by the table, from a basedOn style.
    "band-size-zero": (
        t_table(NO_LOOKS),
        t_style(
            ("band1Horz", "<w:b/>"), base='<w:tblPr><w:tblStyleRowBandSize w:val="0"/></w:tblPr>'
        ),
    ),
    "band-size-of-the-table": (
        t_table(NO_LOOKS + '<w:tblStyleRowBandSize w:val="1"/>'),
        t_style(("band1Horz", "<w:b/>")),
    ),
    # Parts through basedOn; a part of no known type.
    "based-on": (
        t_table(),
        '<w:style w:type="table" w:styleId="T"><w:basedOn w:val="U"/></w:style>'
        + t_style(("firstRow", "<w:b/>")).replace('"T"', '"U"'),
    ),
    "unknown-type": (t_table(), t_style(("firstRows", "<w:b/>"))),
    # First and last row over one cell; a header row below a row that is none; banding past
    # several header rows.
    "first-and-last-row": (t_table(size=1), t_style(("firstRow", "<w:b/>"), ("lastRow", OFF))),
    "stray-header": (
        t_table().replace("</w:tr><w:tr>", f"</w:tr><w:tr>{HEADERS}", 1),
        t_style(("firstRow", "<w:b/>")),
    ),
    "banding-past-headers": (
        t_table().replace("<w:tr>", f"<w:tr>{HEADERS}", 2),
        t_style(("firstRow", "<w:i/>"), ("band1Horz", "<w:b/>"), base=SIZE_ONE),
    ),
    # Where a part applies: a row off the grid, merged cells, a nested table, or the table
    # nested in another.
    "row-off-the-grid": (
        t_table(rows='<w:trPr><w:gridBefore w:val="1"/></w:trPr>'),
        t_style(("firstRow", "<w:b/>")),
    ),
    "merged-cells": (
        t_table().replace("<w:tc>", '<w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>', 1),
        t_style(("firstRow", "<w:b/>")),
    ),
    "nested-under-part": (
        _nested_table('<w:tblLook w:val="0000"/>'),
        t_style(("firstRow", "<w:b/>")),
    ),
    "nested-table-applies": (
        _nested_table().replace(ALL_LOOKS, '<w:tblLook w:val="0000"/>', 1),
        t_style(("firstRow", "<w:b/>")),
    ),
}


@pytest.mark.parametrize(("body", "styles"), NOT_ASKED.values(), ids=NOT_ASKED.keys())
def test_table_style_parts_word_was_not_asked_about_are_refused(body: str, styles: str) -> None:
    assert refusal(body, styles) == "unsupported-element"
    # Over no text, nothing of it is drawn.
    empty = re.sub(r"<w:r><w:t>r[0-9]c[0-9]</w:t></w:r>", "", body)
    assert set(text_of(empty, styles)) == {""}


def test_a_table_style_part_defined_twice_is_refused_wherever_the_style_is_used() -> None:
    styles = t_style(("firstRow", "<w:b/>"), ("firstRow", "<w:i/>"))
    assert refusal(t_table(), styles) == "unsupported-element"
    assert refusal(t_table(NO_LOOKS), styles) == "unsupported-element"


def test_table_style_parts_in_a_note_are_refused_and_in_the_body_read() -> None:
    styles = t_style(("firstRow", "<w:b/>"))
    note = fnote(1, p(r("<w:footnoteRef/>")) + t_table())
    with pytest.raises(DocxRefusedError, match=r"a note, header"):
        read_document(docx(p(r("<w:t>a</w:t>") + ref(1)), styles, footnotes=SEPARATORS + note))
    assert grid(t_table(), styles) == "B B B|. . .|. . ."


def test_conditional_colours_and_fonts_are_read_only_where_they_change_nothing() -> None:
    # A part's colour or font is not on record: read where the text looks the same without it.
    white = '<w:rPr><w:color w:val="FFFFFF"/></w:rPr>'
    styles = t_style(("firstRow", '<w:b/><w:color w:val="000000"/>'), base=white)
    assert refusal(t_table(), styles) == "unsupported-element"  # faint without it, not with it
    styles = t_style(("firstRow", '<w:b/><w:color w:val="1F497D"/><w:sz w:val="18"/>'))
    assert grid(t_table(), styles) == "B B B|. . .|. . ."
    # A Wingdings table whose first row's part sets Arial: Word's font there is not on record.
    wingdings = '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr>'
    styles = t_style(
        ("firstRow", '<w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'), base=wingdings
    )
    only_first = re.sub(r"<w:r><w:t>r[12]c[0-9]</w:t></w:r>", "", t_table())
    assert refusal(only_first, styles) == "symbol-font"


def test_a_based_on_table_style_whose_parts_set_no_mark_changes_nothing() -> None:
    # Its part's cell shading is no run property: the table's own style's bold is applied.
    shaded = '<w:tcPr><w:shd w:val="clear" w:fill="D9D9D9"/></w:tcPr>'
    based = (
        '<w:style w:type="table" w:styleId="U"><w:tblStylePr w:type="firstRow">'
        f"{shaded}</w:tblStylePr></w:style>"
    )
    styles = t_style(("firstRow", "<w:b/>"), base='<w:basedOn w:val="U"/>', extra=based)
    assert grid(t_table(), styles) == "B B B|. . .|. . ."
    # Where the based-on part sets a mark too, how Word merges them is not on record.
    marking = based.replace(shaded, "<w:rPr><w:i/></w:rPr>")
    styles = t_style(("firstRow", "<w:b/>"), base='<w:basedOn w:val="U"/>', extra=marking)
    assert refusal(t_table(), styles) == "unsupported-element"


# --- what company-written labels hold (EMA product information, 2026-10-05) ---------------


def with_settings(body: str, settings: str) -> bytes:
    """A .docx of ``body`` whose settings part holds ``settings``."""
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
        package.writestr("word/settings.xml", f'<w:settings xmlns:w="{W}">{settings}</w:settings>')
    return buffer.getvalue()


def variables(*pairs: tuple[str, str]) -> str:
    """Document variables (``w:docVar``), each a name and its value as XML writes it."""
    held = "".join(f'<w:docVar w:name="{name}" w:val="{value}"/>' for name, value in pairs)
    return f"<w:docVars>{held}</w:docVars>"


# Veeva Vault's anchor at a heading: a DOCVARIABLE whose variable and stored result are a space.
VAULT = "VAULT_ND_0f6b2c1e-7d4a-4c1b-9e2a-3b5c6d7e8f90"
SPACE = r('<w:t xml:space="preserve"> </w:t>')


def complex_field(instruction: str, result: str) -> str:
    return (
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve">{instruction}</w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + result
        + r('<w:fldChar w:fldCharType="end"/>')
    )


def simple_field(instruction: str, result: str) -> str:
    return f'<w:fldSimple w:instr="{instruction}">{result}</w:fldSimple>'


def anchored(field: str, settings: str) -> bytes:
    return with_settings(p(r("<w:t>4.1</w:t>") + field + r("<w:t>b</w:t>")), settings)


_LOWER = VAULT.replace("VAULT_ND_", "vault_nd_")


# Each a DOCVARIABLE read: its name, the field, the variable's name and value as XML writes it,
# and the text read. First every form EMA product-information files hold (2026-10-05), where
# the variable's value and the stored result are one space; then the rule beyond them:
# CHARFORMAT, no switch, a code in any case, a value as XML reads it, split over runs.
DOCVARIABLE_READS: list[tuple[str, str, str, str, str]] = [
    ("complex", complex_field(f" DOCVARIABLE {VAULT} \\* MERGEFORMAT ", SPACE), VAULT, " ", " "),
    (
        "two-formats",
        complex_field(f" DOCVARIABLE {VAULT} \\* MERGEFORMAT \\* CHARFORMAT ", SPACE),
        VAULT,
        " ",
        " ",
    ),
    (
        "complex-no-trailing-space",
        complex_field(f" DOCVARIABLE {VAULT} \\* MERGEFORMAT", SPACE),
        VAULT,
        " ",
        " ",
    ),
    (
        "complex-no-spaces",
        complex_field(f"DOCVARIABLE {VAULT} \\* MERGEFORMAT", SPACE),
        VAULT,
        " ",
        " ",
    ),
    (
        "complex-lower-case-name",
        complex_field(f" DOCVARIABLE {_LOWER} \\* MERGEFORMAT ", SPACE),
        _LOWER,
        " ",
        " ",
    ),
    ("simple", simple_field(f" DOCVARIABLE {VAULT} \\* MERGEFORMAT ", SPACE), VAULT, " ", " "),
    (
        "simple-no-trailing-space",
        simple_field(f" DOCVARIABLE {VAULT} \\* MERGEFORMAT", SPACE),
        VAULT,
        " ",
        " ",
    ),
    (
        "simple-no-spaces",
        simple_field(f"DOCVARIABLE {VAULT} \\* MERGEFORMAT", SPACE),
        VAULT,
        " ",
        " ",
    ),
    (
        "simple-lower-case-name",
        simple_field(f" DOCVARIABLE {_LOWER} \\* MERGEFORMAT ", SPACE),
        _LOWER,
        " ",
        " ",
    ),
    ("charformat", complex_field(f" docvariable {VAULT} \\* charformat ", SPACE), VAULT, " ", " "),
    ("no-switch", complex_field(f" DOCVARIABLE {VAULT} ", SPACE), VAULT, " ", " "),
    (
        "escaped-value",
        complex_field(f" DOCVARIABLE {VAULT} ", r("<w:t>a&amp;</w:t>") + r("<w:t>&lt;b</w:t>")),
        VAULT,
        "a&amp;&#60;b",
        "a&<b",
    ),
]


def read_settings(name: str, value: str) -> str:
    """The variable, beside one of another name and one of none."""
    held = variables((name, value), ("VAULT_ND_other", "x"))
    return held.replace("</w:docVars>", '<w:docVar w:val="y"/></w:docVars>')


@pytest.mark.parametrize(
    ("field", "name", "value", "shown"),
    [case[1:] for case in DOCVARIABLE_READS],
    ids=[case[0] for case in DOCVARIABLE_READS],
)
def test_a_docvariable_showing_its_variables_value_is_read(
    field: str, name: str, value: str, shown: str
) -> None:
    # Word shows the stored result until fields are updated, then the variable's value: where
    # the two are one text, it is what Word shows either way.
    data = anchored(field, read_settings(name, value))
    assert [x.text for x in read_docx(data)] == [f"4.1{shown}b"]
    value_read = json.loads(served(data)[0])
    assert "refusal" not in value_read, value_read.get("refusal")


_NESTED = complex_field(" QUOTE x ", r("<w:t>x</w:t>"))

# Each a DOCVARIABLE Word may show otherwise than read: its name, the field, the settings, the
# reader's refusal (code and detail).
DOCVARIABLE_REFUSALS: list[tuple[str, str, str, str, str]] = [
    (
        "no-variable",
        complex_field(f" DOCVARIABLE {VAULT} \\* MERGEFORMAT ", SPACE),
        "",
        "computed-field",
        "a DOCVARIABLE without one variable of its name",
    ),
    (
        "another-value",
        complex_field(f" DOCVARIABLE {VAULT} ", SPACE),
        variables((VAULT, "x")),
        "computed-field",
        "a DOCVARIABLE showing other than its value",
    ),
    (
        "another-case",
        complex_field(f" DOCVARIABLE {VAULT} ", SPACE),
        variables((_LOWER, " ")),
        "computed-field",
        "a DOCVARIABLE without one variable of its name",
    ),
    (
        "two-ignoring-case",
        complex_field(f" DOCVARIABLE {VAULT} ", SPACE),
        variables((VAULT, " "), (_LOWER, " ")),
        "computed-field",
        "a DOCVARIABLE without one variable of its name",
    ),
    (
        "no-value",
        complex_field(f" DOCVARIABLE {VAULT} ", SPACE),
        f'<w:docVars><w:docVar w:name="{VAULT}"/></w:docVars>',
        "computed-field",
        "a DOCVARIABLE without one variable of its name",
    ),
    (
        "empty-result",
        complex_field(f" DOCVARIABLE {VAULT} ", r("<w:t/>")),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE showing other than its value",
    ),
    (
        "hidden-result",
        complex_field(
            f" DOCVARIABLE {VAULT} ", r('<w:t xml:space="preserve"> </w:t>', "<w:vanish/>")
        ),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE showing other than its value",
    ),
    (
        "another-switch",
        complex_field(f" DOCVARIABLE {VAULT} \\h ", SPACE),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE field the reader cannot read",
    ),
    (
        "another-format",
        complex_field(f" DOCVARIABLE {VAULT} \\* Upper ", SPACE),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE field the reader cannot read",
    ),
    (
        "quoted-name",
        complex_field(f' DOCVARIABLE "{VAULT}" ', SPACE),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE field the reader cannot read",
    ),
    (
        "no-name",
        complex_field(" DOCVARIABLE \\* MERGEFORMAT ", SPACE),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE field the reader cannot read",
    ),
    (
        "bare-code",
        complex_field(" DOCVARIABLE ", SPACE),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE field the reader cannot read",
    ),
    (
        "format-switch-without-its-format",
        complex_field(f" DOCVARIABLE {VAULT} \\* ", SPACE),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE field the reader cannot read",
    ),
    (
        "field-in-its-code",
        r('<w:fldChar w:fldCharType="begin"/>')
        + r('<w:instrText xml:space="preserve"> DOCVARIABLE x </w:instrText>')
        + _NESTED
        + r('<w:fldChar w:fldCharType="separate"/>')
        + SPACE
        + r('<w:fldChar w:fldCharType="end"/>'),
        variables(("x", " ")),
        "computed-field",
        "a DOCVARIABLE field with a field in its code",
    ),
    (
        "tab",
        complex_field(f" DOCVARIABLE {VAULT} ", r("<w:tab/>")),
        variables((VAULT, "&#9;")),
        "computed-field",
        "a DOCVARIABLE's result holding other than text",
    ),
    (
        "symbol",
        complex_field(
            f" DOCVARIABLE {VAULT} ",
            r('<w:t xml:space="preserve"> </w:t>', '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>'),
        ),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE's result holding other than text",
    ),
    (
        "note-mark",
        complex_field(f" DOCVARIABLE {VAULT} ", SPACE + r('<w:footnoteReference w:id="1"/>')),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE's result holding other than text",
    ),
    (
        "field-in-its-result",
        complex_field(f" DOCVARIABLE {VAULT} ", _NESTED),
        variables((VAULT, "x")),
        "computed-field",
        "a field in a DOCVARIABLE's result",
    ),
    (
        "simple-field-in-its-result",
        complex_field(f" DOCVARIABLE {VAULT} ", simple_field(" HYPERLINK x ", SPACE)),
        variables((VAULT, " ")),
        "computed-field",
        "a field in a DOCVARIABLE's result",
    ),
    (
        "field-character-in-a-simple-result",
        r('<w:fldChar w:fldCharType="begin"/>')
        + r('<w:instrText xml:space="preserve"> HYPERLINK x </w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + simple_field(f" DOCVARIABLE {VAULT} ", SPACE + r('<w:fldChar w:fldCharType="end"/>')),
        variables((VAULT, " ")),
        "computed-field",
        "a DOCVARIABLE's result holding other than text",
    ),
    (
        "no-stored-result",
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve"> DOCVARIABLE {VAULT} </w:instrText>')
        + r('<w:fldChar w:fldCharType="end"/>'),
        variables((VAULT, " ")),
        "field-without-result",
        "a field with no stored result",
    ),
]


@pytest.mark.parametrize(
    ("field", "settings", "code", "detail"),
    [case[1:] for case in DOCVARIABLE_REFUSALS],
    ids=[case[0] for case in DOCVARIABLE_REFUSALS],
)
def test_a_docvariable_word_may_show_otherwise_is_refused(
    field: str, settings: str, code: str, detail: str
) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read_document(anchored(field, settings))
    assert (caught.value.code, caught.value.detail) == (code, detail)


def test_a_docvariable_result_past_its_paragraph_is_refused() -> None:
    body = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + r(f'<w:instrText xml:space="preserve"> DOCVARIABLE {VAULT} </w:instrText>')
        + r('<w:fldChar w:fldCharType="separate"/>')
        + SPACE
    ) + p(r('<w:fldChar w:fldCharType="end"/>'))
    with pytest.raises(DocxRefusedError) as caught:
        read_document(with_settings(body, variables((VAULT, " "))))
    assert caught.value.code == "unbalanced-field"


def _declaring(declaration: str, name: str, content: bytes, before: bytes = b"") -> bytes:
    """A one-paragraph .docx with ``name`` holding ``content`` under an XML ``declaration``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx(p(r("<w:t>x</w:t>"))))) as source:
        parts = {n: source.read(n) for n in source.namelist()}
    parts[name] = before + f'<?xml version="1.0" encoding="{declaration}"?>'.encode() + content
    with zipfile.ZipFile(buffer, "w") as package:
        for part, data in parts.items():
            package.writestr(part, data)
    return buffer.getvalue()


# A customXml part, as Word keeps a bibliography.
_ITEM = "customXml/item1.xml"
_SOURCES = b"<b:Sources xmlns:b='x'/>"


@pytest.mark.parametrize("spelling", ["us-ascii", "US-ASCII", "ascii", "us_ascii"])
def test_a_part_declaring_ascii_is_read_when_every_byte_is_ascii(spelling: str) -> None:
    # ASCII is UTF-8's first 128 characters: such a part reads alike either way. EMA
    # product-information files hold customXml parts that declare us-ascii, every byte below 0x80.
    assert [x.text for x in read_docx(_declaring(spelling, _ITEM, _SOURCES))] == ["x"]
    # The document itself too: a character reference is ASCII bytes for any character.
    body = document_xml(p(r("<w:t>caf&#233;</w:t>"))).encode()
    data = _declaring(spelling, "word/document.xml", body)
    assert [x.text for x in read_docx(data)] == ["caf\u00e9"]
    DocxSource(data).certify(json.loads(served(data)[0]))


@pytest.mark.parametrize(
    "data",
    [
        _declaring("us-ascii", _ITEM, "<b:Sources xmlns:b='x'>caf\u00e9</b:Sources>".encode()),
        _declaring("us-ascii", _ITEM, _SOURCES + b"<!-- \xef\xbb\xbf -->"),
        _declaring("windows-1252", _ITEM, _SOURCES),
        _declaring("ASCII-ish", _ITEM, _SOURCES),
    ],
    ids=["a-letter-past-ascii", "a-byte-past-ascii", "another-encoding", "an-unknown-encoding"],
)
def test_a_part_declaring_ascii_over_other_bytes_or_another_encoding_is_refused(
    data: bytes,
) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(data)
    assert caught.value.code == "invalid-package"
    with pytest.raises(CertificationError):
        DocxSource(data)


def test_an_underline_without_a_value_sets_nothing_at_any_level() -> None:
    """A bare ``w:u`` draws no underline and leaves a style's underline as it is.

    Word's answer: Microsoft Word 16.113.3 for Mac, asked 2026-10-05 with two documents of
    this very XML (uval.docx: the first three paragraphs; uval2.docx: the rest, its styles U and
    UC). A bare ``<w:u/>`` draws no underline, with a ``w:color`` or without; under a paragraph
    style's single underline the text stays underlined (single), and under a character style's
    double it stays underlined (double). The reader passes it over at every level, as absent.
    """
    styles = (
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        "</w:style>"
        '<w:style w:type="paragraph" w:styleId="U"><w:name w:val="U"/>'
        '<w:rPr><w:u w:val="single"/></w:rPr></w:style>'
        '<w:style w:type="character" w:styleId="UC"><w:name w:val="UC"/>'
        '<w:rPr><w:u w:val="double"/></w:rPr></w:style>'
    )
    underlined = '<w:pStyle w:val="U"/>'
    body = (
        p(r("<w:t>noval</w:t>", "<w:u/>"))
        + p(r("<w:t>single</w:t>", '<w:u w:val="single"/>'))
        + p(r("<w:t>plain</w:t>"))
        + p(r("<w:t>styleSingleRunBare</w:t>", "<w:u/>"), underlined)
        + p(r("<w:t>styleSingleRunNothing</w:t>"), underlined)
        + p(r("<w:t>charStyleDoubleRunBare</w:t>", '<w:rStyle w:val="UC"/><w:u/>'))
        + p(r("<w:t>bareWithColour</w:t>", '<w:u w:color="FF0000"/>'))
    )
    paragraphs = read_docx(docx(body, styles))
    assert [[m.kind for m in x.marks if m.kind == "underline"] for x in paragraphs] == [
        [],
        ["underline"],
        [],
        ["underline"],
        ["underline"],
        ["underline"],
        [],
    ]
    # The check works it out on its own: the result is certified.
    value = json.loads(served(docx(body, styles))[0])
    assert "refusal" not in value, value.get("refusal")
