"""A Word label's SmPC as ePI sections (``zone_a.word_epi``): narrative, page and refusals.

Bodies are built here, paragraph by paragraph; the one real file is the EMA's QRD template,
pinned in ``qrd/sources/``. A seeded run of random bodies holds the two outputs to each other:
whatever is not refused, the fidelity scanner reads the narrative as the page.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import importlib.util
import io
import json
import random
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from label_docx import browser
from label_docx.reader import Anchored, CommentReference, Mark, Numbering, Paragraph, Picture

from zone_a import certified, drawing, word_epi
from zone_a.certified import Body, read_body
from zone_a.fidelity.normalize import normalize_text
from zone_a.fidelity.xhtml import xhtml_to_text
from zone_a.structure import structure
from zone_a.word_epi import GREY_SPAN, ROOT, RefusedError, _section, blank, sections

REPOSITORY = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((REPOSITORY / "qrd/registry/cap-smpc-en-10.4.json").read_text("utf-8"))
MAPPING = json.loads((REPOSITORY / "fhir/mappings/cap-smpc-en.json").read_text("utf-8"))
TEMPLATE = REPOSITORY / "qrd/sources/qrd-product-information-template-version-104_en.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SPEC = importlib.util.spec_from_file_location(
    "word_fixtures", Path(__file__).parents[1] / "scripts" / "word_fixtures.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
word_fixtures = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(word_fixtures)


def _p(
    text: str,
    *marks: tuple[int, int, str],
    label: str | None = None,
    level: int = 0,
    num: int = 1,
    table: tuple[int, int, int] | None = None,
    **extra: Any,
) -> Paragraph:
    numbering = None if label is None else Numbering(num, level, label, "tab")
    return Paragraph(text, None, numbering, table, marks=tuple(Mark(*m) for m in marks), **extra)


def _grid(
    columns: int, *rows: list[tuple[int, int, str | None]], before: int = 0
) -> dict[str, Any]:
    return {
        "grid": {
            "columns": columns,
            "rows": [
                {
                    "before": before,
                    "after": 0,
                    "cells": [{"column": c, "span": s, "merge": m} for c, s, m in row],
                }
                for row in rows
            ],
        },
        "parent": None,
        "reason": None,
    }


def _build(
    *paragraphs: Paragraph,
    tables: tuple[dict[str, Any], ...] = (),
    images: dict[str, bytes] | None = None,
) -> tuple[str, str]:
    """The narrative and page of a section holding ``paragraphs``; refusals raise."""
    body = Body(tuple(paragraphs), tables, images=images or {})
    div, text = _section(range(len(paragraphs)), body)
    return div or "", text


def _inner(div: str) -> str:
    assert div.startswith(ROOT)
    assert div.endswith("</div>")
    return div[len(ROOT) : -len("</div>")]


def _same(div: str, text: str) -> bool:
    return normalize_text(xhtml_to_text(div)) == normalize_text(text)


def test_a_paragraph_carries_its_marks_and_folds_raised_digits_on_the_page() -> None:
    div, text = _build(
        _p("Take 10 9/l now", (0, 4, "bold"), (2, 7, "italic"), (8, 9, "superscript")),
        _p("H2O a<b & c", (1, 2, "subscript")),
    )
    assert _inner(div) == (
        "<p><strong>Ta</strong><strong><em>ke</em></strong><em> 10</em> <sup>9</sup>/l now</p>"
        "<p>H<sub>2</sub>O a&lt;b &amp; c</p>"
    )
    assert text == "\nTake 10 \u2079/l now\nH\u2082O a<b & c\n"
    assert _same(div, text)


def test_a_line_break_is_br_and_a_line_on_the_page() -> None:
    div, text = _build(_p("one\ntwo"))
    assert _inner(div) == "<p>one<br/>two</p>"
    assert text == "\none\ntwo\n"


def test_lists_are_the_html_list_that_draws_their_labels() -> None:
    div, text = _build(
        _p("a", label="\u2022"),
        _p("b", label="\u2022"),
        _p("between"),
        _p("c", label="1.", num=2),
        _p("d", label="2.", num=2),
    )
    assert _inner(div) == (
        "<ul><li>a</li><li>b</li></ul><p>between</p><ol><li>c</li><li>d</li></ol>"
    )
    assert text == "\n\u2022 a\n\u2022 b\nbetween\n1. c\n2. d\n"
    assert _same(div, text)


@pytest.mark.parametrize(
    "labels",
    [
        ["3.", "4."],
        ["a.", "b."],
        ["i.", "ii."],
        ["I."],
        ["1.", "3."],
        ["a)"],
        ["-", "-"],
        ["\u2013"],
        ["o"],
        ["\u25aa", "\u25aa"],
        ["01."],
        ["\u2022", "-"],
    ],
)
def test_a_list_no_html_list_draws_is_written_as_its_labels(labels: list[str]) -> None:
    """FHIR's narrative rule (txt-1) allows no ``start`` or ``type`` on ``ol``, and EMA's
    stylesheet draws every ``ul`` with discs: each item is a ``p`` of its label as Word draws it."""
    div, text = _build(*(_p(f"item {n}", label=label) for n, label in enumerate(labels)))
    assert _inner(div) == "".join(f"<p>{label} item {n}</p>" for n, label in enumerate(labels))
    assert text == "\n" + "".join(f"{label} item {n}\n" for n, label in enumerate(labels))
    assert _same(div, text)


def test_a_list_as_text_is_written_apart_from_the_lists_html_draws() -> None:
    div, text = _build(
        _p("a", label="\u2022"),
        _p("b", label="-", num=2),
        _p("c", label="1.", num=3),
        _p("d", label="a)", num=4),
    )
    assert _inner(div) == "<ul><li>a</li></ul><p>- b</p><ol><li>c</li></ol><p>a) d</p>"
    assert text == "\n\u2022 a\n- b\n1. c\na) d\n"
    assert _same(div, text)
    labels = ["\u2022", "-", "1.", "a)"]
    paragraphs = [
        _p(x, label=y, num=n) for n, (x, y) in enumerate(zip("abcd", labels, strict=True))
    ]
    body = Body(tuple(paragraphs), ())
    assert word_epi.text_labels(range(4), body) == {1, 3}


def test_a_label_as_text_is_escaped_and_marks_stay_on_the_text() -> None:
    div, text = _build(_p("bold", (0, 4, "bold"), label="<a>"))
    assert _inner(div) == "<p>&lt;a&gt; <strong>bold</strong></p>"
    assert text == "\n<a> bold\n"
    assert _same(div, text)


def test_a_bullet_no_html_list_draws_in_a_cell_is_refused() -> None:
    """The page leaves a step 4 bullet glyph out of a cell, so the narrative cannot write it."""
    table = (_grid(1, [(0, 1, None)]),)
    with pytest.raises(RefusedError) as refused:
        _build(_p("x", label="\u25aa", table=(0, 0, 0)), tables=table)
    assert refused.value.code == "list-label"
    div, text = _build(_p("x", label="a)", table=(0, 0, 0)), tables=table)
    assert _inner(div) == "<table><tr><td><p>a) x</p></td></tr></table>"
    assert _same(div, text)
    div, text = _build(_p("x", label="\u2022", table=(0, 0, 0)), tables=table)
    assert _inner(div) == "<table><tr><td><ul><li>x</li></ul></td></tr></table>"
    assert _same(div, text)


def test_a_tab_after_a_bullet_glyph_that_begins_the_text_is_a_space() -> None:
    # Owner decision 2026-10-06: a typed bullet's tab is written as a space, as the EMA's own
    # ePIs carry no tab; on the page section 3 step 4 still reads the bullet as a list bullet.
    div, page = _build(_p("\u2022\tOnce a day"), _p("  \u25aa\tor twice"))
    assert _inner(div) == "<p>\u2022 Once a day</p><p>  \u25aa or twice</p>"
    assert page == "\n\u2022 Once a day\n  \u25aa or twice\n"


@pytest.mark.parametrize(
    "label",
    [
        *("-", "\u2013", "\u2014", "*", "**", "\u2020", "\u2021\u2021", "\u00a7", "#"),
        *("1.", "12)", "a)", "B.", "(c)", "(iv)", "ix.", "- ", "1. ", "\u2022 "),
    ],
)
def test_a_tab_after_a_typed_label_is_a_space(label: str) -> None:
    """Owner decisions of 2026-10-06: a dash, a footnote mark or "1.", "a)", "(iv)" typed before
    a tab is a label, as a typed bullet is; the narrative and the page write its tab as a space."""
    div, page = _build(_p(f"{label}\tTake once daily."))
    assert _inner(div) == f"<p>{label} Take once daily.</p>"
    assert page == f"\n{label} Take once daily.\n"
    assert _same(div, page)


CELL = (_grid(1, [(0, 1, None)]),)

# The labels of owner decision 9 (2026-10-07): a non-breaking hyphen, a caption's number.
CAPTIONS = (
    "\u2011",
    "Table 1",
    "Table 1:",
    "Table\u00a01.",
    "Table 12a:",
    "Figure 3.",
    "Figure 100",
)


@pytest.mark.parametrize("label", CAPTIONS)
def test_a_tab_after_a_non_breaking_hyphen_or_a_captions_number_is_a_space(label: str) -> None:
    """Owner decision 9 (2026-10-07): U+2011, the hyphen it draws (decision 8), and "Table 1:" or
    "Figure 3." typed before a tab are labels; the EMA's own ePIs write "Table 1: ..." with a
    space."""
    div, page = _build(_p(f"{label}\tTake once daily."), _p(f"{label}  \tor twice."))
    assert _inner(div) == f"<p>{label} Take once daily.</p><p>{label}   or twice.</p>"
    assert page == f"\n{label} Take once daily.\n{label}   or twice.\n"
    assert _same(div, page)


@pytest.mark.parametrize(
    "label", [*("\u2022", "-", "\u2013", "*", "\u2020\u2020", "1.", "a)", "(iv)", "- "), *CAPTIONS]
)
def test_a_tab_after_a_typed_label_in_a_table_cell_is_a_space(label: str) -> None:
    """Owner decision 9 (2026-10-07): in a table cell as outside one. The cell's line keeps the
    label as typed, a step 4 bullet glyph included: the page leaves out only a list's label."""
    div, page = _build(
        _p(f"{label}\tTake once daily.", table=(0, 0, 0)),
        _p(f"  {label}\tor twice.", table=(0, 0, 0)),
        tables=CELL,
    )
    assert _inner(div) == (
        f"<table><tr><td><p>{label} Take once daily.</p><p>  {label} or twice.</p></td></tr>"
        "</table>"
    )
    assert (
        page == f"\n\ufdd0\n\ufdd2\t\ufdd3\t{label} Take once daily.   {label} or twice.\n\ufdd1\n"
    )
    assert _same(div, page)


@pytest.mark.parametrize(
    ("text", "raised", "drawn", "paged"),
    [
        ("a\tx", 1, "<sup>a</sup> x", "a x"),
        ("1\tx", 1, "<sup>1</sup> x", "\u00b9 x"),
        ("*\tx", 1, "<sup>*</sup> x", "* x"),
        ("\u2020\tx", 1, "<sup>\u2020</sup> x", "\u2020 x"),
        ("12\tx", 2, "<sup>12</sup> x", "\u00b9\u00b2 x"),
        ("a,b\tx", 3, "<sup>a,b</sup> x", "a,b x"),
        # Then spaces, raised or not, then the tab.
        ("a  \tx", 1, "<sup>a</sup>   x", "a   x"),
        ("a  \tx", 3, "<sup>a  </sup> x", "a   x"),
        # The tab raised with its key is a raised space, as the narrative writes it.
        ("a\tx", 2, "<sup>a </sup>x", "a x"),
        ("1\tx", 2, "<sup>1 </sup>x", "\u00b9 x"),
    ],
)
def test_a_tab_after_a_raised_footnote_key_is_a_space(
    text: str, raised: int, drawn: str, paged: str
) -> None:
    """Owner decision 9 (2026-10-07): one to three code points, each inside a superscript mark,
    typed before a tab are a footnote key, in a table cell as outside one."""
    div, page = _build(_p(text, (0, raised, "superscript")))
    assert _inner(div) == f"<p>{drawn}</p>"
    assert page == f"\n{paged}\n"
    assert _same(div, page)
    div, page = _build(
        _p(text, (0, raised, "superscript"), table=(0, 0, 0)), _p("y", table=(0, 0, 0)), tables=CELL
    )
    assert _inner(div) == f"<table><tr><td><p>{drawn}</p><p>y</p></td></tr></table>"
    assert page == f"\n\ufdd0\n\ufdd2\t\ufdd3\t{paged} y\n\ufdd1\n"
    assert _same(div, page)


def test_every_other_tab_is_refused() -> None:
    # A tab is still Word's jump to a tab stop: a second one, one before the label, one in the
    # text, after a bare letter or number that is not raised (a column: "n<tab>= 50"), after a
    # decimal, after a list label, after four raised code points or a lead that mixes raised and
    # level ones, after a caption's word with no number or another number, in a table cell as
    # outside one.
    raised = "superscript"
    for paragraph in (
        _p("\u2022\t\tdouble"),
        _p("\t\u2022\tled"),
        _p("\t- led"),
        _p("a \u2022\tb"),
        _p("\u2022\ta\tb"),
        _p("-\ta\tb"),
        _p("n\t= 50"),
        _p("1\tTake"),
        _p("2.5\tmg"),
        _p("Adults\t10 mg"),
        _p("e.g.\tx"),
        _p("\u2022\tx", label="1."),
        _p("\u2011\u2011\tx"),
        _p("abcd\tx", (0, 4, raised)),
        _p("ab\tx", (0, 1, raised)),
        _p("ab\tx", (1, 2, raised)),
        _p("a b\tx", (0, 1, raised), (2, 3, raised)),
        _p("a\tx", (0, 1, "subscript")),
        # A raised key that is or may join right-to-left text: a tab separates bidi segments.
        _p("\u0627\t\u0646\u0635", (0, 1, raised)),
        _p("\u200f\tx", (0, 1, raised)),
        _p("a\u200f\tx", (0, 2, raised)),
        _p("\u0661\tx", (0, 1, raised)),
        _p("a\u0301\tx", (0, 2, raised)),
        _p("a\tx\ty", (0, 1, raised)),
        _p("a\tx", (0, 1, raised), label="1."),
        _p("Tables 1\tx"),
        _p("Table\tx"),
        _p("Table \tx"),
        _p("table 1\tx"),
        _p("TABLE 1\tx"),
        _p("Table  1\tx"),
        _p("Table\u20091\tx"),
        _p("Table 1234\tx"),
        _p("Table 1A\tx"),
        _p("Table 1ab\tx"),
        _p("Table 1.2\tx"),
        _p("Table 1)\tx"),
        _p("Table 1:.\tx"),
        _p("Table \u0661\tx"),
        _p("Fig. 1\tx"),
        _p("Table 1:\tx\ty"),
        _p("Table 1:\tx", label="1."),
    ):
        for tables, where in (((), None), (CELL, (0, 0, 0))):
            with pytest.raises(RefusedError) as refused:
                _build(dataclasses.replace(paragraph, table=where), tables=tables)
            assert refused.value.code == "tab", (paragraph.text, where)


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome is not installed")
def test_chrome_draws_a_typed_labels_tab_as_word_does_in_a_cell_and_outside() -> None:
    """The drawing check reads the tab as Chrome draws the space (both collapse to one space),
    and a raised key as raised, in a table cell as outside one."""
    paragraphs = (
        _p("Table 1:\tDose", table=(0, 0, 0)),
        _p("a\tx", (0, 1, "superscript"), table=(0, 0, 0)),
        _p("•\tOnce", table=(0, 0, 0)),
        _p("Figure 3.\tLevels"),
        _p("1\tSee", (0, 2, "superscript")),
    )
    body = Body(paragraphs, CELL)
    div, _ = _section(range(len(paragraphs)), body)
    section = {"key": "s", "refusal": None, "narrative": div, "paragraphs": [0, len(paragraphs)]}
    verdict = drawing.check(body, {"sections": [section]})
    assert verdict["sections"] == [{"key": "s", "agrees": True, "where": None}]
    # Held to the same drawing, a read whose key is not raised differs.
    level = dataclasses.replace(paragraphs[1], marks=())
    moved = Body((paragraphs[0], level, *paragraphs[2:]), CELL)
    assert not drawing.check(moved, {"sections": [section]})["sections"][0]["agrees"]


def test_a_table_carries_its_grid() -> None:
    # A cell spanning two columns, then one spanning two rows beside an ordinary cell.
    tables = (
        _grid(
            2, [(0, 2, None)], [(0, 1, "restart"), (1, 1, None)], [(0, 1, "continue"), (1, 1, None)]
        ),
    )
    div, text = _build(
        _p("head", table=(0, 0, 0)),
        _p("left", table=(0, 1, 0)),
        _p("x", label="\u2022", table=(0, 1, 1)),
        _p("y", label="\u2022", table=(0, 1, 1)),
        _p("", table=(0, 2, 0)),
        _p("z", table=(0, 2, 1)),
        _p("after"),
        tables=tables,
    )
    assert _inner(div) == (
        '<table><tr><td colspan="2"><p>head</p></td></tr>'
        '<tr><td rowspan="2"><p>left</p></td><td><ul><li>x</li><li>y</li></ul></td></tr>'
        "<tr><td><p>z</p></td></tr></table><p>after</p>"
    )
    assert text == (
        "\n\ufdd0\n"
        "\ufdd2\t\ufdd3\thead\t\ufdd4\t\n"
        "\ufdd2\t\ufdd3\tleft\t\ufdd3\tx y\n"
        "\ufdd2\t\ufdd5\t\t\ufdd3\tz\n"
        "\ufdd1\nafter\n"
    )
    assert _same(div, text)


def test_whitespace_alone_is_drawn_as_nothing_and_left_out() -> None:
    div, text = _build(_p("a"), _p(" \t\u00a0"), _p("b"))
    assert _inner(div) == "<p>a</p><p>b</p>"
    assert text == "\na\nb\n"


def test_an_underline_is_left_out_only_where_it_cannot_change_the_text() -> None:
    div, _ = _build(_p("Long-term use", (0, 13, "underline")))
    assert _inner(div) == "<p>Long-term use</p>"
    with pytest.raises(RefusedError) as refused:
        _build(_p("CrCl < 30", (5, 6, "underline")))
    assert refused.value.code == "underline"


@pytest.mark.parametrize("kind", sorted(word_epi.GREY))
def test_the_templates_grey_is_the_style_guides_silver_span(kind: str) -> None:
    """The EMA ePI style guide's form for QRD "not printed" text (owner decision 2026-10-06)."""
    div, text = _build(_p("Report it here", (7, 14, kind)))
    assert _inner(div) == f"<p>Report {GREY_SPAN}it here</span></p>"
    assert text == "\nReport it here\n"
    assert _same(div, text)


def test_the_grey_span_holds_the_other_marks() -> None:
    """``sup`` and ``sub`` hold no element, so the span is outermost."""
    div, text = _build(
        _p("m2 dose", (0, 7, "shading-D9D9D9"), (1, 2, "superscript"), (3, 7, "bold"))
    )
    assert _inner(div) == (
        f"<p>{GREY_SPAN}m</span>{GREY_SPAN}<sup>2</sup></span>{GREY_SPAN} </span>"
        f"{GREY_SPAN}<strong>dose</strong></span></p>"
    )
    assert text == "\nm\u00b2 dose\n"
    assert _same(div, text)


@pytest.mark.parametrize(
    "kind",
    [
        *("highlight-darkGray", "shading-D9D9D8", "highlight-yellow"),
        # Word draws these in other colours than the template's two (its own print, 2026-10-07).
        *("shading-BFBFBF", "shading-E6E6E6"),
        # Word prints 15% auto on auto or white as D9D9D9, but the reader spells no theme pattern
        # colour (a themed accent reads the same) and an auto fill may let a cell's paint through.
        *("shading-pct15-AUTO-AUTO", "shading-pct15-AUTO-FFFFFF", "shading-pct15-AUTO-E6E6E6"),
        # Nothing on record says how Word draws these.
        *("shading-pct10-AUTO-AUTO", "shading-pct20-AUTO-AUTO", "shading-pct15-AUTO-D9D9D9"),
        *("shading-pct15-000000-AUTO", "shading-pct15-AUTO-C0C0C0", "shading-C0C0C1"),
        *("shading-THEME-background1", "shading-clear-AUTO-C0C0C0"),
    ],
)
def test_another_grey_or_colour_is_refused(kind: str) -> None:
    with pytest.raises(RefusedError) as refused:
        _build(_p("x", (0, 1, kind)))
    assert refused.value.code == "formatting"


def test_the_greys_are_the_templates_two_and_the_shading_word_draws_as_its_highlight() -> None:
    """Decision 5's two marks, and C0C0C0 shading (decision 9, by Word's own print)."""
    assert frozenset({"highlight-lightGray", "shading-D9D9D9", "shading-C0C0C0"}) == word_epi.GREY


def _shaded_docx(shading: str) -> bytes:
    """A minimal .docx of one paragraph whose second word's run carries ``shading``."""
    run = '<w:r><w:t xml:space="preserve">{}</w:t></w:r>'
    body = (
        "<w:p>"
        + run.format("Report ")
        + f'<w:r><w:rPr>{shading}</w:rPr><w:t xml:space="preserve">it here</w:t></w:r>'
        + "</w:p>"
    )
    rels = "http://schemas.openxmlformats.org/package/2006/relationships"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        package.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{rels}"><Relationship Id="r1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        package.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="{rels}"/>')
        package.writestr(
            "word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
        )
    return out.getvalue()


def test_a_solid_c0c0c0_run_read_by_the_reader_is_the_silver_span_and_a_themed_one_is_not() -> None:
    """Through the label reader's certified read: a solid fill is opaque, and a theme fill is
    another kind (``shading-THEME-...``), refused."""
    solid = '<w:shd w:val="clear" w:color="auto" w:fill="C0C0C0"/>'
    body = read_body(_shaded_docx(solid))
    assert [(m.start, m.end, m.kind) for m in body.paragraphs[0].marks] == [
        (7, 14, "shading-C0C0C0")
    ]
    div, text = _section(range(len(body.paragraphs)), body)
    assert _inner(div or "") == f"<p>Report {GREY_SPAN}it here</span></p>"
    assert text == "\nReport it here\n"
    themed = solid.replace("/>", ' w:themeFill="background1"/>')
    body = read_body(_shaded_docx(themed))
    with pytest.raises(RefusedError) as refused:
        _section(range(len(body.paragraphs)), body)
    assert refused.value.code == "formatting"


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome is not installed")
def test_chrome_draws_every_grey_as_the_silver_span_word_shades() -> None:
    """Owner decision 9 (2026-10-07): C0C0C0 shading is drawn by Word as the light grey highlight;
    each grey is the silver span, and the drawing check reads it so, in a table cell as outside
    one."""
    paragraphs = tuple(
        _p(f"Report {n} here", (7, 8, kind), table=where)
        for where in (None, (0, 0, 0))
        for n, kind in enumerate(sorted(word_epi.GREY))
    )
    body = Body(paragraphs, (_grid(1, [(0, 1, None)]),))
    div, _ = _section(range(len(paragraphs)), body)
    section = {"key": "s", "refusal": None, "narrative": div, "paragraphs": [0, len(paragraphs)]}
    assert drawing.check(body, {"sections": [section]})["sections"][0]["agrees"]
    plain = Body(tuple(dataclasses.replace(p, marks=()) for p in paragraphs), body.tables)
    assert not drawing.check(plain, {"sections": [section]})["sections"][0]["agrees"]


@pytest.mark.parametrize(
    ("paragraph", "code"),
    [
        (_p("struck", (0, 6, "strike")), "formatting"),
        (_p("Caps", (0, 4, "caps")), "formatting"),
        (_p("faint", (0, 5, "faint")), "formatting"),
        (_p("raised", (0, 6, "position")), "formatting"),
        (_p("x", (0, 1, "highlight-yellow")), "formatting"),
        (_p("\u00a0", (0, 1, "shading-FFFF00")), "formatting"),
        (_p("x", (0, 1, "superscript"), (0, 1, "subscript")), "script"),
        (_p("a<b", (1, 2, "superscript")), "script"),
        (_p("x\u00bd", (1, 2, "superscript")), "script"),
        (_p("word\u00adbreak"), "soft-hyphen"),
        (_p("a\tb"), "tab"),
        (_p("a\ufffcb"), "picture"),
        (_p("x\n\u2022 y"), "bullet-after-break"),
        (_p(" ", label="1."), "empty-numbered"),
        (_p("x", comments=(CommentReference(0, 1),)), "comment"),
        (_p("x", mark_hidden=True), "hidden-mark"),
    ],
)
def test_what_the_closed_lists_leave_out_is_refused(paragraph: Paragraph, code: str) -> None:
    with pytest.raises(RefusedError) as refused:
        _build(paragraph)
    assert refused.value.code == code


def test_a_list_that_changes_level_or_misses_a_number_is_refused() -> None:
    with pytest.raises(RefusedError) as refused:
        _build(_p("a", label="1."), _p("b", label="\u2022", level=1))
    assert refused.value.code == "list-level"
    with pytest.raises(RefusedError) as refused:
        _build(_p("a", label="1."), _p("b", label="3."), _p("c", label="2", level=1))
    assert refused.value.code == "list-level"
    div, _ = _build(_p("a", label="1."), _p("b", label="3."))
    assert _inner(div) == "<p>1. a</p><p>3. b</p>"


@pytest.mark.parametrize(
    ("table", "paragraphs", "code"),
    [
        (
            {"grid": None, "parent": None, "reason": "h-merge"},
            [_p("x", table=(0, 0, 0))],
            "table-grid",
        ),
        (_grid(2, [(1, 1, None)], before=1), [_p("x", table=(0, 0, 0))], "table-shape"),
        (
            _grid(1, [(0, 1, None)], [(0, 1, "continue")]),
            [_p("x", table=(0, 0, 0)), _p("", table=(0, 1, 0))],
            "table-shape",
        ),
        (
            _grid(1, [(0, 1, "restart")], [(0, 1, "continue")]),
            [_p("x", table=(0, 0, 0)), _p("y", table=(0, 1, 0))],
            "table-shape",
        ),
        (
            _grid(2, [(0, 2, "restart")], [(0, 1, "continue"), (1, 1, None)]),
            [_p("x", table=(0, 0, 0)), _p("", table=(0, 1, 0)), _p("", table=(0, 1, 1))],
            "table-shape",
        ),
    ],
)
def test_a_table_these_rules_cannot_draw_is_refused(
    table: dict[str, Any], paragraphs: list[Paragraph], code: str
) -> None:
    with pytest.raises(RefusedError) as refused:
        _build(*paragraphs, tables=(table,))
    assert refused.value.code == code


def test_a_nested_table_is_refused() -> None:
    tables = (_grid(1, [(0, 1, None)]), {**_grid(1, [(0, 1, None)]), "parent": [0, 0, 0]})
    with pytest.raises(RefusedError) as refused:
        _build(_p("outer", table=(0, 0, 0)), _p("inner", table=(1, 0, 0)), tables=tables)
    assert refused.value.code == "nested-table"


def _structured(headings: dict[str, int]) -> dict[str, Any]:
    return {
        "structurer": "smpc-structure/1.0.0",
        "ready": True,
        "end": None,
        "sections": [
            {"key": k, "parent": None, "code": f"code-{k}", "heading": h}
            for k, h in headings.items()
        ],
    }


def test_sections_are_cut_at_every_heading_and_an_empty_one_has_no_narrative() -> None:
    body = Body((_p("4. X"), _p("4.1 Y"), _p("text"), _p(" "), _p("4.2 Z")), ())
    built = sections(body, _structured({"smpc.4": 0, "smpc.4.1": 1, "smpc.4.2": 4}), REGISTRY)
    by_key = {s["key"]: s for s in built["sections"]}
    assert by_key["smpc.4"]["narrative"] is None
    assert by_key["smpc.4"]["page"] == ""
    assert by_key["smpc.4.1"]["paragraphs"] == [2, 4]
    assert by_key["smpc.4.1"]["page"] == "\ntext\n"
    assert by_key["smpc.4.1"]["title"] == "4.1 Y"
    assert built["refused"] == 0


def test_a_table_across_two_sections_or_under_a_heading_is_refused() -> None:
    table = (_grid(1, [(0, 1, None)], [(0, 1, None)]),)
    body = Body((_p("4.1 Y"), _p("a", table=(0, 0, 0)), _p("4.2 Z", table=(0, 1, 0))), table)
    built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
    codes = {s["key"]: s["refusal"]["code"] for s in built["sections"]}
    assert codes == {"smpc.4.1": "table-across-sections", "smpc.4.2": "heading-in-table"}


def test_a_floating_object_refuses_its_section_alone_and_an_unready_structure_is_an_error() -> None:
    box = (Anchored(0, "text-box"),)
    body = Body(
        (_p("4.1 Y"), _p("text"), _p("", anchored=box), _p("4.2 Z"), _p("more"), _p("4.3 W")), ()
    )
    built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 3, "smpc.4.3": 5}), REGISTRY)
    refusals = {s["key"]: s["refusal"] for s in built["sections"]}
    assert refusals == {
        "smpc.4.1": {"code": "anchored-object", "paragraph": 2, "detail": "text-box"},
        "smpc.4.2": None,
        "smpc.4.3": None,
    }
    # One anchored in a heading refuses the heading's section: Word draws it there.
    logo = (Anchored(3, "picture"),)
    body = Body((_p("4.1 Y", anchored=logo), _p("text"), _p("4.2 Z"), _p("more")), ())
    built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
    assert [(s["refusal"] or {}).get("detail") for s in built["sections"]] == ["picture", None]
    with pytest.raises(ValueError, match="not ready"):
        sections(body, {**_structured({}), "ready": False}, REGISTRY)


def test_the_qrd_template_carries_what_its_markup_allows() -> None:
    body = read_body(TEMPLATE.read_bytes())
    # 6.5 and 6.6 as a person assigns them (test_structure.py).
    structured = structure(body.paragraphs, REGISTRY, MAPPING, {"smpc.6.5": 192, "smpc.6.6": 196})
    built = sections(body, structured, REGISTRY)
    codes = Counter((s["refusal"] or {}).get("code", "carried") for s in built["sections"])
    # The template's guidance in angle brackets and its tabs; its black triangle, drawn 0.9% out
    # of its own proportions, and its grey (a silver span, from word-epi 1.3.0) are carried.
    assert codes == {"carried": 27, "underline": 3, "tab": 2}


# ---- the two outputs, held to each other ---------------------------------------------------------

_WORDS = [
    "Take",
    "10",
    "mg",
    "(",
    ")",
    "-",
    "+",
    "=",
    "\u2265",
    "e\u0301",
    "\u03bcg",
    "x",
    "1.5",
    ",",
    "a",
    "<",
    ">",
    "&",
]
_LABELS = ["\u2022", "1.", "2.", "3.", "a.", "b.", "i.", "ii.", "I.", "a)", "-"]
_KINDS = ["bold", "italic", "superscript", "subscript"] * 3 + ["underline"]


def _random_paragraph(rng: random.Random, table: tuple[int, int, int] | None) -> Paragraph:
    text = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(0, 6)))
    if rng.random() < 0.1:
        text += "\n" + rng.choice(_WORDS)
    marks = []
    for _ in range(rng.randint(0, 3)):
        if text:
            start = rng.randrange(len(text))
            marks.append((start, rng.randint(start + 1, len(text)), rng.choice(_KINDS)))
    label = rng.choice(_LABELS) if rng.random() < 0.4 else None
    return _p(text, *marks, label=label, num=rng.randint(1, 2), table=table)


def _random_body(rng: random.Random) -> tuple[tuple[Paragraph, ...], tuple[dict[str, Any], ...]]:
    paragraphs: list[Paragraph] = []
    tables: list[dict[str, Any]] = []
    for _ in range(rng.randint(1, 6)):
        if rng.random() < 0.25:
            t = len(tables)
            columns = rng.randint(1, 3)
            rows = []
            for r in range(rng.randint(1, 3)):
                cells, column = [], 0
                while column < columns:
                    span = rng.randint(1, columns - column)
                    merge = (
                        rng.choice([None, None, "restart", "continue"])
                        if r
                        else rng.choice([None, "restart"])
                    )
                    cells.append((column, span, merge))
                    column += span
                rows.append(cells)
                for c in range(len(cells)):
                    for _ in range(rng.randint(1, 2)):
                        paragraphs.append(_random_paragraph(rng, (t, r, c)))
            tables.append(_grid(columns, *rows))
        else:
            paragraphs.append(_random_paragraph(rng, None))
    return tuple(paragraphs), tuple(tables)


def test_whatever_is_not_refused_the_scanner_reads_as_the_page() -> None:
    rng = random.Random(20261005)
    outcomes: Counter[str] = Counter()
    for _ in range(3000):
        paragraphs, tables = _random_body(rng)
        try:
            div, text = _build(*paragraphs, tables=tables)
        except RefusedError as refused:
            outcomes[refused.code] += 1
            continue
        outcomes["carried"] += 1
        try:
            scanned = normalize_text(xhtml_to_text(div))
        except ValueError as error:
            outcomes[f"scanner:{getattr(error, 'code', '?')}"] += 1
            continue
        assert scanned == normalize_text(text)
    # The run must reach both outcomes often enough to mean something.
    assert outcomes["carried"] > 250
    assert sum(outcomes.values()) - outcomes["carried"] > 500


# ---- real Word SmPCs, and what Chrome draws for them -------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "word-smpc"
RECORDED = json.loads((FIXTURES / "chrome.json").read_text("utf-8"))
# Each fixture's sections, carried or refused (scripts/word_fixtures.py, and the folder's README).
OUTCOMES = {
    "brukinsa-smpc-en": {"carried": 29, "formatting": 3},
    "imatinib-teva-smpc-en": {"carried": 29, "underline": 2, "formatting": 1},
    "imatinib-teva-tablets-smpc-en": {"carried": 29, "underline": 2, "formatting": 1},
    "jentadueto-smpc-en": {"carried": 30, "formatting": 1, "underline": 1},
    "nuvaxovid-smpc-en": {"carried": 30, "formatting": 2},
}


def _fixture(name: str) -> tuple[Body, dict[str, Any]]:
    body = read_body((FIXTURES / f"{name}.docx").read_bytes())
    structured = structure(body.paragraphs, REGISTRY, MAPPING, word_fixtures.ASSIGNED.get(name))
    return body, sections(body, structured, REGISTRY)


def _replay(monkeypatch: pytest.MonkeyPatch, name: str, built: dict[str, Any]) -> None:
    """Chrome's recorded answers for the fixture, in place of Chrome."""
    recorded = RECORDED["documents"][name]
    divs = [s["narrative"] for s in built["sections"] if s["refusal"] is None and s["narrative"]]
    # A recording is of the narratives it was made of: re-record after the builder changes.
    assert word_fixtures.digest(divs) == recorded["narratives"]
    monkeypatch.setattr(browser, "browser_sections", lambda _divs, _chrome: recorded["shown"])
    monkeypatch.setattr(browser, "browser_markers", lambda _divs, _chrome: recorded["markers"])
    monkeypatch.setattr(browser, "chrome_version", lambda _chrome: RECORDED["application"])


@pytest.mark.parametrize("name", sorted(OUTCOMES))
def test_a_word_smpc_carries_what_the_closed_lists_allow(name: str) -> None:
    _, built = _fixture(name)
    codes = Counter((s["refusal"] or {}).get("code", "carried") for s in built["sections"])
    assert codes == OUTCOMES[name]


@pytest.mark.parametrize("name", sorted(OUTCOMES))
def test_chrome_draws_every_carried_section_as_word_does(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    body, built = _fixture(name)
    _replay(monkeypatch, name, built)
    verdict = drawing.check(body, built)
    assert verdict["checker"] == drawing.DRAWING_VERSION
    assert verdict["sections"]
    assert all(v["agrees"] for v in verdict["sections"])


def _tampered(body: Body, index: int, **change: Any) -> Body:
    paragraphs = list(body.paragraphs)
    paragraphs[index] = dataclasses.replace(paragraphs[index], **change)
    return dataclasses.replace(body, paragraphs=tuple(paragraphs))


def test_the_drawing_check_sees_a_read_that_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Held against the same drawing, a read with one change differs where it changed."""
    name = "jentadueto-smpc-en"
    body, built = _fixture(name)
    _replay(monkeypatch, name, built)
    section = next(s for s in built["sections"] if s["key"] == "smpc.4.1")
    at = section["paragraphs"][0]
    while blank(body.paragraphs[at]):
        at += 1
    text = body.paragraphs[at].text
    changes = {
        "a letter": {"text": text[:-1] + ("x" if text[-1] != "x" else "y")},
        "a mark": {"marks": (*body.paragraphs[at].marks, Mark(0, 1, "superscript"))},
        "a label": {"numbering": Numbering(99, 0, "1.", "tab")},
    }
    for change in changes.values():
        verdicts = drawing.check(_tampered(body, at, **change), built)["sections"]
        assert not next(v for v in verdicts if v["key"] == "smpc.4.1")["agrees"]


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome is not installed")
def test_chrome_itself_draws_the_fixtures_as_recorded() -> None:
    for name in sorted(OUTCOMES):
        body, built = _fixture(name)
        assert all(v["agrees"] for v in drawing.check(body, built)["sections"])


def test_the_script_writes_the_sections_the_structure_or_the_refusal(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "epi_from_word", Path(__file__).parents[1] / "scripts" / "epi_from_word.py"
    )
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    out = tmp_path / "epi.json"
    label = FIXTURES / "jentadueto-smpc-en.docx"
    assert script.main([str(label), "--no-drawing", "--out", str(out)]) == 0
    result = json.loads(out.read_text("utf-8"))
    assert result["epi"]["builder"] == word_epi.WORD_EPI_VERSION
    assert result["product"]["products"] == ["EU/1/12/780"]
    assert result["drawing"] is None
    assert len(result["epi"]["sections"]) == sum(OUTCOMES["jentadueto-smpc-en"].values())
    # A structure a person must still confirm: the structure alone.
    assert script.main([str(TEMPLATE), "--no-drawing", "--out", str(out)]) == 0
    result = json.loads(out.read_text("utf-8"))
    assert result["structure"]["ready"] is False
    assert "epi" not in result
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    assert script.main([str(broken), "--out", str(out)]) == 0
    assert json.loads(out.read_text("utf-8"))["refusal"]["code"] == "invalid-package"
    # The package leaflet: the template's own, its name "X", its bracketed headings for a person.
    assert script.main([str(TEMPLATE), "--no-drawing", "--document", "pl", "--out", str(out)]) == 0
    (only,) = json.loads(out.read_text("utf-8"))["leaflets"]
    statuses = {s["key"]: s["status"] for s in only["structure"]["sections"]}
    assert only["structure"]["name"] == "X"
    assert statuses["pl"] == statuses["pl.1"] == statuses["pl.6.revised"] == "mapped"
    assert statuses["pl.2"] == "missing"
    assert "epi" not in only
    # An assignment to a paragraph in no leaflet is an error, not dropped.
    with pytest.raises(SystemExit):
        script.main([str(TEMPLATE), "--no-drawing", "--document", "pl", "--assign", "pl.2=3"])
    # An SmPC alone has no leaflet.
    assert script.main([str(label), "--no-drawing", "--document", "pl", "--out", str(out)]) == 0
    assert json.loads(out.read_text("utf-8"))["leaflets"] == {
        "ready": False,
        "reason": "0 lines 'B. PACKAGE LEAFLET', expected one",
    }


# ---- the independent review's cases (2026-10-05) -------------------------------------------------


def test_the_drawing_check_refuses_what_chrome_drew_otherwise_or_did_not_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "jentadueto-smpc-en"
    body, built = _fixture(name)
    _replay(monkeypatch, name, built)
    section = next(s for s in built["sections"] if s["key"] == "smpc.4.1")
    at = next(i for i in range(*section["paragraphs"]) if not blank(body.paragraphs[i]))
    tampered = _tampered(body, at, text=body.paragraphs[at].text + "x")
    refused = drawing.refuse(built, drawing.check(tampered, built))
    by_key = {s["key"]: s for s in refused["sections"]}
    assert by_key["smpc.4.1"]["refusal"]["code"] == "drawn-otherwise"
    assert by_key["smpc.4.1"]["narrative"] is None
    assert by_key["smpc.4.3"]["refusal"] is None
    undrawn = drawing.refuse(built, None)
    assert {
        s["refusal"]["code"] for s in undrawn["sections"] if s["narrative"] is None and s["refusal"]
    } >= {"not-drawn"}
    assert all(s["refusal"] is not None for s in undrawn["sections"] if s["page"])


def _rewritten(name: str, change: Callable[[str], str]) -> bytes:
    """The fixture with its document part rewritten."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(FIXTURES / f"{name}.docx") as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            data = source.read(item)
            if item.filename == "word/document.xml":
                changed = change(data.decode("utf-8"))
                assert changed != data.decode("utf-8")
                data = changed.encode("utf-8")
            target.writestr(item, data)
    return out.getvalue()


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (
            lambda x: x.replace(
                "<w:tblPr>", '<w:tblPr><w:tblpPr w:vertAnchor="text" w:tblpY="7200"/>', 1
            ),
            "floating-table",
        ),
        (lambda x: x.replace("<w:tblPr>", "<w:tblPr><w:bidiVisual/>", 1), "right-to-left-table"),
        (
            lambda x: x.replace(
                "<w:pPr>", '<w:pPr><w:framePr w:hAnchor="page" w:x="8000" w:y="2000"/>', 1
            ),
            "frame",
        ),
        (
            lambda x: x.replace(
                "glycaemic control",
                'glycaemic</w:t></w:r><w:r><w:br w:type="page"/><w:t>control',
                1,
            ),
            "page-break",
        ),
    ],
)
def test_what_word_draws_and_the_read_does_not_say_refuses_the_document(
    change: Callable[[str], str], code: str
) -> None:
    body = read_body(_rewritten("jentadueto-smpc-en", change))
    assert body.layout == (code,)
    structured = structure(body.paragraphs, REGISTRY, MAPPING)
    with pytest.raises(RefusedError) as refused:
        sections(body, structured, REGISTRY)
    assert refused.value.code == code


_WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
# A floating text box, as Word writes one (its VML fallback left out).
_TEXT_BOX = (
    '<w:p><w:r><mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/'
    'markup-compatibility/2006"><mc:Choice Requires="wps"><w:drawing>'
    '<wp:anchor xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    f'<a:graphicData uri="{_WPS}"><wps:wsp xmlns:wps="{_WPS}"><wps:txbx><w:txbxContent>'
    "<w:p><w:r><w:t>Months</w:t></w:r></w:p></w:txbxContent></wps:txbx></wps:wsp>"
    "</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice></mc:AlternateContent>"
    "</w:r></w:p>"
)


def test_a_floating_text_box_refuses_the_section_it_is_anchored_in_and_no_other() -> None:
    def add(xml: str) -> str:
        at = xml.index("</w:p>", xml.index("glycaemic control")) + len("</w:p>")
        return xml[:at] + _TEXT_BOX + xml[at:]

    def built(body: Body) -> dict[str, Any]:
        out = sections(body, structure(body.paragraphs, REGISTRY, MAPPING), REGISTRY)
        return {
            s["key"]: (s["narrative"], s["page"], s["refusal"] and s["refusal"]["code"])
            for s in out["sections"]
        }

    plain = read_body((FIXTURES / "jentadueto-smpc-en.docx").read_bytes())
    boxed = read_body(_rewritten("jentadueto-smpc-en", add))
    assert boxed.layout == plain.layout == ()
    (anchored,) = [p for p in boxed.paragraphs if p.anchored]
    assert (anchored.text, anchored.anchored) == ("", (Anchored(0, "text-box"),))
    before, after = built(plain), built(boxed)
    changed = {key for key in before if before[key] != after[key]}
    assert len(changed) == 1
    (key,) = changed
    assert (before[key][2], after[key][2]) == (None, "anchored-object")


def test_a_page_break_beside_a_space_or_alone_is_layout_only() -> None:
    for xml in (
        '<w:p><w:r><w:t xml:space="preserve">a </w:t><w:br w:type="page"/><w:t>b</w:t></w:r></w:p>',
        '<w:p><w:r><w:br w:type="page"/><w:t>Heading</w:t></w:r></w:p>',
        '<w:p><w:r><w:t>Last</w:t><w:br w:type="column"/></w:r></w:p>',
    ):
        assert not certified._break_between_words(
            ET.fromstring(xml.replace("<w:p>", f'<w:p xmlns:w="{W}">', 1))
        )
    joined = '<w:p xmlns:w="W"><w:r><w:t>10</w:t><w:br w:type="page"/><w:t>5 mg</w:t></w:r></w:p>'
    assert certified._break_between_words(ET.fromstring(joined.replace('"W"', f'"{W}"')))


def test_one_list_level_in_a_section() -> None:
    with pytest.raises(RefusedError) as refused:
        _build(
            _p("hepatic impairment", label="\u2022", num=1),
            _p("Child-Pugh C", label="\u2022", num=2, level=1),
            _p("renal impairment", label="\u2022", num=1),
        )
    assert refused.value.code == "list-level"
    with pytest.raises(RefusedError):
        _build(_p("a", label="\u2022"), _p("between"), _p("b", label="\u2022", level=1))


def test_a_label_run_into_its_text_is_refused() -> None:
    nothing = Paragraph("5 mg", None, Numbering(1, 0, "1.", "nothing"), None)
    with pytest.raises(RefusedError) as refused:
        _build(nothing)
    assert refused.value.code == "list-label"


def test_a_hyphen_may_be_underlined_only_inside_an_underlined_word() -> None:
    _build(_p("Long-term use", (0, 9, "underline")))
    for text, mark in (
        ("CL-CR", (2, 3)),
        ("CL-CR", (0, 3)),
        ("a\u2e40b", (0, 3)),
        ("a\u30a0b", (0, 3)),
    ):
        with pytest.raises(RefusedError) as refused:
            _build(_p(text, (*mark, "underline")))
        assert refused.value.code == "underline"


def test_capitals_are_left_out_only_where_they_draw_the_same() -> None:
    div, _ = _build(_p("4. ABC 10", (0, 9, "caps")))
    assert "4. ABC 10" in div
    for text in ("abc", "5 \u00b5g", "Stra\u00dfe"):
        with pytest.raises(RefusedError):
            _build(_p(text, (0, len(text), "smallCaps")))


def test_every_line_start_bullet_after_a_break_is_refused() -> None:
    for space in ("", " ", "\u00a0", "\u2003", "\u3000"):
        with pytest.raises(RefusedError) as refused:
            _build(_p(f"x\n{space}\u2022 y"))
        assert refused.value.code == "bullet-after-break"
    with pytest.raises(RefusedError) as refused:
        _build(_p("a\u2028b"))
    assert refused.value.code == "line-separator"


def test_a_section_of_blank_paragraphs_and_cells_has_no_narrative_and_the_empty_page() -> None:
    table = (_grid(2, [(0, 1, None), (1, 1, None)]),)
    div, text = _build(
        _p(" "), _p("", table=(0, 0, 0)), _p("\u00a0", table=(0, 0, 1)), tables=table
    )
    assert (div, text) == ("", "")


def test_a_heading_is_plain_text() -> None:
    body = Body((_p("4.1\tY", (0, 3, "caps")), _p("text"), _p("4.2 Z")), ())
    built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
    assert built["refused"] == 0
    for heading, code in (
        (_p("4.1 Y", (4, 5, "strike")), "heading-formatting"),
        (_p("4.1 Y", (4, 5, "superscript")), "heading-formatting"),
        (_p("4.1 y", (4, 5, "smallCaps")), "heading-formatting"),
        (_p("4.1 Y", mark_hidden=True), "hidden-mark"),
        (_p("4.1 Y", comments=(CommentReference(0, 1),)), "comment"),
    ):
        body = Body((heading, _p("text"), _p("4.2 Z")), ())
        built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
        assert built["sections"][0]["refusal"]["code"] == code


def test_the_page_reads_the_grid_apart_from_the_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """A merge the builder misreads is seen: the page does not take the builder's reading."""
    tables = (_grid(2, [(0, 1, "restart"), (1, 1, None)], [(0, 1, "continue"), (1, 1, None)]),)
    paragraphs = (
        _p("a", table=(0, 0, 0)),
        _p("b", table=(0, 0, 1)),
        _p("", table=(0, 1, 0)),
        _p("c", table=(0, 1, 1)),
    )
    _build(*paragraphs, tables=tables)
    real = word_epi._grid

    def misread(table: int, members: Any, body: Body) -> Any:
        columns, rows = real(table, members, body)
        return columns, [[dataclasses.replace(cell, rows=1) for cell in row] for row in rows]

    monkeypatch.setattr(word_epi, "_grid", misread)
    with pytest.raises(RefusedError) as refused:
        _build(*paragraphs, tables=tables)
    assert refused.value.code in ("narrative", "page-differs")


# ---- pictures -----------------------------------------------------------------------------------


def _png(width: int, height: int) -> bytes:
    """A real PNG of ``width`` x ``height`` grey pixels."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _pictured(
    image: bytes,
    extent: tuple[int, int],
    pixels: tuple[int, int] = (4, 2),
    reason: str | None = None,
) -> tuple[Paragraph, dict[str, bytes]]:
    sha = hashlib.sha256(image).hexdigest()
    picture = Picture(
        5, "picture", "word/media/image1.png", sha, "png", pixels, extent, None, reason
    )
    return Paragraph("Take \ufffc daily", None, None, None, pictures=(picture,)), {sha: image}


def test_a_picture_is_carried_as_its_exact_bytes() -> None:
    image = _png(4, 2)
    paragraph, images = _pictured(image, (4 * 9525, 2 * 9525))
    div, text = _build(paragraph, images=images)
    source = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    assert _inner(div) == f'<p>Take <img src="{source}"/> daily</p>'
    token = hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert text == f"\nTake \ufffc{token}\ufffc daily\n"
    # Smaller than its own size, and 1.9% out of proportion: still carried.
    _build(_pictured(image, (2 * 9525, 9525))[0], images=images)
    _build(_pictured(image, (38100, 18700))[0], images=images)


@pytest.mark.parametrize(
    ("extent", "reason", "detail"),
    [
        ((0, 0), None, "no size"),
        ((4 * 9525, 0), None, "no size"),
        ((5 * 9525, 2 * 9525), None, "larger"),
        ((4 * 9525, 3 * 9525), None, "larger"),
        ((38100, 18600), None, "proportion"),
        ((4 * 9525, 2 * 9525), "cropped", "cropped"),
        ((4 * 9525, 2 * 9525), "effects", "effects"),
    ],
)
def test_a_picture_drawn_otherwise_is_refused(
    extent: tuple[int, int], reason: str | None, detail: str
) -> None:
    paragraph, images = _pictured(_png(4, 2), extent, reason=reason)
    with pytest.raises(RefusedError) as refused:
        _build(paragraph, images=images)
    assert refused.value.code == "picture"
    assert detail in refused.value.detail


def test_a_picture_without_its_bytes_or_over_a_mebibyte_is_refused() -> None:
    paragraph, _ = _pictured(_png(4, 2), (4 * 9525, 2 * 9525))
    with pytest.raises(RefusedError):
        _build(paragraph, images={})
    big = _png(4, 2) + b"\x00" * (1 << 20)
    paragraph, images = _pictured(big, (4 * 9525, 2 * 9525))
    with pytest.raises(RefusedError) as refused:
        _build(paragraph, images=images)
    assert "1 MiB" in refused.value.detail
    with pytest.raises(RefusedError):
        _build(_p("Take \ufffc daily"))  # a U+FFFC the reader says nothing of


def test_a_heading_with_a_picture_is_refused() -> None:
    heading, images = _pictured(_png(4, 2), (4 * 9525, 2 * 9525))
    heading = dataclasses.replace(
        heading, text="4.1 \ufffcY", pictures=(dataclasses.replace(heading.pictures[0], offset=4),)
    )
    body = Body((heading, _p("text"), _p("4.2 Z")), (), images=images)
    built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
    assert built["sections"][0]["refusal"]["code"] == "picture"


def test_the_templates_black_triangle_is_carried() -> None:
    body = read_body(TEMPLATE.read_bytes())
    structured = structure(body.paragraphs, REGISTRY, MAPPING, {"smpc.6.5": 192, "smpc.6.6": 196})
    root = sections(body, structured, REGISTRY)["sections"][0]
    assert root["key"] == "smpc"
    assert root["refusal"] is None
    assert root["narrative"].count("<img ") == 1
    assert root["page"].count("\ufffc") == 2


def test_a_word_part_that_does_not_parse_refuses_the_document() -> None:
    out = io.BytesIO()
    with (
        zipfile.ZipFile(FIXTURES / "jentadueto-smpc-en.docx") as source,
        zipfile.ZipFile(out, "w") as target,
    ):
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr("word/unused.xml", b"<not closed")
        target.writestr("customXml/item9.xml", b"<also not closed")
    assert certified.layout(out.getvalue()) == ("unreadable-part",)


def test_chrome_decodes_each_picture_to_the_size_the_reader_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = word_fixtures.TEMPLATE.stem
    body = read_body(word_fixtures.TEMPLATE.read_bytes())
    built = sections(
        body, structure(body.paragraphs, REGISTRY, MAPPING, word_fixtures.ASSIGNED[name]), REGISTRY
    )
    _replay(monkeypatch, name, built)
    assert all(v["agrees"] for v in drawing.check(body, built)["sections"])
    # Drawn broken (not decoded), or decoded to another size: the section differs.
    recorded = RECORDED["documents"][name]
    for size in ([0, 0], [41, 48]):
        shown = [dict(s, pictures=[size] if s["pictures"] else []) for s in recorded["shown"]]
        monkeypatch.setattr(browser, "browser_sections", lambda _divs, _chrome, shown=shown: shown)
        verdicts = {v["key"]: v for v in drawing.check(body, built)["sections"]}
        assert not verdicts["smpc"]["agrees"]
        assert "picture" in verdicts["smpc"]["where"]


def test_a_row_of_exact_height_is_refused() -> None:
    table = _grid(1, [(0, 1, None)])
    table["grid"]["rows"][0]["exactHeight"] = True
    with pytest.raises(RefusedError) as refused:
        _build(_p("x", table=(0, 0, 0)), tables=(table,))
    assert refused.value.code == "row-height"


def _package(document: str, **parts: str) -> bytes:
    """A package of a body and other ``word/`` parts (name: root element and its content)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        target.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{W}"><w:body>{document}</w:body></w:document>',
        )
        for name, xml in parts.items():
            target.writestr(f"word/{name}.xml", xml)
    return out.getvalue()


def _part(root: str, content: str) -> str:
    return f'<w:{root} xmlns:w="{W}">{content}</w:{root}>'


def test_character_scaling_that_may_stretch_a_picture_refuses_the_document() -> None:
    picture = "<w:p><w:r><w:drawing/></w:r></w:p>"
    scaled = '<w:p><w:r><w:rPr><w:w w:val="150"/></w:rPr><w:t>x</w:t></w:r></w:p>'
    unscaled = '<w:p><w:r><w:rPr><w:w w:val="100"/></w:rPr><w:t>x</w:t></w:r></w:p>'
    stretched = '<w:p><w:r><w:rPr><w:w w:val="150"/></w:rPr><w:drawing/></w:r></w:p>'
    assert certified.layout(_package(stretched)) == ("character-scale",)
    # Scaling a run holding no picture, or a list label, stretches none.
    assert certified.layout(_package(picture + scaled)) == ()
    assert certified.layout(_package(picture + unscaled)) == ()
    assert certified.layout(_package(scaled)) == ()
    label = _part(
        "numbering",
        '<w:abstractNum><w:lvl><w:rPr><w:w w:val="90"/></w:rPr></w:lvl></w:abstractNum>',
    )
    assert certified.layout(_package(picture, numbering=label)) == ()
    # In a style, it may reach the picture's run.
    style = _part("styles", '<w:style w:styleId="S"><w:rPr><w:w w:val="90"/></w:rPr></w:style>')
    assert certified.layout(_package(picture, styles=style)) == ("character-scale",)


def test_a_frame_counts_where_it_can_reach_the_bodys_text() -> None:
    frame = '<w:framePr w:w="2000" w:hAnchor="page"/>'

    def styles(*definitions: str) -> str:
        return _part("styles", "".join(definitions))

    def paragraph_style(key: str, inner: str = "", default: bool = False) -> str:
        marked = ' w:default="1"' if default else ""
        return f'<w:style w:type="paragraph"{marked} w:styleId="{key}">{inner}</w:style>'

    framed = paragraph_style("Envelope", f"<w:pPr>{frame}</w:pPr>")
    based = paragraph_style("Child", '<w:basedOn w:val="Envelope"/>')
    plain = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
    styled = '<w:p><w:pPr><w:pStyle w:val="{}"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    # In a style no body paragraph uses (Word's built-in envelope address): nothing.
    assert certified.layout(_package(plain, styles=styles(framed))) == ()
    # Used, directly, through basedOn, or as the default paragraph style; on a paragraph, in the
    # defaults or in a table style: a frame.
    for document, definitions in (
        (styled.format("Envelope"), styles(framed)),
        (styled.format("Child"), styles(framed, based)),
        (plain, styles(paragraph_style("Normal", f"<w:pPr>{frame}</w:pPr>", default=True))),
        (f"<w:p><w:pPr>{frame}</w:pPr></w:p>", styles()),
        (
            plain,
            styles(
                f"<w:docDefaults><w:pPrDefault><w:pPr>{frame}</w:pPr></w:pPrDefault></w:docDefaults>"
            ),
        ),
        (plain, styles(f'<w:style w:type="table" w:styleId="T"><w:pPr>{frame}</w:pPr></w:style>')),
    ):
        assert certified.layout(_package(document, styles=definitions)) == ("frame",)
    # Word's page-number frame in a footer stays on its own line there; any other does not.
    number = (
        '<w:framePr w:wrap="around" w:vAnchor="text" w:hAnchor="margin" w:xAlign="center" w:y="1"/>'
    )
    footer = "<w:p><w:pPr>{}</w:pPr><w:r><w:t>1</w:t></w:r></w:p>"
    assert certified.layout(_package(plain, footer1=_part("ftr", footer.format(number)))) == ()
    for other in (
        number.replace('w:y="1"', 'w:y="-2000"'),
        number.replace('w:vAnchor="text"', 'w:vAnchor="page"'),
        number.replace("/>", ' w:h="3000"/>'),
        number.replace("/>", ' w:yAlign="top"/>'),
    ):
        assert certified.layout(_package(plain, footer1=_part("ftr", footer.format(other)))) == (
            "frame",
        )


def _scoreboard() -> Any:
    spec = importlib.util.spec_from_file_location(
        "scoreboard", Path(__file__).parents[1] / "scripts" / "scoreboard.py"
    )
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_the_scoreboard_measures_a_tracked_label_only_by_the_view_asked_for(tmp_path: Path) -> None:
    name = next(n for n in OUTCOMES if "brukinsa" not in n)
    inserted = '<w:p><w:ins w:id="9000" w:author="A" w:date="2026-01-01T00:00:00Z"><w:r>'
    inserted += "<w:t>x</w:t></w:r></w:ins></w:p>"
    folder = tmp_path / "labels"
    folder.mkdir()
    (folder / f"{name}.docx").write_bytes(
        _rewritten(
            name, lambda x: x[: x.rindex("<w:sectPr")] + inserted + x[x.rindex("<w:sectPr") :]
        )
    )
    script, out = _scoreboard(), tmp_path / "board.json"
    assert script.main([str(folder), "--no-drawing", "--out", str(out)]) == 0
    (entry,) = json.loads(out.read_text("utf-8"))["files"]
    assert (entry["outcome"], entry["code"]) == ("reader-refused", "tracked-change")
    for view in ("accepted", "original"):
        assert script.main([str(folder), "--no-drawing", "--view", view, "--out", str(out)]) == 0
        (entry,) = json.loads(out.read_text("utf-8"))["files"]
        assert (entry["outcome"], entry["view"]) == ("built", view)
    # The import builds it only from the view a person names, and says which (ADR 0006).
    spec = importlib.util.spec_from_file_location(
        "epi_from_word", Path(__file__).parents[1] / "scripts" / "epi_from_word.py"
    )
    assert spec is not None
    assert spec.loader is not None
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    data = (folder / f"{name}.docx").read_bytes()
    assert importer.build(data, {}, None)["refusal"]["code"] == "tracked-change"
    built = importer.build(data, {}, None, "accepted")
    assert built["tracked"] == {"view": "accepted", "changes": 1}
    assert built["epi"]["sections"]


def test_the_scoreboard_counts_without_saying_anything(tmp_path: Path) -> None:
    script = _scoreboard()
    out = tmp_path / "board.json"
    keys = REPOSITORY / "labels" / "ema-epi" / "sources"
    assert script.main([str(FIXTURES), "--keys", str(keys), "--no-drawing", "--out", str(out)]) == 0
    board = json.loads(out.read_text("utf-8"))
    totals = board["totals"]
    # Brukinsa words 6.5 and 6.6 otherwise than the template: it waits for a person.
    assert totals["outcomes"] == {"built": 4, "needs-a-person": 1}
    assert totals["carried"] == sum(OUTCOMES[n]["carried"] for n in OUTCOMES if "brukinsa" not in n)
    assert totals["key"]["same"] > 100
    # Nothing a label says: no fixture's longest paragraph, nor any part of it, is in the board.
    text = out.read_text("utf-8")
    for name in OUTCOMES:
        body = read_body((FIXTURES / f"{name}.docx").read_bytes())
        longest = max((p.text for p in body.paragraphs), key=len)
        assert longest[:40] not in text
