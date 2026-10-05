"""A Word label's SmPC as ePI sections (``zone_a.word_epi``): narrative, page and refusals.

Bodies are built here, paragraph by paragraph; the one real file is the EMA's QRD template,
pinned in ``qrd/sources/``. A seeded run of random bodies holds the two outputs to each other:
whatever is not refused, the fidelity scanner reads the narrative as the page.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from label_docx import browser
from label_docx.reader import CommentReference, Mark, Numbering, Paragraph

from zone_a import drawing
from zone_a.certified import Body, read_body
from zone_a.fidelity.normalize import normalize_text
from zone_a.fidelity.xhtml import xhtml_to_text
from zone_a.structure import structure
from zone_a.word_epi import ROOT, RefusedError, _greys, _section, blank, sections

REPOSITORY = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((REPOSITORY / "qrd/registry/cap-smpc-en-10.4.json").read_text("utf-8"))
MAPPING = json.loads((REPOSITORY / "fhir/mappings/cap-smpc-en.json").read_text("utf-8"))
TEMPLATE = REPOSITORY / "qrd/sources/qrd-product-information-template-version-104_en.docx"
GREYS = _greys(REGISTRY)
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
    *paragraphs: Paragraph, tables: tuple[dict[str, Any], ...] = (), key: str = "smpc.4.1"
) -> tuple[str, str]:
    """The narrative and page of a section holding ``paragraphs``; refusals raise."""
    body = Body(tuple(paragraphs), tables, 0)
    div, text = _section(key, range(len(paragraphs)), body, GREYS)
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
        "<p><b>Ta</b><b><i>ke</i></b><i> 10</i> <sup>9</sup>/l now</p>"
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
        _p("c", label="3.", num=2),
        _p("d", label="4.", num=2),
        _p("e", label="b.", num=3),
        _p("f", label="ii.", num=4),
        _p("g", label="iii.", num=4),
        _p("h", label="I.", num=5),
    )
    assert _inner(div) == (
        "<ul><li>a</li><li>b</li></ul><p>between</p>"
        '<ol start="3"><li>c</li><li>d</li></ol>'
        '<ol type="a" start="2"><li>e</li></ol>'
        '<ol type="i" start="2"><li>f</li><li>g</li></ol>'
        '<ol type="I"><li>h</li></ol>'
    )
    assert text == "\n\u2022 a\n\u2022 b\nbetween\n3. c\n4. d\nb. e\nii. f\niii. g\nI. h\n"
    assert _same(div, text)


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


def test_the_templates_grey_is_left_out_only_where_the_registry_names_it() -> None:
    statement = next(g for g in GREYS if g.section == "smpc.4.8")
    grey = (statement.start, statement.end, "highlight-lightGray")
    div, _ = _build(_p(statement.text, grey), key="smpc.4.8.reporting")
    assert "highlight" not in div
    with pytest.raises(RefusedError) as refused:
        _build(_p(statement.text, grey), key="smpc.4.7")
    assert refused.value.code == "formatting"
    with pytest.raises(RefusedError):
        _build(_p(statement.text + " ", grey), key="smpc.4.8")
    with pytest.raises(RefusedError):
        _build(
            _p(statement.text, (statement.start, statement.end + 1, "highlight-lightGray")),
            key="smpc.4.8",
        )


@pytest.mark.parametrize(
    ("paragraph", "code"),
    [
        (_p("struck", (0, 6, "strike")), "formatting"),
        (_p("CAPS", (0, 4, "caps")), "formatting"),
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
        (_p("x", label="a)"), "list-label"),
        (_p("x", label="-"), "list-label"),
        (_p("x", label="01."), "list-label"),
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
        _build(_p("a", label="1."), _p("b", label="3."))
    assert refused.value.code == "list-label"


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
    body = Body((_p("4. X"), _p("4.1 Y"), _p("text"), _p(" "), _p("4.2 Z")), (), 0)
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
    body = Body((_p("4.1 Y"), _p("a", table=(0, 0, 0)), _p("4.2 Z", table=(0, 1, 0))), table, 0)
    built = sections(body, _structured({"smpc.4.1": 0, "smpc.4.2": 2}), REGISTRY)
    codes = {s["key"]: s["refusal"]["code"] for s in built["sections"]}
    assert codes == {"smpc.4.1": "table-across-sections", "smpc.4.2": "heading-in-table"}


def test_a_floating_object_refuses_the_document_and_an_unready_structure_is_an_error() -> None:
    body = Body((_p("4.1 Y"),), (), 1)
    with pytest.raises(RefusedError) as refused:
        sections(body, _structured({"smpc.4.1": 0}), REGISTRY)
    assert refused.value.code == "floating-object"
    with pytest.raises(ValueError, match="not ready"):
        sections(body, {**_structured({}), "ready": False}, REGISTRY)


def test_the_qrd_template_carries_what_its_markup_allows() -> None:
    body = read_body(TEMPLATE.read_bytes())
    # 6.5 and 6.6 as a person assigns them (test_structure.py).
    structured = structure(body.paragraphs, REGISTRY, MAPPING, {"smpc.6.5": 192, "smpc.6.6": 196})
    built = sections(body, structured, REGISTRY)
    codes = Counter((s["refusal"] or {}).get("code", "carried") for s in built["sections"])
    # The template's guidance in angle brackets, its tabs, its "*" and its triangle picture.
    assert codes == {"carried": 24, "underline": 3, "tab": 2, "formatting": 2, "picture": 1}


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
    assert outcomes["carried"] > 500
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
    assert result["epi"]["builder"] == "word-epi/1.0.0"
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
