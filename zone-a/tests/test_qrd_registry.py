"""The QRD template registry: pinned sources, the bracket grammar, the registry and headings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from label_docx.reader import Mark, Numbering, Paragraph

from zone_a.qrd.headings import HeadingPatternError, forms, index, match_heading
from zone_a.qrd.pattern import UnbalancedTemplateError, parse, render
from zone_a.qrd.registry import (
    ERRATA,
    RegistryError,
    Source,
    _check,
    _check_errata,
    _guard,
    _items,
    _split_trailer,
    build,
    build_appendix_ii,
    serialise,
)

ROOT = Path(__file__).resolve().parents[2]
QRD = ROOT / "qrd"
LOCK = json.loads((QRD / "sources.lock.json").read_text(encoding="utf-8"))
REGISTRY: dict[str, Any] = json.loads(
    (QRD / "registry" / "cap-smpc-en-10.4.json").read_text(encoding="utf-8")
)
MAPPING = json.loads((ROOT / "fhir" / "mappings" / "cap-smpc-en.json").read_text(encoding="utf-8"))


# --- pinned sources ---------------------------------------------------------------------


def test_every_pinned_file_matches_its_lock_entry_and_nothing_else_is_there() -> None:
    files = {path.name for path in (QRD / "sources").iterdir()}
    assert files == {entry["file"] for entry in LOCK["sources"]}
    for entry in LOCK["sources"]:
        data = (QRD / "sources" / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]
        assert len(data) == entry["bytes"], entry["file"]
        assert entry["url"].endswith("/" + entry["file"])


# --- the bracket grammar ----------------------------------------------------------------


def _all_items(registry: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for section in registry["sections"] for item in section["items"]]
    items += registry["documentStatements"] + registry["appendices"]["III"]["items"]
    return items


def test_every_parsed_item_renders_back_to_its_source() -> None:
    for item in _all_items(REGISTRY):
        expected = ERRATA[item["source"]][0] if "erratum" in item else item["source"]
        assert render(item["pattern"]) + item.get("trailer", "") == expected


def test_a_less_than_sign_followed_by_space_is_not_a_bracket() -> None:
    tokens = parse("<Common (\u2265\u00a01/100 to <\u00a01/10)>")
    assert tokens == [
        {
            "kind": "optional",
            "value": [{"kind": "text", "value": "Common (\u2265\u00a01/100 to <\u00a01/10)"}],
        }
    ]


def test_nested_fill_guidance_and_optional_tokens() -> None:
    assert parse("<{X} has <no> [see] {a {b}}.>") == [
        {
            "kind": "optional",
            "value": [
                {"kind": "fill", "value": "X"},
                {"kind": "text", "value": " has "},
                {"kind": "optional", "value": [{"kind": "text", "value": "no"}]},
                {"kind": "text", "value": " "},
                {"kind": "guidance", "value": "see"},
                {"kind": "text", "value": " "},
                {"kind": "fill", "value": "a {b}"},
                {"kind": "text", "value": "."},
            ],
        }
    ]
    assert parse("a > b") == [{"kind": "text", "value": "a > b"}]


@pytest.mark.parametrize("text", ["<open", "{open", "[open", "close}", "close]", "<a <b>"])
def test_unbalanced_strings_are_not_parsed(text: str) -> None:
    with pytest.raises(UnbalancedTemplateError):
        parse(text)


# --- the registry -----------------------------------------------------------------------


def test_the_committed_registry_is_what_the_sources_build() -> None:
    expected = serialise(build(QRD / "sources", LOCK))
    current = (QRD / "registry" / "cap-smpc-en-10.4.json").read_text(encoding="utf-8")
    assert current == expected, "regenerate with zone-a/scripts/generate_qrd_registry.py"


def test_every_appendix_i_pattern_and_heading_title_renders_back() -> None:
    for entry in REGISTRY["appendices"]["I"]["entries"]:
        if entry["pattern"] is not None:
            assert render(entry["pattern"]) == "\n".join(entry["paragraphs"])
    for section in REGISTRY["sections"]:
        assert render(section["title"]).strip() in section["source"]


def test_appendix_iii_statements_are_all_optional_with_their_markers_split_off() -> None:
    items = REGISTRY["appendices"]["III"]["items"]
    assert {(item["kind"], item["optional"]) for item in items} == {("statement", True)}
    assert [item.get("note") for item in items] == [
        None, None, None, "*", None, "**", None, "****", "****", "****", None, "*****",
    ]  # fmt: skip
    assert items[0]["connector"] == "or"


def test_only_three_smpc_items_span_paragraphs() -> None:
    spanning = [
        item["source"].split("\n", 1)[0]
        for section in REGISTRY["sections"]
        for item in section["items"]
        if "\n" in item["source"]
    ]
    assert spanning == [
        "<Traceability",
        "<This medicinal product has been authorised under a so-called \u2018conditional "
        "approval\u2019 scheme. This means that further evidence on this medicinal product is "
        "awaited.",
        "<This medicinal product has been authorised under \u2018exceptional circumstances\u2019. "
        "This means that <due to the rarity of the disease> <for scientific reasons> <for ethical "
        "reasons> it has not been possible to obtain complete information on this medicinal "
        "product.",
    ]


def test_the_black_triangle_and_the_not_printed_highlights_are_kept() -> None:
    monitoring = REGISTRY["documentStatements"][0]
    assert monitoring["source"].startswith("<\ufffcThis medicinal product is subject")
    marked = {
        item["source"][m["start"] : m["end"]]
        for section in REGISTRY["sections"]
        for item in section["items"]
        for m in item.get("marks", [])
    }
    assert marked == {"the national reporting system listed in Appendix V", "not yet assigned"}


def test_the_registry_names_its_sources_by_hash() -> None:
    assert {s["sha256"] for s in REGISTRY["sources"]} == {s["sha256"] for s in LOCK["sources"]}
    assert REGISTRY["template"]["version"] == "10.4"
    assert REGISTRY["template"]["status"] == "adopted"


def test_the_section_tree_is_the_qrd_10_4_smpc() -> None:
    keys = [section["key"] for section in REGISTRY["sections"]]
    assert keys == [
        "smpc.1", "smpc.2", "smpc.2.1", "smpc.2.2", "smpc.3", "smpc.4",
        *(f"smpc.4.{n}" for n in range(1, 10)),
        "smpc.5", "smpc.5.1", "smpc.5.2", "smpc.5.3", "smpc.6",
        *(f"smpc.6.{n}" for n in range(1, 7)),
        "smpc.7", "smpc.8", "smpc.9", "smpc.10", "smpc.11", "smpc.12",
    ]  # fmt: skip
    optional = {s["key"]: s["guidance"] for s in REGISTRY["sections"] if s["optional"]}
    assert optional == {
        "smpc.2.1": "For advanced therapy products only",
        "smpc.2.2": "For advanced therapy products only",
        "smpc.11": None,
        "smpc.12": None,
    }


def test_exactly_one_erratum_is_applied_and_it_is_recorded() -> None:
    corrected = [item for item in _all_items(REGISTRY) if "erratum" in item]
    assert len(corrected) == len(ERRATA) == 1
    assert corrected[0]["source"] in ERRATA


def test_appendix_i_keeps_unbalanced_entries_verbatim_without_a_pattern() -> None:
    entries = REGISTRY["appendices"]["I"]["entries"]
    assert [e["id"] for e in entries] == [
        *(f"pregnancy.{n}" for n in range(1, 10)),
        *(f"lactation.{n}" for n in range(1, 4)),
    ]
    unbalanced = [e["id"] for e in entries if not e["bracketsBalanced"]]
    assert unbalanced == ["pregnancy.1", "pregnancy.2", "pregnancy.3", "pregnancy.6"]
    assert all(e["pattern"] is None for e in entries if not e["bracketsBalanced"])


def test_appendix_ii_codes_and_terms() -> None:
    groups = REGISTRY["appendices"]["II"]["groups"]
    frequency = groups["MedDRA frequency convention"]
    assert [row["code"] for row in frequency] == ["001", "002", "003", "004", "005", "006"]
    assert frequency[0]["text"] == "<Very common (\u2265\u00a01/10)>"
    organ_classes = groups["MedDRA- system organ class database"]
    assert [row["code"] for row in organ_classes] == [f"{n:03d}" for n in range(7, 34)]


def test_appendix_iii_storage_statements_and_notes() -> None:
    appendix = REGISTRY["appendices"]["III"]
    assert len(appendix["items"]) == 12
    assert appendix["items"][1]["source"] == "<Store below <25\u00a0\u00b0C> <30\u00a0\u00b0C>.>"
    assert [note.split(" ", 1)[0] for note in appendix["notes"]] == [
        "*",
        "**",
        "***",
        "****",
        "*****",
    ]


# --- headings ---------------------------------------------------------------------------

TABLE = index(REGISTRY)


def _mapping_rules(rule: dict[str, Any]) -> list[dict[str, Any]]:
    return [rule, *(r for child in rule.get("children", []) for r in _mapping_rules(child))]


def test_every_numbered_heading_of_the_current_mapping_is_a_registry_heading() -> None:
    numbered = [
        rule
        for rule in _mapping_rules(MAPPING["root"])
        if rule["sourceKey"].count(".") in (1, 2) and rule["sourceKey"].split(".")[-1].isdigit()
    ]
    assert len(numbered) == 28
    for rule in numbered:
        found = match_heading(rule["title"], TABLE)
        assert found is not None, rule["title"]
        assert found.key == rule["sourceKey"]


def test_the_mapping_names_the_optional_parts_of_6_5_and_6_6() -> None:
    # The mapping's titles include the optional segments. For 6.5 that segment is marked in the
    # annotated template as being for advanced therapy medicinal products only; see
    # docs/design/qrd-registry.md, "What this shows about the current mapping".
    six_five = match_heading(
        "6.5 Nature and contents of container and special equipment for use, administration or "
        "implantation",
        TABLE,
    )
    assert six_five is not None
    assert six_five.segments == (True,)
    plain = match_heading("6.5\tNature and contents of container", TABLE)
    assert plain is not None
    assert plain.segments == (False,)


def test_every_named_subsection_of_the_mapping_is_a_registry_subheading() -> None:
    sections = {section["key"]: section for section in REGISTRY["sections"]}
    for rule in _mapping_rules(MAPPING["root"]):
        if rule["sourceKey"].split(".")[-1].isdigit() or rule["sourceKey"] == "smpc":
            continue
        parent = sections[rule["sourceKey"].rsplit(".", 1)[0]]
        subheadings = {
            render(item["pattern"]).strip()
            for item in parent["items"]
            if item["kind"] == "subheading"
        }
        assert rule["title"] in subheadings, rule["sourceKey"]


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("4.1\tTherapeutic indications", "smpc.4.1"),
        ("4.1  Therapeutic\u00a0indications ", "smpc.4.1"),
        ("5.1 \tPharmacodynamic properties", "smpc.5.1"),
        ("1. NAME OF THE MEDICINAL PRODUCT", "smpc.1"),
        ("8. MARKETING AUTHORISATION NUMBER(S)", "smpc.8"),
        ("11. DOSIMETRY", "smpc.11"),
        ("6.6 Special precautions for disposal", "smpc.6.6"),
    ],
)
def test_headings_are_recognised(line: str, key: str) -> None:
    found = match_heading(line, TABLE)
    assert found is not None
    assert found.key == key


@pytest.mark.parametrize(
    "line",
    [
        "4.1 therapeutic indications",
        "4.1. Therapeutic indications",
        "1 NAME OF THE MEDICINAL PRODUCT",
        "4.10 Something",
        "Therapeutic indications",
        "6.5 Nature and contents of container and special equipment",
    ],
)
def test_near_misses_are_not_headings(line: str) -> None:
    assert match_heading(line, TABLE) is None


@pytest.mark.parametrize("title", ["Title <and {X}>", "Title <[guidance]>", "Title <and <more>>"])
def test_a_heading_title_a_form_cannot_expand_is_refused_at_any_depth(title: str) -> None:
    # A fill-in inside an optional segment would be written as its placeholder's name ("Title
    # and X"), and a nested segment has no flag of its own (it raised IndexError).
    section = {"key": "smpc.4.1", "number": "4.1", "dotted": False, "title": parse(title)}
    with pytest.raises(HeadingPatternError):
        forms(section)


# --- refusals of the build ----------------------------------------------------------------


def _paragraph(text: str, *marks: Mark, table: tuple[int, int, int] | None = None) -> Paragraph:
    return Paragraph(text=text, style=None, numbering=None, table=table, marks=marks)


@pytest.mark.parametrize(
    ("paragraph", "refused"),
    [
        (_paragraph("10", Mark(1, 2, "superscript")), True),
        (_paragraph("gone", Mark(0, 4, "strike")), True),
        (_paragraph("Text", Mark(0, 4, "caps")), True),
        (_paragraph("TEXT", Mark(0, 4, "caps")), False),
        (_paragraph("yellow", Mark(0, 6, "highlight-yellow")), True),
        (_paragraph("shade", Mark(0, 5, "shading-FFFF00")), True),
        (_paragraph("shade", Mark(0, 5, "shading-solid-auto-000000")), True),
        (Paragraph("x", None, Numbering(3, 0), None), True),
        (Paragraph("x", None, Numbering(0, 0), None), False),
        (_paragraph("abc", Mark(0, 3, "rtl")), True),
        (_paragraph("abc", Mark(0, 3, "faint")), True),
        # An underline changes a sign, not a word, a quotation or the template's brackets.
        (_paragraph("Breast-feeding", Mark(0, 14, "underline")), False),
        (_paragraph("<Traceability>", Mark(0, 14, "underline")), False),
        (_paragraph("“Pregnancy”", Mark(0, 11, "underline")), False),
        (_paragraph("CrCl ≤ 30", Mark(5, 6, "underline")), True),
        (_paragraph("+", Mark(0, 1, "underline")), True),
        (_paragraph("2-3", Mark(0, 3, "underline")), True),
        # Judged on the drawn text: a dash between digits, a look-alike of "<", an ordinal.
        (_paragraph("2-3", Mark(1, 2, "underline")), True),
        (_paragraph("\u02c2 30", Mark(0, 1, "underline")), True),
        (_paragraph("1a", Mark(1, 2, "underline")), True),
        (Paragraph("run on", None, None, None, mark_hidden=True), True),
        (Paragraph("", None, None, None, mark_hidden=True), False),
    ],
)
def test_a_source_paragraph_with_a_mark_that_changes_it_is_refused(
    paragraph: Paragraph, refused: bool
) -> None:
    if refused:
        with pytest.raises(RegistryError):
            _check(paragraph, "test")
    else:
        _check(paragraph, "test")


def test_grey_marks_are_kept_on_items_and_refused_where_the_registry_drops_marks() -> None:
    for kind in ("highlight-lightGray", "shading-D9D9D9"):
        grey = _paragraph("<grey>", Mark(0, 6, kind))
        _check(grey, "item", keeps_marks=True)
        with pytest.raises(RegistryError):
            _check(grey, "heading")
        assert _items([grey], "test")[0]["marks"] == [{"start": 0, "end": 6, "kind": kind}]


def test_a_literal_greater_than_sign_is_refused_at_any_depth() -> None:
    with pytest.raises(RegistryError):
        _guard(parse("<patients > 65 years>"), "test")
    with pytest.raises(RegistryError):
        _guard(parse("<a <b> c > d>"), "test")
    _guard(parse("<Common (\u2265 1/100 to < 1/10)>"), "test")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<Store below 25\u00b0C.>*", ("<Store below 25\u00b0C.>", "*", None)),
        ("<Do not freeze.>**** ", ("<Do not freeze.>", "****", None)),
        ("<Store in a refrigerator.> or", ("<Store in a refrigerator.>", None, "or")),
        ("listed in Appendix V.*", ("listed in Appendix V.", "*", None)),
        ("a*b", ("a*b", None, None)),
        ("Take it or", ("Take it or", None, None)),
    ],
)
def test_footnote_markers_and_connectors_are_split_off(
    text: str, expected: tuple[str, str | None, str | None]
) -> None:
    assert _split_trailer(text) == expected


def test_a_bracket_left_open_at_the_end_of_a_section_is_refused() -> None:
    with pytest.raises(RegistryError):
        _items([_paragraph("<Opened"), _paragraph("and never closed")], "test")


def _source(paragraphs: list[Paragraph]) -> Source:
    return Source(file="synthetic.docx", sha256="0" * 64, paragraphs=paragraphs)


def _rows(*rows: tuple[str, ...]) -> list[Paragraph]:
    return [
        _paragraph(text, table=(0, row, cell))
        for row, cells in enumerate(rows)
        for cell, text in enumerate(cells)
    ]


def test_appendix_ii_reads_a_well_formed_table() -> None:
    table = _rows(("Ref", "EN"), ("", "[Frequency]"), ("001", "Very common"))
    assert build_appendix_ii(_source(table)) == {
        "Frequency": [{"code": "001", "text": "Very common"}]
    }


@pytest.mark.parametrize(
    "rows",
    [
        [("Ref", "EN"), ("", "[Frequency]"), ("001", "Very common", "Tr\u00e8s fr\u00e9quent")],
        [("Ref", "FR"), ("", "[Frequency]"), ("001", "Very common")],
        [("", "[Frequency]"), ("001", "Very common"), ("Ref", "EN")],
        [("Ref", "EN"), ("001", "Very common")],
        [("Ref", "EN"), ("", "[Frequency]"), ("002", "Common"), ("001", "Very common")],
        [("Ref", "EN"), ("", "[Frequency]"), ("1", "Very common")],
        [("", "[Frequency]"), ("001", "Very common")],
        [("Ref", "EN"), ("", "[Frequency]"), ("001",)],
        [("Ref", "EN"), ("Ref", "EN"), ("", "[Frequency]"), ("001", "Very common")],
    ],
)
def test_appendix_ii_refuses_a_table_of_another_shape(rows: list[tuple[str, ...]]) -> None:
    with pytest.raises(RegistryError):
        build_appendix_ii(_source(_rows(*rows)))


def test_an_erratum_must_apply_exactly_once() -> None:
    source = next(iter(ERRATA))
    item = {"source": source, "erratum": ERRATA[source][1]}
    _check_errata([item])
    with pytest.raises(RegistryError):
        _check_errata([])
    with pytest.raises(RegistryError):
        _check_errata([item, item])


def test_a_blank_numbered_paragraph_among_the_storage_statements_is_refused() -> None:
    with pytest.raises(RegistryError):
        _check(Paragraph("", None, Numbering(2, 0), None), "Appendix III SmPC", keeps_marks=True)
