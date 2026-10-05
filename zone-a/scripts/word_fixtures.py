"""The Word SmPCs of tests/fixtures/word-smpc/: made by Word, and drawn by Chrome.

    uv run --frozen python scripts/word_fixtures.py make     # Word writes each .docx (macOS)
    uv run --frozen python scripts/word_fixtures.py record   # Chrome draws each narrative

``make``: each EMA ePI SmPC pinned in ``labels/ema-epi/sources/`` is written as HTML (ANNEX I,
then each section's title as a paragraph and its narrative, then ANNEX II) and Microsoft Word
opens it and saves it as a .docx: a Word label of public text, as a company's is written in
Word. Pictures and rules (``img``, ``hr``) are left out of the HTML, since Word imports them as
fields and drawings the reader refuses. Word's version is recorded in the folder's README by
hand.

``record``: each fixture is built (zone_a.word_epi, with the assignments in ``ASSIGNED``) and its
narratives drawn by Chrome; what Chrome showed and drew is written to ``chrome.json``, which
tests/test_word_epi.py replays where Chrome is not installed. A change to the builder's output
changes the narratives, and the replay then refuses until this is run again.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from label_docx import browser

from zone_a import word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import read_body
from zone_a.structure import structure

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "labels" / "ema-epi" / "sources"
FIXTURES = ROOT / "zone-a" / "tests" / "fixtures" / "word-smpc"
REGISTRY = ROOT / "qrd" / "registry" / "cap-smpc-en-10.4.json"
MAPPING = ROOT / "fhir" / "mappings" / "cap-smpc-en.json"
CONTAINER = Path.home() / "Library/Containers/com.microsoft.Word/Data"
# Headings a person confirms: Brukinsa words 6.5 and 6.6 otherwise than the template.
ASSIGNED: dict[str, dict[str, int]] = {"brukinsa-smpc-en": {"smpc.6.5": 1091, "smpc.6.6": 1093}}

SAVE_AS = """
on run argv
  set source to (POSIX file (item 1 of argv)) as string
  set target to (POSIX file (item 3 of argv)) as string
  with timeout of 600 seconds
    tell application "Microsoft Word"
      open file name source
      repeat 600 times
        try
          if (name of every document) contains {item 2 of argv} then exit repeat
        end try
        delay 0.1
      end repeat
      save as document (item 2 of argv) file name target file format format document
      close document (item 4 of argv) saving no
    end tell
  end timeout
end run
"""


def _html(bundle: dict[str, Any]) -> str:
    parts = ["<p>ANNEX I</p>"]

    def visit(section: dict[str, Any]) -> None:
        parts.append(f"<p>{html.escape(section.get('title', ''))}</p>")
        div = section.get("text", {}).get("div")
        if div:
            parts.append(re.sub(r"<(img|hr)\b[^>]*/>", "", div))
        for child in section.get("section", []):
            visit(child)

    for section in bundle["entry"][0]["resource"]["section"]:
        visit(section)
    parts.append("<p>ANNEX II</p>")
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>label</title></head><body>'
        + "".join(parts)
        + "</body></html>"
    )


def make() -> None:
    """Word writes each fixture from its pinned ePI."""
    for source in sorted(SOURCES.glob("*-smpc-en.json")):
        with tempfile.TemporaryDirectory(dir=CONTAINER) as folder:
            page = Path(folder) / f"{source.stem}.html"
            saved = Path(folder) / f"{source.stem}.docx"
            page.write_text(_html(json.loads(source.read_text("utf-8"))), "utf-8")
            done = subprocess.run(
                ["osascript", "-", str(page), page.name, str(saved), saved.name],
                input=SAVE_AS,
                capture_output=True,
                text=True,
                check=False,
                timeout=900,
            )
            if done.returncode != 0 or not saved.exists():
                raise SystemExit(f"{source.stem}: Word failed: {done.stderr.strip()}")
            shutil.copyfile(saved, FIXTURES / saved.name)


def narratives(path: Path) -> list[str]:
    """The fixture's carried narratives, in order."""
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    body = read_body(path.read_bytes())
    structured = structure(body.paragraphs, registry, mapping, ASSIGNED.get(path.stem))
    built = word_epi.sections(body, structured, registry)
    return [s["narrative"] for s in built["sections"] if s["refusal"] is None and s["narrative"]]


def digest(divs: list[str]) -> str:
    """SHA-256 of the narratives, in canonical JSON: what a recording was made of."""
    return hashlib.sha256(canonical_json(divs).encode("utf-8")).hexdigest()


def record() -> None:
    """Chrome draws each fixture's narratives; written to chrome.json."""
    chrome = browser.find_chrome()
    if chrome is None:
        raise SystemExit("Chrome is not installed")
    out: dict[str, Any] = {"application": browser.chrome_version(chrome), "documents": {}}
    for path in sorted(FIXTURES.glob("*.docx")):
        divs = narratives(path)
        out["documents"][path.stem] = {
            "narratives": digest(divs),
            "shown": browser.browser_sections(divs, chrome),
            "markers": browser.browser_markers(divs, chrome),
        }
    (FIXTURES / "chrome.json").write_text(canonical_json(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    commands = {"make": make, "record": record}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(__doc__)
    commands[sys.argv[1]]()
