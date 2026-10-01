r"""Generate ePI sections at random and hold the reader to Chrome on every one it reads.

    uv run --frozen python scripts/fuzz_epi.py --cases 5000 --seed 1
    uv run --frozen python scripts/fuzz_epi.py --cases 300 --seed 7 --write corpus/epi-generated

The corpus holds what authors happened to write; this explores what they could write. Each
case is a div built from the elements, attributes and inline styles the reader accepts, nested
at random, with text in many scripts and every kind of whitespace, references and signs a
label holds ("<", "&", "\u2265", "\u00b5g", "x\u00b2", "H\u2082O"). Each case the reader reads
is shown by headless Chrome and compared line by line, character by character, mark by mark,
and list marker by list marker; each it refuses is counted. A case read otherwise than Chrome
shows is a fault in the reader, printed with its seed and number so it can be made again.

``--write`` keeps the cases as a corpus set (one ePI Bundle of all of them, with ``sources.json``
naming the seed), so ``scripts/browser_oracle.py record`` can record Chrome's answers and the
tests hold every later reader to them without a browser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from label_docx import epi
from label_docx.browser import (
    browser_lines,
    browser_markers,
    browser_sections,
    chrome_version,
    digest,
    first_difference,
    markers_digest,
    reader_lines,
    reader_markers,
)

XHTML = "http://www.w3.org/1999/xhtml"
WORDS = [
    "Store",
    "below",
    "25",
    "\u00b0C",
    "mg/kg",
    "\u2265",
    "\u2264",
    "\u00b5g",
    "1/10",
    "\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac",
    "\u017dlu\u0165ou\u010dk\u00fd",
    "na\u00efve",
    "x\u00b2",
    "H\u2082O",
    "&lt;",
    "&amp;",
    "&gt;",
    "&#160;",
    "&#x2265;",
    "(",
    ")",
    "\u2013",
    "\u201cdose\u201d",
    "ml",
    "tablet",
    "every",
    "day",
    "\u00a0",
    "-",
    "%",
]
SPACES = [" ", " ", " ", "  ", "\n", "\t", " \n  ", "\r\n"]
# The second round: what the first did not reach.
WORDS += ["anti&#173;body", "\u00a0\u00a0", "&#x00A0;", "GFR < 60", "&#8804;", "\u00ae", "\u2122"]
SPACES += [" \u00a0 "]
INLINE = ["span", "span", "strong", "b", "em", "i", "u", "s", "sup", "sub", "a"]
WEIGHTS = ["normal", "bold", "bolder", "lighter", "100", "300", "400", "500", "600", "700", "900"]
COLOURS = ["red", "#0070C0", "rgb(35, 31, 32)", "black", "#ffffff", "navy", "#999", "windowtext"]
COLOURS += ["#fefefe", "#111111", "white", "inherit", "#EEECE1", "currentcolor"]
BACKGROUNDS = ["yellow", "#d3d3d3", "white", "transparent", "silver", "#ceD4d9"]


class Case:
    """One generated div."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def text(self) -> str:
        """A few words, with whitespace of every kind between and around them."""
        rng = self.rng
        words = [rng.choice(WORDS) for _ in range(rng.randint(1, 4))]
        out = rng.choice(["", "", " ", "\n "])
        for index, word in enumerate(words):
            out += word if index == 0 else rng.choice(SPACES) + word
        return out + rng.choice(["", "", " ", " \n"])

    def style(self, inline: bool) -> str:
        """Up to three declarations a label's HTML holds, some the reader refuses."""
        rng = self.rng
        declarations: list[str] = []
        for _ in range(rng.randint(0, 3)):
            pick = rng.randrange(13 if inline else 7)
            if pick == 0:
                declarations.append(f"font-weight: {rng.choice(WEIGHTS)}")
            elif pick == 1:
                declarations.append(f"font-style: {rng.choice(['normal', 'italic', 'oblique'])}")
            elif pick == 2:
                choice = rng.choice(["underline", "line-through", "none"])
                declarations.append(f"text-decoration: {choice}")
            elif pick == 3:
                declarations.append(f"color: {rng.choice(COLOURS)}")
            elif pick == 4:
                declarations.append(f"background: {rng.choice(BACKGROUNDS)}")
            elif pick == 5:
                declarations.append(f"font-size: {rng.choice(['8pt', '10pt', '11pt', '12pt'])}")
            elif pick == 6:
                declarations.append(
                    rng.choice(["margin: 0cm", "line-height: normal", "text-align: justify"])
                )
            elif pick == 7:
                declarations.append(f"vertical-align: {rng.choice(['super', 'sub', 'baseline'])}")
            elif pick == 8:
                declarations.append(
                    rng.choice(["border-bottom: 1px solid black", "font-weight: bold !important"])
                )
            elif pick == 9:
                shift = rng.choice(["-3pt", "2pt", "-0.5pt", "1pt", "-6pt", "0pt"])
                declarations += ["position: relative", f"{rng.choice(['top', 'bottom'])}: {shift}"]
            elif pick == 10:
                declarations.append(
                    rng.choice(
                        [
                            "font-family: 'Times New Roman', serif",
                            "font-family: Arial, sans-serif",
                            "font-family: Calibri",
                            "font-size: 1pt",
                        ]
                    )
                )
            elif pick == 11:
                declarations.append(
                    rng.choice(
                        [
                            "border: 1px solid black",
                            "border-left: 1px solid",
                            "border-top: 0.5pt solid red",
                            "background-color: #ffff00",
                            "text-decoration: none",
                        ]
                    )
                )
            else:
                declarations.append(
                    rng.choice(
                        [
                            "line-height: 13pt",
                            "margin-left: 18pt",
                            "text-indent: -9pt",
                            "line-height: 14pt",
                        ]
                    )
                )
        return "; ".join(declarations)

    def attributes(self, name: str, inline: bool) -> str:
        """An element's style (a heading's with its size) and, for a link, its href."""
        out = ""
        style = self.style(inline)
        if name.startswith("h"):
            style = "; ".join(filter(None, [style, "font-size: 12pt"]))
        if style:
            out += f' style="{style}"'
        if name == "a" and self.rng.random() < 0.7:
            out += ' href="https://example.org/x"'
        return out

    def inline(self, depth: int) -> str:
        """Text and inline elements, nested to ``depth``."""
        rng = self.rng
        parts: list[str] = []
        for _ in range(rng.randint(1, 3)):
            roll = rng.random()
            if roll < 0.55 or depth > 3:
                parts.append(self.text())
            elif roll < 0.9:
                name = rng.choice(INLINE)
                inner = self.inline(depth + 1)
                if name == "a":
                    # An a in an a is rebuilt by an HTML parser; the reader refuses it.
                    inner = (
                        inner.replace("<a ", "<span ")
                        .replace("<a>", "<span>")
                        .replace("</a>", "</span>")
                    )
                parts.append(f"<{name}{self.attributes(name, inline=True)}>{inner}</{name}>")
            elif roll < 0.93:
                parts.append(rng.choice(["<br/>", "<br/><br/>", " <br/> "]))
            elif roll < 0.95:
                parts.append(
                    rng.choice(['<a name="x">anchor</a>', "<span> </span>", "<span></span>"])
                )
            else:
                parts.append('<img src="x.png" alt=""/>')
        return "".join(parts)

    def block(self, depth: int) -> str:
        """A paragraph, heading, list, table or division."""
        rng = self.rng
        roll = rng.random()
        if roll < 0.45 or depth > 2:
            name = rng.choice(["p", "p", "div", "h2", "h4"])
            return f"<{name}{self.attributes(name, inline=False)}>{self.inline(0)}</{name}>"
        if roll < 0.7:
            kind = rng.choice(["ul", "ol"])
            attributes = ""
            if kind == "ol" and rng.random() < 0.6:
                attributes += f' type="{rng.choice(["1", "a", "A", "i", "I"])}"'
            if kind == "ol" and rng.random() < 0.4:
                attributes += f' start="{rng.randint(0, 30)}"'
            if kind == "ul" and rng.random() < 0.3:
                attributes += f' type="{rng.choice(["disc", "circle", "square", "Square"])}"'
            items = ""
            for _ in range(rng.randint(1, 4)):
                inner = self.inline(0)
                if rng.random() < 0.3:
                    inner += self.block(depth + 1)
                items += f"<li{self.attributes('li', inline=False)}>{inner}</li>"
            return f"<{kind}{attributes}>{items}</{kind}>"
        if roll < 0.85:
            rows = ""
            for _ in range(rng.randint(1, 3)):
                spans = ["", "", ' colspan="2"', ' rowspan="2"', ' valign="top"']
                cells = "".join(
                    f"<{c}{self.attributes(c, inline=False)}{rng.choice(spans)}>"
                    f"{self.inline(0)}</{c}>"
                    for c in rng.choices(["td", "td", "th"], k=rng.randint(1, 3))
                )
                rows += f"<tr{self.attributes('tr', inline=False)}>{cells}</tr>"
            head = (
                f"<thead><tr><th>{self.inline(0)}</th></tr></thead>" if rng.random() < 0.3 else ""
            )
            body = f"<tbody{self.attributes('tbody', inline=False)}>{rows}</tbody>"
            return f"<table>{head}{body}</table>"
        inner = self.block(depth + 1) + self.block(depth + 1)
        return f"<div{self.attributes('div', inline=False)}>{inner}</div>"

    def div(self) -> str:
        """A section's div."""
        blocks = "".join(self.block(0) for _ in range(self.rng.randint(1, 4)))
        return f'<div xmlns="{XHTML}">{blocks}</div>'


def cases(seed: int, count: int) -> list[str]:
    """``count`` divs, the same ones for the same seed."""
    rng = random.Random(seed)
    return [Case(rng).div() for _ in range(count)]


def run(divs: list[str]) -> dict[str, int]:
    """Read each div, have Chrome show the ones read, and compare; print every difference."""
    read = [(i, epi.read_div(div)) for i, div in enumerate(divs)]
    readable = [(i, paragraphs) for i, (paragraphs, refusal, _) in read if refusal is None]
    counts = {"cases": len(divs), "read": len(readable), "agree": 0, "differ": 0}
    refused: dict[str, int] = {}
    for _, (_, refusal, _) in read:
        if refusal is not None:
            refused[refusal.code] = refused.get(refusal.code, 0) + 1
    for start in range(0, len(readable), 250):
        batch = readable[start : start + 250]
        shown = browser_sections([divs[i] for i, _ in batch])
        drawn = browser_markers([divs[i] for i, _ in batch])
        for (index, paragraphs), browser, markers in zip(batch, shown, drawn, strict=True):
            lines = browser_lines(browser) if browser["error"] is None else None
            mine = reader_lines(paragraphs)
            if lines is None:
                where = f"browser text not placed: {browser['error']}"
            elif mine != lines:
                where = first_difference(lines, mine)
            elif reader_markers(paragraphs) != markers:
                where = f"list markers: reader {reader_markers(paragraphs)}, Chrome {markers}"
            else:
                counts["agree"] += 1
                continue
            counts["differ"] += 1
            sys.stdout.write(f"case {index}: {where}\n")
    sys.stdout.write(f"refused: {json.dumps(refused, sort_keys=True)}\n")
    return counts


def cases_sha256(divs: list[str]) -> str:
    """SHA-256 of the cases, so a record is known to be of these cases."""
    return hashlib.sha256(json.dumps(divs, ensure_ascii=False).encode("utf-8")).hexdigest()


def record(divs: list[str], seed: int, target: Path) -> None:
    """Write Chrome's answers for every case: its lines' and marks' digests and its markers'."""
    answers: list[dict[str, str] | None] = []
    for start in range(0, len(divs), 250):
        batch = divs[start : start + 250]
        shown = browser_sections(batch)
        drawn = browser_markers(batch)
        for browser, markers in zip(shown, drawn, strict=True):
            if browser["error"] is not None:
                answers.append(None)
                continue
            answers.append({**digest(browser_lines(browser)), "markers": markers_digest(markers)})
    data = {
        "application": chrome_version(),
        "cases": len(divs),
        "casesSha256": cases_sha256(divs),
        "generator": f"scripts/fuzz_epi.py --cases {len(divs)} --seed {seed}",
        "answers": answers,
    }
    target.write_text(json.dumps(data, indent=0, sort_keys=True) + "\n", "utf-8")
    sys.stdout.write(f"wrote {target}\n")


def _bundle(divs: list[str]) -> bytes:
    composition = {
        "resourceType": "Composition",
        "title": "Generated cases",
        "section": [{"title": f"Case {i}", "text": {"div": d}} for i, d in enumerate(divs)],
    }
    bundle = {"resourceType": "Bundle", "type": "document", "entry": [{"resource": composition}]}
    return json.dumps(bundle, ensure_ascii=False, indent=1).encode("utf-8")


def main() -> int:
    """Run the cases; 1 if any is read otherwise than Chrome shows it."""
    parser = argparse.ArgumentParser(description="Hold the ePI reader to Chrome on random cases.")
    parser.add_argument("--cases", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--write", type=Path, help="keep the cases as a corpus set here")
    parser.add_argument("--record", type=Path, help="write Chrome's answers for the cases here")
    args = parser.parse_args()
    divs = cases(args.seed, args.cases)
    counts = run(divs)
    if args.record:
        record(divs, args.seed, args.record)
    sys.stdout.write(f"{json.dumps(counts, sort_keys=True)}\n")
    if args.write:
        args.write.mkdir(parents=True, exist_ok=True)
        data = _bundle(divs)
        name = f"generated-seed-{args.seed}.json"
        (args.write / name).write_bytes(data)
        manifest = {
            "schemaVersion": "1.0.0",
            "note": (
                f"Synthetic ePI sections written by scripts/fuzz_epi.py --cases {args.cases} "
                f"--seed {args.seed}; the same seed writes the same bytes."
            ),
            "sources": [
                {
                    "name": f"{args.cases} generated sections, seed {args.seed}",
                    "file": name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            ],
        }
        (args.write / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    return 1 if counts["differ"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
