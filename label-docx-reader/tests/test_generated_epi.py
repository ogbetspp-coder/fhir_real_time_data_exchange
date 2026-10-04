"""The ePI reader against Chrome on 3,000 generated sections, without a browser.

``scripts/fuzz_epi.py`` builds sections at random from everything the reader accepts and
``--record`` kept Chrome's answers for one seed (``tests/data/generated-epi.json``): for each
section, digests of the lines and marks it shows and of its list markers. The cases are made
again from the seed, and must be the ones recorded; every section the reader reads must be what
Chrome showed. Runs of other seeds (``fuzz_epi.py --seed N``) explore further, with Chrome.
"""

from __future__ import annotations

import json
from pathlib import Path

from fuzz_epi import cases, cases_sha256
from label_docx import epi
from label_docx.browser import digest, markers_digest, reader_lines, reader_markers
from lock import GENERATED, epi_outcome, outcomes_sha256

RECORD = Path(__file__).resolve().parent / "data" / "generated-epi.json"


def test_every_generated_section_the_reader_reads_is_what_chrome_shows() -> None:
    recorded = json.loads(RECORD.read_text("utf-8"))
    seed = int(recorded["generator"].rsplit(" ", 1)[1])
    divs = cases(seed, recorded["cases"])
    assert cases_sha256(divs) == recorded["casesSha256"], "the generator changed: record again"
    read = 0
    for index, (div, answer) in enumerate(zip(divs, recorded["answers"], strict=True)):
        paragraphs, refusal, _ = epi.read_div(div)
        if refusal is not None:
            continue
        assert answer is not None, f"case {index}"
        mine = {
            **digest(reader_lines(paragraphs)),
            "markers": markers_digest(reader_markers(paragraphs)),
        }
        assert mine == answer, f"case {index}: run scripts/fuzz_epi.py --seed {seed} to see it"
        read += 1
    assert read > 1000
    # Each case's outcome as recorded: a reader that refuses more is a change to review.
    outcomes = [epi_outcome(div) for div in divs]
    recorded = json.loads(GENERATED.read_text("utf-8"))["epi"]
    assert outcomes_sha256(outcomes) == recorded, "outcomes changed: review, run scripts/lock.py"
