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
    certify_docx,
    certify_epi,
)
from label_docx.output import canonical
from test_reader import W, docx

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
_MANIFESTS = {"sources", "expected", "word", "browser"}
# A character no document holds and no reading can produce: an inserted or substituted
# character is always one the document does not have there.
_FOREIGN = "\u2603"
# Changes of each kind per document.
_TIMES = 4


def _documents() -> list[Path]:
    return sorted(
        p
        for p in CORPUS.glob("*/*")
        if (p.suffix == ".docx" or (p.suffix == ".json" and p.stem not in _MANIFESTS))
    )


def _read(path: Path) -> tuple[DocxSource | EpiSource, dict[str, Any]] | None:
    data = path.read_bytes()
    if path.suffix == ".docx":
        value = json.loads(output.read(data)[0])
        source: DocxSource | EpiSource | None = None if "refusal" in value else DocxSource(data)
    else:
        value = json.loads(epi_output.read(data)[0])
        source = None if "refusal" in value else EpiSource(data)
    return None if source is None else (source, value)


READ = [(p, r) for p in _documents() if (r := _read(p)) is not None]


def test_the_corpus_is_read_widely_enough_to_test_the_check() -> None:
    kinds = {p.suffix for p, _ in READ}
    assert kinds == {".docx", ".json"}
    assert len(READ) >= 180


@pytest.mark.parametrize(("path", "read"), READ, ids=[p.name for p, _ in READ])
def test_every_result_the_readers_make_is_certified(
    path: Path, read: tuple[DocxSource | EpiSource, dict[str, Any]]
) -> None:
    source, value = read
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
]


@pytest.mark.parametrize(("path", "read"), READ, ids=[p.name for p, _ in READ])
def test_every_change_to_a_result_is_refused(
    path: Path, read: tuple[DocxSource | EpiSource, dict[str, Any]]
) -> None:
    source, value = read
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
            {"text": "n", "pages": [], "notes": [], "table": None, "comments": [], "marks": []}
        ],
    }
    marked = {"text": "ab", "pages": [2], "notes": [{"offset": 1, "kind": "footnote", "id": 1}]}
    source.certify(_value(marked, footnotes=[note]))
    moved = {**marked, "notes": [{"offset": 0, "kind": "footnote", "id": 1}]}
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
    data = docx(_p(f"<w:r><w:pict><v:shape {_VML}>{inner}</v:shape></w:pict></w:r>"))
    if not inner:
        DocxSource(data).certify(_value("\ufffc"))
        return
    with pytest.raises(CertificationError):
        DocxSource(data)


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


@pytest.mark.parametrize("name", ["Symbol", "symbol", "SymbolMT", "Symbol MT"])
def test_the_symbol_font_by_any_of_its_names(name: str) -> None:
    assert _mapped(docx(_plain(run_properties=f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}"/>')))


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
