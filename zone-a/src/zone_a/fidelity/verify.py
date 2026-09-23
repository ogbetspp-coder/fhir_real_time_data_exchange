"""Pure, synchronous narrative fidelity verifier, ported from ``src/fidelity/verify.ts``.

ADR 0003 and ``docs/fidelity-normalization.md`` section 6. Per-section problems become statuses
in the report; only structurally unusable input (wrong normalisation version, duplicate keys,
invalid pages) raises. Nothing here logs, performs I/O, or places narrative text in its outputs:
a report carries statuses, counts, offsets, lengths, reason codes and hashes only.

Offsets are Unicode code points, which Python's ``str`` indexes natively; the TypeScript has to
build a code point array first. Where the TypeScript reads past the end of that array it gets
``undefined``; Python would wrap around to the end of the string for a negative index, so every
such read goes through ``_at()``.

Numbers are the other trap. JSON has one number type and JavaScript has one number type, so
``1`` and ``1.0`` are the same value on the Zone B side and ``Number.isInteger`` accepts both;
``json.loads`` gives Python an ``int`` for the first and a ``float`` for the second, and
``isinstance(x, int)`` accepts only the first. A page written ``"page": 1.0`` — which the
contract's ``{"type": "integer"}`` permits, because JSON Schema defines an integer as a number
with a zero fractional part — therefore verified in Zone B and was refused here. Every offset
read from the payload goes through ``_as_integer()``, which is ``Number.isInteger`` plus the
normalisation to ``int`` that Python's slicing and arithmetic need afterwards.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Final

from zone_a.canonical_json import sha256_json, sha256_utf8

from .normalize import (
    NORMALIZATION_VERSION,
    NormalizationError,
    count_words,
    find_forbidden_character,
    is_whitespace,
    normalize_text,
)
from .xhtml import SOFT_HYPHEN, XhtmlError, xhtml_to_text

# Most text a page may exclude as running header/footer. The body range is declared by the
# extractor, so it is bounded and must sit on line boundaries rather than trusted outright.
MAX_EXCLUDED_CODE_POINTS_PER_PAGE: Final = 240

type Json = Any


class FidelityError(Exception):
    """Structurally unusable input: the verifier cannot produce a report at all."""

    def __init__(self, message: str, issues: list[str]) -> None:
        super().__init__(message)
        self.issues = issues


@dataclass(slots=True)
class PageIndex:
    page: int
    text: str
    body_start: int
    body_end: int
    malformed: bool
    body_issue: str | None


@dataclass(slots=True)
class _Piece:
    index: PageIndex
    start: int
    end: int


def _as_integer(value: Json) -> int | None:
    """``Number.isInteger(value)`` with the value normalised to ``int``, else ``None``.

    ``bool`` is excluded: it is a subclass of ``int`` in Python and a ``boolean`` in JSON, and
    ``Number.isInteger(true)`` is ``false``. A ``float`` is accepted when it is finite and has
    no fractional part, because that is the same JSON number as the corresponding ``int``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _normalized_span(span: Json) -> dict[str, Json]:
    """A span whose page and offsets are ``int`` wherever JSON gave an integral number.

    A span is read as raw JSON, so ``1.0`` arrives as a ``float`` and would slice, subtract and
    serialise differently from the ``1`` the Zone B verifier sees. Anything that is not an
    integer is left alone: the contract forbids it, and inventing a coercion here would hide
    the difference instead of reproducing it.
    """
    normalized: dict[str, Json] = dict(span)
    for key in ("page", "startOffset", "endOffset"):
        if key in normalized:
            integral = _as_integer(normalized[key])
            if integral is not None:
                normalized[key] = integral
    return normalized


def _number_text(value: Json) -> str:
    """A value as a JavaScript template literal writes it into an issue string.

    ``1.0`` is ``1``, not ``1.0``; a boolean is ``true``/``false``, not ``True``/``False`` (tested
    before the integer case, because ``bool`` is a subclass of ``int``); ``None`` is ``null``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    integral = _as_integer(value)
    return str(value) if integral is None else str(integral)


def _at(text: str, offset: int) -> str | None:
    """``text[offset]`` with JavaScript's out-of-range behaviour: None, never a wrapped index."""
    if offset < 0 or offset >= len(text):
        return None
    return text[offset]


def _soft_hyphen_break_before(text: str, offset: int) -> bool:
    """True when the line break ending just before ``offset`` follows U+00AD.

    Normalisation step 1 deletes such a break, so it lies inside a word, not between words.
    """
    if _at(text, offset - 1) != "\n":
        return False
    before = offset - 3 if _at(text, offset - 2) == "\r" else offset - 2
    return _at(text, before) == SOFT_HYPHEN


def _body_issue_for(text: str, body_start: int, body_end: int) -> str | None:
    if body_start != 0 and (
        _at(text, body_start - 1) != "\n" or _soft_hyphen_break_before(text, body_start)
    ):
        return "body-boundary"
    # A non-empty body always ends with its own line terminator, even at the end of the page:
    # otherwise the last word of one page and the first word of the next would read as one.
    if body_end != body_start and _at(text, body_end - 1) != "\n":
        return "body-boundary"
    if len(text) - (body_end - body_start) > MAX_EXCLUDED_CODE_POINTS_PER_PAGE:
        return "excluded-text"
    return None


def index_pages(source: Json) -> tuple[dict[int, PageIndex], list[str]]:
    pages: dict[int, PageIndex] = {}
    structural: list[str] = []
    issues: list[str] = []
    for position, page in enumerate(source["pages"], start=1):
        text = page["text"]
        # `Number.isInteger`, not `isinstance(..., int)`: a JSON `1.0` is the same number as `1`
        # on the Zone B side, and the issue strings are inside `reportHash`, so the number is
        # rendered the way JavaScript renders it rather than the way `str(1.0)` does.
        number = _as_integer(page["page"])
        body_start = _as_integer(page["bodyStart"])
        body_end = _as_integer(page["bodyEnd"])
        if number is not None and number in pages:
            structural.append(f"Duplicate page number {_number_text(page['page'])}")
        # Pages are numbered 1..N in array order, so no page can be left out of the document and
        # the text before a section's first span is always the text the document puts there.
        if number != position:
            structural.append(f"Page {_number_text(page['page'])} at position {position}")
        if (
            number is None
            or number < 1
            or body_start is None
            or body_end is None
            or body_start < 0
            or body_end < body_start
            or body_end > len(text)
        ):
            structural.append(f"Invalid body range on page {_number_text(page['page'])}")
            continue
        body_issue = _body_issue_for(text, body_start, body_end)
        if body_issue is not None:
            issues.append(f"Page {number}: {body_issue}")
        pages[number] = PageIndex(
            page=number,
            text=text,
            body_start=body_start,
            body_end=body_end,
            malformed=find_forbidden_character(text) is not None,
            body_issue=body_issue,
        )
    if structural:
        raise FidelityError("Source document text is invalid", structural)
    return pages, issues


def _from_line_start(index: PageIndex, start: int) -> int:
    """Where a slice of page text starting at ``start`` is read from.

    The U+000A that ends the previous line when only whitespace other than U+000A lies between
    it and ``start`` (before ``bodyStart``, section 1 makes that code point U+000A), otherwise
    ``start``. Normalisation does not treat the start of a text as the start of a line (section
    3 step 4), so a slice that begins at a line start carries its line terminator with it.
    """
    position = start
    while position > index.body_start:
        character = index.text[position - 1]
        if character == "\n":
            return position - 1
        if not is_whitespace(ord(character)):
            return start
        position -= 1
    return position - 1 if _at(index.text, position - 1) == "\n" else start


def _last_line_has_tab(index: PageIndex, end: int) -> bool:
    """Whether the whole page line a slice ending at ``end`` ends on contains U+0009.

    The line is read in the body from the U+000A before it to the next U+000A, not only its part
    inside the slice. A slice that ends with U+000A ends on no partial line.
    """
    if end <= index.body_start or index.text[end - 1] == "\n":
        return False
    line_start = end - 1
    while line_start > index.body_start and index.text[line_start - 1] != "\n":
        line_start -= 1
    line_end = end
    while line_end < index.body_end and index.text[line_end] != "\n":
        line_end += 1
    return "\t" in index.text[line_start:line_end]


@dataclass(slots=True)
class _Resolved:
    raw: list[str]
    last_line_has_tab: bool


def _is_blank_slice(index: PageIndex, start: int, end: int) -> bool:
    if end <= start:
        return True
    try:
        text = index.text[_from_line_start(index, start) : end]
        return normalize_text(text, last_line_has_tab=_last_line_has_tab(index, end)) == ""
    except NormalizationError:
        return False


# Whitespace for the edge rules: section 3 step 5's list without U+00A0, U+2007 and U+202F, which
# join the groups of a number (`10 000`) and so are not a boundary between tokens.
NUMBER_JOINERS: Final = frozenset({0x00A0, 0x2007, 0x202F})


def _is_edge_whitespace(character: str | None) -> bool:
    if character is None:
        return False
    code_point = ord(character)
    return is_whitespace(code_point) and code_point not in NUMBER_JOINERS


def _is_decimal_digit(character: str | None) -> bool:
    """General category Nd, which is what JavaScript's ``\\p{Nd}`` tests."""
    return character is not None and unicodedata.category(character) == "Nd"


def _next_token(index: PageIndex, start: int, step: int) -> str | None:
    """The first non-whitespace code point from ``start`` in direction ``step``, in the body.

    Reading stops at U+000A (``None``): a number is never read across a line break.
    """
    position = start
    while index.body_start <= position < index.body_end:
        character = index.text[position]
        if character == "\n":
            return None
        if not is_whitespace(ord(character)):
            return character
        position += step
    return None


def _cuts_digit_group(index: PageIndex, inner: int, beyond: int, step: int) -> bool:
    """A digit at the edge with a digit beyond it, on the same line, is one grouped number."""
    return _is_decimal_digit(_at(index.text, inner)) and _is_decimal_digit(
        _next_token(index, beyond, step)
    )


def _start_cuts_word(pages: dict[int, PageIndex], span: Json) -> bool:
    """Section 6 start rule: does the section begin inside a token?

    Reads backwards from the code point before the first span, through its page's body and then
    the bodies of the pages before it (as declared, whether or not they pass section 1 or 2),
    skipping edge whitespace. The first other code point cuts a token if nothing was skipped
    before it, whatever it is (a letter, a digit, the ``.`` of ``0.5``, the minus of ``-20``,
    U+00A0 of ``10 000``), or if it is U+00AD. Reading past page 1 is no cut. It also cuts a
    number when the span's first code point that is not edge whitespace is a digit and the first
    non-whitespace code point before that on the same line is a digit too.
    """
    first = pages.get(span["page"])
    start = span["startOffset"]
    if first is not None:
        # The span's first code point that is not section 3 whitespace (joiners included).
        inner = start
        while inner < span["endOffset"] and is_whitespace(ord(first.text[inner])):
            inner += 1
        if inner < span["endOffset"] and _cuts_digit_group(first, inner, inner - 1, -1):
            return True
    skipped = False
    page_number = span["page"]
    position = start - 1
    index = first
    while index is not None:
        while position >= index.body_start:
            character = index.text[position]
            if _is_edge_whitespace(character):
                skipped = True
                position -= 1
                continue
            return character == SOFT_HYPHEN or not skipped
        page_number -= 1
        index = pages.get(page_number)
        if index is not None:
            position = index.body_end - 1
    return False


def _end_cuts_word(index: PageIndex, span: Json) -> bool:
    """Section 6 end rule: does the section end inside a token?

    It does if the last span, without trailing edge whitespace, ends in U+00AD; otherwise it does
    unless the code point at its end offset is edge whitespace or the end offset is at or past
    ``bodyEnd``. The ``1`` of ``1.5`` is a cut. It also cuts a number when the span's last code
    point that is not edge whitespace is a digit and the first non-whitespace code point after
    that on the same line is a digit too.
    """
    start, end = span["startOffset"], span["endOffset"]
    trimmed = end
    while trimmed > start and _is_edge_whitespace(index.text[trimmed - 1]):
        trimmed -= 1
    if trimmed > start and index.text[trimmed - 1] == SOFT_HYPHEN:
        return True
    if end >= index.body_end:
        return False
    if not _is_edge_whitespace(_at(index.text, end)):
        return True
    # The span's last code point that is not section 3 whitespace (joiners included).
    inner = trimmed
    while inner > start and is_whitespace(ord(index.text[inner - 1])):
        inner -= 1
    return inner > start and _cuts_digit_group(index, inner - 1, inner, 1)


def _resolve_spans(spans: list[Json], pages: dict[int, PageIndex]) -> _Resolved | tuple[str, str]:
    """Locate and hash-check a section's spans.

    Returns one contiguous raw slice per page — so the source's own characters, never
    whitespace of ours, decide where words begin and end — or a ``(status, reason)`` pair.
    """
    pieces: list[_Piece] = []
    previous: Json = None

    for span in spans:
        index = pages.get(span["page"])
        if index is None:
            return ("span-not-found", "page-not-found")
        if index.malformed:
            return ("span-not-found", "page-malformed")
        if index.body_issue is not None:
            return ("span-not-found", index.body_issue)
        body_start, body_end = index.body_start, index.body_end
        start_offset, end_offset = span["startOffset"], span["endOffset"]
        if start_offset < body_start or end_offset > body_end or start_offset >= end_offset:
            return ("span-not-found", "outside-body")
        if sha256_utf8(index.text[start_offset:end_offset]) != span["textSha256"]:
            return ("span-not-found", "hash-mismatch")

        last = pieces[-1] if pieces else None
        if previous is not None and last is not None:
            if span["page"] == previous["page"]:
                if start_offset < previous["endOffset"]:
                    return ("invalid-provenance", "span-order")
                if not _is_blank_slice(index, previous["endOffset"], start_offset):
                    return ("invalid-provenance", "non-contiguous")
                last.end = end_offset
            elif span["page"] == previous["page"] + 1:
                previous_index = pages.get(previous["page"])
                if (
                    previous_index is None
                    or not _is_blank_slice(
                        previous_index, previous["endOffset"], previous_index.body_end
                    )
                    or not _is_blank_slice(index, body_start, start_offset)
                ):
                    return ("invalid-provenance", "non-contiguous")
                # The blank tails and heads around a page break are part of the text, not
                # discarded: an invisible character hiding in them cannot change where a word ends.
                last.end = previous_index.body_end
                pieces.append(_Piece(index=index, start=body_start, end=end_offset))
            else:
                return ("invalid-provenance", "non-contiguous")
        else:
            pieces.append(
                _Piece(index=index, start=_from_line_start(index, start_offset), end=end_offset)
            )
        previous = span

    # The outer edges of a section must fall on word boundaries: a section may omit words, but
    # it may not begin or end inside one (spec section 6).
    if (
        pieces
        and spans
        and (_start_cuts_word(pages, spans[0]) or _end_cuts_word(pieces[-1].index, spans[-1]))
    ):
        return ("invalid-provenance", "word-cut")

    last = pieces[-1] if pieces else None
    return _Resolved(
        raw=[piece.index.text[piece.start : piece.end] for piece in pieces],
        last_line_has_tab=last is not None and _last_line_has_tab(last.index, last.end),
    )


def _diff_hint(expected: str, actual: str) -> dict[str, Json]:
    first = 0
    while first < len(expected) and first < len(actual) and expected[first] == actual[first]:
        first += 1
    suffix = 0
    while (
        suffix < len(expected) - first
        and suffix < len(actual) - first
        and expected[len(expected) - 1 - suffix] == actual[len(actual) - 1 - suffix]
    ):
        suffix += 1
    return {
        "expectedLength": len(expected),
        "actualLength": len(actual),
        "firstDifferingOffset": first,
        "commonSuffixLength": suffix,
        "expectedWordCount": count_words(expected),
        "actualWordCount": count_words(actual),
        "expectedSha256": sha256_utf8(expected),
        "actualSha256": sha256_utf8(actual),
    }


def normalize_narrative(div: str) -> dict[str, str]:
    """Normalised narrative text of one section as ``{"text": ...}``, or ``{"reason": ...}``."""
    try:
        text = normalize_text(xhtml_to_text(div))
    except XhtmlError as error:
        return {"reason": error.code}
    except NormalizationError as error:
        return {"reason": error.code}
    return {"reason": "empty-narrative"} if text == "" else {"text": text}


def compute_narrative_binding(sections: list[Json]) -> tuple[list[Json], str]:
    """Binding of a Bundle's narratives that Zone B can recompute without the source text."""
    bindings: list[Json] = []
    for section in sections:
        normalized = normalize_narrative(section["div"])
        bindings.append(
            {
                "sourceKey": section["sourceKey"],
                "normalizedTextSha256": (
                    sha256_utf8(normalized["text"]) if "text" in normalized else None
                ),
            }
        )
    return bindings, sha256_json(bindings)


def _coverage(pages: dict[int, PageIndex], verified_spans: list[Json]) -> dict[str, int]:
    page_code_points = 0
    body_code_points = 0
    covered_code_points = 0
    uncovered_gaps = 0
    for index in pages.values():
        # Page totals are reported alongside body totals so a reviewer can see how much text the
        # extractor-declared body range excludes; the body range itself is not trusted blindly.
        page_code_points += len(index.text)
        body_code_points += index.body_end - index.body_start
        spans = sorted(
            (span for span in verified_spans if span["page"] == index.page),
            key=lambda span: span["startOffset"],
        )
        cursor = index.body_start
        for span in spans:
            if not _is_blank_slice(index, cursor, span["startOffset"]):
                uncovered_gaps += 1
            covered_code_points += span["endOffset"] - span["startOffset"]
            cursor = span["endOffset"]
        if not _is_blank_slice(index, cursor, index.body_end):
            uncovered_gaps += 1
    return {
        "pageCodePoints": page_code_points,
        "bodyCodePoints": body_code_points,
        "coveredCodePoints": covered_code_points,
        "uncoveredGaps": uncovered_gaps,
    }


def _assert_integer_spans(provenance: list[Json]) -> None:
    """Every span's page and offsets are integers (``Number.isInteger``; ``1.0`` is one).

    A boolean is refused explicitly: ``True`` is an ``int`` to Python and would be read as page 1,
    where JavaScript reads it as no page at all. A structural error, never a status.
    """
    invalid = [
        f"Invalid span in provenance {entry['sourceKey']}"
        for entry in provenance
        for span in entry["spans"]
        if any(_as_integer(span.get(key)) is None for key in ("page", "startOffset", "endOffset"))
    ]
    if invalid:
        raise FidelityError("Provenance span is invalid", invalid)


def _assert_unique_keys(keys: list[str], what: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for key in keys:
        if key in seen:
            duplicates.append(f"Ambiguous {what} {key}")
        seen.add(key)
    if duplicates:
        raise FidelityError(f"Duplicate {what}", duplicates)


def verify_narrative_fidelity(payload: Json) -> dict[str, Json]:
    """Verify every narrative section against the extractor's page text and emit the report."""
    if payload["normalizationVersion"] != NORMALIZATION_VERSION:
        raise FidelityError(
            "Normalization version mismatch",
            [f"Expected {NORMALIZATION_VERSION}, received {payload['normalizationVersion']}"],
        )
    _assert_unique_keys([s["sourceKey"] for s in payload["sections"]], "source section")
    _assert_unique_keys([e["sourceKey"] for e in payload["provenance"]], "provenance entry")
    _assert_integer_spans(payload["provenance"])
    pages, issues = index_pages(payload["source"])
    provenance = {entry["sourceKey"]: entry for entry in payload["provenance"]}
    section_keys = {section["sourceKey"] for section in payload["sections"]}
    if not payload["sections"]:
        issues.append("No narrative sections to verify")
    for key in provenance:
        if key not in section_keys:
            issues.append(f"Orphan provenance {key}")

    results: list[dict[str, Json]] = []
    verified_spans: list[tuple[str, Json]] = []

    for section in payload["sections"]:
        entry = provenance.get(section["sourceKey"])
        base: dict[str, Json] = {"sourceKey": section["sourceKey"], "path": section["path"]}
        normalized = normalize_narrative(section["div"])
        normalized_hash: dict[str, Json] = (
            {"normalizedTextSha256": sha256_utf8(normalized["text"])}
            if "text" in normalized
            else {}
        )

        if entry is None:
            results.append(
                {**base, "status": "missing-provenance", "spanCount": 0, **normalized_hash}
            )
            continue
        spans = [_normalized_span(span) for span in entry["spans"]]
        span_count = len(spans)
        if "text" not in normalized:
            results.append(
                {
                    **base,
                    "status": "malformed-narrative",
                    "spanCount": span_count,
                    "reason": normalized["reason"],
                }
            )
            continue
        resolved = _resolve_spans(spans, pages)
        if isinstance(resolved, tuple):
            status, reason = resolved
            results.append(
                {
                    **base,
                    "status": status,
                    "spanCount": span_count,
                    "reason": reason,
                    **normalized_hash,
                }
            )
            continue
        # Pieces from consecutive pages are concatenated verbatim: a page body ends with its own
        # line terminator (or a soft hyphen when a word continues), so nothing is inserted here.
        expected = normalize_text(
            "".join(resolved.raw), last_line_has_tab=resolved.last_line_has_tab
        )
        if expected != normalized["text"]:
            results.append(
                {
                    **base,
                    "status": "mismatch",
                    "spanCount": span_count,
                    "details": _diff_hint(expected, normalized["text"]),
                    **normalized_hash,
                }
            )
            continue
        results.append({**base, "status": "verified", "spanCount": span_count, **normalized_hash})
        verified_spans.extend((section["sourceKey"], span) for span in spans)

    # Spans of different sections may not overlap: stitching one source passage into two sections
    # would otherwise pass every per-section check.
    overlapping: set[str] = set()
    ordered = sorted(verified_spans, key=lambda item: (item[1]["page"], item[1]["startOffset"]))
    for position in range(1, len(ordered)):
        previous_key, previous_span = ordered[position - 1]
        current_key, current_span = ordered[position]
        if (
            previous_span["page"] == current_span["page"]
            and current_span["startOffset"] < previous_span["endOffset"]
            and previous_key != current_key
        ):
            overlapping.add(previous_key)
            overlapping.add(current_key)
    sections = [
        {**result, "status": "invalid-provenance", "reason": "overlap"}
        if result["sourceKey"] in overlapping and result["status"] == "verified"
        else result
        for result in results
    ]

    verified = sum(1 for section in sections if section["status"] == "verified")
    status = "passed" if verified == len(sections) and not issues else "failed"
    body: dict[str, Json] = {
        "reportVersion": "1.0.0",
        "normalizationVersion": NORMALIZATION_VERSION,
        "extractedTextSha256": sha256_json(payload["source"]),
        "narrativeBindingSha256": compute_narrative_binding(payload["sections"])[1],
        "status": status,
        "sections": sections,
        "issues": issues,
        "summary": {"total": len(sections), "verified": verified},
        "coverage": _coverage(
            pages, [span for key, span in verified_spans if key not in overlapping]
        ),
    }
    return {**body, "reportHash": sha256_json(body)}


def verify_report_hash(report: dict[str, Json]) -> bool:
    """Recompute a report's hash from its own content, the way ``verifyReportHash`` does."""
    body = {key: value for key, value in report.items() if key != "reportHash"}
    return bool(sha256_json(body) == report["reportHash"])
