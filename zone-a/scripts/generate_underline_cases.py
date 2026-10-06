"""Write, or check, the underline cases the TypeScript port is held to.

    uv run --frozen python scripts/generate_underline_cases.py          # write
    uv run --frozen python scripts/generate_underline_cases.py --check  # fail on drift

Each case is a text, a code point range, the options, and zone_a.underline's answer; the
authority importer's port (src/authority/underline.ts) must give the same answer for each
(test/authority/underline.test.ts). The texts are the reference tests' and every underlined run
of the pinned Imatinib Teva tablets SmPC, with its neighbours.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path

from zone_a.underline import underline_changes

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "test" / "fixtures" / "authority" / "underline-cases.json"
LABEL = ROOT / "labels" / "ema-epi" / "sources" / "imatinib-teva-tablets-smpc-en.json"

# (text, start, end, options): the reference tests' cases and more at each rule's edges.
_FIXED: list[tuple[str, int, int, dict[str, object]]] = [
    *[
        (text, 0, len(text), {})
        for text in [
            "see section 4.4",
            "Strong CYP3A inhibitors",
            "CYP2C19",
            "bg.ireland@beigene.com",
            "\u201cPregnancy\u201d",
            "<",
            ">",
            "+",
            "=",
            "-",
            "~",
            "\u2013",
            "\u2212",
            "\u02c2",
            "\u1438",
            "\u2265",
            "Ph+ ALL",
            "\u00aa",
            "\u00ba",
            "\u1d43",
            "10\u00a0mg",
            "film\u2011coated",
            "",
        ]
    ],
    ("1a", 1, 2, {}),
    ("40o", 2, 3, {}),
    ("3A", 1, 2, {}),
    ("1 a", 2, 3, {}),
    ("10 mg", 3, 5, {}),
    ("1ab", 1, 3, {}),
    ("Breast-feeding", 0, 14, {}),
    ("Breast-feeding", 0, 14, {"hyphensInWords": True}),
    ("film\u2011coated", 0, 11, {"hyphensInWords": True}),
    ("2-3", 0, 3, {"hyphensInWords": True}),
    ("2-3", 1, 2, {"hyphensInWords": True}),
    ("-a", 0, 2, {"hyphensInWords": True}),
    ("a-", 0, 2, {"hyphensInWords": True}),
    # A hyphen at the line's edge, or any other dash, is not inside an underlined word.
    ("CL-CR", 2, 3, {"hyphensInWords": True}),
    ("CL-CR", 0, 3, {"hyphensInWords": True}),
    ("CL-CR", 2, 5, {"hyphensInWords": True}),
    ("CL-CR", 1, 4, {"hyphensInWords": True}),
    ("a\u2e40b", 0, 3, {"hyphensInWords": True}),
    ("a\u30a0b", 0, 3, {"hyphensInWords": True}),
    ("a\u2013b", 0, 3, {"hyphensInWords": True}),
    ("Long\u2010term", 0, 9, {"hyphensInWords": True}),
    ("<Traceability>", 0, 14, {"also": ["<", ">"]}),
    ("1\u0430", 1, 2, {}),
    ("20\u043eC", 2, 3, {}),
    ("20\u03bfC", 2, 3, {}),
    ("1\u2063a", 2, 3, {}),
    ("1\u200ba", 2, 3, {}),
    ("No 5", 1, 2, {}),
    ("No dose adjustment", 0, 18, {}),
    ("1\u0251", 1, 2, {}),
    ("1\u1d0f", 1, 2, {}),
    ("20\u1d0fC", 2, 3, {}),
    ("1\u03b1", 1, 2, {}),
    ("\uff11a", 1, 2, {}),
    ("\U0001d7d9a", 1, 2, {}),
    ("\u0663o", 1, 2, {}),
    ("no 5", 1, 2, {}),
    ("\u039do 5", 1, 2, {}),
    ("\uff2eo 5", 1, 2, {}),
    ("N\u043e 5", 1, 2, {}),
    ("N\u03bf 5", 1, 2, {}),
    ("N\u1d0f 5", 1, 2, {}),
    ("x\U0001d4c9y", 0, 3, {}),
]


def _label_cases() -> list[tuple[str, int, int, dict[str, object]]]:
    """Each underlined run of the pinned label, with up to 8 code points either side."""
    document = json.loads(LABEL.read_text(encoding="utf-8"))
    cases: list[tuple[str, int, int, dict[str, object]]] = []

    def walk(sections: list[dict[str, object]]) -> None:
        for section in sections:
            text_element = section.get("text")
            div = str(text_element.get("div", "")) if isinstance(text_element, dict) else ""
            for match in re.finditer(r"<u\b[^>]*>(.*?)</u>", div, re.S):
                before = unescape(re.sub(r"<[^>]+>", "", div[: match.start()]))[-8:]
                inner = unescape(re.sub(r"<[^>]+>", "", match.group(1)))
                after = unescape(re.sub(r"<[^>]+>", "", div[match.end() :]))[:8]
                text = before + inner + after
                span = (len(before), len(before) + len(inner))
                cases.append((text, *span, {"hyphensInWords": True}))
            walk(section.get("section", []))  # type: ignore[arg-type]

    walk(document["entry"][0]["resource"]["section"])
    return cases


def render() -> str:
    """The file's content."""
    cases = []
    for text, start, end, options in [*_FIXED, *_label_cases()]:
        also_option = options.get("also", [])
        also = frozenset(also_option) if isinstance(also_option, list) else frozenset()
        hyphens = bool(options.get("hyphensInWords", False))
        changes = underline_changes(text, start, end, also=also, hyphens_in_words=hyphens)
        cases.append({"text": text, "start": start, "end": end, **options, "changes": changes})
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
