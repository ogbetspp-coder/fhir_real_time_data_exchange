"""Ask a browser (headless Chrome) what each ePI section shows, and hold the ePI reader to it.

    uv run --frozen python scripts/browser_oracle.py record corpus/ema-epi
    uv run --frozen python scripts/browser_oracle.py compare path/to/epi.json [...]

A browser is the reference for an ePI's text, as Word is for a .docx. For each document the
oracle writes one page holding every section's div, which Chrome parses as HTML (each div into
its own element, as a viewer inserts it), lays out with its default style sheet, and reads back:

- the text a reader of the page would copy: the section selected whole, as Chrome serialises a
  selection (whitespace collapsed by the layout, a line break between blocks, a tab between
  table cells), with each picture as U+FFFC, the reader's character for one;
- for each text node, where its text lands in that serialisation (the node selected alone) and
  what the browser computed for it: font weight and style, colour, font size, the background
  painted under it, the decorations (underline, line-through) of every element it inherits them
  from, the vertical alignment and relative shift of each inline element around it, and the
  borders of those inline elements.

The comparison is line by line: both sides are split into lines at every line break, tab and
paragraph end (the browser's separators, the reader's paragraphs and its U+000A), empty lines
dropped. Each line must have the same characters, and each character the same marks: the
browser's facts turned into the reader's kinds by the reader's own thresholds (bold at weight
600, faint under a contrast of 1.33:1 or under 2pt, a colour nearly black as none...). The
thresholds are definitions; the facts they are applied to are the browser's.

``record`` writes ``browser.json`` for a corpus set: for each document and each section (in the
order ``walk`` gives), a SHA-256 of the browser's lines and of their marks, which
``tests/test_browser_oracle.py`` holds the reader to without a browser. ``compare`` prints the
verdict for any files and writes nothing, never the text itself.

Not yet compared: list numbers and bullets (drawn by the browser outside the text) and the
layout the reader refuses or states as a residual.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from label_docx import epi
from label_docx.reader import Paragraph

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

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


def chrome_version() -> str:
    """Chrome's version, as it reports it."""
    done = subprocess.run([str(CHROME), "--version"], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def browser_sections(divs: list[str]) -> list[dict[str, Any]]:
    """What Chrome shows for each div: its text, and the facts of each text node's piece."""
    payload = json.dumps(divs).replace("<", "\\u003c")
    with tempfile.TemporaryDirectory() as folder:
        page = Path(folder) / "page.html"
        page.write_text(PAGE.format(divs=payload), "utf-8")
        # Chrome writes the page as soon as it has run, then stays open (its extensions and
        # network services), so the page is read as it comes and Chrome closed after it.
        chrome = subprocess.Popen(
            [
                str(CHROME),
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
        assert chrome.stdout is not None  # noqa: S101 - set by stdout=PIPE
        dumped = bytearray()
        try:
            while not dumped.rstrip().endswith(b"</html>"):
                chunk = os.read(chrome.stdout.fileno(), 1 << 20)
                if not chunk:
                    break
                dumped += chunk
        finally:
            chrome.kill()
            chrome.wait()
    found = _OUT.search(dumped.decode("utf-8"))
    if found is None:
        raise SystemExit("Chrome did not write the page")
    results: list[dict[str, Any]] = json.loads(found.group(1))
    return results


def _hex(colour: str) -> str | None:
    """``rgb(r, g, b)`` as ``#rrggbb``; None for a colour with no paint (alpha 0)."""
    found = _RGB.fullmatch(colour)
    if found is None:
        raise SystemExit(f"a colour the oracle does not read: {colour}")
    if found.group(4) is not None and float(found.group(4)) == 0:
        return None
    if found.group(4) is not None and float(found.group(4)) != 1:
        raise SystemExit(f"a translucent colour the oracle does not read: {colour}")
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


def sections(path: Path) -> list[epi.Section]:
    """The document's sections that hold a div, in ``walk`` order."""
    document = epi.read_epi(path.read_bytes())
    return [s for s in epi.walk(document.sections) if s.paragraphs or s.refusal is not None]


def divs(path: Path) -> list[str]:
    """The sections' divs, in the order ``sections`` gives them."""
    out: list[str] = []

    def visit(raw: dict[str, Any]) -> None:
        div = raw.get("text", {}).get("div")
        paragraphs, refusal, _ = epi.read_div(div) if isinstance(div, str) else ((), None, ())
        if paragraphs or refusal is not None:
            out.append(div)
        for child in raw.get("section", []):
            visit(child)

    bundle = json.loads(path.read_bytes())
    (composition,) = [
        e["resource"] for e in bundle.get("entry", []) if "section" in e.get("resource", {})
    ]
    for raw in composition["section"]:
        visit(raw)
    return out


def check(path: Path) -> tuple[list[dict[str, str] | None], list[str]]:
    """The browser's digest of each section, and a verdict for each."""
    read = sections(path)
    shown = browser_sections(divs(path))
    digests: list[dict[str, str] | None] = []
    verdicts: list[str] = []
    for section, browser in zip(read, shown, strict=True):
        if browser["error"] is not None:
            digests.append(None)
            verdicts.append(f"browser text not placed: {browser['error']}")
            continue
        lines = browser_lines(browser)
        digests.append(digest(lines))
        if section.refusal is not None:
            verdicts.append(f"reader refuses: {section.refusal.code}")
            continue
        mine = reader_lines(section.paragraphs)
        verdicts.append("agrees" if mine == lines else "differs: " + first_difference(lines, mine))
    return digests, verdicts


def main() -> int:
    """Record a corpus set's answers, or compare files; 1 if the reader differs from Chrome."""
    parser = argparse.ArgumentParser(description="Hold the ePI reader to a browser.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("record", help="write browser.json for a corpus set").add_argument(
        "folder", type=Path
    )
    commands.add_parser("compare", help="compare files, writing nothing").add_argument(
        "files", type=Path, nargs="+"
    )
    args = parser.parse_args()
    paths = (
        sorted(p for p in args.folder.glob("*.json") if p.stem not in _NOT_EPI)
        if args.command == "record"
        else args.files
    )
    answers: dict[str, list[dict[str, str] | None]] = {}
    differs = False
    for path in paths:
        try:
            digests, verdicts = check(path)
        except epi.EpiRefusedError as refused:
            sys.stdout.write(f"{path.name}: reader refuses the document: {refused}\n")
            continue
        answers[path.name] = digests
        counts: dict[str, int] = {}
        for verdict in verdicts:
            counts[verdict.split(":")[0]] = counts.get(verdict.split(":")[0], 0) + 1
        sys.stdout.write(f"{path.name}: {json.dumps(counts, sort_keys=True)}\n")
        for index, verdict in enumerate(verdicts):
            if not verdict.startswith(("agrees", "reader refuses")):
                differs = True
                sys.stdout.write(f"  section {index + 1}: {verdict}\n")
        sys.stdout.flush()
    if args.command == "record":
        record = {
            "application": chrome_version(),
            "method": (
                "each section's div parsed as HTML into its own element of one page, laid out "
                "with the browser's default style sheet; the text of the section selected "
                "whole, and each text node's place in it and computed style"
            ),
            "recorded": datetime.date.today().isoformat(),
            "sections": answers,
        }
        target = args.folder / "browser.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {target}\n")
    return 1 if differs else 0


_NOT_EPI = {"sources", "expected", "word", "browser"}

if __name__ == "__main__":
    raise SystemExit(main())
