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
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from label_docx import epi_output, output
from label_docx.certify import CertificationError, DocxSource, EpiSource, certify_docx, certify_epi
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


# --- the check's own test: every change is refused ----------------------------------------

Change = Callable[[dict[str, Any], random.Random], bool]


def _paragraphs(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Every paragraph of a result: the body and notes of a .docx, or every ePI section's."""
    if "sections" not in value:
        notes = [
            p for kind in ("footnotes", "endnotes") for n in value[kind] for p in n["paragraphs"]
        ]
        return [*value["paragraphs"], *notes]

    def walk(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [p for s in sections for p in [*s["paragraphs"], *walk(s["sections"])]]

    return walk(value["sections"])


def _lists(value: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """The lists paragraphs stand in, so a paragraph can be dropped, repeated or moved."""
    if "sections" not in value:
        notes = [n["paragraphs"] for kind in ("footnotes", "endnotes") for n in value[kind]]
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
    # A section emptied and called refused, with the count left as it was: a loss the receipt
    # would not show.
    if "sections" not in value:
        return False
    found = [s for s in value["sections"] if s["paragraphs"]]
    if not found:
        return False
    section = rng.choice(found)
    section["paragraphs"], section["refusal"] = [], {"code": "x", "detail": "x"}
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


def test_a_symbol_run_may_be_read_through_the_symbol_table_and_is_counted() -> None:
    body = _p(
        '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>\u00b3</w:t></w:r>'
    )
    data = docx(body)
    source, value = DocxSource(data), _docx_value(data)
    assert value["paragraphs"][0]["text"] == "\u2265"
    assert source.certify(value)["symbolMapped"] == 1
    # Read as stored is the other reading the check allows; anything else is refused.
    value["paragraphs"][0]["text"] = "\u00b3"
    assert source.certify(value)["symbolMapped"] == 0
    value["paragraphs"][0]["text"] = "\u2264"
    with pytest.raises(CertificationError):
        source.certify(value)


def test_text_in_another_font_may_not_be_read_through_the_symbol_table() -> None:
    data = docx(_p("<w:r><w:t>\u00b3</w:t></w:r>"))
    source, value = DocxSource(data), _docx_value(data)
    value["paragraphs"][0]["text"] = "\u2265"
    with pytest.raises(CertificationError):
        source.certify(value)


def test_hidden_whitespace_may_be_left_out_and_is_counted_and_nothing_else_may() -> None:
    body = _p(
        '<w:r><w:t xml:space="preserve">a</w:t></w:r>'
        '<w:r><w:rPr><w:vanish/></w:rPr><w:t xml:space="preserve">  </w:t></w:r>'
        "<w:r><w:t>b</w:t></w:r>"
    )
    data = docx(body)
    source, value = DocxSource(data), _docx_value(data)
    assert value["paragraphs"][0]["text"] == "ab"
    assert source.certify(value)["setAside"]["hiddenWhitespace"] == 2
    value["paragraphs"][0]["text"] = "a b"
    with pytest.raises(CertificationError):
        source.certify(value)
    # Visible whitespace may never be left out.
    plain = docx(_p('<w:r><w:t xml:space="preserve">a  b</w:t></w:r>'))
    plain_value = _docx_value(plain)
    plain_value["paragraphs"][0]["text"] = "ab"
    with pytest.raises(CertificationError):
        DocxSource(plain).certify(plain_value)


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


def test_a_symbol_run_with_a_code_outside_the_table_has_one_reading() -> None:
    body = _p(
        '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>\u4e00</w:t></w:r>'
    )
    source = DocxSource(docx(body))
    value: dict[str, Any] = {
        "paragraphs": [{"text": "\u4e00", "pages": [], "notes": [], "table": None}]
    }
    value |= {"footnotes": [], "endnotes": []}
    assert source.certify(value)["symbolMapped"] == 0


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
