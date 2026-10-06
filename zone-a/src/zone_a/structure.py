"""An SmPC's sections, found in its Word text by the QRD template's own headings.

A label read exactly (``zone_a.certified``) is a run of paragraphs; an ePI is a tree of coded
sections. This module puts the one into the shape of the other for the centralised SmPC in
English, using the QRD registry (``qrd/registry/``) for the headings and the section mapping
(``fhir/mappings/cap-smpc-en.json``) for the tree and each section's EMA code. It finds; a person
confirms. It never guesses a heading.

Headings. A paragraph is a section's heading when its line, the list label Word draws before it
and its text, is one of the forms the registry allows for that section (``zone_a.qrd.headings``:
spaces collapsed, everything else exact). The mapping's root title starts the SmPC, and a named
subsection (Posology, Method of administration, Reporting of suspected adverse reactions) is a
line that is exactly its title, inside the section it belongs to. Nothing else is a heading. A
line that starts with a section's number but goes on otherwise ("4.4 Warnings and precautions")
is a ``number`` candidate for that section, and a paragraph in a heading style that is no QRD
heading is a ``style`` candidate in the section it stands in: both are shown to a person, and a
section stays ``missing`` until the person names its heading (``assignments``), which makes it
``assigned``.

Text. The SmPC ends where a line is the registry's own end of it (``SMPC_END``, "ANNEX II"): a
file of the whole product information goes on with the labelling, which reuses section 1's line,
and nothing from there on (``end``) is looked at. Every paragraph with text before it belongs to
the last heading before it, recognised or assigned, or to the ``preamble`` before the first; a
heading's own paragraph is its section's heading, not its text. Nothing is moved, merged or
dropped: each paragraph is in exactly one place, by its index in the read.

Sections. Each section of the template gets one status:

- ``mapped``: its heading was found once, in the template's order, and the mapping has its code;
- ``assigned``: a person named its heading;
- ``missing``: a required section with no heading found, for a person (see its candidates);
- ``absent``: an optional one with none, which is as the template allows;
- ``duplicate``: its heading was found more than once, for a person: its text is the paragraphs
  after each;
- ``order``: its heading comes before a heading the template puts ahead of it, for a person;
- ``no-code``: the mapping has no code for it (sections 11 and 12), so an ePI cannot place it.

The structure is ``ready`` when every required section is mapped or assigned and none is
``duplicate``, ``order`` or ``no-code``: the point at which a person's review can turn it into an
ePI. A person's assignment that names a section the template does not have or one whose heading
was found, a paragraph outside the read, or a paragraph that is already a heading is refused
(ValueError).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from label_docx.reader import Paragraph

from zone_a.qrd.headings import collapse, forms, index, match_heading
from zone_a.qrd.registry import SMPC_END

STRUCTURE_VERSION = "smpc-structure/1.0.4"

_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)\.?\s+\S")
_HEADING_STYLE = re.compile(r"Heading", re.IGNORECASE)
NEEDS_A_PERSON = frozenset({"missing", "duplicate", "order", "no-code"})


# How Word draws text in capitals (``w:caps``), as the label reader's Word oracle records Word's
# own answers (``label_docx.word``): a character's one capital where it has one; the micro sign and
# the small roman numerals stay as they are, a character whose capital is more than one character
# stays, and Greek iota and upsilon with dialytika and tonos lose the tonos. Held equal to the
# oracle's copy by tests/test_structure.py.
_CAPS_KEPT = frozenset("\u00b5" + "".join(chr(code) for code in range(0x2170, 0x2180)))
_CAPS_OWN = {"\u0390": "\u03aa", "\u03b0": "\u03ab"}


def capitals(text: str) -> str:
    """``text`` as Word draws it in capitals."""
    out: list[str] = []
    for character in text:
        capital = character.upper()
        if character in _CAPS_OWN:
            out.append(_CAPS_OWN[character])
        elif character in _CAPS_KEPT or len(capital) != 1:
            out.append(character)
        else:
            out.append(capital)
    return "".join(out)


def line(paragraph: Paragraph) -> str:
    """What Word shows on the paragraph's first line: its list label and its text, collapsed.

    Text in capitals (a heading style that sets ``w:caps`` over a heading typed in lower case) is
    taken as Word draws it. Small capitals are not: they draw a capital in a small size, and the
    line keeps the letters as typed, so such a heading is found by a person, not by its text.
    """
    label = paragraph.numbering.text if paragraph.numbering and paragraph.numbering.text else ""
    text = paragraph.text
    for mark in paragraph.marks:
        if mark.kind == "caps":
            text = text[: mark.start] + capitals(text[mark.start : mark.end]) + text[mark.end :]
    return collapse(f"{label} {text}")


def _nodes(registry: dict[str, Any], mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The template's sections in order: the root, each registry section, each named one."""
    codes: dict[str, dict[str, Any]] = {}
    named: dict[str, list[dict[str, Any]]] = {}

    def visit(node: Mapping[str, Any], parent: str | None) -> None:
        codes[node["sourceKey"]] = dict(node)
        # A named subsection's key ends in a word (smpc.4.2.posology), a numbered one's in a number.
        if parent is not None and not node["sourceKey"].rsplit(".", 1)[-1].isdigit():
            named.setdefault(parent, []).append(dict(node))
        for child in node.get("children", []):
            visit(child, node["sourceKey"])

    visit(mapping["root"], None)
    root = mapping["root"]
    out = [
        {
            "key": root["sourceKey"],
            "number": None,
            "title": root["title"],
            "code": root["targetCode"],
            "required": True,
            "parent": None,
        }
    ]
    for section in registry["sections"]:
        key = section["key"]
        coded = codes.get(key)
        title = next(iter(forms(section)))  # every optional segment present
        parent = key.rsplit(".", 1)[0] if section["level"] > 1 else root["sourceKey"]
        out.append(
            {
                "key": key,
                "number": section["number"],
                "title": title,
                "code": None if coded is None else coded["targetCode"],
                "required": not section["optional"],
                "parent": parent,
            }
        )
        for child in named.get(key, []):
            out.append(
                {
                    "key": child["sourceKey"],
                    "number": None,
                    "title": child["title"],
                    "code": child["targetCode"],
                    "required": bool(child.get("required", True)),
                    "parent": key,
                }
            )
    return out


def structure(
    paragraphs: Sequence[Paragraph],
    registry: dict[str, Any],
    mapping: Mapping[str, Any],
    assignments: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """The SmPC's sections in the read paragraphs, as JSON values (the module docstring)."""
    nodes = _nodes(registry, mapping)
    by_key = {n["key"]: n for n in nodes}
    order = {n["key"]: i for i, n in enumerate(nodes)}
    table = index(registry)
    root_title = collapse(nodes[0]["title"])
    named = {collapse(n["title"]): n for n in nodes if n["number"] is None and n["parent"]}
    numbers = {n["number"]: n["key"] for n in nodes if n["number"] is not None}

    found: list[tuple[int, str, str]] = []  # (paragraph, key, how): how = "found" or "assigned"
    candidates: dict[str, list[dict[str, Any]]] = {}
    current: str | None = None  # the numbered section the scan is in
    end = next((i for i, p in enumerate(paragraphs) if line(p) == collapse(SMPC_END)), None)
    for i, paragraph in enumerate(paragraphs[:end]):
        text = line(paragraph)
        if not text:
            continue
        hit = match_heading(text, table)
        if hit is not None:
            found.append((i, hit.key, "found"))
            current = hit.key
            continue
        if text == root_title:
            found.append((i, nodes[0]["key"], "found"))
            continue
        sub = named.get(text)
        if sub is not None and sub["parent"] == current:
            found.append((i, sub["key"], "found"))
            continue
        number = _NUMBER.match(text)
        if number is not None and number.group(1) in numbers:
            key = numbers[number.group(1)]
            candidates.setdefault(key, []).append({"paragraph": i, "why": "number"})
        elif paragraph.style is not None and _HEADING_STYLE.match(paragraph.style):
            where = current or nodes[0]["key"]
            candidates.setdefault(where, []).append({"paragraph": i, "why": "style"})

    headings = {i for i, _, _ in found}
    for key, at in (assignments or {}).items():
        if key not in by_key:
            raise ValueError(f"no section {key} in the template")
        if not 0 <= at < (len(paragraphs) if end is None else end):
            raise ValueError(f"no paragraph {at} in the SmPC")
        if at in headings:
            raise ValueError(f"paragraph {at} is already a heading")
        if any(k == key for _, k, _ in found):
            raise ValueError(f"section {key} already has its heading")
        found.append((at, key, "assigned"))
        headings.add(at)
    found.sort()

    seen: dict[str, int] = {}
    out_of_order: set[str] = set()
    furthest = -1
    for _, key, _ in found:
        seen[key] = seen.get(key, 0) + 1
        if order[key] < furthest:
            out_of_order.add(key)
        furthest = max(furthest, order[key])

    texts: dict[str, list[int]] = {n["key"]: [] for n in nodes}
    preamble: list[int] = []
    owner: str | None = None
    marks = {i: key for i, key, _ in found}
    for i, paragraph in enumerate(paragraphs[:end]):
        if i in marks:
            owner = marks[i]
            continue
        if not paragraph.text.strip():
            continue
        (texts[owner] if owner is not None else preamble).append(i)

    first = {key: (i, how) for i, key, how in reversed(found)}
    sections: list[dict[str, Any]] = []
    for n in nodes:
        key = n["key"]
        heading, how = first.get(key, (None, None))
        if how == "assigned":
            status = "assigned"
        elif heading is None:
            status = "missing" if n["required"] else "absent"
        elif seen[key] > 1:
            status = "duplicate"
        elif key in out_of_order:
            status = "order"
        elif n["code"] is None:
            status = "no-code"
        else:
            status = "mapped"
        sections.append(
            {
                "key": key,
                "parent": n["parent"],
                "number": n["number"],
                "title": n["title"],
                "code": n["code"],
                "status": status,
                "heading": heading,
                "paragraphs": texts[key],
                "candidates": candidates.get(key, []),
            }
        )
    counts = {
        status: sum(1 for s in sections if s["status"] == status)
        for status in ("mapped", "assigned", "missing", "absent", "duplicate", "order", "no-code")
    }
    return {
        "structurer": STRUCTURE_VERSION,
        "registryVersion": registry["registryVersion"],
        "mappingVersion": mapping["mappingVersion"],
        "preamble": preamble,
        "end": end,
        "sections": sections,
        "summary": counts,
        "ready": not any(s["status"] in NEEDS_A_PERSON for s in sections),
    }
