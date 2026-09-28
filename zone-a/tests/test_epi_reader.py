"""The EMA ePI reader: the text a browser shows, or a refusal with a reason.

Most cases build a one-section Bundle in memory so each rule is tested on its own; one group
reads the pinned EMA ePIs, where the rules were found.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from zone_a.epi.reader import EpiRefusedError, read_div, read_epi, walk

SOURCES = Path(__file__).resolve().parents[2] / "labels" / "ema-epi" / "sources"
XHTML = "http://www.w3.org/1999/xhtml"


def div(inner: str) -> str:
    return f'<div xmlns="{XHTML}">{inner}</div>'


def texts(inner: str) -> list[str]:
    paragraphs, refusal, _ = read_div(div(inner))
    assert refusal is None, refusal
    return [paragraph.text for paragraph in paragraphs]


def refusal(inner: str) -> str:
    _, refused, _ = read_div(div(inner))
    assert refused is not None
    return refused.code


def kinds(inner: str) -> list[tuple[int, int, str]]:
    paragraphs, refused, _ = read_div(div(inner))
    assert refused is None, refused
    return [(m.start, m.end, m.kind) for m in paragraphs[0].marks]


def bundle(sections: list[dict[str, Any]], resource_type: Any = "Composition") -> bytes:
    composition = {"resourceType": resource_type, "title": "X", "date": "2024-01-01"}
    composition["section"] = sections
    return json.dumps(
        {"resourceType": "Bundle", "type": "document", "entry": [{"resource": composition}]}
    ).encode()


# --- text -----------------------------------------------------------------------------------


def test_blocks_are_paragraphs_and_whitespace_collapses_as_a_browser_shows_it() -> None:
    body = "<p>  Store\n   below\t25\u00a0\u00b0C. </p><p>\u00a0</p><p>Next<br/>line</p>"
    assert texts(body) == ["Store below 25\u00a0\u00b0C.", "\u00a0", "Next\nline"]


def test_inline_elements_are_read_through_and_a_picture_is_an_object_character() -> None:
    body = '<p><img src="#t"/>This <strong>is</strong> <a href="x">subject</a>.</p>'
    assert texts(body) == ["\ufffcThis is subject."]


def test_a_typed_object_character_is_refused() -> None:
    assert refusal("<p>a\ufffcb</p>") == "reserved-character"


def test_tables_carry_their_cell_and_lists_their_level() -> None:
    body = "<table><tbody><tr><td><p>a</p></td><td>b</td></tr></tbody></table><ul><li>c</li></ul>"
    paragraphs, _, _ = read_div(div(body))
    assert [(p.text, p.table) for p in paragraphs] == [
        ("a", (0, 0, 0)),
        ("b", (0, 0, 1)),
        ("c", None),
    ]
    assert paragraphs[2].numbering is not None


# --- defects read through by a stated rule, and refusals --------------------------------------


def test_a_bare_less_than_sign_is_text_and_is_noted() -> None:
    paragraphs, refused, notes = read_div(div("<p>GFR <\u00a060 mL/min</p>"))
    assert refused is None
    assert paragraphs[0].text == "GFR <\u00a060 mL/min"
    assert notes == ("1 unescaped '<' read as text (invalid XHTML)",)


@pytest.mark.parametrize(
    ("inner", "code"),
    [
        ("<p>a&nbsp;b</p>", "malformed-xhtml"),
        ('<p><img src="#t" annotationsrc="x"" /></p>', "malformed-xhtml"),
        ("<p><font>x</font></p>", "unsupported-element"),
        ("<p><ins>x</ins></p>", "unsupported-element"),
        ("<p><del>x</del></p>", "unsupported-element"),
        ('<p onclick="x">x</p>', "unsupported-attribute"),
        ('<p><span class="MsoCommentReference">x</span></p>', "embedded-comment"),
        ('<div class="msocomtxt"><p>reviewer note</p></div>', "embedded-comment"),
        ('<p style="visibility: hidden">x</p>', "unsupported-style"),
        ('<p style="display: none">x</p>', "unsupported-style"),
        ('<p style="font-size: small">x</p>', "unsupported-style"),
        ('<p style="text-transform: uppercase">x</p>', "unsupported-style"),
        ('<p style="vertical-align: 3pt">x</p>', "unsupported-style"),
        ('<p style="color">x</p>', "unsupported-style"),
    ],
)
def test_what_the_reader_cannot_vouch_for_refuses_the_section(inner: str, code: str) -> None:
    assert refusal(inner) == code


def test_a_dtd_is_refused() -> None:
    _, refused, _ = read_div("<!DOCTYPE x>" + div(""))
    assert refused is not None
    assert refused.code == "malformed-xhtml"


def test_layout_only_styles_are_ignored() -> None:
    style = (
        "margin: 0cm; line-height: 13pt; font-family: 'Times New Roman', serif; "
        "border-top: solid black 1pt; break-after: avoid; text-align: justify; "
        "font-weight: bold; visibility: visible; width: 10pt; padding: 0cm 5.4pt"
    )
    assert kinds(f'<p style="{style}">x</p>') == []


# --- marks ------------------------------------------------------------------------------------


def test_superscript_subscript_and_strike_are_marked() -> None:
    body = (
        '<p>10<sup>9</sup> CO<sub>2</sub> <s>no</s><span style="vertical-align: super">x</span></p>'
    )
    assert kinds(body) == [
        (2, 3, "superscript"),
        (6, 7, "subscript"),
        (8, 10, "strike"),
        (10, 11, "superscript"),
    ]


def test_colour_shading_and_faint_text_are_marked() -> None:
    body = (
        '<p><span style="color: red">r</span><span style="color: black">b</span>'
        '<span style="background: yellow">y</span><span style="background: white">w</span>'
        '<span style="color: white">h</span><span style="font-size: 1pt">t</span>'
        '<span style="text-decoration: line-through">s</span></p>'
    )
    assert kinds(body) == [
        (0, 1, "color-red"),
        (2, 3, "shading-yellow"),
        (4, 6, "faint"),
        (6, 7, "strike"),
    ]


def test_underline_is_marked() -> None:
    # An underline turns a sign into another: "<" underlined is drawn "≤" (fidelity-norm/3.0.0
    # review round 17), so a caller must see it.
    body = (
        '<p>CrCl <u>&lt;</u> 30 <a href="https://x.example/">&gt;</a> <a name="n">n</a> '
        '<span style="text-decoration: underline">+</span></p>'
    )
    assert kinds(body) == [(5, 6, "underline"), (10, 11, "underline"), (14, 15, "underline")]


def test_a_border_on_inline_text_is_marked_as_underline() -> None:
    # A border along inline text is drawn as a line under it (review round 18).
    body = (
        '<p><span style="border-bottom: 1px solid">&lt;</span> '
        '<span style="border: none">x</span> <span style="border-width: 0">y</span></p>'
    )
    assert kinds(body) == [(0, 1, "underline")]


def test_a_border_beside_inline_text_is_its_own_mark() -> None:
    # A border on the left is drawn as a bar beside the text: "|05 mg" (review round 19).
    body = (
        '<p><span style="border-left: 1px solid">0</span>5 mg '
        '<span style="border: 1px solid">x</span></p>'
    )
    assert kinds(body) == [(0, 1, "border"), (6, 7, "border"), (6, 7, "underline")]


# --- the Bundle -------------------------------------------------------------------------------


def test_sections_keep_their_code_title_and_nesting() -> None:
    data = bundle(
        [
            {
                "title": "4. CLINICAL PARTICULARS",
                "code": {"coding": [{"code": "200000029798"}]},
                "text": {"div": div("")},
                "section": [{"title": "Posology", "text": {"div": div("<p>x</p>")}}],
            }
        ]
    )
    document = read_epi(data)
    sections = walk(document.sections)
    assert [(s.code, s.title) for s in sections] == [
        ("200000029798", "4. CLINICAL PARTICULARS"),
        (None, "Posology"),
    ]
    assert sections[1].paragraphs[0].text == "x"
    assert document.quirks == ()


def test_the_ema_numeric_resource_type_is_accepted_by_name_and_recorded() -> None:
    document = read_epi(bundle([], resource_type=0))
    assert document.quirks == ("Composition.resourceType is 0, not 'Composition'",)


@pytest.mark.parametrize(
    "data",
    [
        b"not json",
        json.dumps({"resourceType": "List"}).encode(),
        json.dumps({"resourceType": "Bundle", "type": "collection", "entry": []}).encode(),
        json.dumps({"resourceType": "Bundle", "type": "document", "entry": []}).encode(),
        bundle([], resource_type="Binary"),
        bundle([{"text": {"div": div("")}}]),
    ],
)
def test_a_bundle_of_another_shape_is_refused(data: bytes) -> None:
    with pytest.raises(EpiRefusedError):
        read_epi(data)


# --- the pinned EMA ePIs ----------------------------------------------------------------------


def _read(name: str) -> list[Any]:
    return walk(read_epi((SOURCES / name).read_bytes()).sections)


def test_what_the_pinned_epis_refuse_and_note() -> None:
    brukinsa = _read("brukinsa-smpc-en.json")
    assert [(s.title, s.refusal.code) for s in brukinsa if s.refusal] == [
        ("4.8 Undesirable effects", "embedded-comment")
    ]
    nuvaxovid = _read("nuvaxovid-smpc-en.json")
    assert [(s.title, s.refusal.code) for s in nuvaxovid if s.refusal] == [
        ("SUMMARY OF PRODUCT CHARACTERISTICS", "malformed-xhtml")
    ]
    jentadueto = _read("jentadueto-smpc-en.json")
    assert not [s for s in jentadueto if s.refusal]
    assert sum(1 for s in jentadueto if s.notes) == 7
    # From epi-reader/1.2.0 Word's tab-stops (section 1) and the relative shifts of 4.2 are read,
    # as the importer's T reads them; 5.1's line height under 12pt still refuses.
    for name in ("imatinib-teva-smpc-en.json", "imatinib-teva-tablets-smpc-en.json"):
        assert [(s.title, s.refusal.detail) for s in _read(name) if s.refusal] == [
            ("5.1 Pharmacodynamic properties", "p line-height: 11.7pt")
        ], name


def test_the_black_triangle_is_read_where_the_markup_is_well_formed() -> None:
    root = _read("brukinsa-smpc-en.json")[0]
    assert root.paragraphs[0].text.startswith("\ufffcThis medicinal product is subject to")


# --- review round 1 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inner", "code"),
    [
        ("<table>STRAY<tr><td>a</td></tr></table>", "unsupported-element"),
        ("<table><tbody>STRAY<tr><td>a</td></tr></tbody></table>", "unsupported-element"),
        ("<table><tr>STRAY<td>a</td></tr></table>", "unsupported-element"),
        ("<table><tr><td>a</td>STRAY</tr></table>", "unsupported-element"),
        ('<p style="margin-left: -9999px">x</p>', "unsupported-style"),
        ('<p style="text-indent: -200pt">x</p>', "unsupported-style"),
        ("<p>a\u200bb</p>", "format-character"),
        ("<p>a\u202eb\u202c</p>", "format-character"),
        ("<p>soft\u00adhyphen</p>", "format-character"),
        ("<p><![CDATA[x]]></p>", "malformed-xhtml"),
    ],
)
def test_review_round_one_refusals(inner: str, code: str) -> None:
    assert refusal(inner) == code


def test_a_hanging_indent_is_layout() -> None:
    assert texts('<p style="margin-left: 36pt; text-indent: -18pt">x</p>') == ["x"]


def test_colours_are_normalised_and_inherit_is_not_a_mark() -> None:
    body = (
        '<p><span style="color: rgb(255, 255, 255)">a</span><span style="color: #FFF">b</span>'
        '<span style="color: red"><span style="color: inherit">c</span></span></p>'
    )
    assert kinds(body) == [(0, 2, "faint"), (2, 3, "color-red")]


def test_a_numbered_list_is_told_from_a_bulleted_one() -> None:
    paragraphs, _, _ = read_div(div("<ol><li>a</li></ol><ul><li>b</li></ul>"))
    assert [p.numbering.num_id if p.numbering else None for p in paragraphs] == [2, 1]


# --- review round 2 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "style",
    [
        "color: #ffffff00",
        "color: #fff0",
        "color: hsl(0, 0%, 100%)",
        "color: rgba(0 0 0 / 0)",
        "color: rgb(100%, 100%, 100%)",
        "color: rgba(0, 0, 0, 0.001)",
        "margin-left: -100%",
        "text-indent: -100%",
        "margin-left: -1e4px",
        "margin-left: -100vw",
        "text-indent: -999rem",
        "margin-left: calc(-9999px)",
    ],
)
def test_colours_and_offsets_the_reader_cannot_place_are_refused(style: str) -> None:
    assert refusal(f'<p style="{style}">x</p>') == "unsupported-style"


def test_nearly_white_is_faint_and_nearly_black_is_nothing() -> None:
    body = '<p><span style="color: #f5f5f5">a</span><span style="color: #0d0d0d">b</span></p>'
    assert kinds(body) == [(0, 1, "faint")]


# --- review round 3 -----------------------------------------------------------------------


def test_colour_keywords_are_not_marks_and_light_greys_are_faint() -> None:
    body = (
        '<p><span style="color: none">a</span><span style="background: auto">b</span>'
        '<span style="background: currentcolor">c</span><span style="color: #EFEFEF">d</span>'
        '<span style="color: #EEECE1">e</span></p>'
    )
    # From epi-reader/1.2.0 a background of the text's own colour (currentcolor) hides it.
    assert kinds(body) == [(2, 5, "faint")]


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("border-bottom: 1px solid", ["underline"]),
        ("border-top: 1px solid", ["border"]),
        ("border-left: 1px dotted", ["border"]),
        ("border: 1px solid", ["border", "underline"]),
        ("border: 1px", []),
        ("border: 0 solid", []),
        ("border-width: 1px; border-style: solid", ["border", "underline"]),
        ("border-width: 1px", []),
        ("border-width: 0 0 1px 0; border-style: none none solid none", ["underline"]),
        ("border-width: 1px; border-style: solid none", ["border", "underline"]),
        ("border-width: 1px; border-style: none solid", ["border"]),
        ("border-bottom-width: 1px; border-bottom-style: solid", ["underline"]),
        ("border-bottom: none", []),
        # Review round 33: a border under a pixel is still drawn (at one pixel).
        ("border-bottom: 0.5px solid", ["underline"]),
        ("border-color: red", []),
    ],
)
def test_inline_borders_are_read_side_by_side(style: str, expected: list[str]) -> None:
    # Review round 20: the 1-4 value shorthands are expanded per side, as a browser does.
    marks = kinds(f'<p><span style="{style}">&lt;</span></p>')
    assert sorted({kind for _, _, kind in marks}) == expected


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        # An !important declaration wins over a later normal one, as in a browser.
        ("border-bottom: 1px solid !important; border-bottom: none", ["underline"]),
        # A border image is drawn whatever the style.
        ("border-image: none", []),
        ("border-image: initial", []),
        # "initial" and "unset" are the initial values, none.
        ("border-bottom: 1px solid; border-bottom: initial", []),
        ("border-bottom: 1px solid; border-bottom-style: unset", []),
        # A shorthand resets every part it leaves out: no style is none (review round 34).
        ("border-bottom: 1px solid; border-bottom: 1px", []),
    ],
)
def test_inline_borders_cascade_as_a_browser_does(style: str, expected: list[str]) -> None:
    marks = kinds(f'<p><span style="{style}">&lt;</span></p>')
    assert sorted({kind for _, _, kind in marks}) == expected


@pytest.mark.parametrize(
    "style",
    [
        "border-bottom-width: 0 1px",
        "border-bottom-style: none solid",
        "border-width: 0 0 0 0 0",
        "border-bottom: nonsense",
        "border-bottom: 0 0",
        "border-bottom: var(--u, 1px solid)",
        "border-image: linear-gradient(black, black) 0 0 1 0",
        # A colour a browser does not accept, and a keyword among other values: dropped by a
        # browser, which keeps the earlier border (review round 22).
        "border-bottom: 1px solid; border-bottom: none #12345",
        "border-bottom: 1px solid; border-bottom: none auto",
        "border-bottom: 1px solid; border-bottom: 0 inherit",
        "border-bottom: 1px solid; border-bottom: none initial",
        # "inherit" takes the parent's border, which the reader does not follow.
        "border-bottom: inherit",
        # Two styles in one shorthand: a browser drops it and keeps the earlier border.
        "border-bottom: 1px solid; border-bottom: none dotted",
        # A lone closing bracket: a browser drops the declaration and keeps the earlier border.
        "border-bottom: 1px solid; border-bottom: none)",
    ],
)
def test_a_border_value_the_reader_cannot_read_whole_refuses(style: str) -> None:
    # A browser drops or reads these otherwise; the reader cannot tell which (review round 21).
    _, refused, _ = read_div(div(f'<p><span style="{style}">&lt;</span></p>'))
    assert refused is not None
    assert refused.code == "unsupported-style"


@pytest.mark.parametrize(
    ("family", "refused"),
    [
        ("'Times New Roman', serif", False),
        ("Verdana, sans-serif", False),
        ("Wingdings", True),
        ("Symbol", True),
        ("'Times New Roman', Symbol", True),
    ],
)
def test_a_font_outside_the_text_fonts_refuses(family: str, refused: bool) -> None:
    # A symbol-encoded font draws other glyphs: Wingdings "J" is a smiling face (review round 22).
    _, refusal, _ = read_div(div(f'<p><span style="font-family: {family}">J</span></p>'))
    assert (refusal is not None and refusal.code == "unsupported-style") == refused


@pytest.mark.parametrize(
    ("inner", "refused"),
    [
        # Overprint: an underscore pulled under ">" is drawn "≥" (review round 23).
        ('<p>CrCl &gt;<span style="margin-left:-12pt">_</span> 30</p>', True),
        ('<p style="margin-top:-6pt">x</p>', True),
        ('<p style="margin: 0cm -0.1pt 0cm 0cm">x</p>', False),
        ('<p>&lt;<span style="background-color:white;padding-top:30px">x</span></p>', True),
        ('<p><span style="padding-left:2pt">x</span></p>', False),
        ('<p style="height:0pt">Take 10</p>', True),
        ('<p style="line-height:4pt">x</p>', True),
        ('<p style="line-height:80%">x</p>', True),
        ('<p style="line-height:0.5">x</p>', True),
        ('<p style="line-height:13pt">x</p>', False),
        ('<p style="line-height:115%">x</p>', False),
        # Review round 24: a large font under its line, padding in a unit the reader cannot
        # place, vertical padding on inline text, a picture pulled over text.
        ('<p style="line-height:115%">x<span style="font-size:40pt">y</span></p>', True),
        ('<p style="font-size:40pt;line-height:8pt">x</p>', True),
        ('<p style="line-height:0.9em">x</p>', True),
        ('<p style="font-size:12pt">x</p>', False),
        ('<p><span style="background-color:white;padding-top:1.1rem">x</span></p>', True),
        ('<p><span style="background-color:white;padding:1.2ex 1ch">x</span></p>', True),
        ('<p><span style="padding-top:13pt;border-top:1px solid black">x</span></p>', True),
        (
            '<p>Do not crush.<img style="margin-left:-66pt" src="data:image/png;base64,AA=="/></p>',
            True,
        ),
        # Review round 25: a border wider than a hairline paints a band over the lines around;
        # a value the reader cannot place refuses instead of breaking the reading.
        ('<p>Keep<span style="border-top:24pt solid white"></span> dry.</p>', True),
        ('<p>Keep<span style="border-bottom:24pt solid black"></span> dry.</p>', True),
        ('<p>Keep<span style="border-bottom:solid"></span> dry.</p>', True),
        ('<p><span style="border-bottom:1px solid">x</span></p>', False),
        ('<p><span style="font-size:1rem">x</span></p>', True),
        ('<p style="line-height:1.2rem">x</p>', True),
        ('<p style="line-height:x%">x</p>', True),
        # Review round 34: a shorthand resets the width it leaves out (to medium); margin and
        # padding shorthands are read per side; the rules hold on every inline element.
        ('<p>1 <span style="border-bottom-width:0;border-bottom:solid">&lt;</span> 2</p>', True),
        ('<p>Keep<span style="border-bottom:thick solid"></span> dry.</p>', True),
        ('<p>a</p><p style="margin:-10pt 0 0 0">x</p>', True),
        ('<p style="margin:0 0 -10pt 0">x</p><p>b</p>', True),
        ('<p>a</p><p><span style="padding:10pt 0">x</span></p>', True),
        ('<p>a</p><p><span style="padding:0 10pt">x</span></p>', False),
        ('<p>&lt;<b style="padding-top:30px">x</b></p>', True),
        ('<p>a<span style="margin-left:-0.5pt">x</span></p>', True),
        ('<p>&lt;<span style="background:white;padding-left:2pt">x</span></p>', True),
        ('<p>&lt;<span style="padding-left:2pt">x</span></p>', False),
        ('<p style="line-height:0">x</p>', True),
        ('<p style="height:0">Take 10</p>', True),
    ],
)
def test_layout_that_overprints_text_refuses(inner: str, refused: bool) -> None:
    _, refusal, _ = read_div(div(inner))
    assert (refusal is not None and refusal.code == "unsupported-style") == refused


@pytest.mark.parametrize(
    ("inner", "refused"),
    [
        # Review round 26: margins and indents that add up move text off the page's left edge.
        ('<div style="margin-left:-72pt"><p style="margin-left:-72pt">Do not take</p></div>', True),
        ('<p style="margin-left:-72pt;text-indent:-72pt">Do not take</p>', True),
        ('<div style="text-indent:-72pt"><p style="margin-left:-60pt">Do not take</p></div>', True),
        ('<ul style="margin-left:-40pt"><li style="margin-left:-40pt">Do not take</li></ul>', True),
        ('<p style="margin-left:-6em">Do not take</p>', True),
        # A hanging indent under its own margin, and a small pull-back in a cell, stay.
        ('<p style="margin-left:72pt;text-indent:-72pt">Take</p>', False),
        ('<table><tr><td><p style="margin-left:-9pt">x</p></td></tr></table>', False),
        # Review round 27: a declaration a browser keeps (!important) or drops (invalid) cannot
        # hide a pull to the left; an indent reaches a cell from its row or row group, and a
        # block from an inline element; chained tables add up.
        ('<p style="margin-left:-72pt !important;margin-left:0">Do not take</p>', True),
        ('<p style="margin-left:-72pt;margin-left:0 0">Do not take</p>', True),
        ('<p style="margin-left:-72pt;margin:0 0 0 0 0">Do not take</p>', True),
        ('<p style="text-indent:-72pt;text-indent:auto">Do not take</p>', True),
        ('<table><tr style="text-indent:-72pt"><td>Take</td></tr></table>', True),
        ('<table><tbody style="text-indent:-72pt"><tr><td>Take</td></tr></tbody></table>', True),
        ('<div><span style="text-indent:-72pt"><p>Do not take</p></span></div>', True),
        ('<table style="margin-left:-11pt"><tr><td>' * 3 + "x" + "</td></tr></table>" * 3, True),
        # Review round 34: a cell's own margin gives no credit; an indent is order-free; a row's
        # indent does not leak out of a nested table into the next cell; an inline element
        # carries an indent down; each declaration alone is bounded by an inch.
        (
            '<table><tr><td style="margin-left:20pt"><p style="margin-left:-25pt">x</p>'
            "</td></tr></table>",
            True,
        ),
        (
            '<table><tr><td style="margin-left:20pt"><p style="margin-left:-5pt">x</p>'
            "</td></tr></table>",
            False,
        ),
        ('<p style="text-indent:-30pt !important;text-indent:0">x</p>', True),
        (
            '<table><tr style="text-indent:-10pt"><td><table><tr style="text-indent:0"><td>i'
            '</td></tr></table></td><td><p style="margin-left:-5pt">x</p></td></tr></table>',
            True,
        ),
        (
            '<table><tr style="text-indent:0"><td><table><tr style="text-indent:0"><td>i'
            '</td></tr></table></td><td><p style="margin-left:-5pt">x</p></td></tr></table>',
            False,
        ),
        ('<div><b style="text-indent:-30pt"><p>x</p></b></div>', True),
        ('<div style="margin-left:100pt"><p style="margin-left:-80pt">x</p></div>', True),
        ('<div style="margin-left:100pt"><p style="margin-left:-5.2em">x</p></div>', True),
        ('<div style="margin-left:100pt"><p style="margin-left:-5.1em">x</p></div>', False),
        ('<div style="margin-left:100pt"><p style="margin-left:-72pt">x</p></div>', False),
        # Each unit's size, either side of the 12pt bound.
        ('<p style="margin-left:-0.43cm">x</p>', True),
        ('<p style="margin-left:-0.42cm">x</p>', False),
        ('<p style="margin-left:-4.25mm">x</p>', True),
        ('<p style="margin-left:-4.2mm">x</p>', False),
        ('<p style="margin-left:-0.17in">x</p>', True),
        ('<p style="margin-left:-0.16in">x</p>', False),
        ('<p style="margin-left:-1.01pc">x</p>', True),
        ('<p style="margin-left:-1pc">x</p>', False),
        ('<p style="margin-left:-16.1px">x</p>', True),
        ('<p style="margin-left:-16px">x</p>', False),
    ],
)
def test_text_drawn_left_of_its_container_refuses(inner: str, refused: bool) -> None:
    _, refusal, _ = read_div(div(inner))
    assert (refusal is not None and refusal.code == "unsupported-style") == refused


def test_nesting_too_deep_to_read_refuses_the_section() -> None:
    _, refusal, _ = read_div(div("<p>" + "<span>" * 1200 + "x" + "</span>" * 1200 + "</p>"))
    assert refusal is not None
    assert refusal.code == "malformed-xhtml"


def test_sections_nested_too_deep_to_read_refuse_the_document() -> None:
    section: dict[str, Any] = {"title": "leaf"}
    for _ in range(900):
        section = {"title": "x", "section": [section]}
    with pytest.raises(EpiRefusedError):
        read_epi(bundle([section]))


@pytest.mark.parametrize(
    "sections",
    [[1], ["s"], [{"title": "x", "code": "x"}], [{"title": "x", "text": "x"}],
     [{"title": "x", "section": {"title": "y"}}], [{"title": "x", "code": {"coding": [1]}}],
     [{"title": "x", "code": {"coding": [{"code": ["x"]}]}}],
     [{"title": "x", "text": {"div": 5}}], [{"title": "x", "section": 5}],
     [{"title": "x", "code": {"coding": {"x": 1}}}]],
)  # fmt: skip
def test_a_bundle_of_the_wrong_shape_refuses_the_document(sections: list[Any]) -> None:
    with pytest.raises(EpiRefusedError):
        read_epi(bundle(sections))


def test_nesting_the_interpreter_cannot_follow_refuses_the_section() -> None:
    # Deep in a Bundle a div within the bound can still be too deep to read: a false failure.
    import sys

    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(120)
    try:
        _, refusal, _ = read_div(div("<p>" + "<span>" * 100 + "x" + "</span>" * 100 + "</p>"))
    finally:
        sys.setrecursionlimit(limit)
    assert refusal is not None
    assert refusal.detail == "nested too deeply to read"


@pytest.mark.parametrize(
    ("inner", "code"),
    [
        # Review round 28: a positive em gives no credit to the right (the font can be tiny).
        (
            '<div style="font-size:2pt;margin-left:5em">'
            '<p style="font-size:12pt;margin-left:-70pt">Do not take</p></div>',
            "unsupported-style",
        ),
        # A value a browser drops as invalid refuses instead of counting.
        ('<div style="margin-left:72pt 72pt"><p style="margin-left:-72pt">x</p></div>',
         "unsupported-style"),
        ('<p style="text-indent:auto">x</p>', "unsupported-style"),
        # A style CSS tokenizes other than a split on ";" does: a quote, a comment, an escape.
        ('<p><span style="border-bottom:1px solid black;font-family:\'arial;border-bottom:none">'
         "&lt;</span> 30</p>", "unsupported-style"),
        ('<p><span style="mso-a:/*;border-bottom:none;mso-b:*/">x</span></p>', "unsupported-style"),
        ('<p><span style="mso-a:x\\;border-bottom:none">x</span></p>', "unsupported-style"),
        ('<p style="mso-a:f(;margin-left:72pt;mso-b:)">x</p>', "unsupported-style"),
        # Where an HTML parser rebuilds the tree, the reader refuses.
        ('<p style="margin-left:72pt"><p style="margin-left:-72pt">x</p></p>', "malformed-xhtml"),
        ('<ul><li>a<li>b</li></li></ul>', "malformed-xhtml"),
        ('<p><a>x<a>y</a></a></p>', "malformed-xhtml"),
        ('<hr><p>x</p></hr>', "malformed-xhtml"),
        # Review round 29: each block an HTML parser moves out of an open p; a count of margin
        # values a browser drops; an unclosed function; markup an HTML parser reads otherwise.
        ('<p><div>x</div></p>', "malformed-xhtml"),
        ('<p><ul><li>x</li></ul></p>', "malformed-xhtml"),
        ('<p><ol><li>x</li></ol></p>', "malformed-xhtml"),
        ('<p><table><tr><td>x</td></tr></table></p>', "malformed-xhtml"),
        ('<p>a<hr/>b</p>', "malformed-xhtml"),
        ('<p><li>x</li></p>', "malformed-xhtml"),
        ('<p style="margin:72pt 72pt 72pt 72pt 0">x</p>', "unsupported-style"),
        ('<p><span style="border-bottom:1px solid black;mso-x:rgb(0;border-bottom:none">'
         "&lt;</span></p>", "unsupported-style"),
        ('<p>Hypersensitivity <?x >not<?y ?> to the active substances</p>', "malformed-xhtml"),
        ('<p>Take 2<!-->0 mg--> tablets</p>', "malformed-xhtml"),
        ('<p>eGFR <span style="border-bottom:1px solid black"/>&lt; 30</p>', "malformed-xhtml"),
        ('<p>10<sup/>6 mg</p>', "malformed-xhtml"),
        ('<h:div xmlns:h="http://www.w3.org/1999/xhtml"><p>x</p></h:div>', "malformed-xhtml"),
        ('<p>a</br>b</p>', "malformed-xhtml"),
        ('<p>&#150;</p>', "malformed-xhtml"),
        ('<p><td>x</td></p>', "malformed-xhtml"),
        # Review round 30: each stated rule pinned on its own, and the bounds at their edge.
        ('<p>a<br></br>b</p>', "malformed-xhtml"),
        ('<p>a&#0150;b</p>', "malformed-xhtml"),
        ('<p>a&#x96;b</p>', "malformed-xhtml"),
        ('<p>x<span style="background-color:yellow;padding-left:40pt">y</span></p>',
         "unsupported-style"),
        ('<table><tbody><tr style="text-indent:-50pt"><td>x</td></tr></tbody></table>',
         "unsupported-style"),
        ('<p style="font-size:15pt">x</p>', "unsupported-style"),
        ('<p style="line-height:95%">x</p>', "unsupported-style"),
        ('<p style="margin-left:-0.9em">x</p>', "unsupported-style"),
        # Review round 31: content in a void element, whatever it is; a style limited to ASCII;
        # a negative bottom margin; vertical padding at the bottom; a border with two colours;
        # the hairline and line-height bounds at their edge; a zero-padded hexadecimal C1 reference;
        # a root that is not a div.
        ('<p>1<img src="data:image/png;base64,AA==">x</img>0</p>', "malformed-xhtml"),
        ('<p>5<img src="data:image/png;base64,AA=="> </img>mg</p>', "malformed-xhtml"),
        ('<p>1<img src="data:image/png;base64,AA==">&#160;</img>0</p>', "malformed-xhtml"),
        ('<div>1<hr>&#160;</hr>0</div>', "malformed-xhtml"),
        ('<p><span style="border-bottom:1px solid;border-bottom:0 none blac&#x212A;">'
         "&lt;</span> 5</p>", "unsupported-style"),
        ('<p style="margin-bottom:-30pt">a</p><p>b</p>', "unsupported-style"),
        ('<p>x<span style="padding-bottom:13pt">y</span></p>', "unsupported-style"),
        ('<p><span style="border-bottom:1px solid black red">x</span></p>', "unsupported-style"),
        ('<p><span style="border-bottom:1pt solid">x</span></p>', "unsupported-style"),
        ('<p style="line-height:11.5pt">x</p>', "unsupported-style"),
        ('<p>a&#x0096;b</p>', "malformed-xhtml"),
        # Review round 32: a child in an img, a space in an hr, and each bound just past its edge.
        ('<p>1<img src="data:image/png;base64,AA=="><b>0</b></img> mg</p>', "malformed-xhtml"),
        ('<div>1<hr> </hr>0</div>', "malformed-xhtml"),
        ('<p><span style="border-bottom:1.01px solid">x</span></p>', "unsupported-style"),
        ('<p style="line-height:11.99pt">x</p>', "unsupported-style"),
        ('<p style="line-height:99.9%">x</p>', "unsupported-style"),
        ('<p style="font-size:14.01pt">x</p>', "unsupported-style"),
        ('<p style="margin-left:-12.01pt">x</p>', "unsupported-style"),
        # Review round 33: a list moved out of an open p on its own; a border width in em; a
        # table's positive offset not carried into its cells.
        ('<p>a<ol>b</ol></p>', "malformed-xhtml"),
        ('<p>a<ul>b</ul></p>', "malformed-xhtml"),
        ('<p><span style="border-bottom:0.1em solid">x</span></p>', "unsupported-style"),
        ('<div style="margin-left:20pt"><table><tr><td><p style="margin-left:-25pt">x</p>'
         "</td></tr></table></div>", "unsupported-style"),
    ],
)  # fmt: skip
def test_what_a_browser_reads_otherwise_refuses(inner: str, code: str) -> None:
    _, refusal, _ = read_div(div(inner))
    assert refusal is not None
    assert refusal.code == code


@pytest.mark.parametrize(
    "inner",
    [
        # What an HTML parser does not rebuild stays accepted: a list in a list item, a link in
        # a cell inside a link's paragraph, a block after a closed p, and colour in rgb().
        "<ul><li>a<ul><li>b</li></ul></li></ul>",
        "<div><a>x</a><table><tr><td><a>y</a></td></tr></table></div>",
        "<p>a</p><div>b</div>",
        '<p style="color:rgb(0, 0, 0)">x</p>',
        # Each bound itself is accepted: a hairline, 12pt and 100% lines, a 14pt font, 12pt left.
        '<p><span style="border-bottom:1px solid">x</span></p>',
        '<p style="line-height:12pt">x</p>',
        '<p style="line-height:100%">x</p>',
        '<p style="font-size:14pt">x</p>',
        '<p style="margin-left:-12pt">x</p>',
    ],
)
def test_what_an_html_parser_keeps_is_read(inner: str) -> None:
    _, refusal, _ = read_div(div(inner))
    assert refusal is None


def test_a_row_groups_style_marks_its_cells() -> None:
    # A struck or white row group draws its cells so (review round 29).
    marks = kinds(
        '<table><tbody style="text-decoration:line-through"><tr><td>x</td></tr></tbody></table>'
    )
    assert (0, 1, "strike") in marks


def test_the_nesting_bound_counts_elements_and_a_tables_row_group_and_row() -> None:
    # The bound refuses, not the interpreter's recursion limit (review round 30).
    def spans(depth: int) -> str:
        return "<p>" + "<span>" * depth + "x" + "</span>" * depth + "</p>"

    assert read_div(div(spans(126)))[1] is None
    refusal = read_div(div(spans(130)))[1]
    assert refusal is not None
    assert refusal.detail == "elements nested deeper than 128"

    # A table is four levels to its cell's content: table, row group, row, cell.
    def tables(depth: int) -> str:
        return "<table><tr><td>" * depth + "x" + "</td></tr></table>" * depth

    assert read_div(div(tables(30)))[1] is None
    refusal = read_div(div(tables(33)))[1]
    assert refusal is not None
    assert refusal.detail == "elements nested deeper than 128"


def test_a_root_that_is_not_a_div_and_a_lone_surrogate_refuse() -> None:
    _, refusal, _ = read_div('<p xmlns="http://www.w3.org/1999/xhtml">x</p>')
    assert refusal is not None
    assert refusal.code == "malformed-xhtml"
    _, refusal, _ = read_div(div("<p>a\ud800b</p>"))
    assert refusal is not None
    assert refusal.code == "malformed-xhtml"


def test_a_less_than_sign_before_a_digit_is_read_as_text() -> None:
    paragraphs, refusal, notes = read_div(div("<p>CrCl <30</p>"))
    assert refusal is None
    assert paragraphs[0].text == "CrCl <30"
    assert notes


@pytest.mark.parametrize("where", ["title", "code"])
def test_a_lone_surrogate_anywhere_refuses_the_document(where: str) -> None:
    section: dict[str, Any] = {"title": "x"}
    if where == "title":
        section["title"] = "a\ud800"
    else:
        section["code"] = {"coding": [{"code": "\udc00"}]}
    with pytest.raises(EpiRefusedError):
        read_epi(bundle([section]))


@pytest.mark.parametrize(
    "document",
    [
        b'{"resourceType":"Bundle","type":"document","entry":5}',
        b'{"resourceType":"Bundle","type":"document","x":' + b"1" * 5000 + b',"entry":[]}',
    ],
)
def test_a_bundle_python_cannot_read_refuses_the_document(document: bytes) -> None:
    with pytest.raises(EpiRefusedError):
        read_epi(document)


@pytest.mark.parametrize(
    ("inner", "code"),
    [
        # Each rule below is pinned alone (mutation run, review round 34).
        ('<p><span style="border-bottom:revert">&lt;</span></p>', "unsupported-style"),
        ('<p style="max-height:0">x</p>', "unsupported-style"),
        ('<p><span style="font-weight:bold)">x</span></p>', "unsupported-style"),
        ('<p style="color:rgb(300,0,0)">x</p>', "unsupported-style"),
        ('<p style="color">x</p>', "unsupported-style"),
        ('<table><tr><td style="text-decoration:overline">x</td></tr></table>',
         "unsupported-style"),
        (
            '<p><span style="border-bottom-width:1px;border-bottom:initial;'
            'border-bottom-style:solid">&lt;</span></p>',
            "unsupported-style",
        ),
        ('<p><span style="border-bottom:1px solid;border-color:inherit">&lt;</span></p>',
         "unsupported-style"),
        ('<p><span style="font-family:\'Ari&quot;al\'">x</span></p>', "unsupported-style"),
        ("<table><tbody><tr><td>x</td></tr></tbody>stray</table>", "unsupported-element"),
        ("<table><tbody><tr><td>x</td></tr>stray</tbody></table>", "unsupported-element"),
        ("<table>&#160;<tr><td>x</td></tr></table>", "unsupported-element"),
        ("<p><del>x</del></p>", "unsupported-element"),
        ('<p><b xmlns="urn:x">x</b></p>', "malformed-xhtml"),
        ("<p>" + "<span>" * 127 + "x" + "</span>" * 127 + "</p>", "malformed-xhtml"),
        ('<p><span class="MSOCOMANCHOR">x</span></p>', "embedded-comment"),
        ('<p><span class="MsoCommentReference">x</span></p>', "embedded-comment"),
        # Review round 35: each C1 reference a browser maps through windows-1252, at both
        # edges and in both cases; a quote inside a quoted family; control characters; table
        # parts a browser moves.
        ("<p>10&#128;</p>", "malformed-xhtml"),
        ("<p>Wait&#133;</p>", "malformed-xhtml"),
        ("<p>&#159;</p>", "malformed-xhtml"),
        ("<p>10&#x80;</p>", "malformed-xhtml"),
        ("<p>&#x8A;</p>", "malformed-xhtml"),
        ("<p>&#x9F;</p>", "malformed-xhtml"),
        ("<p>&#x9a;</p>", "malformed-xhtml"),
        ('<p><span style="font-family:\'arial&quot;\'">x</span></p>', "unsupported-style"),
        ("<p>Take&#127;5</p>", "format-character"),
        ("<p>Take\x7f5</p>", "format-character"),
        ("<p>Take\x855</p>", "format-character"),
        ("<p>Take\x805</p>", "format-character"),
        ("<p>Take\x9f5</p>", "format-character"),
        # A self-closing block: HTML ignores the "/" and moves the text after it inside.
        ('<div><p style="color:red"/>x</div>', "malformed-xhtml"),
        ("<table><tfoot><tr><td>x</td></tr></tfoot></table>", "unsupported-element"),
        ("<table><caption><b>c</b></caption><tr><td>x</td></tr></table>", "unsupported-element"),
        ("<table><colgroup></colgroup><tr><td>x</td></tr></table>", "unsupported-element"),
        ("<table><tbody><p>x</p></tbody></table>", "unsupported-element"),
        ("<table><tr><p>x</p></tr></table>", "unsupported-element"),
        ("<table><tbody><div><td>x</td></div></tbody></table>", "unsupported-element"),
        ("<table><tbody><tr><td>B</td></tr></tbody><thead><tr><td>A</td></tr></thead></table>",
         "unsupported-element"),
        ("<table><tr><td>B</td></tr><thead><tr><td>A</td></tr></thead></table>",
         "unsupported-element"),
    ],
)  # fmt: skip
def test_each_rule_refuses_alone(inner: str, code: str) -> None:
    _, refusal, _ = read_div(div(inner))
    assert refusal is not None
    assert refusal.code == code


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        # A cell closes the li around its table, so a list in it is not an li in an li.
        (
            "<ul><li><table><tr><td><ul><li>x</li></ul></td></tr></table></li></ul>",
            [("x", (0, 0, 0), 2, [])],
        ),
        (
            "<ul><li><table><tr><td><li>x</li></td></tr></table></li></ul>",
            [("x", (0, 0, 0), 1, [])],
        ),
        # A nested table's text belongs to the outer cell.
        (
            "<table><tr><td>o</td><td><table><tr><td>i</td></tr></table></td></tr></table>",
            [("o", (0, 0, 0), None, []), ("i", (0, 0, 1), None, [])],
        ),
        ("<ul><li>a<ul><li>b</li></ul></li></ul>", [("a", None, 1, []), ("b", None, 2, [])]),
        # A line break swallows the space before it; a trailing one is dropped.
        ("<p>a <br/>b</p>", [("a\nb", None, None, [])]),
        ("<p>a<br/></p>", [("a", None, None, [])]),
        ("<p>a<br/> b</p>", [("a\nb", None, None, [])]),
        # Review round 35: an a with any href, even an empty one, is underlined; one without
        # is not. U+00A0 is read as it is.
        ('<p>eGFR <a href="">&lt;</a> 30</p>', [("eGFR < 30", None, None, ["underline"])]),
        ('<p><a name="x">&lt;</a></p>', [("<", None, None, [])]),
        ("<p>~&#160;x</p>", [("~\xa0x", None, None, [])]),
        # The faint bounds at their edge, and a hidden border drawn as none.
        ('<p style="color:#e0e0e0">x</p>', [("x", None, None, ["faint"])]),
        ('<p style="color:#dfdfdf">x</p>', [("x", None, None, ["color-#dfdfdf"])]),
        ('<p style="font-size:2pt">x</p>', [("x", None, None, [])]),
        ('<p style="font-size:1.9pt">x</p>', [("x", None, None, ["faint"])]),
        ('<p><span style="border-bottom:1px hidden">&lt;</span></p>', [("<", None, None, [])]),
        (
            "<table><thead><tr><td>A</td></tr></thead><thead><tr><td>B</td></tr></thead></table>",
            [("A", (0, 0, 0), None, []), ("B", (0, 1, 0), None, [])],
        ),
        ('<p style="color:rgb(255,0,0)">x</p>', [("x", None, None, ["color-#ff0000"])]),
        ('<p style="color:#303030">x</p>', [("x", None, None, ["color-#303030"])]),
        ('<p style="color:#202020">x</p>', [("x", None, None, [])]),
        ('<p style="color:transparent">x</p>', [("x", None, None, ["faint"])]),
        (
            '<table><tr><td style="text-decoration:underline">x</td></tr></table>',
            [("x", (0, 0, 0), None, ["underline"])],
        ),
        ("<p><strike>x</strike></p>", [("x", None, None, ["strike"])]),
        (
            '<p><span style="border-bottom:1px solid #0008">&lt;</span></p>',
            [("<", None, None, ["underline"])],
        ),
        ('<p><span style="border-bottom:0ex solid">&lt;</span></p>', [("<", None, None, [])]),
        ('<p><span style="border-bottom:0rem solid">&lt;</span></p>', [("<", None, None, [])]),
        (
            '<p><span style="border-image:1">&lt;</span></p>',
            [("<", None, None, ["border", "underline"])],
        ),
        ("<p>" + "<span>" * 126 + "x" + "</span>" * 126 + "</p>", [("x", None, None, [])]),
    ],
)
def test_what_each_rule_reads(inner: str, expected: list[Any]) -> None:
    paragraphs, refusal, _ = read_div(div(inner))
    assert refusal is None, refusal
    assert [
        (
            p.text,
            p.table,
            p.numbering.level if p.numbering else None,
            sorted(m.kind for m in p.marks),
        )
        for p in paragraphs
    ] == expected


@pytest.mark.parametrize("changes", [{"type": "collection"}, {"resourceType": "Parameters"}])
def test_a_bundle_that_is_not_a_document_bundle_refuses(changes: dict[str, Any]) -> None:
    document = json.loads(bundle([{"title": "t", "text": {"div": div("<p>x</p>")}}]))
    document.update(changes)
    with pytest.raises(EpiRefusedError):
        read_epi(json.dumps(document).encode())


# --- audit B12 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        # Text on a background of its own colour cannot be seen: faint, whatever the colours.
        ('<p style="background: black">x</p>', ["faint", "shading-black"]),
        ('<p style="background: navy; color: navy">x</p>', ["faint", "shading-navy"]),
        ('<p style="background: #111111; color: #000">x</p>', ["faint", "shading-#111111"]),
        (
            '<p style="background: black"><span style="color: #0d0d0d">x</span></p>',
            ["faint", "shading-black"],
        ),
        # White text on a dark background is read, and reported by its colour.
        ('<p style="background: black; color: white">x</p>', ["color-white", "shading-black"]),
        (
            '<p style="background: black"><span style="color: white">x</span></p>',
            ["color-white", "shading-black"],
        ),
        # A child's readable colour undoes its parent's faint one; a transparent background
        # keeps what is under it; the last declaration wins.
        ('<p style="color: white"><span style="color: red">x</span></p>', ["color-red"]),
        (
            '<p style="background: black"><span style="background: transparent">x</span></p>',
            ["faint", "shading-black"],
        ),
        ('<p style="color: white; color: black">x</p>', []),
        # A row group's or a row's background reaches its cells' text.
        (
            '<table><tbody style="background: black"><tr><td>x</td></tr></tbody></table>',
            ["faint", "shading-black"],
        ),
        (
            '<table><tr style="background: navy; color: navy"><td>x</td></tr></table>',
            ["faint", "shading-navy"],
        ),
        (
            '<table><tr style="background: black"><td style="color: white">x</td></tr></table>',
            ["color-white", "shading-black"],
        ),
    ],
)
def test_text_is_faint_where_it_cannot_be_told_from_its_background(
    inner: str, expected: list[str]
) -> None:
    paragraphs, refused, _ = read_div(div(inner))
    assert refused is None, refused
    assert sorted({m.kind for m in paragraphs[0].marks}) == expected


@pytest.mark.parametrize(
    ("character", "code"),
    [
        ("&#xF0B3;", "private-use-character"),
        ("\ue000", "private-use-character"),
        ("\U000f0000", "private-use-character"),
        ("\ufe0f", "format-character"),
        ("\u115f", "format-character"),
        ("\u3164", "format-character"),
        ("\u034f", "format-character"),
        ("\u0378", "unassigned-character"),
        ("\U0002fffe", "unassigned-character"),
    ],
)
def test_a_character_a_browser_does_not_show_as_itself_refuses(character: str, code: str) -> None:
    assert refusal(f"<p>Very common ({character} 1/10)</p>") == code


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("position: relative; top: -5pt", ["superscript"]),
        ("position: relative; top: -5.0pt", ["superscript"]),
        ("position: relative; bottom: 3px", ["superscript"]),
        ("position: relative; top: 1.5pt", ["subscript"]),
        ("position: relative; top: .5pt", []),
        ("position: relative; top: 0", []),
        ("position: relative; top: -0.4em", ["superscript"]),
        ("position: relative; top: 6pt", ["subscript"]),
    ],
)
def test_a_relative_shift_is_read_as_the_importer_reads_it(style: str, expected: list[str]) -> None:
    assert [
        m.kind for m in read_div(div(f'<p>10<span style="{style}">9</span></p>'))[0][0].marks
    ] == [*expected]


@pytest.mark.parametrize(
    "inner",
    [
        '<p>a<span style="position: absolute; top: -5pt">9</span></p>',
        '<p>a<span style="position: static">9</span></p>',
        '<p>a<span style="top: -5pt">9</span></p>',
        '<p style="position: relative; top: -5pt">9</p>',
        '<p>a<sup style="position: relative; top: -5pt">9</sup></p>',
        '<p>a<span style="position: relative; top: -5pt; bottom: 1pt">9</span></p>',
        '<p>a<span style="position: relative">9</span></p>',
        '<p>a<span style="position: relative; top: -6.1pt">9</span></p>',
        '<p>a<span style="position: relative; top: -1em">9</span></p>',
        '<p>a<span style="position: relative; top: 5%">9</span></p>',
        '<p>a<span style="position: relative; top: 5">9</span></p>',
        '<p>a<span style="position: relative; top: -5pt; vertical-align: super">9</span></p>',
        '<p>a<span style="position: relative; top: -5pt; background: yellow">9</span></p>',
        '<p>a<span style="position: relative; top: -5pt"><span style="background: red">9</span>'
        "</span></p>",
        '<p>a<span style="left: 5pt">9</span></p>',
    ],
)
def test_any_other_shift_refuses(inner: str) -> None:
    assert refusal(inner) == "unsupported-style"


def test_words_tab_stops_are_ignored_as_a_browser_ignores_them() -> None:
    assert texts('<p style="tab-stops: 35.4pt; mso-pagination: none">1. x</p>') == ["1. x"]
