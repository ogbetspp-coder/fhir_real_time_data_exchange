"""Who a label is for, from the label itself: ADR 0006 decision 5.

A structured SmPC (``zone_a.structure``) states its product's name in section 1, its marketing
authorisation holder in section 7 and its EU authorisation numbers in section 8. Only the numbers
can be taken by rule; the name and the holder are proposed for a person to confirm, once per
product, by choosing from the label's own text, never by retyping it.

Numbers. An EU authorisation number is ``EU/1/YY/NNN/PPP`` (YY the year, NNN the product, three or
four digits, PPP the presentation), and section 8 may write a run of presentations as
``EU/1/YY/NNN/PPP-PPP``. Each is taken exactly as written, with its product number
``EU/1/YY/NNN`` (the number without its presentation), from any paragraph of section 8, standing
alone: after the start, a space or one of ",;:(", and before the end, a space or one of ",;:.()".
Anything else in section 8 that starts "EU/" is ``unread``, for a person: never read loosely,
never left out unseen.

Products. A label is the product whose confirmed EU product numbers are exactly the label's (as a
set): the capsules' and the tablets' SmPCs of one authorisation are one product. No match is a new
product, for a person; two matches is an error in the records. The name and the holder a person
confirmed are compared with the label's text exactly, and a difference is a finding, never a
reason to match otherwise.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from label_docx.reader import Paragraph

PRODUCT_VERSION: Final = "product/1.0.11"

# A number standing alone: after the start, whitespace or one of ",;:(", and before the end,
# whitespace or one of ",;:.()". Anything else next to it (a dash, a letter, a slash) leaves it
# unread.
EU_NUMBER: Final = re.compile(
    r"(?<![^\s,;:(])(EU/1/[0-9]{2}/[0-9]{3,4})/([0-9]{3})(?:-([0-9]{3}))?(?=$|[\s,;:.()])"
)
_EU: Final = re.compile(r"EU/")


def _texts(paragraphs: Sequence[Paragraph], structured: Mapping[str, Any], key: str) -> list[int]:
    """The paragraphs of a section that hold text, by index (``zone_a.structure``'s own list)."""
    section = next((s for s in structured["sections"] if s["key"] == key), None)
    return (
        [] if section is None else [i for i in section["paragraphs"] if paragraphs[i].text.strip()]
    )


def propose(paragraphs: Sequence[Paragraph], structured: Mapping[str, Any]) -> dict[str, Any]:
    """The label's own statements of its name, holder and numbers (the module docstring)."""
    numbers: list[dict[str, Any]] = []
    unread: list[dict[str, Any]] = []
    for i in _texts(paragraphs, structured, "smpc.8"):
        text = paragraphs[i].text
        taken: set[int] = set()
        for found in EU_NUMBER.finditer(text):
            first, last = found.group(2), found.group(3)
            if last is not None and int(last) <= int(first):
                continue  # not a run of presentations: left to ``unread``
            taken.add(found.start())
            numbers.append(
                {
                    "paragraph": i,
                    "start": found.start(),
                    "end": found.end(),
                    "number": found.group(0),
                    "product": found.group(1),
                }
            )
        unread += [
            {"paragraph": i, "start": m.start()}
            for m in _EU.finditer(text)
            if m.start() not in taken
        ]
    return {
        "proposer": PRODUCT_VERSION,
        "name": _texts(paragraphs, structured, "smpc.1"),
        "holder": _texts(paragraphs, structured, "smpc.7"),
        "numbers": numbers,
        "unread": unread,
        "products": sorted({n["product"] for n in numbers}),
    }


def match(
    proposal: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """The confirmed product whose EU product numbers are exactly the label's, or None.

    ``records`` are confirmed products: each with ``id`` and ``eu`` (``{"products": [...]}``).

    Raises:
        ValueError: The label states no number it reads, or holds one it cannot, or two records
            claim its numbers.
    """
    if proposal["unread"] or not proposal["products"]:
        raise ValueError("the label's EU numbers are not all read: a person must place it")
    found = [r for r in records if sorted(r["eu"]["products"]) == proposal["products"]]
    if len(found) > 1:
        raise ValueError(f"{len(found)} products claim {proposal['products']}")
    return found[0] if found else None
