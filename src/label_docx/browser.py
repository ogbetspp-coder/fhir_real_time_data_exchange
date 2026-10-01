"""Hold an ePI's reading to a browser: what headless Chrome shows for each section.

Chrome parses each section's div as HTML (each into its own element of one page, as a viewer
inserts it), lays it out with its default style sheet, and reports the text a reader of the page
would copy (the section selected whole) and, for each text node, where its text lands in that
text and what Chrome computed for it: weight, slant, colour, size, the background under it, the
decorations it inherits, the vertical alignment and shifts of the inline elements around it, and
their borders. Those facts become the reader's mark kinds by the reader's own thresholds (bold at
weight 600, faint under a contrast of 1.33:1...), and the two sides are compared line by line,
character by character and mark by mark.

``verify_epi`` does this for one ePI and its result, as the service does for every ePI it
ingests where Chrome is installed (``label-docx-service serve``); ``scripts/browser_oracle.py``
does it for the corpus. Chrome is not a dependency: where it is not installed, nothing is
verified this way, and the service says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from label_docx import epi
from label_docx.reader import Paragraph

# How long Chrome may take over one document's page.
TIMEOUT_SECONDS = 300


class BrowserError(Exception):
    """Chrome could not be asked: it is not there, failed or did not answer in time."""


# Where Chrome is looked for: LABEL_CHROME, then the usual places on macOS and Linux.
_CHROMES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def find_chrome() -> Path | None:
    """The Chrome to ask, or None where there is none."""
    for candidate in (os.environ.get("LABEL_CHROME"), *_CHROMES):
        if not candidate:
            continue
        found = shutil.which(candidate) if "/" not in candidate else candidate
        if found and Path(found).exists():
            return Path(found)
    return None


CHROME = find_chrome() or Path(_CHROMES[0])

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>oracle</title></head><body>
<script type="application/json" id="divs">{divs}</script>
<script>
"use strict";
const divs = JSON.parse(document.getElementById("divs").textContent);
const selection = window.getSelection();
const results = [];
function selected(range) {{
  selection.removeAllRanges();
  selection.addRange(range);
  return selection.toString();
}}
function points(value) {{ return value === "auto" ? 0 : parseFloat(value) * 0.75; }}
function facts(element, host) {{
  const own = getComputedStyle(element);
  const out = {{
    weight: own.fontWeight, style: own.fontStyle, color: own.color, size: own.fontSize,
    underline: false, strike: false, background: null, align: [], shift: 0, borders: []
  }};
  for (let e = element; e && e !== host; e = e.parentElement) {{
    const style = getComputedStyle(e);
    if (style.textDecorationLine.includes("underline")) out.underline = true;
    if (style.textDecorationLine.includes("line-through")) out.strike = true;
    if (out.background === null && style.backgroundColor !== "rgba(0, 0, 0, 0)") {{
      out.background = style.backgroundColor;
    }}
  }}
  for (let e = element; e && e !== host; e = e.parentElement) {{
    const style = getComputedStyle(e);
    if (style.display !== "inline") break;
    if (style.verticalAlign !== "baseline") out.align.push(style.verticalAlign);
    if (style.position === "relative") {{
      out.shift += style.top !== "auto" ? points(style.top) : -points(style.bottom);
    }}
    for (const side of ["Top", "Right", "Bottom", "Left"]) {{
      const kind = style["border" + side + "Style"];
      const width = parseFloat(style["border" + side + "Width"]);
      if (kind !== "none" && kind !== "hidden" && width > 0) {{
        out.borders.push(side.toLowerCase());
      }}
    }}
  }}
  return out;
}}
for (const div of divs) {{
  const host = document.createElement("div");
  document.body.appendChild(host);
  host.innerHTML = div;
  for (const picture of host.querySelectorAll("img")) {{
    picture.replaceWith(document.createTextNode("\\ufffc"));
  }}
  const whole = document.createRange();
  whole.selectNodeContents(host);
  const text = selected(whole);
  const runs = [];
  let at = 0, error = null;
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {{
    const range = document.createRange();
    range.selectNodeContents(node);
    const piece = selected(range);
    if (!piece) continue;
    const found = text.indexOf(piece, at);
    if (found < 0 || /[^\\n\\t]/.test(text.slice(at, found))) {{
      error = "a text node's piece is not where the whole text has it";
      break;
    }}
    runs.push([found, found + piece.length, facts(node.parentElement, host)]);
    at = found + piece.length;
  }}
  if (error === null && /[^\\n\\t]/.test(text.slice(at))) {{
    error = "text after the last text node's piece";
  }}
  results.push({{ text, runs, error }});
  host.remove();
}}
selection.removeAllRanges();
const out = document.createElement("script");
out.type = "application/json";
out.id = "out";
out.textContent = JSON.stringify(results).replace(/</g, "\\\\u003c");
document.body.appendChild(out);
</script>
</body></html>
"""

_OUT = re.compile(r'<script type="application/json" id="out">(.*?)</script>', re.S)
_RGB = re.compile(r"rgba?\((\d+), (\d+), (\d+)(?:, ([0-9.]+))?\)")


def chrome_version(chrome: Path = CHROME) -> str:
    """Chrome's version, as it reports it."""
    done = subprocess.run([str(chrome), "--version"], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def browser_sections(divs: list[str], chrome: Path = CHROME) -> list[dict[str, Any]]:
    """What Chrome shows for each div: its text, and the facts of each text node's piece."""
    payload = json.dumps(divs).replace("<", "\\u003c")
    with tempfile.TemporaryDirectory() as folder:
        page = Path(folder) / "page.html"
        page.write_text(PAGE.format(divs=payload), "utf-8")
        # Chrome writes the page as soon as it has run, then stays open (its extensions and
        # network services), so the page is read as it comes and Chrome closed after it.
        process = subprocess.Popen(
            [
                str(chrome),
                "--headless",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={folder}/profile",
                "--dump-dom",
                page.as_uri(),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None  # noqa: S101 - set by stdout=PIPE
        # A Chrome that hangs is stopped: the page then never arrives, and nothing is verified.
        timer = threading.Timer(TIMEOUT_SECONDS, process.kill)
        timer.start()
        dumped = bytearray()
        try:
            while not dumped.rstrip().endswith(b"</html>"):
                chunk = os.read(process.stdout.fileno(), 1 << 20)
                if not chunk:
                    break
                dumped += chunk
        finally:
            timer.cancel()
            process.kill()
            process.wait()
    found = _OUT.search(dumped.decode("utf-8", errors="replace"))
    if found is None:
        raise BrowserError("Chrome did not write the page")
    results: list[dict[str, Any]] = json.loads(found.group(1))
    return results


def _hex(colour: str) -> str | None:
    """``rgb(r, g, b)`` as ``#rrggbb``; None for a colour with no paint (alpha 0)."""
    found = _RGB.fullmatch(colour)
    if found is None:
        raise BrowserError(f"a colour the oracle does not read: {colour}")
    if found.group(4) is not None and float(found.group(4)) == 0:
        return None
    if found.group(4) is not None and float(found.group(4)) != 1:
        raise BrowserError(f"a translucent colour the oracle does not read: {colour}")
    return "#" + "".join(f"{int(found.group(i)):02x}" for i in (1, 2, 3))


def _kinds(facts: dict[str, Any]) -> frozenset[str]:
    """The reader's mark kinds for a text node, from what the browser computed for it."""
    kinds: set[str] = set()
    if int(facts["weight"]) >= 600:
        kinds.add("bold")
    if facts["style"] != "normal":
        kinds.add("italic")
    if facts["underline"] or "bottom" in facts["borders"]:
        kinds.add("underline")
    if set(facts["borders"]) - {"bottom"}:
        kinds.add("border")
    if facts["strike"]:
        kinds.add("strike")
    shift = facts["shift"]
    if "super" in facts["align"] or shift <= -epi._SHIFT_MARK_POINTS:
        kinds.add("superscript")
    if "sub" in facts["align"] or shift >= epi._SHIFT_MARK_POINTS:
        kinds.add("subscript")
    colour = _hex(facts["color"])
    background = _hex(facts["background"]) if facts["background"] else None
    if background is not None and not epi._light(background):
        kinds.add(f"shading-{background}")
    text = epi._rgb(colour, (0, 0, 0)) if colour else None
    under = epi._rgb(background, (0xFF, 0xFF, 0xFF))
    size = float(facts["size"].removesuffix("px")) * 0.75
    if text is None or epi._contrast(text, under) < epi._FAINT_CONTRAST or size < 2:
        kinds.add("faint")
    elif colour is not None and colour != "#000000" and not epi._dark(colour):
        kinds.add(f"color-{colour}")
    return frozenset(kinds)


Line = tuple[str, tuple[frozenset[str], ...]]


def _split(characters: list[tuple[str, frozenset[str]]], separators: str) -> list[Line]:
    lines: list[Line] = []
    current: list[tuple[str, frozenset[str]]] = []
    for character, kinds in [*characters, (separators[0], frozenset())]:
        if character in separators:
            if current:
                lines.append(("".join(c for c, _ in current), tuple(k for _, k in current)))
            current = []
        else:
            current.append((character, kinds))
    return lines


def browser_lines(section: dict[str, Any]) -> list[Line]:
    """The browser's text in lines, each character with its marks."""
    text: str = section["text"]
    kinds: list[frozenset[str]] = [frozenset()] * len(text)
    for start, end, facts in section["runs"]:
        node = _kinds(facts)
        for index in range(start, end):
            kinds[index] = node
    return _split(list(zip(text, kinds, strict=True)), "\n\t")


def _canonical_kind(kind: str) -> str:
    """A colour kind with the reader's colour spelling written as the browser's ``#rrggbb``."""
    for prefix in ("color-", "shading-"):
        if kind.startswith(prefix):
            red, green, blue = epi._rgb(kind.removeprefix(prefix), (0, 0, 0))
            return f"{prefix}#{red:02x}{green:02x}{blue:02x}"
    return kind


def reader_lines(paragraphs: tuple[Paragraph, ...]) -> list[Line]:
    """The reader's paragraphs in lines, each character with its marks."""
    characters: list[tuple[str, frozenset[str]]] = []
    for paragraph in paragraphs:
        kinds: list[set[str]] = [set() for _ in paragraph.text]
        for mark in paragraph.marks:
            for index in range(mark.start, mark.end):
                kinds[index].add(_canonical_kind(mark.kind))
        characters += [(c, frozenset(k)) for c, k in zip(paragraph.text, kinds, strict=True)]
        characters.append(("\n", frozenset()))
    return _split(characters, "\n")


def digest(lines: list[Line]) -> dict[str, str]:
    """SHA-256 of the lines' text, and of their marks, in canonical JSON."""
    text = json.dumps([line for line, _ in lines], ensure_ascii=False, separators=(",", ":"))
    marks = json.dumps([[sorted(k) for k in kinds] for _, kinds in lines], separators=(",", ":"))
    return {
        "text": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "marks": hashlib.sha256(marks.encode("utf-8")).hexdigest(),
    }


def first_difference(browser: list[Line], reader: list[Line]) -> str:
    """Where the two first differ: which line, which character, text or marks (no text shown)."""
    for index, (shown, read) in enumerate(zip(browser, reader, strict=False)):
        if shown[0] != read[0]:
            at = next(
                (i for i, (a, b) in enumerate(zip(shown[0], read[0], strict=False)) if a != b),
                min(len(shown[0]), len(read[0])),
            )
            return f"line {index + 1}: text differs at character {at + 1}"
        for at, (a, b) in enumerate(zip(shown[1], read[1], strict=True)):
            if a != b:
                return (
                    f"line {index + 1}: marks differ at character {at + 1}: browser "
                    f"{sorted(a)}, reader {sorted(b)}"
                )
    return f"{len(browser)} lines shown, {len(reader)} read"


def result_lines(paragraphs: list[dict[str, Any]]) -> list[Line]:
    """A result's paragraphs (JSON, as served) in lines, each character with its marks."""
    characters: list[tuple[str, frozenset[str]]] = []
    for paragraph in paragraphs:
        text: str = paragraph["text"]
        kinds: list[set[str]] = [set() for _ in text]
        for mark in paragraph["marks"]:
            for index in range(mark["start"], mark["end"]):
                kinds[index].add(_canonical_kind(mark["kind"]))
        characters += [(c, frozenset(k)) for c, k in zip(text, kinds, strict=True)]
        characters.append(("\n", frozenset()))
    return _split(characters, "\n")


def verify_epi(data: bytes, result: dict[str, Any], chrome: Path = CHROME) -> dict[str, Any]:
    """Chrome's verdict on an ePI result: every section read, held to what Chrome shows.

    ``result`` is the result as served (``epi_output``). The verdict names Chrome's version,
    counts the sections that agree and the ones the reader refused, and lists each section that
    differs with where (a line and character, never the text).
    """
    bundle = json.loads(data.decode("utf-8"))
    composition = next(
        e["resource"]
        for e in bundle["entry"]
        if isinstance(e.get("resource"), dict) and "section" in e["resource"]
    )
    pairs: list[tuple[str, dict[str, Any]]] = []
    refused = 0

    def visit(raws: list[dict[str, Any]], read: list[dict[str, Any]]) -> None:
        nonlocal refused
        for raw, section in zip(raws, read, strict=True):
            div = raw.get("text", {}).get("div")
            if section["refusal"] is not None:
                refused += 1
            elif isinstance(div, str):
                pairs.append((div, section))
            visit(raw.get("section", []), section["sections"])

    visit(composition["section"], result["sections"])
    shown = browser_sections([div for div, _ in pairs], chrome) if pairs else []
    differs: list[dict[str, Any]] = []
    for index, ((_, section), browser) in enumerate(zip(pairs, shown, strict=True)):
        if browser["error"] is not None:
            differs.append({"section": index + 1, "where": browser["error"]})
            continue
        mine, theirs = result_lines(section["paragraphs"]), browser_lines(browser)
        if mine != theirs:
            differs.append({"section": index + 1, "where": first_difference(theirs, mine)})
    return {
        "application": chrome_version(chrome),
        "agrees": len(pairs) - len(differs),
        "differs": differs,
        "refused": refused,
        "sections": len(pairs) + refused,
    }
