"""Check an SmPC against the QRD template registry.

The checker compares a document read by ``zone_a.epi.reader`` with the registry
(``qrd/registry/cap-smpc-en-10.4.json``) and the section mapping
(``fhir/mappings/cap-smpc-en.json``) and proposes findings for a person to review. It never
changes, corrects or completes the label's text, and it states how it reached each finding so
the person can check it.

Headings. Each section's code is looked up in the mapping. A section whose code the mapping
knows must carry that section's heading: for a numbered section, one of the forms the registry
allows (``zone_a.qrd.headings``); for a named subsection (Posology, Method of administration,
Reporting of suspected adverse reactions), the mapping's title. Findings: ``missing-heading``
(a required mapped section is not in the document), ``heading-text`` (the code is there with
other wording), ``order`` (mapped sections out of the template's order), ``duplicate-section``
and ``unmapped-code`` (an EMA code our mapping does not list, reported for information).

Statements. The registry's items are matched against the text of the section they belong to:
its own paragraphs and the titles and paragraphs of its subsections, except subsections that
are registry sections themselves (4.1 under 4), which are checked on their own. A named
subsection such as Posology belongs to its section's text. Label text and template text are both
compared after runs of space, tab and no-break space are collapsed to one space. In a pattern:

- literal text must appear exactly, except that a space next to an optional segment may be
  absent (removing "<months>" from "{x to y} <years> <months>" leaves one space, not two);
- a fill-in (``{...}``) is any non-empty text of at most 300 characters within one paragraph
  (as little as possible between literals, and the rest of the line at the end);
- an optional segment (``<...>``) may be present or absent; a whole statement in ``<...>`` is
  matched on its content, since "absent" is the answer when it does not match;
- guidance (``[...]``) is not label text and is dropped, and so are footnote markers (runs of
  ``*``).

A statement spanning paragraphs (``<Traceability`` and the sentence under it) is matched
against as many consecutive paragraphs. A statement that matches is ``used``, with where it
matched. One that does not match but resembles a paragraph of its section (a word-level
similarity of at least ``SIMILARITY``, difflib's ratio over a window of the paragraph as long as
the statement, taking the statement with all of its optional segments or with none, whichever
is closer) is a ``deviation`` finding with the word-level differences; a person decides
whether the wording was changed on purpose. A paragraph that already holds an exact match of
another statement of the same section or appendix is not compared again, so one statement
matching exactly does not make its sibling a deviation. A statement with no required literal
text of at least ``MIN_LITERAL`` characters is not checked (too little to tell). A
non-optional statement or subheading that is absent is a ``missing-statement`` or
``missing-subheading`` finding.

Sections the reader refused are ``refused-section`` findings, and their text is not checked:
the checker never guesses around them. Defects the reader read through by a stated rule are
``xhtml-defect`` findings. Colour and shading marks are ``formatting`` findings: coloured or
highlighted text in a published SmPC is usually a left-over from review.
"""

from __future__ import annotations

import difflib
import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from typing import Any

from zone_a.docx.reader import Paragraph
from zone_a.epi.reader import READER_VERSION, Document, Section, read_epi, walk
from zone_a.qrd.headings import collapse, index, match_heading
from zone_a.qrd.pattern import Token, parse

CHECKER_VERSION = "qrd-check/1.0.0"
SIMILARITY = 0.85
MIN_LITERAL = 12
FILL_LIMIT = 300
EXCERPT = 80


@dataclass(frozen=True)
class _Target:
    key: str
    code: str
    title: str
    required: bool
    order: int


def _targets(mapping: dict[str, Any]) -> dict[str, _Target]:
    out: dict[str, _Target] = {}

    def visit(node: dict[str, Any]) -> None:
        out[node["targetCode"]] = _Target(
            key=node["sourceKey"],
            code=node["targetCode"],
            title=node["title"],
            required=bool(node.get("required")),
            order=len(out),
        )
        for child in node.get("children", []):
            visit(child)

    visit(mapping["root"])
    return out


def _excerpt(text: str) -> str:
    text = collapse(text)
    return text if len(text) <= EXCERPT else text[: EXCERPT - 1] + "\u2026"


# --- patterns -------------------------------------------------------------------------------


def _regex(tokens: list[Token]) -> str:
    out: list[str] = []
    for position, token in enumerate(tokens):
        value = token["value"]
        if token["kind"] == "text":
            assert isinstance(value, str)
            literal = collapse(value) if value.strip() else " "
            if value[:1].isspace() and literal != " ":
                literal = " " + literal
            if value[-1:].isspace() and literal != " ":
                literal = literal + " "
            out.append(re.escape(literal).replace("\\ ", " ?"))
        elif token["kind"] == "fill":
            # Lazy between literals; at the end of the pattern it takes the rest of the line, so
            # the reported span covers what was filled in.
            lazy = "?" if position < len(tokens) - 1 else ""
            out.append(f"[^\\n]{{1,{FILL_LIMIT}}}{lazy}")
        elif token["kind"] == "optional":
            assert isinstance(value, list)
            out.append(f"(?:{_regex(value)})?")
    return "".join(out)


def _literals(tokens: list[Token], required: bool = True) -> list[str]:
    """The literal pieces a match must contain."""
    out: list[str] = []
    for token in tokens:
        if token["kind"] == "text" and required:
            text = collapse(str(token["value"]))
            if text:
                out.append(text)
    return out


def _render(tokens: list[Token], optional: bool) -> str:
    out: list[str] = []
    for token in tokens:
        value = token["value"]
        if token["kind"] == "text":
            out.append(str(value))
        elif token["kind"] == "fill":
            out.append("\u2026")
        elif token["kind"] == "optional" and optional:
            assert isinstance(value, list)
            out.append(_render(value, optional))
    return "".join(out)


def _reference(tokens: list[Token], optional: bool) -> list[str]:
    """The statement as words, with every optional segment present or every one absent, and
    each fill-in shown as an ellipsis."""
    return collapse(_render(tokens, optional)).split()


_NOTE_MARKER = re.compile(r"\*+")


def _without_notes(tokens: list[Token]) -> list[Token]:
    """The tokens with footnote markers removed: in the QRD templates a run of ``*`` only ever
    points at a note (Appendix III writes "<Keep the {container}*** in the outer carton"), and
    the label does not carry it."""
    out: list[Token] = []
    for token in tokens:
        value = token["value"]
        if token["kind"] == "text":
            out.append({"kind": "text", "value": _NOTE_MARKER.sub("", str(value))})
        elif token["kind"] == "optional":
            assert isinstance(value, list)
            out.append({"kind": "optional", "value": _without_notes(value)})
        else:
            out.append(token)
    return out


def _content(item_pattern: list[Token]) -> list[Token]:
    """A statement wholly in ``<...>`` is matched on its content, without footnote markers."""
    item_pattern = _without_notes(item_pattern)
    meaningful = [t for t in item_pattern if t["kind"] != "guidance" and str(t["value"]).strip()]
    if len(meaningful) == 1 and meaningful[0]["kind"] == "optional":
        value = meaningful[0]["value"]
        assert isinstance(value, list)
        return value
    return [t for t in item_pattern if t["kind"] != "guidance"]


@dataclass(frozen=True)
class _Located:
    """One paragraph of text, or a run of consecutive paragraphs joined by line feeds."""

    section: str
    paragraph: int
    text: str
    # Identity of the paragraphs, for telling which ones an exact match already explains.
    keys: tuple[tuple[int, int], ...]


def _parts(tokens: list[Token]) -> int:
    """How many paragraphs a pattern spans (the registry joins them with a line feed)."""
    count = 1
    for token in tokens:
        value = token["value"]
        if token["kind"] == "optional":
            assert isinstance(value, list)
            count += _parts(value) - 1
        else:
            count += str(value).count("\n")
    return count


def _windows(lines: list[_Located], size: int) -> list[_Located]:
    if size == 1:
        return lines
    out: list[_Located] = []
    for start in range(len(lines) - size + 1):
        run = lines[start : start + size]
        out.append(
            _Located(
                run[0].section,
                run[0].paragraph,
                "\n".join(line.text for line in run),
                tuple(key for line in run for key in line.keys),
            )
        )
    return out


def _search(tokens: list[Token], lines: list[_Located]) -> tuple[_Located, int, int] | None:
    literals = _literals(tokens)
    anchor = max(literals, key=len) if literals else ""
    compiled = re.compile(_regex(tokens))
    for line in _windows(lines, _parts(tokens)):
        if anchor and anchor not in line.text:
            continue
        match = compiled.search(line.text)
        if match is not None and match.end() > match.start():
            return line, match.start(), match.end()
    return None


def _closest(
    reference: list[str], lines: list[_Located], size: int
) -> tuple[float, _Located, list[str]] | None:
    best: tuple[float, _Located, list[str]] | None = None
    length = len(reference)
    for line in _windows(lines, size):
        words = line.text.split()
        if not words:
            continue
        for start in range(max(1, len(words) - length + 1)):
            window = words[start : start + length]
            ratio = difflib.SequenceMatcher(None, reference, window, autojunk=False).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, line, words)
    return best


def _differences(reference: list[str], words: list[str]) -> list[dict[str, str]]:
    """Word-level differences from the statement to the paragraph it resembles. A paragraph
    much longer than the statement is compared on its best-matching stretch only."""
    matcher = difflib.SequenceMatcher(None, reference, words, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    if len(words) > 2 * len(reference) and blocks:
        first, last = blocks[0], blocks[-1]
        words = words[
            max(0, first.b - first.a) : last.b + last.size + (len(reference) - last.a - last.size)
        ]
        matcher = difflib.SequenceMatcher(None, reference, words, autojunk=False)
    out: list[dict[str, str]] = []
    for operation, a1, a2, b1, b2 in matcher.get_opcodes():
        if operation != "equal":
            out.append(
                {
                    "change": operation,
                    "template": " ".join(reference[a1:a2]),
                    "label": " ".join(words[b1:b2]),
                }
            )
    return out


# --- the check ------------------------------------------------------------------------------


class _Report:
    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []
        self.statements: list[dict[str, Any]] = []

    def finding(self, kind: str, **details: Any) -> None:
        self.findings.append({"kind": kind, **details})


def _lines(section: Section, own: set[str]) -> tuple[list[_Located], list[str]]:
    """The text of a section for statement matching, and the titles of refused parts."""
    lines: list[_Located] = []
    refused: list[str] = []

    def add(current: Section, path: str, top: bool) -> None:
        if current.refusal is not None:
            refused.append(path)
        if not top:
            lines.append(_Located(path, -1, collapse(current.title), ((id(current), -1),)))
        for number, paragraph in enumerate(current.paragraphs):
            text = collapse(paragraph.text)
            if text:
                # A paragraph of only spaces is a blank line between paragraphs, not text.
                lines.append(_Located(path, number, text, ((id(current), number),)))
        for child in current.sections:
            if child.code not in own:
                add(child, f"{path} > {child.title}", False)

    add(section, section.title, True)
    return lines, refused


@dataclass(frozen=True)
class _Job:
    identifier: str
    item: dict[str, Any]
    lines: list[_Located]
    refused: list[str]
    # Items that are alternatives of each other: an exact match of one explains a paragraph.
    group: str


def _exact(report: _Report, job: _Job, explained: dict[str, set[tuple[int, int]]]) -> bool:
    """Record an exact match, or return False to leave the item for the second pass."""
    item = job.item
    pattern = item.get("pattern")
    if pattern is None:
        report.statements.append({"id": job.identifier, "status": "not-checkable"})
        return True
    tokens = _content(pattern)
    if item["kind"] == "subheading":
        wanted = collapse("".join(str(t["value"]) for t in tokens if t["kind"] == "text"))
        found = next((line for line in job.lines if line.text == wanted), None)
        if found is not None:
            report.statements.append({"id": job.identifier, "status": "used", "in": found.section})
        elif not item["optional"]:
            report.finding(
                "missing-subheading", id=job.identifier, text=wanted, refused=job.refused
            )
        return True
    if sum(len(text) for text in _literals(tokens)) < MIN_LITERAL:
        report.statements.append({"id": job.identifier, "status": "not-checkable"})
        return True
    located = _search(tokens, job.lines)
    if located is None:
        return False
    line, start, end = located
    explained.setdefault(job.group, set()).update(line.keys)
    report.statements.append(
        {
            "id": job.identifier,
            "status": "used",
            "in": line.section,
            "paragraph": line.paragraph,
            "start": start,
            "end": end,
        }
    )
    return True


def _near(report: _Report, job: _Job, explained: dict[str, set[tuple[int, int]]]) -> None:
    item = job.item
    tokens = _content(item["pattern"])
    taken = explained.get(job.group, set())
    free = [line for line in job.lines if not set(line.keys) & taken]
    # The statement with all of its optional segments and with none; the closer one counts.
    best: tuple[float, _Located, list[str], list[str]] | None = None
    for reference in (_reference(tokens, True), _reference(tokens, False)):
        closest = _closest(reference, free, _parts(tokens)) if len(reference) >= 4 else None
        if closest is not None and (best is None or closest[0] > best[0]):
            best = (*closest, reference)
    if best is not None and best[0] >= SIMILARITY:
        ratio, line, words, reference = best
        report.finding(
            "deviation",
            id=job.identifier,
            similarity=round(ratio, 3),
            **{"in": line.section, "paragraph": line.paragraph},
            differences=_differences(reference, words),
        )
        report.statements.append({"id": job.identifier, "status": "deviation"})
        return
    report.statements.append({"id": job.identifier, "status": "absent"})
    if not item["optional"] and item["kind"] == "statement":
        report.finding("missing-statement", id=job.identifier, refused=job.refused)


def check(document: Document, registry: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    report = _Report()
    targets = _targets(mapping)
    registry_keys = {section["key"] for section in registry["sections"]}
    # Codes of sections checked on their own; every other subsection is part of its parent.
    own = {code for code, target in targets.items() if target.key in registry_keys}
    own.add(mapping["root"]["targetCode"])
    headings = index(registry)
    sections_by_key = {section["key"]: section for section in registry["sections"]}
    everything = walk(document.sections)
    paths: dict[int, str] = {}

    def trace(sections: tuple[Section, ...], parent: str) -> None:
        for part in sections:
            numbered = part.code in own or not parent
            paths[id(part)] = part.title if numbered else f"{parent} > {part.title}"
            trace(part.sections, paths[id(part)])

    # A numbered section starts a path of its own ("4.8 Undesirable effects"); an unnumbered
    # one is named after the section it belongs to ("5.3 Preclinical safety data > Metformin").
    trace(document.sections, "")

    for quirk in document.quirks:
        report.finding("document-quirk", detail=quirk)
    for section in everything:
        if section.refusal is not None:
            report.finding(
                "refused-section",
                section=paths[id(section)],
                code=section.refusal.code,
                detail=section.refusal.detail,
            )
        for note in section.notes:
            report.finding("xhtml-defect", section=paths[id(section)], detail=note)
        for number, paragraph in enumerate(section.paragraphs):
            _formatting(report, paths[id(section)], number, paragraph)

    # Headings.
    found: dict[str, Section] = {}
    order: list[_Target] = []
    for section in everything:
        if section.code is None:
            continue
        target = targets.get(section.code)
        if target is None:
            report.finding("unmapped-code", section=paths[id(section)], code=section.code)
            continue
        if target.key in found:
            report.finding("duplicate-section", section=paths[id(section)], key=target.key)
            continue
        found[target.key] = section
        order.append(target)
        if target.key in sections_by_key:
            match = match_heading(section.title, headings)
            ok = match is not None and match.key == target.key
        else:
            ok = collapse(section.title) == collapse(target.title)
        if not ok:
            report.finding(
                "heading-text",
                key=target.key,
                code=target.code,
                expected=target.title,
                found=section.title,
            )
    for previous, current in itertools.pairwise(order):
        if current.order < previous.order:
            report.finding("order", key=current.key, after=previous.key)
    for target in targets.values():
        if target.required and target.key not in found:
            report.finding("missing-heading", key=target.key, expected=target.title)

    # Statements: every item of every section, then those placed around the sections, then
    # the appendices.
    jobs: list[_Job] = []
    for key, section_entry in sections_by_key.items():
        part = found.get(key)
        if part is None:
            continue
        lines, refused = _lines(part, own)
        for number, item in enumerate(section_entry["items"]):
            if item["kind"] in ("statement", "subheading"):
                jobs.append(_Job(f"{key}#{number}", item, lines, refused, key))
    # The monitoring statement stands before section 1 (in the root section's own text); the
    # closing statement follows section 10, where an ePI puts it.
    for number, item in enumerate(registry["documentStatements"]):
        place = found.get("smpc" if item["placement"] == "before-section-1" else "smpc.10")
        if place is not None:
            lines, refused = _lines(place, own)
            jobs.append(_Job(f"document#{number}", item, lines, refused, "document"))
    appendices = registry["appendices"]
    for name, owner in (("I", "smpc.4.6"), ("III", "smpc.6.4")):
        part = found.get(owner)
        if part is None:
            continue
        lines, refused = _lines(part, own)
        entries = appendices[name]["entries"] if name == "I" else appendices[name]["items"]
        for number, entry in enumerate(entries):
            item = {"kind": "statement", "optional": True, "pattern": entry.get("pattern")}
            identifier = f"appendix-{name}#{entry.get('id', number)}"
            jobs.append(_Job(identifier, item, lines, refused, f"appendix-{name}"))
    part = found.get("smpc.4.8")
    if part is not None:
        lines, refused = _lines(part, own)
        for group in appendices["II"]["groups"].values():
            for row in group:
                item = {"kind": "statement", "optional": True, "pattern": parse(row["text"])}
                jobs.append(_Job(f"appendix-II#{row['code']}", item, lines, refused, "appendix-II"))

    explained: dict[str, set[tuple[int, int]]] = {}
    pending = [job for job in jobs if not _exact(report, job, explained)]
    for job in pending:
        _near(report, job, explained)
    report.statements.sort(
        key=lambda statement: [job.identifier for job in jobs].index(statement["id"])
    )

    return {
        "checker": CHECKER_VERSION,
        "template": registry["template"],
        "registryVersion": registry["registryVersion"],
        "document": {"title": document.title, "date": document.date},
        "summary": _summary(report),
        "findings": report.findings,
        "statements": report.statements,
    }


def _formatting(report: _Report, section: str, number: int, paragraph: Paragraph) -> None:
    for mark in paragraph.marks:
        covered = paragraph.text[mark.start : mark.end]
        if not any(c.isalnum() for c in covered):
            # A coloured picture or shaded space shows no text differently.
            continue
        if mark.kind.startswith(("color-", "shading-")) or mark.kind == "faint":
            report.finding(
                "formatting",
                section=section,
                paragraph=number,
                mark=mark.kind,
                text=_excerpt(covered),
            )


def _summary(report: _Report) -> dict[str, dict[str, int]]:
    findings: dict[str, int] = {}
    for finding in report.findings:
        findings[finding["kind"]] = findings.get(finding["kind"], 0) + 1
    statements: dict[str, int] = {}
    for statement in report.statements:
        statements[statement["status"]] = statements.get(statement["status"], 0) + 1
    return {
        "findings": dict(sorted(findings.items())),
        "statements": dict(sorted(statements.items())),
    }


def report(file: str, data: bytes, registry: dict[str, Any], mapping: dict[str, Any]) -> str:
    """The committed check result for one pinned source file, as JSON text."""
    result = {
        "source": {"file": file, "sha256": hashlib.sha256(data).hexdigest()},
        "reader": READER_VERSION,
        **check(read_epi(data), registry, mapping),
    }
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
