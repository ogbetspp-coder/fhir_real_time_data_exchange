"""Build the QRD template registry for the centrally authorised SmPC (English) from EMA's files.

The registry is data derived mechanically from four pinned EMA documents (``qrd/sources/``,
hashes in ``qrd/sources.lock.json``): the QRD product-information template 10.4 and QRD
Appendices I, II and III. Nothing in it is typed by hand except the few anchors named in this
module, each of which the build asserts is present. A change in any source changes its hash,
which fails the lock check before this code runs; a change in this code changes the registry
bytes, which fails ``tests/test_qrd_registry.py`` until the committed file is regenerated.

See ``docs/design/qrd-registry.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from label_docx.output import FORMAT_VERSION
from label_docx.reader import READER_VERSION, Paragraph

from zone_a.certified import read_docx
from zone_a.qrd.pattern import Token, UnbalancedTemplateError, children, is_balanced, parse
from zone_a.underline import underline_changes

REGISTRY_VERSION = "1.3.3"

TEMPLATE_FILE = "qrd-product-information-template-version-104_en.docx"
APPENDIX_I_FILE = (
    "qrd-appendix-i-statements-use-section-46-pregnancy-lactation-summary-product-characteristics"
    "_en.docx"
)
APPENDIX_II_FILE = (
    "qrd-appendix-ii-medical-dictionary-regulatory-activities-terminology-be-used-section-48"
    "-undesirable-effects-summary-product-characteristics_en.docx"
)
APPENDIX_III_FILE = (
    "qrd-appendix-iii-quality-review-documents-templates-human-medicinal-products_en.docx"
)

# The only hand-written anchors. Each must occur exactly once in its source or the build fails.
SMPC_START = "SUMMARY OF PRODUCT CHARACTERISTICS"
SMPC_END = "ANNEX II"
# The statement the annotated template says is included "for ALL medicinal products" at the end
# of the SmPC. In the base template it follows section 12 with no heading of its own, so it is
# identified by its opening words rather than by position.
CLOSING_PREFIX = "Detailed information on this medicinal product is available on the website"
APPENDIX_I_HEADINGS = {
    "With Respect to \u201cPregnancy\u201d": "pregnancy",
    "With Respect to \u201cLactation\u201d": "lactation",
}
APPENDIX_III_START = "6.4\tSpecial precautions for storage"
APPENDIX_III_END = "A. LABELLING"
# The package leaflet (Annex IIIB): its title page line, the line that opens each leaflet, and the
# line before the leaflet's list of its own six sections.
PL_ROOT = "B. PACKAGE LEAFLET"
PL_TITLE = "Package leaflet: Information for the <patient> <user>"
PL_CONTENTS = "What is in this leaflet"

# Corrections the registry applies to EMA's text, each for a defect in the source itself. The key
# is the exact source paragraph (it must occur exactly once); the value is the corrected text and
# the reason. The registry keeps the verbatim source next to the corrected pattern, so the
# correction is visible and reversible. See docs/design/qrd-registry.md, "What the EMA's own
# files got wrong".
ERRATA: dict[str, tuple[str, str]] = {
    "<There is no relevant use of {X} <in the paediatric population> <in children aged {x to y} "
    "<years> <months> [or any other relevant subsets, e.g. weight, pubertal age, gender] "
    "<for the indication of...>.>": (
        "<There is no relevant use of {X} <in the paediatric population> "
        "<in children aged {x to y} "
        "<years> <months> [or any other relevant subsets, e.g. weight, pubertal age, gender]> "
        "<for the indication of...>.>",
        "The template opens '<in children aged' and never closes it (one '<' more than '>'; the "
        "v11 draft repeats it). The registry closes it after the guidance, as in the sibling "
        "statement '<{X} should not be used in children aged ... [or any other relevant subsets, "
        "...] because of ...>', so that the two ways of naming the population are alternatives.",
    )
}

HEADING = re.compile(
    r"^(?P<open><)?(?P<number>\d{1,2}(?:\.\d{1,2})?)(?P<dot>\.)?(?P<gap>[ \t]*\t)(?P<rest>.*)$",
    re.DOTALL,
)
ENTRY = re.compile(r"^\s*\[(?P<number>\d+)\]")
TERMINAL = (".", ":", ";", "!", "?")


class RegistryError(ValueError):
    """The sources do not have the shape the registry build relies on."""


@dataclass(frozen=True)
class Source:
    file: str
    sha256: str
    paragraphs: list[Paragraph]


def load_source(directory: Path, file: str) -> Source:
    data = (directory / file).read_bytes()
    return Source(file=file, sha256=hashlib.sha256(data).hexdigest(), paragraphs=read_docx(data))


def _plain(tokens: list[Token]) -> str:
    """The literal text of tokens with every bracket removed, for classification only."""
    out: list[str] = []
    for token in tokens:
        value = token["value"]
        if token["kind"] == "optional":
            out.append(_plain(children(token)))
        elif token["kind"] == "text":
            out.append(str(value))
    return "".join(out)


def _kind(tokens: list[Token]) -> str:
    """fill, guidance, subheading or statement. The rule is in docs/design/qrd-registry.md."""
    inner = tokens
    while len(inner) == 1 and inner[0]["kind"] == "optional":
        inner = children(inner[0])
    meaningful = [t for t in inner if not (t["kind"] == "text" and not str(t["value"]).strip())]
    if meaningful and all(t["kind"] == "guidance" for t in meaningful):
        return "guidance"
    if meaningful and all(t["kind"] == "fill" for t in meaningful):
        return "fill"

    def has_fill(tokens: list[Token]) -> bool:
        for token in tokens:
            value = token["value"]
            if token["kind"] == "fill":
                return True
            if token["kind"] == "optional" and isinstance(value, list) and has_fill(value):
                return True
        return False

    plain = _plain(inner).strip()
    if has_fill(inner) or plain.endswith(TERMINAL) or ". " in plain or "\n" in plain:
        return "statement"
    return "subheading"


def _optional(tokens: list[Token]) -> bool:
    meaningful = [t for t in tokens if not (t["kind"] == "text" and not str(t["value"]).strip())]
    return (
        len(meaningful) >= 1
        and meaningful[0]["kind"] == "optional"
        and all(t["kind"] in ("optional", "guidance") for t in meaningful)
    )


# Marks kept on items: the template's light-grey highlight and light-grey (D9D9D9) shading mean
# "not in the printed material" (the annotated template, "Text which will not appear in the
# final printed material is to be presented as grey-shaded text"). The registry is built from
# text alone, so every other mark is refused rather than flattened, and so is a kept mark where
# the registry does not store marks (headings, appendix entries, notes). Capitals over text
# that is already in capitals change nothing and are allowed, and so are bold and italic, which
# draw the same characters.
_KEPT = {"highlight-lightGray", "shading-D9D9D9"}
_CASE = {"caps", "smallCaps"}
_EMPHASIS = {"bold", "italic"}
# An underline is accepted only where it changes nothing (zone_a.underline, ADR 0005): over
# letters, digits, plain punctuation, a hyphen inside a word ("Breast-feeding") and the template's
# own brackets, which the registry reads as markup, never as text ("<Traceability>").
_TEMPLATE_BRACKETS = frozenset("<>[]{}")


def _check(paragraph: Paragraph, where: str, keeps_marks: bool = False) -> None:
    if paragraph.mark_hidden and paragraph.text.strip():
        raise RegistryError(f"{where}: a paragraph with text has a hidden paragraph mark")
    if paragraph.numbering is not None and paragraph.numbering.num_id != 0:
        # Word shows a number or bullet the text does not hold.
        raise RegistryError(f"{where}: a numbered or bulleted paragraph")
    for mark in paragraph.marks:
        covered = paragraph.text[mark.start : mark.end]
        if (
            (keeps_marks and mark.kind in _KEPT)
            or mark.kind in _EMPHASIS
            or (mark.kind in _CASE and covered == covered.upper())
            or (
                mark.kind == "underline"
                and not underline_changes(
                    paragraph.text,
                    mark.start,
                    mark.end,
                    also=_TEMPLATE_BRACKETS,
                    hyphens_in_words=True,
                )
            )
        ):
            continue
        raise RegistryError(f"{where}: {mark.kind} changes what the text shows")


def _guard(tokens: list[Token], where: str) -> None:
    """Refuse a literal '>' left in text: the bracket reading around it is not certain."""
    for token in tokens:
        value = token["value"]
        if token["kind"] == "optional":
            _guard(children(token), where)
        elif token["kind"] == "text" and ">" in str(value):
            raise RegistryError(f"{where}: a literal '>' makes the bracket reading ambiguous")


def _split_trailer(text: str) -> tuple[str, str | None, str | None]:
    """(body, note marker, connector) of an item.

    Appendix III ends statements with footnote markers ("...>*", "...>****") and joins
    alternatives with " or"; section 4.8 ends its reporting statement with "Appendix V.*", the
    marker of the guidance note that follows it. None of these is part of the statement.
    """
    body = text.rstrip()
    note: str | None = None
    stripped = body.rstrip("*")
    if stripped != body and stripped.endswith((">", ".")):
        note = body[len(stripped) :]
        body = stripped
    connector: str | None = None
    if body.endswith(" or") and body[:-3].rstrip().endswith(">"):
        connector = "or"
        body = body[:-3]
    return body, note, connector


def _items(paragraphs: list[Paragraph], where: str) -> list[dict[str, Any]]:
    """Group paragraphs into balanced items and classify each."""
    items: list[dict[str, Any]] = []
    pending: list[Paragraph] = []
    for paragraph in paragraphs:
        _check(paragraph, where, keeps_marks=True)
        if not pending and not paragraph.text.strip():
            continue
        pending.append(paragraph)
        joined = "\n".join(p.text for p in pending)
        erratum = ERRATA.get(joined)
        corrected = erratum[0] if erratum is not None else joined
        body, note, connector = _split_trailer(corrected)
        if not is_balanced(body):
            continue
        tokens = parse(body)
        _guard(tokens, where)
        item: dict[str, Any] = {
            "kind": _kind(tokens),
            "optional": _optional(tokens),
            "source": joined,
            "pattern": tokens,
        }
        if corrected[len(body) :]:
            item["trailer"] = corrected[len(body) :]
        if note is not None:
            item["note"] = note
        if connector is not None:
            item["connector"] = connector
        marks: list[dict[str, Any]] = []
        offset = 0
        for part in pending:
            marks += [
                {"start": offset + m.start, "end": offset + m.end, "kind": m.kind}
                for m in part.marks
                if m.kind in _KEPT
            ]
            offset += len(part.text) + 1
        if marks:
            item["marks"] = marks
        if erratum is not None:
            item["erratum"] = erratum[1]
        items.append(item)
        pending = []
    if pending:
        raise RegistryError(f"{where}: a bracket opened and never closed")
    return items


def _heading(text: str) -> dict[str, Any] | None:
    match = HEADING.match(text)
    if match is None:
        return None
    number = match.group("number")
    rest = match.group("rest")
    optional = match.group("open") is not None
    body = rest
    guidance: str | None = None
    if optional:
        # The heading is "<N\tTitle> [guidance]": find the ">" that closes the leading "<".
        for end in range(len(rest), 0, -1):
            candidate = "<" + rest[:end]
            if candidate.endswith(">") and is_balanced(candidate):
                body = rest[: end - 1]
                trailer = rest[end:].strip()
                if trailer:
                    trailer_tokens = parse(trailer)
                    if len(trailer_tokens) != 1 or trailer_tokens[0]["kind"] != "guidance":
                        raise RegistryError(f"heading {number}: unexpected text after it")
                    guidance = str(trailer_tokens[0]["value"])
                break
        else:
            raise RegistryError(f"heading {number}: the opening '<' is never closed")
    tokens = parse(body)
    _guard(tokens, f"heading {number}")
    if tokens and tokens[-1]["kind"] == "guidance":
        guidance = str(tokens[-1]["value"])
        tokens = tokens[:-1]
    while tokens and tokens[-1]["kind"] == "text" and not str(tokens[-1]["value"]).strip():
        tokens = tokens[:-1]
    if tokens and tokens[-1]["kind"] == "text":
        # "8.\tMARKETING AUTHORISATION NUMBER(S) " ends in a space in the template; the title
        # pattern drops trailing whitespace, and "source" keeps the paragraph as written.
        tokens = [*tokens[:-1], {"kind": "text", "value": str(tokens[-1]["value"]).rstrip()}]
    level = 2 if "." in number else 1
    return {
        "number": number,
        "level": level,
        "optional": optional,
        "source": text,
        "title": tokens,
        "guidance": guidance,
        "dotted": match.group("dot") is not None,
        "gap": match.group("gap"),
    }


def _order(number: str) -> tuple[int, ...]:
    return tuple(int(part) for part in number.split("."))


def _exactly_one(texts: list[str], wanted: str, where: str) -> int:
    hits = [i for i, text in enumerate(texts) if text.strip() == wanted]
    if len(hits) != 1:
        raise RegistryError(f"{where}: {wanted!r} occurs {len(hits)} times, expected once")
    return hits[0]


def build_smpc(template: Source) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    texts = [p.text for p in template.paragraphs]
    start = _exactly_one(texts, SMPC_START, TEMPLATE_FILE)
    end = _exactly_one(texts, SMPC_END, TEMPLATE_FILE)
    if not start < end:
        raise RegistryError("the SmPC ends before it starts")
    body = template.paragraphs[start + 1 : end]
    if any(p.table is not None for p in body):
        raise RegistryError("the SmPC template holds a table; the registry does not model one")

    closing_at = [i for i, p in enumerate(body) if p.text.startswith(CLOSING_PREFIX)]
    if len(closing_at) != 1:
        raise RegistryError(f"closing statement occurs {len(closing_at)} times, expected once")
    closing_index = closing_at[0]
    closing = _items(body[closing_index:], "closing statement")
    body = body[:closing_index]

    sections: list[dict[str, Any]] = []
    preamble: list[Paragraph] = []
    current: dict[str, Any] | None = None
    lines: list[Paragraph] = []

    def close() -> None:
        if current is not None:
            current["items"] = _items(lines, f"section {current['number']}")

    for paragraph in body:
        heading = _heading(paragraph.text)
        if heading is None:
            (lines if current is not None else preamble).append(paragraph)
            continue
        _check(paragraph, f"heading {heading['number']}")
        close()
        if sections and _order(heading["number"]) <= _order(sections[-1]["number"]):
            raise RegistryError(f"heading {heading['number']} is out of order")
        current = {"key": f"smpc.{heading['number']}", **heading}
        sections.append(current)
        lines = []
    close()
    front = [{"placement": "before-section-1", **item} for item in _items(preamble, "preamble")]
    back = [{"placement": "end-of-document", **item} for item in closing]
    return sections, [*front, *back]


def build_appendix_i(source: Source) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    topic: str | None = None
    current: dict[str, Any] | None = None
    for paragraph in source.paragraphs:
        _check(paragraph, "Appendix I")
        text = paragraph.text
        stripped = text.strip()
        if stripped in APPENDIX_I_HEADINGS:
            topic = APPENDIX_I_HEADINGS[stripped]
            current = None
            continue
        if not stripped:
            continue
        match = ENTRY.match(text)
        if match is not None:
            if topic is None:
                raise RegistryError("Appendix I: a numbered statement before any topic heading")
            current = {"id": f"{topic}.{match.group('number')}", "paragraphs": []}
            entries.append(current)
        if current is None:
            raise RegistryError("Appendix I: text before the first numbered statement")
        current["paragraphs"].append(text)
    seen = {entry["id"] for entry in entries}
    if len(seen) != len(entries):
        raise RegistryError("Appendix I: a statement number repeats")
    for entry in entries:
        # Appendix I does not keep its own bracketing convention: in four of its twelve entries
        # the "<" that opens the statement after "[n]" is never closed. An entry that balances
        # as a whole gets a pattern; one that does not keeps its paragraphs verbatim and no
        # pattern, so nothing downstream relies on a structure the source does not have.
        joined = "\n".join(entry["paragraphs"])
        entry["bracketsBalanced"] = is_balanced(joined)
        entry["pattern"] = parse(joined) if entry["bracketsBalanced"] else None
        if entry["pattern"] is not None:
            _guard(entry["pattern"], f"Appendix I {entry['id']}")
    return entries


def build_appendix_ii(source: Source) -> dict[str, list[dict[str, str]]]:
    rows: dict[tuple[int, int], dict[int, str]] = {}
    for paragraph in source.paragraphs:
        _check(paragraph, "Appendix II")
        if paragraph.table is None:
            continue
        table, row, cell = paragraph.table
        if table != 0:
            raise RegistryError("Appendix II: more than one table")
        if cell > 1:
            raise RegistryError(f"Appendix II: row {row} has more than two cells")
        rows.setdefault((table, row), {})
        previous = rows[(table, row)].get(cell)
        rows[(table, row)][cell] = (
            paragraph.text if previous is None else previous + "\n" + paragraph.text
        )
    keys = sorted(rows)
    if not keys or [rows[keys[0]].get(0, "").strip(), rows[keys[0]].get(1, "").strip()] != [
        "Ref",
        "EN",
    ]:
        raise RegistryError("Appendix II: the first row is not the 'Ref | EN' header")
    groups: dict[str, list[dict[str, str]]] = {}
    group: str | None = None
    for key in keys[1:]:
        cells = rows[key]
        if set(cells) != {0, 1}:
            raise RegistryError(f"Appendix II: row {key} does not have exactly two cells")
        code = cells[0].strip()
        text = cells[1]
        if not code and text.strip().startswith("[") and text.strip().endswith("]"):
            group = text.strip()[1:-1]
            groups[group] = []
            continue
        if not re.fullmatch(r"\d{3}", code):
            raise RegistryError(f"Appendix II: unexpected row {key}")
        if group is None:
            raise RegistryError("Appendix II: a coded row before any group heading")
        _guard(parse(text), f"Appendix II {code}")
        groups[group].append({"code": code, "text": text})
    codes = [row["code"] for rows_ in groups.values() for row in rows_]
    if codes != sorted(codes) or len(set(codes)) != len(codes):
        raise RegistryError("Appendix II: codes are not unique and ascending")
    return groups


def build_appendix_iii(source: Source) -> dict[str, Any]:
    texts = [p.text for p in source.paragraphs]
    start = _exactly_one(texts, APPENDIX_III_START, APPENDIX_III_FILE)
    end = _exactly_one(texts, APPENDIX_III_END, APPENDIX_III_FILE)
    for paragraph in source.paragraphs[start + 1 : end]:
        _check(paragraph, "Appendix III SmPC", keeps_marks=True)
    statements = [p for p in source.paragraphs[start + 1 : end] if p.text.strip()]
    notes = [p for p in source.paragraphs[end:] if re.match(r"^\*+ ", p.text)]
    if not statements or not notes:
        raise RegistryError("Appendix III: no SmPC statements or no footnotes")
    for note in notes:
        _check(note, "Appendix III note")
    return {"items": _items(statements, "Appendix III SmPC"), "notes": [p.text for p in notes]}


def flat(tokens: list[Token]) -> str:
    """A title as the EMA's profiles write it: brackets dropped, fill-ins in braces, no guidance.

    The profile titles ("Do not take use X", "This leaflet was last revised in {MM/YYYY}{month
    YYYY}.") are the template's headings so flattened; spaces are collapsed.
    """

    def walk(tokens: list[Token]) -> str:
        out: list[str] = []
        for token in tokens:
            if token["kind"] == "optional":
                out.append(walk(children(token)))
            elif token["kind"] == "fill":
                out.append("{" + str(token["value"]) + "}")
            elif token["kind"] == "text":
                out.append(str(token["value"]))
        return "".join(out)

    return _collapse(walk(tokens))


def _collapse(text: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", text).strip()


def build_pl(template: Source, mapping: dict[str, Any]) -> dict[str, Any]:
    """The package leaflet's headings, as the template writes them.

    Its root line, the line opening each leaflet, the six numbered sections, and each named
    section of the mapping (``fhir/mappings/cap-pl-en.json``, from the EMA's profile). A named
    section's template paragraph is the one paragraph of its parent section, outside a table,
    whose title flattened as the profile writes it (``flat``) is the mapping's title.
    """
    texts = [p.text for p in template.paragraphs]
    root = _exactly_one(texts, PL_ROOT, TEMPLATE_FILE)
    title = _exactly_one(texts, PL_TITLE, TEMPLATE_FILE)
    contents = _exactly_one(texts, PL_CONTENTS, TEMPLATE_FILE)
    if not root < title < contents:
        raise RegistryError("the package leaflet's opening lines are out of order")
    for at in (root, title, contents):
        _check(template.paragraphs[at], "package leaflet")
    numbered = [
        (i, heading)
        for i in range(contents + 1, len(texts))
        if (heading := _heading(texts[i])) is not None
    ]
    # The leaflet lists its six sections after PL_CONTENTS, then gives each its heading.
    listed, headed = numbered[:6], numbered[6:]
    if [h["number"] for _, h in listed] != [str(n) for n in range(1, 7)] or [
        _collapse(h["source"]) for _, h in listed
    ] != [_collapse(h["source"]) for _, h in headed]:
        raise RegistryError("the package leaflet does not list then head its six sections")
    if any(
        texts[i].strip() for i in range(listed[0][0], listed[-1][0]) if _heading(texts[i]) is None
    ):
        raise RegistryError("the package leaflet's list of sections is interrupted")
    sections: list[dict[str, Any]] = []
    for i, heading in headed:
        _check(template.paragraphs[i], f"package leaflet heading {heading['number']}")
        sections.append({"key": f"pl.{heading['number']}", **heading})
    starts = [i for i, _ in headed] + [len(texts)]

    def visit(node: dict[str, Any]) -> list[dict[str, Any]]:
        return [node, *(n for child in node.get("children", []) for n in visit(child))]

    keys = {s["key"]: n for n, s in enumerate(sections)}
    headings: list[dict[str, Any]] = []
    for node in visit(mapping["root"])[1:]:
        if node["sourceKey"] in keys:
            continue
        parent = node["sourceKey"].rsplit(".", 1)[0]
        if parent not in keys:
            raise RegistryError(f"{node['sourceKey']} is under no numbered section")
        n = keys[parent]
        hits = [
            i
            for i in range(starts[n] + 1, starts[n + 1])
            if template.paragraphs[i].table is None
            and is_balanced(texts[i])
            and flat(parse(texts[i])) == node["title"]
        ]
        if len(hits) != 1:
            raise RegistryError(f"{node['sourceKey']}: {len(hits)} template paragraphs, expected 1")
        at = hits[0]
        _check(template.paragraphs[at], f"package leaflet {node['sourceKey']}")
        tokens = parse(texts[at])
        _guard(tokens, node["sourceKey"])
        headings.append(
            {
                "key": node["sourceKey"],
                "parent": parent,
                "source": texts[at],
                "title": tokens,
                "optional": _optional(tokens),
            }
        )
    order = {key: n for n, key in enumerate(node["sourceKey"] for node in visit(mapping["root"]))}
    return {
        "root": PL_ROOT,
        "leafletTitle": {"source": PL_TITLE, "title": parse(PL_TITLE)},
        "contents": PL_CONTENTS,
        "sections": sections,
        "headings": sorted(headings, key=lambda h: order[h["key"]]),
    }


def _check_errata(items: list[dict[str, Any]]) -> None:
    """Each erratum corrects exactly one item; one that matches nothing is a stale correction."""
    for source in ERRATA:
        applied = sum(1 for item in items if item.get("erratum") and item["source"] == source)
        if applied != 1:
            raise RegistryError(f"an erratum applies {applied} times, expected once")


def build(directory: Path, lock: dict[str, Any]) -> dict[str, Any]:
    """The registry, built from the four pinned EMA files.

    Args:
        directory: The folder holding the pinned files (``qrd/sources/``).
        lock: The parsed ``qrd/sources.lock.json``.

    Raises:
        RegistryError: A file does not match its lock entry, or the sources do not have the
            shape the build relies on.
        DocxRefusedError: The Word reader refuses a file.
    """
    by_file = {entry["file"]: entry for entry in lock["sources"]}
    sources = {
        name: load_source(directory, name)
        for name in (TEMPLATE_FILE, APPENDIX_I_FILE, APPENDIX_II_FILE, APPENDIX_III_FILE)
    }
    for name, source in sources.items():
        if name not in by_file or by_file[name]["sha256"] != source.sha256:
            raise RegistryError(f"{name} does not match qrd/sources.lock.json")
    sections, document_statements = build_smpc(sources[TEMPLATE_FILE])
    appendix_iii = build_appendix_iii(sources[APPENDIX_III_FILE])
    _check_errata(
        [
            *(item for section in sections for item in section["items"]),
            *document_statements,
            *appendix_iii["items"],
        ]
    )
    keys = {section["key"] for section in sections}
    for required in ("smpc.4.6", "smpc.4.8", "smpc.6.4"):
        if required not in keys:
            raise RegistryError(f"the template has no {required}")
    return {
        "registryVersion": REGISTRY_VERSION,
        "readerVersion": READER_VERSION,
        "readerFormat": FORMAT_VERSION,
        "template": {
            "family": "EMA QRD product-information template, centralised procedure",
            "version": "10.4",
            "status": "adopted",
            "annex": "I",
            "document": "Summary of product characteristics",
            "language": "en",
        },
        "sources": [
            {
                "file": name,
                "sha256": source.sha256,
                "emaReference": by_file[name].get("emaReference"),
                "lastUpdated": by_file[name]["lastUpdated"],
            }
            for name, source in sources.items()
        ],
        "sections": sections,
        "documentStatements": document_statements,
        "appendices": {
            "I": {"attachesTo": "smpc.4.6", "entries": build_appendix_i(sources[APPENDIX_I_FILE])},
            "II": {
                "attachesTo": "smpc.4.8",
                "groups": build_appendix_ii(sources[APPENDIX_II_FILE]),
            },
            "III": {"attachesTo": "smpc.6.4", **appendix_iii},
        },
    }


def build_leaflet(directory: Path, lock: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """The package leaflet's registry, built from the pinned template and the leaflet mapping.

    Raises:
        RegistryError: The template does not match its lock entry, or does not have the shape the
            build relies on.
        DocxRefusedError: The Word reader refuses the template.
    """
    entry = next((e for e in lock["sources"] if e["file"] == TEMPLATE_FILE), None)
    template = load_source(directory, TEMPLATE_FILE)
    if entry is None or entry["sha256"] != template.sha256:
        raise RegistryError(f"{TEMPLATE_FILE} does not match qrd/sources.lock.json")
    return {
        "registryVersion": REGISTRY_VERSION,
        "readerVersion": READER_VERSION,
        "readerFormat": FORMAT_VERSION,
        "mappingVersion": mapping["mappingVersion"],
        "template": {
            "family": "EMA QRD product-information template, centralised procedure",
            "version": "10.4",
            "status": "adopted",
            "annex": "IIIB",
            "document": "Package leaflet",
            "language": "en",
        },
        "sources": [
            {
                "file": TEMPLATE_FILE,
                "sha256": template.sha256,
                "emaReference": entry.get("emaReference"),
                "lastUpdated": entry["lastUpdated"],
            }
        ],
        **build_pl(template, mapping),
    }


def serialise(registry: dict[str, Any]) -> str:
    """The registry as its committed file holds it: JSON, indented by two, with a final newline."""
    return json.dumps(registry, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "READER_VERSION",
    "REGISTRY_VERSION",
    "RegistryError",
    "UnbalancedTemplateError",
    "build",
    "build_leaflet",
    "serialise",
]
