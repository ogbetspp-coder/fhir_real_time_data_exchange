"""The independent conservation check (``label_docx.certify``): it passes every result the
readers make of the corpus, and it fails every result changed in any way it does not allow.

The second half is the check's own test. For every corpus document the readers read, the
result is changed in each way a faulty reader could change it (a character dropped, added,
changed, swapped or moved; a paragraph dropped, repeated, swapped, merged or split; a page
number, note mark, table cell, title or count put elsewhere), several times over at places
chosen by a seeded generator, and every changed result must be refused. Thousands of changes,
none let through: the equality the check tests is what a result must satisfy, and no result
other than the document's does.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from label_docx import epi_output, output
from label_docx.certify import (
    CHECKED_MARKS,
    CertificationError,
    DocxSource,
    EpiSource,
    _formatted,
    certify_docx,
    certify_epi,
)
from label_docx.output import canonical
from label_docx.word import SUFFIXES, label_as_drawn
from lock import MANIFESTS
from test_reader import LINE, SHAPE, WP, W, _alternate, docx

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
# A character no document holds and no reading can produce: an inserted or substituted
# character is always one the document does not have there.
_FOREIGN = "\u2603"
# Changes of each kind per document.
_TIMES = 4


def _documents() -> list[Path]:
    return sorted(
        p
        for p in CORPUS.glob("*/[!.]*")
        if (p.suffix == ".docx" or (p.suffix == ".json" and p.stem not in MANIFESTS))
    )


def _locked() -> dict[Path, str]:
    """Each corpus document's outcome as locked (scripts/lock.py): its refusal's code, or read."""
    out: dict[Path, str] = {}
    for record in sorted(CORPUS.glob("*/expected.json")):
        for name, entry in json.loads(record.read_text("utf-8")).items():
            # A document with tracked changes is two reads, certified in tests/test_tracked.py.
            out[record.parent / name] = entry.get(
                "refusal", "tracked" if "trackedSha256" in entry else "read"
            )
    return out


LOCKED = _locked()
# The documents locked as read. Each is read once, when a test first needs it, so a test that
# needs one document does not wait for the corpus.
READ = sorted(path for path, outcome in LOCKED.items() if outcome == "read")


@functools.cache
def _read(path: Path) -> tuple[DocxSource | EpiSource | None, dict[str, Any], str]:
    """The source as the check reads it (None if refused), the result, and the outcome."""
    data = path.read_bytes()
    if path.suffix == ".docx":
        value = json.loads(output.read(data)[0])
        if "tracked" in value:
            return None, value, "tracked"
        source: DocxSource | EpiSource | None = None if "refusal" in value else DocxSource(data)
    else:
        value = json.loads(epi_output.read(data)[0])
        source = None if "refusal" in value else EpiSource(data)
    return source, value, value["refusal"]["code"] if "refusal" in value else "read"


def _certified(path: Path) -> tuple[DocxSource | EpiSource, dict[str, Any]]:
    """A document locked as read, read: its refusal now is a failure, never a skip."""
    source, value, outcome = _read(path)
    assert source is not None, f"{path.name} is locked as read but refused: {outcome}"
    return source, value


def test_every_corpus_document_is_read_or_refused_as_locked() -> None:
    # A check that refuses what it should certify is caught here: the documents certified are
    # exactly those locked as read (scripts/lock.py), and each refusal is the one locked. The
    # smallest first, and the first that differs fails the test, so a fault shows quickly.
    documents = _documents()
    assert set(documents) == set(LOCKED)
    for path in sorted(documents, key=lambda p: (p.stat().st_size, p)):
        assert _read(path)[2] == LOCKED[path], path.name


def test_the_corpus_is_read_widely_enough_to_test_the_check() -> None:
    assert {p.suffix for p in READ} == {".docx", ".json"}
    assert len(READ) >= 180


@pytest.mark.parametrize("path", READ, ids=[p.name for p in READ])
def test_every_result_the_readers_make_is_certified(path: Path) -> None:
    source, value = _certified(path)
    certificate = source.certify(value)
    if path.suffix == ".docx":
        source_count = sum(certificate["source"].values())
        kept = certificate["output"]["characters"] + sum(certificate["setAside"].values())
        assert source_count == kept
    else:
        assert certificate["output"]["characters"] == (
            certificate["source"]["characters"]
            - certificate["source"]["whitespace"]
            + certificate["output"]["spaces"]
            + certificate["source"]["breaks"]
            - certificate["setAside"]["closingBreaks"]
            + certificate["source"]["pictures"]
        )
    # The same result, checked again, gets the same certificate.
    assert source.certify(value) == certificate
    # And every count in it is the one locked for this document (scripts/lock.py).
    locked = json.loads((path.parent / "expected.json").read_text("utf-8"))[path.name]
    digest = hashlib.sha256(canonical(certificate)).hexdigest()
    assert digest == locked["certificateSha256"], f"{path.name}: run scripts/lock.py and review"


# --- the check's own test: every change is refused ----------------------------------------

Change = Callable[[dict[str, Any], random.Random], bool]


def _paragraphs(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Every paragraph of a result: the body and notes of a .docx, or every ePI section's."""
    if "sections" not in value:
        parts = ("footnotes", "endnotes", "headers", "footers", "comments")
        notes = [p for kind in parts for n in value[kind] for p in n["paragraphs"]]
        return [*value["paragraphs"], *notes]

    def walk(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [p for s in sections for p in [*s["paragraphs"], *walk(s["sections"])]]

    return walk(value["sections"])


def _lists(value: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """The lists paragraphs stand in, so a paragraph can be dropped, repeated or moved."""
    if "sections" not in value:
        parts = ("footnotes", "endnotes", "headers", "footers", "comments")
        notes = [n["paragraphs"] for kind in parts for n in value[kind]]
        return [value["paragraphs"], *notes]

    def walk(sections: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        return [x for s in sections for x in [s["paragraphs"], *walk(s["sections"])]]

    return [x for x in walk(value["sections"]) if x]


def _texted(value: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    found = [p for p in _paragraphs(value) if p["text"]]
    return rng.choice(found) if found else None


def _drop_character(value: dict[str, Any], rng: random.Random) -> bool:
    paragraph = _texted(value, rng)
    if paragraph is None:
        return False
    at = rng.randrange(len(paragraph["text"]))
    paragraph["text"] = paragraph["text"][:at] + paragraph["text"][at + 1 :]
    return True


def _add_character(value: dict[str, Any], rng: random.Random) -> bool:
    paragraph = rng.choice(_paragraphs(value)) if _paragraphs(value) else None
    if paragraph is None:
        return False
    at = rng.randrange(len(paragraph["text"]) + 1)
    paragraph["text"] = paragraph["text"][:at] + _FOREIGN + paragraph["text"][at:]
    return True


def _change_character(value: dict[str, Any], rng: random.Random) -> bool:
    paragraph = _texted(value, rng)
    if paragraph is None:
        return False
    at = rng.randrange(len(paragraph["text"]))
    paragraph["text"] = paragraph["text"][:at] + _FOREIGN + paragraph["text"][at + 1 :]
    return True


def _repeat_character(value: dict[str, Any], rng: random.Random) -> bool:
    paragraph = _texted(value, rng)
    if paragraph is None:
        return False
    at = rng.randrange(len(paragraph["text"]))
    text = paragraph["text"]
    paragraph["text"] = text[: at + 1] + text[at] + text[at + 1 :]
    return True


def _swap_characters(value: dict[str, Any], rng: random.Random) -> bool:
    places = [
        (p, i)
        for p in _paragraphs(value)
        for i in range(len(p["text"]) - 1)
        if p["text"][i] != p["text"][i + 1]
    ]
    if not places:
        return False
    paragraph, at = rng.choice(places)
    text = paragraph["text"]
    paragraph["text"] = text[:at] + text[at + 1] + text[at] + text[at + 2 :]
    return True


def _move_character(value: dict[str, Any], rng: random.Random) -> bool:
    paragraphs = _paragraphs(value)
    source = _texted(value, rng)
    if source is None or len(paragraphs) < 2:
        return False
    target = rng.choice([p for p in paragraphs if p is not source])
    character, source["text"] = source["text"][-1], source["text"][:-1]
    target["text"] = character + target["text"]
    return True


def _drop_paragraph(value: dict[str, Any], rng: random.Random) -> bool:
    lists = [x for x in _lists(value) if x]
    if not lists:
        return False
    found = rng.choice(lists)
    del found[rng.randrange(len(found))]
    return True


def _repeat_paragraph(value: dict[str, Any], rng: random.Random) -> bool:
    lists = [x for x in _lists(value) if x]
    if not lists:
        return False
    found = rng.choice(lists)
    at = rng.randrange(len(found))
    found.insert(at, copy.deepcopy(found[at]))
    return True


def _swap_paragraphs(value: dict[str, Any], rng: random.Random) -> bool:
    places = [
        (x, i) for x in _lists(value) for i in range(len(x) - 1) if x[i]["text"] != x[i + 1]["text"]
    ]
    if not places:
        return False
    found, at = rng.choice(places)
    found[at], found[at + 1] = found[at + 1], found[at]
    return True


def _merge_paragraphs(value: dict[str, Any], rng: random.Random) -> bool:
    places = [(x, i) for x in _lists(value) for i in range(len(x) - 1)]
    if not places:
        return False
    found, at = rng.choice(places)
    found[at]["text"] += found[at + 1]["text"]
    del found[at + 1]
    return True


def _split_paragraph(value: dict[str, Any], rng: random.Random) -> bool:
    places = [(x, i) for x in _lists(value) for i in range(len(x)) if len(x[i]["text"]) > 1]
    if not places:
        return False
    found, at = rng.choice(places)
    cut = rng.randrange(1, len(found[at]["text"]))
    second = copy.deepcopy(found[at])
    second["text"], found[at]["text"] = found[at]["text"][cut:], found[at]["text"][:cut]
    second["pages"], second["notes"] = [], []
    found.insert(at + 1, second)
    return True


def _move_page_number(value: dict[str, Any], rng: random.Random) -> bool:
    found = [p for p in _paragraphs(value) if p.get("pages")]
    if not found:
        return False
    paragraph = rng.choice(found)
    paragraph["pages"][0] += 1 if paragraph["pages"][0] < len(paragraph["text"]) else -1
    return True


def _move_note_mark(value: dict[str, Any], rng: random.Random) -> bool:
    found = [p for p in _paragraphs(value) if p.get("notes")]
    if not found:
        return False
    note = rng.choice(found)["notes"][0]
    if rng.random() < 0.5:
        note["id"] += 1
    else:
        note["offset"] += 1
    return True


def _move_table_cell(value: dict[str, Any], rng: random.Random) -> bool:
    found = [p for p in _paragraphs(value) if p.get("table")]
    if not found:
        return False
    rng.choice(found)["table"][2] += 1
    return True


def _change_title(value: dict[str, Any], rng: random.Random) -> bool:
    if "sections" not in value:
        return False
    sections = value["sections"]
    rng.choice(sections)["title"] += _FOREIGN
    return True


def _hide_a_refusal(value: dict[str, Any], rng: random.Random) -> bool:
    # A section, header or footer emptied and called refused, with the count left as it was: a
    # loss the receipt would not show.
    if "sections" not in value:
        parts = [s for kind in ("headers", "footers") for s in value[kind] if s["paragraphs"]]
        if not parts:
            return False
        part = rng.choice(parts)
        part["paragraphs"], part["refusal"] = [], {"code": "x", "detail": "x"}
        return True
    found = [s for s in value["sections"] if s["paragraphs"]]
    if not found:
        return False
    section = rng.choice(found)
    section["paragraphs"], section["refusal"] = [], {"code": "x", "detail": "x"}
    return True


def _change_a_mark(value: dict[str, Any], rng: random.Random) -> bool:
    # A Word document's bold, italic, capitals, strike, super- or subscript or underline added
    # where Word shows none, or taken away where it shows one, or moved by a character.
    if "sections" in value:
        return False
    paragraph = _texted(value, rng)
    if paragraph is None:
        return False
    checked = sorted(CHECKED_MARKS)
    own = [m for m in paragraph["marks"] if m["kind"] in checked]
    roll = rng.random()
    if own and roll < 0.4:
        paragraph["marks"].remove(rng.choice(own))
    elif own and roll < 0.6:
        mark = rng.choice(own)
        if mark["end"] < len(paragraph["text"]):
            mark["end"] += 1
        else:
            mark["start"] = max(0, mark["start"] - 1) if mark["start"] else mark["start"] + 1
            if mark["start"] >= mark["end"]:
                paragraph["marks"].remove(mark)
    else:
        at = rng.randrange(len(paragraph["text"]))
        present = {m["kind"] for m in own if m["start"] <= at < m["end"]}
        missing = [k for k in checked if k not in present]
        paragraph["marks"].append({"start": at, "end": at + 1, "kind": rng.choice(missing)})
    return True


def _change_a_label(value: dict[str, Any], rng: random.Random) -> bool:
    # A list label drawn otherwise: another number, its suffix, or a label where none is drawn.
    if "sections" in value:
        return False
    labelled = [p for p in value["paragraphs"] if p["numbering"] and p["numbering"]["text"]]
    if labelled and rng.random() < 0.8:
        numbering = rng.choice(labelled)["numbering"]
        if rng.random() < 0.7:
            numbering["text"] = numbering["text"] + "1"
        else:
            numbering["suffix"] = "space" if numbering["suffix"] != "space" else "tab"
        return True
    plain = [p for p in value["paragraphs"] if p["numbering"] is None]
    if not plain:
        return False
    rng.choice(plain)["numbering"] = {"level": 0, "numId": 1, "suffix": "tab", "text": "1."}
    return True


def _change_a_note_mark(value: dict[str, Any], rng: random.Random) -> bool:
    if "sections" in value:
        return False
    marked = [n for p in _paragraphs(value) for n in p["notes"]]
    if not marked:
        return False
    note = rng.choice(marked)
    note["mark"] = "9" if note["mark"] != "9" else "8"
    return True


def _change_a_notes_own_mark(value: dict[str, Any], rng: random.Random) -> bool:
    if "sections" in value:
        return False
    notes = [n for kind in ("footnotes", "endnotes") for n in value[kind] if "mark" in n]
    if not notes:
        return False
    note = rng.choice(notes)
    note["mark"] = "9" if note["mark"] != "9" else "8"
    return True


CHANGES: list[Change] = [
    _drop_character,
    _add_character,
    _change_character,
    _repeat_character,
    _swap_characters,
    _move_character,
    _drop_paragraph,
    _repeat_paragraph,
    _swap_paragraphs,
    _merge_paragraphs,
    _split_paragraph,
    _move_page_number,
    _move_note_mark,
    _move_table_cell,
    _change_title,
    _hide_a_refusal,
    _change_a_mark,
    _change_a_label,
    _change_a_note_mark,
    _change_a_notes_own_mark,
]


@pytest.mark.parametrize("path", READ, ids=[p.name for p in READ])
def test_every_change_to_a_result_is_refused(path: Path) -> None:
    source, value = _certified(path)
    rng = random.Random(path.name)
    tried = 0
    for change in CHANGES:
        for _ in range(_TIMES):
            changed = copy.deepcopy(value)
            if not change(changed, rng):
                break
            tried += 1
            with pytest.raises(CertificationError):
                source.certify(changed)
    assert tried >= 5 * _TIMES


# --- what the check allows, and the rules it reads by -----------------------------------------


def _docx_value(data: bytes) -> dict[str, Any]:
    value = json.loads(output.read(data)[0])
    assert "refusal" not in value, value.get("refusal")
    return value  # type: ignore[no-any-return]


def _p(inner: str) -> str:
    return f"<w:p>{inner}</w:p>"


def test_field_code_is_set_aside_and_its_result_kept() -> None:
    body = _p(
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> DOCPROPERTY Title </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Shown</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    data = docx(body)
    certificate = DocxSource(data).certify(_docx_value(data))
    assert certificate["setAside"]["fieldCode"] == len(" DOCPROPERTY Title ")
    assert certificate["output"]["characters"] == len("Shown")


def test_a_page_number_is_set_aside_and_must_be_placed() -> None:
    body = _p(
        "<w:r><w:t>Intro</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>12</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    data = docx(body)
    source, value = DocxSource(data), _docx_value(data)
    assert source.certify(value)["setAside"]["pageNumbers"] == 2
    value["paragraphs"][0]["pages"] = []
    with pytest.raises(CertificationError):
        source.certify(value)


def test_a_page_break_is_set_aside_and_a_line_break_kept() -> None:
    data = docx(_p('<w:r><w:t>a</w:t><w:br w:type="page"/><w:t>b</w:t><w:br/><w:t>c</w:t></w:r>'))
    certificate = DocxSource(data).certify(_docx_value(data))
    assert certificate["setAside"]["pageBreaks"] == 1
    assert certificate["output"]["characters"] == len("ab\nc")


def _symbol_case(run_properties: str, styles: str | None = None, theme: str | None = None) -> bytes:
    body = _p(f"<w:r><w:rPr>{run_properties}</w:rPr><w:t>\u00b3</w:t></w:r>")
    return docx(body, styles=styles, minor_font=theme)


def _only(data: bytes, text: str) -> int:
    """The Symbol count when ``text`` is the one reading the check takes; every other refused."""
    source, value = DocxSource(data), _docx_value(data)
    assert value["paragraphs"][0]["text"] == text
    count: int = source.certify(value)["symbolMapped"]
    for other in {"\u00b3", "\u2265"} - {text}:
        value["paragraphs"][0]["text"] = other
        with pytest.raises(CertificationError):
            source.certify(value)
    return count


def test_a_symbol_run_is_read_through_the_symbol_table_and_only_so() -> None:
    data = _symbol_case('<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>')
    assert _only(data, "\u2265") == 1


def test_text_in_another_font_is_read_as_stored_and_only_so() -> None:
    assert _only(docx(_p("<w:r><w:t>\u00b3</w:t></w:r>")), "\u00b3") == 0


def test_the_font_is_the_nearest_levels_by_word_precedence() -> None:
    symbol_style = (
        '<w:style w:type="paragraph" w:styleId="Sym"><w:rPr>'
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:style>'
    )
    # From the paragraph's style.
    styled = docx(
        _p('<w:pPr><w:pStyle w:val="Sym"/></w:pPr><w:r><w:t>\u00b3</w:t></w:r>'),
        styles=symbol_style,
    )
    assert _only(styled, "\u2265") == 1
    # The run's own font is nearer than its paragraph style's.
    overridden = docx(
        _p(
            '<w:pPr><w:pStyle w:val="Sym"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" '
            'w:hAnsi="Arial"/></w:rPr><w:t>\u00b3</w:t></w:r>'
        ),
        styles=symbol_style,
    )
    assert _only(overridden, "\u00b3") == 0
    # Through the theme: the run names the theme's minor font, which is Symbol.
    themed = _symbol_case(
        '<w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/>', theme="Symbol"
    )
    assert _only(themed, "\u2265") == 1


def test_symbol_in_only_some_slots_or_outside_the_table_is_never_certified() -> None:
    with pytest.raises(CertificationError):
        DocxSource(_symbol_case('<w:rFonts w:ascii="Symbol" w:hAnsi="Arial"/>'))
    unmapped = _p(
        '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>\u4e00</w:t></w:r>'
    )
    with pytest.raises(CertificationError):
        DocxSource(docx(unmapped))


def test_hidden_whitespace_is_left_out_and_counted_and_nothing_else() -> None:
    body = _p(
        '<w:r><w:t xml:space="preserve">a</w:t></w:r>'
        '<w:r><w:rPr><w:vanish/></w:rPr><w:t xml:space="preserve">  </w:t></w:r>'
        "<w:r><w:t>b</w:t></w:r>"
    )
    data = docx(body)
    source, value = DocxSource(data), _docx_value(data)
    assert value["paragraphs"][0]["text"] == "ab"
    assert source.certify(value)["setAside"]["hiddenWhitespace"] == 2
    for kept in ("a b", "a  b"):
        value["paragraphs"][0]["text"] = kept
        with pytest.raises(CertificationError):
            source.certify(value)
    # Visible whitespace may never be left out.
    plain = docx(_p('<w:r><w:t xml:space="preserve">a  b</w:t></w:r>'))
    plain_value = _docx_value(plain)
    plain_value["paragraphs"][0]["text"] = "ab"
    with pytest.raises(CertificationError):
        DocxSource(plain).certify(plain_value)


def test_hidden_is_the_runs_own_setting_first_then_any_style() -> None:
    hiding = '<w:style w:type="character" w:styleId="H"><w:rPr><w:vanish/></w:rPr></w:style>'
    # Hidden by its character style.
    styled = docx(
        _p(
            '<w:r><w:t>a</w:t></w:r><w:r><w:rPr><w:rStyle w:val="H"/></w:rPr>'
            '<w:t xml:space="preserve"> </w:t></w:r>'
        ),
        styles=hiding,
    )
    source, value = DocxSource(styled), _docx_value(styled)
    assert value["paragraphs"][0]["text"] == "a"
    assert source.certify(value)["setAside"]["hiddenWhitespace"] == 1
    # The run says it is shown: the space must be kept.
    shown = docx(
        _p(
            '<w:r><w:t>a</w:t></w:r><w:r><w:rPr><w:rStyle w:val="H"/><w:vanish w:val="0"/>'
            '</w:rPr><w:t xml:space="preserve"> </w:t></w:r>'
        ),
        styles=hiding,
    )
    shown_value = _docx_value(shown)
    assert shown_value["paragraphs"][0]["text"] == "a "
    DocxSource(shown).certify(shown_value)
    shown_value["paragraphs"][0]["text"] = "a"
    with pytest.raises(CertificationError):
        DocxSource(shown).certify(shown_value)
    # Hidden text with characters to show is never certified (the reader refuses it).
    with pytest.raises(CertificationError):
        DocxSource(docx(_p("<w:r><w:rPr><w:vanish/></w:rPr><w:t>secret</w:t></w:r>")))


def test_note_marks_and_notes_must_be_the_documents() -> None:
    body = _p('<w:r><w:t>Text</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r>')
    notes = (
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="1"><w:p><w:r><w:footnoteRef/></w:r>'
        '<w:r><w:t xml:space="preserve"> Note text.</w:t></w:r></w:p></w:footnote>'
    )
    data = docx(body, footnotes=notes)
    source, value = DocxSource(data), _docx_value(data)
    source.certify(value)
    changed = copy.deepcopy(value)
    changed["footnotes"][0]["paragraphs"][0]["text"] = " Note text"
    with pytest.raises(CertificationError):
        source.certify(changed)
    changed = copy.deepcopy(value)
    changed["footnotes"] = []
    with pytest.raises(CertificationError):
        source.certify(changed)


def test_text_the_reader_does_not_read_is_listed_with_its_size() -> None:
    data = docx(_p("<w:r><w:t>Body</w:t></w:r>"))
    # A header part the body does not read, added beside the document.
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr(
            "word/header1.xml",
            f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>Head</w:t></w:r></w:p></w:hdr>',
        )
    certificate = DocxSource(out.getvalue()).certify(_docx_value(data))
    assert certificate["notRead"] == {"word/header1.xml": 4}


def test_an_epi_section_keeps_every_character_and_collapses_whitespace_only() -> None:
    div = (
        '<div xmlns="http://www.w3.org/1999/xhtml"><p>  Store \n below 25&#160;\u00b0C. </p>'
        '<p>a<br/>b<br/></p><p><img src="x"/> x</p></div>'
    )
    composition = {"resourceType": "Composition", "title": "T"}
    composition["section"] = [{"title": "S", "text": {"div": div}}]  # type: ignore[assignment]
    bundle = {"resourceType": "Bundle", "type": "document", "entry": [{"resource": composition}]}
    data = json.dumps(bundle).encode()
    value = json.loads(epi_output.read(data)[0])
    source = EpiSource(data)
    certificate = source.certify(value)
    assert [p["text"] for p in value["sections"][0]["paragraphs"]] == [
        "Store below 25\u00a0\u00b0C.",
        "a\nb",
        "\ufffc x",
    ]
    assert certificate["setAside"]["closingBreaks"] == 1
    # A no-break space is text, never collapsible whitespace.
    value["sections"][0]["paragraphs"][0]["text"] = "Store below 25 \u00b0C."
    with pytest.raises(CertificationError):
        source.certify(value)


def test_a_picture_holding_text_is_never_one_character() -> None:
    body = _p("<w:r><w:t>a</w:t></w:r>")
    data = docx(body)
    value = _docx_value(data)
    # WordArt keeps its text in an attribute; the check refuses to stand it for one character.
    wordart = docx(
        _p(
            '<w:r><w:t>a</w:t><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
            '<v:textpath string="DANGER"/></v:shape></w:pict></w:r>'
        )
    )
    value["paragraphs"][0]["text"] = "a\ufffc"
    with pytest.raises(CertificationError):
        DocxSource(wordart).certify(value)


# --- the check's refusals, each on its own ----------------------------------------------------


def _with_part(data: bytes, name: str, content: str | bytes) -> bytes:
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            if info.filename != name:
                target.writestr(info, source.read(info))
        target.writestr(name, content)
    return out.getvalue()


_VML = 'xmlns:v="urn:schemas-microsoft-com:vml"'


@pytest.mark.parametrize(
    "body",
    [
        "<w:t>stray</w:t>",
        _p(
            f"<w:r><w:pict><v:shape {_VML}><w:p><w:r><w:t>x</w:t></w:r></w:p>"
            "</v:shape></w:pict></w:r>"
        ),
        _p("<w:pPr><w:t>x</w:t></w:pPr>"),
        _p("<w:hyperlink><w:t>x</w:t></w:hyperlink>"),
        _p("<w:r><w:footnoteRef/></w:r>"),
        _p("<w:r><w:t>a<w:b/></w:t></w:r>"),
        _p('<w:r><w:sym w:font="Symbol" w:char="F001"/></w:r>'),
        _p("<w:r><w:pgNum/></w:r>"),
        _p('<w:r><w:fldChar w:fldCharType="separate"/></w:r>'),
    ],
    ids=[
        "text-outside-a-paragraph",
        "paragraph-in-a-paragraph",
        "text-in-paragraph-properties",
        "text-outside-a-run",
        "note-echo-outside-a-note",
        "element-in-text",
        "symbol-outside-the-table",
        "unknown-run-content",
        "field-character-out-of-place",
    ],
)
def test_a_source_the_check_cannot_read_token_by_token_is_never_certified(body: str) -> None:
    with pytest.raises(CertificationError):
        DocxSource(docx(body))


def test_every_token_kind_and_simple_fields_are_accounted_for() -> None:
    body = _p(
        "<w:r><w:t>a</w:t><w:cr/><w:t>b</w:t><w:softHyphen/><w:t>c</w:t></w:r>"
        '<w:fldSimple w:instr=" PAGE "><w:r><w:t>7</w:t></w:r></w:fldSimple>'
        '<w:fldSimple w:instr=" DOCPROPERTY Title "><w:r><w:t>T</w:t></w:r></w:fldSimple>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:t xml:space="preserve"> PAGE </w:t>'
        '</w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>8</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    data = docx(body)
    value = _docx_value(data)
    certificate = certify_docx(data, value)
    assert value["paragraphs"][0]["text"] == "a\nb\u00adcT"
    assert certificate["setAside"]["pageNumbers"] == 2
    assert certificate["setAside"]["fieldCode"] == len(" PAGE ")


def test_a_package_the_check_cannot_open_is_never_certified() -> None:
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(CertificationError):
        DocxSource(out.getvalue())
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="r" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"/>')
    with pytest.raises(CertificationError):
        DocxSource(out.getvalue())
    data = docx(_p("<w:r><w:t>x</w:t></w:r>"))
    with pytest.raises(CertificationError):
        DocxSource(_with_part(data, "word/header1.xml", "<not well-formed"))


def test_a_continuation_notice_is_listed_as_not_read() -> None:
    body = _p('<w:r><w:t>Text</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r>')
    notes = (
        '<w:footnote w:type="continuationNotice" w:id="0"><w:p><w:r><w:t>(continued)</w:t>'
        '</w:r></w:p></w:footnote><w:footnote w:id="1"><w:p><w:r><w:footnoteRef/></w:r>'
        "<w:r><w:t>N</w:t></w:r></w:p></w:footnote>"
    )
    data = docx(body, footnotes=notes)
    certificate = DocxSource(data).certify(_docx_value(data))
    assert certificate["notRead"] == {"word/footnotes.xml#continuationNotice": len("(continued)")}


def test_an_epi_result_of_another_shape_is_never_certified() -> None:
    data = (CORPUS / "ema-epi" / "jentadueto-smpc-en.json").read_bytes()
    value = json.loads(epi_output.read(data)[0])
    assert certify_epi(data, value)["checker"] == value["certificate"]["checker"]
    source = EpiSource(data)
    fewer = copy.deepcopy(value)
    fewer["sections"].pop()
    nested = copy.deepcopy(value)
    nested["sections"][0]["sections"].append(copy.deepcopy(nested["sections"][0]))
    claimed = copy.deepcopy(value)
    texted = next(s for s in claimed["sections"][0]["sections"] if s["paragraphs"])
    texted["refusal"] = {"code": "x", "detail": "x"}
    claimed["refusedSections"] += 1
    for changed in (fewer, nested, claimed):
        with pytest.raises(CertificationError):
            source.certify(changed)
    with pytest.raises(CertificationError):
        EpiSource(json.dumps({"entry": []}).encode())


# --- each rule of the check, held so that a fault in it shows (scripts/mutate_checker.py) ------


def _value(*paragraphs: str | dict[str, Any], **notes: list[dict[str, Any]]) -> dict[str, Any]:
    """A result of the given paragraphs: text alone, or text with its pages, notes or cell."""
    out = []
    for paragraph in paragraphs:
        base: dict[str, Any] = {
            "comments": [],
            "numbering": None,
            "marks": [],
            "pages": [],
            "notes": [],
            "table": None,
        }
        out.append(
            {**base, "text": paragraph} if isinstance(paragraph, str) else {**base, **paragraph}
        )
    return {
        "paragraphs": out,
        "footnotes": notes.get("footnotes", []),
        "endnotes": [],
        "headers": [],
        "footers": [],
        "comments": [],
        "refusedParts": 0,
    }


def _field(code: str, result: str) -> str:
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        f"<w:r><w:t>{result}</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


@pytest.mark.parametrize("code", ["PAGEREF _Toc1 \\h", "PAGE", "NUMPAGES", "SECTIONPAGES"])
def test_each_page_number_field_is_set_aside_and_placed(code: str) -> None:
    data = docx(_p("<w:r><w:t>p</w:t></w:r>" + _field(code, "12")))
    certificate = DocxSource(data).certify(_value({"text": "p", "pages": [1]}))
    assert certificate["setAside"]["pageNumbers"] == 2
    with pytest.raises(CertificationError):
        DocxSource(data).certify(_value("p12"))


def test_a_field_in_another_fields_code_is_code() -> None:
    nested = (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> IF </w:instrText></w:r>'
        + _field("PAGE", "3")
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>x</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    certificate = DocxSource(docx(_p(nested))).certify(_value("x"))
    assert certificate["setAside"] == {
        "fieldCode": len(" IF ") + len(" PAGE ") + 1,
        "floatingObjects": 0,
        "hiddenWhitespace": 0,
        "pageBreaks": 0,
        "pageNumbers": 0,
    }


@pytest.mark.parametrize(
    "run",
    [
        '<w:instrText xml:space="preserve"> PAGE </w:instrText>',
        '<w:fldChar w:fldCharType="end"/>',
        '<w:fldChar w:fldCharType="begin"/><w:fldChar w:fldCharType="other"/>',
        '<w:fldChar w:fldCharType="begin"/><w:fldChar w:fldCharType="separate"/>'
        "<w:instrText>X</w:instrText>",
        '<w:sym w:font="Symbol"/>',
    ],
    ids=[
        "code-outside-a-field",
        "end-alone",
        "unknown-kind",
        "code-in-a-result",
        "sym-without-code",
    ],
)
def test_field_characters_out_of_place_are_never_certified(run: str) -> None:
    with pytest.raises(CertificationError):
        DocxSource(docx(_p(f"<w:r>{run}</w:r>")))


def test_text_before_a_mark_in_the_same_run_stands_before_it() -> None:
    body = _p(
        '<w:r><w:t>a</w:t><w:footnoteReference w:id="1"/><w:t>b</w:t></w:r>' + _field("PAGE", "4")
    )
    notes = '<w:footnote w:id="1"><w:p><w:r><w:t>n</w:t></w:r></w:p></w:footnote>'
    source = DocxSource(docx(body, footnotes=notes))
    note = {
        "id": 1,
        "mark": "1",
        "paragraphs": [
            {
                "text": "n",
                "pages": [],
                "notes": [],
                "table": None,
                "comments": [],
                "marks": [],
                "numbering": None,
            }
        ],
    }
    marked = {
        "text": "ab",
        "pages": [2],
        "notes": [{"offset": 1, "kind": "footnote", "id": 1, "mark": "1"}],
    }
    source.certify(_value(marked, footnotes=[note]))
    moved = {**marked, "notes": [{"offset": 0, "kind": "footnote", "id": 1, "mark": "1"}]}
    with pytest.raises(CertificationError):
        source.certify(_value(moved, footnotes=[note]))
    run_with_field = _p(
        '<w:r><w:t>a</w:t><w:fldChar w:fldCharType="begin"/><w:instrText> PAGE </w:instrText>'
        '<w:fldChar w:fldCharType="separate"/><w:t>9</w:t><w:fldChar w:fldCharType="end"/></w:r>'
    )
    DocxSource(docx(run_with_field)).certify(_value({"text": "a", "pages": [1]}))


def test_every_character_element_stands_for_its_character() -> None:
    body = _p(
        "<w:r><w:t>a</w:t><w:ptab/><w:tab/><w:noBreakHyphen/><w:softHyphen/>"
        '<w:br w:type="column"/><w:br w:type="page"/><w:br/><w:cr/>'
        "<w:lastRenderedPageBreak/>"
        '<w:sym w:font="Symbol" w:char="F0B3"/><w:t>b</w:t></w:r>'
    )
    certificate = DocxSource(docx(body)).certify(_value("a\t\t\u2011\u00ad\n\n\u2265b"))
    assert certificate["setAside"]["pageBreaks"] == 2
    assert certificate["source"] == {"elements": 9, "instructionCharacters": 0, "textCharacters": 2}
    assert certificate["symbolMapped"] == 1
    # A tab element in a field's code is code: one token set aside.
    tab_in_code = _p(
        '<w:r><w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve"> PAGE '
        '</w:instrText><w:tab/><w:fldChar w:fldCharType="separate"/><w:t>1</w:t>'
        '<w:fldChar w:fldCharType="end"/></w:r>'
    )
    certificate = DocxSource(docx(tab_in_code)).certify(_value({"text": "", "pages": [0]}))
    assert certificate["setAside"]["fieldCode"] == len(" PAGE ") + 1
    assert certificate["setAside"]["pageNumbers"] == 1


_DRAWING = (
    '<w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
    'wordprocessingDrawing">{inner}</wp:inline></w:drawing>'
)


@pytest.mark.parametrize("inner", ["", "<w:t>x</w:t>", "<w:txbx/>", "<w:txbxContent/>"])
def test_a_drawing_is_one_character_unless_it_holds_text(inner: str) -> None:
    data = docx(_p("<w:r>" + _DRAWING.format(inner=inner) + "</w:r>"))
    if not inner:
        assert DocxSource(data).certify(_value("\ufffc"))["source"]["elements"] == 1
        return
    with pytest.raises(CertificationError):
        DocxSource(data)


@pytest.mark.parametrize("inner", ["", "<v:textbox/>", '<v:textpath string="x"/>'])
def test_a_vml_picture_is_one_character_unless_it_holds_text(inner: str) -> None:
    data = docx(_p(f"<w:r><w:pict><v:shape {_VML}><v:imagedata/>{inner}</v:shape></w:pict></w:r>"))
    if not inner:
        DocxSource(data).certify(_value("\ufffc"))
        return
    with pytest.raises(CertificationError):
        DocxSource(data)


@pytest.mark.parametrize(
    "fallback",
    [
        LINE,
        "<w:t>x</w:t>",
        "<w:sym w:font='Symbol' w:char='F0B7'/>",
        "<w:tab/>",
        f"<w:pict><v:shape {_VML}><v:textbox/></v:shape></w:pict>",
        _alternate(SHAPE),
    ],
)
def test_alternate_content_is_read_as_its_drawing_unless_a_branch_holds_text_or_run_content(
    fallback: str,
) -> None:
    data = docx(_p("<w:r>" + _alternate(SHAPE, fallback) + "</w:r>"))
    if fallback == LINE:
        # Anchored: set aside, as Word's text shows it.
        assert DocxSource(data).certify(_value(""))["setAside"]["floatingObjects"] == 1
        return
    with pytest.raises(CertificationError):
        DocxSource(data)


def test_a_floating_drawing_is_set_aside_and_counted_and_one_in_line_is_one_character() -> None:
    inline = _DRAWING.format(inner="")
    anchored = inline.replace("wp:inline", "wp:anchor")
    vml = f'<w:pict><v:shape {_VML} style="{{}}width:9pt"><v:imagedata/></v:shape></w:pict>'
    shape = _alternate(SHAPE.replace("wp:anchor", "wp:inline"), LINE)
    for drawing, in_line in (
        (inline, True),
        (anchored, False),
        (vml.format(""), True),
        (vml.format("position: ABSOLUTE;"), False),
        (shape, True),
    ):
        source = DocxSource(docx(_p(f"<w:r><w:t>a</w:t>{drawing}<w:t>b</w:t></w:r>")))
        right, wrong = ("a￼b", "ab") if in_line else ("ab", "a￼b")
        assert source.certify(_value(right))["setAside"]["floatingObjects"] == (not in_line)
        with pytest.raises(CertificationError):
            source.certify(_value(wrong))
    for drawing in (
        inline.replace("</wp:inline>", f'</wp:inline><wp:anchor xmlns:wp="{WP}"/>'),
        vml.format("position:relative;"),
        vml.format("").replace("</w:pict>", f"<v:shape {_VML}><v:imagedata/></v:shape></w:pict>"),
        _alternate(SHAPE, LINE, requires="wpg"),
    ):
        with pytest.raises(CertificationError):
            DocxSource(docx(_p(f"<w:r>{drawing}</w:r>")))


def test_paragraphs_in_block_containers_are_read_and_one_in_a_paragraph_is_not() -> None:
    body = (
        "<w:sdt><w:sdtContent>" + _p("<w:r><w:t>a</w:t></w:r>") + "</w:sdtContent></w:sdt>"
        "<w:customXml>" + _p("<w:r><w:t>b</w:t></w:r>") + "</w:customXml>"
    )
    DocxSource(docx(body)).certify(_value("a", "b"))
    inside = _p("<w:sdt><w:sdtContent>" + _p("<w:r><w:t>x</w:t></w:r>") + "</w:sdtContent></w:sdt>")
    with pytest.raises(CertificationError):
        DocxSource(docx(inside))


def _cell(inner: str) -> str:
    return f"<w:tc>{inner}</w:tc>"


def test_cells_are_counted_in_their_own_table_and_row() -> None:
    nested = (
        "<w:tbl><w:tr>" + _cell(_p("<w:r><w:t>n</w:t></w:r>")) + _cell(_p("")) + "</w:tr></w:tbl>"
    )
    body = (
        "<w:tbl><w:tr>"
        + _cell(_p("<w:r><w:t>a</w:t></w:r>") + nested + _p(""))
        + "<w:customXml>"
        + _cell(_p("<w:r><w:t>b</w:t></w:r>"))
        + "</w:customXml>"
        + "</w:tr><w:sdt><w:sdtContent><w:tr>"
        + _cell(_p("<w:r><w:t>c</w:t></w:r>"))
        + "</w:tr></w:sdtContent></w:sdt></w:tbl>"
        + _p("<w:r><w:t>d</w:t></w:r>")
    )
    cells = [
        ("a", [0, 0, 0]),
        ("n", [0, 0, 0]),
        ("", [0, 0, 0]),
        ("", [0, 0, 0]),
        ("b", [0, 0, 1]),
        ("c", [0, 1, 0]),
        ("d", None),
    ]
    source = DocxSource(docx(body))
    source.certify(_value(*({"text": t, "table": c} for t, c in cells)))
    for index in (1, 4, 5):
        moved = [list(c) if c else c for _, c in cells]
        moved[index][2 if index != 5 else 1] += 1  # type: ignore[index]
        with pytest.raises(CertificationError):
            source.certify(
                _value(*({"text": t, "table": c} for (t, _), c in zip(cells, moved, strict=True)))
            )


# Fonts and hidden text: each level and setting on its own.

_SYMBOL = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>'


def _styles(*styles: str, defaults: str = "") -> str:
    return (
        f"<w:docDefaults><w:rPrDefault>{defaults}</w:rPrDefault></w:docDefaults>"
        if defaults
        else ""
    ) + "".join(styles)


def _mapped(data: bytes) -> bool:
    """Whether the check reads the paragraph's "\u00b3" through the Symbol table, not as stored."""
    source = DocxSource(data)
    try:
        source.certify(_value("\u2265"))
    except CertificationError:
        source.certify(_value("\u00b3"))
        return False
    return True


def _plain(paragraph_properties: str = "", run_properties: str = "") -> str:
    return _p(
        f"<w:pPr>{paragraph_properties}</w:pPr><w:r><w:rPr>{run_properties}</w:rPr><w:t>\u00b3</w:t></w:r>"
    )


@pytest.mark.parametrize("flag", ["1", "true", "on"])
def test_a_style_marked_default_in_any_spelling_is_the_default(flag: str) -> None:
    style = f'<w:style w:type="paragraph" w:default="{flag}" w:styleId="D">{_SYMBOL}</w:style>'
    assert _mapped(docx(_plain(), styles=style))
    not_default = style.replace(f'w:default="{flag}"', 'w:default="0"')
    assert not _mapped(docx(_plain(), styles=not_default))


def test_each_kind_of_style_and_the_defaults_give_the_font() -> None:
    # A style with no type is a paragraph style; one based on another takes its font.
    untyped = f'<w:style w:default="1" w:styleId="U">{_SYMBOL}</w:style>'
    assert _mapped(docx(_plain(), styles=untyped))
    based = (
        f'<w:style w:type="paragraph" w:styleId="A">{_SYMBOL}</w:style>'
        '<w:style w:type="paragraph" w:styleId="B"><w:basedOn w:val="A"/></w:style>'
    )
    assert _mapped(docx(_plain('<w:pStyle w:val="B"/>'), styles=based))
    # An unknown paragraph style is the default one.
    default = f'<w:style w:type="paragraph" w:default="1" w:styleId="D">{_SYMBOL}</w:style>'
    assert _mapped(docx(_plain('<w:pStyle w:val="Missing"/>'), styles=default))
    # The document defaults; never the default character style, which Word does not apply.
    character = f'<w:style w:type="character" w:default="1" w:styleId="C">{_SYMBOL}</w:style>'
    assert not _mapped(docx(_plain(), styles=character))
    defaults = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>'
    assert _mapped(docx(_plain(), styles=_styles(defaults=defaults)))
    # A basedOn loop ends.
    loop = (
        '<w:style w:type="paragraph" w:styleId="A"><w:basedOn w:val="B"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="B"><w:basedOn w:val="A"/></w:style>'
    )
    assert not _mapped(docx(_plain('<w:pStyle w:val="A"/>'), styles=loop))


def test_a_table_style_gives_its_font_inside_its_table_only() -> None:
    table_style = f'<w:style w:type="table" w:default="1" w:styleId="T">{_SYMBOL}</w:style>'
    run = "<w:r><w:t>\u00b3</w:t></w:r>"
    body = "<w:tbl><w:tr>" + _cell(_p(run)) + "</w:tr></w:tbl>" + _p(run)
    source = DocxSource(docx(body, styles=table_style))
    source.certify(_value({"text": "\u2265", "table": [0, 0, 0]}, "\u00b3"))
    named = table_style.replace('w:default="1" ', "")
    body = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr><w:tr>'
        + _cell(_p(run))
        + "</w:tr></w:tbl>"
    )
    DocxSource(docx(body, styles=named)).certify(_value({"text": "\u2265", "table": [0, 0, 0]}))


@pytest.mark.parametrize("name", ["symbol", "SymbolMT", "Symbol MT", "Sym bol"])
def test_the_symbol_font_by_its_exact_name_only(name: str) -> None:
    assert _mapped(docx(_plain(run_properties='<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>')))
    # Another spelling, for a run or a w:sym, is not on record: never read either way.
    with pytest.raises(CertificationError):
        DocxSource(docx(_plain(run_properties=f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}"/>')))
    with pytest.raises(CertificationError):
        DocxSource(docx(_p(f'<w:r><w:sym w:font="{name}" w:char="F061"/></w:r>')))


def test_symbol_text_stored_in_the_private_range_is_mapped() -> None:
    run = f"<w:r>{_SYMBOL}<w:t>\uf0b3</w:t></w:r>"
    DocxSource(docx(_p(run))).certify(_value("\u2265"))


_FULL_THEME = (
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements>'
    "<a:fontScheme><a:majorFont>{major}</a:majorFont><a:minorFont>{minor}</a:minorFont>"
    "</a:fontScheme></a:themeElements></a:theme>"
)


def _themed(rfonts: str, major: str = "", minor: str = "") -> bytes:
    data = docx(_plain(run_properties=rfonts), minor_font="Calibri")
    return _with_part(data, "word/theme/theme1.xml", _FULL_THEME.format(major=major, minor=minor))


def test_theme_fonts_by_slot_script_and_major_or_minor() -> None:
    symbol = '<a:latin typeface="Symbol"/>'
    plain = '<a:latin typeface="Arial"/><a:ea typeface="Arial"/><a:cs typeface="Arial"/>'
    for theme in ("majorHAnsi", "majorAscii"):
        both = f'<w:rFonts w:asciiTheme="{theme}" w:hAnsiTheme="{theme}"/>'
        assert _mapped(_themed(both, major=symbol, minor=plain))
        assert not _mapped(_themed(both, major=plain, minor=symbol))
    for slot, theme, script in (("eastAsia", "minorEastAsia", "ea"), ("cs", "minorBidi", "cs")):
        attribute = "cstheme" if slot == "cs" else f"{slot}Theme"
        one = f'<w:rFonts w:{attribute}="{theme}"/>'
        with pytest.raises(CertificationError):
            DocxSource(_themed(one, minor=f'<a:{script} typeface="Symbol"/>'))
        assert not _mapped(_themed(one, minor=f'<a:{script} typeface="Arial"/>'))
    with pytest.raises(CertificationError):
        DocxSource(_themed('<w:rFonts w:asciiTheme="minorHAnsi"/>'))


@pytest.mark.parametrize(
    ("value", "hidden"),
    [("1", True), ("true", True), ("on", True), ("0", False), ("false", False), ("off", False)],
)
def test_hidden_by_every_spelling_of_on_and_off(value: str, hidden: bool) -> None:
    body = _p(
        f'<w:r><w:t>a</w:t></w:r><w:r><w:rPr><w:vanish w:val="{value}"/></w:rPr>'
        '<w:t xml:space="preserve"> </w:t></w:r>'
    )
    DocxSource(docx(body)).certify(_value("a" if hidden else "a "))


# The package: relationships as a package writes them.


def _relationships(data: bytes, name: str, extra: str, first: bool = True) -> bytes:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rels = archive.read(name).decode()
    at = (
        rels.index(">", rels.index("<Relationships")) + 1
        if first
        else rels.index("</Relationships>")
    )
    return _with_part(data, name, rels[:at] + extra + rels[at:])


def test_external_relationships_are_not_parts_and_absolute_targets_are() -> None:
    styles = f'<w:style w:type="paragraph" w:default="1" w:styleId="D">{_SYMBOL}</w:style>'
    data = docx(_plain(), styles=styles)
    external = (
        '<Relationship Id="x" TargetMode="External" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/styles" Target="http://example.org/styles.xml"/>'
    )
    assert _mapped(_relationships(data, "word/_rels/document.xml.rels", external))
    root = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="/word/document.xml"/></Relationships>'
    )
    assert _mapped(_with_part(data, "_rels/.rels", root))


# The ePI: HTML's blocks, breaks and the Bundle around them.


def _epi(*divs: str, entries: list[dict[str, Any]] | None = None, text: str | None = None) -> bytes:
    composition: dict[str, Any] = {"resourceType": "Composition", "title": "T"}
    composition["section"] = [
        {"title": f"S{i}", "text": {"div": f'<div xmlns="http://www.w3.org/1999/xhtml">{d}</div>'}}
        for i, d in enumerate(divs)
    ]
    if text is not None:
        composition["text"] = {"div": text}
    bundle = {
        "resourceType": "Bundle",
        "type": "document",
        "entry": [*(entries or []), {"resource": composition}],
    }
    return json.dumps(bundle).encode()


def test_every_block_element_ends_a_paragraph() -> None:
    headings = "".join(f'<h{n} style="font-size: 12pt">h{n}</h{n}>x{n}' for n in range(1, 7))
    div = (
        "a<div>b</div>c<ul>u<li>l</li></ul>o<ol>v<li>m</li></ol>"
        "<table><thead><tr><th>t</th></tr></thead><tbody><tr><td>d</td></tr></tbody></table>"
        f"e<hr/>f{headings}<p>g<br/> h</p>"
    )
    data = _epi(div)
    value = json.loads(epi_output.read(data)[0])
    assert "refusal" not in value
    assert value["sections"][0]["refusal"] is None
    texts = [p["text"] for p in value["sections"][0]["paragraphs"]]
    assert texts == [
        "a",
        "b",
        "c",
        "u",
        "l",
        "o",
        "v",
        "m",
        "t",
        "d",
        "e",
        "f",
        "h1",
        "x1",
        "h2",
        "x2",
        "h3",
        "x3",
        "h4",
        "x4",
        "h5",
        "x5",
        "h6",
        "x6",
        "g\nh",
    ]
    assert [p["table"] for p in value["sections"][0]["paragraphs"]][8:10] == [[0, 0, 0], [0, 1, 0]]
    EpiSource(data).certify(value)


def test_the_composition_is_found_among_the_entries_and_other_narratives_are_listed() -> None:
    other = {"resource": {"resourceType": "Organization", "text": {"div": "<div>EMA</div>"}}}
    data = _epi(
        "<p>a</p>", entries=[{"resource": "not a resource"}, other], text="<div>summary</div>"
    )
    value = json.loads(epi_output.read(data)[0])
    assert EpiSource(data).certify(value)["notRead"] == {"narratives": 2}


def test_east_asian_and_complex_script_fonts_set_directly_count() -> None:
    for slot in ("eastAsia", "cs"):
        with pytest.raises(CertificationError):
            DocxSource(docx(_plain(run_properties=f'<w:rFonts w:{slot}="Symbol"/>')))


def test_a_style_without_an_id_is_not_a_default() -> None:
    styles = (
        f'<w:style w:type="paragraph" w:default="1" w:styleId="D">{_SYMBOL}</w:style>'
        '<w:style w:type="paragraph" w:default="1"><w:name w:val="no id"/></w:style>'
    )
    assert _mapped(docx(_plain(), styles=styles))


@pytest.mark.parametrize(
    "body",
    [
        "<w:tbl><w:customXml><w:tbl><w:tr>"
        + "<w:tc>"
        + _p("<w:r><w:t>x</w:t></w:r>")
        + "</w:tc></w:tr></w:tbl></w:customXml></w:tbl>",
        "<w:tbl><w:tr><w:customXml><w:tr><w:tc>"
        + _p("<w:r><w:t>x</w:t></w:r>")
        + "</w:tc></w:tr></w:customXml></w:tr></w:tbl>",
    ],
    ids=["table-in-a-table-outside-its-cells", "row-in-a-row"],
)
def test_table_parts_outside_their_place_are_never_passed_over(body: str) -> None:
    with pytest.raises(CertificationError):
        DocxSource(docx(body))


# --- the second reading of the text, without an XML parser -------------------------------------


@pytest.mark.parametrize(
    ("xml", "texts"),
    [
        (
            f'<w:p xmlns:w="{W}"><w:t>a &lt;b&gt; &amp; &#8805; &#x2265;</w:t></w:p>',
            [("t", "a <b> & \u2265 \u2265")],
        ),
        (f'<x:p xmlns:x="{W}"><x:t>other prefix</x:t></x:p>', [("t", "other prefix")]),
        (
            f'<p xmlns="{W}"><t>default namespace</t><instrText> PAGE </instrText></p>',
            [("t", "default namespace"), ("instrText", " PAGE ")],
        ),
        (f'<w:p xmlns:w="{W}"><w:t>a<![CDATA[<b>&]]>c</w:t></w:p>', [("t", "a<b>&c")]),
        (f'<w:p xmlns:w="{W}"><w:t>a<!-- not text -->b<?pi x?></w:t></w:p>', [("t", "ab")]),
        (
            f'<w:p xmlns:w="{W}"><w:t>one\r\ntwo\rthree&#13;</w:t></w:p>',
            [("t", "one\ntwo\nthree\r")],
        ),
        (f'<w:p xmlns:w="{W}" a="x>y"><w:t a=\'>\'>q</w:t><w:t/></w:p>', [("t", "q"), ("t", "")]),
        (
            f'<w:p xmlns:w="{W}"><o:t xmlns:o="urn:other">not w</o:t><w:t>w</w:t></w:p>',
            [("t", "w")],
        ),
        (
            f'<?xml version="1.0"?><w:p xmlns:w="{W}"><w:r><w:t>nested</w:t></w:r></w:p>',
            [("t", "nested")],
        ),
        (
            f'<w:p xmlns:w="{W}"><w:t>&quot;quoted&quot; &apos;one&apos;</w:t></w:p>',
            [("t", "\"quoted\" 'one'")],
        ),
        # A namespace written with a reference is the namespace it spells.
        (f'<w:p xmlns:w="{W[:-1]}&#110;"><w:t>spelled</w:t></w:p>', [("t", "spelled")]),
        # A prefix bound again inside an element is bound so there only.
        (
            f'<w:p xmlns:w="{W}"><x:a xmlns:x="urn:x" xmlns:w="urn:other"><w:t>no</w:t></x:a>'
            "<w:t>yes</w:t></w:p>",
            [("t", "yes")],
        ),
    ],
    ids=[
        "references",
        "prefix",
        "default-namespace",
        "cdata",
        "comment-and-pi",
        "line-ends",
        "quoted-gt",
        "other-namespace",
        "declaration",
        "quotes",
        "namespace-reference",
        "rebound-prefix",
    ],
)
def test_the_second_reading_of_the_text_matches_the_xml_parser(
    xml: str, texts: list[tuple[str, str]]
) -> None:
    import xml.etree.ElementTree as ET

    from label_docx.certify import _raw_texts

    assert _raw_texts(xml.encode()) == texts
    parsed = [
        (node.tag.rsplit("}", 1)[-1], node.text or "")
        for node in ET.fromstring(xml.encode()).iter()
        if node.tag in (f"{{{W}}}t", f"{{{W}}}instrText")
    ]
    assert parsed == texts


@pytest.mark.parametrize(
    "xml",
    [
        f'<w:p xmlns:w="{W}"><w:t>a & b</w:t></w:p>',
        f'<!DOCTYPE p><w:p xmlns:w="{W}"/>',
        f'<w:p xmlns:w="{W}"><w:t>open',
        f'<w:p xmlns:w="{W}"><w:t a="unterminated>x</w:t></w:p>',
        f'<w:p xmlns:w="{W}"><w:r>',
        f'<!DOCTYPE x><w:p xmlns:w="{W}"><w:t>a</w:t></w:p></x>',
        f'<w:p xmlns:w="{W}"/><x a=\'q/',
    ],
    ids=[
        "bare-ampersand",
        "doctype",
        "unclosed",
        "tag-without-end",
        "unclosed-without-text",
        "declaration-closed-as-a-tag",
        "tag-cut-off-at-the-end",
    ],
)
def test_the_second_reading_stops_at_what_it_does_not_read(xml: str) -> None:
    from label_docx.certify import _raw_texts

    with pytest.raises(CertificationError):
        _raw_texts(xml.encode())


def test_a_part_whose_two_readings_differ_is_never_certified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import label_docx.certify as module

    data = docx(_p("<w:r><w:t>abc</w:t></w:r>"))
    real = module._raw_texts
    monkeypatch.setattr(
        module, "_raw_texts", lambda raw: [(k, t.replace("b", "x")) for k, t in real(raw)]
    )
    with pytest.raises(CertificationError):
        DocxSource(data)


def test_the_key_marks_from_every_level_are_worked_out_alike() -> None:
    styles = (
        "<w:docDefaults><w:rPrDefault><w:rPr><w:caps/></w:rPr></w:rPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="N"><w:rPr><w:i/></w:rPr></w:style>'
        '<w:style w:type="character" w:default="1" w:styleId="D"><w:rPr><w:b/></w:rPr></w:style>'
        '<w:style w:type="character" w:styleId="S"><w:rPr><w:vertAlign w:val="subscript"/>'
        '<w:u w:val="single"/></w:rPr></w:style>'
        '<w:style w:type="table" w:default="1" w:styleId="T"><w:rPr><w:strike/></w:rPr></w:style>'
    )
    body = (
        _p(
            '<w:r><w:rPr><w:smallCaps/><w:dstrike/><w:vertAlign w:val="superscript"/></w:rPr>'
            "<w:t>a</w:t></w:r>"
            '<w:r><w:rPr><w:rStyle w:val="S"/></w:rPr><w:t>b</w:t></w:r>'
            '<w:r><w:rPr><w:rStyle w:val="S"/><w:u w:val="none"/></w:rPr><w:t>c</w:t></w:r>'
        )
        + "<w:tbl><w:tr><w:tc>"
        + _p("<w:r><w:t>d</w:t></w:r>")
        + "</w:tc></w:tr></w:tbl>"
    )
    value = json.loads(output.read(docx(body, styles=styles))[0])
    assert "refusal" not in value, value.get("refusal")

    def at(paragraph: int, offset: int) -> set[str]:
        return {
            m["kind"]
            for m in value["paragraphs"][paragraph]["marks"]
            if m["start"] <= offset < m["end"] and m["kind"] in CHECKED_MARKS
        }

    # The default character style's bold is never applied (Word does not apply it).
    assert at(0, 0) == {"italic", "caps", "smallCaps", "dstrike", "superscript"}
    assert at(0, 1) == {"italic", "caps", "subscript", "underline"}
    assert at(0, 2) == {"italic", "caps", "subscript"}
    assert at(1, 0) == {"italic", "caps", "strike"}


def test_the_checks_own_list_labels_and_note_marks_are_words() -> None:
    # Held to Word's recorded answers directly, not only to the reader's: for every corpus
    # document read, each label and note mark the check draws is the one Word drew.
    read = {
        p: s
        for p in READ
        if p.suffix == ".docx"
        for s in [_certified(p)[0]]
        if isinstance(s, DocxSource)
    }
    values = {p: _certified(p)[1] for p in read}
    checked = 0
    for record in sorted(CORPUS.glob("*/word.json")):
        answers = json.loads(record.read_text("utf-8"))
        for name, word in sorted(answers["drawn"].items()):
            source = read.get(record.parent / name)
            if source is None:
                continue
            drawn = [
                str(label["text"] or "") + SUFFIXES[str(label["suffix"] or "nothing")]
                for label in source.labels
                if label is not None and label["numId"]
            ]
            fonts = answers["fonts"][name]
            mapped = [label_as_drawn(w, f) for w, f in zip(word, fonts, strict=True)]
            assert [d for d in drawn if d] == mapped, name
            checked += 1
        for name, word_marks in sorted(answers["notes"].items()):
            source = read.get(record.parent / name)
            if source is None:
                continue
            # Where each mark stands, in order, from the result; the mark itself from the check.
            value = values[record.parent / name]
            notes = [*value["footnotes"], *value["endnotes"]]
            body = [(n["kind"], n["id"]) for p in value["paragraphs"] for n in p["notes"]]
            echoes = [
                (n["kind"], n["id"])
                for note in notes
                for p in note["paragraphs"]
                for n in p["notes"]
            ]
            for where, places in (("body", body), ("notes", echoes)):
                ours = [source.note_marks.get(place, "?") for place in places]
                assert ours == word_marks[where], f"{name}: {where}"
            checked += 1
    assert checked >= 80


# --- the check's own list labels and note marks, setting by setting ---------------------------

_NUM = '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>'
_ARIAL = '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr>'
_SYMBOL = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>'


def _level(index: int, text: str, fmt: str = "decimal", extra: str = "") -> str:
    return (
        f'<w:lvl w:ilvl="{index}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{text}"/>{extra}</w:lvl>'
    )


def _list(*levels: str, key: int = 1) -> str:
    return f'<w:abstractNum w:abstractNumId="{key}">{"".join(levels)}</w:abstractNum>'


def _item(num_id: int, level: int, props: str = "") -> str:
    numbered = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'
    return f"<w:p><w:pPr>{props}{numbered}</w:pPr><w:r>{_ARIAL}<w:t>x</w:t></w:r></w:p>"


def _labels_certified(data: bytes) -> list[tuple[int, str | None, str | None]]:
    """The check's own labels, after the read is certified: reader and check agree on them."""
    value = json.loads(output.read(data)[0])
    assert "refusal" not in value, value.get("refusal")
    labels = [label for label in DocxSource(data).labels if label is not None]
    return [(int(str(x["numId"])), x["text"], x["suffix"]) for x in labels]


_SETTINGS = {
    # An lvlRestart Word draws empty, on a level no paragraph draws, is never in the way.
    "restart-on-a-level-not-drawn": (
        _list(_level(0, "%1."), _level(1, "%2.", extra='<w:lvlRestart w:val="1"/>')),
        [(1, "1.", "tab")],
    ),
    "suffix-space": (
        _list(_level(0, "%1.", extra='<w:suff w:val="space"/>')),
        [(1, "1.", "space")],
    ),
    "no-number-format": (
        _list('<w:lvl w:ilvl="0"><w:start w:val="3"/><w:lvlText w:val="%1."/></w:lvl>'),
        [(1, "3.", "tab")],
    ),
    "legal": (
        _list(_level(0, "%1.", "upperRoman"), _level(1, "%1.%2", "upperRoman", "<w:isLgl/>")),
        [(1, "I.", "tab"), (1, "1.1", "tab")],
    ),
    # Word keeps a decimalZero level's zero under isLgl.
    "legal-decimal-zero": (
        _list(_level(0, "%1."), _level(1, "%1.%2", "decimalZero", "<w:isLgl/>")),
        [(1, "1.", "tab"), (1, "1.01", "tab")],
    ),
    "text-of-255": (_list(_level(0, "x" * 255)), [(1, "x" * 255, "tab")]),
    **{
        f"text-null-{value}": (
            _list(
                '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
                f'<w:lvlText w:val="%1." w:null="{value}"/></w:lvl>'
            ),
            [(1, "" if value != "0" else "1.", "tab")],
        )
        for value in ("1", "true", "on", "0")
    },
    **{
        f"legacy-{value}": (
            _list(_level(0, "%1.", extra=f'<w:legacy w:legacy="{value}"/>')),
            [(1, "1.", "legacy" if value == "1" else "tab")],
        )
        for value in ("1", "0", "false", "off")
    },
}


@pytest.mark.parametrize("name", sorted(_SETTINGS))
def test_the_check_reads_every_setting_of_a_list_level(name: str) -> None:
    numbering, expected = _SETTINGS[name]
    items = "".join(_item(1, level) for level in range(len(expected)))
    assert _labels_certified(docx(items, numbering=numbering + _NUM)) == expected


def test_a_level_restarted_only_after_level_0_counts_on_after_level_1() -> None:
    numbering = _list(
        _level(0, "%1"), _level(1, "%2"), _level(2, "%3", extra='<w:lvlRestart w:val="1"/>')
    )
    items = "".join(_item(1, level) for level in (0, 1, 2, 1, 2))
    labels = _labels_certified(docx(items, numbering=numbering + _NUM))
    assert [text for _, text, _ in labels] == ["1", "1", "1", "2", "2"]


_STYLED = {
    # The list named by a paragraph style, the default paragraph style (also for a style that
    # is not defined), and a table style inside its table; a default table style outside any
    # table names none.
    "paragraph-style": ('<w:pStyle w:val="L"/>', "paragraph", "L", "", [(1, "1.", "tab")]),
    "default-style": ("", "paragraph", "N", ' w:default="1"', [(1, "1.", "tab")]),
    "undefined-style": (
        '<w:pStyle w:val="Nope"/>',
        "paragraph",
        "N",
        ' w:default="1"',
        [(1, "1.", "tab")],
    ),
    # A table style's list is not on record: never drawn.
    "table-style": ("", "table", "T", "", None),
    "default-table-style": ("", "table", "T", ' w:default="1"', None),
    "default-table-style-outside": ("", "table", "T", ' w:default="1"', []),
}


@pytest.mark.parametrize("name", sorted(_STYLED))
def test_the_check_finds_a_list_through_styles_as_word_does(name: str) -> None:
    props, kind, style, default, expected = _STYLED[name]
    numbered = '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr>'
    styles = (
        f'<w:style w:type="{kind}"{default} w:styleId="{style}"><w:name w:val="{style}"/>'
        f"{numbered}</w:style>"
    )
    paragraph = f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>x</w:t></w:r></w:p>"
    if name == "table-style":
        paragraph = (
            f'<w:tbl><w:tblPr><w:tblStyle w:val="{style}"/></w:tblPr><w:tr><w:tc>{paragraph}'
            "</w:tc></w:tr></w:tbl>"
        )
    elif name == "default-table-style":
        paragraph = f"<w:tbl><w:tr><w:tc>{paragraph}</w:tc></w:tr></w:tbl>"
    data = docx(paragraph, styles=styles, numbering=_list(_level(0, "%1.")) + _NUM)
    if expected is None:
        with pytest.raises(CertificationError):
            DocxSource(data)
    else:
        assert _labels_certified(data) == expected


@pytest.mark.parametrize("numbered", ['<w:ilvl w:val="1"/>', '<w:ilvl w:val="0"/><w:numId/>'])
def test_a_paragraph_whose_numbering_names_no_list_is_in_none(numbered: str) -> None:
    paragraph = f"<w:p><w:pPr><w:numPr>{numbered}</w:numPr></w:pPr><w:r><w:t>x</w:t></w:r></w:p>"
    data = docx(paragraph, numbering=_list(_level(0, "%1.")) + _NUM)
    assert [label[0] for label in _labels_certified(data)] == [0]


@pytest.mark.parametrize("kind", ["paragraph", "table"])
@pytest.mark.parametrize("default", [False, True], ids=["named", "default"])
def test_a_bullet_in_symbol_through_a_paragraph_or_table_style(kind: str, default: bool) -> None:
    flag = ' w:default="1"' if default else ""
    styles = f'<w:style w:type="{kind}"{flag} w:styleId="S"><w:name w:val="S"/>{_SYMBOL}</w:style>'
    named = '<w:pStyle w:val="S"/>' if kind == "paragraph" and not default else ""
    item = _item(1, 0, named)
    if kind == "table":
        table = "" if default else '<w:tblPr><w:tblStyle w:val="S"/></w:tblPr>'
        item = f"<w:tbl>{table}<w:tr><w:tc>{item}</w:tc></w:tr></w:tbl>"
    data = docx(item, styles=styles, numbering=_list(_level(0, "\uf0b7", "bullet")) + _NUM)
    assert _labels_certified(data) == [(1, "\u2022", "tab")]


_REFUSED_LISTS: dict[str, tuple[str, list[tuple[Any, ...]]]] = {
    "custom-format": (
        _list(
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal" w:format="001"/>'
            '<w:lvlText w:val="%1"/></w:lvl>'
        ),
        [(1, 0)],
    ),
    "list-not-defined": (_list(_level(0, "%1.")), [(9, 0)]),
    "level-only-in-an-override": (
        _list(_level(0, "%1."))
        + '<w:num w:numId="2"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="1">'
        + _level(1, "-")
        + "</w:lvlOverride></w:num>",
        [(2, 1)],
    ),
    "list-without-definition": (_list(_level(0, "%1."), key=0) + '<w:num w:numId="1"/>', [(1, 0)]),
    "level-not-defined": (_list(_level(0, "%1.")), [(1, 3)]),
    "ancestor-not-defined": (_list(_level(1, "%2.")), [(1, 1)]),
    "level-without-text": (
        _list('<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/></w:lvl>'),
        [(1, 0)],
    ),
    "label-shows-a-deeper-level": (_list(_level(0, "%2.")), [(1, 0)]),
    "label-shows-a-level-never-counted": (_list(_level(0, "%2."), _level(1, "%2.")), [(1, 0)]),
    "restart-after-the-level-above": (
        _list(_level(0, "%1."), _level(1, "%2.", extra='<w:lvlRestart w:val="1"/>')),
        [(1, 0), (1, 1)],
    ),
    "restart-on-level-0": (_list(_level(0, "%1.", extra='<w:lvlRestart w:val="1"/>')), [(1, 0)]),
    "negative-restart-deeper": (
        _list(_level(0, "%1."), _level(1, "%2.", extra='<w:lvlRestart w:val="-1"/>')),
        [(1, 0)],
    ),
    "text-past-255": (_list(_level(0, "x" * 256)), [(1, 0)]),
    "hidden-mark": (_list(_level(0, "%1.")), [(1, 0, "<w:rPr><w:vanish/></w:rPr>")]),
    "special-hidden-mark": (_list(_level(0, "%1.")), [(1, 0, "<w:rPr><w:specVanish/></w:rPr>")]),
    "hidden-mark-visible-level": (
        _list(_level(0, "%1.", extra='<w:rPr><w:vanish w:val="0"/></w:rPr>')),
        [(1, 0, "<w:rPr><w:vanish/></w:rPr>")],
    ),
}


@pytest.mark.parametrize("name", sorted(_REFUSED_LISTS))
def test_the_check_draws_no_label_it_cannot_draw_as_word_does(name: str) -> None:
    numbering, items = _REFUSED_LISTS[name]
    nums = "" if name == "list-without-definition" else _NUM
    data = docx("".join(_item(*item) for item in items), numbering=numbering + nums)
    with pytest.raises(CertificationError):
        DocxSource(data)


def test_the_check_refuses_a_numbering_style_naming_no_list_back() -> None:
    path = CORPUS / "numbering-cases" / "numbering-style-link-one-way.docx"
    with pytest.raises(CertificationError):
        DocxSource(path.read_bytes())


def _notes(*ids: int) -> str:
    return "".join(
        f'<w:footnote w:id="{i}"><w:p><w:r><w:t>note</w:t></w:r></w:p></w:footnote>' for i in ids
    )


def _reference(note: int, extra: str = "") -> str:
    return f'<w:r><w:footnoteReference{extra} w:id="{note}"/></w:r>'


@pytest.mark.parametrize("value", ["1", "true", "on"])
def test_a_custom_note_mark_draws_no_number(value: str) -> None:
    body = _p(
        "<w:r><w:t>a</w:t></w:r>"
        + _reference(1, f' w:customMarkFollows="{value}"')
        + "<w:r><w:t>*</w:t></w:r>"
    )
    data = docx(body, footnotes=_notes(1))
    assert "refusal" not in json.loads(output.read(data)[0])
    assert DocxSource(data).note_marks == {("footnote", 1): None}


def test_note_marks_take_each_sections_format_in_turn() -> None:
    def closing(fmt: str) -> str:
        return f'<w:sectPr><w:footnotePr><w:numFmt w:val="{fmt}"/></w:footnotePr></w:sectPr>'

    body = (
        f"<w:p><w:pPr>{closing('lowerRoman')}</w:pPr><w:r><w:t>a</w:t></w:r>{_reference(1)}</w:p>"
        f"<w:p><w:pPr>{closing('upperLetter')}</w:pPr><w:r><w:t>b</w:t></w:r>{_reference(2)}</w:p>"
        f"<w:p><w:r><w:t>c</w:t></w:r>{_reference(3)}</w:p>{closing('chicago')}"
    )
    data = docx(body, footnotes=_notes(1, 2, 3))
    assert "refusal" not in json.loads(output.read(data)[0])
    marks = DocxSource(data).note_marks
    assert [marks[("footnote", n)] for n in (1, 2, 3)] == ["i", "B", "\u2021"]


@pytest.mark.parametrize(
    "body",
    [
        _p("<w:r><w:t>a</w:t></w:r>" + _reference(1) + _reference(1)),
        _p("<w:r><w:t>a</w:t></w:r>" + _reference(1))
        + '<w:sectPr><w:footnotePr><w:numRestart w:val="eachPage"/></w:footnotePr></w:sectPr>',
    ],
    ids=["referred-to-twice", "restart-each-page"],
)
def test_the_check_draws_no_note_mark_it_cannot_draw_as_word_does(body: str) -> None:
    with pytest.raises(CertificationError):
        DocxSource(docx(body, footnotes=_notes(1)))


@pytest.mark.parametrize(
    ("value", "fmt", "written"),
    [
        (3888, "upperRoman", "MMMDCCCLXXXVIII"),
        (444, "lowerRoman", "cdxliv"),
        (999, "upperRoman", "CMXCIX"),
        (3999, "upperRoman", "MMMCMXCIX"),
        (1, "upperRoman", "I"),
        (26, "upperLetter", "Z"),
        (27, "lowerLetter", "aa"),
        (53, "upperLetter", "AAA"),
        (780, "upperLetter", "Z" * 30),
        (5, "chicago", "**"),
        (6, "chicago", "\u2020\u2020"),
        (7, "decimalZero", "07"),
        (0, "decimal", "0"),
        (12, "none", ""),
    ],
)
def test_numbers_are_written_in_every_format_as_word_writes_them(
    value: int, fmt: str, written: str
) -> None:
    assert _formatted(value, fmt) == written


@pytest.mark.parametrize(
    ("value", "fmt"),
    [
        (0, "upperRoman"),
        (4000, "lowerRoman"),
        (0, "upperLetter"),
        (781, "lowerLetter"),
        (0, "chicago"),
        (7, "chicago"),
        (-1, "decimal"),
        (1, "ordinal"),
    ],
)
def test_a_number_no_format_writes_is_never_written(value: int, fmt: str) -> None:
    with pytest.raises(CertificationError):
        _formatted(value, fmt)


def test_a_wingdings_bullet_is_drawn_through_its_closed_table_and_only_so() -> None:
    wingdings = '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/></w:rPr>'
    square = _list(_level(0, "", "bullet", extra=wingdings)) + _NUM
    data = docx(_item(1, 0), numbering=square)
    assert _labels_certified(data) == [(1, "▪", "tab")]
    # The label the reader would have drawn without the table is not the check's.
    value = json.loads(output.read(data)[0])
    value["paragraphs"][0]["numbering"]["text"] = ""
    with pytest.raises(CertificationError):
        DocxSource(data).certify(value)
    # Wingdings in one Latin slot only, or a code outside the table, is never certified.
    half = '<w:rPr><w:rFonts w:ascii="Wingdings" w:hAnsi="Arial"/></w:rPr>'
    for numbering in (
        _list(_level(0, "", "bullet", extra=half)) + _NUM,
        _list(_level(0, "", "bullet", extra=wingdings)) + _NUM,
    ):
        with pytest.raises(CertificationError):
            _ = DocxSource(docx(_item(1, 0), numbering=numbering)).labels


_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _wrapped(inner: str) -> str:
    return (
        f'<mc:AlternateContent xmlns:mc="{_MC}"><mc:Choice Requires="w14">{inner}</mc:Choice>'
        f"<mc:Fallback>{inner}</mc:Fallback></mc:AlternateContent>"
    )


def test_alternate_content_and_compatibility_processing_are_never_resolved() -> None:
    vanish = _wrapped("<w:vanish/>")
    hidden = (
        f'<w:style w:type="paragraph" w:default="1" w:styleId="D"><w:rPr>{vanish}</w:rPr></w:style>'
    )
    with pytest.raises(CertificationError):
        DocxSource(docx(_plain(), styles=hidden))
    started = _list(_level(0, "%1.")) + _NUM.replace(
        "</w:num>",
        _wrapped('<w:lvlOverride w:ilvl="0"><w:startOverride w:val="5"/></w:lvlOverride>')
        + "</w:num>",
    )
    with pytest.raises(CertificationError):
        DocxSource(docx(_item(1, 0), numbering=started))
    themed = docx(_plain(), minor_font="Calibri")
    theme = f'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">{_wrapped("<a:themeElements/>")}</a:theme>'
    with pytest.raises(CertificationError):
        DocxSource(_with_part(themed, "word/theme/theme1.xml", theme))
    for attribute in ("ProcessContent", "MustUnderstand"):
        with pytest.raises(CertificationError):
            DocxSource(docx(f'<w:p mc:{attribute}="w"><w:r><w:t>a</w:t></w:r></w:p>'))
    # A list level's own alternate content is the level's, and a level no paragraph draws.
    unused = _list(_level(0, "%1."), _level(1, "%2.", extra=_wrapped("<w:isLgl/>"))) + _NUM
    assert _labels_certified(docx(_item(1, 0), numbering=unused)) == [(1, "1.", "tab")]


def test_a_label_takes_the_paragraph_marks_character_style() -> None:
    styles = (
        '<w:style w:type="character" w:styleId="Sym"><w:rPr>'
        '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:style>'
    )
    letters = _list(_level(0, "%1)", "lowerLetter")) + _NUM
    data = docx(
        _item(1, 0, '<w:rPr><w:rStyle w:val="Sym"/></w:rPr>'), styles=styles, numbering=letters
    )
    assert _labels_certified(data) == [(1, "\u03b1)", "tab")]


def test_a_based_on_naming_another_kind_of_style() -> None:
    # Word takes nothing from a character style a paragraph style is based on.
    styles = (
        f'<w:style w:type="character" w:styleId="C">{_SYMBOL}</w:style>'
        '<w:style w:type="paragraph" w:styleId="P"><w:basedOn w:val="C"/></w:style>'
    )
    assert not _mapped(docx(_plain('<w:pStyle w:val="P"/>'), styles=styles))
    # A character style based on a paragraph style is not on record.
    under = (
        f'<w:style w:type="paragraph" w:styleId="Q">{_SYMBOL}</w:style>'
        '<w:style w:type="character" w:styleId="D"><w:basedOn w:val="Q"/></w:style>'
    )
    with pytest.raises(CertificationError):
        DocxSource(docx(_plain(run_properties='<w:rStyle w:val="D"/>'), styles=under))


@pytest.mark.parametrize(
    "kind",
    [
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/x/styles",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/styles",
    ],
)
def test_a_relationship_of_a_type_word_does_not_write_is_never_followed(kind: str) -> None:
    data = docx(
        _plain(),
        styles=f'<w:style w:type="paragraph" w:default="1" w:styleId="D">{_SYMBOL}</w:style>',
    )
    standard = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rels = archive.read("word/_rels/document.xml.rels").decode()
    with pytest.raises(CertificationError):
        DocxSource(_with_part(data, "word/_rels/document.xml.rels", rels.replace(standard, kind)))
