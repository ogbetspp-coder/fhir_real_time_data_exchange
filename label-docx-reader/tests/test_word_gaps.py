"""A Word 6 label is followed by a tab only where Word draws at least a space after it.

Word's drawing of corpus/numbering-cases legacy-drawn is in ``word-gaps.json`` beside it
(``scripts/word_gaps.py``): for each row, where the label's ink and the text's ink start and end,
in points. A row's gap is its text's start less its label's start, less the same for the label
drawn with nothing after it at that size: the glyphs' sides cancel, and what is left is the
gap Word draws. These hold the reader, its rule and its constants to that drawing without Word.
"""

from __future__ import annotations

import hashlib
import json
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

CASE = Path(__file__).resolve().parents[1] / "corpus" / "numbering-cases" / "legacy-drawn.docx"
RECORD = json.loads(CASE.with_name("word-gaps.json").read_text("utf-8"))
ROWS = numbering_cases.drawn_rows()
PIXEL = 1 / RECORD["scale"]


def _gaps() -> list[float]:
    """Each row's gap as Word drew it, in points."""
    ink = RECORD["rows"]
    alone = {
        (row.label, row.size): text - label
        for row, (label, _, text, _) in zip(ROWS, ink, strict=True)
        if row.suff == "nothing"
    }
    return [
        text - label - alone[(row.label, row.size)]
        for row, (label, _, text, _) in zip(ROWS, ink, strict=True)
    ]


def _paragraphs() -> list[Paragraph]:
    return read_docx(CASE.read_bytes())


def _spaces() -> dict[tuple[str, int], float]:
    """The gap Word draws for a label whose suffix is a space, by label and size."""
    return {
        (row.label, row.size): gap
        for row, gap in zip(ROWS, _gaps(), strict=True)
        if row.suff == "space"
    }


def test_words_drawing_is_on_record_for_the_case_as_it_is() -> None:
    assert RECORD["sha256"] == hashlib.sha256(CASE.read_bytes()).hexdigest()
    assert len(RECORD["rows"]) == len(ROWS) == len(_paragraphs())


def test_word_draws_the_text_at_the_greater_of_legacy_indent_and_the_label_and_legacy_space() -> (
    None
):
    off = []
    for row, gap, paragraph in zip(ROWS, _gaps(), _paragraphs(), strict=True):
        if row.legacy is None:
            continue
        assert paragraph.numbering is not None
        font = "Symbol" if row.label in ("bullet", "minus") else "Times New Roman"
        units = sum(LEGACY_ADVANCES[font][c] for c in str(paragraph.numbering.text))
        advance = units / LEGACY_EM * row.size / 2
        space, indent = row.legacy
        off.append(abs(gap - max(indent / 20 - advance, space / 20)))
    assert len(off) == 674
    # To a pixel of Word's drawing, which the margin exceeds.
    assert max(off) <= PIXEL + 1e-9 < LEGACY_MARGIN / 20


def test_word_draws_a_labels_space_as_wide_as_legacy_space() -> None:
    spaces = _spaces()
    assert len(spaces) == len(numbering_cases.DRAWN_LABELS) * len(numbering_cases.DRAWN_SIZES)
    for (_, size), gap in spaces.items():
        assert abs(gap - LEGACY_SPACE / LEGACY_EM * size / 2) <= PIXEL + 1e-9


def test_a_word_6_label_is_followed_by_a_tab_only_where_word_draws_a_space_or_more() -> None:
    spaces = _spaces()
    named: dict[tuple[str, bool], int] = {}
    for row, gap, paragraph in zip(ROWS, _gaps(), _paragraphs(), strict=True):
        assert paragraph.numbering is not None
        suffix = paragraph.numbering.suffix
        if row.legacy is None:
            assert suffix == row.suff
            continue
        spaced = gap >= spaces[(row.label, row.size)]
        assert suffix in ("tab", "legacy")
        assert spaced or suffix == "legacy", row
        named[(str(suffix), spaced)] = named.get((str(suffix), spaced), 0) + 1
    # Word draws no space after some (as "10.5 mg"): a bare tab for every Word 6 label, the
    # reader before this rule, is wrong there.
    assert named == {("tab", True): 503, ("legacy", False): 170, ("legacy", True): 1}
