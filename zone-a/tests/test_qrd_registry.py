"""The QRD template registry: pinned sources, the bracket grammar, the registry and headings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from zone_a.qrd.headings import index, match_heading
from zone_a.qrd.pattern import UnbalancedTemplateError, parse, render
from zone_a.qrd.registry import ERRATA, build, serialise

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
        assert render(item["pattern"]) == expected


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
