"""Zone A reads labels through the label reader's certified reads only (``zone_a.certified``).

A failing comparison names a file, never the text: each is computed before it is asserted.
"""

from __future__ import annotations

from pathlib import Path

import label_docx.epi_output
import label_docx.output
import pytest
from label_docx import read_docx as reader_read_docx
from label_docx.epi import EpiRefusedError
from label_docx.epi import read_epi as reader_read_epi
from label_docx.reader import DocxRefusedError

from zone_a import certified

ROOT = Path(__file__).resolve().parents[2]
QRD_SOURCES = sorted((ROOT / "qrd" / "sources").glob("*.docx"))
EPI_SOURCES = sorted((ROOT / "labels" / "ema-epi" / "sources").glob("*.json"))
# An EMA cover page whose body refers to a footnote.
NOTED = (
    "qrd-appendix-iii-quality-review-documents-templates-human-medicinal-products-"
    "cover-page_en.docx"
)


@pytest.mark.parametrize("path", QRD_SOURCES, ids=lambda path: path.name)
def test_a_certified_docx_read_is_the_readers_own_paragraphs(path: Path) -> None:
    data = path.read_bytes()
    same = certified.read_docx(data) == reader_read_docx(data)
    assert same, path.name


@pytest.mark.parametrize("path", EPI_SOURCES, ids=lambda path: path.name)
def test_a_certified_epi_read_is_the_readers_own_document(path: Path) -> None:
    data = path.read_bytes()
    same = certified.read_epi(data) == reader_read_epi(data)
    assert same, path.name


def test_what_the_check_cannot_certify_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Unaccounted:
        def __init__(self, data: bytes) -> None:
            pass

        def certify(self, _value: object) -> object:
            raise ValueError("a character not in the source")

    monkeypatch.setattr(label_docx.output, "DocxSource", Unaccounted)
    with pytest.raises(DocxRefusedError) as refused:
        certified.read_docx(QRD_SOURCES[0].read_bytes())
    assert refused.value.code == "uncertified"


def test_what_the_check_cannot_certify_in_an_epi_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Unaccounted:
        def __init__(self, data: bytes) -> None:
            pass

        def certify(self, _value: object) -> object:
            raise ValueError("a character not in the source")

    monkeypatch.setattr(label_docx.epi_output, "EpiSource", Unaccounted)
    with pytest.raises(EpiRefusedError) as refused:
        certified.read_epi(EPI_SOURCES[0].read_bytes())
    assert refused.value.code == "uncertified"


def test_a_body_that_refers_to_a_note_is_refused() -> None:
    # The reader reads the note; nothing in Zone A reads its text, so the body is not taken
    # without it.
    data = (ROOT / "label-docx-reader" / "corpus" / "ema-templates" / NOTED).read_bytes()
    assert any(paragraph.notes for paragraph in reader_read_docx(data))
    with pytest.raises(DocxRefusedError) as refused:
        certified.read_docx(data)
    assert refused.value.code == "note-reference"


def test_a_refusal_is_the_readers() -> None:
    with pytest.raises(DocxRefusedError) as docx:
        certified.read_docx(b"not a zip")
    assert docx.value.code == "invalid-package"
    with pytest.raises(EpiRefusedError) as epi:
        certified.read_epi(b"{}")
    assert epi.value.code == "invalid-bundle"


def test_a_tracked_document_is_refused() -> None:
    # Two texts, every change accepted and every change rejected; Zone A takes neither.
    data = (
        ROOT / "label-docx-reader" / "corpus" / "tracked-cases" / "format-bold.docx"
    ).read_bytes()
    with pytest.raises(DocxRefusedError) as refused:
        certified.read_docx(data)
    assert refused.value.code == "tracked-change"
