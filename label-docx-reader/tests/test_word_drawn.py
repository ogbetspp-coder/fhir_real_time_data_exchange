"""The reader's pictures, shadings, pattern colours and tab stops against Word's drawing.

Word's drawing of corpus/drawing-cases is in ``word-drawn.json`` beside them
(``scripts/word_drawn.py``): for a picture case, the box of its pixels, their digest and the ink
around them; for a row case, row by row, where the red and the blue ink are, the ink between them
and the colours the row is painted. These hold the reader to that drawing without Word: it
carries only what Word draws as it says, and refuses or names apart the rest. Every case is drawn
twice: as written, with no compatibility options, which Word opens in its Compatibility Mode, and
under the QRD template's (``compatibilityMode`` 15, ``-compat``), as EMA's labels are.
"""

from __future__ import annotations

import hashlib
import json

import pytest

import drawing_cases
from label_docx.reader import DocxRefusedError, read_docx

FOLDER = drawing_cases.FOLDER
RECORD = json.loads((FOLDER / "word-drawn.json").read_text("utf-8"))
MODES = ["", "-compat"]


def _drawn(name: str) -> list[list[object]]:
    drawn: list[list[object]] = RECORD["cases"][name]["drawn"]
    return drawn


def _painted(row: list[object]) -> str:
    """The colour most of a row is painted, but white: FFFFFF where nothing is painted."""
    colours = str(row[5])
    return colours.split(":")[0] if colours else "FFFFFF"


def _pixels(row: list[object], colour: str) -> int:
    """How many pixels of a row are ``colour`` (among its three commonest)."""
    found = dict(entry.split(":") for entry in str(row[5]).split())
    return int(found.get(colour, "0"))


def test_words_drawing_is_on_record_for_the_cases_as_they_are() -> None:
    assert set(RECORD["cases"]) == set(drawing_cases.CASES)
    for name, case in drawing_cases.CASES.items():
        data = (FOLDER / f"{name}.docx").read_bytes()
        assert RECORD["cases"][name]["sha256"] == hashlib.sha256(data).hexdigest(), name
        assert len(_drawn(name)) == (case.rows or 1), name


def test_word_draws_every_case_alike_in_both_modes() -> None:
    compat = [name for name in drawing_cases.CASES if name.endswith("-compat")]
    assert len(compat) * 2 == len(drawing_cases.CASES)
    for name in compat:
        own, twin = _drawn(name[: -len("-compat")]), _drawn(name)
        if name.startswith("picture-"):
            assert own[0][2:] == twin[0][2:], name
        else:
            assert [row[:5] for row in own] == [row[:5] for row in twin], name
            assert [row[5] for row in own] == [row[5] for row in twin], name


@pytest.mark.parametrize("mode", MODES)
def test_a_picture_is_carried_only_where_word_draws_its_pixels_and_nothing_else(mode: str) -> None:
    # The box's size, its pixels' digest and no ink round it or elsewhere: as the plain one's.
    plain = _drawn(f"picture-plain{mode}")[0][2:]
    assert plain[3:] == [0, 0]
    alone, carried = set(), set()
    for name in [*drawing_cases.PICTURES, *drawing_cases.PLACED]:
        if _drawn(f"picture-{name}{mode}")[0][2:] == plain:
            alone.add(name)
        reasons = [
            picture.reason
            for paragraph in read_docx((FOLDER / f"picture-{name}{mode}.docx").read_bytes())
            for picture in paragraph.pictures
        ]
        assert reasons in ([None], ["effects"]), name
        if reasons == [None]:
            carried.add(name)
    # Carried only where Word drew the picture alone; one it drew alone in a wide cell is not
    # carried (an extent's space in a cell is not free in a narrow one).
    assert carried == alone - {"extent-cell-wide"}
    # Word clips a picture under a negative effect extent, draws a shadow or a line it has, and
    # draws none in a narrow fixed cell or a frame too low for the extent.
    assert set(drawing_cases.PLACED) - alone == {"extent-cell-fixed", "extent-frame-exact"}
    assert set(drawing_cases.PICTURES) - alone == {
        "extent-negative",
        "extent-negative-bottom",
        "shadow-drawn",
        "line-drawn",
    }
    for name in ("extent-cell-fixed", "extent-frame-exact"):
        assert _drawn(f"picture-{name}{mode}")[0][:2] == [-1, -1]


def _rows(name: str) -> list[tuple[str, list[str]]]:
    """Each row of a shading case: what Word paints, and the reader's marks."""
    paragraphs = read_docx((FOLDER / f"{name}.docx").read_bytes())
    return [
        (_painted(row), [mark.kind for mark in paragraph.marks])
        for row, paragraph in zip(_drawn(name), paragraphs, strict=True)
    ]


@pytest.mark.parametrize(
    "name",
    [
        f"{name}{mode}"
        for name in ("shading", "shading-srgb-white", "shading-red", "shading-unmapped")
        for mode in MODES
    ]
    + ["shading-shades", "shading-shades-compat"],
)
def test_a_shading_the_reader_names_by_its_colour_is_the_colour_word_paints(name: str) -> None:
    for painted, kinds in _rows(name):
        assert len(kinds) <= 1
        kind = kinds[0] if kinds else "shading-FFFFFF"
        if "THEME" not in kind and "-" not in kind[len("shading-") :]:
            assert kind == f"shading-{painted}"
        if kind == "shading-pct15-AUTO-AUTO":
            # The pattern grey: Word paints it as D9D9D9 (a later fidelity norm's).
            assert painted == "D9D9D9"


@pytest.mark.parametrize("mode", MODES)
def test_a_theme_shading_is_resolved_only_where_word_paints_what_it_is_resolved_to(
    mode: str,
) -> None:
    names = [entry[0] for entry in drawing_cases.SHADINGS]
    rows = dict(zip(names, _rows(f"shading{mode}"), strict=True))
    resolved = {name for name, (_, kinds) in rows.items() if not any("THEME" in k for k in kinds)}
    assert {name for name, _ in drawing_cases.SHADINGS if "THEME" in "".join(rows[name][1])} == {
        "bg1-shade-lower",
        "bg1-tint",
        "bg1-tint-shade",
        "light1",
        "accent1",
        "text1",
        "pct15-accent2",
        "pct15-accent2-shade",
        "pct15-text1",
        "pct15-fill-bg1",
    }
    themed = {
        name for name, shd in drawing_cases.SHADINGS if "themeFill" in shd and "nil" not in shd
    }
    assert {"bg1", "bg1-stale", "bg1-shade-D9", "para-bg1-shade-D9"} <= themed & resolved
    # The same rows under a red lt1 or no mapping: never resolved, and Word paints the theme.
    for name in (f"shading-red{mode}", f"shading-unmapped{mode}"):
        for (_, kinds), (row, _) in zip(_rows(name), drawing_cases.SHADINGS, strict=True):
            if row in themed:
                assert kinds, (name, row)
                assert all("THEME" in kind for kind in kinds), (name, row)
    red = dict(zip(names, _rows(f"shading-red{mode}"), strict=True))
    assert red["bg1"][0] == "FF0000"
    assert red["bg1-shade-D9"][0] == "D90000"
    # A pattern's theme colour is not the automatic one: 15% of accent2, not grey.
    assert rows["pct15-accent2"][0] not in ("D9D9D9", "FFFFFF")
    assert rows["pct15-accent2"][1] == ["shading-pct15-THEME-accent2-AUTO"]
    # nil is no shading, as Word paints none, whatever its fill or theme.
    for row in ("bg1-nil", "nil-fill", "nil-fill-colour"):
        assert rows[row] == ("FFFFFF", []), row
    # A tint, and a tint with a shade, are named as such.
    assert rows["bg1-tint"][1] == ["shading-THEME-background1-tint80"]
    assert rows["bg1-tint-shade"][1] == ["shading-THEME-background1-tint80-shadeD9"]


@pytest.mark.parametrize("mode", MODES)
def test_every_shade_of_white_is_resolved_to_the_grey_word_paints(mode: str) -> None:
    rows = _rows(f"shading-shades{mode}")
    for value, (painted, kinds) in zip(drawing_cases.SHADES, rows, strict=True):
        assert painted == value * 3
        assert kinds == ([] if value == "FF" else [f"shading-{value * 3}"])


@pytest.mark.parametrize("mode", MODES)
def test_a_white_run_is_a_mark_where_word_paints_it_over_grey(mode: str) -> None:
    # Rows: a white run, a background1 run, an unshaded run, each in a grey paragraph; a white
    # run and an unshaded one in a grey cell (a table's row, then a paragraph between); a white
    # run on the page.
    drawn = _drawn(f"shading-white{mode}")
    grey = [_pixels(row, "D9D9D9") for row in drawn]
    # Word paints the white run over the grey: less grey than with the run unshaded.
    assert grey[0] == grey[1] < grey[2]
    assert grey[3] < grey[5]
    assert drawn[7][5] == ""
    paragraphs = read_docx((FOLDER / f"shading-white{mode}.docx").read_bytes())
    kinds = [sorted(m.kind for m in paragraph.marks) for paragraph in paragraphs]
    assert kinds == [
        ["shading-D9D9D9", "shading-FFFFFF"],
        ["shading-D9D9D9", "shading-FFFFFF"],
        ["shading-D9D9D9"],
        ["shading-FFFFFF"],
        [],
        [],
        [],
        [],
    ]


@pytest.mark.parametrize("mode", MODES)
def test_word_paints_a_themes_shade_over_trailing_spaces_as_a_fills(mode: str) -> None:
    # Rows: "A" then spaces under background1 shaded 80 (808080) ending the paragraph; the same
    # between "A" and "B"; a paragraph of them; a cell of them; a paragraph between; "A" then
    # spaces under a fill of 808080. Word paints the shade only between words, as the fill
    # (strike-spaces' record), so the builder may leave it out where it leaves the fill out.
    drawn = _drawn(f"shading-trailing{mode}")
    assert [_pixels(row, "808080") > 0 for row in drawn] == [
        False,
        True,
        False,
        False,
        False,
        False,
    ]
    paragraphs = read_docx((FOLDER / f"shading-trailing{mode}.docx").read_bytes())
    kinds = [[m.kind for m in paragraph.marks] for paragraph in paragraphs]
    assert kinds == [["shading-808080"]] * 4 + [[], ["shading-808080"]]


def _ink(row: list[object]) -> bool:
    """Whether Word drew a leader or a rule: ink across the gap, the label's red past where it
    ends (its leader is in its colour), or dark ink, the text being red and blue."""
    label_end, between = row[1], row[4]
    assert isinstance(label_end, float)
    assert isinstance(between, int)
    dark = any(
        all(int(colour[i : i + 2], 16) < 0x40 for i in (0, 2, 4)) and int(count) > 50
        for colour, count in (entry.split(":") for entry in str(row[5]).split())
    )
    return between > 1000 or label_end > 100 or dark


def _refused(data: bytes) -> bool:
    """Whether the reader refuses ``data`` for a tab stop (it reads it otherwise)."""
    try:
        read_docx(data)
    except DocxRefusedError as refusal:
        return refusal.code == "unsupported-formatting" and refusal.detail in (
            "a tab with a leader",
            "a bar tab stop",
        )
    return False


@pytest.mark.parametrize(
    ("name", "settings"), [("tabs", None), ("tabs-compat", drawing_cases.COMPAT)]
)
def test_a_tab_is_refused_exactly_where_word_draws_a_leader_or_a_rule(
    name: str, settings: str | None
) -> None:
    drawn = [_ink(row) for row in _drawn(name)]
    refused = []
    for row, content, props in drawing_cases.TABS:
        alone = drawing_cases.Case(
            row,
            drawing_cases.rows([(content, props)]),
            styles=drawing_cases.TAB_STYLES,
            numbering=drawing_cases.TAB_NUMBERING,
            settings=settings,
            rows=1,
        )
        refused.append(_refused(drawing_cases.package(alone)))
    assert refused == drawn
    assert drawn.count(True) == 12


@pytest.mark.parametrize("mode", MODES)
def test_a_leader_from_the_defaults_or_a_table_style_is_refused(mode: str) -> None:
    for name in ("tabs-defaults", "tabs-table-style"):
        assert _ink(_drawn(f"{name}{mode}")[0])
        with pytest.raises(DocxRefusedError, match="a tab with a leader"):
            read_docx((FOLDER / f"{name}{mode}.docx").read_bytes())
    # A table style's parts: Word drew the first row's leader and not the whole table's, and the
    # reader, not knowing which part applies, refuses both (a refusal beyond the drawing).
    drawn = _drawn(f"tabs-conditional{mode}")
    assert [_ink(drawn[0]), _ink(drawn[2])] == [True, False]
    case = drawing_cases.CASES["tabs-conditional"]
    for table in ("CondFirst", "CondWhole"):
        alone = case._replace(
            body=drawing_cases._styled_table(table, drawing_cases.row(drawing_cases.TABBED))
            + drawing_cases.row(""),
            settings=drawing_cases.COMPAT if mode else None,
        )
        assert _refused(drawing_cases.package(alone)), table
