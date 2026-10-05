"""Who a label is for, from the label itself (``zone_a.product``)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from label_docx.reader import Paragraph

from zone_a.certified import read_body
from zone_a.product import match, propose
from zone_a.structure import structure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / "qrd/registry/cap-smpc-en-10.4.json").read_text("utf-8"))
MAPPING = json.loads((ROOT / "fhir/mappings/cap-smpc-en.json").read_text("utf-8"))
FIXTURES = Path(__file__).parent / "fixtures" / "word-smpc"
_SPEC = importlib.util.spec_from_file_location(
    "word_fixtures", Path(__file__).parents[1] / "scripts" / "word_fixtures.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
word_fixtures = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(word_fixtures)


def _label(eight: list[str]) -> tuple[list[Paragraph], dict[str, Any]]:
    """Sections 1, 7 and 8, each its heading then its paragraphs."""
    texts = ["1. NAME", "Medicine 10 mg tablets", "7. HOLDER", "Holder Ltd", "8. NUMBERS", *eight]
    paragraphs = [Paragraph(t, None, None, None) for t in texts]
    structured = {
        "sections": [
            {"key": "smpc.1", "paragraphs": [1]},
            {"key": "smpc.7", "paragraphs": [3]},
            {"key": "smpc.8", "paragraphs": list(range(5, len(texts)))},
        ]
    }
    return paragraphs, structured


def test_each_number_is_taken_exactly_with_its_product() -> None:
    paragraphs, structured = _label(
        [
            "EU/1/21/1576/001\u00a0",
            "EU/1/12/808/021-040 (bottles)",
            "EU/1/12/7800/005;EU/1/12/7800/006",
        ]
    )
    proposal = propose(paragraphs, structured)
    assert [n["number"] for n in proposal["numbers"]] == [
        "EU/1/21/1576/001",
        "EU/1/12/808/021-040",
        "EU/1/12/7800/005",
        "EU/1/12/7800/006",
    ]
    assert proposal["products"] == ["EU/1/12/7800", "EU/1/12/808", "EU/1/21/1576"]
    assert proposal["unread"] == []
    assert (proposal["name"], proposal["holder"]) == ([1], [3])
    first = proposal["numbers"][0]
    assert paragraphs[first["paragraph"]].text[first["start"] : first["end"]] == first["number"]


@pytest.mark.parametrize(
    "text",
    [
        "EU/1/12/808/001\u2013005",  # an en dash: a run read as it is not written
        "EU/1/12/808/040-021",  # a run that runs backwards
        "EU/1/12/808/01",  # a presentation of two digits
        "EU/2/12/808/001",  # another procedure
        "XEU/1/12/808/001",  # inside a longer token
        "EU/1/12/808/001/2",
    ],
)
def test_anything_else_that_starts_eu_is_unread_never_guessed(text: str) -> None:
    proposal = propose(*_label([text]))
    assert proposal["unread"]
    with pytest.raises(ValueError, match="not all read"):
        match(proposal, [])


def test_a_label_is_the_product_whose_numbers_are_exactly_its_own() -> None:
    proposal = propose(*_label(["EU/1/12/808/001", "EU/1/12/808/002"]))
    same = {"id": "p-1", "eu": {"products": ["EU/1/12/808"]}}
    more = {"id": "p-2", "eu": {"products": ["EU/1/12/808", "EU/1/12/809"]}}
    assert match(proposal, [more, same]) is same
    assert match(proposal, [more]) is None
    with pytest.raises(ValueError, match="2 products"):
        match(proposal, [same, {**same, "id": "p-3"}])
    with pytest.raises(ValueError, match="not all read"):
        match(propose(*_label(["no number here"])), [same])


def test_the_capsules_and_the_tablets_of_one_authorisation_are_one_product() -> None:
    products = {}
    for name in ("imatinib-teva-smpc-en", "imatinib-teva-tablets-smpc-en", "jentadueto-smpc-en"):
        body = read_body((FIXTURES / f"{name}.docx").read_bytes())
        structured = structure(body.paragraphs, REGISTRY, MAPPING, word_fixtures.ASSIGNED.get(name))
        proposal = propose(body.paragraphs, structured)
        assert proposal["unread"] == []
        products[name] = proposal["products"]
    assert products["imatinib-teva-smpc-en"] == products["imatinib-teva-tablets-smpc-en"]
    assert products["imatinib-teva-smpc-en"] == ["EU/1/12/808"]
    assert products["jentadueto-smpc-en"] == ["EU/1/12/780"]
