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
from label_docx.reader import (
    Anchored,
    CommentReference,
    DocxRefusedError,
    Mark,
    Numbering,
    Paragraph,
    Picture,
)

from zone_a import certified, drawing, word_epi
from zone_a.certified import Body, read_body
from zone_a.fidelity.normalize import normalize_text
from zone_a.fidelity.xhtml import XhtmlError, xhtml_to_text
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


def _grid(columns: int, *rows: list[tuple[int, int, str | None]]) -> dict[str, Any]:
    return {
        "grid": {
            "columns": columns,
            "rows": [
                {
                    "before": 0,
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


# What decisions 7 and 9 do not read as a typed label: a bare letter or number that is not raised,
# a decimal, four raised code points or a lead that mixes raised and level ones, a raised key that
# is or may join right-to-left text, a caption's word with no number or another number. Under
# decision 11 the lone tab after one is a space all the same, unless it is right-to-left text.
NOT_TYPED = (
    _p("a \u2022\tb"),
    _p("n\t= 50"),
    _p("1\tTake"),
    _p("2.5\tmg"),
    _p("Adults\t10 mg"),
    _p("e.g.\tx"),
    _p("\u2011\u2011\tx"),
    _p("abcd\tx", (0, 4, "superscript")),
    _p("ab\tx", (0, 1, "superscript")),
    _p("ab\tx", (1, 2, "superscript")),
    _p("a b\tx", (0, 1, "superscript"), (2, 3, "superscript")),
    _p("a\tx", (0, 1, "subscript")),
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
    _p("Fig. 1\tx"),
)
NOT_TYPED_RIGHT_TO_LEFT = (
    _p("\u0627\t\u0646\u0635", (0, 1, "superscript")),
    _p("\u200f\tx", (0, 1, "superscript")),
    _p("a\u200f\tx", (0, 2, "superscript")),
    _p("\u0661\tx", (0, 1, "superscript")),
    _p("Table \u0661\tx"),
)


# Numbers that are not Nd: a vulgar fraction, a circled digit, a Roman numeral, a script digit.
NUMERALS = ("\u00bd", "\u00bc", "\u2460", "\u216b", "\u00b3")


@pytest.mark.parametrize("numeral", NUMERALS)
def test_a_tab_between_a_number_and_a_numeral_is_refused(numeral: str) -> None:
    """As a space, "Day 1", a tab, "\u00bd tablet" would read "1\u00bd": a number of any kind
    on both sides keeps the tab, a lone tab or a typed label's, in a cell as outside."""
    for paragraph in (
        _p(f"Day 1\t{numeral} tablet"),
        _p(f"Dose {numeral}\t2 tablets"),
        _p(f"Dose {numeral}\t{numeral}"),
        _p(f"Table 1\t{numeral} of patients"),
        _p(f"1\t{numeral}", (0, 1, "superscript")),
    ):
        for tables, where in (((), None), (CELL, (0, 0, 0))):
            with pytest.raises(RefusedError) as refused:
                _build(dataclasses.replace(paragraph, table=where), tables=tables)
            assert refused.value.code == "tab", (paragraph.text, where)
    div, page = _build(_p(f"Dose {numeral}\tdaily"), _p(f"Day 1\tTablet {numeral}"))
    assert _inner(div) == f"<p>Dose {numeral} daily</p><p>Day 1 Tablet {numeral}</p>"
    assert _same(div, page)


@pytest.mark.parametrize(
    "text",
    [
        "1\t,5 mg",
        "1\t.5",
        "1\t\u22122",
        "5\t\u00b11",
        "1\t+2",
        "1\t-2",
        "1\t\u00b7 5",
        "1\t\u00b1\u20091",
        "Table 1\t,5",
        "Table 1\t\u066b5",
        "1\t\uff0c5",
        "1\t\uff0e5",
        "1\t\ufe505",
        "1\t\ufe525",
        "1\t+.5",
        "1\t\u2212,5",
    ],
)
def test_a_tab_before_a_sign_or_a_decimal_separator_and_a_digit_is_refused(text: str) -> None:
    """A decimal separator or a sign before a digit is read with it: "1", a tab, ",5 mg" as a
    space would read "1 ,5 mg"."""
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(_p(text, table=where), tables=tables)
        assert refused.value.code == "tab", (text, where)


@pytest.mark.parametrize(
    "text", ["1\t, then 2", "1\t- see 4.4", "5\t\u00b1 SD", "1\t.", "Table 1\t+ placebo", "a\t-2"]
)
def test_a_tab_before_a_sign_or_a_separator_and_no_digit_is_a_space(text: str) -> None:
    div, page = _build(_p(text))
    assert _inner(div) == "<p>" + word_epi._escape(text.replace("\t", " ")) + "</p>"
    assert _same(div, page)


DASH_LABELS = ("-", "\u2011", "\u2013", "\u2014")


@pytest.mark.parametrize("dash", DASH_LABELS)
def test_a_typed_dash_before_a_number_keeps_its_tab(dash: str) -> None:
    """As a space, "\u2013", a tab, "2 to 8 \u00b0C" would read as a minus: -2 \u00b0C, a storage
    temperature. A fix narrowing decisions 7 and 9, in a cell as outside."""
    for text in (
        f"{dash}\t2 to 8 \u00b0C",
        f"{dash}\t\u00bd tablet",
        f"{dash}\t,5 mg",
        f"{dash}\t\u22122",
        f"{dash} \t2 tablets",
        f"\t{dash}\t2 tablets",
    ):
        for tables, where in (((), None), (CELL, (0, 0, 0))):
            with pytest.raises(RefusedError) as refused:
                _build(_p(text, table=where), tables=tables)
            assert refused.value.code == "tab", (text, where)
    div, page = _build(_p(f"{dash}\tTablets"), _p(f"{dash}\tTake 2"), _p(f"{dash}\t, then"))
    assert _inner(div) == f"<p>{dash} Tablets</p><p>{dash} Take 2</p><p>{dash} , then</p>"
    assert _same(div, page)


@pytest.mark.parametrize("dash", DASH_LABELS)
def test_a_dash_list_label_before_a_number_is_refused(dash: str) -> None:
    """Decision 6 writes a dash label as text: before a number it would read as a minus."""
    for text in ("2 tablets", "\u00bd tablet", " \u22122 \u00b0C", ",5 mg", "\u2460"):
        for tables, where in (((), None), (CELL, (0, 0, 0))):
            with pytest.raises(RefusedError) as refused:
                _build(_p(text, label=dash, table=where), tables=tables)
            assert refused.value.code == "list-label", (text, where)
    div, page = _build(_p("Tablets", label=dash), _p("Take 2", label=dash))
    assert _inner(div) == f"<p>{dash} Tablets</p><p>{dash} Take 2</p>"
    assert _same(div, page)
    # A list HTML draws, of bullets or numbers, writes no label as text.
    div, page = _build(_p("2 tablets", label="\u2022"), _p("2 mg", label="1.", num=2))
    assert _inner(div) == "<ul><li>2 tablets</li></ul><ol><li>2 mg</li></ol>"


@pytest.mark.parametrize(
    "text",
    [
        "Storage:\n-\t2 to 8 \u00b0C",
        "x\n\u2013\t2",
        "- \t2",
        "\u2013 \u2013\t2",
        "Store at -\t2 to 8 \u00b0C",
        "Store at\u00a0\u2013\t2",
        # A Symbol font's minus, as the reader maps it, and the other dashes and minus signs.
        *(
            f"{dash}\t2 to 8 \u00b0C"
            for dash in "\u2212\u2010\u2012\u2015\ufe63\uff0d\ufe58\u207b\u2796"
        ),
        "-\t+.5",
        # Read across a line break, which a cell writes as a space.
        "5\n\t2",
    ],
)
def test_a_tab_after_any_dash_or_minus_before_a_number_is_refused(text: str) -> None:
    """One test for every tab written as a space: the last code point before it, past every gap
    (a line break included), a dash (Pd) or a minus, and a number after it keep the tab: as a
    space, "- 2 to 8 \u00b0C" reads as -2 \u00b0C."""
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(_p(text, table=where), tables=tables)
        assert refused.value.code == "tab", (text, where)


@pytest.mark.parametrize(
    "text",
    [
        "Storage:\n-\tsee 6.4",
        "- \tsee",
        "\u2212\tsee",
        "\u2010\tTablets",
        "Store at -\tsee below",
        # A bracket or a comparison sign before the number: no sign's.
        "Store at -\t(2 to 8 \u00b0C)",
        "-\t\u22652",
        "-\t<2",
        "-\t~2",
    ],
)
def test_a_tab_after_a_dash_before_no_number_is_a_space(text: str) -> None:
    div, page = _build(_p(text))
    drawn = word_epi._escape(text.replace("\t", " ")).replace("\n", "<br/>")
    assert _inner(div) == f"<p>{drawn}</p>"
    assert _same(div, page)


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("\u2212", "2 to 8 \u00b0C"),
        ("- ", "2 tablets"),
        ("\u2010", "2 tablets"),
        ("\u2015", " ,5 mg"),
        # Two numbers joined through the label (section 7, decision 6).
        ("1", "000 mg"),
        ("1.1", "5 mg"),
        ("12", "\u00bd tablet"),
        ("\u2160", "2"),
    ],
)
def test_a_label_written_as_text_that_joins_the_number_after_it_is_refused(
    label: str, text: str
) -> None:
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(_p(text, label=label, table=where), tables=tables)
        assert refused.value.code == "list-label", (label, text, where)


@pytest.mark.parametrize(
    "text",
    [
        # A dash or minus after the tab is the number's sign: an en dash, as the EMA writes a
        # minus ("5", a tab, "\u201320 \u00b0C" would read as the range 5\u201320 \u00b0C).
        "5\t\u201320 \u00b0C",
        "1\t\u20132",
        "5\t\uff0d2",
        "5\t\u2796 2",
        "Day 1\t\u20142",
        "1\t\u2010.5",
        # A hyphen bullet or a modifier minus before the tab.
        "\u2043\t2 to 8",
        "\u02d7\t2",
        # A combining mark is read to its base.
        "-\u0301\t2 to 8",
        "5\u0301\t2",
        "-\u0332\t2",
    ],
)
def test_a_dash_or_a_combining_mark_beside_a_number_keeps_the_tab(text: str) -> None:
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(_p(text, table=where), tables=tables)
        assert refused.value.code == "tab", (text, where)


@pytest.mark.parametrize(("label", "text"), [("1", "\u20132"), ("\u02d7", "2"), ("\u2043", "2 mg")])
def test_a_label_before_a_dash_number_or_a_minus_label_before_a_number_is_refused(
    label: str, text: str
) -> None:
    with pytest.raises(RefusedError) as refused:
        _build(_p(text, label=label))
    assert refused.value.code == "list-label"


def test_a_dash_after_the_tab_before_no_number_is_a_space() -> None:
    div, page = _build(
        _p("-\tTablets"), _p("\u2013\tTake 2"), _p("5\t\u2013 see 4.4"), _p("a\t\u20132")
    )
    assert _inner(div) == (
        "<p>- Tablets</p><p>\u2013 Take 2</p><p>5 \u2013 see 4.4</p><p>a \u20132</p>"
    )
    assert _same(div, page)


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("\u2212", "Tablets"),
        ("- ", "see 4.4"),
        ("a)", "5 mg"),
        ("(1)", "2 mg"),
        ("3.", "5 mg"),
        ("1", "mg"),
    ],
)
def test_a_label_written_as_text_before_no_number_or_after_none_is_carried(
    label: str, text: str
) -> None:
    div, page = _build(_p(text, label=label))
    assert _inner(div) == f"<p>{word_epi._escape(label)} {text}</p>"
    assert _same(div, page)


@pytest.mark.parametrize(
    ("marks", "drawn"),
    [
        # The tab inside a raised or lowered run: refused (None).
        (((2, 5, "superscript"),), None),
        (((3, 4, "superscript"),), None),
        (((3, 6, "subscript"),), None),
        (((0, 7, "subscript"),), None),
        # A run that ends at the tab, or starts after it: the tab is level, a space.
        (((2, 3, "superscript"),), "ab<sup>c</sup> de"),
        (((4, 6, "subscript"),), "abc <sub>de</sub>"),
        (((0, 3, "superscript"), (4, 6, "superscript")), "<sup>abc</sup> <sup>de</sup>"),
    ],
)
def test_a_raised_or_lowered_tab_is_refused_and_a_level_one_beside_a_run_is_a_space(
    marks: tuple[tuple[int, int, str], ...], drawn: str | None
) -> None:
    """Decision 11: a lone tab inside a superscript or subscript mark stays refused, unless a
    raised key carries it (decision 9: ``test_a_typed_label_keeps_precedence_over_a_lone_tab``)."""
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        paragraph = _p("abc\tde", *marks, table=where)
        if drawn is None:
            with pytest.raises(RefusedError) as refused:
                _build(paragraph, tables=tables)
            assert refused.value.code == "tab", (marks, where)
            continue
        div, page = _build(paragraph, tables=tables)
        assert drawn in div
        assert _same(div, page)


# A raised combining mark: refused as script, whatever its tab is.
@pytest.mark.parametrize(
    "paragraph", [*NOT_TYPED, *NOT_TYPED_RIGHT_TO_LEFT, _p("a\u0301\tx", (0, 2, "superscript"))]
)
def test_what_decisions_7_and_9_read_as_no_typed_label(paragraph: Paragraph) -> None:
    assert word_epi.typed_tab(paragraph) is None
    assert word_epi.typed_tab(dataclasses.replace(paragraph, table=(0, 0, 0))) is None


@pytest.mark.parametrize("paragraph", NOT_TYPED)
def test_a_lone_tab_is_a_space(paragraph: Paragraph) -> None:
    """Owner decision 11 (2026-10-08): the one tab of a paragraph's text, past its indent, is a
    space in a table cell as outside one, where no digit stands on both sides of it and it is not
    raised or lowered; the text, its order and its code points are Word's."""
    text = paragraph.text.replace("\t", " ")
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        div, page = _build(dataclasses.replace(paragraph, table=where), tables=tables)
        assert "\t" not in div
        assert page == (
            f"\n{text}\n" if where is None else f"\n\ufdd0\n\ufdd2\t\ufdd3\t{text}\n\ufdd1\n"
        )
        assert _same(div, page)


@pytest.mark.parametrize(
    ("text", "drawn"),
    [
        ("Step 1\tTake one", "Step 1 Take one"),
        ("10 mg\t 20 mg", "10 mg  20 mg"),
        ("x\ny\tz", "x<br/>y z"),
        ("a <\t5", "a &lt; 5"),
    ],
)
def test_a_lone_tab_mid_text_is_a_space(text: str, drawn: str) -> None:
    div, page = _build(_p(text))
    assert _inner(div) == f"<p>{drawn}</p>"
    assert page == "\n" + text.replace("\t", " ") + "\n"
    assert _same(div, page)


def test_a_typed_label_keeps_precedence_over_a_lone_tab() -> None:
    """Decision 9's raised key carries its tab where decision 11 would not: the tab raised with
    its key."""
    div, page = _build(_p("a\tx", (0, 2, "superscript")), _p("a\t2 mg", (0, 1, "superscript")))
    assert _inner(div) == "<p><sup>a </sup>x</p><p><sup>a</sup> 2 mg</p>"
    assert page == "\na x\na 2 mg\n"
    assert _same(div, page)


@pytest.mark.parametrize(
    "paragraph",
    [
        _p("Table 1\t2-year results"),
        _p("Table 12 \t2-year results"),
        _p("Figure 3\t\u00a010 mg"),
        _p("1\t2 mg", (0, 1, "superscript")),
        _p("1\t2 mg", (0, 2, "superscript")),
        _p("a1\t2 mg", (0, 2, "superscript")),
        _p("\u00b9\t2 mg", (0, 1, "superscript")),
        _p("Table 2\t\u00b2"),
        _p("Table 1\t\u2082"),
        _p("\tTable 1\t2-year results"),
    ],
)
def test_a_typed_labels_tab_between_two_digits_is_refused(paragraph: Paragraph) -> None:
    """From 3.5.0, a fix to decision 9's rule: as a space, the tab after a label that ends in a
    digit (a raised one, or a script digit, included) would join it to a digit after it into one
    number, as section 6 reads "1 000"; the tab stays, refused, in a cell as outside."""
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(dataclasses.replace(paragraph, table=where), tables=tables)
        assert refused.value.code == "tab", (paragraph.text, where)


@pytest.mark.parametrize(
    ("paragraph", "drawn", "paged"),
    [
        (_p("Table 1:\t2-year results"), "Table 1: 2-year results", "Table 1: 2-year results"),
        (_p("Table 1.\t2-year results"), "Table 1. 2-year results", "Table 1. 2-year results"),
        (_p("Table 1a\t2-year results"), "Table 1a 2-year results", "Table 1a 2-year results"),
        (_p("1.\t2 mg"), "1. 2 mg", "1. 2 mg"),
        (_p("a\t2 mg", (0, 1, "superscript")), "<sup>a</sup> 2 mg", "a 2 mg"),
        (_p("Table 1\tAge"), "Table 1 Age", "Table 1 Age"),
    ],
)
def test_a_typed_labels_tab_with_no_digit_on_one_side_is_a_space(
    paragraph: Paragraph, drawn: str, paged: str
) -> None:
    div, page = _build(paragraph)
    assert _inner(div) == f"<p>{drawn}</p>"
    assert page == f"\n{paged}\n"
    assert _same(div, page)


@pytest.mark.parametrize(
    ("text", "drawn"),
    [
        ("\tTake once daily.", " Take once daily."),
        ("\t\t- led", "  - led"),
        ("\t\u2022\tled", " \u2022 led"),
        ("\u00a0\t\u25cf\tled", "\u00a0 \u25cf led"),
        ("\tTable 1:\tDose", " Table 1: Dose"),
        ("\tAdults\t10 mg", " Adults 10 mg"),
    ],
)
def test_the_tabs_of_an_indent_are_spaces(text: str, drawn: str) -> None:
    """Owner decision 12 (2026-10-08): the tabs before a paragraph's text starts are its indent,
    each a space; the tabs after it are judged as if it were absent."""
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        div, page = _build(_p(text, table=where), tables=tables)
        if where is None:
            assert _inner(div) == f"<p>{drawn}</p>"
            assert page == f"\n{drawn}\n"
        else:
            assert _inner(div) == f"<table><tr><td><p>{drawn}</p></td></tr></table>"
            assert page == f"\n\ufdd0\n\ufdd2\t\ufdd3\t{drawn}\n\ufdd1\n"
        assert _same(div, page)


def test_every_other_tab_is_refused() -> None:
    # A tab is still Word's jump to a tab stop: two or more past the indent (columns), one
    # between two digits (read past every gap: "1 000" would read as one number), one raised or
    # lowered that no raised key carries, one in right-to-left text (a space joins its bidi
    # segments, a tab does not), and any tab after a list label, in a table cell as outside one.
    raised = "superscript"
    for paragraph in (
        _p("\u2022\t\tdouble"),
        _p("\u2022\ta\tb"),
        _p("-\ta\tb"),
        _p("a\tb\tc"),
        _p("\ta\tb\tc"),
        _p("\t\u2022\ta\tb"),
        _p("a\tx\ty", (0, 1, raised)),
        _p("Table 1:\tx\ty"),
        _p("1\t000"),
        _p("10\t 000"),
        _p("10 \t\u00a0000"),
        _p("1\t\u2009000"),
        _p("1\u2007\t2"),
        _p("1\t\n2"),
        _p("\t1\t000"),
        _p("Dose 10\t20"),
        _p("10\t3", (3, 4, raised)),
        _p("ab x\ty", (3, 6, raised)),
        _p("10\t2", (2, 3, raised)),
        _p("x\ty", (1, 2, "subscript")),
        _p("\u05d0\t\u05d1"),
        _p("\u0627\t\u0628"),
        _p("\u05d0\t1"),
        _p("a\t\u05d0"),
        _p("x\t\u0661"),
        *NOT_TYPED_RIGHT_TO_LEFT,
        _p("\u2022\tx", label="1."),
        _p("a\tx", (0, 1, raised), label="1."),
        _p("Table 1:\tx", label="1."),
        _p("a\tb", label="1."),
        _p("\tx", label="1."),
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


def _rows_grid(columns: int, *rows: tuple[int, list[tuple[int, int, str | None]], int]) -> Any:
    """A table whose rows each leave ``before`` and ``after`` grid columns out."""
    table = _grid(columns, *(cells for _, cells, _ in rows))
    for row, (before, _, after) in zip(table["grid"]["rows"], rows, strict=True):
        row["before"], row["after"] = before, after
    return table


def _in_cells(*texts: list[str]) -> list[Paragraph]:
    """One paragraph per cell, row by row: ``texts[r][c]`` in row r's cell c."""
    return [_p(text, table=(0, r, c)) for r, row in enumerate(texts) for c, text in enumerate(row)]


def test_a_grid_column_at_which_no_cell_starts_is_dropped() -> None:
    """Section 7 (fidelity-norm/3.5.0): a column no cell starts at is drawn at no width, which
    section 5 refuses (``table-shape``); without it the table is the same, each value in its cell.
    """
    table = _grid(3, [(0, 2, None), (2, 1, None)], [(0, 3, None)], [(0, 2, None), (2, 1, None)])
    div, page = _build(*_in_cells(["a", "b"], ["c"], ["d", "e"]), tables=(table,))
    assert _inner(div) == (
        "<table><tr><td><p>a</p></td><td><p>b</p></td></tr>"
        '<tr><td colspan="2"><p>c</p></td></tr>'
        "<tr><td><p>d</p></td><td><p>e</p></td></tr></table>"
    )
    assert page == (
        "\n\ufdd0\n\ufdd2\t\ufdd3\ta\t\ufdd3\tb\n\ufdd2\t\ufdd3\tc\t\ufdd4\t\n\ufdd2\t\ufdd3\td\t\ufdd3\te\n\ufdd1\n"
    )
    assert _same(div, page)
    # The table as Word's grid has it is what section 5 refuses.
    with pytest.raises(XhtmlError) as refused:
        xhtml_to_text(
            ROOT + '<table><tr><td colspan="2">a</td><td>b</td></tr><tr><td colspan="3">c</td></tr>'
            '<tr><td colspan="2">d</td><td>e</td></tr></table></div>'
        )
    assert refused.value.code == "table-shape"


def test_columns_dropped_keep_a_merge_and_every_other_boundary() -> None:
    # Columns 1 and 2 start no cell: a merged cell spans one column; column 3 stays.
    table = _grid(
        4, [(0, 3, "restart"), (3, 1, None)], [(0, 3, "continue"), (3, 1, None)], [(0, 4, None)]
    )
    div, page = _build(*_in_cells(["a", "b"], ["", "c"], ["d"]), tables=(table,))
    assert _inner(div) == (
        '<table><tr><td rowspan="2"><p>a</p></td><td><p>b</p></td></tr>'
        '<tr><td><p>c</p></td></tr><tr><td colspan="2"><p>d</p></td></tr></table>'
    )
    assert page == (
        "\n\ufdd0\n\ufdd2\t\ufdd3\ta\t\ufdd3\tb\n\ufdd2\t\ufdd5\t\t\ufdd3\tc\n\ufdd2\t\ufdd3\td\t\ufdd4\t\n\ufdd1\n"
    )
    assert _same(div, page)


def test_a_grid_whose_every_column_starts_a_cell_is_unchanged() -> None:
    table = _grid(3, [(0, 2, None), (2, 1, None)], [(0, 1, None), (1, 2, None)])
    paragraphs = _in_cells(["a", "b"], ["c", "d"])
    assert word_epi._rows(table["grid"]) == [
        [(0, 2, None, 0), (2, 1, None, 1)],
        [(0, 1, None, 0), (1, 2, None, 1)],
    ]
    # Column 1 starts a cell but none of one column: section 5 refuses it still.
    with pytest.raises(RefusedError) as refused:
        _build(*paragraphs, tables=(table,))
    assert (refused.value.code, refused.value.detail) == ("narrative", "table-shape")


@pytest.mark.parametrize(
    ("before", "after", "drawn", "slots"),
    [
        (1, 0, "<td></td><td><p>x</p></td>", "\t\ufdd3\t\t\ufdd3\tx"),
        (0, 1, "<td><p>x</p></td><td></td>", "\t\ufdd3\tx\t\ufdd3\t"),
        (1, 1, "<td></td><td><p>x</p></td><td></td>", "\t\ufdd3\t\t\ufdd3\tx\t\ufdd3\t"),
        (2, 0, '<td colspan="2"></td><td><p>x</p></td>', "\t\ufdd3\t\t\ufdd4\t\t\ufdd3\tx"),
    ],
)
def test_grid_columns_a_row_leaves_out_are_an_empty_cell(
    before: int, after: int, drawn: str, slots: str
) -> None:
    """Owner decision 10 (2026-10-08): Word draws no cell there and no text; the ePI holds the
    same text in the same columns, with an empty cell over them."""
    columns = 1 + before + after
    table = _rows_grid(
        columns,
        (before, [(before, 1, None)], after),
        (0, [(c, 1, None) for c in range(columns)], 0),
    )
    paragraphs = [_p("x", table=(0, 0, 0)), *(_p(f"{c}", table=(0, 1, c)) for c in range(columns))]
    div, page = _build(*paragraphs, tables=(table,))
    second = "".join(f"<td><p>{c}</p></td>" for c in range(columns))
    assert _inner(div) == f"<table><tr>{drawn}</tr><tr>{second}</tr></table>"
    row = "".join(f"\t\ufdd3\t{c}" for c in range(columns))
    assert page == f"\n\ufdd0\n\ufdd2{slots}\n\ufdd2{row}\n\ufdd1\n"
    assert _same(div, page)


def test_an_empty_cell_and_a_dropped_column_together() -> None:
    # Row 0 leaves out two columns at its start; no cell starts at column 1, so the empty cell
    # spans one column of the grid as laid.
    table = _rows_grid(3, (2, [(2, 1, None)], 0), (0, [(0, 2, None), (2, 1, None)], 0))
    div, page = _build(*_in_cells(["x"], ["a", "b"]), tables=(table,))
    assert _inner(div) == (
        "<table><tr><td></td><td><p>x</p></td></tr>"
        "<tr><td><p>a</p></td><td><p>b</p></td></tr></table>"
    )
    assert page == "\n\ufdd0\n\ufdd2\t\ufdd3\t\t\ufdd3\tx\n\ufdd2\t\ufdd3\ta\t\ufdd3\tb\n\ufdd1\n"
    assert _same(div, page)


def test_an_empty_cell_beside_a_merge() -> None:
    # Two rows leave column 0 out beside a cell merged down over both, then a full row.
    table = _rows_grid(
        2,
        (1, [(1, 1, "restart")], 0),
        (1, [(1, 1, "continue")], 0),
        (0, [(0, 1, None), (1, 1, None)], 0),
    )
    div, page = _build(*_in_cells(["x"], [""], ["a", "b"]), tables=(table,))
    assert _inner(div) == (
        '<table><tr><td></td><td rowspan="2"><p>x</p></td></tr><tr><td></td></tr>'
        "<tr><td><p>a</p></td><td><p>b</p></td></tr></table>"
    )
    assert page == (
        "\n\ufdd0\n\ufdd2\t\ufdd3\t\t\ufdd3\tx\n\ufdd2\t\ufdd3\t\t\ufdd5\t\n"
        "\ufdd2\t\ufdd3\ta\t\ufdd3\tb\n\ufdd1\n"
    )
    assert _same(div, page)


@pytest.mark.parametrize(
    "rows",
    [
        # A merge that would run through an empty cell: under no cell of its columns.
        (
            (0, [(0, 1, "restart"), (1, 1, None)], 0),
            (1, [(1, 1, None)], 0),
            (0, [(0, 1, "continue"), (1, 1, None)], 0),
        ),
        (
            (0, [(0, 1, None), (1, 1, "restart")], 0),
            (0, [(0, 1, None)], 1),
            (0, [(0, 1, None), (1, 1, "continue")], 0),
        ),
        # A merge under an empty cell.
        ((1, [(1, 1, None)], 0), (0, [(0, 1, "continue"), (1, 1, None)], 0)),
        ((0, [(0, 1, None)], 1), (0, [(0, 1, None), (1, 1, "continue")], 0)),
        # A row of no cells of its own.
        ((0, [(0, 1, None), (1, 1, None)], 0), (2, [], 0)),
        ((0, [(0, 1, None), (1, 1, None)], 0), (1, [], 1)),
    ],
)
def test_an_empty_cell_that_would_meet_a_merge_is_refused(rows: Any) -> None:
    table = _rows_grid(2, *rows)
    paragraphs = [
        _p("x" if merge != "continue" else "", table=(0, r, c))
        for r, (_, cells, _) in enumerate(rows)
        for c, (_, _, merge) in enumerate(cells)
    ]
    with pytest.raises(RefusedError) as refused:
        _build(*paragraphs, tables=(table,))
    assert refused.value.code == "table-shape"


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome is not installed")
def test_chrome_draws_word_epi_1_5_0s_tables_tabs_and_unpainted_marks_as_word_does() -> None:
    """The drawing check reads each as Chrome draws it: an empty cell and a dropped column draw
    no line, a tab and a space collapse alike, and a mark over trailing spaces marks no character
    that is not whitespace; held to the same drawing, a read that differs differs."""
    # Column 3 starts no cell; rows 0 and 2 leave columns out.
    table = _rows_grid(
        4,
        (1, [(1, 1, None), (2, 2, None)], 0),
        (0, [(0, 2, None), (2, 2, None)], 0),
        (0, [(0, 1, None)], 3),
    )
    paragraphs = (
        _p("\tIndented", table=(0, 0, 0)),
        _p("Dose", table=(0, 0, 1)),
        _p("Adults\t10 mg", table=(0, 1, 0)),
        _p("x   ", (1, 4, "highlight-yellow"), table=(0, 1, 1)),
        _p("\t\u2022\tOnce", table=(0, 2, 0)),
        _p("n\t= 50"),
        _p("Alpha beta   ", (10, 13, "strike")),
        _p("   ", (0, 3, "shading-000000")),
    )
    body = Body(paragraphs, (table,))
    div, _ = _section(range(len(paragraphs)), body)
    section = {"key": "s", "refusal": None, "narrative": div, "paragraphs": [0, len(paragraphs)]}
    assert drawing.check(body, {"sections": [section]})["sections"] == [
        {"key": "s", "agrees": True, "where": None}
    ]
    for at, change in ((2, {"text": "Adults\t10 mgx"}), (5, {"marks": (Mark(0, 1, "bold"),)})):
        moved = _tampered(body, at, **change)
        assert not drawing.check(moved, {"sections": [section]})["sections"][0]["agrees"]


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
        # A theme's pattern colour or fill, which the reader spells from docx-reader/1.34.0: Word
        # draws an accent at 15% in its own colour.
        *("shading-pct15-THEME-accent2-AUTO", "shading-pct15-THEME-text1-AUTO"),
        *("shading-pct15-THEME-text1-FFFFFF", "shading-pct15-AUTO-THEME-background1"),
        *("shading-pct15-THEME-text1-tint99-AUTO", "shading-pct15-AUTO-THEME-background1-shadeD9"),
        # Nothing on record says how Word draws these: another pattern, colour or fill.
        *("shading-pct10-AUTO-AUTO", "shading-pct20-AUTO-AUTO", "shading-pct15-AUTO-D9D9D9"),
        *("shading-pct12-AUTO-AUTO", "shading-pct25-AUTO-FFFFFF", "shading-solid-AUTO-AUTO"),
        *("shading-pct15-AUTO-E6E6E6", "shading-pct15-AUTO-FFFFFE", "shading-pct15-FFFFFF-AUTO"),
        *("shading-pct15-000000-AUTO", "shading-pct15-AUTO-C0C0C0", "shading-C0C0C1"),
        *("shading-pct15-000000-FFFFFF", "shading-horzStripe-AUTO-AUTO", "shading-AUTO"),
        *("shading-THEME-background1", "shading-clear-AUTO-C0C0C0", "shading-FFFFFF"),
        # Not the reader's spelling: lower case, or a part more or less.
        *("shading-pct15-auto-auto", "shading-pct15-AUTO", "shading-pct15-AUTO-AUTO-AUTO"),
        *("shading-pct15-AUTO-ffffff", "shading-Pct15-AUTO-AUTO", "shading-pct15-AUTO-AUTO "),
    ],
)
def test_another_grey_or_colour_is_refused(kind: str) -> None:
    with pytest.raises(RefusedError) as refused:
        _build(_p("x", (0, 1, kind)))
    assert refused.value.code == "formatting"


def test_the_greys_are_the_templates_two_and_the_shadings_word_draws_as_one_of_them() -> None:
    """Decision 5's two marks; C0C0C0 shading (decision 9, by Word's own print); and from
    fidelity-norm/3.7.0 the 15% pattern of the automatic colour on an automatic or white fill,
    which Word printed exactly as D9D9D9 on a white page, over cells shaded FFFF00 and 000000, over
    a paragraph shaded D9D9D9 and on a page coloured FFFF00 (2026-10-07 and 2026-10-09)."""
    assert (
        frozenset(
            {
                *("highlight-lightGray", "shading-D9D9D9", "shading-C0C0C0"),
                *("shading-pct15-AUTO-AUTO", "shading-pct15-AUTO-FFFFFF"),
            }
        )
        == word_epi.GREY
    )


# A theme's colour scheme (Office's), for a theme colour the reader resolves and names.
THEME = (
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="t">'
    '<a:themeElements><a:clrScheme name="Office">'
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
    '<a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
    "</a:clrScheme></a:themeElements></a:theme>"
)


def _shaded_docx(shading: str, theme: bool = False) -> bytes:
    """A minimal .docx of one paragraph whose second word's run carries ``shading`` (run
    properties), with ``THEME`` as its theme where ``theme``."""
    run = '<w:r><w:t xml:space="preserve">{}</w:t></w:r>'
    body = (
        "<w:p>"
        + run.format("Report ")
        + f'<w:r><w:rPr>{shading}</w:rPr><w:t xml:space="preserve">it here</w:t></w:r>'
        + "</w:p>"
    )
    return _docx(body, theme)


def _docx(body: str, theme: bool = False) -> bytes:
    """A minimal .docx of ``body``, with ``THEME`` as its theme where ``theme``."""
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
        package.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="{rels}">'
            + (
                '<Relationship Id="t1" Type="http://schemas.openxmlformats.org/officeDocument/'
                '2006/relationships/theme" Target="theme/theme1.xml"/>'
                if theme
                else ""
            )
            + "</Relationships>",
        )
        if theme:
            package.writestr("word/theme/theme1.xml", THEME)
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


@pytest.mark.parametrize(
    ("shading", "kind"),
    [
        ('<w:shd w:val="pct15" w:color="auto" w:fill="auto"/>', "shading-pct15-AUTO-AUTO"),
        ('<w:shd w:val="pct15" w:fill="auto"/>', "shading-pct15-AUTO-AUTO"),
        ('<w:shd w:val="pct15" w:color="auto"/>', "shading-pct15-AUTO-AUTO"),
        ('<w:shd w:val="pct15" w:color="auto" w:fill="FFFFFF"/>', "shading-pct15-AUTO-FFFFFF"),
        ('<w:shd w:val="pct15" w:color="auto" w:fill="ffffff"/>', "shading-pct15-AUTO-FFFFFF"),
    ],
)
def test_a_15_percent_pattern_of_the_automatic_colour_read_by_the_reader_is_the_grey(
    shading: str, kind: str
) -> None:
    """fidelity-norm/3.7.0, through the label reader's certified read: Word prints 15% of the
    automatic colour on an automatic or white fill as exactly D9D9D9, ``shading-D9D9D9``'s colour
    (the recorded grounds: ``test_the_15_percent_patterns_word_printed_as_d9d9d9_are_the_grey``)."""
    body = read_body(_shaded_docx(shading))
    assert [(m.start, m.end, m.kind) for m in body.paragraphs[0].marks] == [(7, 14, kind)]
    div, text = _section(range(len(body.paragraphs)), body)
    assert _inner(div or "") == f"<p>Report {GREY_SPAN}it here</span></p>"
    assert text == "\nReport it here\n"


@pytest.mark.parametrize(
    ("shading", "kind"),
    [
        # The review's blocker of 3.4.0: a theme's pattern colour, which Word draws in that colour
        # (accent2 at 15% as #FCEBE0), read as auto before docx-reader/1.34.0. Now spelt, refused.
        (
            '<w:shd w:val="pct15" w:color="auto" w:themeColor="accent2" w:fill="auto"/>',
            "shading-pct15-THEME-accent2-AUTO",
        ),
        (
            '<w:shd w:val="pct15" w:themeColor="text1" w:fill="FFFFFF"/>',
            "shading-pct15-THEME-text1-FFFFFF",
        ),
        (
            '<w:shd w:val="pct15" w:color="auto" w:themeColor="text1" w:themeShade="80"'
            ' w:fill="auto"/>',
            "shading-pct15-THEME-text1-shade80-AUTO",
        ),
        (
            '<w:shd w:val="pct15" w:color="auto" w:fill="FFFFFF" w:themeFill="background1"/>',
            "shading-pct15-AUTO-THEME-background1",
        ),
        ('<w:shd w:val="pct15" w:color="000000" w:fill="auto"/>', "shading-pct15-000000-AUTO"),
        ('<w:shd w:val="pct15" w:color="auto" w:fill="D9D9D9"/>', "shading-pct15-AUTO-D9D9D9"),
        ('<w:shd w:val="pct10" w:color="auto" w:fill="auto"/>', "shading-pct10-AUTO-AUTO"),
        ('<w:shd w:val="pct20" w:color="auto" w:fill="auto"/>', "shading-pct20-AUTO-AUTO"),
    ],
)
def test_a_pattern_in_a_theme_or_another_colour_read_by_the_reader_is_refused(
    shading: str, kind: str
) -> None:
    """With a theme, the reader names a theme's pattern colour or fill (never AUTO), and the
    builder refuses it; with none, the reader refuses a theme colour it cannot resolve."""
    if "accent" in shading:  # text1 and background1 resolve to the system's colours
        with pytest.raises(DocxRefusedError) as unread:
            read_body(_shaded_docx(shading))
        assert unread.value.code == "unsupported-formatting"
    body = read_body(_shaded_docx(shading, theme=True))
    assert [(m.start, m.end, m.kind) for m in body.paragraphs[0].marks] == [(7, 14, kind)]
    with pytest.raises(RefusedError) as refused:
        _section(range(len(body.paragraphs)), body)
    assert (refused.value.code, refused.value.detail) == ("formatting", kind)


@pytest.mark.parametrize(
    "kind",
    [
        # A shift of a point or less at the paragraph's size: the reader names the paragraph's
        # size from its style, not its neighbours' (a run at the style's 10 pt raised a point
        # among runs set to 14 pt is drawn smaller and raised, as "m" then a raised "2"), and
        # shifts add up across runs; not carried (withdrawn from fidelity-norm/3.7.0).
        *("position-1-size22-in22", "position+1-size22-in22", "position-2-size22-in22"),
        *("position+2-size22-in22", "position+2-size20-in20", "position-1-size20-in20"),
        # Moved further, or smaller or larger than the paragraph's text.
        *("position-3-size22-in22", "position+6-size22-in22", "position+8-size14-in22"),
        *("position-1-size20-in22", "position+2-size24-in22", "position-4-size14-in22"),
    ],
)
def test_every_raised_or_lowered_position_is_refused(kind: str) -> None:
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(_p("Take 2 tablets", (5, 6, kind), table=where), tables=tables)
        assert (refused.value.code, refused.value.detail) == ("formatting", kind), where
    body = Body((_p("4.1 Y", (4, 5, kind)), _p("text"), _p("4.2 Z")), ())
    built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
    assert built["sections"][0]["refusal"]["code"] == "heading-formatting"


def test_a_raised_run_at_the_styles_size_among_larger_runs_is_refused() -> None:
    """The independent review's case: "Area 5 m" set to 14 pt, "2" raised a point at the
    style's 10 pt, " daily" at 14 pt. The reader names it ``position+2-size20-in20``; Word draws
    a smaller raised 2 (m\u00b2). Refused, not carried as "m2"."""
    run = '<w:r><w:rPr>{}</w:rPr><w:t xml:space="preserve">{}</w:t></w:r>'
    body = (
        "<w:p>"
        + run.format('<w:sz w:val="28"/>', "Area 5 m")
        + run.format('<w:position w:val="2"/>', "2")
        + run.format('<w:sz w:val="28"/>', " daily")
        + "</w:p>"
    )
    read = read_body(_docx(body))
    assert "position+2-size20-in20" in {m.kind for m in read.paragraphs[0].marks}
    with pytest.raises(RefusedError) as refused:
        _section(range(len(read.paragraphs)), read)
    assert (refused.value.code, refused.value.detail) == ("formatting", "position+2-size20-in20")


# Word's print of the 15% pattern's edge cases (2026-10-09, word-oracle/claude-edges-*): every
# shaded paragraph the reader reads, its index and its kind, and what the builder does; Word drew
# each such text #D9D9D9 (label-docx-reader-scratch/grey-shading/RESULTS.md).
EDGES = {
    "claude-edges-probe.docx": {
        # A run's: auto on auto; w:color absent; w:fill absent; both absent.
        0: "shading-pct15-AUTO-AUTO",
        2: "shading-pct15-AUTO-AUTO",
        4: "shading-pct15-AUTO-AUTO",
        6: "shading-pct15-AUTO-AUTO",
        # A paragraph's: auto on auto; both absent; auto on white.
        8: "shading-pct15-AUTO-AUTO",
        10: "shading-pct15-AUTO-AUTO",
        12: "shading-pct15-AUTO-FFFFFF",
        # In a cell shaded 000000: a run's on auto, on white; a paragraph's.
        14: "shading-pct15-AUTO-AUTO",
        16: "shading-pct15-AUTO-FFFFFF",
        18: "shading-pct15-AUTO-AUTO",
    },
    # On a page coloured FFFF00, which Word does not print: a run's on auto, on white; a
    # paragraph's; a run's with both absent.
    "claude-edges-page-probe.docx": {
        0: "shading-pct15-AUTO-AUTO",
        2: "shading-pct15-AUTO-FFFFFF",
        4: "shading-pct15-AUTO-AUTO",
        6: "shading-pct15-AUTO-AUTO",
    },
}


@pytest.mark.parametrize("probe", sorted(EDGES))
def test_the_15_percent_patterns_word_printed_as_d9d9d9_are_the_grey(probe: str) -> None:
    """The reader reads each of Word's printed edge cases as the plain kind (it reports no
    cell's shading or page colour on the text), and the builder carries each as the silver span;
    over a paragraph shaded 000000 the reader adds that shading, refused."""
    body = read_body((ORACLE / probe).read_bytes())
    shaded = {i: [m.kind for m in p.marks] for i, p in enumerate(body.paragraphs) if p.marks}
    expected = {i: [kind] for i, kind in EDGES[probe].items()}
    if probe == "claude-edges-probe.docx":
        expected[20] = ["shading-000000", "shading-pct15-AUTO-AUTO"]
    assert shaded == expected
    for at in EDGES[probe]:
        div, _ = _section(range(at, at + 1), body)
        assert GREY_SPAN in (div or ""), (probe, at)
    if probe == "claude-edges-probe.docx":
        with pytest.raises(RefusedError) as refused:
            _section(range(20, 21), body)
        assert (refused.value.code, refused.value.detail) == ("formatting", "shading-000000")


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


UNPAINTED = ("strike", "highlight-yellow", "highlight-darkBlue", "shading-000000", "shading-FF00AA")
ORACLE = Path(__file__).parent / "fixtures" / "word-oracle"


@pytest.mark.parametrize("kind", UNPAINTED)
def test_a_mark_word_paints_over_nothing_is_left_out(kind: str) -> None:
    """Word paints no strike, highlight or shading over U+0020 that end a paragraph's text (its
    own print, 2026-10-08): the narrative and the page leave the mark out, in a cell as outside,
    a paragraph or a cell of such spaces included."""
    div, page = _build(
        _p("Alpha beta   ", (10, 13, kind)),
        _p("Alpha beta   ", (11, 12, kind)),
        _p("   ", (0, 3, kind)),
        _p("x   ", (2, 3, kind), table=(0, 0, 0)),
        _p("   ", (0, 3, kind), table=(0, 0, 1)),
        tables=(_grid(2, [(0, 1, None), (1, 1, None)]),),
    )
    assert _inner(div) == (
        "<p>Alpha beta   </p><p>Alpha beta   </p>"
        "<table><tr><td><p>x   </p></td><td></td></tr></table>"
    )
    assert (
        page == "\nAlpha beta   \nAlpha beta   \n\ufdd0\n\ufdd2\t\ufdd3\tx   \t\ufdd3\t\n\ufdd1\n"
    )
    assert _same(div, page)


@pytest.mark.parametrize("kind", [*UNPAINTED, "dstrike"])
@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Alpha   beta", 5, 8),  # between words: Word paints it
        ("Alpha beta\u00a0\u00a0", 10, 12),  # over U+00A0: Word paints it
        ("Alpha beta \u00a0", 10, 11),  # U+00A0 after it
        ("Alpha  \nbeta", 5, 7),  # a line break and text after it
        ("Alpha beta  ", 6, 12),  # over a visible code point
        ("Alpha beta \t", 10, 11),  # a tab after it
        ("\u00a0\u00a0", 0, 2),  # a paragraph of U+00A0 alone
    ],
)
def test_a_mark_over_spaces_word_paints_is_refused(
    kind: str, text: str, start: int, end: int
) -> None:
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(_p(text, (start, end, kind), table=where), tables=tables)
        assert refused.value.code == "formatting", (kind, text, where)
    if kind != "dstrike":
        assert not word_epi.unpainted(_p(text), Mark(start, end, kind))


@pytest.mark.parametrize(
    "kind",
    [
        "dstrike",
        # A pattern's or a theme's shading: not yet probed in Word (a 15% grey is the template's).
        *("shading-pct20-AUTO-AUTO", "shading-solid-000000-AUTO", "shading-pct50-FF0000-FFFF00"),
        *("shading-THEME-background1", "shading-THEME-accent1"),
        # Not a solid fill of six hex digits, or not one of Word's highlight colours.
        *("shading-00000", "shading-0000000", "shading-00000g", "highlight-orange", "highlight-"),
    ],
)
def test_a_mark_word_was_not_shown_painting_over_trailing_spaces_is_refused(kind: str) -> None:
    """Only a strike, a highlight of Word's own colours and a solid shading were printed."""
    for tables, where in (((), None), (CELL, (0, 0, 0))):
        with pytest.raises(RefusedError) as refused:
            _build(_p("Alpha   ", (5, 8, kind), table=where), tables=tables)
        assert refused.value.code == "formatting", (kind, where)
        assert not word_epi.unpainted(_p("Alpha   "), Mark(5, 8, kind))


def test_the_highlights_are_words_sixteen_colours() -> None:
    assert {kind.removeprefix("highlight-") for kind in word_epi.HIGHLIGHTS} == {
        *("black", "blue", "cyan", "green", "magenta", "red", "yellow", "white", "darkBlue"),
        *("darkCyan", "darkGreen", "darkMagenta", "darkRed", "darkYellow", "darkGray", "lightGray"),
    }


def test_the_marks_word_painted_over_nothing_in_its_own_print() -> None:
    """Word's own print of the two synthetic probes (2026-10-08), read by the label reader: each
    mark over trailing spaces drew nothing, and is left out; over spaces between words, or over
    trailing U+00A0, it drew, and is refused."""
    strike = read_body((ORACLE / "claude-strike-probe.docx").read_bytes())
    painted = read_body((ORACLE / "claude-hl-probe.docx").read_bytes())
    for body, cases in (
        (strike, {0: "carried", 2: "formatting", 4: "carried", 6: "formatting"}),
        (painted, {0: "carried", 2: "formatting", 4: "carried", 6: "carried", 8: "carried"}),
        (painted, {10: "carried"}),
    ):
        for at, outcome in cases.items():
            assert (at, _outcome(body, at)) == (at, outcome)


def _outcome(body: Body, at: int) -> str:
    """Paragraph ``at`` built as a section on its own: its refusal's code, or "carried"."""
    try:
        div, page = _section(range(at, at + 1), body)
    except RefusedError as refused:
        return refused.code
    # A paragraph or a cell of the spaces alone draws nothing; no mark is written.
    assert (div, page) == (None, "") if blank(body.paragraphs[at]) else _same(div or "", page)
    assert "span" not in (div or "")
    return "carried"


def test_a_heading_may_end_in_spaces_word_paints_over_nothing() -> None:
    for kind in UNPAINTED:
        body = Body((_p("4.1 Y   ", (5, 8, kind)), _p("text"), _p("4.2 Z")), ())
        built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
        assert built["refused"] == 0, kind
        body = Body((_p("4.1   Y", (3, 6, kind)), _p("text"), _p("4.2 Z")), ())
        built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
        assert built["sections"][0]["refusal"]["code"] == "heading-formatting", kind


@pytest.mark.parametrize(
    ("paragraph", "code"),
    [
        (_p("struck", (0, 6, "strike")), "formatting"),
        (_p("Caps", (0, 4, "caps")), "formatting"),
        (_p("faint", (0, 5, "faint")), "formatting"),
        (_p("raised", (0, 6, "position-1-size22-in22")), "formatting"),
        (_p("raised", (0, 6, "position+8-size14-in22")), "formatting"),
        (_p("x", (0, 1, "highlight-yellow")), "formatting"),
        (_p("\u00a0", (0, 1, "shading-FFFF00")), "formatting"),
        (_p("x", (0, 1, "superscript"), (0, 1, "subscript")), "script"),
        (_p("a<b", (1, 2, "superscript")), "script"),
        (_p("x\u00bd", (1, 2, "superscript")), "script"),
        (_p("word\u00adbreak"), "soft-hyphen"),
        (_p("a\tb\tc"), "tab"),
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
    # The template's guidance in angle brackets; its black triangle, drawn 0.9% out of its own
    # proportions, its grey (a silver span, from word-epi 1.3.0) and, from word-epi 1.5.0, the
    # lone tab of "<2.1<tab>General description>" and "<11.<tab>DOSIMETRY>" (owner decision 11)
    # are carried.
    assert codes == {"carried": 29, "underline": 3}


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
    # Tabs (an indent, a lone one, several) and trailing spaces under a mark Word paints over
    # nothing (word-epi 1.5.0).
    if rng.random() < 0.2:
        at = rng.randint(0, len(text))
        text = text[:at] + "\t" + text[at:]
    if rng.random() < 0.1:
        text = "\t" + text
    marks = []
    for _ in range(rng.randint(0, 3)):
        if text:
            start = rng.randrange(len(text))
            marks.append((start, rng.randint(start + 1, len(text)), rng.choice(_KINDS)))
    if rng.random() < 0.1:
        marks.append((len(text), len(text) + 2, rng.choice(["strike", "highlight-yellow"])))
        text += "  "
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
            edges = []
            for r in range(rng.randint(1, 3)):
                # The grid columns a row leaves out at its start and end (decision 10).
                before = rng.choice([0, 0, 0, 1]) if columns > 1 else 0
                after = rng.choice([0, 0, 0, 1]) if columns - before > 1 else 0
                edges.append((before, after))
                cells, column = [], before
                while column < columns - after:
                    span = rng.randint(1, columns - after - column)
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
            tables.append(
                _rows_grid(columns, *((b, row, a) for (b, a), row in zip(edges, rows, strict=True)))
            )
        else:
            paragraphs.append(_random_paragraph(rng, None))
    return tuple(paragraphs), tuple(tables)


def test_whatever_is_not_refused_the_scanner_reads_as_the_page() -> None:
    rng = random.Random(20261005)
    outcomes: Counter[str] = Counter()
    reached: Counter[str] = Counter()
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
        rows = [row for table in tables for row in table["grid"]["rows"]]
        reached["an empty cell"] += any(row["before"] or row["after"] for row in rows)
        reached["a tab"] += any("\t" in p.text and not blank(p) for p in paragraphs)
        reached["unpainted"] += any(m.kind == "strike" for p in paragraphs for m in p.marks)
    # The run must reach both outcomes, and word-epi 1.5.0's rules, often enough to mean something.
    assert outcomes["carried"] > 250
    assert sum(outcomes.values()) - outcomes["carried"] > 500
    assert min(reached["an empty cell"], reached["a tab"], reached["unpainted"]) > 20


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
        return [
            [dataclasses.replace(cell, rows=1) for cell in row]
            for row in real(table, members, body)
        ]

    monkeypatch.setattr(word_epi, "_grid", misread)
    with pytest.raises(RefusedError) as refused:
        _build(*paragraphs, tables=tables)
    assert refused.value.code in ("narrative", "page-differs")


def _random_grid(rng: random.Random) -> dict[str, Any]:
    """A grid as the reader lays one: each row's cells side by side over the columns exactly."""
    columns = rng.randint(1, 6)
    rows = []
    for _ in range(rng.randint(1, 5)):
        before = rng.choice([0, 0, rng.randint(0, columns - 1)])
        after = rng.choice([0, 0, rng.randint(0, columns - before - 1)])
        cells, column = [], before
        while column < columns - after:
            span = rng.randint(1, columns - after - column)
            cells.append(
                {"column": column, "span": span, "merge": rng.choice([None, "restart", "continue"])}
            )
            column += span
        rows.append({"before": before, "after": after, "cells": cells})
    return {"columns": columns, "rows": rows}


def test_the_page_and_the_builder_lay_the_grid_alike_by_separate_code() -> None:
    """``_slots`` (the page) and ``_rows`` (the builder) are written apart; on random grids they
    write the same slots: each cell's first column U+FDD3, the rest U+FDD4, a merge's U+FDD5."""
    rng = random.Random(20261008)
    dropped = 0
    for _ in range(3000):
        grid = _random_grid(rng)
        laid = [
            [
                ("\ufdd5" if merge == "continue" else "\ufdd3" if k == 0 else "\ufdd4", n)
                for column, span, merge, n in row
                for k in range(span)
            ]
            for row in word_epi._rows(grid)
        ]
        assert word_epi._slots(grid) == laid, grid
        dropped += len(laid[0]) < grid["columns"]
    assert dropped > 300


def _html_table_model(div: str) -> list[list[str]]:
    """The narrative's table as the HTML table model places it: each slot the text of its cell,
    a cell named by where it starts, so that two cells of one text are told apart."""
    table = ET.fromstring(div).find("{http://www.w3.org/1999/xhtml}table")
    assert table is not None
    slots: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(table):
        k = 0
        for td in tr:
            while (r, k) in slots:
                k += 1
            name = f"{r},{k}:" + "".join(td.itertext())
            for dr in range(int(td.get("rowspan", "1"))):
                for dk in range(int(td.get("colspan", "1"))):
                    slots[r + dr, k + dk] = name
            k += int(td.get("colspan", "1"))
    rows = 1 + max(r for r, _ in slots)
    return [[slots[r, k] for k in range(1 + max(k for _, k in slots))] for r in range(rows)]


def _word_grid_model(
    grid: dict[str, Any], texts: dict[tuple[int, int], str]
) -> list[list[tuple[int, int, str]]]:
    """The same from Word's grid, by its own rules: each grid column's owner, row by row (a
    continued merge the owner above it, a column left out an empty cell of its own), then each
    column whose owners are those of the column before it, in every row, left out."""
    owners: list[list[tuple[int, int, str]]] = []
    for r, row in enumerate(grid["rows"]):
        owner = [(r, -1, "")] * row["before"]
        for c, cell in enumerate(row["cells"]):
            for k in range(cell["column"], cell["column"] + cell["span"]):
                owner.append(owners[-1][k] if cell["merge"] == "continue" else (r, c, texts[r, c]))
        owners.append(owner + [(r, -2, "")] * row["after"])
    kept = [k for k in range(grid["columns"]) if k == 0 or any(o[k] != o[k - 1] for o in owners)]
    return [[o[k] for k in kept] for o in owners]


def _same_partition(html: list[list[str]], word: list[list[tuple[int, int, str]]]) -> bool:
    """Whether the two grids group their slots into the same cells, each with the same text."""
    if [len(row) for row in html] != [len(row) for row in word]:
        return False
    pairs = {(h, w) for hs, ws in zip(html, word, strict=True) for h, w in zip(hs, ws, strict=True)}
    return len(pairs) == len({h for h, _ in pairs}) == len({w for _, w in pairs}) and all(
        h.split(":", 1)[1] == w[2] for h, w in pairs
    )


def _random_merged_grid(rng: random.Random) -> dict[str, Any]:
    """A random grid whose vertical merges are mostly ones Word writes: a cell continues the
    merge of the cell above it where that cell spans the same columns (and now and then where it
    does not, which the builder refuses)."""
    grid = _random_grid(rng)
    above: dict[tuple[int, int], str | None] = {}
    for row in grid["rows"]:
        here: dict[tuple[int, int], str | None] = {}
        for cell in row["cells"]:
            key = (cell["column"], cell["span"])
            if key in above and above[key] is not None and rng.random() < 0.7:
                cell["merge"] = "continue"
            elif rng.random() < 0.95:
                cell["merge"] = rng.choice([None, "restart", "restart"])
            here[key] = cell["merge"]
        above = here
    return grid


def test_each_carried_table_is_the_html_table_model_of_words_grid() -> None:
    """On random grids, the narrative's table, placed by the HTML table model, groups the slots
    into the cells Word's grid does (each owner of a column, merged down, the columns that only
    repeat the one before them left out), each cell with its own text."""
    rng = random.Random(20261009)
    carried = merged = 0
    for _ in range(3000):
        grid = _random_merged_grid(rng)
        texts = {
            (r, c): "" if cell["merge"] == "continue" else f"r{r}c{c}"
            for r, row in enumerate(grid["rows"])
            for c, cell in enumerate(row["cells"])
        }
        paragraphs = [_p(text, table=(0, r, c)) for (r, c), text in texts.items()]
        try:
            div, _ = _build(*paragraphs, tables=({"grid": grid, "parent": None, "reason": None},))
        except RefusedError:
            continue
        if not div:  # every cell blank: no table drawn
            continue
        carried += 1
        merged += "rowspan" in div
        assert _same_partition(_html_table_model(div), _word_grid_model(grid, texts)), grid
    assert carried > 1000
    assert merged >= 200


@pytest.mark.parametrize("bug", ["no column dropped", "the empty cell moved"])
def test_a_builder_that_lays_the_grid_otherwise_is_seen(
    bug: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page lays the grid by its own code, so a fault in ``_rows`` refuses the section."""
    # Row 0 leaves column 0 out; no cell starts at column 2.
    table = _rows_grid(3, (1, [(1, 2, None)], 0), (0, [(0, 3, None)], 0))
    paragraphs = _in_cells(["x"], ["y"])
    div, _ = _build(*paragraphs, tables=(table,))
    assert _inner(div) == (
        '<table><tr><td></td><td><p>x</p></td></tr><tr><td colspan="2"><p>y</p></td></tr></table>'
    )
    misread = {
        "no column dropped": [[(0, 1, None, None), (1, 2, None, 0)], [(0, 3, None, 0)]],
        "the empty cell moved": [[(0, 1, None, 0), (1, 1, None, None)], [(0, 2, None, 0)]],
    }[bug]
    monkeypatch.setattr(word_epi, "_rows", lambda _grid: misread)
    with pytest.raises(RefusedError) as refused:
        _build(*paragraphs, tables=(table,))
    assert refused.value.code in ("narrative", "page-differs")


VECTORS = json.loads((REPOSITORY / "test/fixtures/fidelity/vectors.json").read_text("utf-8"))


def test_the_verify_vectors_of_3_5_0_are_what_the_builder_writes() -> None:
    """The page and the narrative of fidelity-norm/3.5.0's two certified Word vectors
    (``test/fixtures/fidelity/cases.ts``) are what ``zone_a.word_epi`` writes for their read."""
    table = _rows_grid(
        3,
        (0, [(0, 2, None), (2, 1, None)], 0),
        (0, [(0, 2, None), (2, 1, None)], 0),
        (0, [(0, 2, None)], 1),
        (2, [(2, 1, None)], 0),
    )
    div, page = _build(
        _p("\tTable 2:\tDose by weight"),
        *_in_cells(
            ["Weight", "Dose"],
            ["Under 40 kg", "5 mg\t(one tablet)"],
            ["40 kg or more"],
            ["See 4.4"],
        ),
        _p("Take with food.  ", (15, 17, "strike")),
        tables=(table,),
    )
    by_name = {vector["name"]: vector for vector in VECTORS["verify"]}
    passed = by_name["certified-word-dropped-column-and-empty-cells"]["input"]
    failed = by_name["certified-word-empty-cell-on-the-wrong-side"]["input"]
    for vector in (passed, failed):
        assert [p["text"] for p in vector["source"]["pages"]] == [page]
    inner = passed["sections"][0]["div"].removeprefix('<div xmlns="http://www.w3.org/1999/xhtml">')
    assert _inner(div) == inner.removesuffix("</div>")


def _half_lives(text: str, *lowered: str) -> list[tuple[int, int, str]]:
    """Subscript marks over each of ``lowered``, found in ``text`` in order."""
    marks, at = [], 0
    for part in lowered:
        at = text.index(part, at)
        marks.append((at, at + len(part), "subscript"))
        at += len(part)
    return marks


def test_the_verify_vectors_of_3_6_0_are_what_the_builder_writes() -> None:
    """The page and the narrative of fidelity-norm/3.6.0's two certified Word vectors
    (``test/fixtures/fidelity/cases.ts``) are what ``zone_a.word_epi`` writes for their read: the
    half-lives T½, t½ with a phase's letter and t½ß, in a paragraph, a cell and brackets."""
    text = (
        "Half-lives: T\u00bd 12 h, t\u00bd\u03b1 1.5 h, t\u00bd\u03b4 4 h and t\u00bd\u00df 30 h."
    )
    lowered = ("\u00bd", "\u00bd\u03b1", "\u00bd\u03b4", "\u00bd\u00df")
    table = _grid(2, [(0, 1, None), (1, 1, None)], [(0, 1, None), (1, 1, None)])
    cells = _in_cells(["Phase", "Half-life"], ["Elimination", ""])
    cells[3] = _p("t\u00bd\u03b2 4 h", (1, 3, "subscript"), table=(0, 1, 1))
    div, page = _build(
        _p(text, *_half_lives(text, *lowered)),
        *cells,
        _p("(T\u00bd) as above.", (2, 3, "subscript")),
        tables=(table,),
    )
    by_name = {vector["name"]: vector for vector in VECTORS["verify"]}
    passed = by_name["certified-word-half-lives"]["input"]
    failed = by_name["certified-word-half-life-sharp-s-as-beta"]["input"]
    for vector in (passed, failed):
        assert [p["text"] for p in vector["source"]["pages"]] == [page]
    inner = passed["sections"][0]["div"].removeprefix('<div xmlns="http://www.w3.org/1999/xhtml">')
    assert _inner(div) == inner.removesuffix("</div>")


def test_the_verify_vectors_of_3_7_0_are_what_the_builder_writes() -> None:
    """The page and the narrative of fidelity-norm/3.7.0's two certified Word vectors
    (``test/fixtures/fidelity/cases.ts``) are what ``zone_a.word_epi`` writes for their read: a
    15% pattern grey over a run, over a whole paragraph and in a table cell."""
    first = "Report side effects via the national system."
    whole = "Keep out of the sight and reach of children."
    grey = first.index("via")
    cells = _in_cells(["Dose", "10 mg"])
    cells[1] = _p("10 mg", (0, 5, "shading-pct15-AUTO-FFFFFF"), table=(0, 0, 1))
    div, page = _build(
        _p(first, (grey, len(first) - 1, "shading-pct15-AUTO-AUTO")),
        _p(whole, (0, len(whole), "shading-pct15-AUTO-AUTO")),
        *cells,
        tables=(_grid(2, [(0, 1, None), (1, 1, None)]),),
    )
    by_name = {vector["name"]: vector for vector in VECTORS["verify"]}
    passed = by_name["certified-word-pattern-grey"]["input"]
    failed = by_name["certified-word-pattern-grey-dropped"]["input"]
    for vector in (passed, failed):
        assert [p["text"] for p in vector["source"]["pages"]] == [page]
    inner = passed["sections"][0]["div"].removeprefix('<div xmlns="http://www.w3.org/1999/xhtml">')
    assert _inner(div) == inner.removesuffix("</div>")


@pytest.mark.parametrize(
    ("text", "lowered", "carried"),
    [
        ("the T\u00bd was 4 h", ("\u00bd",), True),
        ("t\u00bd\u03b3 6 h", ("\u00bd\u03b3",), True),
        ("t\u00bd\u00df 30 h", ("\u00bd\u00df",), True),
        # Not μ, the micro prefix; nor another Greek letter; nor T inside a word.
        ("T\u00bd\u03bc g", ("\u00bd\u03bc",), False),
        ("t\u00bd\u03c9 2 h", ("\u00bd\u03c9",), False),
        ("AT\u00bd 4 h", ("\u00bd",), False),
        ("t\u00bd\u03b1\u03b2 2 h", ("\u00bd\u03b1\u03b2",), False),
    ],
)
def test_a_lowered_half_carries_as_section_5_keeps_it(
    text: str, lowered: tuple[str, ...], carried: bool
) -> None:
    """fidelity-norm/3.6.0: a half-life Word lowers is carried where section 5 keeps it, and the
    section refused where it does not (``narrative``, ``unmappable-script``)."""
    paragraph = _p(text, *_half_lives(text, *lowered))
    if carried:
        div, page = _build(paragraph)
        assert _same(div, page)
        return
    with pytest.raises(RefusedError) as refused:
        _build(paragraph)
    assert (refused.value.code, refused.value.detail) == ("narrative", "unmappable-script")


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
