"""Mutation testing of documents: does the reader notice every change a reader of the page would?

    uv run --frozen python scripts/mutate.py corpus/ema-qrd [more folders or files] [--per 5]

Each mutation makes one small edit to a document's XML. A mutation that changes what Word shows
(a letter, a word, a paragraph, a symbol, a superscript, hidden text, a list label) must change
the reader's result or make it refuse: if the result is the same, a change went unnoticed (a
``miss``). A mutation that changes nothing Word shows (bookkeeping attributes, a run split in two,
a proofing mark) must leave the result byte-identical: otherwise the reader is not stable (an
``unstable``). Some mutations change appearance the reader does not report (font size); they are
counted as ``unreported`` and must leave the result identical, so what the reader is blind to is
known and stays the same.

The result compared is the reader's canonical JSON without its ``source`` (which names the bytes
and so always differs). Mutants are chosen by a generator seeded with the document's SHA-256, so a
run is the same every time. The script prints a table and never a document's text.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import random
import re
import sys
import zipfile
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import NamedTuple
from xml.sax.saxutils import escape, unescape

from label_docx.output import read

DOCUMENT = "word/document.xml"
_TEXT = re.compile(r"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)")
_RUN = re.compile(r"<w:r(?:\s[^>]*)?>(?:(?!</w:r>).)*?</w:r>", re.S)
_PARAGRAPH = re.compile(r"<w:p(?:\s[^>]*)?>(?:(?!</w:p>).)*?</w:p>", re.S)
_ILVL = re.compile(r'<w:ilvl w:val="(\d)"/>')
_SYM = re.compile(r'(<w:sym\b[^>]*w:char=")([0-9A-Fa-f]{2,4})(")')

type Mutator = Callable[[str, random.Random], str | None]

# The document's styles part, for the toggle mutations (set by ``outcomes`` for each document).
_STYLES = {"xml": ""}


class Mutation(NamedTuple):
    """One kind of edit: what Word shows changes (``change``), or not (``same``, ``unreported``)."""

    name: str
    expect: str
    mutate: Mutator


def _pick[T](items: list[T], rng: random.Random) -> T | None:
    return items[rng.randrange(len(items))] if items else None


_LAYOUT = {"PAGEREF", "PAGE", "NUMPAGES", "SECTIONPAGES"}


def _page_numbers(xml: str) -> list[tuple[int, int]]:
    """Where page-number fields stand: Word sets their text from the layout, not the file."""
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, list[str]]] = []
    for run in _RUN.finditer(xml):
        body = run.group(0)
        if 'fldCharType="begin"' in body:
            stack.append((run.start(), []))
        for instruction in re.findall(r"<w:instrText[^>]*>([^<]*)</w:instrText>", body):
            if stack:
                stack[-1][1].append(instruction)
        if 'fldCharType="end"' in body and stack:
            start, code = stack.pop()
            words = "".join(code).split()
            if words and words[0].upper() in _LAYOUT:
                spans.append((start, run.end()))
    return spans


# Drawings, shapes and their fallbacks: a floating object's text is set aside unread ("Anchored"
# in the reader's docstring), and its run's formatting is not drawn on it.
_OBJECT = re.compile(r"<(/?)(?:w:drawing|w:pict|w:object|mc:AlternateContent)\b[^>]*?(/?)>")


def _aside(xml: str) -> list[tuple[int, int]]:
    """Page numbers and objects (one still open at the end runs to it)."""
    spans = _page_numbers(xml)
    depth = start = 0
    for tag in _OBJECT.finditer(xml):
        if tag.group(2):
            continue
        if not tag.group(1):
            start = tag.start() if depth == 0 else start
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                spans.append((start, tag.end()))
    return spans + ([(start, len(xml))] if depth else [])


def _outside(spans: list[tuple[int, int]], start: int, end: int | None = None) -> bool:
    """Whether ``xml[start:end]`` (or the position ``start``) meets none of ``spans``."""
    stop = start + 1 if end is None else end
    return not any(a < stop and start < b for a, b in spans)


def _visible_texts(xml: str) -> list[re.Match[str]]:
    """Text runs a reader of the page sees, page numbers and objects aside."""
    aside = _aside(xml)
    return [
        m
        for m in _TEXT.finditer(xml)
        if unescape(m.group(2)).strip() and _outside(aside, m.start())
    ]


def _replace_text(xml: str, match: re.Match[str], text: str) -> str:
    opening = match.group(1)
    if text != text.strip(" ") and "xml:space" not in opening:
        opening = opening[:-1] + ' xml:space="preserve">'
    return xml[: match.start()] + opening + escape(text) + match.group(3) + xml[match.end() :]


def change_character(xml: str, rng: random.Random) -> str | None:
    """One letter or digit becomes another."""
    match = _pick(_visible_texts(xml), rng)
    if match is None:
        return None
    text = unescape(match.group(2))
    places = [i for i, c in enumerate(text) if c.isascii() and c.isalnum()]
    if not places:
        return None
    i = places[rng.randrange(len(places))]
    c = text[i]
    other = str((int(c) + 1) % 10) if c.isdigit() else ("b" if c.lower() == "a" else "a")
    other = other.upper() if c.isupper() else other
    return _replace_text(xml, match, text[:i] + other + text[i + 1 :])


def delete_word(xml: str, rng: random.Random) -> str | None:
    """One word is removed from a text run."""
    match = _pick(_visible_texts(xml), rng)
    if match is None:
        return None
    text = unescape(match.group(2))
    words = list(re.finditer(r"\S+", text))
    word = words[rng.randrange(len(words))]
    return _replace_text(xml, match, text[: word.start()] + text[word.end() :])


def insert_word(xml: str, rng: random.Random) -> str | None:
    """A word is added to a text run."""
    match = _pick(_visible_texts(xml), rng)
    if match is None:
        return None
    text = unescape(match.group(2))
    i = rng.randrange(len(text) + 1)
    return _replace_text(xml, match, text[:i] + " not " + text[i:])


def delete_paragraph(xml: str, rng: random.Random) -> str | None:
    """A paragraph with text is removed."""
    paragraphs = [
        m
        for m in _PARAGRAPH.finditer(xml)
        if _visible_texts(m.group(0)) and "sectPr" not in m.group(0)
    ]
    match = _pick(paragraphs, rng)
    return None if match is None else xml[: match.start()] + xml[match.end() :]


def _with_property(xml: str, rng: random.Random, prop: str, absent: str) -> str | None:
    """A run with text gains ``prop`` in its run properties, where ``absent`` is not there."""
    aside = _aside(xml)
    runs = [
        m
        for m in _RUN.finditer(xml)
        if _visible_texts(m.group(0))
        and absent not in m.group(0)
        and _outside(aside, m.start(), m.end())
    ]
    match = _pick(runs, rng)
    if match is None:
        return None
    run = match.group(0)
    if "<w:rPr>" in run:
        changed = run.replace("<w:rPr>", f"<w:rPr>{prop}", 1)
    else:
        changed = re.sub(r"(<w:r(?:\s[^>]*)?>)", rf"\1<w:rPr>{prop}</w:rPr>", run, count=1)
    return xml[: match.start()] + changed + xml[match.end() :]


def _flip(xml: str, rng: random.Random, tag: str) -> str | None:
    """A run's toggle ``tag`` turned the other way, so that Word shows a change.

    A run's own setting wins in Word, so flipping one always shows. A run without one is given
    one only where no style or default in the document sets the toggle, so it was off.
    """
    # Word writes both <w:b/> and <w:b /> (and never matches <w:bCs/> here).
    setting = re.compile(rf'<w:{tag}(?:\s+w:val="(\w+)")?\s*/>')
    styled = bool(setting.search(_STYLES["xml"]))
    aside = _aside(xml)
    runs = []
    for m in _RUN.finditer(xml):
        properties = re.search(r"<w:rPr>.*?</w:rPr>", m.group(0), re.S)
        direct = setting.search(properties.group(0)) if properties else None
        if (
            _visible_texts(m.group(0))
            and _outside(aside, m.start(), m.end())
            and (direct or not styled)
        ):
            runs.append(m)
    match = _pick(runs, rng)
    if match is None:
        return None
    run = match.group(0)
    properties = re.search(r"<w:rPr>.*?</w:rPr>", run, re.S)
    direct = setting.search(properties.group(0)) if properties else None
    if properties and direct:
        on = direct.group(1) not in ("0", "false", "off")
        flipped = f'<w:{tag} w:val="{"0" if on else "1"}"/>'
        start = properties.start() + direct.start()
        changed = run[:start] + flipped + run[properties.start() + direct.end() :]
    elif properties:
        changed = run.replace("<w:rPr>", f"<w:rPr><w:{tag}/>", 1)
    else:
        changed = re.sub(r"(<w:r(?:\s[^>]*)?>)", rf"\1<w:rPr><w:{tag}/></w:rPr>", run, count=1)
    return xml[: match.start()] + changed + xml[match.end() :]


def symbol(xml: str, rng: random.Random) -> str | None:
    """A Symbol-font glyph becomes another (≥ becomes ≤, say)."""
    match = _pick(list(_SYM.finditer(xml)), rng)
    if match is None:
        return None
    code = int(match.group(2), 16)
    other = code ^ 0x10  # 0xB3 (≥) and 0xA3 (≤), 0xB0 (°) and 0xA0...
    return xml[: match.start(2)] + f"{other:04X}" + xml[match.end(2) :]


def list_level(xml: str, rng: random.Random) -> str | None:
    """A list item moves one level down or up."""
    match = _pick(list(_ILVL.finditer(xml)), rng)
    if match is None:
        return None
    level = int(match.group(1))
    other = level + 1 if level < 8 else level - 1
    return xml[: match.start(1)] + str(other) + xml[match.end(1) :]


def rsid(xml: str, rng: random.Random) -> str | None:
    """Word's revision-session bookkeeping on a paragraph changes (or appears)."""
    match = _pick(list(re.finditer(r"<w:p(?=[\s>])[^>]*>", xml)), rng)
    if match is None:
        return None
    tag = match.group(0)
    value = f"{rng.randrange(16**8):08X}"
    if 'w:rsidR="' in tag:
        changed = re.sub(r'w:rsidR="[0-9A-Fa-f]*"', f'w:rsidR="{value}"', tag, count=1)
    else:
        changed = tag[:4] + f' w:rsidR="{value}"' + tag[4:]
    return xml[: match.start()] + changed + xml[match.end() :]


_PLAIN_RUN = re.compile(
    r"<w:r(?:\s[^>]*)?>(?:<w:rPr>(?:(?!</w:rPr>).)*</w:rPr>)?<w:t(?:\s[^>]*)?>[^<]*</w:t></w:r>",
    re.S,
)


def split_run(xml: str, rng: random.Random) -> str | None:
    """A run holding one text of two or more characters is split into two alike runs."""
    runs = [
        m for m in _PLAIN_RUN.finditer(xml) if len(unescape(_TEXT.findall(m.group(0))[0][1])) >= 2
    ]
    match = _pick(runs, rng)
    if match is None:
        return None
    run = match.group(0)
    text = _TEXT.search(run)
    if text is None:
        return None
    content = unescape(text.group(2))
    i = 1 + rng.randrange(len(content) - 1)
    head = _replace_text(run, text, content[:i])
    tail = _replace_text(run, text, content[i:])
    return xml[: match.start()] + head + tail + xml[match.end() :]


def proofing(xml: str, rng: random.Random) -> str | None:
    """A spelling mark is added before a run."""
    match = _pick([m for m in _RUN.finditer(xml) if _visible_texts(m.group(0))], rng)
    if match is None:
        return None
    return xml[: match.start()] + '<w:proofErr w:type="spellStart"/>' + xml[match.start() :]


MUTATIONS = [
    Mutation("change a character", "change", change_character),
    Mutation("delete a word", "change", delete_word),
    Mutation("insert a word", "change", insert_word),
    Mutation("delete a paragraph", "change", delete_paragraph),
    Mutation(
        "superscript",
        "change",
        partial(_with_property, prop='<w:vertAlign w:val="superscript"/>', absent="vertAlign"),
    ),
    Mutation("strike through", "change", partial(_flip, tag="strike")),
    Mutation("hide text", "change", partial(_with_property, prop="<w:vanish/>", absent="vanish")),
    Mutation("change a symbol", "change", symbol),
    Mutation("change a list level", "change", list_level),
    Mutation("revision bookkeeping", "same", rsid),
    Mutation("split a run", "same", split_run),
    Mutation("proofing mark", "same", proofing),
    Mutation(
        "proofing language",
        "same",
        partial(_with_property, prop='<w:lang w:val="fr-FR"/>', absent="<w:lang"),
    ),
    Mutation("bold", "change", partial(_flip, tag="b")),
    Mutation("italic", "change", partial(_flip, tag="i")),
    Mutation(
        "font size",
        "unreported",
        partial(_with_property, prop='<w:sz w:val="28"/>', absent="<w:sz "),
    ),
]


def _result(data: bytes) -> str:
    """The reader's result without its source: what it read, or the refusal code."""
    value = json.loads(read(data)[0])
    if "refusal" in value:
        return f"refused:{value['refusal']['code']}"
    value.pop("source")
    return json.dumps(value, sort_keys=True)


def _with_document(data: bytes, xml: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        parts = [(info, source.read(info)) for info in source.infolist()]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info, content in parts:
            target.writestr(info.filename, xml.encode() if info.filename == DOCUMENT else content)
    return out.getvalue()


def outcomes(data: bytes, per: int) -> list[tuple[str, str, str]]:
    """``(mutation, expectation, verdict)`` for up to ``per`` mutants of each kind."""
    original = _result(data)
    if original.startswith("refused:"):
        return []
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        xml = source.read(DOCUMENT).decode("utf-8")
        names = source.namelist()
        _STYLES["xml"] = (
            source.read("word/styles.xml").decode("utf-8") if "word/styles.xml" in names else ""
        )
    rng = random.Random(hashlib.sha256(data).hexdigest())
    out: list[tuple[str, str, str]] = []
    for mutation in MUTATIONS:
        seen: set[str] = set()
        for _ in range(per * 3):
            if len(seen) == per:
                break
            mutant = mutation.mutate(xml, rng)
            if mutant is None or mutant == xml or mutant in seen:
                continue
            seen.add(mutant)
            result = _result(_with_document(data, mutant))
            if mutation.expect == "change":
                verdict = (
                    "refused"
                    if result.startswith("refused:")
                    else "detected"
                    if result != original
                    else "miss"
                )
            else:
                verdict = "stable" if result == original else "unstable"
            out.append((mutation.name, mutation.expect, verdict))
    return out


def main() -> int:
    """Print the table; 1 if any change went unnoticed or any unchanged page read differently."""
    parser = argparse.ArgumentParser(description="Mutation testing of documents.")
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--per", type=int, default=5, help="mutants of each kind per document")
    args = parser.parse_args()
    files = sorted(f for p in args.paths for f in ([p] if p.is_file() else p.rglob("*.docx")))
    table: dict[tuple[str, str], collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for path in files:
        for name, expect, verdict in outcomes(path.read_bytes(), args.per):
            table[(name, expect)][verdict] += 1
    bad = 0
    sys.stdout.write(f"{len(files)} documents\n")
    for mutation in MUTATIONS:
        counts = table[(mutation.name, mutation.expect)]
        bad += counts["miss"] + counts["unstable"]
        line = ", ".join(f"{n} {v}" for v, n in sorted(counts.items()))
        sys.stdout.write(f"  {mutation.name:22} ({mutation.expect:10}) {line or 'no site'}\n")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
