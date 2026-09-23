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
compared after runs of space, tab and no-break space are collapsed to one space. Characters the
reader marked struck through or faint are masked: no statement matches them. In a pattern:

- literal text must appear exactly, every space included; a space between literal text and an
  optional segment belongs to the segment ("above <25 C>" is "above 25 C" or "above", never
  "above25 C"), and spaces at either end of the pattern are dropped;
- a fill-in (``{...}``) is any non-empty text of at most 300 characters within one paragraph
  (as little as possible, except at the very end of the pattern, where it takes the rest of the
  line);
- an optional segment (``<...>``) may be present or absent; a whole statement in ``<...>`` is
  matched on its content, since "absent" is the answer when it does not match;
- guidance (``[...]``) is not label text and is dropped, and so are footnote markers (runs of
  ``*``).

A statement spanning paragraphs (``<Traceability`` and the sentence under it) is matched
against as many consecutive paragraphs. A statement that matches is ``used``, with where it
matched: the section's path, the paragraph's index and the character offsets in the
paragraph's text as the reader returned it (``lastParagraph`` when it runs over several). One
that does not match but resembles a paragraph of its section (a word-level
similarity of at least ``SIMILARITY``, difflib's ratio over a window of the paragraph as long as
the statement, taking the statement with all of its optional segments or with none, whichever
is closer) is a ``deviation`` finding with the word-level differences; a person decides
whether the wording was changed on purpose. The differences run from where the resemblance
starts to the end of the sentence in which the statement's last matching word falls; text in
the place of a fill-in is not a difference, and a run of struck or faint characters is shown
as one word, ``HIDDEN_WORD``. A resemblance in the readable part of a section is reported even
when another part was refused. Characters that an exact match of another statement of the same
section or appendix explains are not compared again, so one statement matching exactly does not make
its sibling a deviation, and the rest of the paragraph is still compared. A statement with no
required literal text of at least ``MIN_LITERAL`` characters is ``not-checkable`` (too little
to tell). A non-optional statement or subheading that is absent is a ``missing-statement`` or
``missing-subheading`` finding.

Sections the reader refused are ``refused-section`` findings. A statement not found in a
section with a refused part is ``not-checked``, not ``absent``: it may be in the part that
could not be read, and the checker never guesses around it. Defects the reader read through
by a stated rule are ``xhtml-defect`` findings. Colour, shading, strike-through and faint marks
over text are ``formatting`` findings: coloured, highlighted or struck text in a published SmPC
is usually a left-over from review.
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
TERMINAL = (".", ":", ";", "!", "?")


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


# Characters a reader cannot see (struck through or faint) are masked with HIDDEN, so no literal
# of a pattern matches them and no fill-in takes them; in a deviation's differences a run of them
# is one word, HIDDEN_WORD. Characters an exact match of a sibling statement already explains are
# masked with TAKEN before the near-match pass, and a run of them separates words.
HIDDEN = "\x00"
TAKEN = "\x01"
HIDDEN_WORD = "[struck or faint text]"
_HIDING = {"strike", "faint"}
_SPACES = " \t\u00a0"


def _collapse(text: str) -> tuple[str, tuple[int, ...]]:
    """The text as ``headings.collapse`` gives it, with, for each character kept, its index in
    ``text``."""
    out: list[str] = []
    positions: list[int] = []
    pending: int | None = None
    for offset, character in enumerate(text):
        if character in _SPACES:
            if out and pending is None:
                pending = offset
            continue
        if pending is not None:
            out.append(" ")
            positions.append(pending)
            pending = None
        out.append(character)
        positions.append(offset)
    # headings.collapse ends with str.strip(): no whitespace of any kind at either end.
    start, end = 0, len(out)
    while start < end and out[start].isspace():
        start += 1
    while end > start and out[end - 1].isspace():
        end -= 1
    return "".join(out[start:end]), tuple(positions[start:end])


def _squeeze(value: str) -> str:
    """Runs of space, tab and no-break space as one space, none next to a paragraph break or at
    either end; paragraph breaks kept."""
    text = re.sub(f"[{_SPACES}]+", " ", value).strip(" ")
    return re.sub(r" ?\n ?", "\n", text)


def _regex(tokens: list[Token], at_end: bool = True) -> str:
    """The pattern as a regular expression over collapsed text.

    A space is required. A space between literal text and an optional segment belongs to the
    segment ("above <25 \u00b0C>" matches "above 25 \u00b0C" and "above", not "above25 \u00b0C"),
    and spaces at either end of the pattern are dropped. A space after an optional segment that
    opens the pattern may be absent."""
    out: list[str] = []
    last = len(tokens) - 1
    carry = False
    for position, token in enumerate(tokens):
        value = token["value"]
        before = tokens[position - 1]["kind"] if position else None
        after = tokens[position + 1]["kind"] if position < last else None
        if token["kind"] == "text":
            assert isinstance(value, str)
            core = _squeeze(value)
            opens = before == "optional" and position == 1
            if not core:
                if after == "optional":
                    carry = True
                elif before is not None and after is not None:
                    out.append(" ?" if opens else " ")
                continue
            piece = re.escape(core).replace("\\ ", " ")
            if value[:1] in _SPACES and before is not None:
                piece = (" ?" if opens else " ") + piece
            if value[-1:] in _SPACES:
                if after == "optional":
                    carry = True
                elif after is not None:
                    piece += " "
            out.append(piece)
        elif token["kind"] == "fill":
            # As little as possible, except at the very end of the pattern, where it takes the
            # rest of the line so the reported span covers what was filled in.
            lazy = "" if at_end and position == last else "?"
            out.append(f"[^\\n{HIDDEN}{TAKEN}]{{1,{FILL_LIMIT}}}{lazy}")
        elif token["kind"] == "optional":
            assert isinstance(value, list)
            inner = _regex(value, at_end and position == last)
            out.append(f"(?:{' ' if carry else ''}{inner})?")
            carry = False
    return "".join(out)


def _literals(tokens: list[Token]) -> list[str]:
    """The literal pieces a match must contain."""
    return [text for t in tokens if t["kind"] == "text" and (text := _squeeze(str(t["value"])))]


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


def _content(item_pattern: list[Token]) -> list[Token]:
    """A statement wholly in ``<...>`` is matched on its content, without footnote markers and
    guidance."""
    item_pattern = _without_notes(item_pattern)
    meaningful = [t for t in item_pattern if t["kind"] != "guidance" and str(t["value"]).strip()]
    if len(meaningful) == 1 and meaningful[0]["kind"] == "optional":
        value = meaningful[0]["value"]
        assert isinstance(value, list)
        return _joined(value)
    return _joined(item_pattern)


def _joined(tokens: list[Token]) -> list[Token]:
    """The tokens without guidance, with the literal text on either side of it made one."""
    out: list[Token] = []
    for token in tokens:
        value = token["value"]
        if token["kind"] == "guidance":
            continue
        if token["kind"] == "optional":
            assert isinstance(value, list)
            token = {"kind": "optional", "value": _joined(value)}
        if token["kind"] == "text" and out and out[-1]["kind"] == "text":
            out[-1] = {"kind": "text", "value": str(out[-1]["value"]) + str(value)}
        else:
            out.append(token)
    return out


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


# --- the text of a section ------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    """One paragraph (or a subsection's title, ``paragraph`` -1), collapsed, with the characters
    a reader cannot see masked."""

    path: str
    paragraph: int
    text: str
    # For each character of ``text``, its index in the paragraph's own text.
    positions: tuple[int, ...]
    key: tuple[int, int]


@dataclass(frozen=True)
class _Window:
    """Consecutive lines joined by line feeds, for a statement over several paragraphs."""

    lines: tuple[_Line, ...]
    text: str
    # For each character of ``text``: (line, character of that line), or None for a join.
    origin: tuple[tuple[int, int] | None, ...]


def _visible(paragraph: Paragraph) -> str:
    text = list(paragraph.text)
    for mark in paragraph.marks:
        if mark.kind in _HIDING:
            text[mark.start : mark.end] = HIDDEN * (mark.end - mark.start)
    return "".join(text)


def _lines(section: Section, own: set[str], paths: dict[int, str]) -> tuple[list[_Line], list[str]]:
    """The text of a section for statement matching, and the paths of its refused parts."""
    lines: list[_Line] = []
    refused: list[str] = []

    def add(current: Section, top: bool) -> None:
        path = paths[id(current)]
        if current.refusal is not None:
            refused.append(path)
        if not top:
            text, positions = _collapse(current.title)
            lines.append(_Line(path, -1, text, positions, (id(current), -1)))
        for number, paragraph in enumerate(current.paragraphs):
            text, positions = _collapse(_visible(paragraph))
            if text.strip(HIDDEN):
                # A paragraph of only spaces is a blank line between paragraphs, not text.
                lines.append(_Line(path, number, text, positions, (id(current), number)))
        for child in current.sections:
            if child.code not in own:
                add(child, False)

    add(section, True)
    return lines, refused


def _windows(lines: list[_Line], size: int) -> list[_Window]:
    out: list[_Window] = []
    for start in range(len(lines) - size + 1):
        run = lines[start : start + size]
        text: list[str] = []
        origin: list[tuple[int, int] | None] = []
        for number, line in enumerate(run):
            if number:
                text.append("\n")
                origin.append(None)
            text.append(line.text)
            origin.extend((number, offset) for offset in range(len(line.text)))
        out.append(_Window(tuple(run), "".join(text), tuple(origin)))
    return out


def _mask(lines: list[_Line], taken: dict[tuple[int, int], set[int]]) -> list[_Line]:
    """The lines with the characters an exact match already explains masked."""
    out: list[_Line] = []
    for line in lines:
        used = taken.get(line.key)
        if used:
            text = "".join(TAKEN if at in used else c for at, c in enumerate(line.text))
            line = _Line(line.path, line.paragraph, text, line.positions, line.key)
        out.append(line)
    return out


@dataclass(frozen=True)
class _Match:
    window: _Window
    start: int
    end: int

    def location(self) -> dict[str, Any]:
        first = self.window.origin[self.start]
        last = self.window.origin[self.end - 1]
        assert first is not None
        assert last is not None
        head, tail = self.window.lines[first[0]], self.window.lines[last[0]]
        where: dict[str, Any] = {
            "in": head.path,
            "paragraph": head.paragraph,
            "start": head.positions[first[1]],
            "end": tail.positions[last[1]] + 1,
        }
        if tail is not head:
            where["lastParagraph"] = tail.paragraph
            where["lastIn"] = tail.path
        return where

    def characters(self) -> dict[tuple[int, int], set[int]]:
        out: dict[tuple[int, int], set[int]] = {}
        for place in self.window.origin[self.start : self.end]:
            if place is not None:
                out.setdefault(self.window.lines[place[0]].key, set()).add(place[1])
        return out


def _search(tokens: list[Token], lines: list[_Line]) -> _Match | None:
    literals = _literals(tokens)
    anchor = max(literals, key=len) if literals else ""
    compiled = re.compile(_regex(tokens))
    for window in _windows(lines, _parts(tokens)):
        if anchor and anchor not in window.text:
            continue
        match = compiled.search(window.text)
        if match is not None and match.end() > match.start():
            return _Match(window, match.start(), match.end())
    return None


def _words(text: str) -> list[str]:
    """Words for comparison: TAKEN separates words, and a run of HIDDEN is one word."""
    out: list[str] = []
    for word in re.split(f"[\\s{TAKEN}]+", text):
        for piece in re.split(f"({HIDDEN}+)", word):
            if piece:
                out.append(HIDDEN_WORD if piece[0] == HIDDEN else piece)
    return out


def _closest(
    reference: list[str], lines: list[_Line], size: int
) -> tuple[float, _Window, list[str], int] | None:
    best: tuple[float, _Window, list[str], int] | None = None
    length = len(reference)
    for window in _windows(lines, size):
        words = _words(window.text)
        if not words:
            continue
        for start in range(max(1, len(words) - length + 1)):
            stretch = words[start : start + length]
            ratio = difflib.SequenceMatcher(None, reference, stretch, autojunk=False).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, window, words, start)
    return best


def _differences(reference: list[str], words: list[str], start: int) -> list[dict[str, str]]:
    """Word-level differences from the statement to the stretch of the paragraph it resembles,
    carried on to the end of that sentence (at most half the statement's length again)."""
    limit = min(len(words), start + len(reference) + len(reference) // 2)
    blocks = [
        block
        for block in difflib.SequenceMatcher(
            None, reference, words[start:limit], autojunk=False
        ).get_matching_blocks()
        if block.size
    ]
    # The label's word where the statement's last matching word falls; the stretch runs from
    # there to the end of that sentence, not into the next one.
    end = start + (blocks[-1].b + blocks[-1].size if blocks else len(reference))
    while end < limit and not words[end - 1].endswith(TERMINAL):
        end += 1
    stretch = words[start:end]
    out: list[dict[str, str]] = []
    matcher = difflib.SequenceMatcher(None, reference, stretch, autojunk=False)
    for operation, a1, a2, b1, b2 in matcher.get_opcodes():
        if operation == "replace" and set(reference[a1:a2]) == {"\u2026"}:
            # Text in the place of a fill-in is what was filled in, not a difference.
            continue
        if operation != "equal":
            out.append(
                {
                    "change": operation,
                    "template": " ".join(reference[a1:a2]),
                    "label": " ".join(stretch[b1:b2]),
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


@dataclass(frozen=True)
class _Job:
    identifier: str
    item: dict[str, Any]
    lines: list[_Line]
    refused: list[str]
    # Items of one section or appendix: characters one of them matched exactly are not
    # compared again for another.
    group: str


_Taken = dict[str, dict[tuple[int, int], set[int]]]


def _exact(report: _Report, job: _Job, taken: _Taken) -> bool:
    """Record an exact match or a settled status; False leaves the item for the second pass."""
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
            report.statements.append({"id": job.identifier, "status": "used", "in": found.path})
        elif job.refused:
            report.statements.append({"id": job.identifier, "status": "not-checked"})
        else:
            report.statements.append({"id": job.identifier, "status": "absent"})
            if not item["optional"]:
                report.finding("missing-subheading", id=job.identifier, text=wanted)
        return True
    if sum(len(text) for text in _literals(tokens)) < MIN_LITERAL:
        report.statements.append({"id": job.identifier, "status": "not-checkable"})
        return True
    match = _search(tokens, job.lines)
    if match is None:
        return False
    group = taken.setdefault(job.group, {})
    for key, characters in match.characters().items():
        group.setdefault(key, set()).update(characters)
    report.statements.append({"id": job.identifier, "status": "used", **match.location()})
    return True


def _near(report: _Report, job: _Job, taken: _Taken) -> None:
    item = job.item
    tokens = _content(item["pattern"])
    lines = _mask(job.lines, taken.get(job.group, {}))
    # The statement with all of its optional segments and with none; the closer one counts.
    best: tuple[float, _Window, list[str], int, list[str]] | None = None
    for reference in (_reference(tokens, True), _reference(tokens, False)):
        closest = _closest(reference, lines, _parts(tokens)) if len(reference) >= 4 else None
        if closest is not None and (best is None or closest[0] > best[0]):
            best = (*closest, reference)
    differences = _differences(best[4], best[2], best[3]) if best is not None else []
    if best is not None and best[0] >= SIMILARITY and differences:
        ratio, window = best[0], best[1]
        # A resemblance in the readable part is reported even when another part of the section
        # was refused: the differing wording is there to see.
        report.finding(
            "deviation",
            id=job.identifier,
            similarity=round(ratio, 3),
            **{"in": window.lines[0].path, "paragraph": window.lines[0].paragraph},
            differences=differences,
        )
        report.statements.append({"id": job.identifier, "status": "deviation"})
        return
    if job.refused:
        # Part of the section could not be read; the statement may be there.
        report.statements.append({"id": job.identifier, "status": "not-checked"})
        return
    report.statements.append({"id": job.identifier, "status": "absent"})
    if not item["optional"] and item["kind"] == "statement":
        report.finding("missing-statement", id=job.identifier)


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
        lines, refused = _lines(part, own, paths)
        for number, item in enumerate(section_entry["items"]):
            if item["kind"] in ("statement", "subheading"):
                jobs.append(_Job(f"{key}#{number}", item, lines, refused, key))
    # The monitoring statement stands before section 1 (in the root section's own text); the
    # closing statement follows section 10, where an ePI puts it.
    for number, item in enumerate(registry["documentStatements"]):
        place = found.get("smpc" if item["placement"] == "before-section-1" else "smpc.10")
        if place is not None:
            lines, refused = _lines(place, own, paths)
            jobs.append(_Job(f"document#{number}", item, lines, refused, "document"))
    appendices = registry["appendices"]
    for name, owner in (("I", "smpc.4.6"), ("III", "smpc.6.4")):
        part = found.get(owner)
        if part is None:
            continue
        lines, refused = _lines(part, own, paths)
        entries = appendices[name]["entries"] if name == "I" else appendices[name]["items"]
        for number, entry in enumerate(entries):
            item = {"kind": "statement", "optional": True, "pattern": entry.get("pattern")}
            identifier = f"appendix-{name}#{entry.get('id', number)}"
            jobs.append(_Job(identifier, item, lines, refused, f"appendix-{name}"))
    part = found.get("smpc.4.8")
    if part is not None:
        lines, refused = _lines(part, own, paths)
        for group in appendices["II"]["groups"].values():
            for row in group:
                item = {"kind": "statement", "optional": True, "pattern": parse(row["text"])}
                jobs.append(_Job(f"appendix-II#{row['code']}", item, lines, refused, "appendix-II"))

    taken: _Taken = {}
    pending = [job for job in jobs if not _exact(report, job, taken)]
    for job in pending:
        _near(report, job, taken)
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
        if mark.kind.startswith(("color-", "shading-")) or mark.kind in ("faint", "strike"):
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
