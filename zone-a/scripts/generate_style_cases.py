"""Write, or check, the CSS cases both readers of the EMA's divs are held to.

    uv run --frozen python scripts/generate_style_cases.py          # write
    uv run --frozen python scripts/generate_style_cases.py --check  # fail on drift

Two readers read the same EMA divs: the label reader's (label_docx.epi), for the QRD check, and
the authority importer's T (src/authority/t/), which decides what an import keeps. Each checks CSS
against a closed list of its own, and they differ on purpose where their jobs differ (T redraws
the text and must know how it is drawn; the reader only needs the characters a browser shows).
Each case here is a style on an element in a small div, with the reader's answer (the marks it
reads, or its refusal: its rule for the div, label_docx.epi.read_div; the certificate of a
label's read accounts for its characters, not for a case's CSS) and, where T answers otherwise,
T's answer and why the two differ. The TypeScript test (test/authority/style-cases.test.ts)
holds T to the reader's answer on every other case, so a change to either closed list shows as a
failing case until it is recorded here: a change to the reader changes this file
(tests/test_style_cases.py), and a change to T fails that test.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from label_docx.epi import read_div

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "test" / "fixtures" / "authority" / "style-cases.json"

_INLINE = {"span", "b", "strong", "i", "em", "u", "s", "strike", "sup", "sub"}
# Synthetic text, marked as every narrative under test/fixtures is (test/synthetic-only.test.ts).
_TEXT = "Store at 9 C, not for clinical use."
_TEMPLATES = {
    "p": '<p style="{style}">{text}</p>',
    "div": '<div style="{style}">{text}</div>',
    "ul": '<ul style="{style}"><li>{text}</li></ul>',
    "li": '<ul><li style="{style}">{text}</li></ul>',
    "table": '<table style="{style}"><tr><td>{text}</td></tr></table>',
    "tbody": '<table><tbody style="{style}"><tr><td>{text}</td></tr></tbody></table>',
    "tr": '<table><tr style="{style}"><td>{text}</td></tr></table>',
    "td": '<table><tr><td style="{style}">{text}</td></tr></table>',
    # A styled element inside a shifted, a slightly shifted or a raised one.
    "span in shifted span": (
        '<p>Store at <span style="position: relative; top: -3pt">9<span style="{style}">9</span>'
        "</span> C, not for clinical use.</p>"
    ),
    "sup in shifted span": (
        '<p>Store at <span style="position: relative; top: -3pt">9<sup style="{style}">2</sup>'
        "</span> C, not for clinical use.</p>"
    ),
    "sup in slightly shifted span": (
        '<p>Store at <span style="position: relative; top: .5pt">m<sup style="{style}">2</sup>'
        "</span> C, not for clinical use.</p>"
    ),
    "span holding a sup": (
        '<p>Store at m<span style="{style}">x<sup>2</sup></span> C, not for clinical use.</p>'
    ),
    "span in sup": (
        '<p>Store at 9<sup><span style="{style}">2</span></sup> C, not for clinical use.</p>'
    ),
}
# The styled element holds the "9" alone, with text on either side, as a shifted digit stands.
_INLINE_TEMPLATE = '<p>Store at <{name} style="{style}">9</{name}> C, not for clinical use.</p>'

# (element, style). Each property either reader lists, at its edges, and the styles of the pinned
# labels where the two readers were found to differ.
CASES: list[tuple[str, str]] = [
    ("span", ""),
    ("span", "color: red"),
    ("span", "color: #231f20"),
    ("span", "color: white"),
    ("span", "color: #e0e0e0"),
    ("span", "color: #808080"),
    ("span", "background: yellow"),
    ("span", "background: black"),
    ("span", "background: #d9d9d9"),
    ("span", "font-size: 7pt"),
    ("span", "font-size: 1pt"),
    ("span", "font-size: 20pt"),
    ("span", "font-family: 'Times New Roman'"),
    ("span", "font-family: Arial, sans-serif"),
    ("span", "font-family: Verdana"),
    ("span", "font-family: Symbol"),
    ("span", "font-weight: bold"),
    ("span", "font-style: italic"),
    ("span", "text-decoration: underline"),
    ("span", "text-decoration: line-through"),
    ("span", "text-decoration: none"),
    ("span", "vertical-align: super"),
    ("span", "vertical-align: sub"),
    ("span", "vertical-align: top"),
    ("span", "position: relative; top: -5pt"),
    ("span", "position: relative; top: .5pt"),
    ("span", "position: relative; top: 1.5pt"),
    ("span", "position: relative; top: -7pt"),
    ("span", "position: absolute; top: -5pt"),
    ("span", "top: -5pt"),
    ("span", "position: relative; top: -5pt; background: yellow"),
    ("span", "font-size: 7.0pt; color: #231f20; position: relative; top: -5.0pt;"),
    ("span", "color: #231f20; position: relative; top: .5pt;"),
    ("sup", "position: relative; top: -5pt"),
    ("span in shifted span", "position: relative; top: -3pt"),
    ("span in shifted span", "vertical-align: super"),
    ("span in shifted span", ""),
    ("sup in shifted span", ""),
    ("sup in slightly shifted span", ""),
    ("span in sup", "position: relative; top: -3pt"),
    ("span holding a sup", "position: relative; top: .5pt"),
    ("span holding a sup", "position: relative; top: -0.9pt"),
    ("span holding a sup", "position: relative; top: -1.3px"),
    ("span holding a sup", "font-size: 10pt; position: relative; top: -0.8pt"),
    ("span holding a sup", "position: relative; top: -1pt"),
    ("span", "color: black; color: none"),
    ("span", "background-color: black; background-color: auto"),
    ("span", "background: black; background: none"),
    ("span", "tab-stops: 35.4pt"),
    ("span", "mso-bidi-font-size: 11pt"),
    ("span", "layout-grid-mode: line"),
    ("span", "text-autospace: none"),
    ("span", "widows: 2"),
    ("span", "border-bottom: 1px solid"),
    ("span", "border-top: 1px solid"),
    ("span", "margin-left: -2pt"),
    ("span", "padding-top: 2pt"),
    ("span", "line-height: 107%"),
    ("span", "line-height: 11.7pt"),
    ("span", "display: none"),
    ("span", "visibility: hidden"),
    ("span", "visibility: visible"),
    ("span", "letter-spacing: 1pt"),
    ("span", "width: 10pt"),
    ("p", ""),
    ("p", "margin: 0cm"),
    ("p", "margin-left: 36pt; text-indent: -18pt"),
    ("p", "margin-left: -100pt"),
    ("p", "text-align: justify"),
    ("p", "line-height: 107%"),
    ("p", "line-height: 11.7pt"),
    ("p", "line-height: normal; mso-pagination: none; tab-stops: 35.4pt;"),
    ("p", "page-break-after: avoid"),
    ("p", "break-before: page"),
    ("p", "width: 100pt"),
    ("p", "height: 10pt"),
    ("p", "background: #d9d9d9"),
    ("p", "position: relative; top: -5pt"),
    ("p", "border-bottom: 1px solid"),
    ("p", "padding: 5pt"),
    ("p", "font-size: 11pt; font-family: Calibri"),
    ("li", "margin-left: 0pt"),
    ("ul", "margin-top: 0cm"),
    ("table", "border-collapse: collapse"),
    ("table", "width: 100%"),
    ("table", "border: 1px solid"),
    ("tbody", "background: black"),
    ("tr", "height: 10pt"),
    ("tr", "background: yellow"),
    ("td", "width: 50pt"),
    ("td", "border: 1px solid black"),
    ("td", "border-bottom: 1px solid #000000"),
    ("td", "padding: 0cm 5.4pt"),
    ("td", "vertical-align: top"),
    ("td", "background: #d9d9d9"),
    ("td", "height: 20pt"),
]

# Why the two readers answer a case differently.
_CONTRAST = (
    "T refuses text under 4.5:1 contrast with what is painted under it (T3a); the reader reads "
    "it, marked as its colour, or as faint under 1.33:1, and the QRD check reports either"
)
_FONTS = (
    "T takes only the fonts the renderer draws with (T3); the reader takes any Unicode text font "
    "on its list, which draws the same characters"
)
_TINY_FONT = "T refuses text under its smallest font (T3b); the reader marks text under 2pt faint"
_BIG_FONT = (
    "the reader refuses a font above 14pt, a bound in place of a layout; T compares each font "
    "with the line it is on (T3b)"
)
_BOXES = (
    "T takes borders, widths and heights on table parts only (T3); the reader marks a hairline "
    "border on inline text as an underline or a border, and takes a block's border as layout"
)
_STRIKE = (
    "T takes only an underline (T3); the reader marks struck text, which the QRD check reports"
)
_SHIFT = (
    "T refuses a shift it can neither drop (under 0.1 of the font) nor write as sup or sub (from "
    "0.2 of the font, T4); the reader marks any shift of a point or more, up to 6pt, none inside "
    "another"
)
_WORD_ONLY = (
    "a Word property a browser ignores, on the reader's list and not on T's, which refuses any "
    "property it does not name (T3)"
)
_NOT_ON_READERS_LIST = (
    "a property T takes (T3) that is not on the reader's closed list, which refuses any property "
    "it does not name"
)
_VISIBILITY = (
    "T refuses visibility of any value (T3); the reader takes visible, which hides nothing"
)
_BACKGROUND_NONE = (
    "T takes only a colour as a background (T3a); the reader takes the shorthand's none, which "
    "a browser reads as no background (a colour keyword a browser drops refuses in both)"
)
_SLIGHT_SHIFT = (
    "T drops a shift only under 0.1 of the smallest text beneath it (T4: under 0.9pt around a "
    "superscript of 12pt text, less under a smaller font) and refuses one around a superscript "
    "otherwise; the reader, which does not follow font sizes, lets any shift under a point hold "
    "one"
)

# (element, style) -> (T's answer, why): only where T answers otherwise than the reader.
DIVERGENCES: dict[tuple[str, str], tuple[str, str]] = {
    ("span", "color: red"): ("refused:contrast", _CONTRAST),
    ("span", "color: white"): ("refused:contrast", _CONTRAST),
    ("span", "color: #e0e0e0"): ("refused:contrast", _CONTRAST),
    ("span", "color: #808080"): ("refused:contrast", _CONTRAST),
    ("span", "background: black"): ("refused:contrast", _CONTRAST),
    ("tbody", "background: black"): ("refused:contrast", _CONTRAST),
    ("span", "font-size: 1pt"): ("refused:font-size", _TINY_FONT),
    ("span", "font-size: 20pt"): ("accepted", _BIG_FONT),
    ("span", "font-family: Verdana"): ("refused:font", _FONTS),
    ("span", "text-decoration: line-through"): ("refused:css-value", _STRIKE),
    ("span", "position: relative; top: 1.5pt"): ("refused:baseline-shift", _SHIFT),
    ("span", "layout-grid-mode: line"): ("refused:css-property", _WORD_ONLY),
    ("span", "text-autospace: none"): ("accepted", _NOT_ON_READERS_LIST),
    ("span", "widows: 2"): ("accepted", _NOT_ON_READERS_LIST),
    ("span", "border-bottom: 1px solid"): ("refused:css-property", _BOXES),
    ("span", "border-top: 1px solid"): ("refused:css-property", _BOXES),
    ("p", "border-bottom: 1px solid"): ("refused:css-property", _BOXES),
    ("span", "visibility: visible"): ("refused:css-property", _VISIBILITY),
    ("span", "background: black; background: none"): ("refused:css-value", _BACKGROUND_NONE),
    ("span holding a sup", "position: relative; top: -0.9pt"): (
        "refused:baseline-shift",
        _SLIGHT_SHIFT,
    ),
    ("span holding a sup", "position: relative; top: -1.3px"): (
        "refused:baseline-shift",
        _SLIGHT_SHIFT,
    ),
    ("span holding a sup", "font-size: 10pt; position: relative; top: -0.8pt"): (
        "refused:baseline-shift",
        _SLIGHT_SHIFT,
    ),
}


def _div(name: str, style: str) -> str:
    template = _INLINE_TEMPLATE if name in _INLINE else _TEMPLATES[name]
    inner = template.format(name=name, style=style, text=_TEXT)
    return f'<div xmlns="http://www.w3.org/1999/xhtml">{inner}</div>'


def _reader(div: str) -> tuple[str, list[str]]:
    paragraphs, refused, _ = read_div(div)
    if refused is not None:
        return f"refused:{refused.code}", []
    return "read", sorted({mark.kind for paragraph in paragraphs for mark in paragraph.marks})


def render() -> str:
    """The file's content.

    Raises:
        ValueError: A recorded divergence is not one (both readers read the case, or both
            refuse it), or names a case that is not in ``CASES``.
    """
    cases: list[dict[str, object]] = []
    for name, style in CASES:
        div = _div(name, style)
        answer, marks = _reader(div)
        case: dict[str, object] = {"element": name, "style": style, "div": div, "reader": answer}
        if answer == "read":
            case["marks"] = marks
        divergence = DIVERGENCES.get((name, style))
        if divergence is not None:
            t_answer, why = divergence
            if (t_answer == "accepted") == (answer == "read"):
                raise ValueError(f"not a divergence: {name} {style!r}")
            case["t"] = t_answer
            case["why"] = why
        cases.append(case)
    stale = set(DIVERGENCES) - set(CASES)
    if stale:
        raise ValueError(f"divergences for cases not in CASES: {sorted(stale)}")
    return json.dumps(cases, ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    """Write the file, or with --check report whether it is current."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    content = render()
    if parser.parse_args().check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != content:
            sys.stderr.write(f"{TARGET} is out of date; run this script\n")
            return 1
        return 0
    TARGET.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
