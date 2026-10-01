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
    emphasis_verdict,
    field_verdict,
    note_verdict,
    print_verdict,
    reader_labels,
    reader_note_marks,
    verdict,
)

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
RECORDS = sorted(CORPUS.glob("*/word.json"))

# Documents the reader refuses although Word draws labels or marks for them, and why.
REFUSED = {
    # A numStyleLink to an abstractNum with no styleLink back: Word draws an empty label.
    "numbering-cases/numbering-style-link-one-way.docx": "unsupported-numbering",
    # A custom-marked footnote whose text holds footnoteRef: Word draws there the number the
    # next footnote will take, which no reference shows.
    "numbering-cases/notes-custom-mark.docx": "ambiguous-numbering",
    # A REF to a bookmark that is not there: Word prints "Error! Reference source not found."
    "numbering-cases/fields-ref-missing.docx": "computed-field",
    # Captions stored as 7 and 7, which Word shows on screen and prints as 1 and 2.
    "numbering-cases/fields-stale.docx": "stale-field",
    # A cross-reference stored as other text than its bookmark's, and a note reference stored as 7
    # for note 1: Word prints the bookmark's text and the mark 1.
    "numbering-cases/fields-ref-stale.docx": "stale-field",
    "numbering-cases/fields-noteref-stale.docx": "stale-field",
    # EMA's stray U+F02D in Times New Roman, a code no font draws as the template means it.
    "ema-templates/qrd-product-information-template-version-104_es.docx": "private-use-character",
}


def _record(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return data


def _cases() -> list[tuple[Path, list[str]]]:
    out: list[tuple[Path, list[str]]] = []
    for record in RECORDS:
        labels = _record(record)["drawn"]
        out += [(record.parent / name, word) for name, word in sorted(labels.items())]
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


@pytest.mark.parametrize(("path", "word"), _cases(), ids=lambda value: getattr(value, "stem", ""))
def test_the_reader_draws_words_labels_or_refuses_as_listed(path: Path, word: list[str]) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = verdict(word, reader_labels(path))
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
