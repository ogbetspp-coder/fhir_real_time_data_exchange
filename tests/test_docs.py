"""The documentation in the code points at things that exist."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = r"[a-z]{2}[a-z0-9]*(?:-[a-z0-9]+)+"
# Case names in brackets, as in "[fields-seq, fields-seq-switches]", possibly over comment lines.
# A regex class ("[0-9a-f]") or a type-ignore code ("ignore[arg-type]") is not one.
CITATION = re.compile(rf"(?<!ignore)\[({NAME}(?:,\s*(?:#\s*)?{NAME})*)\]")


def test_every_case_the_code_cites_is_in_the_corpus() -> None:
    cases = {path.stem for path in (ROOT / "corpus").glob("*/*.docx")}
    cited = {
        name
        for path in (ROOT / "src").rglob("*.py")
        for group in CITATION.findall(path.read_text())
        for name in re.split(r",\s*(?:#\s*)?", group)
    }
    assert len(cited) > 30
    missing = sorted(cited - cases)
    assert not missing, f"cited cases with no corpus file: {missing}"
