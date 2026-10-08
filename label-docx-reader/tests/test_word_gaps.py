"""A Word 6 label is followed by a tab only where Word draws at least a space after it.

Word's drawing of the corpus/numbering-cases legacy-drawn cases is in ``word-gaps.json`` beside
them (``scripts/word_gaps.py``): for each row, where the label's ink and the text's ink start and
end, in points. A row's gap is its text's start less its label's start, less the same for its
label drawn with nothing after it at that size and in that face: the glyphs' sides cancel, and
what is left is the gap Word draws. These hold the reader, its rule and its constants to that
drawing without Word.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import numbering_cases
import word_gaps
from label_docx.reader import (
    LEGACY_ADVANCES,
    LEGACY_EM,
    LEGACY_MARGIN,
    LEGACY_SPACE,
    Paragraph,
    read_docx,
)
from numbering_cases import DrawnRow

FOLDER = Path(__file__).resolve().parents[1] / "corpus" / "numbering-cases"
RECORD = json.loads((FOLDER / "word-gaps.json").read_text("utf-8"))
PIXEL = 1 / RECORD["scale"]
# Rows whose paragraph hangs further than legacyIndent: Word starts the text there, further out.
HANGING = {"ind-direct", "ind-style", "ind-style-direct", "level-negative"}


def _face(row: DrawnRow) -> str:
    return row.condition if row.condition in numbering_cases.DRAWN_FACES else "plain"


BANDED = [name for name in numbering_cases.ROWS if "sample" not in name]
SAMPLES = [name for name in numbering_cases.ROWS if "sample" in name]


def _rows() -> Iterator[tuple[str, DrawnRow, float, float, Paragraph]]:
    """Every row of every case but the samples: its case, the row, its gap and Word's space, and
    the reader's read."""
    for name in BANDED:
        rows = numbering_cases.ROWS[name]()
        ink = [row[:4] for row in RECORD["cases"][name]["rows"]]
        paragraphs = read_docx((FOLDER / f"{name}.docx").read_bytes())
        offset = {
            (row.label, row.size, _face(row), row.suff): text - label
            for row, (label, _, text, _) in zip(rows, ink, strict=True)
            if row.suff
        }
        for row, (label, _, text, _), paragraph in zip(rows, ink, paragraphs, strict=True):
            key = (row.label, row.size, _face(row))
            alone = offset[(*key, "nothing")]
            yield name, row, text - label - alone, offset[(*key, "space")] - alone, paragraph


def test_words_drawing_is_on_record_for_the_cases_as_they_are() -> None:
    assert set(RECORD["cases"]) == set(numbering_cases.ROWS)
    for name, rows_of in numbering_cases.ROWS.items():
        case = RECORD["cases"][name]
        assert case["sha256"] == hashlib.sha256((FOLDER / f"{name}.docx").read_bytes()).hexdigest()
        assert len(case["rows"]) == len(rows_of())


def test_each_rows_ink_is_its_own_clear_of_its_bands_edges() -> None:
    # A row whose ink comes near its band's edge may hold its neighbour's ink, or lend its own.
    for name in numbering_cases.ROWS:
        for index, row in enumerate(RECORD["cases"][name]["rows"]):
            assert -1.0 not in row
            assert word_gaps.inside(name, index, row), (name, index)


def _predicted(row: DrawnRow, paragraph: Paragraph) -> float:
    assert paragraph.numbering is not None
    assert row.legacy is not None
    font = "Symbol" if row.label in ("bullet", "minus") else "Times New Roman"
    units = sum(LEGACY_ADVANCES[font][c] for c in str(paragraph.numbering.text))
    space, indent = row.legacy
    return max(indent / 20 - units / LEGACY_EM * row.size / 2, space / 20)


def test_word_draws_the_text_at_the_greater_of_legacy_indent_and_the_label_and_legacy_space() -> (
    None
):
    exact = [
        abs(gap - _predicted(row, paragraph))
        for _, row, gap, _, paragraph in _rows()
        if row.legacy is not None
        and row.condition not in HANGING
        and not (row.label == "bullet" and row.condition in ("b", "bi"))
    ]
    assert len(exact) == 3338
    # To a pixel of Word's drawing, which the margin exceeds.
    assert max(exact) <= PIXEL + 1e-9 < LEGACY_MARGIN / 20
    # A paragraph hanging further only moves the text further out: the rule is a floor there.
    further = [
        gap - _predicted(row, paragraph)
        for _, row, gap, _, paragraph in _rows()
        if row.legacy is not None and row.condition in HANGING
    ]
    assert min(further) >= -PIXEL
    assert max(further) > 10
    # Word draws Symbol's bold, which it makes up, wider: the rule is not a floor there.
    bold = [
        gap - _predicted(row, paragraph)
        for _, row, gap, _, paragraph in _rows()
        if row.legacy is not None and row.label == "bullet" and row.condition in ("b", "bi")
    ]
    assert min(bold) < -2 * PIXEL


def test_word_draws_a_labels_space_as_wide_as_legacy_space() -> None:
    spaces = [(row, space) for _, row, _, space, _ in _rows() if row.suff == "space"]
    assert len(spaces) == 180
    for row, space in spaces:
        assert abs(space - LEGACY_SPACE / LEGACY_EM * row.size / 2) <= PIXEL + 1e-9


def test_a_word_6_label_is_followed_by_a_tab_only_where_word_draws_a_space_or_more() -> None:
    named: dict[tuple[str, bool], int] = {}
    for _, row, gap, space, paragraph in _rows():
        assert paragraph.numbering is not None
        suffix = paragraph.numbering.suffix
        if row.legacy is None:
            assert suffix == row.suff
            continue
        spaced = gap >= space
        assert suffix in ("tab", "legacy")
        assert spaced or suffix == "legacy", row
        named[(str(suffix), spaced)] = named.get((str(suffix), spaced), 0) + 1
    # Word draws no space after some (as "10.5 mg"): a bare tab for every Word 6 label, the
    # reader before this rule, is wrong there. "2345." and "6789.", longer than any label drawn
    # at random, are not taken.
    assert named == {("tab", True): 2785, ("legacy", False): 924, ("legacy", True): 169}


def _sample(name: str) -> Iterator[tuple[DrawnRow, float, float, Paragraph]]:
    """A sample's rows: each, its gap from its label's start, its control's, the reader's read."""
    rows, ink = numbering_cases.ROWS[name](), RECORD["cases"][name]["rows"]
    paragraphs = [p for p in read_docx((FOLDER / f"{name}.docx").read_bytes()) if p.numbering]
    for index in range(0, len(rows), 2):
        row, control = ink[index], ink[index + 1]
        yield rows[index], row[2] - row[0], control[2] - control[0], paragraphs[index]


def test_a_sample_of_all_the_whitelist_takes_draws_every_tab_a_space_wide() -> None:
    # Seeded (numbering_cases.SAMPLE_SEED), jointly: label characters, size, faces, gaps, the
    # level's and the paragraph's indents, tab stops, alignment, style, spacing and other
    # properties; from INHERITED, where the label's run properties and the paragraph's are set,
    # and the mark's and the text's sizes. Each case is its own document (DRAWN_SAMPLE_PAGES):
    # no settings part, each compat combination, and the corpus's default tab stops, line
    # pitches and defaults.
    assert numbering_cases.SAMPLE_SEED == 20261008
    assert len(SAMPLES) == len(numbering_cases.DRAWN_SAMPLE_PAGES) == 18
    named: dict[str, int] = {}
    for name in SAMPLES:
        for row, gap, space, paragraph in _sample(name):
            assert paragraph.numbering is not None
            suffix = str(paragraph.numbering.suffix)
            named[suffix] = named.get(suffix, 0) + 1
            if suffix == "tab":
                assert gap >= space, (name, row)
                continue
            # Every sampled row is one the whitelist takes: a legacy one has the gap short.
            assert row.legacy is not None
            parts = dict(row.extra)
            units = sum(LEGACY_ADVANCES[parts["font"]][c] for c in str(paragraph.numbering.text))
            space_twips, indent = row.legacy
            floor = max(indent * LEGACY_EM - units * row.size * 10, space_twips * LEGACY_EM)
            assert floor < LEGACY_SPACE * row.size * 10 + LEGACY_MARGIN * LEGACY_EM
    assert named == {"tab": 711, "legacy": 99}


def test_word_draws_the_rule_to_a_pixel_within_half_a_point_of_a_space() -> None:
    # Each sample from INHERITED ends with NEAR rows whose legacyIndent puts the text, by the
    # rule, 0.2 to 0.5 pt past a space or short of one; Word draws each where the rule says, to a
    # pixel, beside its own space, and every tab among them at least a space wide.
    named: dict[str, int] = {}
    for case in range(numbering_cases.INHERITED, len(numbering_cases.DRAWN_SAMPLE_PAGES)):
        rows = list(_sample(f"legacy-drawn-sample-{case}"))[-numbering_cases.NEAR :]
        for row, gap, space, paragraph in rows:
            assert paragraph.numbering is not None
            assert row.legacy is not None
            font = dict(row.extra)["font"]
            units = sum(LEGACY_ADVANCES[font][c] for c in str(paragraph.numbering.text))
            past = (row.legacy[1] / 20) - (units + LEGACY_SPACE) / LEGACY_EM * row.size / 2
            assert 0.17 <= abs(past) <= 0.53
            assert abs(gap - space - past) <= PIXEL + 1e-9
            suffix = str(paragraph.numbering.suffix)
            assert suffix == ("tab" if past >= LEGACY_MARGIN / 20 else "legacy")
            assert gap >= space or suffix == "legacy"
            named[suffix] = named.get(suffix, 0) + 1
    assert named == {"tab": 24, "legacy": 21}
