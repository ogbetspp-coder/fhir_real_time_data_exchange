"""The reader draws the list labels and note marks Microsoft Word draws (corpus/*/word.json).

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

from word_oracle import note_verdict, reader_labels, reader_note_marks, verdict

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
RECORDS = sorted(CORPUS.glob("*/word.json"))

# Documents the reader refuses although Word draws labels or marks for them, and why.
REFUSED = {
    # A numStyleLink to an abstractNum with no styleLink back: Word draws an empty label.
    "numbering-cases/numbering-style-link-one-way.docx": "unsupported-numbering",
    # A custom-marked footnote whose text holds footnoteRef: Word draws there the number the
    # next footnote will take, which no reference shows.
    "numbering-cases/notes-custom-mark.docx": "ambiguous-numbering",
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
