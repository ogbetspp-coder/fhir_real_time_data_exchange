"""A package leaflet's sections, found in its Word text by the QRD template's own headings.

The leaflet counterpart of ``zone_a.structure``, for the centralised package leaflet in English:
the QRD registry's leaflet part (``qrd/registry/cap-pl-en-10.4.json``) gives the headings, the
leaflet mapping (``fhir/mappings/cap-pl-en.json``, from the EMA's profile) the tree and each
section's EMA code, and ``zone_a.structure.find`` the statuses, candidates and assignments, which
mean what they mean for an SmPC. The output has the SmPC structure's shape, so
``zone_a.word_epi.sections`` builds a leaflet as it builds an SmPC. It finds; a person confirms.

The leaflet. It starts at the registry's root line ("B. PACKAGE LEAFLET", which must stand once)
and runs to the end of the document. A file of the whole product information may hold several
leaflets, one per presentation: each starts at a line the template opens a leaflet with
("Package leaflet: Information for the <patient> <user>"), and each is its own ePI whose root
heading is the root line they share (``leaflets``), as for the SmPCs of one Annex I.

The name. The template writes the medicine's (invented) name as "X" in its headings ("Throughout
the text 'X' stands for the (invented) name of the medicine", the annotated template 10.4). A
leaflet's name is what stands for X in its section 1 heading ("1. What X is and what it is used
for"); every line of the leaflet that is that heading names one and the same name, or the leaflet
has none and no heading holding X is recognised.

Headings. A line, collapsed as ``zone_a.qrd.headings`` collapses it and with each non-breaking
hyphen (U+2011) read as the hyphen it draws, is a heading when it is one of the forms the
registry allows for it with the leaflet's name for X: each optional segment present or absent, an
optional segment that begins with a comma written without the space before it ("Pregnancy,
breast-feeding and fertility"). The root line starts the leaflet; a numbered section's line
anywhere in it; a named section's line only inside its numbered section, and only a named section
the mapping (the EMA's profile) requires: an optional one stays text of its section, as for an
SmPC. A heading whose title holds a fill-in ("This leaflet was last revised in <{MM/YYYY}><{month
YYYY}>.", completed at printing) is recognised by its text before the first fill-in: a line that
is that text, or that text followed by anything but a letter or digit. Two forms the template does
not write are each a named section's heading too (``ALSO``).

Lists. Lines with text that stand next to each other (blank paragraphs aside), each starting with
a section number from 1 to 6, the numbers rising by one, are a list: the leaflet's list of its
own sections, or numbered steps. None of them is a heading or a candidate: two sections never
stand next to each other with no text between.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from label_docx.reader import Paragraph

from zone_a.qrd.headings import collapse
from zone_a.qrd.pattern import Token, children
from zone_a.structure import find, line

LEAFLET_VERSION = "pl-structure/1.0.0"

# Headings the template does not write that stand for one of its sections, each from the EMA's
# own guidance (ADR 0006, owner decision 8). The annotated template
# 10.4, section 6: "[If MAH and manufacturer are the same, the general heading 'Marketing
# Authorisation Holder and Manufacturer' can be used.]"; otherwise the holder and the manufacturer
# are each stated "and identified as such", which leaflets write as a line "Marketing Authorisation
# Holder" opening the section, the manufacturer's in its text.
ALSO: dict[str, tuple[str, ...]] = {"pl.6.holder": ("Marketing Authorisation Holder",)}

_X = re.compile(r"\bX\b")
_NUMBERED = re.compile(r"^([1-6])\.?\s+\S")


def key(text: str) -> str:
    """The line as headings are compared: collapsed, each non-breaking hyphen a hyphen."""
    return collapse(text).replace("\u2011", "-")


def _expand(tokens: list[Token]) -> list[str]:
    """Every rendering of text and optional tokens, each optional segment present or absent."""
    out = [""]
    for token in tokens:
        if token["kind"] == "text":
            out = [o + str(token["value"]) for o in out]
        elif token["kind"] == "optional":
            inner = _expand(children(token))
            out = [o + i for o in out for i in ["", *inner]]
        else:
            raise ValueError(f"a heading form holds a {token['kind']}")
    return out


def _has_fill(tokens: list[Token]) -> bool:
    return any(
        t["kind"] == "fill" or (t["kind"] == "optional" and _has_fill(children(t))) for t in tokens
    )


def forms(head: Mapping[str, Any]) -> tuple[set[str], str | None]:
    """A registry heading's lines, X as written, and the text before its first fill-in (if any).

    The template's own wording, every optional segment present, is the first form.
    """
    title: list[Token] = head["title"]
    number = (head["number"] + ("." if head["dotted"] else "") + " ") if "number" in head else ""
    cut = next((n for n, t in enumerate(title) if _has_fill([t])), None)
    if cut is not None:
        if any(t["kind"] != "text" for t in title[:cut]):
            raise ValueError("a heading holds a choice before its fill-in")
        return set(), key(number + "".join(str(t["value"]) for t in title[:cut]))
    lines = {key(number + rendered).replace(" ,", ",") for rendered in _expand(title)}
    return {form for form in lines if form}, None


def _named(text: str, name: str) -> str:
    return _X.sub(lambda _: name, text)


def name(
    paragraphs: Sequence[Paragraph], registry: Mapping[str, Any], start: int, end: int
) -> str | None:
    """The leaflet's name: what stands for X in every section 1 line from start to end, or None."""
    one = next(s for s in registry["sections"] if s["key"] == "pl.1")
    patterns = [
        re.compile("^" + "(.+)".join(re.escape(piece) for piece in _X.split(form)) + "$")
        for form in forms(one)[0]
    ]
    names = {
        hit.group(1)
        for i in range(start, end)
        for pattern in patterns
        if (hit := pattern.match(key(line(paragraphs[i]))))
    }
    return names.pop() if len(names) == 1 else None


def leaflets(
    paragraphs: Sequence[Paragraph], registry: Mapping[str, Any]
) -> tuple[list[tuple[int, int, int | None]], str | None]:
    """Each leaflet as ``structure``'s ``part`` (start, stop, shared root heading), or why not."""
    root = key(registry["root"])
    roots = [i for i, p in enumerate(paragraphs) if key(line(p)) == root]
    if len(roots) != 1:
        return [], f"{len(roots)} lines {registry['root']!r}, expected one"
    titles, _ = forms(registry["leafletTitle"])
    opens = [i for i in range(roots[0], len(paragraphs)) if key(line(paragraphs[i])) in titles]
    if len(opens) < 2:
        return [(roots[0], len(paragraphs), None)], None
    starts = [roots[0], *opens[1:]]
    stops = [*opens[1:], len(paragraphs)]
    return [
        (start, stop, None if n == 0 else roots[0])
        for n, (start, stop) in enumerate(zip(starts, stops, strict=True))
    ], None


def _listed(paragraphs: Sequence[Paragraph], start: int, end: int) -> frozenset[int]:
    """The paragraphs of runs of numbered lines, numbers rising by one (the module docstring)."""
    out: set[int] = set()
    run: list[tuple[int, int]] = []
    for i in [*range(start, end), None]:
        text = key(line(paragraphs[i])) if i is not None else "end"
        if not text:
            continue  # a blank paragraph: the lines on either side stand next to each other
        hit = _NUMBERED.match(text)
        n = int(hit.group(1)) if hit else None
        if n is not None and i is not None and run and n == run[-1][1] + 1:
            run.append((i, n))
            continue
        if len(run) >= 2:
            out.update(at for at, _ in run)
        run = [(i, n)] if n is not None and i is not None else []
    return frozenset(out)


def _nodes(registry: Mapping[str, Any], mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The leaflet's sections in the template's order.

    The root, then each numbered section followed by its named sections the mapping requires.
    """
    rules: dict[str, Mapping[str, Any]] = {}

    def visit(rule: Mapping[str, Any]) -> None:
        rules[rule["sourceKey"]] = rule
        for child in rule.get("children", []):
            visit(child)

    visit(mapping["root"])
    root = mapping["root"]
    out = [
        {
            "key": root["sourceKey"],
            "number": None,
            "title": registry["root"],
            "code": root["targetCode"],
            "required": True,
            "parent": None,
            "head": None,
        }
    ]
    for section in registry["sections"]:
        rule = rules[section["key"]]
        out.append(
            {
                "key": section["key"],
                "number": section["number"],
                "title": rule["title"],
                "code": rule["targetCode"],
                "required": rule["required"],
                "parent": root["sourceKey"],
                "head": section,
            }
        )
        for head in registry["headings"]:
            rule = rules[head["key"]]
            if head["parent"] == section["key"] and rule["required"]:
                out.append(
                    {
                        "key": head["key"],
                        "number": None,
                        "title": rule["title"],
                        "code": rule["targetCode"],
                        "required": True,
                        "parent": section["key"],
                        "head": head,
                    }
                )
    return out


def structure(
    paragraphs: Sequence[Paragraph],
    registry: Mapping[str, Any],
    mapping: Mapping[str, Any],
    assignments: Mapping[str, int] | None = None,
    part: tuple[int, int, int | None] | None = None,
) -> dict[str, Any]:
    """The leaflet's sections, as ``zone_a.structure.structure`` gives an SmPC's.

    ``part`` is one leaflet from ``leaflets``; without one, the document's only leaflet. A
    document whose leaflets ``leaflets`` cannot tell is a ValueError.
    """
    if part is None:
        parts, why = leaflets(paragraphs, registry)
        if why is not None or len(parts) != 1:
            raise ValueError(why or "the document holds several leaflets; name one")
        part = parts[0]
    start, end, shared = part
    called = name(paragraphs, registry, start, end)
    nodes = _nodes(registry, mapping)
    exact: dict[str, str] = {}
    prefixes: list[tuple[str, str]] = []
    for node in nodes[1:]:
        lines, prefix = forms(node["head"])
        if prefix is not None:
            prefixes.append((prefix, node["key"]))
        for form in [*lines, *(key(also) for also in ALSO.get(node["key"], ()))]:
            if _X.search(form) and called is None:
                continue  # no name: a heading holding X is not recognised
            text = _named(form, called or "X")
            if exact.setdefault(text, node["key"]) != node["key"]:
                raise ValueError(f"{text!r} is a heading of two sections")
    by_key = {n["key"]: n for n in nodes}
    root = key(registry["root"])

    def recognise(text: str, current: str | None) -> str | None:
        text = key(text)
        if text == root:
            return str(nodes[0]["key"])
        found = exact.get(text)
        if found is None:
            found = next(
                (
                    k
                    for prefix, k in prefixes
                    if text.startswith(prefix)
                    and (len(text) == len(prefix) or not text[len(prefix)].isalnum())
                ),
                None,
            )
        if found is None:
            return None
        node = by_key[found]
        return found if node["number"] is not None or node["parent"] == current else None

    listed = _listed(paragraphs, start, end)
    result = find(paragraphs, nodes, recognise, start, end, shared, assignments, listed)
    return {
        "structurer": LEAFLET_VERSION,
        "registryVersion": registry["registryVersion"],
        "mappingVersion": mapping["mappingVersion"],
        "name": called,
        **result,
    }
