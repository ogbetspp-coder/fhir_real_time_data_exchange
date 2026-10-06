"""Recognise a QRD section heading in a line of label text, using the registry.

A heading is recognised when the line, after runs of space, tab and no-break space are collapsed to
one space and the ends are trimmed, equals one of the forms the registry allows for a section: the
number as the template writes it ("4." for a first-level section, "4.1" for a second-level one), one
space, and the title with each optional segment either present or absent, and each "(S)" after a
word in capitals kept, dropped or written "S" (the template's singular or plural). Case, punctuation
and every other character must match exactly. Anything else is not a heading as far as this function
is concerned; it never guesses.

The registry is the SmPC's (Annex I). The labelling and the package leaflet reuse some of the
same lines ("1. NAME OF THE MEDICINAL PRODUCT" is section 1 of the labelling too), so a caller
must apply this only to text it already knows is Annex I.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import Any

_SPACE = re.compile(r"[ \t\u00a0]+")


class HeadingPatternError(ValueError):
    """A heading title a heading form cannot expand.

    A fill-in or guidance anywhere in it, or an optional segment inside another.
    """


@dataclass(frozen=True)
class HeadingMatch:
    """A recognised heading: the registry section's key and its optional segments' flags."""

    key: str
    # One flag per optional segment of the title, in order: True where the segment is present.
    segments: tuple[bool, ...]


def collapse(text: str) -> str:
    """The text with each run of space, tab and no-break space as one space, ends trimmed."""
    return _SPACE.sub(" ", text).strip()


def _optional_count(tokens: list[dict[str, Any]], nested: bool = False) -> int:
    """How many optional segments the title has; refuses a token a form cannot expand.

    Every level is checked: a fill-in inside an optional segment would otherwise be written as
    its placeholder's name, and an optional segment inside another has no flag of its own.
    """
    count = 0
    for token in tokens:
        if token["kind"] == "optional":
            if nested:
                raise HeadingPatternError("a heading title nests an optional segment")
            _optional_count(token["value"], nested=True)
            count += 1
        elif token["kind"] != "text":
            raise HeadingPatternError(f"a heading title holds a {token['kind']} token")
    return count


def _render(tokens: list[dict[str, Any]], choice: tuple[bool, ...]) -> str:
    out: list[str] = []
    index = 0
    for token in tokens:
        if token["kind"] == "optional":
            if choice[index]:
                out.append(_render(token["value"], ()))
            index += 1
        else:
            out.append(str(token["value"]))
    return "".join(out)


# "(S)" after a word in capitals: the template leaves singular or plural to the author, so
# "NUMBER(S)", "NUMBER" and "NUMBERS" are each its wording (owner decision 2026-10-06, ADR 0006).
_PLURAL = re.compile(r"(?<=[A-Z])\(S\)")


def _plurals(line: str) -> list[str]:
    """The line with each "(S)" kept, dropped, or written "S"; the template's own first."""
    found = _PLURAL.search(line)
    if found is None:
        return [line]
    head, tail = line[: found.start()], line[found.end() :]
    return [head + mark + rest for mark in ("(S)", "", "S") for rest in _plurals(tail)]


def forms(section: dict[str, Any]) -> dict[str, tuple[bool, ...]]:
    """Every accepted heading line for a registry section, collapsed, with its segment flags.

    The first is the template's own wording, every optional segment present.
    """
    title = section["title"]
    number = section["number"] + ("." if section["dotted"] else "")
    result: dict[str, tuple[bool, ...]] = {}
    for choice in itertools.product((True, False), repeat=_optional_count(title)):
        for line in _plurals(collapse(f"{number} {_render(title, choice)}")):
            result.setdefault(line, choice)
    return result


def index(registry: dict[str, Any]) -> dict[str, HeadingMatch]:
    """Every accepted heading line in the registry. Two sections sharing a form is an error."""
    table: dict[str, HeadingMatch] = {}
    for section in registry["sections"]:
        for line, choice in forms(section).items():
            if line in table:
                raise HeadingPatternError(f"{line!r} is a heading of two sections")
            table[line] = HeadingMatch(key=section["key"], segments=choice)
    return table


def match_heading(text: str, table: dict[str, HeadingMatch]) -> HeadingMatch | None:
    """The heading the line is, once collapsed, in a table from ``index``; else None."""
    return table.get(collapse(text))
