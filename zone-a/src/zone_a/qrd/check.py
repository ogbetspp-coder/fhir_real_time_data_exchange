"""Check an SmPC against the QRD template registry.

The checker compares a document read by the label reader (``zone_a.certified.read_epi``) with
the registry (``qrd/registry/cap-smpc-en-10.4.json``) and the section mapping
(``fhir/mappings/cap-smpc-en.json``) and proposes findings for a person to review. It never
changes, corrects or completes the label's text, and it states how it reached each finding so
the person can check it.

Headings. Each section's code is looked up in the mapping. A section whose code the mapping
knows must carry that section's heading: for a numbered section, one of the forms the registry
allows (``zone_a.qrd.headings``); for a named subsection (Posology, Method of administration,
Reporting of suspected adverse reactions), the mapping's title. Findings: ``missing-heading`` (a
required mapped section is not in the document), ``heading-text`` (the code is there with other
wording), ``order`` (mapped sections out of the template's order), ``duplicate-section`` and
``unmapped-code`` (an EMA code our mapping does not list, reported for information).

Statements. The registry's items are matched against the text of the section they belong to: its
own paragraphs and the titles and paragraphs of its subsections, except subsections that are
registry sections themselves (4.1 under 4), which are checked on their own. A named subsection
such as Posology belongs to its section's text, and so does an optional registry subsection the
mapping does not list (2.1 and 2.2, for advanced therapies: the template files section 2's
standard statements under 2.2, and they apply to every product). Label text and template text
are both compared after runs of space, tab and no-break space are collapsed to one space.
Characters the reader marked struck through or faint (faint text includes text on a background
it cannot be told from) are masked: no statement matches them. A subheading must be a line of
its own, exactly, but for "(s)". In a pattern:

- literal text must appear exactly, word for word, with a space wherever the template has one
  and a paragraph break wherever it has one (blank paragraphs fold into one break); the space or
  break before an optional segment belongs to the segment ("above <25 C>" is "above 25 C" or
  "above", never "above25 C"); two segments the template writes together between letters are
  separated by a space, as a person writes them; nothing is required before the first word,
  whose first letter may be either case (also after optional segments the label leaves out);
- a fill-in (``{...}``) is any non-empty text of at most 300 characters within one paragraph (as
  little as possible, except at the very end of the pattern, where it takes the rest of the
  line);
- an optional segment (``<...>``) may be present or absent; a whole statement in ``<...>`` is
  matched on its content, since "absent" is the answer when it does not match;
- guidance (``[...]``) is not label text and is dropped, and so are footnote markers (runs of
  ``*``);
- "(s)" after a word is the template's choice of singular or plural: "substance(s)" matches
  "substance" and "substances".

A statement spanning paragraphs (``<Traceability`` and the sentence under it) is matched against
as many consecutive paragraphs. A statement that matches is ``used``, with where it matched: the
section's path, the paragraph's index and the character offsets in the paragraph's text as the
reader returned it (``lastParagraph`` when it runs over several). One that does not match but
resembles a stretch of its section is a ``deviation`` finding, a proposal for a person to judge.
Resemblance is an alignment of the statement with the text, token by token (words, a word with
"(s)", and single punctuation marks): a token matches an equal one, a fill-in takes any run of
tokens within one paragraph, an optional segment is taken or skipped, and each substituted,
missing or inserted token costs one (a word for a punctuation mark, or the reverse, costs two).
The score is matched tokens over matched tokens plus cost, and a deviation needs at least
``SIMILARITY`` and either ``MIN_MATCHED`` matched words or every word outside the optional
segments. The differences are the alignment's runs of changes, the label's side as its text
reads; where the statement's last tokens are missing, the label's words to the end of that
sentence stand in their place. The same tokens with other spaces or paragraph breaks are a
``layout`` difference; the search takes in up to two paragraphs more than the statement spans,
so a statement set out over more paragraphs is found. Struck or faint text is one token,
``HIDDEN_WORD``, that nothing matches and no fill-in takes. Characters that an exact match of
another statement of the same section or appendix explains are not compared again, and where two
resemblances of one section or appendix overlap, only the closer is reported. A resemblance in
the readable part of a section is reported even when another part was refused. A statement with
less than ``MIN_LITERAL`` characters of required literal text is too little to tell on its own;
when it is made of alternatives, each is matched instead (``_alternatives``: each optional
segment taken as required in turn, and in one still too short each of its own segments), and a
``used`` statement names the alternatives that matched, a deviation the one it resembles. One
with no alternative long enough is ``not-checkable`` (reason ``too-little-text``), and so is an
Appendix I entry the EMA left unbalanced (``unbalanced-brackets``). A non-optional statement or
subheading that is absent is a ``missing-statement`` or ``missing-subheading`` finding.

Every statement and subheading of the registry gets exactly one status. One with no text to be
checked against is ``not-checked`` with the reason: ``section-absent`` (its section is not in the
document) or ``section-not-mapped`` (the mapping has no code for its section, as for section 12).
Sections the reader refused are ``refused-section`` findings. A statement not found in a section
with a refused part is ``not-checked`` (``refused-part``), not ``absent``: it may be in the part
that could not be read, and the checker never guesses around it. Defects the reader read through
by a stated rule are ``xhtml-defect`` findings. Colour, shading, strike-through and faint marks
over text are ``formatting`` findings: coloured, highlighted or struck text in a published SmPC
is usually a left-over from review. So is an underline over text it can change
(``zone_a.underline``: an underlined "<" is drawn "≤"), which the text alone reads as the plain
sign. The colour a browser draws a link in (``#0000ee``, under the link's underline) is the
browser's, not the label's, and is not a finding.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections import Counter, deque
from dataclasses import dataclass, replace
from typing import Any

from label_docx.epi import READER_VERSION, Document, Section, walk
from label_docx.epi_output import FORMAT_VERSION
from label_docx.reader import Mark, Paragraph

from zone_a.certified import read_epi
from zone_a.qrd.headings import collapse, index, match_heading
from zone_a.qrd.pattern import Token, children, parse
from zone_a.underline import underline_changes

# The version of the rules in this module and in headings.py, pattern.py and zone_a.underline.
# A change to any of them changes its hash in versions.lock.json, and
# tests/test_versions_lock.py then requires a new version here.
CHECKER_VERSION = "qrd-check/1.4.2"
SIMILARITY = 0.85
MIN_LITERAL = 12
FILL_LIMIT = 300
EXCERPT = 80
MIN_MATCHED = 6
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
    """The text as ``headings.collapse`` gives it, with where each kept character came from.

    For each character kept, its index in ``text``.
    """
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


@dataclass(frozen=True)
class _Piece:
    """A word of literal text, a fill-in or an optional segment, and the joint before it.

    The joint is nothing, a space or a paragraph break.
    """

    joint: str
    kind: str
    text: str = ""
    pieces: tuple[_Piece, ...] = ()

    def first(self) -> str | None:
        if self.kind == "text":
            return self.text[0]
        return self.pieces[0].first() if self.kind == "optional" else None

    def last(self) -> str | None:
        if self.kind == "text":
            return self.text[-1]
        return self.pieces[-1].last() if self.kind == "optional" else None


_ORDER = {"": 0, " ": 1, "\n": 2}
_PLURAL = re.compile(r"\([sS]\)")


def _stronger(one: str, other: str) -> str:
    return one if _ORDER[one] >= _ORDER[other] else other


def _pieces(tokens: list[Token]) -> tuple[list[_Piece], str, str]:
    """The pieces of a pattern, and the joints before its first piece and after its last.

    Whitespace between two pieces is their joint: a paragraph break if it holds a line feed
    (blank paragraphs fold into one), otherwise a space. Whitespace at the start or end of an
    optional segment is lifted out of it, so the segment owns the joint before it and the
    joint after it belongs to what follows. Two pieces the template writes together, one of
    them optional, with a letter or digit on each side ("<due to the rarity of the
    disease><for scientific reasons>"), are joined by a space, as a person writes them.
    """
    out: list[_Piece] = []
    pending = ""
    leading: str | None = None

    def add(piece: _Piece) -> None:
        nonlocal pending, leading
        if not out:
            leading = pending
            joint = ""
        else:
            joint = pending
            previous = out[-1]
            if not joint and "optional" in (previous.kind, piece.kind):
                before, after = previous.last(), piece.first()
                if before and after and before.isalnum() and after.isalnum():
                    joint = " "
        out.append(_Piece(joint, piece.kind, piece.text, piece.pieces))
        pending = ""

    for token in tokens:
        value = token["value"]
        if token["kind"] == "text":
            for part in re.split(r"(\s+)", str(value)):
                if not part:
                    continue
                if part.isspace():
                    pending = _stronger(pending, "\n" if "\n" in part else " ")
                else:
                    add(_Piece("", "text", part))
        elif token["kind"] == "fill":
            add(_Piece("", "fill"))
        elif token["kind"] == "optional":
            inner, lead, trail = _pieces(children(token))
            if not inner:
                pending = _stronger(pending, _stronger(lead, trail))
                continue
            pending = _stronger(pending, lead)
            add(_Piece("", "optional", pieces=tuple(inner)))
            pending = trail
    return out, leading or "", pending


def _statement(pattern: list[Token]) -> list[_Piece]:
    """The pieces of a registry statement, without Appendix I's option letters.

    A capital letter alone at the start of a paragraph, before an optional segment ("A <Studies
    in animals have shown ...>"), names an option; the label does not carry it.
    """
    pieces = _pieces(_content(pattern))[0]
    kept: list[_Piece] = []
    for position, piece in enumerate(pieces):
        letter = piece.kind == "text" and re.fullmatch("[A-Z]", piece.text) is not None
        at_start = position == 0 or piece.joint == "\n"
        before = position + 1 < len(pieces) and pieces[position + 1].kind == "optional"
        if letter and at_start and before:
            if position + 1 < len(pieces):
                following = pieces[position + 1]
                pieces[position + 1] = _Piece(
                    _stronger(piece.joint, following.joint) if kept else "",
                    following.kind,
                    following.text,
                    following.pieces,
                )
            continue
        kept.append(piece)
    return kept


def _regex(pieces: list[_Piece], at_end: bool = True, opening: bool = True) -> str:
    """The pieces as a regular expression over collapsed text.

    An optional segment's joint is inside it, so an absent segment leaves no extra space or
    break. While every piece so far is optional and at the start of the pattern, the next joint
    may be absent too. A fill-in takes as little as it can, except where nothing required
    follows it to the end of the pattern: there it takes the rest of the line, so a reported
    span covers what was filled in.
    """
    out: list[str] = []
    for position, piece in enumerate(pieces):
        rest_optional = all(later.kind == "optional" for later in pieces[position + 1 :])
        joint = {"": "", " ": " ", "\n": "\\n"}[piece.joint]
        if joint and opening and position:
            # The segments before may all be absent: then nothing is needed here but the start
            # of the text or a space or break before it; if one is present, the joint is.
            joint = f"(?:{joint}|(?<![^ \\n]))"
        if (
            piece.kind == "optional"
            and piece.joint == "\n"
            and at_end
            and rest_optional
            and not any(char.isalnum() for run in _required(list(piece.pieces)) for char in run)
        ):
            # A trailing segment with no word of its own ("<{name} <...> <...>.>") would take
            # whatever text follows; it cannot be told from the next paragraph, so it is not
            # matched.
            continue
        if piece.kind == "text":
            # "(s)" is the template's choice of singular or plural ("substance(s)").
            text = re.escape(piece.text)
            if opening and piece.text[:1].isalpha():
                # A statement's first letter is a capital only where it starts a sentence.
                head = piece.text[0]
                text = f"[{head.upper()}{head.lower()}]" + re.escape(piece.text[1:])
            text = text.replace(r"\(s\)", r"(?:s|\(s\))?").replace(r"\(S\)", r"(?:S|\(S\))?")
            out.append(joint + text)
        elif piece.kind == "fill":
            lazy = "" if at_end and rest_optional else "?"
            out.append(f"{joint}[^\\n{HIDDEN}{TAKEN}]{{1,{FILL_LIMIT}}}{lazy}")
        else:
            inner = _regex(list(piece.pieces), at_end and rest_optional, opening=False)
            out.append(f"(?:{joint}{inner})?")
        if piece.kind != "optional":
            opening = False
    return "".join(out)


def _required(pieces: list[_Piece]) -> list[str]:
    """Runs of consecutive required words, as they must appear."""
    runs: list[str] = []
    current = ""
    for piece in pieces:
        if piece.kind == "text":
            current = current + piece.joint + piece.text if current else piece.text
        else:
            if current:
                runs.append(current)
            current = ""
    if current:
        runs.append(current)
    return runs


def _span(pieces: list[_Piece]) -> int:
    """The most paragraphs a pattern can run over."""
    breaks = 0
    for piece in pieces:
        breaks += piece.joint == "\n"
        if piece.kind == "optional":
            breaks += _span(list(piece.pieces)) - 1
    return breaks + 1


_NOTE_MARKER = re.compile(r"\*+")


def _without_notes(tokens: list[Token]) -> list[Token]:
    """The tokens with footnote markers removed.

    In the QRD templates a run of ``*`` only ever points at a note (Appendix III writes "<Keep
    the {container}*** in the outer carton"), and the label does not carry it.
    """
    out: list[Token] = []
    for token in tokens:
        value = token["value"]
        if token["kind"] == "text":
            out.append({"kind": "text", "value": _NOTE_MARKER.sub("", str(value))})
        elif token["kind"] == "optional":
            out.append({"kind": "optional", "value": _without_notes(children(token))})
        else:
            out.append(token)
    return out


def _content(item_pattern: list[Token]) -> list[Token]:
    """The tokens a statement is matched on, without footnote markers and guidance.

    A statement wholly in ``<...>`` is matched on its content.
    """
    item_pattern = _without_notes(item_pattern)
    meaningful = [t for t in item_pattern if t["kind"] != "guidance" and str(t["value"]).strip()]
    if len(meaningful) == 1 and meaningful[0]["kind"] == "optional":
        return _joined(children(meaningful[0]))
    return _joined(item_pattern)


def _joined(tokens: list[Token]) -> list[Token]:
    """The tokens without guidance, with the literal text on either side of it made one."""
    out: list[Token] = []
    for token in tokens:
        value = token["value"]
        if token["kind"] == "guidance":
            continue
        kept: Token = token
        if token["kind"] == "optional":
            kept = {"kind": "optional", "value": _joined(children(token))}
        if kept["kind"] == "text" and out and out[-1]["kind"] == "text":
            out[-1] = {"kind": "text", "value": str(out[-1]["value"]) + str(value)}
        else:
            out.append(kept)
    return out


# --- the text of a section ------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    """One paragraph, collapsed, with the characters a reader cannot see masked.

    A subsection's title is a line too, with ``paragraph`` -1.
    """

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
    """From each line, it and up to ``size - 1`` lines after it, joined by line feeds."""
    out: list[_Window] = []
    for start in range(len(lines)):
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
            out.append(_Line(line.path, line.paragraph, text, line.positions, line.key))
        else:
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
        if first is None or last is None:
            # A match is trimmed to text, so neither end is the break joining two lines.
            raise ValueError("a match that starts or ends between two lines")
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


def _lead(pieces: list[_Piece]) -> _Piece | None:
    """The first piece that is not optional: the one whose first letter ``_regex`` frees."""
    return next((piece for piece in pieces if piece.kind != "optional"), None)


def _search(pieces: list[_Piece], lines: list[_Line], views: _Views | None = None) -> _Match | None:
    # The longest stretch of required text, cut at each "(s)", must be in the window.
    stretches = [part for run in _required(pieces) for part in _PLURAL.split(run)]
    longest = max(range(len(stretches)), key=lambda at: len(stretches[at])) if stretches else -1
    anchor = stretches[longest] if stretches else ""
    lead = _lead(pieces)
    if longest == 0 and lead is not None and lead.kind == "text" and lead.text[:1].isalpha():
        # The first stretch starts with the statement's first letter, which ``_regex`` lets be
        # either case in the label, whatever optional segments stand before it.
        anchor = anchor[1:]
    compiled = re.compile(_regex(pieces))
    windows = views.windows(lines, _span(pieces)) if views else _windows(lines, _span(pieces))
    for window in windows:
        if anchor and anchor not in window.text:
            continue
        match = compiled.search(window.text)
        if match is None:
            continue
        start, end = match.start(), match.end()
        # A match never starts or ends on the line feed that joins two paragraphs.
        while start < end and window.origin[start] is None:
            start += 1
        while end > start and window.origin[end - 1] is None:
            end -= 1
        if end > start:
            return _Match(window, start, end)
    return None


# --- resemblance: aligning the statement with the label ----------------------------------

# Words, a word with the template's "(s)", and single punctuation marks, compared one by one.
_TOKEN = re.compile(r"[^\W_]+\([sS]\)|[^\W_]+|[^\w\s]|_")


@dataclass(frozen=True)
class _Node:
    """One step of a statement: a token, a fill-in, or an optional segment's opening or closing.

    ``end`` is the index of an opening's closing in the whole list.
    """

    kind: str
    text: str = ""
    space: bool = False
    end: int = 0
    # A token the template sets at the start of a paragraph.
    brk: bool = False
    # How many optional segments the node is inside.
    depth: int = 0


def _nodes(pieces: list[_Piece]) -> list[_Node]:
    out: list[_Node] = []
    for piece in pieces:
        if piece.kind == "text":
            for number, token in enumerate(_TOKEN.findall(piece.text)):
                joint = piece.joint if number == 0 else ""
                out.append(_Node("token", token, space=bool(joint), brk=joint == "\n"))
        elif piece.kind == "fill":
            out.append(_Node("fill", "\u2026", space=bool(piece.joint), brk=piece.joint == "\n"))
        else:
            start = len(out)
            out.append(_Node("open"))
            # The inner list's openings point at their closings within it; shift them to this one.
            inner = [
                _Node(
                    n.kind,
                    n.text,
                    n.space,
                    n.end + start + 1 if n.kind == "open" else n.end,
                    n.brk,
                    n.depth,
                )
                for n in _nodes(list(piece.pieces))
            ]
            if piece.joint:
                # The joint before the segment belongs to its first token or fill-in, however
                # deeply nested.
                for at, node in enumerate(inner):
                    if node.kind in ("token", "fill"):
                        inner[at] = _Node(
                            node.kind, node.text, True, node.end, piece.joint == "\n", node.depth
                        )
                        break
            out += inner
            out.append(_Node("close"))
            out[start] = _Node("open", end=len(out) - 1)
    return out


@dataclass(frozen=True)
class _Token:
    text: str
    line: int
    start: int
    end: int

    @property
    def fillable(self) -> bool:
        return self.text not in (HIDDEN_WORD, TAKEN)


def _tokens(window: _Window) -> list[_Token]:
    """The window's tokens.

    A run of struck or faint characters is one token, ``HIDDEN_WORD``, and a character an exact
    match of a sibling explains is a token nothing matches.
    """
    out: list[_Token] = []
    pattern = re.compile(f"{HIDDEN}+|{TAKEN}|" + _TOKEN.pattern)
    for number, line in enumerate(window.lines):
        for match in pattern.finditer(line.text):
            text = match.group()
            if text[0] == HIDDEN:
                text = HIDDEN_WORD
            out.append(_Token(text, number, match.start(), match.end()))
    return out


def _same(template: str, label: str, first: bool = False) -> bool:
    if template == label:
        return True
    if first and template[1:] == label[1:] and template[:1].lower() == label[:1].lower():
        return True
    plural = _PLURAL.search(template)
    return plural is not None and label in (
        template[: plural.start()],
        template[: plural.start()] + plural.group()[1],
    )


_INFINITE = 1 << 30


@dataclass(frozen=True)
class _Alignment:
    cost: int
    matched: int
    # (operation, node index or -1, token index or -1), in order.
    steps: tuple[tuple[str, int, int], ...]

    @property
    def score(self) -> float:
        return self.matched / (self.matched + self.cost) if self.matched else 0.0


def _align(nodes: list[_Node], tokens: list[_Token]) -> _Alignment | None:
    """The cheapest alignment of the whole statement with a stretch of the tokens.

    Edit distance over tokens, with the statement's structure: a token matches an equal token
    (cost 0) or is substituted, deleted or has a token inserted before it (cost 1 each; a word
    substituted for a punctuation mark, or the reverse, costs 2); a fill-in takes one or more
    tokens of one paragraph, at most ``FILL_LIMIT`` characters, never struck or faint text,
    starts a paragraph only where the template has a break before it (or it opens the
    statement), and never takes a whole paragraph unless the template has a break next (cost 0),
    or is missing (cost 1); an optional segment, nested or not, is taken or skipped (cost 0). Of
    two alignments that cost the same, the one with more matched tokens is kept. The stretch may
    start and end anywhere, but not on a substitution: a first or last token the label does not
    match is missing, not replaced by the word beside the stretch.
    """
    rows, columns = len(nodes) + 1, len(tokens) + 1
    # The tokens whose capital a label may drop mid-sentence: the statement's first, and the
    # first outside its opening optional segments (where ``_regex`` frees the first letter).
    opening = {next((n for n, node in enumerate(nodes) if node.kind == "token"), -1)}
    depth = 0
    for position, node in enumerate(nodes):
        depth += (node.kind == "open") - (node.kind == "close")
        if not depth and node.kind in ("token", "fill"):
            if node.kind == "token":
                opening.add(position)
            break
    cost = [[_INFINITE] * columns for _ in range(rows)]
    hits = [[0] * columns for _ in range(rows)]
    back: list[list[tuple[str, int, int] | None]] = [[None] * columns for _ in range(rows)]
    cost[0] = [0] * columns
    # Whether a fill-in may take a whole paragraph: only where the template has a break next,
    # or nothing follows. Whether it may start a paragraph: only where the template has a break
    # before it, or nothing comes before it.
    breaks = {}
    starts = {}
    for position, node in enumerate(nodes):
        if node.kind == "fill":
            after = next((n for n in nodes[position + 1 :] if n.kind in ("token", "fill")), None)
            breaks[position] = after is None or after.brk
            before = any(n.kind in ("token", "fill") for n in nodes[:position])
            starts[position] = node.brk or not before

    def relax(row: int, column: int, value: int, found: int, step: tuple[str, int, int]) -> None:
        # Of two alignments that cost the same, the one with more matched tokens is kept.
        if (value, -found) < (cost[row][column], -hits[row][column]):
            cost[row][column] = value
            hits[row][column] = found
            back[row][column] = step

    for row in range(rows):
        if row:
            for column in range(columns - 1):
                relax(
                    row,
                    column + 1,
                    cost[row][column] + 1,
                    hits[row][column],
                    ("insert", row, column),
                )
        if row == rows - 1:
            break
        node = nodes[row]
        current, found = cost[row], hits[row]
        if node.kind == "token":
            for column in range(columns):
                if current[column] >= _INFINITE:
                    continue
                relax(row + 1, column, current[column] + 1, found[column], ("delete", row, column))
                if column < columns - 1:
                    label = tokens[column].text
                    equal = _same(node.text, label, first=row in opening)
                    step = ("match" if equal else "replace", row, column)
                    # A word for a punctuation mark, or a mark for a word, is two changes (one
                    # missing, one added), not one substitution.
                    kinds = node.text[:1].isalnum() != (label == HIDDEN_WORD or label[:1].isalnum())
                    relax(
                        row + 1,
                        column + 1,
                        current[column] + (0 if equal else 2 if kinds else 1),
                        found[column] + equal,
                        step,
                    )
        elif node.kind == "fill":
            for column in range(columns):
                if current[column] < _INFINITE:
                    relax(
                        row + 1, column, current[column] + 1, found[column], ("delete", row, column)
                    )
                    token = tokens[column] if column < columns - 1 else None
                    if token is not None and not token.fillable:
                        # Struck or faint text where the fill-in stands is one change.
                        relax(
                            row + 1,
                            column + 1,
                            current[column] + 1,
                            found[column],
                            ("replace", row, column),
                        )
            # A fill-in ending with token column - 1 starts at the best origin within one
            # paragraph, over fillable tokens and at most FILL_LIMIT characters: a sliding
            # minimum over origins, kept in a queue ordered by (cost, -matches).
            queue: deque[int] = deque()
            low, line_start = 0, 0
            for column in range(1, columns):
                origin = column - 1
                token = tokens[origin]
                if not token.fillable:
                    queue.clear()
                    low = column
                    continue
                if origin == 0 or tokens[origin - 1].line != token.line:
                    queue.clear()
                    low = line_start = origin
                if current[origin] < _INFINITE and (origin != line_start or starts[row]):
                    key = (current[origin], -found[origin])
                    while queue and (current[queue[-1]], -found[queue[-1]]) >= key:
                        queue.pop()
                    queue.append(origin)
                while low < origin and token.end - tokens[low].start > FILL_LIMIT:
                    low += 1
                while queue and queue[0] < low:
                    queue.popleft()
                if token.end - tokens[low].start > FILL_LIMIT:
                    continue
                closes_line = column < columns - 1 and tokens[column].line != token.line
                if closes_line and not breaks[row]:
                    # A fill-in may run to the end of a paragraph without a break in the
                    # template next, but not take the whole paragraph.
                    options = [
                        at
                        for at in range(max(low, line_start + 1), column)
                        if current[at] < _INFINITE
                    ]
                    # (Starting at the paragraph's first token would take all of it.)
                    if options:
                        pick = min(options, key=lambda at: (current[at], -found[at], -at))
                        relax(row + 1, column, current[pick], found[pick], ("fill", row, pick))
                    continue
                if queue:
                    pick = queue[0]
                    relax(row + 1, column, current[pick], found[pick], ("fill", row, pick))
        else:
            for column in range(columns):
                if current[column] < _INFINITE:
                    relax(row + 1, column, current[column], found[column], ("pass", row, column))
                    if node.kind == "open":
                        relax(
                            node.end + 1,
                            column,
                            current[column],
                            found[column],
                            ("skip", row, column),
                        )
    last = cost[rows - 1]
    end = min(range(columns), key=lambda column: (last[column], -hits[rows - 1][column], column))
    if last[end] >= _INFINITE:
        return None
    steps: list[tuple[str, int, int]] = []
    row, column = rows - 1, end
    while True:
        previous = back[row][column]
        if previous is None:
            break
        operation, from_row, from_column = previous
        if operation == "fill":
            for index in range(column - 1, from_column - 1, -1):
                steps.append(("fill", from_row, index))
        elif operation == "insert":
            steps.append(("insert", -1, from_column))
        elif operation in ("match", "replace"):
            steps.append((operation, from_row, from_column))
        elif operation == "delete":
            steps.append(("delete", from_row, -1))
        row, column = from_row, from_column
    steps.reverse()
    # A stretch starts and ends on the label's own words: a first or last substitution is a
    # missing token, not a claim on the word beside the stretch.
    for order in (range(len(steps)), range(len(steps) - 1, -1, -1)):
        for position in order:
            operation, at, _ = steps[position]
            if operation == "replace":
                steps[position] = ("delete", at, -1)
            elif operation in ("match", "fill", "insert"):
                break
    matched = sum(1 for operation, _, _ in steps if operation == "match")
    return _Alignment(last[end], matched, tuple(steps))


@dataclass(frozen=True)
class _Near:
    alignment: _Alignment
    window: _Window
    tokens: list[_Token]
    nodes: list[_Node]
    # The alternative of the statement it resembles, for a statement of alternatives.
    alternative: str | None = None

    def used(self) -> list[int]:
        """The tokens the alignment covers, from its first to its last."""
        indices = [index for _, _, index in self.alignment.steps if index >= 0]
        return list(range(min(indices), max(indices) + 1)) if indices else []


def _depths(nodes: list[_Node]) -> list[_Node]:
    out: list[_Node] = []
    depth = 0
    for node in nodes:
        if node.kind == "close":
            depth -= 1
        out.append(_Node(node.kind, node.text, node.space, node.end, node.brk, depth))
        if node.kind == "open":
            depth += 1
    return out


@dataclass(frozen=True)
class _Prepared:
    """A window, its tokens, and the words it holds counted generously (see ``_closest``)."""

    window: _Window
    tokens: list[_Token]
    held: Counter[str]


def _prepare(window: _Window) -> _Prepared:
    tokens = _tokens(window)
    # Either case, with or without a final "s" or "(s)".
    held = Counter(token.text.lower() for token in tokens)
    held.update(token.text.lower()[:-1] for token in tokens if token.text.lower().endswith("s"))
    held.update(
        _PLURAL.sub("", token.text.lower()) for token in tokens if _PLURAL.search(token.text)
    )
    return _Prepared(window, tokens, held)


class _Views:
    """The windows of a section's lines, and their tokens, built once per check.

    The same lines are searched by every statement of a section or appendix, and masked lines
    are the same for every statement of a group, so each is built once. Keys are the lines'
    identity, and each entry holds its lines, so an identity is never reused during a check.
    """

    def __init__(self) -> None:
        self._windows: dict[tuple[int, int], tuple[list[_Line], list[_Window]]] = {}
        self._prepared: dict[tuple[int, int], tuple[list[_Line], list[_Prepared]]] = {}
        self._masked: dict[tuple[str, int], tuple[list[_Line], list[_Line]]] = {}

    def windows(self, lines: list[_Line], size: int) -> list[_Window]:
        key = (id(lines), size)
        if key not in self._windows:
            self._windows[key] = (lines, _windows(lines, size))
        return self._windows[key][1]

    def prepared(self, lines: list[_Line], size: int) -> list[_Prepared]:
        key = (id(lines), size)
        if key not in self._prepared:
            self._prepared[key] = (lines, [_prepare(w) for w in self.windows(lines, size)])
        return self._prepared[key][1]

    def masked(self, group: str, lines: list[_Line], taken: _Taken) -> list[_Line]:
        """``_mask`` of the lines with the group's taken characters (fixed after the first pass)."""
        key = (group, id(lines))
        if key not in self._masked:
            self._masked[key] = (lines, _mask(lines, taken.get(group, {})))
        return self._masked[key][1]


def _closest(nodes: list[_Node], prepared: list[_Prepared]) -> _Near | None:
    """The best alignment over windows whose tokens hold enough of the statement's words."""
    nodes = _depths(nodes)
    every = Counter(_PLURAL.sub("", node.text).lower() for node in nodes if node.kind == "token")
    required = Counter(
        _PLURAL.sub("", node.text).lower()
        for node in nodes
        if node.kind == "token" and not node.depth
    )
    best: _Near | None = None
    for view in prepared:
        window, tokens, held = view.window, view.tokens, view.held
        # The best score this window could reach: every statement token it holds matched, and
        # every required token it lacks costing one. Below the threshold, it is not aligned.
        # Counted generously (either case, with or without a final "s"), so a window is
        # never passed over that could reach the threshold.
        present = sum(min(n, held[text]) for text, n in every.items())
        lacking = sum(max(0, n - held[text]) for text, n in required.items())
        if not present or present / (present + lacking) < SIMILARITY:
            continue
        alignment = _align(nodes, tokens)
        if alignment is None:
            continue
        near = _Near(alignment, window, tokens, nodes)
        if best is None or (alignment.score, -alignment.cost) > (
            best.alignment.score,
            -best.alignment.cost,
        ):
            best = near
    return best


def _differences(near: _Near) -> list[dict[str, str]]:
    """The alignment's substitutions, deletions and insertions, grouped into runs.

    The template's tokens are given as the template spaces them, the label's as its text reads.
    """
    out: list[dict[str, str]] = []
    run: list[tuple[str, int, int]] = []

    def flush() -> None:
        if not run:
            return
        template = ""
        for operation, node, _ in run:
            if operation in ("replace", "delete"):
                piece = near.nodes[node]
                template += (" " if piece.space and template else "") + piece.text
        label_tokens = [near.tokens[t] for op, _, t in run if op in ("replace", "insert")]
        label = _label_text(near, label_tokens)
        change = "replace" if template and label else ("delete" if template else "insert")
        out.append({"change": change, "template": template, "label": label})
        run.clear()

    steps = list(near.alignment.steps)
    # The stretch ends where the alignment does. When the statement's last tokens are missing
    # there, the label's words up to the end of its sentence stand in their place.
    covered = [index for _, _, index in steps if index >= 0]
    if steps and steps[-1][0] == "delete" and covered:
        last = max(covered)
        line = near.tokens[last].line
        limit = min(len(near.tokens), last + 1 + len(near.nodes))
        extension: list[tuple[str, int, int]] = []
        for at in range(last + 1, limit):
            token = near.tokens[at]
            if token.line != line or not token.fillable:
                break
            extension.append(("insert", -1, at))
            if token.text in TERMINAL:
                # Only words that finish the sentence stand in for the missing end.
                steps += extension
                break
    for step in steps:
        if step[0] in ("replace", "delete", "insert"):
            run.append(step)
        else:
            flush()
    flush()
    return out


def _label_text(near: _Near, tokens: list[_Token]) -> str:
    if not tokens:
        return ""
    if any(token.text == HIDDEN_WORD for token in tokens):
        return " ".join(token.text for token in tokens)
    first, last = tokens[0], tokens[-1]
    lines = near.window.lines
    if first.line == last.line:
        return lines[first.line].text[first.start : last.end]
    parts = [lines[first.line].text[first.start :]]
    parts += [lines[n].text for n in range(first.line + 1, last.line)]
    parts.append(lines[last.line].text[: last.end])
    return "\n".join(parts)


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


def _literal(pieces: list[_Piece]) -> int:
    """How many characters of literal text a statement requires."""
    return sum(len(run) for run in _required(pieces))


def _alternatives(
    pieces: list[_Piece], start: int = 0, end: int | None = None, prefix: str = ""
) -> list[tuple[str, list[_Piece]]]:
    """The forms of a statement made of alternatives, each named by where it stands.

    Each optional segment from ``start`` to ``end`` is taken as required in turn, its content in
    its place. Its siblings there that are long enough to be alternatives of their own (Appendix
    I's sentences, which the template separates with "[or]") are left out of the form; shorter
    ones stay optional ("Do not <refrigerate> <or> <freeze>."). A form with too little literal
    text takes each optional segment of the content in turn ("{Invented name}<is contraindicated
    during breast-feeding ...> [or] <should not be used during breast-feeding>."), and one with
    none left is dropped. Names count the segments from 1, one within another after a dot ("4.2").
    """
    stop = len(pieces) if end is None else end

    def alternative(piece: _Piece) -> bool:
        return piece.kind == "optional" and _literal(list(piece.pieces)) >= MIN_LITERAL

    out: list[tuple[str, list[_Piece]]] = []
    number = 0
    for position in range(start, stop):
        piece = pieces[position]
        if piece.kind != "optional":
            continue
        number += 1
        name = f"{prefix}{number}"
        inner = list(piece.pieces)
        # The segment's joint is its first piece's once the segment is required.
        head = inner[0]
        inner[0] = _Piece(piece.joint, head.kind, head.text, head.pieces)
        before = [p for at, p in enumerate(pieces[:position]) if at < start or not alternative(p)]
        after = [
            p
            for at, p in enumerate(pieces[position + 1 :], position + 1)
            if at >= stop or not alternative(p)
        ]
        form = [*before, *inner, *after]
        # Nothing is required before the first piece, whatever stood before it in the template.
        first = form[0]
        form[0] = _Piece("", first.kind, first.text, first.pieces)
        if _literal(form) >= MIN_LITERAL:
            out.append((name, form))
        else:
            out += _alternatives(form, len(before), len(before) + len(inner), f"{name}.")
    return out


def _forms(pattern: list[Token]) -> list[tuple[str | None, list[_Piece]]]:
    """What a statement is matched as: itself, or its alternatives when it is too short.

    A statement with less than ``MIN_LITERAL`` characters of required literal text is too
    little to tell on its own; when it is made of alternatives (Appendix I's lactation.1, whose
    every sentence is optional; Appendix III's "<Do not <refrigerate> <or> <freeze>.>"), each
    alternative long enough is matched instead. None when neither is possible.
    """
    pieces = _statement(pattern)
    if _literal(pieces) >= MIN_LITERAL:
        return [(None, pieces)]
    return [(name, form) for name, form in _alternatives(pieces)]


def _exact(report: _Report, job: _Job, taken: _Taken, views: _Views) -> bool:
    """Record an exact match or a settled status; False leaves the item for the second pass."""
    item = job.item
    pattern = item.get("pattern")
    if pattern is None:
        # Appendix I's entries whose brackets the EMA does not balance have no pattern.
        report.statements.append(
            {"id": job.identifier, "status": "not-checkable", "reason": "unbalanced-brackets"}
        )
        return True
    tokens = _content(pattern)
    if item["kind"] == "subheading":
        wanted = collapse("".join(str(t["value"]) for t in tokens if t["kind"] == "text"))
        # "(s)" is the template's choice of singular or plural, as in a statement.
        heading = re.compile(
            re.escape(wanted).replace(r"\(s\)", r"(?:s|\(s\))?").replace(r"\(S\)", r"(?:S|\(S\))?")
        )
        found = next((line for line in job.lines if heading.fullmatch(line.text)), None)
        if found is not None:
            report.statements.append({"id": job.identifier, "status": "used", "in": found.path})
        elif job.refused:
            report.statements.append(
                {"id": job.identifier, "status": "not-checked", "reason": "refused-part"}
            )
        else:
            report.statements.append({"id": job.identifier, "status": "absent"})
            if not item["optional"]:
                report.finding("missing-subheading", id=job.identifier, text=wanted)
        return True
    forms = _forms(pattern)
    if not forms:
        report.statements.append(
            {"id": job.identifier, "status": "not-checkable", "reason": "too-little-text"}
        )
        return True
    matches = [
        (name, match)
        for name, pieces in forms
        if (match := _search(pieces, job.lines, views)) is not None
    ]
    if not matches:
        return False
    group = taken.setdefault(job.group, {})
    for _, match in matches:
        for key, characters in match.characters().items():
            group.setdefault(key, set()).update(characters)
    statement = {"id": job.identifier, "status": "used", **matches[0][1].location()}
    if matches[0][0] is not None:
        # Where it matched is the first alternative's; every alternative that matched is named.
        statement["alternatives"] = [name for name, _ in matches]
    report.statements.append(statement)
    return True


def _candidate(job: _Job, taken: _Taken, views: _Views) -> _Near | None:
    """The closest resemblance of a statement not matched exactly, if close enough.

    For a statement of alternatives, the closest of its alternatives' resemblances.
    """
    lines = views.masked(job.group, job.lines, taken)
    best: _Near | None = None
    for name, pieces in _forms(job.item["pattern"]):
        near = _resemblance(pieces, lines, views)
        if near is not None and (best is None or near.alignment.score > best.alignment.score):
            best = replace(near, alternative=name)
    return best


def _resemblance(pieces: list[_Piece], lines: list[_Line], views: _Views) -> _Near | None:
    nodes = _nodes(pieces)
    near = _closest(nodes, views.prepared(lines, _span(pieces)))
    if near is None or near.alignment.cost:
        # Also over two paragraphs more, for a statement set out over more paragraphs than the
        # template gives it; the closer of the two counts.
        wider = _closest(nodes, views.prepared(lines, _span(pieces) + 2))
        if wider is not None and (near is None or wider.alignment.score > near.alignment.score):
            near = wider
    if near is None:
        return None
    words = sum(
        1
        for operation, node, _ in near.alignment.steps
        if operation == "match" and near.nodes[node].text[:1].isalnum()
    )
    if words < MIN_MATCHED and not _all_required_words(near):
        # Too few words in common to tell a resemblance from a coincidence, unless every word
        # the statement requires is there and only punctuation or optional text differs.
        return None
    return near if near.alignment.score >= SIMILARITY else None


def _all_required_words(near: _Near) -> bool:
    """Every word outside the statement's optional segments is matched."""
    required: set[int] = set()
    depth = 0
    for position, node in enumerate(near.nodes):
        if node.kind == "open":
            depth += 1
        elif node.kind == "close":
            depth -= 1
        elif node.kind == "token" and not depth and node.text[:1].isalnum():
            required.add(position)
    matched = {node for operation, node, _ in near.alignment.steps if operation == "match"}
    return bool(required) and required <= matched


def _claims(near: _Near) -> set[tuple[tuple[int, int], int]]:
    """The characters a resemblance covers, as (line, offset) pairs."""
    out: set[tuple[tuple[int, int], int]] = set()
    for used in near.used():
        token = near.tokens[used]
        key = near.window.lines[token.line].key
        out.update((key, offset) for offset in range(token.start, token.end))
    return out


def _near(report: _Report, job: _Job, near: _Near | None) -> None:
    if near is not None:
        differences = _differences(near) or [
            # The same tokens, with paragraph breaks or spaces placed otherwise.
            {"change": "layout", "template": "", "label": ""}
        ]
        line = near.window.lines[near.tokens[near.used()[0]].line]
        # A resemblance in the readable part is reported even when another part of the section
        # was refused: the differing wording is there to see.
        alternative = {} if near.alternative is None else {"alternative": near.alternative}
        report.finding(
            "deviation",
            id=job.identifier,
            **alternative,
            similarity=round(near.alignment.score, 3),
            **{"in": line.path, "paragraph": line.paragraph},
            differences=differences,
        )
        report.statements.append({"id": job.identifier, "status": "deviation"})
        return
    if job.refused:
        # Part of the section could not be read; the statement may be there.
        report.statements.append(
            {"id": job.identifier, "status": "not-checked", "reason": "refused-part"}
        )
        return
    report.statements.append({"id": job.identifier, "status": "absent"})
    if not job.item["optional"] and job.item["kind"] == "statement":
        report.finding("missing-statement", id=job.identifier)


def check(document: Document, registry: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """The findings and statement statuses for a document, by the rules in the module docstring.

    The result also names the checker version, the template, the registry and mapping versions
    and the document's title and date, and counts the findings and statuses by kind. Every
    statement and subheading of the registry has exactly one status.

    Args:
        document: The ePI as ``zone_a.certified.read_epi`` returns it.
        registry: The parsed QRD registry (``qrd/registry/cap-smpc-en-10.4.json``).
        mapping: The parsed section mapping (``fhir/mappings/cap-smpc-en.json``).
    """
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
    # the appendices. Every item gets exactly one status: one with no text to be checked
    # against is not-checked, with the reason.
    identifiers: list[str] = []
    jobs: list[_Job] = []
    unchecked: list[tuple[str, str]] = []
    texts: dict[str, tuple[list[_Line], list[str]]] = {}
    mapped = {target.key for target in targets.values()}

    def add(identifier: str, item: dict[str, Any], key: str, group: str, why: str | None) -> None:
        identifiers.append(identifier)
        if why is None and key not in found:
            why = "section-absent"
        if why is not None:
            unchecked.append((identifier, why))
            return
        if key not in texts:
            texts[key] = _lines(found[key], own, paths)
        lines, refused = texts[key]
        jobs.append(_Job(identifier, item, lines, refused, group))

    for key, section_entry in sections_by_key.items():
        home, why = key, None
        if key not in mapped:
            parent = key.rsplit(".", 1)[0]
            if section_entry["optional"] and key.count(".") == 2 and parent in mapped:
                # An optional subsection the mapping does not list (2.1 and 2.2, for advanced
                # therapies) is read as part of its section: the template files "For the full
                # list of excipients, see section 6.1." under 2.2, and it applies to every
                # product's section 2.
                home = parent
            else:
                why = "section-not-mapped"
        for number, item in enumerate(section_entry["items"]):
            if item["kind"] in ("statement", "subheading"):
                add(f"{key}#{number}", item, home, home, why)
    # The monitoring statement stands before section 1 (in the root section's own text); the
    # closing statement follows section 10, where an ePI puts it.
    for number, item in enumerate(registry["documentStatements"]):
        place = "smpc" if item["placement"] == "before-section-1" else "smpc.10"
        add(f"document#{number}", item, place, "document", None)
    appendices = registry["appendices"]
    for name, owner in (("I", "smpc.4.6"), ("III", "smpc.6.4")):
        entries = appendices[name]["entries"] if name == "I" else appendices[name]["items"]
        for number, entry in enumerate(entries):
            item = {"kind": "statement", "optional": True, "pattern": entry.get("pattern")}
            identifier = f"appendix-{name}#{entry.get('id', number)}"
            add(identifier, item, owner, f"appendix-{name}", None)
    for group in appendices["II"]["groups"].values():
        for row in group:
            item = {"kind": "statement", "optional": True, "pattern": parse(row["text"])}
            add(f"appendix-II#{row['code']}", item, "smpc.4.8", "appendix-II", None)

    views = _Views()
    taken: _Taken = {}
    pending = [job for job in jobs if not _exact(report, job, taken, views)]
    # A paragraph that resembles several statements of one section or appendix (alternatives
    # such as "waived" and "deferred") is a deviation of the one it resembles most.
    candidates = {job.identifier: _candidate(job, taken, views) for job in pending}
    claimed: dict[str, set[tuple[tuple[int, int], int]]] = {}
    ranked = sorted(
        ((job, near) for job in pending if (near := candidates[job.identifier]) is not None),
        key=lambda pair: -pair[1].alignment.score,
    )
    for job, near in ranked:
        covered = _claims(near)
        if covered & claimed.setdefault(job.group, set()):
            candidates[job.identifier] = None
        else:
            claimed[job.group] |= covered
    for job in pending:
        _near(report, job, candidates[job.identifier])
    for identifier, why in unchecked:
        report.statements.append({"id": identifier, "status": "not-checked", "reason": why})
    rank = {identifier: position for position, identifier in enumerate(identifiers)}
    report.statements.sort(key=lambda statement: rank[statement["id"]])

    return {
        "checker": CHECKER_VERSION,
        "template": registry["template"],
        "registryVersion": registry["registryVersion"],
        "mappingVersion": mapping["mappingVersion"],
        "document": {"title": document.title, "date": document.date},
        "summary": _summary(report),
        "findings": report.findings,
        "statements": report.statements,
    }


_PICTURE = frozenset("\ufffc")
# The colour a browser draws a link in (an ``a`` with an ``href``, which the reader also marks
# underlined), unless the link's own style gives one.
_LINK_COLOUR = "color-#0000ee"


def _link_colour(paragraph: Paragraph, mark: Mark) -> bool:
    """Whether ``mark`` is the link colour, over text that one underline covers whole."""
    return mark.kind == _LINK_COLOUR and any(
        other.kind == "underline" and other.start <= mark.start and mark.end <= other.end
        for other in paragraph.marks
    )


def _formatting(report: _Report, section: str, number: int, paragraph: Paragraph) -> None:
    for mark in paragraph.marks:
        covered = paragraph.text[mark.start : mark.end]
        if mark.kind == "underline":
            # A sign alone under a line is the case that matters ("≥" typed as an underlined
            # ">"). A picture under a line is left out: what it shows is never read here, and
            # an underlined picture of a sign is one of the pictures ADR 0005 refuses on import.
            if underline_changes(
                paragraph.text, mark.start, mark.end, also=_PICTURE, hyphens_in_words=True
            ):
                report.finding(
                    "formatting",
                    section=section,
                    paragraph=number,
                    mark=mark.kind,
                    text=_excerpt(covered),
                )
            continue
        if mark.kind in ("border", "faint", "strike") or mark.kind.startswith("shading-"):
            # A bar beside or over text can join or change any of it ("|05 mg", a bar over
            # "<"), and faint or struck text hides or withdraws any of it ("Store at" a white
            # "-" "20 °C"), and so does dark or same-colour shading: a sign as well as a word.
            if any(not c.isspace() and c not in _PICTURE for c in covered):
                report.finding(
                    "formatting",
                    section=section,
                    paragraph=number,
                    mark=mark.kind,
                    text=_excerpt(covered),
                )
            continue
        if not any(c.isalnum() for c in covered):
            # A coloured picture or shaded space shows no text differently.
            continue
        if mark.kind.startswith("color-") and not _link_colour(paragraph, mark):
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


def report(file: str, data: bytes, registry: bytes, mapping: bytes) -> str:
    """The committed check result for one pinned source file, as JSON text.

    Besides ``check``'s result it names the source file and the SHA-256 of the exact bytes of
    the three inputs, and the reader's version and its format's: the registry's version does not
    change with every byte of it (its ``readerVersion`` has), and the mapping's version names the
    heading findings' source only as far as it is bumped.

    Args:
        file: The pinned source file's name.
        data: Its bytes.
        registry: The bytes of ``qrd/registry/cap-smpc-en-10.4.json``.
        mapping: The bytes of ``fhir/mappings/cap-smpc-en.json``.
    """
    result = {
        "source": {"file": file, "sha256": hashlib.sha256(data).hexdigest()},
        "reader": READER_VERSION,
        "format": FORMAT_VERSION,
        "inputs": {
            "registrySha256": hashlib.sha256(registry).hexdigest(),
            "mappingSha256": hashlib.sha256(mapping).hexdigest(),
        },
        **check(read_epi(data), json.loads(registry), json.loads(mapping)),
    }
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
