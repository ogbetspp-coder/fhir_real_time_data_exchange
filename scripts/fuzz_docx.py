"""Generate .docx documents at random, for Microsoft Word to judge the reader on (macOS, Word).

    uv run --frozen python scripts/fuzz_docx.py --documents 40 --seed 1 FOLDER
    uv run --frozen python scripts/word_oracle.py compare FOLDER/*.docx

The numbering cases (``scripts/numbering_cases.py``) put one question each to Word; these mix
them. Each document combines what the reader computes rather than copies, where its rules are
Word's answers and nothing else: paragraph and character styles in ``basedOn`` chains, a table
style and the document defaults, each switching bold, italic, capitals and strike on or off;
direct formatting over them; lists of one to three definitions with random formats, level
texts, starts, restarts and overrides, shared between lists; and footnotes numbered by the
section's format and start. ``word_oracle.py compare`` then holds the reader to Word on every
document: its list labels, note marks, emphasis and print. A document the reader reads
otherwise than Word is a fault in the reader, made again from its seed and number.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from numbering_cases import Case, abstract, lvl, notes, num, package, para, words

TOGGLES = ("b", "i", "caps", "smallCaps", "strike", "dstrike")
FORMATS = ("decimal", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman", "decimalZero")
NOTE_FORMATS = ("decimal", "lowerRoman", "upperRoman", "lowerLetter", "upperLetter", "chicago")
WORDS = ("Store", "below", "25", "C", "Take", "one", "tablet", "daily", "with", "water")


class Document:
    """One generated document."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.paragraph_styles = [f"P{i}" for i in range(rng.randint(1, 4))]
        self.character_styles = [f"C{i}" for i in range(rng.randint(1, 3))]
        self.notes = 0

    def toggles(self, capitals: bool = True) -> str:
        """A random setting, on or off, of some of the toggles.

        Without capitals in a paragraph style: they reach the list label, which the reader
        refuses in capitals.
        """
        out = ""
        for name in TOGGLES if capitals else ("b", "i", "strike", "dstrike"):  # no caps, smallCaps
            roll = self.rng.random()
            if roll < 0.25:
                out += f"<w:{name}/>"
            elif roll < 0.4:
                out += f'<w:{name} w:val="0"/>'
            elif roll < 0.45:
                out += f'<w:{name} w:val="{self.rng.choice(["true", "1", "on", "false", "off"])}"/>'
        roll = self.rng.random()
        if roll < 0.15:
            align = self.rng.choice(["superscript", "subscript", "baseline"])
            out += f'<w:vertAlign w:val="{align}"/>'
        elif roll < 0.3:
            out += f'<w:u w:val="{self.rng.choice(["single", "double", "none"])}"/>'
        return out

    def styles(self) -> str:
        """Document defaults, paragraph and character styles in chains, and a table style."""
        rng = self.rng
        defaults = f"<w:rPrDefault><w:rPr>{self.toggles(capitals=False)}</w:rPr></w:rPrDefault>"
        out = f"<w:docDefaults>{defaults}</w:docDefaults>"
        kinds = (("paragraph", self.paragraph_styles), ("character", self.character_styles))
        for kind, names in kinds:
            for index, name in enumerate(names):
                based = ""
                default = ' w:default="1"' if rng.random() < 0.2 else ""
                if index and rng.random() < 0.6:
                    based = f'<w:basedOn w:val="{rng.choice(names[:index])}"/>'
                out += (
                    f'<w:style w:type="{kind}"{default} w:styleId="{name}">'
                    f'<w:name w:val="{name}"/>{based}'
                    f"<w:rPr>{self.toggles(capitals=kind == 'character')}</w:rPr></w:style>"
                )
        out += (
            '<w:style w:type="table" w:styleId="T"><w:name w:val="T"/>'
            f"<w:rPr>{self.toggles(capitals=False)}</w:rPr></w:style>"
        )
        return out

    def numbering(self) -> tuple[str, list[int]]:
        """One to three list definitions, and the lists naming them."""
        rng = self.rng
        abstracts = ""
        count = rng.randint(1, 3)
        for key in range(1, count + 1):
            levels = []
            for level in range(3):
                fmt = rng.choice(FORMATS)
                texts = [f"%{level + 1}.", f"({'%' + str(level + 1)})", f"%{level + 1})"]
                if level:
                    texts.append(".".join(f"%{n + 1}" for n in range(level + 1)) + ".")
                extra = ""
                if level and rng.random() < 0.2:
                    # What Word writes: never (0), or after a level above the one directly above.
                    extra = f'<w:lvlRestart w:val="{rng.randint(0, level - 1)}"/>'
                # 0 only in decimal: in letters or roman the reader refuses it.
                start = rng.choice(
                    [None, 0, 1, 1, 1, 2, 5] if fmt.startswith("decimal") else [1, 1, 2, 5]
                )
                levels.append(lvl(level, fmt, rng.choice(texts), extra, start))
            abstracts += abstract(key, *levels)
        nums = ""
        ids = []
        for key in range(1, rng.randint(2, 4) + 1):
            override = ""
            if rng.random() < 0.3:
                level = rng.randint(0, 2)
                value = rng.randint(1, 9)
                override = (
                    f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="{value}"/>'
                    "</w:lvlOverride>"
                )
            nums += num(key, rng.randint(1, count), override)
            ids.append(key)
        return abstracts + nums, ids

    def run(self) -> str:
        """A run: a character style and direct toggles, maybe a footnote reference."""
        rng = self.rng
        style = ""
        if rng.random() < 0.4:
            style = f'<w:rStyle w:val="{rng.choice(self.character_styles)}"/>'
        properties = style + self.toggles()
        text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))
        if rng.random() < 0.08:
            self.notes += 1
            return words(text) + f'<w:r><w:footnoteReference w:id="{self.notes}"/></w:r>'
        prefix = f"<w:rPr>{properties}</w:rPr>" if properties else ""
        return f'<w:r>{prefix}<w:t xml:space="preserve">{text} </w:t></w:r>'

    def level(self) -> int:
        """The next list level: mostly one step from the last, as authors write lists."""
        last = getattr(self, "_last", 0)
        roll = self.rng.random()
        if roll < 0.1:
            nxt = self.rng.randint(0, 2)
        elif roll < 0.45:
            nxt = min(last + 1, 2)
        elif roll < 0.75:
            nxt = last
        else:
            nxt = max(last - 1, 0)
        self._last = nxt
        return nxt

    def paragraph(self, numbers: list[int]) -> str:
        """A paragraph, maybe styled, maybe in a list."""
        rng = self.rng
        props = ""
        if rng.random() < 0.5:
            props += f'<w:pStyle w:val="{rng.choice(self.paragraph_styles)}"/>'
        if rng.random() < 0.6:
            props += (
                f'<w:numPr><w:ilvl w:val="{self.level()}"/>'
                f'<w:numId w:val="{rng.choice(numbers)}"/></w:numPr>'
            )
        return para(*(self.run() for _ in range(rng.randint(1, 3))), props=props)

    def body(self, numbers: list[int]) -> str:
        """Paragraphs, some in one-row tables in the table style."""
        rng = self.rng
        out = ""
        for _ in range(rng.randint(12, 30)):
            if rng.random() < 0.1:
                count = rng.randint(1, 2)
                cells = "".join(f"<w:tc>{self.paragraph(numbers)}</w:tc>" for _ in range(count))
                table = '<w:tblPr><w:tblStyle w:val="T"/></w:tblPr>'
                out += f"<w:tbl>{table}<w:tr>{cells}</w:tr></w:tbl>"
            else:
                out += self.paragraph(numbers)
        return out

    def case(self) -> Case:
        """The document as a numbering case, for ``package``."""
        numbering, numbers = self.numbering()
        styles = self.styles()
        body = self.body(numbers)
        fmt = self.rng.choice(NOTE_FORMATS)
        start = self.rng.randint(1, 4)
        final = (
            f'<w:sectPr><w:footnotePr><w:numFmt w:val="{fmt}"/><w:numStart w:val="{start}"/>'
            "</w:footnotePr></w:sectPr>"
        )
        footnotes = notes("footnote", *range(1, self.notes + 1)) if self.notes else ""
        return Case("generated", numbering, body, styles, footnotes=footnotes, final=final)


def documents(seed: int, count: int) -> list[bytes]:
    """``count`` documents, the same ones for the same seed."""
    rng = random.Random(seed)
    return [package(Document(rng).case()) for _ in range(count)]


def main() -> int:
    """Write the documents into a folder."""
    parser = argparse.ArgumentParser(description="Generate .docx documents for Word to judge.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--documents", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    for index, data in enumerate(documents(args.seed, args.documents)):
        (args.folder / f"generated-{args.seed}-{index:03d}.docx").write_bytes(data)
    sys.stdout.write(f"wrote {args.documents} documents to {args.folder}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
