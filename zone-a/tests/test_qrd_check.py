"""The QRD conformance check: pinned EMA ePIs, the matching rules, and the committed results.

The rule cases build a document from the mapping's own sections, so each starts conformant and
changes one thing; the product cases pin what the check finds in the three EMA ePIs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from zone_a.docx.reader import Mark, Paragraph
from zone_a.epi.reader import Document, Section, SectionRefusal
from zone_a.qrd.check import check
from zone_a.qrd.pattern import parse

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "labels" / "ema-epi"
LOCK = json.loads((LABELS / "sources.lock.json").read_text(encoding="utf-8"))
REGISTRY: dict[str, Any] = json.loads(
    (ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json").read_text(encoding="utf-8")
)
MAPPING: dict[str, Any] = json.loads(
    (ROOT / "fhir" / "mappings" / "cap-smpc-en.json").read_text(encoding="utf-8")
)
RESULTS = {
    entry["file"]: json.loads((LABELS / "checks" / entry["file"]).read_text(encoding="utf-8"))
    for entry in LOCK["sources"]
}


# --- pinned sources and committed results ------------------------------------------------------


def test_every_pinned_label_matches_its_lock_entry_and_nothing_else_is_there() -> None:
    files = {path.name for path in (LABELS / "sources").iterdir()}
    assert files == {entry["file"] for entry in LOCK["sources"]}
    for entry in LOCK["sources"]:
        data = (LABELS / "sources" / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]
        assert len(data) == entry["bytes"], entry["file"]
        assert entry["url"].startswith("https://epi.ema.europa.eu/consuming/api/fhir/Bundle/")


def test_the_committed_results_are_what_the_sources_give() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_labels", ROOT / "zone-a" / "scripts" / "check_labels.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, content in module.expected().items():
        assert path.read_text(encoding="utf-8") == content, "run zone-a/scripts/check_labels.py"


# --- a conformant document to change one thing at a time ---------------------------------------


def _paragraphs(*texts: str) -> tuple[Paragraph, ...]:
    return tuple(Paragraph(text=t, style=None, numbering=None, table=None) for t in texts)


def _tree(node: dict[str, Any], content: dict[str, tuple[Paragraph, ...]]) -> Section:
    return Section(
        code=node["targetCode"],
        title=node["title"],
        paragraphs=content.get(node["sourceKey"], ()),
        refusal=None,
        sections=tuple(_tree(child, content) for child in node.get("children", [])),
    )


def document(**content: tuple[Paragraph, ...]) -> Document:
    keys = {key.replace("_", "."): value for key, value in content.items()}
    return Document(
        title="X", date=None, document_type=None, sections=(_tree(MAPPING["root"], keys),)
    )


def findings(result: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [f for f in result["findings"] if f["kind"] == kind]


def status(result: dict[str, Any], identifier: str) -> str:
    return str(next(s["status"] for s in result["statements"] if s["id"] == identifier))


def _item_index(key: str, starts: str) -> int:
    section = next(s for s in REGISTRY["sections"] if s["key"] == key)
    return next(i for i, item in enumerate(section["items"]) if item["source"].startswith(starts))


def test_a_conformant_skeleton_has_no_heading_findings() -> None:
    result = check(document(), REGISTRY, MAPPING)
    for kind in ("missing-heading", "heading-text", "order", "unmapped-code", "duplicate-section"):
        assert findings(result, kind) == [], kind


def test_heading_findings() -> None:
    base = document()
    root = base.sections[0]
    four = root.sections[3]
    renamed = replace(four.sections[0], title="4.1 Indications")
    without_43 = tuple(s for s in four.sections if s.code != "200000029805")
    changed = replace(four, sections=(renamed, *without_43[1:]))
    swapped = (root.sections[1], root.sections[0], *root.sections[2:3], changed, *root.sections[4:])
    extra = Section(code="999", title="Extra", paragraphs=(), refusal=None)
    result = check(
        replace(base, sections=(replace(root, sections=(*swapped, extra)),)), REGISTRY, MAPPING
    )
    assert [f["key"] for f in findings(result, "heading-text")] == ["smpc.4.1"]
    assert [f["key"] for f in findings(result, "missing-heading")] == ["smpc.4.3"]
    assert [f["key"] for f in findings(result, "order")] == ["smpc.1"]
    assert [f["code"] for f in findings(result, "unmapped-code")] == ["999"]


def test_an_optional_heading_segment_may_be_present_or_absent() -> None:
    root = document().sections[0]
    six = root.sections[5]
    short = replace(six.sections[4], title="6.5 Nature and contents of container")
    changed = replace(six, sections=(*six.sections[:4], short, *six.sections[5:]))
    edited = replace(root, sections=(*root.sections[:5], changed, *root.sections[6:]))
    result = check(replace(document(), sections=(edited,)), REGISTRY, MAPPING)
    assert findings(result, "heading-text") == []


# --- statements -------------------------------------------------------------------------------


REPORTING = (
    "Reporting suspected adverse reactions after authorisation of the medicinal product is "
    "important. It allows continued monitoring of the benefit/risk balance of the medicinal "
    "product. Healthcare professionals are asked to report any suspected adverse reactions via "
    "the national reporting system listed in Appendix V."
)


def test_a_standard_statement_with_fill_ins_and_optional_segments_is_used() -> None:
    text = (
        "The safety and efficacy of Xyzzy in children aged 0 to 18 years have not yet been "
        "established."
    )
    result = check(document(smpc_4_2=_paragraphs("Posology", text)), REGISTRY, MAPPING)
    assert status(result, f"smpc.4.2#{_item_index('smpc.4.2', '<The <safety>')}") == "used"


def test_whitespace_and_no_break_spaces_do_not_matter() -> None:
    text = REPORTING.replace(" ", "\u00a0 ", 3)
    result = check(document(smpc_4_8_reporting=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}"
    assert status(result, identifier) == "used"
    assert identifier not in {f["id"] for f in findings(result, "missing-statement")}


def test_changed_wording_is_a_deviation_with_the_difference() -> None:
    text = REPORTING.replace("Appendix V.", "Appendix V and include the batch number.")
    result = check(document(smpc_4_8_reporting=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}"
    assert status(result, identifier) == "deviation"
    (deviation,) = [f for f in findings(result, "deviation") if f["id"] == identifier]
    assert deviation["differences"] == [
        {"change": "replace", "template": ".", "label": "and include the batch number."}
    ]


def test_a_missing_mandatory_statement_and_subheading_are_findings() -> None:
    result = check(document(), REGISTRY, MAPPING)
    missing = {f["id"] for f in findings(result, "missing-statement")}
    assert f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}" in missing
    assert f"smpc.4.2#{_item_index('smpc.4.2', 'Paediatric population')}" in {
        f["id"] for f in findings(result, "missing-subheading")
    }


def test_a_statement_across_paragraphs_matches_consecutive_paragraphs() -> None:
    paragraphs = _paragraphs(
        "Traceability",
        "\u00a0",
        "In order to improve the traceability of biological medicinal products, the name and "
        "the batch number of the administered product should be clearly recorded.",
    )
    result = check(document(smpc_4_4=paragraphs), REGISTRY, MAPPING)
    assert status(result, f"smpc.4.4#{_item_index('smpc.4.4', '<Traceability')}") == "used"


def test_a_paragraph_explained_by_one_statement_is_not_a_deviation_of_its_sibling() -> None:
    text = "This medicinal product does not require any special storage conditions."
    result = check(document(smpc_6_4=_paragraphs(text)), REGISTRY, MAPPING)
    statuses = {
        s["id"]: s["status"] for s in result["statements"] if s["id"].startswith("appendix-III")
    }
    assert "used" in statuses.values()
    assert "deviation" not in statuses.values()


def test_a_refused_section_is_reported_and_its_statements_are_not_guessed() -> None:
    base = document(smpc_4_8_reporting=_paragraphs(REPORTING))
    root = base.sections[0]
    four = root.sections[3]
    eight = next(s for s in four.sections if s.code == "200000029816")
    reporting = replace(
        eight.sections[0], paragraphs=(), refusal=SectionRefusal("embedded-comment", "x")
    )
    new_eight = replace(eight, sections=(reporting,))
    new_four = replace(four, sections=tuple(new_eight if s is eight else s for s in four.sections))
    edited = replace(root, sections=tuple(new_four if s is four else s for s in root.sections))
    result = check(replace(base, sections=(edited,)), REGISTRY, MAPPING)
    assert findings(result, "refused-section")[0]["code"] == "embedded-comment"
    identifier = f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}"
    assert status(result, identifier) == "not-checked"
    assert identifier not in {f["id"] for f in findings(result, "missing-statement")}


@pytest.mark.parametrize(
    ("mark", "reported"),
    [
        ("color-red", True),
        ("shading-yellow", True),
        ("faint", True),
        ("strike", True),
        ("border", True),
        ("superscript", False),
    ],
)
def test_colour_shading_and_faint_marks_are_formatting_findings(mark: str, reported: bool) -> None:
    paragraph = Paragraph("see below", None, None, None, marks=(Mark(0, 3, mark), Mark(3, 4, mark)))
    result = check(document(smpc_1=(paragraph,)), REGISTRY, MAPPING)
    # The mark over the space alone shows no text and is not reported.
    assert len(findings(result, "formatting")) == (1 if reported else 0)


@pytest.mark.parametrize("kind", ["border", "faint", "strike", "shading-black"])
def test_a_bar_faint_or_struck_sign_is_a_formatting_finding(kind: str) -> None:
    # "Store at" a white "-" "20 °C" reads "-20 °C" to the check and "20 °C" to a reader.
    paragraph = Paragraph("Store at -20 °C", None, None, None, marks=(Mark(9, 10, kind),))
    result = check(document(smpc_1=(paragraph,)), REGISTRY, MAPPING)
    assert len(findings(result, "formatting")) == 1


@pytest.mark.parametrize(
    ("text", "start", "end", "reported"),
    [
        # "≥ 1" typed as an underlined ">": the text reads ">", the viewer shows "≥".
        ("defined as >1 target", 11, 12, True),
        ("see section 4.4", 4, 15, False),
        ("1a dose", 1, 2, True),
        ("Strong CYP3A inhibitors", 0, 23, False),
    ],
)
def test_an_underline_over_what_it_changes_is_a_formatting_finding(
    text: str, start: int, end: int, reported: bool
) -> None:
    paragraph = Paragraph(text, None, None, None, marks=(Mark(start, end, "underline"),))
    result = check(document(smpc_1=(paragraph,)), REGISTRY, MAPPING)
    assert len(findings(result, "formatting")) == (1 if reported else 0)


# --- what the check finds in the three EMA ePIs ---------------------------------------------------


def test_brukinsa() -> None:
    result = RESULTS["brukinsa-smpc-en.json"]
    assert [(f["key"], f["found"]) for f in findings(result, "heading-text")] == [
        (
            "smpc.6.5",
            "6.5 Nature and contents of container <and special equipment for use, administration "
            "or implantation>",
        ),
        ("smpc.6.6", "6.6 Special precautions for disposal <and other handling>"),
    ]
    assert [(f["section"], f["code"]) for f in findings(result, "refused-section")] == [
        ("4.8 Undesirable effects", "embedded-comment")
    ]
    assert status(result, "document#0") == "used"
    assert _deviation(result, "smpc.4.7#0")["differences"] == [
        {"change": "replace", "template": "on", "label": "in"}
    ]
    waiver = _deviation(result, "smpc.5.1#6")["differences"]
    assert waiver == [{"change": "replace", "template": "in", "label": "for"}]


def test_jentadueto() -> None:
    result = RESULTS["jentadueto-smpc-en.json"]
    assert [(f["code"], f["found"]) for f in findings(result, "heading-text")] == [
        ("200000029803", "Special populations"),
        ("200000029818", "Summary of the safety profile"),
    ]
    assert len(findings(result, "xhtml-defect")) == 7
    assert findings(result, "refused-section") == []


def test_nuvaxovid() -> None:
    result = RESULTS["nuvaxovid-smpc-en.json"]
    assert [(f["section"], f["code"]) for f in findings(result, "refused-section")] == [
        ("SUMMARY OF PRODUCT CHARACTERISTICS", "malformed-xhtml")
    ]
    reporting = f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}"
    (deviation,) = [f for f in findings(result, "deviation") if f["id"] == reporting]
    assert deviation["differences"] == [
        {
            "change": "replace",
            "template": ".",
            "label": "and include batch/Lot number if available.",
        }
    ]


def test_every_label_links_to_the_old_ema_address() -> None:
    for name, result in RESULTS.items():
        (closing,) = [f for f in findings(result, "deviation") if f["id"] == "document#1"]
        assert closing["differences"][0] == {
            "change": "replace",
            "template": "https",
            "label": "http",
        }, name


# --- review round 1 -----------------------------------------------------------------------


def test_struck_or_faint_text_is_not_a_match() -> None:
    text = "No interaction studies have been performed."
    for kind in ("strike", "faint"):
        paragraph = Paragraph(text, None, None, None, marks=(Mark(0, len(text), kind),))
        result = check(document(smpc_4_5=(paragraph,)), REGISTRY, MAPPING)
        assert status(result, f"smpc.4.5#{_item_index('smpc.4.5', '<No interaction')}") != "used"


def test_an_exact_sibling_does_not_hide_a_deviation_in_the_same_paragraph() -> None:
    text = (
        "No special requirements for disposal. Any unused medicinal product or waste material "
        "should be disposed of in accordance with national requirements."
    )
    result = check(document(smpc_6_6=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.6.6#{_item_index('smpc.6.6', '<Any unused')}"
    assert status(result, identifier) == "deviation"
    (deviation,) = [f for f in findings(result, "deviation") if f["id"] == identifier]
    assert deviation["differences"] == [
        {"change": "replace", "template": "local", "label": "national"}
    ]


def test_a_required_space_is_required() -> None:
    text = (
        "Currentlyavailable dataare described insection 5.1 but no recommendation on a posology "
        "can be made."
    )
    result = check(document(smpc_4_2=_paragraphs(text)), REGISTRY, MAPPING)
    assert status(result, f"smpc.4.2#{_item_index('smpc.4.2', '<Currently available')}") != "used"


def test_a_match_is_located_in_the_paragraph_as_read() -> None:
    text = "Note:\u00a0   No interaction studies have been performed."
    result = check(document(smpc_4_5=_paragraphs(text)), REGISTRY, MAPPING)
    used = next(
        s
        for s in result["statements"]
        if s["id"] == f"smpc.4.5#{_item_index('smpc.4.5', '<No interaction')}"
    )
    assert text[used["start"] : used["end"]] == "No interaction studies have been performed."


def test_differences_stop_at_the_end_of_the_sentence() -> None:
    text = REPORTING.replace("Appendix V.", "Appendix V and include the batch number.")
    text += " The vial contents must be used within six hours."
    result = check(document(smpc_4_8_reporting=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}"
    (deviation,) = [f for f in findings(result, "deviation") if f["id"] == identifier]
    assert deviation["differences"] == [
        {"change": "replace", "template": ".", "label": "and include the batch number."}
    ]


# --- review round 2 -----------------------------------------------------------------------

DISPOSAL = (
    "Any unused medicinal product or waste material should be disposed of in accordance with "
    "local requirements."
)


def _deviation(result: dict[str, Any], identifier: str) -> dict[str, Any]:
    (deviation,) = [f for f in findings(result, "deviation") if f["id"] == identifier]
    return deviation


def test_struck_words_inside_a_statement_are_named_in_its_differences() -> None:
    text = DISPOSAL.replace("local", "national local")
    start = text.index("national")
    paragraph = Paragraph(text, None, None, None, marks=(Mark(start, start + 8, "strike"),))
    result = check(document(smpc_6_6=(paragraph,)), REGISTRY, MAPPING)
    identifier = f"smpc.6.6#{_item_index('smpc.6.6', '<Any unused')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "insert", "template": "", "label": "[struck or faint text]"}
    ]


def test_differences_do_not_run_into_the_next_sentence_when_words_are_missing() -> None:
    text = DISPOSAL.replace("local ", "") + " Store the pen in the fridge until use please."
    result = check(document(smpc_6_6=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.6.6#{_item_index('smpc.6.6', '<Any unused')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "delete", "template": "local", "label": ""}
    ]


def test_a_statement_over_paragraphs_keeps_its_paragraph_breaks() -> None:
    entry = next(e for e in REGISTRY["appendices"]["I"]["entries"] if e["id"] == "pregnancy.4")
    paragraphs = [p.replace("{", "").replace("}", "") for p in entry["paragraphs"]]
    # As a label writes it: no brackets, no guidance, no option letters.
    written = (re.sub(r"^[AB] |[<>]|\[[^\]]*\]", "", p) for p in paragraphs)
    rendered = _paragraphs(*(p for p in written if p.strip()))
    filler = _paragraphs(*(f"Unrelated paragraph {n}." for n in range(20)))
    result = check(document(smpc_4_6=filler + rendered), REGISTRY, MAPPING)
    assert status(result, "appendix-I#pregnancy.4") != "deviation"


@pytest.mark.parametrize(
    "text", ["Do not store above25 \u00b0C.", "No special requirementsfor disposal."]
)
def test_a_space_next_to_a_present_optional_segment_is_required(text: str) -> None:
    result = check(
        document(smpc_6_4=_paragraphs(text), smpc_6_6=_paragraphs(text)), REGISTRY, MAPPING
    )
    assert status(result, "appendix-III#0") != "used"
    assert status(result, f"smpc.6.6#{_item_index('smpc.6.6', '<No special')}") != "used"


def test_a_fill_in_inside_an_optional_segment_is_lazy() -> None:
    closing = (
        "Detailed information on this medicinal product is available on the website of the "
        "European Medicines Agency https://www.ema.europa.eu, and on the website of the Irish "
        "agency. More text follows. And more."
    )
    result = check(document(smpc_10=_paragraphs(closing)), REGISTRY, MAPPING)
    used = next(s for s in result["statements"] if s["id"] == "document#1")
    assert closing[used["start"] : used["end"]].endswith("Irish agency.")


@pytest.mark.parametrize("title", ["Posology ", "Posology\n", "\u2003Posology"])
def test_a_title_is_collapsed_as_the_heading_check_collapses_it(title: str) -> None:
    root = document().sections[0]
    four = root.sections[3]
    two = four.sections[1]
    posology = replace(two.sections[0], title=title)
    new_two = replace(two, sections=(posology, *two.sections[1:]))
    new_four = replace(four, sections=(four.sections[0], new_two, *four.sections[2:]))
    edited = replace(root, sections=(*root.sections[:3], new_four, *root.sections[4:]))
    result = check(replace(document(), sections=(edited,)), REGISTRY, MAPPING)
    assert status(result, f"smpc.4.2#{_item_index('smpc.4.2', 'Posology')}") == "used"


# --- review round 3 -----------------------------------------------------------------------


def _statements() -> list[tuple[str, list[Any]]]:
    out: list[tuple[str, list[Any]]] = []
    for section in REGISTRY["sections"]:
        for number, item in enumerate(section["items"]):
            if item["kind"] == "statement" and item.get("pattern"):
                out.append((f"{section['key']}#{number}", item["pattern"]))
    out += [(f"document#{n}", i["pattern"]) for n, i in enumerate(REGISTRY["documentStatements"])]
    appendices = REGISTRY["appendices"]
    out += [(f"I#{e['id']}", e["pattern"]) for e in appendices["I"]["entries"] if e["pattern"]]
    out += [(f"III#{n}", i["pattern"]) for n, i in enumerate(appendices["III"]["items"])]
    return out


def _render(pieces: list[Any], choose: Any, counter: list[int]) -> str:
    out: list[str] = []
    for piece in pieces:
        if piece.kind == "text":
            out.append(piece.joint + piece.text.replace("(s)", "s").replace("(S)", "S"))
        elif piece.kind == "fill":
            out.append(piece.joint + "Zeta")
        else:
            counter[0] += 1
            if choose(counter[0]):
                out.append(piece.joint + _render(list(piece.pieces), choose, counter))
    return "".join(out)


def test_every_statement_matches_the_ways_a_person_writes_it() -> None:
    """Each statement, written with its optional segments all present, all absent, or every
    other one, and its fill-ins filled, one paragraph per line, is found in that text."""
    from zone_a.qrd import check as module

    for identifier, pattern in _statements():
        pieces = module._pieces(module._content(pattern))[0]
        if sum(len(run) for run in module._required(pieces)) < module.MIN_LITERAL:
            continue
        for choose in (lambda _: True, lambda _: False, lambda k: k % 2 == 0, lambda k: k % 2):
            text = _render(pieces, choose, [0]).strip()
            lines = []
            for number, paragraph in enumerate(text.split("\n")):
                collapsed, positions = module._collapse(paragraph)
                if collapsed:
                    lines.append(module._Line("x", number, collapsed, positions, (0, number)))
            assert module._search(pieces, lines) is not None, (identifier, text[:80])


def test_a_statement_opening_with_an_optional_paragraph_does_not_break_the_check() -> None:
    paragraphs = _paragraphs("Pregnancy", "Zeta can be used during pregnancy.")
    result = check(document(smpc_4_6=paragraphs), REGISTRY, MAPPING)
    assert status(result, "appendix-I#pregnancy.9") == "used"


def test_optional_segments_written_together_are_separated_by_a_space() -> None:
    prefix = "<This medicinal product has been authorised under \u2018exceptional"
    identifier = f"smpc.5.1#{_item_index('smpc.5.1', prefix)}"
    items = next(s for s in REGISTRY["sections"] if s["key"] == "smpc.5.1")["items"]
    source = items[int(identifier.split("#")[1])]["source"]
    # The statement as a label writes it, choosing only "for scientific reasons".
    text = re.sub(r"\[[^\]]*\]", "", source).replace("<", "").replace(">", "")
    text = text.replace("due to the rarity of the disease", "").replace("for ethical reasons", "")
    text = re.sub(" +", " ", text)
    paragraphs = _paragraphs(*(line.strip() for line in text.split("\n") if line.strip()))
    result = check(document(smpc_5_1=paragraphs), REGISTRY, MAPPING)
    assert status(result, identifier) == "used"


def test_a_fill_in_followed_only_by_optional_text_takes_the_rest_of_the_line() -> None:
    result = RESULTS["jentadueto-smpc-en.json"]
    used = next(s for s in result["statements"] if s["id"] == "smpc.5.1#0")
    assert used["end"] - used["start"] > len("Pharmacotherapeutic group: X, ATC code: A")


def test_struck_text_in_the_place_of_a_fill_in_is_a_difference() -> None:
    text = "Keep the bottle tightly closed in order to protect from light."
    start = text.index("bottle")
    paragraph = Paragraph(text, None, None, None, marks=(Mark(start, start + 6, "strike"),))
    result = check(document(smpc_6_4=(paragraph,)), REGISTRY, MAPPING)
    assert "deviation" in {
        s["status"] for s in result["statements"] if s["id"].startswith("appendix-III")
    }


# --- review round 4 -----------------------------------------------------------------------


def test_a_label_that_keeps_the_templates_plural_marker_is_used() -> None:
    text = (
        "Hypersensitivity to the active substance(s) or to any of the excipients listed in "
        "section 6.1."
    )
    result = check(document(smpc_4_3=_paragraphs(text)), REGISTRY, MAPPING)
    assert status(result, "smpc.4.3#0") == "used"


@pytest.mark.parametrize(
    ("key", "text", "difference"),
    [
        (
            "smpc_6_4",
            "Keep the bottle tightly closed in ordr to protect from light.",
            {"change": "replace", "template": "order", "label": "ordr"},
        ),
        (
            "smpc_4_3",
            "Hypersensitivity to the active substance or to any of the excipient listed in "
            "section 6.1 or lactose.",
            {"change": "replace", "template": "excipients", "label": "excipient"},
        ),
    ],
)
def test_one_changed_word_beside_a_fill_in_is_a_deviation(
    key: str, text: str, difference: dict[str, str]
) -> None:
    result = check(document(**{key: _paragraphs(text)}), REGISTRY, MAPPING)
    differences = [d for f in findings(result, "deviation") for d in f["differences"]]
    assert difference in differences


def test_a_trailing_segment_with_no_words_of_its_own_does_not_take_the_next_paragraph() -> None:
    paragraphs = _paragraphs(
        "It is unknown whether Foo/metabolites are excreted in human milk.",
        "A risk to the newborns/infants cannot be excluded.",
        "Women should use contraception.",
    )
    result = check(document(smpc_4_6=paragraphs), REGISTRY, MAPPING)
    used = next(s for s in result["statements"] if s["id"] == "appendix-I#lactation.2")
    assert used["status"] == "used"
    assert used.get("lastParagraph", used["paragraph"]) == 1


def test_the_same_words_with_other_paragraph_breaks_are_a_layout_deviation() -> None:
    text = (
        "Traceability In order to improve the traceability of biological medicinal products, the "
        "name and the batch number of the administered product should be clearly recorded."
    )
    result = check(document(smpc_4_4=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.4.4#{_item_index('smpc.4.4', '<Traceability')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "layout", "template": "", "label": ""}
    ]


def test_appendix_i_option_letters_are_not_label_text() -> None:
    paragraphs = _paragraphs(
        "There are no or limited amount of data from the use of Zeta in pregnant women.",
        "Studies in animals have shown reproductive toxicity (see section 5.3).",
        "Zeta is not recommended during pregnancy and in women of childbearing potential not "
        "using contraception.",
    )
    result = check(document(smpc_4_6=paragraphs), REGISTRY, MAPPING)
    assert status(result, "appendix-I#pregnancy.4") == "used"


def test_a_deviation_starts_where_the_resemblance_starts() -> None:
    text = "Take care. " + DISPOSAL.replace("disposed of", "disposed")
    result = check(document(smpc_6_6=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.6.6#{_item_index('smpc.6.6', '<Any unused')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "delete", "template": "of", "label": ""}
    ]


def test_a_fill_in_next_to_punctuation_is_not_a_difference() -> None:
    closing = (
        "Detailed information on this medicinal product is available on the website of the "
        "European Medicines Agency http://www.ema.europa.eu, and on the website of HPRA."
    )
    result = check(document(smpc_10=_paragraphs(closing)), REGISTRY, MAPPING)
    assert _deviation(result, "document#1")["differences"] == [
        {"change": "replace", "template": "https", "label": "http"}
    ]


def test_a_present_opening_segment_needs_its_break() -> None:
    text = "It is unknown whether Foo/metabolites are excreted in human milk.A risk to the "
    text += "newborns/infants cannot be excluded."
    result = check(document(smpc_4_6=_paragraphs(text)), REGISTRY, MAPPING)
    assert status(result, "appendix-I#lactation.2") != "used"


# --- review round 5 -----------------------------------------------------------------------


def test_inserted_words_count_against_a_short_statement() -> None:
    text = "There is no relevant effect of food, so use of Zeta with or without food is possible."
    result = check(document(smpc_4_2=_paragraphs("Posology", text)), REGISTRY, MAPPING)
    assert not [f for f in findings(result, "deviation") if f["id"].startswith("smpc.4.2")]


def test_two_statements_deviating_in_one_paragraph_are_both_reported() -> None:
    text = (
        "Keep the vial in the outer box in order to protect from light. Store in the original "
        "packaging in order to protect from moisture."
    )
    result = check(document(smpc_6_4=_paragraphs(text)), REGISTRY, MAPPING)
    deviations = {f["id"] for f in findings(result, "deviation")}
    assert {"appendix-III#7", "appendix-III#9"} <= deviations


def test_punctuation_beside_a_fill_in_is_compared() -> None:
    text = "Pharmacotherapeutic group: Vaccines; ATC code: J07BN04"
    result = check(document(smpc_5_1=_paragraphs(text)), REGISTRY, MAPPING)
    assert _deviation(result, "smpc.5.1#0")["differences"] == [
        {"change": "replace", "template": ",", "label": ";"}
    ]


def test_every_choice_of_optional_segments_is_considered() -> None:
    text = (
        "The safety and efficacy of Zeta in children aged 2 to 6 years has not yet been establishd."
    )
    result = check(document(smpc_4_2=_paragraphs("Posology", text)), REGISTRY, MAPPING)
    identifier = f"smpc.4.2#{_item_index('smpc.4.2', '<The <safety>')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "replace", "template": "established", "label": "establishd"}
    ]


def test_an_optional_fill_in_in_the_same_paragraph_is_matched() -> None:
    for name, result in RESULTS.items():
        used = next(s for s in result["statements"] if s["id"] == "smpc.5.1#0")
        source = LABELS / "sources" / name
        assert used["status"] == "used", name
        assert source.exists()
    text = "Pharmacotherapeutic group: Vaccines, ATC code: J07BN04"
    result = check(document(smpc_5_1=_paragraphs(text)), REGISTRY, MAPPING)
    used = next(s for s in result["statements"] if s["id"] == "smpc.5.1#0")
    assert text[used["start"] : used["end"]] == text


def test_a_resemblance_starts_at_the_first_matching_word() -> None:
    text = (
        "No special requirements for disposal of this product. Any unused medicinal product or "
        "waste material should be disposed of in accordance with national requirements."
    )
    result = check(document(smpc_6_6=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.6.6#{_item_index('smpc.6.6', '<Any unused')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "replace", "template": "local", "label": "national"}
    ]


# --- review round 6 -----------------------------------------------------------------------


def test_nested_optional_segments_can_be_skipped() -> None:
    from zone_a.qrd import check as module

    nodes = module._nodes(module._statement(parse("a <b <c> d> e f g h")))
    for position, node in enumerate(nodes):
        if node.kind == "open":
            assert nodes[node.end].kind == "close"
            assert node.end > position


def test_of_two_equal_alignments_the_one_with_more_matches_is_kept() -> None:
    text = "Zeta has no or negligible influence on the ability to drive and use machines."
    start = text.index("negligible")
    paragraph = Paragraph(text, None, None, None, marks=(Mark(start, start + 10, "strike"),))
    result = check(document(smpc_4_7=(paragraph,)), REGISTRY, MAPPING)
    assert _deviation(result, "smpc.4.7#0")["differences"] == [
        {"change": "replace", "template": "negligible", "label": "[struck or faint text]"}
    ]


def test_a_resemblance_does_not_start_on_an_unrelated_paragraph() -> None:
    paragraphs = _paragraphs(
        "Anaphylaxis has been reported with Zeta vaccine",
        "In order to improve the traceability of biological medicinal products, the name and the "
        "batch number of the administered product should be clearly recorded.",
    )
    result = check(document(smpc_4_4=paragraphs), REGISTRY, MAPPING)
    identifier = f"smpc.4.4#{_item_index('smpc.4.4', '<Traceability')}"
    deviation = _deviation(result, identifier)
    assert deviation["paragraph"] == 1
    assert deviation["differences"] == [
        {"change": "delete", "template": "Traceability", "label": ""}
    ]


def test_a_fill_in_does_not_take_an_inserted_paragraph() -> None:
    paragraphs = _paragraphs(
        "There are no or limited amount of data from the use of Zeta in pregnant women.",
        "Studies in animals have shown reproductive toxicity (see section 5.3).",
        "Pregnant women should be told of the risk.",
        "Zeta is not recommended during pregnancy and in women of childbearing potential not "
        "using contraception.",
    )
    result = check(document(smpc_4_6=paragraphs), REGISTRY, MAPPING)
    for finding in findings(result, "deviation"):
        if finding["id"] == "appendix-I#pregnancy.4":
            assert finding["differences"] != [{"change": "insert", "template": "", "label": "Zeta"}]


def test_a_statement_set_out_over_more_paragraphs_is_a_layout_deviation() -> None:
    parts = REPORTING.split(". ")
    paragraphs = _paragraphs(parts[0] + ".", parts[1] + ".", parts[2])
    result = check(document(smpc_4_8_reporting=paragraphs), REGISTRY, MAPPING)
    identifier = f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "layout", "template": "", "label": ""}
    ]


def test_a_fill_in_longer_than_the_limit_is_not_a_perfect_resemblance() -> None:
    condition = "treatment of " + "a very long condition name " * 14
    text = (
        "The European Medicines Agency has waived the obligation to submit the results of "
        f"studies with Zeta in all subsets of the paediatric population in {condition}"
        "(see section 4.2 for information on paediatric use)."
    )
    result = check(document(smpc_5_1=_paragraphs(text)), REGISTRY, MAPPING)
    identifier = f"smpc.5.1#{_item_index('smpc.5.1', '<The European Medicines Agency has waived')}"
    for finding in findings(result, "deviation"):
        if finding["id"] == identifier:
            assert finding["similarity"] < 1.0


def test_words_past_a_missing_end_show_only_when_they_finish_a_sentence() -> None:
    text = (
        "Hypersensitivity to the active substance or to any of the excipients listed in "
        "section 6.1 or to egg proteins"
    )
    result = check(document(smpc_4_3=_paragraphs(text)), REGISTRY, MAPPING)
    for finding in findings(result, "deviation"):
        for difference in finding["differences"]:
            assert "egg" not in difference["label"]


# --- review round 7 -----------------------------------------------------------------------


def test_a_label_keeping_the_plural_marker_is_still_compared() -> None:
    text = (
        "Hypersensitivity to the active substance(s) or any of the excipients listed under "
        "section 6.1."
    )
    result = check(document(smpc_4_3=_paragraphs(text)), REGISTRY, MAPPING)
    assert status(result, "smpc.4.3#0") == "deviation"


@pytest.mark.parametrize(
    ("key", "paragraphs", "identifier"),
    [
        (
            "smpc_5_1",
            ("Pharmacotherapeutic group: antineoplastic agents", "ATC code: L01EL03"),
            "smpc.5.1#0",
        )
    ],
)
def test_a_paragraph_break_after_a_fill_in_is_not_absurd(
    key: str, paragraphs: tuple[str, ...], identifier: str
) -> None:
    result = check(document(**{key: _paragraphs(*paragraphs)}), REGISTRY, MAPPING)
    for finding in findings(result, "deviation"):
        if finding["id"] == identifier:
            # The label drops the comma and breaks the paragraph there instead.
            assert finding["differences"] == [{"change": "delete", "template": ",", "label": ""}]


def test_text_in_the_next_paragraph_is_not_reported_missing() -> None:
    parts = REPORTING.rsplit(" in Appendix V.", 1)
    paragraphs = _paragraphs(parts[0], "in Appendix V.")
    result = check(document(smpc_4_8_reporting=paragraphs), REGISTRY, MAPPING)
    identifier = f"smpc.4.8#{_item_index('smpc.4.8', 'Reporting suspected')}"
    assert _deviation(result, identifier)["differences"] == [
        {"change": "layout", "template": "", "label": ""}
    ]


def test_a_break_inside_an_optional_segment_is_kept() -> None:
    from zone_a.qrd import check as module

    nodes = module._nodes(module._statement(parse("a b c <{x}\nKeep it> d e f")))
    keep = next(node for node in nodes if node.text == "Keep")
    assert keep.brk


# --- review round 8 -----------------------------------------------------------------------


def test_a_joint_before_nested_segments_reaches_the_first_token() -> None:
    from zone_a.qrd import check as module
    from zone_a.qrd.check import _Piece

    pieces = [
        _Piece("", "text", "Store below"),
        _Piece(" ", "fill"),
        _Piece(
            "\n", "optional", pieces=(_Piece("", "optional", pieces=(_Piece("", "text", "Keep"),)),)
        ),
    ]
    keep = next(node for node in module._nodes(pieces) if node.text == "Keep")
    assert keep.brk
    assert keep.space


def test_a_fill_in_does_not_start_a_paragraph_the_template_does_not_break() -> None:
    text = REPORTING.replace("Appendix V.", "Appendix")
    paragraphs = _paragraphs(text, "V and more.", "Next paragraph.")
    result = check(document(smpc_4_8_reporting=paragraphs), REGISTRY, MAPPING)
    for finding in findings(result, "deviation"):
        for difference in finding["differences"]:
            assert "Next paragraph" not in difference["label"]
