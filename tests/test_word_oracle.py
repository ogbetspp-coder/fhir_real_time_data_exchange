"""The reader draws the labels and marks, and reads the fields, as Microsoft Word does.

The answers are in corpus/*/word.json.

Word's answers were recorded by ``scripts/word_oracle.py record``; this holds the reader to them
without Word. Where the reader reads a document, every list label and every footnote and endnote
mark must be Word's. Where it refuses, the refusal must be one listed here, with the reason
Word's answer is not taken.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from label_docx.word import (
    _has_stories,
    _label_fonts,
    emphasis_verdict,
    field_verdict,
    label_as_drawn,
    note_verdict,
    print_verdict,
    reader_labels,
    reader_note_marks,
    story_verdict,
    text_verdict,
    verdict,
)
from test_reader import _field, _marked, docx, p, r

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
RECORDS = sorted(CORPUS.glob("*/word.json"))

# Documents the reader refuses although Word draws labels or marks for them, and why.
REFUSED = {
    # A numStyleLink to an abstractNum with no styleLink back: Word draws an empty label.
    "numbering-cases/numbering-style-link-one-way.docx": "unsupported-numbering",
    # A custom-marked footnote whose text holds footnoteRef: Word draws there the number the
    # next footnote will take, which no reference shows.
    "numbering-cases/notes-custom-mark.docx": "ambiguous-numbering",
    # lvlRestart naming the level directly above, written out: Word draws the level empty.
    "numbering-cases/restart-level-above.docx": "unsupported-numbering",
    # A level that never restarts shown in a deeper label: Word draws it as a space.
    "numbering-cases/restart-never-shown-deeper.docx": "ambiguous-numbering",
    # A level restarted by another list's paragraph, counted in a later row of the same table:
    # Word does not take that list's override there, though it does in the same row.
    "numbering-cases/restart-source-rows.docx": "ambiguous-numbering",
    # A level that never restarts, counted after a higher paragraph and a table row's end: Word
    # draws it one less than its count in some tables and not in others.
    "numbering-cases/restart-never-rows.docx": "ambiguous-numbering",
    # A level counted on from another list's override, taken through a deeper paragraph, after
    # a table row's end: Word takes the override here, but not in other tables (a generated
    # document, fuzz_docx seed 903 with tables of several rows).
    "numbering-cases/override-implicit-rows.docx": "ambiguous-numbering",
    # A level with lvlRestart first counted by a deeper item: Word draws it otherwise later.
    "numbering-cases/restart-skipped-ancestor.docx": "ambiguous-numbering",
    # A STYLEREF to a heading with a Symbol character: Word leaves it out of the result.
    "numbering-cases/fields-styleref-symbol.docx": "computed-field",
    # A REF to a bookmark that is not there: Word prints "Error! Reference source not found."
    "numbering-cases/fields-ref-missing.docx": "computed-field",
    # Captions stored as 7 and 7, which Word shows on screen and prints as 1 and 2.
    "numbering-cases/fields-stale.docx": "stale-field",
    # A cross-reference stored as other text than its bookmark's, and a note reference stored as 7
    # for note 1: Word prints the bookmark's text and the mark 1.
    "numbering-cases/fields-ref-stale.docx": "stale-field",
    "numbering-cases/fields-noteref-stale.docx": "stale-field",
    # A SEQ with no stored result: Word shows nothing on screen and prints 1.
    "numbering-cases/fields-seq-shown-no-result.docx": "field-without-result",
    # EMA's stray U+F02D in Times New Roman, a code no font draws as the template means it.
    "ema-templates/qrd-product-information-template-version-104_es.docx": "private-use-character",
}


def _record(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return data


def _cases() -> list[tuple[Path, list[str], list[str | None]]]:
    out: list[tuple[Path, list[str], list[str | None]]] = []
    for record in RECORDS:
        labels, fonts = _record(record)["drawn"], _record(record)["fonts"]
        out += [(record.parent / name, word, fonts[name]) for name, word in sorted(labels.items())]
    return out


def _note_cases() -> list[tuple[Path, dict[str, list[str]]]]:
    out: list[tuple[Path, dict[str, list[str]]]] = []
    for record in RECORDS:
        marks = _record(record)["notes"]
        out += [(record.parent / name, word) for name, word in sorted(marks.items())]
    return out


def test_every_corpus_document_has_words_answer() -> None:
    assert RECORDS
    for record in RECORDS:
        labels = _record(record)["drawn"]
        present = {path.name for path in record.parent.glob("*.docx")}
        assert set(labels) == present, f"{record.parent.name}: run scripts/word_oracle.py record"


def test_words_note_marks_are_on_record() -> None:
    # The footnote cases and the EMA templates with footnotes, at least.
    assert len(_note_cases()) >= 15


def test_words_label_fonts_are_on_record_for_every_label() -> None:
    for record in RECORDS:
        labels, fonts = _record(record)["drawn"], _record(record)["fonts"]
        assert {n: len(w) for n, w in labels.items()} == {n: len(f) for n, f in fonts.items()}


@pytest.mark.parametrize(
    ("path", "word", "fonts"), _cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_draws_words_labels_or_refuses_as_listed(
    path: Path, word: list[str], fonts: list[str | None]
) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = verdict(word, reader_labels(path), fonts)
    if key in REFUSED:
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"


@pytest.mark.parametrize(
    ("path", "word"), _note_cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_draws_words_note_marks_or_refuses_as_listed(
    path: Path, word: dict[str, list[str]]
) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = note_verdict(word, reader_note_marks(path))
    if key in REFUSED:
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"


def _field_cases() -> list[tuple[Path, dict[str, list[str]]]]:
    out: list[tuple[Path, dict[str, list[str]]]] = []
    for record in RECORDS:
        fields = _record(record)["fields"]
        out += [(record.parent / name, word) for name, word in sorted(fields.items())]
    return out


def test_words_field_results_are_on_record() -> None:
    # The field cases, the stale one among them.
    assert len(_field_cases()) >= 11
    assert any(word["shown"] != word["printed"] for _, word in _field_cases())


@pytest.mark.parametrize(
    ("path", "word"), _field_cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_reads_fields_only_where_word_prints_what_it_shows(
    path: Path, word: dict[str, list[str]]
) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = field_verdict(word, path)
    # A stale-field refusal agrees with Word when Word prints other than it shows.
    if key in REFUSED and REFUSED[key] != "stale-field":
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"


def _print_cases() -> list[tuple[Path, bool]]:
    out: list[tuple[Path, bool]] = []
    for record in RECORDS:
        prints = _record(record)["prints"]
        out += [(record.parent / name, same) for name, same in sorted(prints.items())]
    return out


def test_every_corpus_document_has_words_print() -> None:
    for record in RECORDS:
        prints = _record(record)["prints"]
        present = {path.name for path in record.parent.glob("*.docx")}
        assert set(prints) == present, f"{record.parent.name}: run scripts/word_oracle.py record"


@pytest.mark.parametrize(
    ("path", "same"), _print_cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_reads_only_documents_word_prints_as_it_shows_them(
    path: Path, same: bool
) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = print_verdict(same, path)
    if key in REFUSED and REFUSED[key] != "stale-field":
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"


def _emphasis_cases() -> list[tuple[Path, dict[str, list[bool]]]]:
    out: list[tuple[Path, dict[str, list[bool]]]] = []
    for record in RECORDS:
        emphasis = _record(record)["emphasis"]
        out += [(record.parent / name, word) for name, word in sorted(emphasis.items())]
    return out


def test_words_emphasis_is_on_record_for_every_document() -> None:
    for record in RECORDS:
        emphasis = _record(record)["emphasis"]
        present = {path.name for path in record.parent.glob("*.docx")}
        assert set(emphasis) == present, f"{record.parent.name}: run scripts/word_oracle.py record"


@pytest.mark.parametrize(
    ("path", "word"), _emphasis_cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_marks_bold_italic_caps_and_strike_as_word_shows_them(
    path: Path, word: dict[str, list[bool]]
) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = emphasis_verdict(word, path)
    if key in REFUSED:
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"


def test_a_label_is_drawn_in_the_font_word_gave_it() -> None:
    assert label_as_drawn("\uf0b7\t", "Symbol") == "\u2022\t"
    assert label_as_drawn("\u00b7", "Symbol") == "\u2022"  # Symbol's code, stored as itself
    assert label_as_drawn("\uf0a7\t", "Wingdings") == "\u25aa\t"
    assert label_as_drawn("\u00a7", "Wingdings") == "\u25aa"
    # In any other font, or a code in no table: as stored, so the reader's mapping must match it.
    assert label_as_drawn("\u00a7", "Arial") == label_as_drawn("\u00a7", None) == "\u00a7"
    assert label_as_drawn("\uf0d8", "Wingdings") == "\uf0d8"
    assert verdict(["\u00a7\t"], ["\u25aa\t"], ["Wingdings"]) == "agrees"
    assert verdict(["\u00a7\t"], ["\u25aa\t"], ["Arial"]).startswith("differs")


def test_label_fonts_are_read_from_words_saved_copy_and_must_be_its_labels(tmp_path: Path) -> None:
    run = '<w:r><w:rPr><w:rFonts w:ascii="{0}" w:hAnsi="{1}"/></w:rPr><w:t>{2}</w:t></w:r>'
    original = tmp_path / "a.docx"
    original.write_bytes(
        docx("<w:p><w:r><w:t>one</w:t></w:r></w:p><w:p><w:r><w:t>two</w:t></w:r></w:p>")
    )
    box = (
        "<w:r><w:pict><w:txbxContent><w:p><w:r><w:t>boxed</w:t></w:r></w:p></w:txbxContent>"
        "</w:pict></w:r>"
    )
    square = run.format("Wingdings", "Wingdings", "\uf0a7")
    number = run.format("Symbol", "Arial", "1.")
    saved = docx(
        f"<w:p>{square}<w:r><w:tab/></w:r><w:r><w:t>one</w:t></w:r>{box}</w:p>"
        f"<w:p>{number}<w:r><w:t>two</w:t></w:r></w:p>"
    )
    assert _label_fonts(original, saved, ["\uf0a7\t", "1."]) == ["Wingdings", "mixed"]
    with pytest.raises(SystemExit):
        _label_fonts(original, saved, ["\uf0a7\t"])  # not the labels Word drew
    with pytest.raises(SystemExit):
        _label_fonts(original, docx("<w:p><w:r><w:t>one</w:t></w:r></w:p>"), [])  # one less
    # Word's empty paragraph after a closing table is no paragraph of the document's.
    body = "<w:p><w:r><w:t>one</w:t></w:r></w:p><w:p><w:r><w:t>two</w:t></w:r></w:p>"
    assert _label_fonts(original, docx(body + "<w:p/>"), []) == []
    with pytest.raises(SystemExit):
        _label_fonts(original, docx(body + "<w:p><w:r><w:t>x</w:t></w:r></w:p>"), [])


def test_a_paragraph_word_did_not_measure_is_not_agreement(tmp_path: Path) -> None:
    hidden_mark = "<w:pPr><w:rPr><w:vanish/></w:rPr></w:pPr>"
    path = tmp_path / "a.docx"
    path.write_bytes(
        docx(
            "<w:p><w:r><w:t>a</w:t></w:r></w:p>"
            f"<w:p>{hidden_mark}</w:p><w:p><w:r><w:t>b</w:t></w:r></w:p>"
        )
    )
    measured = [False] * 4
    assert emphasis_verdict({}, path).startswith("differs at paragraph 1")
    # Word joins "b" to the paragraph whose mark is hidden: not measured on its own.
    assert emphasis_verdict({"0": measured}, path) == "agrees"


def _story_cases() -> list[tuple[Path, dict[str, list[list[Any]]]]]:
    out: list[tuple[Path, dict[str, list[list[Any]]]]] = []
    for record in RECORDS:
        stories = _record(record).get("stories", {})
        out += [(record.parent / name, word) for name, word in sorted(stories.items())]
    return out


def test_words_headers_footers_and_comments_are_on_record_for_every_document_with_them() -> None:
    on_record = {path for path, _ in _story_cases()}
    having = {
        path for record in RECORDS for path in record.parent.glob("*.docx") if _has_stories(path)
    }
    assert on_record == having
    assert len(on_record) >= 20


@pytest.mark.parametrize(
    ("path", "word"), _story_cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_reads_headers_footers_and_comments_as_word_shows_them(
    path: Path, word: dict[str, list[list[Any]]]
) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = story_verdict(word, path)
    if key in REFUSED:
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"


def _text_cases() -> list[tuple[Path, list[str]]]:
    out: list[tuple[Path, list[str]]] = []
    for record in RECORDS:
        texts = _record(record).get("text", {})
        out += [(record.parent / name, word) for name, word in sorted(texts.items())]
    return out


def test_words_text_is_on_record_for_every_corpus_document() -> None:
    on_record = {path for path, _ in _text_cases()}
    assert on_record == {path for record in RECORDS for path in record.parent.glob("*.docx")}


@pytest.mark.parametrize(
    ("path", "word"), _text_cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_reads_the_body_text_word_shows(path: Path, word: list[str]) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = text_verdict(word, path)
    if key in REFUSED:
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"


def test_a_page_place_agrees_only_with_a_page_number(tmp_path: Path) -> None:
    path = tmp_path / "a.docx"
    see = r('<w:t xml:space="preserve">See page </w:t>') + _field("PAGEREF _Ref1 \\h", "12")
    path.write_bytes(
        docx(p(_marked("_Ref1", r("<w:t>Table 1</w:t>"))) + p(see + r("<w:t>.</w:t>")))
    )
    for page in ("12", "xiv", "IV"):
        assert text_verdict(["Table 1", f"See page {page}."], path) == "agrees"
    # PAGEREF \\p shows "above" or "below", which is not a page number.
    for words in ("above", "belowXYZ", "five"):
        assert text_verdict(["Table 1", f"See page {words}."], path).startswith("differs")
