"""An SmPC's sections, found by the QRD template's own headings (``zone_a.structure``).

The synthetic paragraphs are built here; the one real file is the EMA's QRD template, pinned in
``qrd/sources/``, read only for statuses and indices.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from label_docx import word
from label_docx.reader import Mark, Numbering, Paragraph

from zone_a.certified import read_docx
from zone_a.qrd.headings import forms
from zone_a.structure import capitals, line, structure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json").read_text("utf-8"))
MAPPING = json.loads((ROOT / "fhir" / "mappings" / "cap-smpc-en.json").read_text("utf-8"))
TEMPLATE = ROOT / "qrd" / "sources" / "qrd-product-information-template-version-104_en.docx"
SECTIONS = {s["key"]: s for s in REGISTRY["sections"]}


def _p(text: str, style: str | None = None, label: str | None = None) -> Paragraph:
    numbering = None if label is None else Numbering(1, 0, label, "tab")
    return Paragraph(text, style, numbering, None)


def _heading(key: str) -> Paragraph:
    """The section's heading as a label writes it: its number, a space, its title."""
    return _p(next(iter(forms(SECTIONS[key]))))


def _skeleton(*extra: tuple[int, Paragraph]) -> list[Paragraph]:
    """Every required section's heading with one paragraph of text, in the template's order."""
    out = [_p("ANNEX I"), _p("SUMMARY OF PRODUCT CHARACTERISTICS")]
    for section in REGISTRY["sections"]:
        if section["optional"]:
            continue
        out.append(_heading(section["key"]))
        if section["key"] == "smpc.4.2":
            out += [_p("Posology"), _p("Take 5 mg daily."), _p("Method of administration")]
        if section["key"] == "smpc.4.8":
            out += [_p("Headache."), _p("Reporting of suspected adverse reactions")]
        out.append(_p(f"Text of {section['key']}."))
    for at, paragraph in extra:
        out.insert(at, paragraph)
    return out


def _by_key(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["key"]: s for s in result["sections"]}


def test_a_label_that_follows_the_template_is_ready() -> None:
    result = structure(_skeleton(), REGISTRY, MAPPING)
    statuses = {s["key"]: s["status"] for s in result["sections"]}
    assert result["ready"]
    assert {statuses[k] for k in ("smpc.2.1", "smpc.2.2", "smpc.11", "smpc.12")} == {"absent"}
    assert statuses["smpc.4.2.posology"] == "mapped"
    assert statuses["smpc.4.8.reporting"] == "mapped"
    assert result["preamble"] == [0]
    # Each section carries the mapping's EMA code for it.
    expected: dict[str, str] = {}

    def visit(node: dict[str, Any]) -> None:
        expected[node["sourceKey"]] = node["targetCode"]
        for child in node.get("children", []):
            visit(child)

    visit(MAPPING["root"])
    assert {k: s["code"] for k, s in _by_key(result).items() if k in expected} == expected


def test_every_paragraph_is_in_exactly_one_place() -> None:
    paragraphs = _skeleton((5, _p("")), (9, _p("   ")))
    result = structure(paragraphs, REGISTRY, MAPPING)
    placed = [*result["preamble"]]
    for section in result["sections"]:
        placed += section["paragraphs"]
        if section["heading"] is not None:
            placed.append(section["heading"])
    with_text = [i for i, p in enumerate(paragraphs) if p.text.strip()]
    assert sorted(placed) == with_text


def test_a_heading_numbered_by_word_is_read_with_its_label() -> None:
    title = next(iter(forms(SECTIONS["smpc.4.4"]))).removeprefix("4.4 ")
    paragraphs = _skeleton()
    at = next(i for i, p in enumerate(paragraphs) if p.text.startswith("4.4 "))
    paragraphs[at] = _p(title, label="4.4")
    assert _by_key(structure(paragraphs, REGISTRY, MAPPING))["smpc.4.4"]["status"] == "mapped"


def test_a_heading_worded_otherwise_is_a_candidate_never_a_heading() -> None:
    paragraphs = _skeleton()
    at = next(i for i, p in enumerate(paragraphs) if p.text.startswith("4.4 "))
    paragraphs[at] = _p("4.4 Warnings and precautions")
    result = structure(paragraphs, REGISTRY, MAPPING)
    section = _by_key(result)["smpc.4.4"]
    assert (section["status"], section["candidates"]) == (
        "missing",
        [{"paragraph": at, "why": "number"}],
    )
    assert not result["ready"]
    # Its text stays where it stands, in 4.3, until a person names the heading.
    assert at in _by_key(result)["smpc.4.3"]["paragraphs"]
    assigned = structure(paragraphs, REGISTRY, MAPPING, {"smpc.4.4": at})
    assert (_by_key(assigned)["smpc.4.4"]["status"], assigned["ready"]) == ("assigned", True)
    assert at not in _by_key(assigned)["smpc.4.3"]["paragraphs"]


def test_a_heading_style_without_a_qrd_heading_is_flagged_where_it_stands() -> None:
    paragraphs = _skeleton()
    at = next(i for i, p in enumerate(paragraphs) if p.text.startswith("4.3 ")) + 1
    paragraphs.insert(at, _p("Special populations", style="Heading3"))
    section = _by_key(structure(paragraphs, REGISTRY, MAPPING))["smpc.4.3"]
    assert (section["status"], section["candidates"]) == (
        "mapped",
        [{"paragraph": at, "why": "style"}],
    )


def test_twice_or_out_of_order_is_for_a_person() -> None:
    paragraphs = _skeleton()
    twice = [*paragraphs, _heading("smpc.4.4"), _p("Again.")]
    assert _by_key(structure(twice, REGISTRY, MAPPING))["smpc.4.4"]["status"] == "duplicate"
    first = next(i for i, p in enumerate(paragraphs) if p.text.startswith("4.4 "))
    second = next(i for i, p in enumerate(paragraphs) if p.text.startswith("4.5 "))
    swapped = list(paragraphs)
    swapped[first], swapped[second] = swapped[second], swapped[first]
    result = structure(swapped, REGISTRY, MAPPING)
    assert _by_key(result)["smpc.4.4"]["status"] == "order"
    assert not result["ready"]


def test_a_section_without_a_code_cannot_be_placed() -> None:
    paragraphs = _skeleton()
    paragraphs.append(_heading("smpc.11"))
    result = structure(paragraphs, REGISTRY, MAPPING)
    assert (_by_key(result)["smpc.11"]["status"], result["ready"]) == ("no-code", False)


def test_a_named_subsection_counts_only_inside_its_section() -> None:
    paragraphs = _skeleton()
    at = next(i for i, p in enumerate(paragraphs) if p.text.startswith("4.3 ")) + 1
    paragraphs.insert(at, _p("Posology"))
    result = _by_key(structure(paragraphs, REGISTRY, MAPPING))
    assert at in result["smpc.4.3"]["paragraphs"]


@pytest.mark.parametrize(
    ("assignments", "message"),
    [
        ({"smpc.99": 3}, "no section"),
        ({"smpc.4.4": 10_000}, "no paragraph"),
        ({"smpc.6.5": 1}, "already a heading"),
        ({"smpc.4.4": 3}, "already has its heading"),
    ],
)
def test_an_assignment_that_cannot_be_right_is_refused(
    assignments: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        structure(_skeleton(), REGISTRY, MAPPING, assignments)


def test_the_qrd_template_itself_ends_at_annex_ii_and_shows_its_markup() -> None:
    paragraphs = read_docx(TEMPLATE.read_bytes())
    result = structure(paragraphs, REGISTRY, MAPPING)
    needs = {s["key"]: s["candidates"] for s in result["sections"] if s["status"] == "missing"}
    # The template writes its optional words in <angle brackets>, which no label does.
    assert sorted(needs) == ["smpc.6.5", "smpc.6.6"]
    assert all(c[0]["why"] == "number" for c in needs.values())
    assert result["summary"]["mapped"] == 30
    assert result["end"] is not None
    assigned = {key: candidates[0]["paragraph"] for key, candidates in needs.items()}
    assert structure(paragraphs, REGISTRY, MAPPING, assigned)["ready"]


def test_the_script_writes_the_structure_or_the_refusal(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "structure_label", ROOT / "zone-a" / "scripts" / "structure_label.py"
    )
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    out = tmp_path / "structure.json"
    assigned = ["--assign", "smpc.6.5=192", "--assign", "smpc.6.6=196"]
    assert script.main([str(TEMPLATE), *assigned, "--out", str(out)]) == 0
    result = json.loads(out.read_text("utf-8"))
    assert (result["ready"], len(result["source"]["sha256"])) == (True, 64)
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    assert script.main([str(broken), "--out", str(out)]) == 0
    assert json.loads(out.read_text("utf-8"))["refusal"]["code"] == "invalid-package"


def test_a_heading_word_draws_in_capitals_is_found_as_drawn() -> None:
    """A heading style with ``w:caps`` over a heading typed in lower case (as real labels have)."""
    # Section 1's template heading is in capitals ("1. NAME OF THE MEDICINAL PRODUCT").
    typed = next(iter(forms(SECTIONS["smpc.1"]))).lower()
    in_capitals = Paragraph(typed, None, None, None, marks=(Mark(0, len(typed), "caps"),))
    small = Paragraph(typed, None, None, None, marks=(Mark(0, len(typed), "smallCaps"),))
    paragraphs = _skeleton()
    at = next(i for i, p in enumerate(paragraphs) if line(p) == typed.upper())
    paragraphs[at] = in_capitals
    found = {s["key"]: s for s in structure(paragraphs, REGISTRY, MAPPING)["sections"]}
    assert (found["smpc.1"]["status"], found["smpc.1"]["heading"]) == ("mapped", at)
    assert line(in_capitals) == typed.upper()
    paragraphs[at] = small
    found = {s["key"]: s for s in structure(paragraphs, REGISTRY, MAPPING)["sections"]}
    assert found["smpc.1"]["status"] == "missing"


def test_capitals_are_the_word_oracles_capitals() -> None:
    """Held equal, character by character, to the copy the label reader holds to Word's answers."""
    sample = "".join(chr(c) for c in range(0x20, 0x2200) if chr(c).isprintable())
    assert capitals(sample) == word._word_capitals(sample)
