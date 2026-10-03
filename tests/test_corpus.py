"""The corpus holds exactly the files its sources record, byte for byte."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lock import MANIFESTS
from numbering_cases import wanted
from tracked_cases import wanted as tracked_wanted

CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def test_the_numbering_cases_are_what_their_script_writes() -> None:
    # Byte for byte: the files are stored, with fixed timestamps (scripts/numbering_cases.py).
    for path, data in wanted().items():
        assert path.read_bytes() == data, f"{path.name}: run scripts/numbering_cases.py"


def test_the_tracked_cases_are_what_their_script_writes() -> None:
    for path, data in tracked_wanted().items():
        assert path.read_bytes() == data, f"{path.name}: run scripts/tracked_cases.py"


def test_every_corpus_file_is_the_recorded_byte_copy() -> None:
    # Word's own files for a set (its views of tracked changes) are recorded beside them.
    for sources in sorted([*CORPUS.glob("*/sources.json"), *CORPUS.glob("*/word/sources.json")]):
        recorded = {s["file"]: s for s in json.loads(sources.read_text("utf-8"))["sources"]}
        present = {path.name for path in sources.parent.glob("*.docx")} | {
            path.name for path in sources.parent.glob("[!.]*.json") if path.stem not in MANIFESTS
        }
        assert present == set(recorded), sources.parent.name
        for name, source in recorded.items():
            data = (sources.parent / name).read_bytes()
            assert len(data) == source["bytes"], name
            assert hashlib.sha256(data).hexdigest() == source["sha256"], name
