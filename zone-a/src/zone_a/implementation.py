"""What changed between two versions of a core data sheet, and where each label stands on it.

A CCDS change is carried into a local label when the label's text holds the new wording. This
module answers that from the labels' own text, read by the label reader (``zone_a.certified``),
never from what a tracker records, and it answers only what the text shows exactly: a label that
words the change its own way is for a person to judge, never matched by resemblance.

Changes. ``changes`` compares two reads of the CCDS paragraph by paragraph, then word by word.
Paragraphs with no text but white space are layout and are left out. The others are paired by
an exact comparison of their text (difflib's matching blocks, with its "junk" heuristic off, so
the pairing depends on the text alone); in a run of replaced paragraphs the n-th old one is
paired with the n-th new one, and the rest are inserted or deleted whole. A pair's text is split
into tokens (a run of word characters, a run of white space, or any other single character, so
"≤" and "≥" are tokens of their own) and compared the same way. The result is a run of segments,
each unchanged, inserted or deleted: the unchanged and deleted parts give the old text and the
unchanged and inserted parts the new one, character for character, or the comparison is an
error, never a guess. Two paragraphs with the same text and different marks (bold, superscript
and the rest) are a ``formatting`` change: the text check below does not judge it, and it is
reported so that nobody misses it. A change names the heading it falls under: the nearest
paragraph before it whose style is a heading style (its id starts with "Heading"), with its list
label.

Edits. Each run of changed tokens in a paragraph becomes an edit, with ``CONTEXT`` words of the
unchanged text on each side (fewer at the paragraph's edges); runs fewer than twice that many
words apart are one edit. An edit's ``old`` and ``new`` wording is that stretch as the old and
the new CCDS have it. A paragraph inserted or deleted whole is an edit whose other side is
``None``. The wording must occur exactly once in the CCDS it comes from: when it occurs more
often, the context grows a word at a time on both sides until it is unique, and an edit still
not unique at the paragraph's edges cannot be checked (``ambiguous-wording``): finding it in a
label would not say which sentence carries it.

Labels. ``check`` looks for an edit's wording in a label's paragraphs, exactly and within one
paragraph, and gives the label one status for the edit:

- ``implemented``: the new wording is there and the old is not;
- ``pending``: the old wording is there and the new is not;
- ``both``: both are there, for a person to look at;
- ``absent``: neither is there. The label may word the change its own way (a deviation,
  justified or not), or the change may not apply to it: for a person to decide. A deleted
  paragraph is ``pending`` while its text is there and ``absent`` when it is not, never
  ``implemented``: the text alone cannot tell a removal from a label that never had it;
- ``not-checked``, with the reason: the edit cannot be checked (``ambiguous-wording``), the label
  is in another language than the CCDS and no wording in its language is given (``language``),
  the reader refused the label (``refused``, with the reader's code), or it refused a part of it
  (``refused-part``), where either wording may stand.

Translations. A label in another language carries a translation of the change, which the CCDS
does not hold, so its wording is given: ``load_wordings`` reads a file of each edit's old and new
wording by language (``template`` writes one to fill in, with each edit's CCDS wording beside it
for reference). Such a label is checked against its language's wording exactly as above, and its
result says which wording it was checked against (``ccds`` or ``translation``). The file is
refused whole, never in part, when it names an edit these CCDS versions do not have, carries a
reference wording other than the CCDS's (it was made for other versions), gives a side the CCDS
change does not have or leaves out one it has, gives an empty wording or the same old and new
wording, or gives wording in the CCDS's own language. A language whose old and new wording are
both left empty is not given.

Deadlines. A label may carry its market and the date it must carry the change by (``due``). On
the day the report is made as of (``as_of``, given, never read from a clock, so the same inputs
give the same report), a label past its date is late by the days since, unless its status is
``implemented``: ``pending``, ``both`` and ``absent`` all lack the evidence that the change is
there. A ``not-checked`` label's lateness is unknown (``None``), and so is a label with no date.

A place where one wording lies inside the other (the old "reported." in the new "Rarely reported.")
is the longer one's. Each place a wording is found is the paragraph's index and the character offset
in its text, as the reader returned them. Where neither wording is found but one would be if every
white-space character were a plain space, the status stays ``absent`` and ``spacing`` is set: the
label's spaces differ (a no-break space for a space, say), for a person to judge. Nothing is
normalised for a status.
"""

from __future__ import annotations

import datetime
import difflib
import hashlib
import itertools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from label_docx import epi, epi_output, output, reader
from label_docx.epi import EpiRefusedError, walk
from label_docx.reader import DocxRefusedError, Paragraph

from zone_a import certified

IMPLEMENTATION_VERSION = "implementation-check/1.1.5"
# Words of unchanged text kept on each side of an edit.
CONTEXT = 4

_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")
_WORD = re.compile(r"\w")
_HEADING = re.compile(r"Heading", re.IGNORECASE)
_WHITE = re.compile(r"\s")


@dataclass(frozen=True)
class Segment:
    """A stretch of a paragraph's text: ``t`` unchanged, ``i`` inserted, ``d`` deleted."""

    kind: str
    text: str


@dataclass(frozen=True)
class Change:
    """One paragraph's change: ``text``, ``inserted``, ``deleted`` or ``formatting``."""

    kind: str
    old_index: int | None
    new_index: int | None
    heading: str | None
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class Edit:
    """The wording one change is checked by; ``reason`` is set when it cannot be checked."""

    id: str
    change: int
    old: str | None
    new: str | None
    reason: str | None = None


@dataclass(frozen=True)
class Label:
    """A label as the reader read it, or the code it refused it with."""

    name: str
    language: str
    paragraphs: tuple[Paragraph, ...] = ()
    refusal: str | None = None
    refused_part: bool = False
    market: str | None = None
    due: datetime.date | None = None


@dataclass(frozen=True)
class Result:
    """Where one label stands on one edit (the module docstring lists the statuses)."""

    status: str
    reason: str | None
    new: tuple[tuple[int, int], ...]
    old: tuple[tuple[int, int], ...]
    spacing: bool
    # The wording the label was checked against: "ccds" or "translation" (None: not checked).
    wording: str | None = None


# Each edit's old and new wording in a label's language, by edit id and language.
type Wordings = dict[str, dict[str, tuple[str | None, str | None]]]


def tokens(text: str) -> list[str]:
    """The text's tokens, which join back to it exactly."""
    found = _TOKEN.findall(text)
    if "".join(found) != text:  # every character is one of the three kinds; never reached
        raise ValueError("the tokens do not give back the text")
    return found


def _is_word(token: str) -> bool:
    return bool(_WORD.match(token))


type _Op = tuple[str, list[str], list[str]]


def _ops(old: str, new: str) -> list[_Op]:
    """The token comparison of two texts: equal, deleted and inserted runs, in order."""
    a, b = tokens(old), tokens(new)
    out: list[_Op] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        out.append(("t" if tag == "equal" else "x", a[i1:i2], b[j1:j2]))
    return out


def segments(old: str, new: str) -> tuple[Segment, ...]:
    """The old text against the new as unchanged, deleted and inserted segments."""
    out: list[Segment] = []
    for kind, a, b in _ops(old, new):
        pieces = [("t", "".join(a))] if kind == "t" else [("d", "".join(a)), ("i", "".join(b))]
        for k, text in pieces:
            if not text:
                continue
            if out and out[-1].kind == k:
                out[-1] = Segment(k, out[-1].text + text)
            else:
                out.append(Segment(k, text))
    if "".join(s.text for s in out if s.kind != "i") != old:
        raise ValueError("the segments do not give back the old text")
    if "".join(s.text for s in out if s.kind != "d") != new:
        raise ValueError("the segments do not give back the new text")
    return tuple(out)


def _texted(paragraphs: Sequence[Paragraph]) -> list[int]:
    return [i for i, p in enumerate(paragraphs) if p.text.strip()]


def _heading(paragraphs: Sequence[Paragraph], index: int) -> str | None:
    for i in range(index - 1, -1, -1):
        p = paragraphs[i]
        if p.style is not None and _HEADING.match(p.style):
            label = p.numbering.text if p.numbering is not None and p.numbering.text else ""
            return f"{label} {p.text}".strip()
    return None


def changes(old: Sequence[Paragraph], new: Sequence[Paragraph]) -> list[Change]:
    """Every paragraph that differs between two reads of the CCDS (the module docstring)."""
    a, b = _texted(old), _texted(new)
    matcher = difflib.SequenceMatcher(
        None, [old[i].text for i in a], [new[j].text for j in b], autojunk=False
    )
    out: list[Change] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(a[i1:i2], b[j1:j2], strict=True):
                if old[i].marks != new[j].marks:
                    out.append(
                        Change("formatting", i, j, _heading(new, j), (Segment("t", new[j].text),))
                    )
            continue
        for i, j in itertools.zip_longest(a[i1:i2], b[j1:j2]):
            if i is not None and j is not None:
                out.append(
                    Change("text", i, j, _heading(new, j), segments(old[i].text, new[j].text))
                )
            elif j is not None:
                out.append(
                    Change("inserted", None, j, _heading(new, j), (Segment("i", new[j].text),))
                )
            elif i is not None:
                out.append(
                    Change("deleted", i, None, _heading(old, i), (Segment("d", old[i].text),))
                )
    return out


def _occurrences(text: str, wording: str) -> list[int]:
    """Every offset the wording starts at in the text, overlapping ones included."""
    out: list[int] = []
    at = text.find(wording)
    while at != -1:
        out.append(at)
        at = text.find(wording, at + 1)
    return out


def _count(paragraphs: Sequence[Paragraph], wording: str) -> int:
    return sum(len(_occurrences(p.text, wording)) for p in paragraphs)


def _clusters(ops: list[_Op]) -> list[tuple[int, int]]:
    """Runs of changed ops, as (first, last) op indexes, joined when close (``CONTEXT``)."""
    changed = [k for k, (kind, _, _) in enumerate(ops) if kind != "t"]
    out: list[tuple[int, int]] = []
    for k in changed:
        if out:
            between = sum(_is_word(t) for _, a, _ in ops[out[-1][1] + 1 : k] for t in a)
            if between < 2 * CONTEXT:
                out[-1] = (out[-1][0], k)
                continue
        out.append((k, k))
    return out


def _wording(ops: list[_Op], first: int, last: int, words: int) -> tuple[str, str, bool]:
    """A cluster's old and new wording with ``words`` words of context; True at both edges."""
    before = [t for _, a, _ in ops[:first] for t in a]
    after = [t for _, a, _ in ops[last + 1 :] for t in a]

    def take(side: list[str]) -> tuple[list[str], bool]:
        # Up to ``words`` words from the change outward; the white space after the last of them,
        # where the context stops short of an edge, is not part of it.
        out: list[str] = []
        seen = 0
        for token in side:
            if seen == words and _is_word(token):
                while out and out[-1].isspace():
                    out.pop()
                return out, False
            out.append(token)
            seen += _is_word(token)
        return out, True

    left, left_edge = take(list(reversed(before)))
    right, right_edge = take(after)
    left.reverse()
    middle_old = [t for _, a, _ in ops[first : last + 1] for t in a]
    middle_new = [t for _, _, b in ops[first : last + 1] for t in b]
    old = "".join(left + middle_old + right)
    new = "".join(left + middle_new + right)
    return old, new, left_edge and right_edge


def _edit_id(old: str | None, new: str | None) -> str:
    return hashlib.sha256(f"{old}\0{new}".encode()).hexdigest()[:12]


def edits(old: Sequence[Paragraph], new: Sequence[Paragraph], found: list[Change]) -> list[Edit]:
    """The wording each text change is checked by (the module docstring, "Edits")."""
    out: list[Edit] = []
    for index, change in enumerate(found):
        if change.kind == "formatting":
            continue
        text = change.segments[0].text
        if change.kind == "inserted":
            reason = None if _count(new, text) == 1 else "ambiguous-wording"
            out.append(Edit(_edit_id(None, text), index, None, text, reason))
            continue
        if change.kind == "deleted":
            reason = None if _count(old, text) == 1 else "ambiguous-wording"
            out.append(Edit(_edit_id(text, None), index, text, None, reason))
            continue
        if change.old_index is None or change.new_index is None:  # a text change has both
            raise ValueError("a text change without both paragraphs")
        ops = _ops(old[change.old_index].text, new[change.new_index].text)
        for first, last in _clusters(ops):
            words = CONTEXT
            while True:
                was, now, edges = _wording(ops, first, last, words)
                unique = _count(old, was) == 1 and _count(new, now) == 1
                if unique or edges:
                    break
                words += 1
            out.append(
                Edit(_edit_id(was, now), index, was, now, None if unique else "ambiguous-wording")
            )
    return out


def _places(paragraphs: Sequence[Paragraph], wording: str | None) -> tuple[tuple[int, int], ...]:
    if wording is None:
        return ()
    return tuple(
        (index, at) for index, p in enumerate(paragraphs) for at in _occurrences(p.text, wording)
    )


def _outside(
    places: tuple[tuple[int, int], ...], length: int, others: tuple[tuple[int, int], ...], span: int
) -> tuple[tuple[int, int], ...]:
    """The places not inside a place of the other wording (one wording may hold the other)."""
    return tuple(
        (p, at)
        for p, at in places
        if not any(q == p and o <= at and at + length <= o + span for q, o in others)
    )


def _spaced(paragraphs: Sequence[Paragraph], wordings: Iterable[str | None]) -> bool:
    texts = [_WHITE.sub(" ", p.text) for p in paragraphs]
    return any(_WHITE.sub(" ", w) in text for w in wordings if w is not None for text in texts)


def check(edit: Edit, label: Label, language: str, wordings: Wordings | None = None) -> Result:
    """Where a label stands on an edit (the module docstring, "Labels" and "Translations")."""
    if edit.reason is not None:
        return Result("not-checked", edit.reason, (), (), False)
    if label.refusal is not None:
        return Result("not-checked", "refused", (), (), False)
    if label.language == language:
        was, now, wording = edit.old, edit.new, "ccds"
    else:
        given = (wordings or {}).get(edit.id, {}).get(label.language)
        if given is None:
            return Result("not-checked", "language", (), (), False)
        (was, now), wording = given, "translation"
    new = _places(label.paragraphs, now)
    old = _places(label.paragraphs, was)
    if was is not None and now is not None:
        # "reported." in "Rarely reported." is the new wording, not the old.
        new, old = _outside(new, len(now), old, len(was)), _outside(old, len(was), new, len(now))
    if label.refused_part:
        return Result("not-checked", "refused-part", new, old, False)
    if now is None:
        status = "pending" if old else "absent"
    elif was is None:
        status = "implemented" if new else "absent"
    else:
        status = {(True, False): "implemented", (False, True): "pending", (True, True): "both"}.get(
            (bool(new), bool(old)), "absent"
        )
    spacing = status == "absent" and _spaced(label.paragraphs, (was, now))
    return Result(status, None, new, old, spacing, wording)


def days_late(result: Result, label: Label, as_of: datetime.date | None) -> int | None:
    """How many days past its date a label is on an edit (the module docstring, "Deadlines")."""
    if label.due is None or as_of is None or result.status == "not-checked":
        return None
    if result.status == "implemented":
        return 0
    return max(0, (as_of - label.due).days)


def template(checked: Sequence[Edit], language: str, languages: Sequence[str]) -> dict[str, Any]:
    """A wording file to fill in: each checkable edit's CCDS wording, an empty pair a language."""
    return {
        e.id: {
            "ccds": {"language": language, "old": e.old, "new": e.new},
            **{code: {"old": None, "new": None} for code in languages},
        }
        for e in checked
        if e.reason is None
    }


def load_wordings(value: object, checked: Sequence[Edit], language: str) -> Wordings:
    """A wording file's wordings, or ValueError naming what is wrong with it (whole or nothing)."""
    if not isinstance(value, dict):
        raise ValueError("a wording file is a JSON object of edits")
    by_id = {e.id: e for e in checked}
    out: Wordings = {}
    for edit_id, entry in value.items():
        edit = by_id.get(edit_id)
        if edit is None:
            raise ValueError(f"no edit {edit_id} in these CCDS versions")
        if not isinstance(entry, dict):
            raise ValueError(f"edit {edit_id}: not an object of languages")
        reference = entry.get("ccds")
        if reference is not None and (
            not isinstance(reference, dict)
            or reference.get("old") != edit.old
            or reference.get("new") != edit.new
        ):
            raise ValueError(f"edit {edit_id}: the file was made for other CCDS wording")
        for code, pair in entry.items():
            if code == "ccds":
                continue
            where = f"edit {edit_id}, {code}"
            if code == language:
                raise ValueError(f"{where}: the CCDS's own language takes the CCDS's wording")
            if not isinstance(pair, dict) or set(pair) != {"old", "new"}:
                raise ValueError(f"{where}: not an object of old and new")
            was, now = pair["old"], pair["new"]
            if was is None and now is None:
                continue  # not filled in: that language is not given
            for side, text, ccds in (("old", was, edit.old), ("new", now, edit.new)):
                if (text is None) != (ccds is None):
                    expected = "null, as in the CCDS" if ccds is None else "given, as in the CCDS"
                    raise ValueError(f"{where}: {side} must be {expected}")
                if text is not None and (not isinstance(text, str) or not text.strip()):
                    raise ValueError(f"{where}: {side} is empty")
            if was == now:
                raise ValueError(f"{where}: old and new are the same")
            out.setdefault(edit_id, {})[code] = (was, now)
    return out


def read_label(
    name: str,
    language: str,
    data: bytes,
    market: str | None = None,
    due: datetime.date | None = None,
) -> Label:
    """A label read through the certified reads: an ePI Bundle (JSON) or a .docx."""
    if data[:1] == b"{":
        try:
            document = certified.read_epi(data)
        except EpiRefusedError as refused:
            return Label(name, language, refusal=refused.code, market=market, due=due)
        sections = list(walk(document.sections))
        paragraphs = tuple(p for s in sections for p in s.paragraphs)
        refused_part = any(s.refusal for s in sections)
        return Label(name, language, paragraphs, refused_part=refused_part, market=market, due=due)
    try:
        return Label(name, language, tuple(certified.read_docx(data)), market=market, due=due)
    except DocxRefusedError as refused:
        return Label(name, language, refusal=refused.code, market=market, due=due)


def report(
    old: Sequence[Paragraph],
    new: Sequence[Paragraph],
    language: str,
    labels: Sequence[Label],
    sources: dict[str, str],
    wordings: object = None,
    as_of: datetime.date | None = None,
) -> dict[str, Any]:
    """The changes, their edits and every label's status on each, as JSON values.

    It names the implementation check's version and the readers' and their formats' (a status is
    only as good as the reading under it); ``sources`` names the two CCDS files' SHA-256
    (``old``, ``new``); a label that the reader refused carries its code. ``wordings`` is a
    wording file's JSON value (``load_wordings``); ``as_of`` is the day lateness is counted on,
    required when a label has a date. ``summary`` counts, for each edit, the labels by status,
    those late, and the markets they are in.
    """
    found = changes(old, new)
    checked = edits(old, new, found)
    given = load_wordings(wordings, checked, language) if wordings is not None else {}
    if as_of is None and any(label.due is not None for label in labels):
        raise ValueError("a label has a date: give the day the report is made as of")
    # By the label's position, then the edit's: two labels may share a name.
    results = [[check(e, label, language, given) for e in checked] for label in labels]
    late = [
        [days_late(r, label, as_of) for r in row]
        for label, row in zip(labels, results, strict=True)
    ]

    def place(found_at: tuple[tuple[int, int], ...]) -> list[dict[str, int]]:
        return [{"paragraph": p, "offset": o} for p, o in found_at]

    return {
        "checker": IMPLEMENTATION_VERSION,
        # The readers the CCDS and the labels were read with, and their formats.
        "readers": {
            "docx": reader.READER_VERSION,
            "docxFormat": output.FORMAT_VERSION,
            "epi": epi.READER_VERSION,
            "epiFormat": epi_output.FORMAT_VERSION,
        },
        "ccds": {"language": language, **sources},
        "changes": [
            {
                "kind": c.kind,
                "oldParagraph": c.old_index,
                "newParagraph": c.new_index,
                "heading": c.heading,
                "segments": [{"kind": s.kind, "text": s.text} for s in c.segments],
            }
            for c in found
        ],
        "edits": [
            {"id": e.id, "change": e.change, "old": e.old, "new": e.new, "reason": e.reason}
            for e in checked
        ],
        "asOf": None if as_of is None else as_of.isoformat(),
        "labels": [
            {
                "name": label.name,
                "language": label.language,
                "market": label.market,
                "due": None if label.due is None else label.due.isoformat(),
                "refusal": label.refusal,
                "results": [
                    {
                        "edit": e.id,
                        "status": r.status,
                        "reason": r.reason,
                        "wording": r.wording,
                        "new": place(r.new),
                        "old": place(r.old),
                        "spacing": r.spacing,
                        "daysLate": days,
                    }
                    for e, r, days in zip(checked, results[i], late[i], strict=True)
                ],
            }
            for i, label in enumerate(labels)
        ],
        "summary": {
            e.id: {
                **{
                    status: sum(1 for row in results if row[k].status == status)
                    for status in ("implemented", "pending", "both", "absent", "not-checked")
                },
                "late": sum(1 for row in late if (row[k] or 0) > 0),
                "lateMarkets": sorted(
                    {
                        label.market
                        for label, row in zip(labels, late, strict=True)
                        if label.market is not None and (row[k] or 0) > 0
                    }
                ),
            }
            for k, e in enumerate(checked)
        },
    }
