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


def docx(body: str, styles: str | None = None, doctype: bool = False) -> bytes:
    document = (
        ("<!DOCTYPE x>" if doctype else "")
        + f'<w:document xmlns:w="{W}" '
        + 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        + f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("word/document.xml", document)
        if styles is not None:
            package.writestr("word/styles.xml", f'<w:styles xmlns:w="{W}">{styles}</w:styles>')
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
        (p(r("<w:t>\uf0b3</w:t>", '<w:rFonts w:ascii="Wingdings"/>')), "private-use-character"),
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


def test_a_drawing_without_text_is_noted_not_refused() -> None:
    paragraphs = read_docx(docx(p(r("<w:drawing><wp:inline xmlns:wp='urn:wp'/></w:drawing>"))))
    assert [paragraph.has_drawing for paragraph in paragraphs] == [True]


@pytest.mark.parametrize("data", [b"not a zip", docx(p(r("<w:t>x</w:t>")), doctype=True)])
def test_invalid_packages_are_refused(data: bytes) -> None:
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(data)
    assert caught.value.code == "invalid-package"


def test_a_package_without_a_document_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("word/styles.xml", "<x/>")
    with pytest.raises(DocxRefusedError) as caught:
        read_docx(buffer.getvalue())
    assert caught.value.code == "invalid-package"


# --- structure --------------------------------------------------------------------------


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
    texts = _read("qrd-product-information-template-version-104_en.docx")
    # "5.1" is followed by a space and then a tab in EMA's file; the reader keeps both.
    assert "5.1 \tPharmacodynamic properties" in texts
    assert all("\uf07b" not in text and "\uf07d" not in text for text in texts)
