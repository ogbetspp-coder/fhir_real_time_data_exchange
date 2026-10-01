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


def test_every_generated_document_read_is_certified() -> None:
    outcomes: dict[str, int] = {}
    for seed in (101, 102):
        for data in documents(seed, 300):
            value = json.loads(output.read(data)[0])
            outcome = value["refusal"]["code"] if "refusal" in value else "certified"
            assert outcome != "uncertified", value["refusal"]["detail"]
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
    assert outcomes["certified"] > 300
