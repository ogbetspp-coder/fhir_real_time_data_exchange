"""How many whole-cell quotes the quote-edge rule's table clause refuses on the pinned SmPCs.

The figure the query design and UR-22 state ("196 of 789") comes from here. For every table in
the three pinned EMA SmPCs (``labels/ema-epi/sources``), every cell's text is quoted against the
table's text twice: once with the whole grid (the rule reads every cell beside it) and once alone
in a one-cell table. A quote that matches alone and not in its table is refused by the table
clause only.

The grid is rebuilt approximately: cells are read with ``html.parser``, a block or line break
inside a cell becomes a space, whitespace is collapsed, and slots are laid out by ``colspan`` and
``rowspan`` in document order. It is not the fidelity scanner (the EMA's divs carry styles the
scanner refuses), so the figure is an estimate of the rule's cost, not a verdict on any label.

Run from ``agent/``: ``uv run python scripts/measure_table_quotes.py``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from verifiable_answer_agent.quote_edge import is_gap, locate_quote

ROOT = Path(__file__).resolve().parents[2]
LABELS = ("brukinsa", "jentadueto", "nuvaxovid")

TABLE, END, ROW, CELL, LEFT, ABOVE = map(chr, range(0xFDD0, 0xFDD6))

# A cell: its text, colspan and rowspan.
Cell = list[Any]


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[Cell]]] = []
        self.cell: Cell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables and self.tables[-1]:
            self.cell = ["", int(values.get("colspan") or 1), int(values.get("rowspan") or 1)]
            self.tables[-1][-1].append(self.cell)
        elif tag in ("br", "p", "div", "li") and self.cell is not None:
            self.cell[0] += " "

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell[0] += data


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("­", "").replace("​", ""))
    return re.sub(r"\s+", " ", text).strip()


def _grid_text(rows: list[list[Cell]]) -> str:
    covered: set[tuple[int, int]] = set()
    out = TABLE
    for row_number, row in enumerate(rows):
        out += " " + ROW
        column = 0
        cells = list(row)
        while cells or (row_number, column) in covered:
            if (row_number, column) in covered:
                out += " " + ABOVE
                column += 1
                continue
            text, columns, spanned = cells.pop(0)
            for down in range(spanned):
                for across in range(columns):
                    if down or across:
                        covered.add((row_number + down, column + across))
            out += f" {CELL} {_normalise(text)}" + f" {LEFT}" * (columns - 1)
            column += columns
    return re.sub(" +", " ", out + " " + END)


def _sections(sections: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for section in sections:
        yield section
        yield from _sections(section.get("section", []))


def main() -> None:
    total = refused = refused_words = 0
    for name in LABELS:
        path = ROOT / "labels" / "ema-epi" / "sources" / f"{name}-smpc-en.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        composition = bundle["entry"][0]["resource"]
        for section in _sections(composition.get("section", [])):
            parser = _Tables()
            parser.feed(section.get("text", {}).get("div", ""))
            for table in parser.tables:
                if not any(table):
                    continue
                text = _grid_text(table)
                for cell in (cell for row in table for cell in row):
                    quote = _normalise(cell[0])
                    if not any(not is_gap(character) for character in quote):
                        continue
                    total += 1
                    alone = f"{TABLE} {ROW} {CELL} {quote} {END}"
                    if locate_quote(text, quote) is None and locate_quote(alone, quote) is not None:
                        refused += 1
                        if unicodedata.category(quote[0])[0] == "L":
                            refused_words += 1
    print(
        f"{refused} of {total} whole-cell quotes are refused by the table clause alone, "
        f"{refused_words} of them beginning with a letter"
    )


if __name__ == "__main__":
    main()
