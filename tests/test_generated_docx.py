"""Generated Word documents: every one the reader reads, the independent check certifies.

``scripts/fuzz_docx.py`` builds documents at random from what the reader computes rather than
copies (style chains and toggles, lists, notes). For each one read, the check must account for
every character and work out the same bold, italic, capitals, strike, superscript, subscript
and underline on its own (``label_docx.certify``), else the reader and the check differ and the
read is refused as ``uncertified``. Word's own judgment of them is ``word_oracle.py compare``.
"""

from __future__ import annotations

import json

from fuzz_docx import documents
from label_docx import output
from lock import GENERATED, docx_outcome, outcomes_sha256


def test_every_generated_document_read_is_certified() -> None:
    outcomes = []
    for seed in (101, 102):
        for data in documents(seed, 300):
            outcomes.append(docx_outcome(output.read(data)[0]))
            assert outcomes[-1] != "uncertified", f"seed {seed}, case {len(outcomes) - 1}"
    assert outcomes.count("certified") > 300
    # Each case's outcome as recorded: a reader that refuses more is a change to review.
    recorded = json.loads(GENERATED.read_text("utf-8"))["docx"]
    assert outcomes_sha256(outcomes) == recorded, "outcomes changed: review, run scripts/lock.py"
