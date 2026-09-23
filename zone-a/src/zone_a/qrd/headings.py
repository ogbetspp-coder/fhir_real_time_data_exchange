"""Recognise a QRD section heading in a line of label text, using the registry.

A heading is recognised when the line, after runs of space, tab and no-break space are collapsed
to one space and the ends are trimmed, equals one of the forms the registry allows for a
section: the number as the template writes it ("4." for a first-level section, "4.1" for a
second-level one), one space, and the title with each optional segment either present or
absent. Case, punctuation and every other character must match exactly. Anything else is not
a heading as far as this function is concerned; it never guesses.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import Any

_SPACE = re.compile(r"[ \t\u00a0]+")


class HeadingPatternError(ValueError):
    """A heading title holds a fill-in or guidance, which a heading form cannot expand."""


@dataclass(frozen=True)
class HeadingMatch:
    key: str
    # One flag per optional segment of the title, in order: True where the segment is present.
    segments: tuple[bool, ...]


def collapse(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _optional_count(tokens: list[dict[str, Any]]) -> int:
    count = 0
    for token in tokens:
        if token["kind"] == "optional":
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


def forms(section: dict[str, Any]) -> dict[str, tuple[bool, ...]]:
    """Every accepted heading line for a registry section, collapsed, with its segment flags."""
    title = section["title"]
    number = section["number"] + ("." if section["dotted"] else "")
    result: dict[str, tuple[bool, ...]] = {}
    for choice in itertools.product((True, False), repeat=_optional_count(title)):
        result[collapse(f"{number} {_render(title, choice)}")] = choice
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
    return table.get(collapse(text))
