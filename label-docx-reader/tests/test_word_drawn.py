"""The reader's pictures, theme shadings, pattern colours and tab leaders against Word's drawing.

Word's drawing of corpus/drawing-cases is in ``word-drawn.json`` beside them
(``scripts/word_drawn.py``): for a picture case, the box of its pixels, their digest and the ink
around them; for a row case, row by row, where the red and the blue ink are, the ink between them
and the colours the row is painted. These hold the reader to that drawing without Word: it
carries only what Word draws as it says, and refuses or names apart the rest.
"""

from __future__ import annotations

import hashlib
import json

import pytest

import drawing_cases
from label_docx.reader import DocxRefusedError, read_docx

FOLDER = drawing_cases.FOLDER
RECORD = json.loads((FOLDER / "word-drawn.json").read_text("utf-8"))


def _drawn(name: str) -> list[list[object]]:
    drawn: list[list[object]] = RECORD["cases"][name]["drawn"]
    return drawn


def _painted(row: list[object]) -> str:
    """The colour most of a row is painted, but white: FFFFFF where nothing is painted."""
    colours = str(row[5])
    return colours.split(":")[0] if colours else "FFFFFF"


def test_words_drawing_is_on_record_for_the_cases_as_they_are() -> None:
    assert set(RECORD["cases"]) == set(drawing_cases.CASES)
    for name, case in drawing_cases.CASES.items():
        data = (FOLDER / f"{name}.docx").read_bytes()
        assert RECORD["cases"][name]["sha256"] == hashlib.sha256(data).hexdigest(), name
        assert len(_drawn(name)) == (case.rows or 1), name


@pytest.mark.parametrize("mode", ["", "-compat"])
def test_a_picture_is_carried_only_where_word_draws_its_pixels_and_nothing_else(mode: str) -> None:
    # The box's size, its pixels' digest and no ink round it or elsewhere: as the plain one's,
    # in Word's own mode and in the QRD template's (compatibilityMode 15) alike.
    plain = _drawn(f"picture-plain{mode}")[0][2:]
    assert plain[3:] == [0, 0]
    assert plain == _drawn("picture-plain")[0][2:]
    alone, carried = set(), set()
    for name in drawing_cases.PICTURES:
        if _drawn(f"picture-{name}{mode}")[0][2:] == plain:
            alone.add(name)
        (paragraph,) = read_docx((FOLDER / f"picture-{name}{mode}.docx").read_bytes())
        reason = paragraph.pictures[0].reason
        assert reason in (None, "effects"), name
        if reason is None:
            carried.add(name)
    assert carried == alone
    # Word clips a picture under a negative effect extent, and draws a shadow or a line it has.
    assert set(drawing_cases.PICTURES) - alone == {
        "extent-negative",
        "extent-negative-bottom",
        "shadow-drawn",
        "line-drawn",
    }


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
        "shading",
        "shading-compat",
        "shading-srgb-white",
        "shading-red",
        "shading-unmapped",
        "shading-shades",
    ],
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


def test_a_theme_shading_is_resolved_only_where_word_paints_what_it_is_resolved_to() -> None:
    names = [entry[0] for entry in drawing_cases.SHADINGS]
    rows = dict(zip(names, _rows("shading"), strict=True))
    resolved = {name for name, (_, kinds) in rows.items() if not any("THEME" in k for k in kinds)}
    assert {name for name, _ in drawing_cases.SHADINGS if "THEME" in "".join(rows[name][1])} == {
        "bg1-shade-lower",
        "bg1-tint",
        "bg1-tint-shade",
        "bg1-nil",
        "light1",
        "accent1",
        "text1",
        "pct15-accent2",
        "pct15-accent2-shade",
        "pct15-text1",
        "pct15-fill-bg1",
    }
    themed = {name for name, shd in drawing_cases.SHADINGS if "themeFill" in shd}
    assert {"bg1", "bg1-stale", "bg1-shade-D9", "para-bg1-shade-D9"} <= themed & resolved
    # The same rows under a red lt1 or no mapping: never resolved, and Word paints the theme.
    for name in ("shading-red", "shading-unmapped"):
        for (_, kinds), (row, _) in zip(_rows(name), drawing_cases.SHADINGS, strict=True):
            if row in themed:
                assert kinds, (name, row)
                assert all("THEME" in kind for kind in kinds), (name, row)
    # In the QRD template's compatibility mode, Word paints and the reader reads every row alike.
    assert _rows("shading-compat") == list(rows.values())
    red = dict(zip(names, _rows("shading-red"), strict=True))
    assert red["bg1"][0] == "FF0000"
    assert red["bg1-shade-D9"][0] == "D90000"
    # A pattern's theme colour is not the automatic one: 15% of accent2, not grey.
    assert rows["pct15-accent2"][0] not in ("D9D9D9", "FFFFFF")
    assert rows["pct15-accent2"][1] == ["shading-pct15-THEME-accent2-AUTO"]


def test_every_shade_of_white_is_resolved_to_the_grey_word_paints() -> None:
    for value, (painted, kinds) in zip(drawing_cases.SHADES, _rows("shading-shades"), strict=True):
        assert painted == value * 3
        assert kinds == ([] if value == "FF" else [f"shading-{value * 3}"])


def _refused(data: bytes) -> bool:
    """Whether the reader refuses ``data`` for a tab's leader (it reads it otherwise)."""
    try:
        read_docx(data)
    except DocxRefusedError as refusal:
        return (refusal.code, refusal.detail) == ("unsupported-formatting", "a tab with a leader")
    return False


def _leader(row: list[object]) -> bool:
    """Whether Word drew a leader: ink across the gap, or the label's red past where it ends."""
    label_end, between = row[1], row[4]
    assert isinstance(label_end, float)
    assert isinstance(between, int)
    return between > 1000 or label_end > 100


@pytest.mark.parametrize(
    ("name", "settings"), [("tabs", None), ("tabs-compat", drawing_cases.COMPAT)]
)
def test_a_tab_is_refused_exactly_where_word_draws_a_leader_across_it(
    name: str, settings: str | None
) -> None:
    drawn = [_leader(row) for row in _drawn(name)]
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
    assert drawn.count(True) == 10
    for name in ("tabs-defaults", "tabs-table-style"):
        assert _leader(_drawn(name)[0])
        with pytest.raises(DocxRefusedError, match="a tab with a leader"):
            read_docx((FOLDER / f"{name}.docx").read_bytes())
