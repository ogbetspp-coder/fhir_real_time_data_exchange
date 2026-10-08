"""A package leaflet's sections, found in its Word text by the QRD template's own headings.

The leaflet counterpart of ``zone_a.structure``, for the centralised package leaflet in English:
the QRD registry's leaflet part (``qrd/registry/cap-pl-en-10.4.json``) gives the headings, the
leaflet mapping (``fhir/mappings/cap-pl-en.json``, from the EMA's profile) the tree and each
section's EMA code, and ``zone_a.structure.find`` the statuses, candidates and assignments, which
mean what they mean for an SmPC. The output has the SmPC structure's shape, so
``zone_a.word_epi.sections`` builds a leaflet as it builds an SmPC. It finds; a person confirms.

The leaflet. It starts at the registry's root line ("B. PACKAGE LEAFLET", which must stand once)
and runs to the product information's next annex ("ANNEX IV", a line of "ANNEX" and a roman
numeral, ``END``) or the end of the document. A file of the whole product information may hold
several leaflets, one per presentation: each starts at a line the template opens a leaflet with
("Package leaflet: Information for the <patient> <user>"), and each is its own ePI whose root
heading is the root line they share (``leaflets``), as for the SmPCs of one Annex I. A leaflet
opened by a line the template does not write ("Package Leaflet: ...") is not split off: the two
leaflets' headings are then each found twice, which is for a person.

The name. The template writes the medicine's (invented) name as "X" in its headings ("Throughout
the text 'X' stands for the (invented) name of the medicine", the annotated template 10.4). A
leaflet's name is what stands for X in its section 1 heading ("1. What X is and what it is used
for"); every line of the leaflet that is that heading names one and the same name, or the leaflet
has none and no heading holding X is recognised.

Headings. A line, collapsed as ``zone_a.qrd.headings`` collapses it and with each non-breaking
hyphen (U+2011) read as the hyphen it draws, is a heading when it is one of the forms the
registry allows for it with the leaflet's name for X: each optional segment present or absent,
but of two or more standing together (only spaces between them, "<take> <use>"), a choice, at
least one; an optional segment that begins with a comma written without the space before it
("Pregnancy, breast-feeding and fertility"). The root line starts the leaflet; a numbered
section's line anywhere in it; a named section's line only inside its numbered section, and only a
named section the mapping (the EMA's profile) requires: an optional one stays text of its
section, as for an SmPC. A heading whose title ends in the date completed at printing ("This
leaflet was last revised in <{MM/YYYY}><{month YYYY}>.") is recognised by its text before the
date: a line that is that text followed by nothing, a date ("06/2026", "June 2026") or the
template's placeholder for one ("{MM/YYYY}"), and at most a full stop. A form the template does not
write is a named section's heading only where ``ALSO`` names it.

Lists. Lines with text that stand next to each other (blank paragraphs aside), each starting with
a section number from 1 to 6, the numbers rising by one, are a list. One that starts with section
1's heading is the leaflet's list of its own sections, none of whose lines is a heading or a
candidate (two sections never stand next to each other with no text between). Any other is
numbered steps, whose lines are neither headings nor candidates except a numbered section's
heading among them ("3. Dispose of the pen." then "4. Possible side effects").
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from label_docx.reader import Paragraph

from zone_a.qrd.headings import collapse
from zone_a.qrd.pattern import Token, children
from zone_a.structure import find, line

LEAFLET_VERSION = "pl-structure/1.0.2"

# Headings the template does not write that stand for one of its sections, each from the EMA's
# own guidance (ADR 0006, owner decision 8). The annotated template
# 10.4, section 6: "[If MAH and manufacturer are the same, the general heading 'Marketing
# Authorisation Holder and Manufacturer' can be used.]"; otherwise the holder and the manufacturer
# are each stated "and identified as such", which leaflets write as a line "Marketing Authorisation
# Holder" opening the section, the manufacturer's in its text.
ALSO: dict[str, tuple[str, ...]] = {"pl.6.holder": ("Marketing Authorisation Holder",)}

# The product information's next annex after the leaflet: Annex IV, in the corpus, as its own line.
END = re.compile(r"^ANNEX [IVXL]+(?: |$)")

_X = re.compile(r"\bX\b")
_NUMBERED = re.compile(r"^([1-6])\.?\s+\S")
# The date a heading ends in, completed at printing: the template's fill-ins for it, and what may
# follow its text in a label: a date, the placeholder still as written, then at most a full stop.
_DATE_FILLS = frozenset({"MM/YYYY", "month YYYY"})
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE = rf"(?:[0-9]{{1,2}}/[0-9]{{4}}|(?:{_MONTHS}) [0-9]{{4}}|MM/YYYY|month YYYY|month/YYYY)"
_DATE_TAIL = re.compile(rf"(?: ?[<{{]*{_DATE}[>}}]*)* ?\.?")


def key(text: str) -> str:
    """The line as headings are compared: collapsed, each non-breaking hyphen a hyphen."""
    return collapse(text).replace("\u2011", "-")


def _expand(tokens: list[Token]) -> list[str]:
    """Every rendering of text and optional tokens (the module docstring's choices)."""
    out = [""]
    at = 0
    while at < len(tokens):
        token = tokens[at]
        if token["kind"] == "text":
            out = [o + str(token["value"]) for o in out]
            at += 1
            continue
        if token["kind"] != "optional":
            raise ValueError(f"a heading form holds a {token['kind']}")
        # The optional segments standing together from here, with the spaces between them.
        run = [token]
        at += 1
        while at < len(tokens):
            gap = tokens[at]
            if gap["kind"] == "optional":
                run.append(gap)
                at += 1
            elif (
                gap["kind"] == "text"
                and not str(gap["value"]).strip()
                and at + 1 < len(tokens)
                and tokens[at + 1]["kind"] == "optional"
            ):
                run += [gap, tokens[at + 1]]
                at += 2
            else:
                break
        renderings: list[tuple[str, bool]] = [("", False)]  # (text, an option present)
        for part in run:
            if part["kind"] == "text":
                renderings = [(r + str(part["value"]), present) for r, present in renderings]
            else:
                inner = _expand(children(part))
                renderings = [*renderings, *((r + i, True) for r, _ in renderings for i in inner)]
        options = sum(1 for part in run if part["kind"] == "optional")
        chosen = [r for r, present in renderings if present or options == 1]
        out = [o + r for o in out for r in chosen]
    return out


def _has_fill(tokens: list[Token]) -> bool:
    return any(
        t["kind"] == "fill" or (t["kind"] == "optional" and _has_fill(children(t))) for t in tokens
    )


def forms(head: Mapping[str, Any]) -> tuple[set[str], str | None]:
    """A registry heading's lines, X as written, or the text before the date it ends in.

    Raises:
        ValueError: The heading holds a fill-in that is not that date, or a choice before it.
    """
    title: list[Token] = head["title"]
    number = (head["number"] + ("." if head["dotted"] else "") + " ") if "number" in head else ""
    cut = next((n for n, t in enumerate(title) if _has_fill([t])), None)
    if cut is not None:
        if any(t["kind"] != "text" for t in title[:cut]):
            raise ValueError("a heading holds a choice before its fill-in")
        if not set(_fills(title[cut:])) <= _DATE_FILLS:
            raise ValueError("a heading holds a fill-in that is not the date it was revised")
        return set(), key(number + "".join(str(t["value"]) for t in title[:cut]))
    lines = {key(number + rendered).replace(" ,", ",") for rendered in _expand(title)}
    return {form for form in lines if form}, None


def _fills(tokens: list[Token]) -> list[str]:
    out: list[str] = []
    for token in tokens:
        if token["kind"] == "fill":
            out.append(str(token["value"]))
        elif token["kind"] == "optional":
            out += _fills(children(token))
    return out


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
    end = next(
        (i for i in range(roots[0] + 1, len(paragraphs)) if END.match(key(line(paragraphs[i])))),
        len(paragraphs),
    )
    titles, _ = forms(registry["leafletTitle"])
    opens = [i for i in range(roots[0], end) if key(line(paragraphs[i])) in titles]
    if len(opens) < 2:
        return [(roots[0], end, None)], None
    starts = [roots[0], *opens[1:]]
    stops = [*opens[1:], end]
    return [
        (start, stop, None if n == 0 else roots[0])
        for n, (start, stop) in enumerate(zip(starts, stops, strict=True))
    ], None


def _listed(
    paragraphs: Sequence[Paragraph], start: int, end: int, numbered: Mapping[str, str]
) -> frozenset[int]:
    """The paragraphs of lists that are neither headings nor candidates (the module docstring).

    ``numbered`` maps each numbered section's heading line to its key.
    """
    out: set[int] = set()
    run: list[tuple[int, int, str]] = []

    def close() -> None:
        if len(run) < 2:
            return
        contents = numbered.get(run[0][2]) == "pl.1"
        out.update(at for at, _, text in run if contents or text not in numbered)

    for i in range(start, end):
        text = key(line(paragraphs[i]))
        if not text:
            continue  # a blank paragraph: the lines on either side stand next to each other
        hit = _NUMBERED.match(text)
        n = int(hit.group(1)) if hit else None
        if n is not None and run and n == run[-1][1] + 1:
            run.append((i, n, text))
            continue
        close()
        run = [(i, n, text)] if n is not None else []
    close()
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
    prefixes: dict[str, str] = {}
    for node in nodes[1:]:
        lines, prefix = forms(node["head"])
        for form, table in [
            *((form, exact) for form in lines),
            *((key(also), exact) for also in ALSO.get(node["key"], ())),
            *(((prefix, prefixes),) if prefix is not None else ()),
        ]:
            if _X.search(form) and called is None:
                continue  # no name: a heading holding X is not recognised
            text = _named(form, called or "X")
            if (exact.get(text) or prefixes.get(text) or node["key"]) != node["key"]:
                raise ValueError(f"{text!r} is a heading of two sections")
            table[text] = node["key"]
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
                    for prefix, k in prefixes.items()
                    if text.startswith(prefix) and _DATE_TAIL.fullmatch(text[len(prefix) :])
                ),
                None,
            )
        if found is None:
            return None
        node = by_key[found]
        return found if node["number"] is not None or node["parent"] == current else None

    numbered = {text: k for text, k in exact.items() if by_key[k]["number"] is not None}
    listed = _listed(paragraphs, start, end, numbered)
    result = find(paragraphs, nodes, recognise, start, end, shared, assignments, listed)
    return {
        "structurer": LEAFLET_VERSION,
        "registryVersion": registry["registryVersion"],
        "mappingVersion": mapping["mappingVersion"],
        "name": called,
        **result,
    }
