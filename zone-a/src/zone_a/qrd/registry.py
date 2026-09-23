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

from zone_a.docx.reader import Paragraph, read_docx
from zone_a.qrd.pattern import Token, UnbalancedTemplateError, is_balanced, parse

REGISTRY_VERSION = "1.0.0"
READER_VERSION = "docx-reader/1.0.0"

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

# Corrections the registry applies to EMA's text, each for a defect in the source itself. The key
# is the exact source paragraph (it must occur exactly once); the value is the corrected text and
# the reason. The registry keeps the verbatim source next to the corrected pattern, so the
# correction is visible and reversible. See docs/design/qrd-registry.md, "Errata".
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
            assert isinstance(value, list)
            out.append(_plain(value))
        elif token["kind"] == "text":
            out.append(str(value))
    return "".join(out)


def _kind(tokens: list[Token]) -> str:
    """fill, guidance, subheading or statement. The rule is in docs/design/qrd-registry.md."""
    inner = tokens
    while len(inner) == 1 and inner[0]["kind"] == "optional":
        value = inner[0]["value"]
        assert isinstance(value, list)
        inner = value
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


def _items(paragraphs: list[str], where: str) -> list[dict[str, Any]]:
    """Group paragraphs into balanced items and classify each."""
    items: list[dict[str, Any]] = []
    pending: list[str] = []
    for text in paragraphs:
        if not pending and not text.strip():
            continue
        pending.append(text)
        joined = "\n".join(pending)
        erratum = ERRATA.get(joined)
        corrected = erratum[0] if erratum is not None else joined
        if not is_balanced(corrected):
            continue
        tokens = parse(corrected)
        item: dict[str, Any] = {
            "kind": _kind(tokens),
            "optional": _optional(tokens),
            "source": joined,
            "pattern": tokens,
        }
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
    closing = _items([p.text for p in body[closing_index:]], "closing statement")
    body = body[:closing_index]

    sections: list[dict[str, Any]] = []
    preamble: list[str] = []
    current: dict[str, Any] | None = None
    lines: list[str] = []

    def close() -> None:
        if current is not None:
            current["items"] = _items(lines, f"section {current['number']}")

    for paragraph in body:
        heading = _heading(paragraph.text)
        if heading is None:
            (lines if current is not None else preamble).append(paragraph.text)
            continue
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
    return entries


def build_appendix_ii(source: Source) -> dict[str, list[dict[str, str]]]:
    rows: dict[tuple[int, int], dict[int, str]] = {}
    for paragraph in source.paragraphs:
        if paragraph.table is None:
            continue
        table, row, cell = paragraph.table
        if table != 0:
            raise RegistryError("Appendix II: more than one table")
        rows.setdefault((table, row), {})
        previous = rows[(table, row)].get(cell)
        rows[(table, row)][cell] = (
            paragraph.text if previous is None else previous + "\n" + paragraph.text
        )
    groups: dict[str, list[dict[str, str]]] = {}
    group: str | None = None
    for key in sorted(rows):
        cells = rows[key]
        code = cells.get(0, "").strip()
        text = cells.get(1, "")
        if code == "Ref":
            continue
        if not code and text.strip().startswith("[") and text.strip().endswith("]"):
            group = text.strip()[1:-1]
            groups[group] = []
            continue
        if not re.fullmatch(r"\d{3}", code):
            raise RegistryError(f"Appendix II: unexpected row {key}")
        if group is None:
            raise RegistryError("Appendix II: a coded row before any group heading")
        parse(text)
        groups[group].append({"code": code, "text": text})
    codes = [row["code"] for rows_ in groups.values() for row in rows_]
    if codes != sorted(codes) or len(set(codes)) != len(codes):
        raise RegistryError("Appendix II: codes are not unique and ascending")
    return groups


def build_appendix_iii(source: Source) -> dict[str, Any]:
    texts = [p.text for p in source.paragraphs]
    start = _exactly_one(texts, APPENDIX_III_START, APPENDIX_III_FILE)
    end = _exactly_one(texts, APPENDIX_III_END, APPENDIX_III_FILE)
    statements = [t for t in texts[start + 1 : end] if t.strip()]
    notes = [t for t in texts[end:] if re.match(r"^\*+ ", t)]
    if not statements or not notes:
        raise RegistryError("Appendix III: no SmPC statements or no footnotes")
    return {"items": _items(statements, "Appendix III SmPC"), "notes": notes}


def build(directory: Path, lock: dict[str, Any]) -> dict[str, Any]:
    by_file = {entry["file"]: entry for entry in lock["sources"]}
    sources = {
        name: load_source(directory, name)
        for name in (TEMPLATE_FILE, APPENDIX_I_FILE, APPENDIX_II_FILE, APPENDIX_III_FILE)
    }
    for name, source in sources.items():
        if name not in by_file or by_file[name]["sha256"] != source.sha256:
            raise RegistryError(f"{name} does not match qrd/sources.lock.json")
    sections, document_statements = build_smpc(sources[TEMPLATE_FILE])
    keys = {section["key"] for section in sections}
    for required in ("smpc.4.6", "smpc.4.8", "smpc.6.4"):
        if required not in keys:
            raise RegistryError(f"the template has no {required}")
    return {
        "registryVersion": REGISTRY_VERSION,
        "readerVersion": READER_VERSION,
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
            "III": {"attachesTo": "smpc.6.4", **build_appendix_iii(sources[APPENDIX_III_FILE])},
        },
    }


def serialise(registry: dict[str, Any]) -> str:
    return json.dumps(registry, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "READER_VERSION",
    "REGISTRY_VERSION",
    "RegistryError",
    "UnbalancedTemplateError",
    "build",
    "serialise",
]
