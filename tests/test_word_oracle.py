"""The reader draws the list labels Microsoft Word draws (corpus/*/word.json).

Word's answers were recorded by ``scripts/word_oracle.py record``; this holds the reader to them
without Word. Where the reader reads a document, every list label must be Word's. Where it
refuses, the refusal must be one listed here, with the reason Word's answer is not taken.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from word_oracle import reader_labels, verdict

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
RECORDS = sorted(CORPUS.glob("*/word.json"))

# Documents the reader refuses although Word draws labels for them, and why.
REFUSED = {
    # A numStyleLink to an abstractNum with no styleLink back: Word draws an empty label.
    "numbering-cases/numbering-style-link-one-way.docx": "unsupported-numbering",
    # Footnotes, which the reader does not read yet (their references are refused).
    "ema-templates/qrd-appendix-iii-quality-review-documents-templates-human-medicinal-products"
    "-cover-page_en.docx": "unsupported-element",
    "ema-templates/qrd-appendix-v-adverse-drug-reaction-reporting-details_en.docx": (
        "unsupported-element"
    ),
    # EMA's stray U+F02D in Times New Roman, a code no font draws as the template means it.
    "ema-templates/qrd-product-information-template-version-104_es.docx": "private-use-character",
}


def _cases() -> list[tuple[Path, list[str]]]:
    out: list[tuple[Path, list[str]]] = []
    for record in RECORDS:
        labels = json.loads(record.read_text("utf-8"))["drawn"]
        out += [(record.parent / name, word) for name, word in sorted(labels.items())]
    return out


def test_every_corpus_document_has_words_answer() -> None:
    assert RECORDS
    for record in RECORDS:
        labels = json.loads(record.read_text("utf-8"))["drawn"]
        present = {path.name for path in record.parent.glob("*.docx")}
        assert set(labels) == present, f"{record.parent.name}: run scripts/word_oracle.py record"


@pytest.mark.parametrize(("path", "word"), _cases(), ids=lambda value: getattr(value, "stem", ""))
def test_the_reader_draws_words_labels_or_refuses_as_listed(path: Path, word: list[str]) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = verdict(word, reader_labels(path))
    if key in REFUSED:
        assert result == f"reader refuses: {REFUSED[key]}"
    else:
        assert result == "agrees"
