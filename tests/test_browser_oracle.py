"""The ePI reader shows what a browser shows, held to answers Chrome gave (browser.json).

``scripts/browser_oracle.py record`` asked headless Chrome, for every section of every ePI in a
corpus set, for the text it shows and what it computed for each text node; these tests hold the
reader to those answers without a browser, by the SHA-256 of each section's lines and of their
marks. A section the reader refuses is compared with nothing: refusing is always allowed.
"""

from __future__ import annotations

import json
from pathlib import Path

from browser_oracle import sections
from label_docx.browser import digest, markers_digest, reader_lines, reader_markers

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
_MANIFESTS = {"sources", "expected", "word", "browser"}


def _sets() -> list[Path]:
    return sorted(path.parent for path in CORPUS.glob("*/browser.json"))


def test_every_epi_in_the_corpus_has_the_browsers_answers() -> None:
    assert _sets()
    for folder in _sets():
        recorded = json.loads((folder / "browser.json").read_text("utf-8"))["sections"]
        present = {p.name for p in folder.glob("*.json") if p.stem not in _MANIFESTS}
        assert set(recorded) == present, folder.name


def test_every_section_the_reader_reads_is_what_the_browser_shows() -> None:
    read = 0
    for folder in _sets():
        recorded = json.loads((folder / "browser.json").read_text("utf-8"))["sections"]
        for name, answers in recorded.items():
            mine = sections(folder / name)
            assert len(mine) == len(answers), name
            for index, (section, answer) in enumerate(zip(mine, answers, strict=True)):
                if section.refusal is not None:
                    continue
                # The browser placed every piece of text the reader reads.
                assert answer is not None, f"{name} section {index + 1}"
                read_now = {
                    **digest(reader_lines(section.paragraphs)),
                    "markers": markers_digest(reader_markers(section.paragraphs)),
                }
                assert read_now == answer, (
                    f"{name} section {index + 1}: compare with scripts/browser_oracle.py"
                )
                read += 1
    # The corpus is more than a few sections: the agreement is broad, not incidental.
    assert read > 3000
