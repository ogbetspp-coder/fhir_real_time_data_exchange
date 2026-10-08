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


def _rows() -> Iterator[tuple[str, DrawnRow, float, float, Paragraph]]:
    """Every row of every case: its case, the row, its gap and Word's space, the reader's read."""
    for name, rows_of in numbering_cases.ROWS.items():
        rows, ink = rows_of(), RECORD["cases"][name]["rows"]
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
    assert len(exact) == 2048
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
    assert len(spaces) == 132
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
    # reader before this rule, is wrong there.
    assert named == {("tab", True): 1679, ("legacy", False): 561, ("legacy", True): 78}
