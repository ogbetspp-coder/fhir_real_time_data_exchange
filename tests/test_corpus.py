"""The corpus holds exactly the files its sources record, byte for byte."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from numbering_cases import wanted

CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def test_the_numbering_cases_are_what_their_script_writes() -> None:
    # Byte for byte: the files are stored, with fixed timestamps (scripts/numbering_cases.py).
    for path, data in wanted().items():
        assert path.read_bytes() == data, f"{path.name}: run scripts/numbering_cases.py"


def test_every_corpus_file_is_the_recorded_byte_copy() -> None:
    for sources in sorted(CORPUS.glob("*/sources.json")):
        recorded = {s["file"]: s for s in json.loads(sources.read_text("utf-8"))["sources"]}
        present = {path.name for path in sources.parent.glob("*.docx")}
        assert present == set(recorded), sources.parent.name
        for name, source in recorded.items():
            data = (sources.parent / name).read_bytes()
            assert len(data) == source["bytes"], name
            assert hashlib.sha256(data).hexdigest() == source["sha256"], name
