"""A package leaflet's sections, found by the QRD template's own headings (``zone_a.leaflet``).

The synthetic paragraphs are built here from the registry's own forms, with "Zorvex" for X.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from label_docx.reader import Numbering, Paragraph

from zone_a import word_epi
from zone_a.certified import Body
from zone_a.leaflet import forms, key, leaflets, structure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / "qrd" / "registry" / "cap-pl-en-10.4.json").read_text("utf-8"))
MAPPING = json.loads((ROOT / "fhir" / "mappings" / "cap-pl-en.json").read_text("utf-8"))
HEADS = {h["key"]: h for h in [*REGISTRY["sections"], *REGISTRY["headings"]]}
NAME = "Zorvex"
CONTENTS = [
    "1. What X is and what it is used for",
    "2. What you need to know before you take X",
    "3. How to take X",
    "4. Possible side effects",
    "5. How to store X",
    "6. Contents of the pack and other information",
]
# Each numbered section's heading line and its required named sections' lines, as a label writes
# them, X for the name.
TEMPLATE = [
    ("pl.1", CONTENTS[0], []),
    (
        "pl.2",
        CONTENTS[1],
        [
            "Do not take X",
            "Warnings and precautions",
            "Other medicines and X",
            "Pregnancy and breast-feeding",
            "Driving and using machines",
        ],
    ),
    ("pl.3", CONTENTS[2], []),
    ("pl.4", CONTENTS[3], ["Reporting of side effects"]),
    ("pl.5", CONTENTS[4], []),
    (
        "pl.6",
        CONTENTS[5],
        [
            "What X contains",
            "What X looks like and contents of the pack",
            "Marketing Authorisation Holder and Manufacturer",
            "This leaflet was last revised in",
        ],
    ),
]


def _p(text: str, label: str | None = None) -> Paragraph:
    numbering = None if label is None else Numbering(1, 0, label, "tab")
    return Paragraph(text.replace("X", NAME), None, numbering, None)


def _leaflet(replace: dict[str, str] | None = None, extra: list[str] | None = None) -> list[str]:
    """A leaflet that follows the template: each heading with a paragraph of text after it."""
    replace = replace or {}
    out = [
        "B. PACKAGE LEAFLET",
        "Package leaflet: Information for the patient",
        "X 10 mg tablets",
        "What is in this leaflet",
        *CONTENTS,
    ]
    for _, heading, named in TEMPLATE:
        out += [replace.get(heading, heading), "Some text."]
        for line in named:
            out += [replace.get(line, line), "More text."]
    return out + (extra or [])


def _paragraphs(lines: list[str]) -> list[Paragraph]:
    return [_p(line) for line in lines]


def _by_key(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["key"]: s for s in result["sections"]}


def test_a_leaflet_that_follows_the_template_is_ready_and_named() -> None:
    lines = _leaflet()
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert result["ready"], result["summary"]
    assert result["name"] == NAME
    sections = _by_key(result)
    assert {s["status"] for s in sections.values()} == {"mapped"}
    assert sections["pl"]["heading"] == 0
    # The list of the leaflet's sections is the root's text; the headings come after it.
    assert sections["pl.1"]["heading"] == lines.index(CONTENTS[0], 5)
    assert sections["pl"]["paragraphs"] == list(range(1, 10))
    assert sections["pl.6.holder"]["code"] == "200000029917"


def test_the_template_wording_with_its_choices() -> None:
    for wording in (
        "Pregnancy, breast-feeding and fertility",
        "Pregnancy and breast\u2011feeding",
        "Pregnancy and  breast-feeding",
    ):
        result = structure(
            _paragraphs(_leaflet({"Pregnancy and breast-feeding": wording})), REGISTRY, MAPPING
        )
        assert _by_key(result)["pl.2.pregnancy"]["status"] == "mapped", wording
    for wording in ("Pregnancy and breastfeeding", "Pregnancy , breast-feeding"):
        result = structure(
            _paragraphs(_leaflet({"Pregnancy and breast-feeding": wording})), REGISTRY, MAPPING
        )
        assert _by_key(result)["pl.2.pregnancy"]["status"] == "missing", wording


def test_the_holder_alone_heads_its_section() -> None:
    lines = _leaflet(
        {"Marketing Authorisation Holder and Manufacturer": "Marketing Authorisation Holder"}
    )
    lines.insert(lines.index("Marketing Authorisation Holder") + 2, "Manufacturer")
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    holder = _by_key(result)["pl.6.holder"]
    assert holder["status"] == "mapped"
    assert lines.index("Manufacturer") in holder["paragraphs"]
    for wording in ("Marketing Authorisation Holder:", "Manufacturer"):
        result = structure(
            _paragraphs(_leaflet({"Marketing Authorisation Holder and Manufacturer": wording})),
            REGISTRY,
            MAPPING,
        )
        assert _by_key(result)["pl.6.holder"]["status"] == "missing", wording


def test_a_heading_ending_in_a_fill_in_is_found_by_its_text_before_it() -> None:
    assert forms(HEADS["pl.6.revised"]) == (set(), "This leaflet was last revised in")
    for wording in (
        "This leaflet was last revised in",
        "This leaflet was last revised in.",
        "This leaflet was last revised in 06/2026.",
        "This leaflet was last revised in {MM/YYYY}",
    ):
        lines = _leaflet({"This leaflet was last revised in": wording})
        assert (
            _by_key(structure(_paragraphs(lines), REGISTRY, MAPPING))["pl.6.revised"]["status"]
            == "mapped"
        ), wording
    lines = _leaflet({"This leaflet was last revised in": "This leaflet was last revisedin"})
    assert (
        _by_key(structure(_paragraphs(lines), REGISTRY, MAPPING))["pl.6.revised"]["status"]
        == "missing"
    )


def test_a_named_section_is_found_only_inside_its_numbered_section() -> None:
    lines = _leaflet()
    # "Warnings and precautions" once more, in section 4: text there.
    at = lines.index(CONTENTS[3], 10) + 1
    lines.insert(at, "Warnings and precautions")
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert result["ready"]
    assert at in _by_key(result)["pl.4"]["paragraphs"]


def test_an_optional_named_section_stays_text() -> None:
    lines = _leaflet()
    at = lines.index(CONTENTS[2], 10) + 2
    lines.insert(at, "If you take more X than you should")
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert "pl.3.toomuch" not in _by_key(result)
    assert at in _by_key(result)["pl.3"]["paragraphs"]


def test_a_wording_the_template_does_not_have_is_for_a_person() -> None:
    given = "2. What you need to know before you are given X"
    result = structure(_paragraphs(_leaflet({CONTENTS[1]: given})), REGISTRY, MAPPING)
    assert not result["ready"]
    section = _by_key(result)["pl.2"]
    assert section["status"] == "missing"
    # The heading line is a candidate; its entry in the list of sections is not.
    lines = _leaflet({CONTENTS[1]: given})
    assert section["candidates"] == [{"paragraph": lines.index(given, 10), "why": "number"}]


def test_a_person_names_a_numbered_heading_and_its_named_sections_are_found() -> None:
    given = "2. What you need to know before you are given X"
    lines = _leaflet({CONTENTS[1]: given})
    at = lines.index(given, 10)
    result = structure(_paragraphs(lines), REGISTRY, MAPPING, {"pl.2": at})
    assert result["ready"], result["summary"]
    sections = _by_key(result)
    assert sections["pl.2"]["status"] == "assigned"
    assert sections["pl.2.donottake"]["status"] == "mapped"
    with pytest.raises(ValueError, match="assigned twice"):
        structure(_paragraphs(lines), REGISTRY, MAPPING, {"pl.2": at, "pl.3": at})


def test_numbered_steps_are_neither_headings_nor_candidates() -> None:
    lines = _leaflet()
    at = lines.index(CONTENTS[2], 10) + 1
    lines[at:at] = ["1. Wash your hands.", "", "2. Clean the skin.", "3. Inject."]
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert result["ready"]
    assert all(not s["candidates"] for s in result["sections"])


def test_a_leaflet_whose_section_1_lines_name_two_medicines_has_no_name() -> None:
    lines = _leaflet()
    lines[lines.index(CONTENTS[0])] = "1. What Other is and what it is used for"
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert result["name"] is None
    assert not result["ready"]
    statuses = {s["key"]: s["status"] for s in result["sections"]}
    assert statuses["pl.1"] == statuses["pl.2.donottake"] == "missing"
    # A heading without X is found; with no name the list of sections is not told from steps, so
    # its "4. Possible side effects" is found too, and the two are for a person.
    assert statuses["pl.4"] == "duplicate"


def test_a_document_with_two_leaflets_gives_each_its_own() -> None:
    first = _leaflet()
    second = _leaflet()[1:]  # the root line is shared
    paragraphs = _paragraphs([*first, *second])
    parts, why = leaflets(paragraphs, REGISTRY)
    assert why is None
    assert parts == [(0, len(first), None), (len(first), len(paragraphs), 0)]
    for part in parts:
        result = structure(paragraphs, REGISTRY, MAPPING, part=part)
        assert result["ready"], result["summary"]
    later = structure(paragraphs, REGISTRY, MAPPING, part=parts[1])
    assert _by_key(later)["pl"]["heading"] == 0
    assert _by_key(later)["pl"]["paragraphs"][0] == len(first)
    with pytest.raises(ValueError, match="several leaflets"):
        structure(paragraphs, REGISTRY, MAPPING)


def test_a_document_without_its_root_line_once_is_for_a_person() -> None:
    lines = _leaflet()
    assert leaflets(_paragraphs(lines[1:]), REGISTRY) == (
        [],
        "0 lines 'B. PACKAGE LEAFLET', expected one",
    )
    assert leaflets(_paragraphs([*lines, lines[0]]), REGISTRY)[1] == (
        "2 lines 'B. PACKAGE LEAFLET', expected one"
    )


def test_a_ready_leaflet_builds() -> None:
    paragraphs = _paragraphs(_leaflet())
    result = structure(paragraphs, REGISTRY, MAPPING)
    built = word_epi.sections(Body(tuple(paragraphs), ()), result, REGISTRY)
    assert built["refused"] == 0
    root = next(s for s in built["sections"] if s["key"] == "pl")
    assert root["title"] == "B. PACKAGE LEAFLET"
    assert "What is in this leaflet" in root["narrative"]
    holder = next(s for s in built["sections"] if s["key"] == "pl.6.holder")
    assert holder["title"] == "Marketing Authorisation Holder and Manufacturer"


def test_the_line_key_collapses_spaces_and_reads_a_non_breaking_hyphen() -> None:
    assert key(" Pregnancy\u00a0and\tbreast\u2011feeding ") == "Pregnancy and breast-feeding"


def test_every_form_of_every_heading_names_x_only_as_a_word() -> None:
    # A form that wrote "X" inside a word would put the name there.
    for head in HEADS.values():
        if head["key"] == "pl.2.excipients":
            # "X contains {name the excipient(s)}" ends in a fill-in no line can match exactly.
            with pytest.raises(ValueError, match="not the date"):
                forms(head)
            continue
        lines, _ = forms(head)
        for line in lines:
            assert all(word == "X" or "X" not in word for word in line.split()), line


# ---- the independent review's cases (2026-10-06) -------------------------------------------------


def test_the_leaflet_ends_at_the_next_annex() -> None:
    after = ["ANNEX IV", "SCIENTIFIC CONCLUSIONS", "The CHMP recommends ..."]
    end = len(_leaflet())
    # As written: _p would write the name for the X of "ANNEX".
    paragraphs = [*_paragraphs(_leaflet()), *(Paragraph(t, None, None, None) for t in after)]
    assert leaflets(paragraphs, REGISTRY) == ([(0, end, None)], None)
    result = structure(paragraphs, REGISTRY, MAPPING)
    assert result["ready"]
    assert result["end"] == end
    assert all(i < end for s in result["sections"] for i in s["paragraphs"])


def test_the_revision_date_heading_takes_a_date_or_its_placeholder_only() -> None:
    def status(wording: str) -> str:
        lines = _leaflet({"This leaflet was last revised in": wording})
        return str(
            _by_key(structure(_paragraphs(lines), REGISTRY, MAPPING))["pl.6.revised"]["status"]
        )

    for wording in (
        "This leaflet was last revised in June 2026.",
        "This leaflet was last revised in <{MM/YYYY}><{month YYYY}>.",
        "This leaflet was last revised in month/YYYY",
        "This leaflet was last revised in .",
    ):
        assert status(wording) == "mapped", wording
    for wording in (
        "This leaflet was last revised in accordance with the CHMP opinion; ask your doctor.",
        "This leaflet was last revised in Other sources of information",
        "This leaflet was last revised in 2026.",
    ):
        assert status(wording) == "missing", wording


def test_steps_before_a_heading_do_not_take_it_into_their_list() -> None:
    lines = _leaflet()
    at = lines.index(CONTENTS[3], 10)
    lines[at:at] = ["1. Wash your hands.", "2. Clean the skin.", "3. Dispose of the pen."]
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert result["ready"]
    assert _by_key(result)["pl.4"]["heading"] == at + 3
    lines = _leaflet()
    at = lines.index(CONTENTS[4], 10)
    lines[at + 1] = "6 months after first opening, throw it away."
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert _by_key(result)["pl.5"]["heading"] == at


def test_steps_at_the_end_of_a_leaflet_are_a_list_and_a_seventh_is_no_step() -> None:
    result = structure(
        _paragraphs(_leaflet(extra=["1. Open the box.", "2. Take out the pen."])), REGISTRY, MAPPING
    )
    assert result["ready"]
    assert all(not s["candidates"] for s in result["sections"])
    lines = _leaflet()
    at = lines.index(CONTENTS[2], 10) + 1
    lines[at:at] = ["6. Hold the pen.", "7. Press the button."]
    result = structure(_paragraphs(lines), REGISTRY, MAPPING)
    assert _by_key(result)["pl.6"]["candidates"] == [{"paragraph": at, "why": "number"}]


def test_a_choice_of_two_takes_at_least_one() -> None:
    assert forms(HEADS["pl.2.donottake"])[0] == {
        "Do not take X",
        "Do not use X",
        "Do not take use X",
    }
    assert "Package leaflet: Information for the" not in forms(REGISTRY["leafletTitle"])[0]
    pregnancy = forms(HEADS["pl.2.pregnancy"])[0]
    assert "Pregnancy breast-feeding" not in pregnancy
    assert "Pregnancy and breast-feeding and fertility" in pregnancy


def test_a_name_with_regular_expression_characters_is_taken_as_written() -> None:
    for called in ("Zor(vex)+", "Zor\\1vex", "a.b*c"):
        lines = [line.replace(NAME, called) for line in _leaflet()]
        paragraphs = [Paragraph(text.replace("X", called), None, None, None) for text in lines]
        result = structure(paragraphs, REGISTRY, MAPPING)
        assert (result["name"], result["ready"]) == (called, True), called


def test_two_sections_sharing_a_form_are_refused() -> None:
    registry = json.loads(json.dumps(REGISTRY))
    heads = {h["key"]: h for h in registry["headings"]}
    heads["pl.2.driving"]["title"] = heads["pl.2.warnings"]["title"]
    with pytest.raises(ValueError, match="a heading of two sections"):
        structure(_paragraphs(_leaflet()), registry, MAPPING)


def test_a_heading_with_a_choice_before_its_date_is_refused() -> None:
    head = {
        "title": [
            {"kind": "optional", "value": [{"kind": "text", "value": "a"}]},
            {"kind": "fill", "value": "MM/YYYY"},
        ]
    }
    with pytest.raises(ValueError, match="a choice before its fill-in"):
        forms(head)
