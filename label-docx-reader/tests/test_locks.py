"""What the reader produces is tied to the code that produces it (scripts/lock.py)."""

from __future__ import annotations

import json

from lock import CORPUS, VERSIONS, current_versions, expected


def test_the_current_versions_are_locked_to_the_current_code() -> None:
    lock = json.loads(VERSIONS.read_text("utf-8"))
    for name, (version, digest) in current_versions().items():
        locked = lock.get(name, {}).get(version)
        assert locked is not None, f"{name} {version} is not locked: run scripts/lock.py"
        assert locked == digest, f"{name} changed under {version}: bump it, run scripts/lock.py"


def test_every_corpus_document_reads_as_locked() -> None:
    folders = sorted(path for path in CORPUS.iterdir() if path.is_dir())
    assert folders
    for folder in folders:
        locked = json.loads((folder / "expected.json").read_text("utf-8"))
        assert locked == expected(folder), f"{folder.name}: review, then run scripts/lock.py"
