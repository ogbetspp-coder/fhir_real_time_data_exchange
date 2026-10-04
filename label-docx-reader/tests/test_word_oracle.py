"""The reader draws the labels and marks, and reads the fields, as Microsoft Word does.

The answers are in corpus/*/word.json.

Word's answers were recorded by ``scripts/word_oracle.py record``; this holds the reader to them
without Word. Where the reader reads a document, every list label and every footnote and endnote
mark must be Word's. Where it refuses, the refusal must be one listed here, with the reason
Word's answer is not taken.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import word_oracle
from label_docx import word as word_module
from label_docx.reader import DocxRefusedError, read_document
from label_docx.word import (
    _has_computed_fields,
    _has_stories,
    _label_fonts,
    _mark_fields,
    _mark_paragraphs,
    _set_page_numbers_aside,
    _unasked,
    emphasis_verdict,
    field_verdict,
    judge,
    label_as_drawn,
    note_text_verdict,
    note_verdict,
    print_verdict,
    reader_labels,
    reader_note_marks,
    story_verdict,
    text_verdict,
    verdict,
    word_stories,
)
from test_headers_comments import COMMENT, _commented, _document, header, reference
from test_reader import W, _field, _marked, document_xml, docx, p, r

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


def _cases() -> list[tuple[Path, list[str], list[str | None], list[int] | None]]:
    out: list[tuple[Path, list[str], list[str | None], list[int] | None]] = []
    for record in RECORDS:
        labels, fonts = _record(record)["drawn"], _record(record)["fonts"]
        # Where each label is, on record since Word was asked for it (None: in order only).
        at = _record(record).get("at", {})
        out += [
            (record.parent / name, word, fonts[name], at.get(name))
            for name, word in sorted(labels.items())
        ]
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
    ("path", "word", "fonts", "at"), _cases(), ids=lambda value: getattr(value, "stem", "")
)
def test_the_reader_draws_words_labels_or_refuses_as_listed(
    path: Path, word: list[str], fonts: list[str | None], at: list[int] | None
) -> None:
    key = f"{path.parent.name}/{path.name}"
    result = verdict(word, reader_labels(path), fonts, at)
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
    # In any other font: as stored, so the reader's mapping must match it.
    assert label_as_drawn("\u00a7", "Arial") == label_as_drawn("\u00a7", None) == "\u00a7"
    assert verdict(["\u00a7\t"], {0: "\u25aa\t"}, ["Wingdings"]) == "agrees"
    assert verdict(["\u00a7\t"], {0: "\u25aa\t"}, ["Arial"]).startswith("differs")


def test_a_code_no_table_holds_in_a_symbol_font_is_never_agreement() -> None:
    # Word draws "c." in Symbol as chi and a period, "l" in Wingdings as a disc: a reader that
    # read them as text has misjudged the font, which is what Word's answer is there to catch.
    assert label_as_drawn("\uf0d8", "Wingdings") is None
    for label, font in (("c.\t", "Symbol"), ("iii.", "Symbol"), ("l\t", "Wingdings")):
        assert label_as_drawn(label, font) is None
        assert verdict([label], {0: label}, [font]).startswith("differs")
    assert verdict(["a.\t"], {0: "a.\t"}, ["Symbol"]).startswith("differs")
    # Two fonts named for one label: which one draws it is not known.
    assert verdict(["1.\t"], {0: "1.\t"}, ["mixed"]).startswith("differs")
    assert verdict(["\t"], {0: "\t"}, ["mixed"]) == "agrees"


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
    assert _label_fonts(original, saved, ["\uf0a7\t", "1."]) == (["Wingdings", "mixed"], [0, 1])
    with pytest.raises(SystemExit):
        _label_fonts(original, saved, ["\uf0a7\t"])  # not the labels Word drew
    with pytest.raises(SystemExit):
        _label_fonts(original, docx("<w:p><w:r><w:t>one</w:t></w:r></w:p>"), [])  # one less
    # Word's empty paragraph after a closing table is no paragraph of the document's.
    body = "<w:p><w:r><w:t>one</w:t></w:r></w:p><w:p><w:r><w:t>two</w:t></w:r></w:p>"
    assert _label_fonts(original, docx(body + "<w:p/>"), []) == ([], [])
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


def test_a_paragraph_partly_so_that_word_finds_wholly_so_is_a_difference(tmp_path: Path) -> None:
    path = tmp_path / "a.docx"
    path.write_bytes(docx(p(r("<w:rPr><w:b/></w:rPr><w:t>Hello</w:t>") + r("<w:t>world</w:t>"))))
    # Word's false is "not wholly bold", which a paragraph partly bold is; its true is not.
    assert emphasis_verdict({"0": [False] * 4}, path) == "agrees"
    assert emphasis_verdict({"0": [True, False, False, False]}, path).startswith(
        "differs at paragraph 1: Word bold True"
    )


def test_which_headers_are_shown_is_held_both_ways_to_words_page_setup(tmp_path: Path) -> None:
    # A first page's header with no titlePg: the reader finds it never shown.
    body = p(
        r("<w:t>Body</w:t>"),
        f"<w:sectPr>{reference('header', 'h1')}{reference('header', 'h2', 'first')}</w:sectPr>",
    )
    parts = {
        "header1.xml": header(p(r("<w:t>Product</w:t>"))),
        "header2.xml": header(p(r("<w:t>Warning</w:t>"))),
    }
    path = tmp_path / "a.docx"
    path.write_bytes(
        _document(body, parts, [("h1", "header", "header1.xml"), ("h2", "header", "header2.xml")])
    )
    (first,) = [h for h in read_document(path.read_bytes()).headers if h.refusal]
    assert first.refusal is not None and first.refusal[0] == "never-shown"
    stories = [
        ["header", 0, "default", "Product\r", []],
        ["header", 0, "first", "Warning\r", []],
        ["header", 0, "even", "\r", []],
    ]

    def judged(first_page: bool) -> str:
        word = {"stories": stories, "comments": [], "setups": [[0, first_page, False]]}
        return story_verdict(word, path)

    assert judged(first_page=False) == "agrees"
    # Word shows the first page's own header, which the reader finds never shown.
    assert judged(first_page=True) == "differs: Word shows a header first in section 1"
    # Not asked which are shown: not agreement.
    assert story_verdict({"stories": stories, "comments": []}, path).startswith("differs")
    # A header the reader reads, of a type Word does not show there.
    titled = body.replace("<w:sectPr>", "<w:sectPr><w:titlePg/>")
    path.write_bytes(
        _document(titled, parts, [("h1", "header", "header1.xml"), ("h2", "header", "header2.xml")])
    )
    assert judged(first_page=False) == "differs: Word never shows the header first of section 1"
    assert judged(first_page=True) == "agrees"


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


def test_a_label_on_another_paragraph_than_words_is_not_agreement() -> None:
    # Word's label on the first paragraph, the reader's on the second: the same sequence.
    assert verdict(["1.\t"], {1: "1.\t"}, [None], [0]).startswith("differs at list item 1")
    assert verdict(["1.\t"], {1: "1.\t"}, [None], [1]) == "agrees"
    # A record made before Word was asked where: the labels in order only.
    assert verdict(["1.\t"], {1: "1.\t"}, [None]) == "agrees"


def test_a_label_word_shows_otherwise_than_its_copy_holds_gets_no_font(tmp_path: Path) -> None:
    # A character style on the paragraph mark: Word's text shows "A)" and U+F0B7 where its copy
    # holds "a)" and U+00B7 with no font named. Recorded, but no table vouches for either.
    original = tmp_path / "a.docx"
    original.write_bytes(docx(p(r("<w:t>one</w:t>")) + p(r("<w:t>two</w:t>"))))
    saved = docx(
        p(r("<w:t>a)</w:t><w:tab/>") + r("<w:t>one</w:t>"))
        + p(r("<w:t>\u00b7</w:t><w:tab/>") + r("<w:t>two</w:t>"))
    )
    assert _label_fonts(original, saved, ["A)\t", "\uf0b7\t"]) == ([None, None], [0, 1])
    with pytest.raises(SystemExit):
        _label_fonts(original, saved, ["B)\t", "\uf0b7\t"])
    with pytest.raises(SystemExit):
        _label_fonts(original, saved, ["A)\t"])
    assert verdict(["A)\t"], {0: "a)\t"}, [None], [0]).startswith("differs")


def _answers(**changes: Any) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "drawn": [],
        "fonts": [],
        "at": [],
        "text": None,
        "notes": None,
        "fields": None,
        "prints": True,
        "emphasis": {},
        "stories": None,
    }
    return answers | changes


def test_what_word_was_not_asked_about_is_not_agreement(tmp_path: Path) -> None:
    path = tmp_path / "a.docx"
    note = (
        '<w:footnote w:id="1"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t>N</w:t></w:r></w:p>'
        "</w:footnote>"
    )
    path.write_bytes(
        docx(p(r("<w:t>Take 5 mg</w:t>") + r('<w:footnoteReference w:id="1"/>')), footnotes=note)
    )
    assert judge(path, _answers(text=["Take 5 mg\x02"])) == (
        "differs: Word was not asked about note marks, note text"
    )
    assert judge(path, _answers()).startswith("differs: Word was not asked about the text")
    # A header named by a relationship in single quotes: still a header.
    body = p(r("<w:t>Body</w:t>"), f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>")
    data = _document(body, {"header1.xml": header(p(r("<w:t>Product</w:t>")))}, [])
    rels = (
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        "<Relationship Id='h1' Type='http://schemas.openxmlformats.org/officeDocument/2006/"
        "relationships/header' Target='header1.xml'/></Relationships>"
    )
    path.write_bytes(_replaced(data, "word/_rels/document.xml.rels", rels))
    assert read_document(path.read_bytes()).headers
    assert _has_stories(path)
    answers = _answers(text=["Body"], emphasis={"0": [False] * 4})
    assert judge(path, answers) == "differs: Word was not asked about headers, footers and comments"
    shown = [["header", 0, "default", "Product\r", []]]
    told = {"stories": shown, "comments": [], "setups": [[0, False, False]]}
    assert judge(path, answers | {"stories": told}) == "agrees"


def _replaced(data: bytes, name: str, content: str) -> bytes:
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, content if info.filename == name else source.read(info))
    return out.getvalue()


def test_every_comment_read_is_one_of_words_and_word_has_no_more(tmp_path: Path) -> None:
    hidden = COMMENT.replace('w:id="0"', 'w:id="1"').replace(
        "<w:t>Check this dose.</w:t>", "<w:rPr><w:vanish/></w:rPr><w:t>x</w:t>"
    )
    body = p(r('<w:commentReference w:id="0"/>') + r('<w:commentReference w:id="1"/>'))
    path = tmp_path / "a.docx"
    path.write_bytes(_commented(body, COMMENT + hidden))
    assert [c.refusal is not None for c in read_document(path.read_bytes()).comments] == [
        False,
        True,
    ]

    def judged(*comments: list[str]) -> str:
        word = {"stories": [], "comments": [list(c) for c in comments], "setups": []}
        return story_verdict(word, path)

    assert judged(["Reviewer", "Check this dose.\r"], ["Reviewer", "x\r"]) == "agrees"
    # One refused, the one read wrong, or Word's count otherwise: never agreement.
    assert judged(["Reviewer", "Check this dose twice.\r"], ["Reviewer", "x\r"]).startswith(
        "differs"
    )
    assert judged(["Someone else", "Check this dose.\r"], ["Reviewer", "x\r"]).startswith("differs")
    assert judged().startswith("differs")
    assert judged(["Reviewer", "Check this dose.\r"]).startswith("differs")
    # Paragraphs are paragraphs: Word's two are not the reader's one.
    assert judged(["Reviewer", "Check this\rdose.\r"], ["Reviewer", "x\r"]).startswith("differs")


def test_words_story_answer_is_parsed_whole_and_its_own_codes_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = p(r("<w:t>Body</w:t>"), f"<w:sectPr>{reference('header', 'h1')}</w:sectPr>")
    hyphenated = p(r("<w:t>Co</w:t><w:noBreakHyphen/><w:t>amoxiclav</w:t>"))
    path = tmp_path / "a.docx"
    path.write_bytes(
        _document(body, {"header1.xml": header(hyphenated)}, [("h1", "header", "header1.xml")])
    )
    monkeypatch.setattr(word_module, "CONTAINER", tmp_path / "container")
    answer = "setup\x1c0\x1cfalse\x1cfalse\x1b"
    answer += "header\x1c0\x1cdefault\x1cCo\x1eamoxiclav\r\x1c\x1b"
    answer += "comment\x1cReviewer\x1cAmoxi\x1fcillin\r\x1b"

    def word_answers(text: str) -> None:
        done = subprocess.CompletedProcess(["osascript"], 0, text + "\n", "")
        monkeypatch.setattr(word_module, "_osascript", lambda _command, _script: done)

    word_answers(answer)
    stories = word_stories(path)
    assert stories == {
        "stories": [["header", 0, "default", "Co\x1eamoxiclav\r", []]],
        "comments": [["Reviewer", "Amoxi\x1fcillin\r"]],
        "setups": [[0, False, False]],
    }
    assert story_verdict(stories | {"comments": []}, path) == "agrees"
    broken_answers = (
        "header\x1c0\x1cdefault\x1b",
        "comment\x1cA\x1cB\x1cC\x1b",
        "setup\x1c0\x1cyes\x1cfalse\x1b",
    )
    for broken in broken_answers:
        word_answers(broken)
        with pytest.raises(SystemExit):
            word_stories(path)


def test_fields_are_marked_element_by_element_or_not_at_all() -> None:
    def fld(kind: str) -> str:
        return f'<w:fldChar w:fldCharType="{kind}"/>'

    def code(text: str) -> str:
        return f'<w:instrText xml:space="preserve"> {text} </w:instrText>'

    # A run that ends one field and begins the next: no marker can go between them.
    shared = p(
        r(fld("begin"))
        + r(code("PAGE"))
        + r(fld("separate"))
        + r("<w:t>1</w:t>")
        + r(fld("end") + fld("begin"))
        + r(code("SEQ Table"))
        + r(fld("separate"))
        + r("<w:t>1</w:t>")
        + r(fld("end"))
    )
    for marking in (_set_page_numbers_aside, _mark_fields):
        with pytest.raises(SystemExit):
            marking(document_xml(shared))
    # An empty simple field is one field: what follows it is not inside it.
    xml = document_xml(
        p('<w:fldSimple w:instr="PAGE"/>')
        + p(r("<w:t>Para two</w:t>"))
        + p('<w:fldSimple w:instr="SEQ Table">' + r("<w:t>1</w:t>") + "</w:fldSimple>")
    )
    aside = _set_page_numbers_aside(xml)
    assert aside.count("@@P@@") == 1
    assert aside.index("@@/P@@") < aside.index("Para two")
    marked = _mark_fields(xml)
    assert marked.count("@@F@@") == 2
    assert marked.index("@@/@@") < marked.index("Para two") < marked.rindex("@@F@@")
    # A field Python's XML parser finds and the scan does not (another prefix): not marked.
    other = xml.replace("<w:fldSimple", "<v:fldSimple").replace("</w:fldSimple>", "</v:fldSimple>")
    other = other.replace("<w:document ", f'<w:document xmlns:v="{W}" ')
    with pytest.raises(SystemExit):
        _mark_fields(other)


def test_a_document_named_like_one_open_in_word_is_not_asked_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    open_count = "1"

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        stdout = open_count if command[1] == "-" and len(command) == 3 else "done"
        return subprocess.CompletedProcess(command, 0, stdout + "\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    command = ["osascript", "-", "/tmp/x/SmPC, v2.docx", "SmPC, v2.docx"]
    with pytest.raises(SystemExit):
        word_module._run_alone(command, "script")
    # The name goes to Word whole, and Word compares it: no list of names is split here.
    assert calls == [["osascript", "-", "SmPC, v2.docx"]]
    open_count = "0"
    assert word_module._run_alone(command, "script").stdout == "done\n"


def test_every_paragraph_is_marked_after_its_properties_or_none_is() -> None:
    xml = document_xml(
        '<w:p>\n  <w:pPr><w:pStyle w:val="x"/></w:pPr><w:r><w:t>a</w:t></w:r></w:p>'
        "<w:p><w:pPr/></w:p><w:p/>"
    )
    marked = _mark_paragraphs(xml)
    assert marked.index("@@Q0@@") > marked.index("</w:pPr>")
    assert marked.index("@@Q1@@") > marked.index("<w:pPr/>")
    assert "@@Q2@@" in marked
    other = xml.replace("<w:p/>", "<v:p/>").replace("<w:document ", f'<w:document xmlns:v="{W}" ')
    with pytest.raises(SystemExit):
        _mark_paragraphs(other)


def test_words_note_marks_and_fields_are_on_record_for_every_document_with_them() -> None:
    for record in RECORDS:
        answers = _record(record)
        for path in sorted(record.parent.glob("*.docx")):
            if _has_computed_fields(path):
                assert path.name in answers["fields"], path.name
            try:
                document = read_document(path.read_bytes())
            except DocxRefusedError:
                continue  # a refusal reads no notes
            if any(paragraph.notes for paragraph in document.body):
                assert path.name in answers["notes"], path.name


@pytest.mark.parametrize("record", RECORDS, ids=lambda path: path.parent.name)
def test_words_answers_are_for_the_bytes_of_the_files_on_record(record: Path) -> None:
    answers = _record(record)
    if "sha256" not in answers:
        pytest.skip(f"{record.parent.name}: recorded before digests; run word_oracle.py record")
    present = {path.name: path for path in record.parent.glob("*.docx")}
    assert set(answers["sha256"]) == set(present)
    for name, digest in answers["sha256"].items():
        assert hashlib.sha256(present[name].read_bytes()).hexdigest() == digest, name


def _stand_in_answers(path: Path) -> dict[str, Any]:
    return _answers(text=[], emphasis={}, asked=path.name)


def test_a_recording_reuses_an_answer_only_for_the_same_bytes_word_and_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("a.docx", "b.docx", "c.docx"):
        (tmp_path / name).write_bytes(docx(p(r(f"<w:t>{name}</w:t>"))))
    digest = {
        n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
        for n in ("a.docx", "b.docx", "c.docx")
    }
    on_record = {"application": "Word 1", "verifier": word_module.VERIFIER}
    progress = {
        "a.docx": _answers(asked="kept") | on_record | {"sha256": digest["a.docx"]},
        "b.docx": _answers(asked="kept") | on_record | {"sha256": "0" * 64},  # other bytes
        "c.docx": _answers(asked="kept")
        | on_record
        | {"sha256": digest["c.docx"], "verifier": "old"},
    }
    (tmp_path / ".word-progress.json").write_text(json.dumps(progress), "utf-8")
    asked: list[str] = []

    def ask(path: Path) -> dict[str, Any]:
        asked.append(path.name)
        return _stand_in_answers(path)

    monkeypatch.setattr(word_oracle, "ask", ask)
    monkeypatch.setattr(word_oracle, "judge", lambda _path, _answers: "agrees")
    monkeypatch.setattr(word_oracle, "word_version", lambda: "Word 1")
    monkeypatch.setattr(sys, "argv", ["word_oracle.py", "record", str(tmp_path)])
    assert word_oracle.main() == 0
    assert asked == ["b.docx", "c.docx"]
    assert "a.docx: Word's answers on record reused" in capsys.readouterr().out
    written = json.loads((tmp_path / "word.json").read_text("utf-8"))
    assert written["sha256"] == digest
    assert written["verifier"] == word_module.VERIFIER
    assert not (tmp_path / ".word-progress.json").exists()
    # --only naming no file of the set is refused, not taken as nothing to ask.
    monkeypatch.setattr(
        sys, "argv", ["word_oracle.py", "record", str(tmp_path), "--only", "tmp/a.docx"]
    )
    with pytest.raises(SystemExit):
        word_oracle.main()


def test_a_comparison_word_did_not_judge_whole_does_not_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.docx"
    path.write_bytes(docx(p(r("<w:t>x</w:t>"))))

    def fails(_path: Path) -> dict[str, Any]:
        raise SystemExit("a.docx: Word failed")

    monkeypatch.setattr(word_oracle, "ask", fails)
    monkeypatch.setattr(sys, "argv", ["word_oracle.py", "compare", str(path)])
    assert word_oracle.main() == 2


@pytest.mark.parametrize(
    ("pieces", "asked"),
    [
        ((" SEQ Table ",), True),
        ((" seq Table ",), True),
        ((" SE", "Q Table "), True),
        ((" PAGE ",), False),
    ],
)
def test_word_is_asked_about_a_field_however_its_code_is_spelt_or_split(
    tmp_path: Path, pieces: tuple[str, ...], asked: bool
) -> None:
    code = "".join(r(f'<w:instrText xml:space="preserve">{c}</w:instrText>') for c in pieces)
    body = p(
        r('<w:fldChar w:fldCharType="begin"/>')
        + code
        + r('<w:fldChar w:fldCharType="separate"/>')
        + r("<w:t>1</w:t>")
        + r('<w:fldChar w:fldCharType="end"/>')
    )
    path = tmp_path / "a.docx"
    path.write_bytes(docx(body))
    assert _has_computed_fields(path) is asked


def test_each_notes_text_is_held_to_words() -> None:
    path = CORPUS / "numbering-cases" / "notes-continuous.docx"
    word = {"footnote": ["\x02 note 1\n", "\x02 note 2\n", "\x02 note 3\n"], "endnote": []}
    assert note_text_verdict(word, path) == "agrees"
    for wrong in (
        {"footnote": ["\x02 note 1\n", "\x02 note X\n", "\x02 note 3\n"], "endnote": []},
        {"footnote": [" note 1\n", "\x02 note 2\n", "\x02 note 3\n"], "endnote": []},  # no echo
        {"footnote": ["\x02 note 1\n", "\x02 note 2\n"], "endnote": []},
    ):
        assert note_text_verdict(wrong, path).startswith("differs")
    # A document with notes whose text Word was not asked about is not agreement.
    answers = json.loads((CORPUS / "numbering-cases" / "word.json").read_text("utf-8"))
    kept = {k: answers[k].get(path.name) for k in ("text", "notes", "fields", "stories")}
    assert "note text" in _unasked(path, {**kept, "noteText": None})
