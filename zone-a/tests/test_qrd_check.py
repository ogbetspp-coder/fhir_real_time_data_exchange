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
        {"change": "replace", "template": "V.", "label": "V and include the batch number."}
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
        ("superscript", False),
    ],
)
def test_colour_shading_and_faint_marks_are_formatting_findings(mark: str, reported: bool) -> None:
    paragraph = Paragraph("see below", None, None, None, marks=(Mark(0, 3, mark), Mark(3, 4, mark)))
    result = check(document(smpc_1=(paragraph,)), REGISTRY, MAPPING)
    # The mark over the space alone shows no text and is not reported.
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
            "template": "V.",
            "label": "V and include batch/Lot number if available.",
        }
    ]


def test_every_label_links_to_the_old_ema_address() -> None:
    for name, result in RESULTS.items():
        (closing,) = [f for f in findings(result, "deviation") if f["id"] == "document#1"]
        assert closing["differences"][0]["template"] == "https://www.ema.europa.eu.", name
        assert closing["differences"][0]["label"].startswith("http://www.ema.europa.eu"), name


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
        {"change": "replace", "template": "V.", "label": "V and include the batch number."}
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
    rendered = _paragraphs(*(re.sub(r"[<>]|\[[^\]]*\]", "", p) for p in paragraphs))
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
