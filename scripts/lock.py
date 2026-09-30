"""Write, or check, the two locks that tie what the reader produces to the code producing it.

    uv run --frozen python scripts/lock.py            # lock the current versions and corpus
    uv run --frozen python scripts/lock.py --check    # fail if either lock is out of date

``versions.lock.json`` records, for every version of the reader and the output format, the
SHA-256 of the source file that decides it. A change to the file leaves the current version
locked to other code; ``tests/test_locks.py`` refuses that until the version is bumped. A version
already in the lock is never re-locked to other code: bump it instead.

``corpus/*/expected.json`` records, for every .docx in the corpus, its SHA-256 and the SHA-256 of
its canonical paragraphs (or its refusal code). A change to what the reader produces for any of
them fails the tests until this script is run and the change reviewed in the diff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from label_docx import output, reader
from label_docx.output import FORMAT_VERSION, canonical
from label_docx.reader import READER_VERSION, DocxRefusedError, read_docx

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "versions.lock.json"
CORPUS = ROOT / "corpus"

type Lock = dict[str, dict[str, str]]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_versions() -> dict[str, tuple[str, str]]:
    """Each component's current version and the hash of the file that decides it."""
    return {
        "reader": (READER_VERSION, _sha256(Path(reader.__file__))),
        "format": (FORMAT_VERSION, _sha256(Path(output.__file__))),
    }


def locked_versions(lock: Lock) -> Lock:
    """``lock`` with the current versions added; SystemExit if one is locked to other code."""
    out = {name: dict(entries) for name, entries in lock.items()}
    for name, (version, digest) in current_versions().items():
        entries = out.setdefault(name, {})
        if entries.get(version, digest) != digest:
            raise SystemExit(f"{name} {version} is locked to other code: bump the version")
        entries[version] = digest
    return out


def expected(folder: Path) -> dict[str, dict[str, str]]:
    """What the reader produces for every .docx in ``folder``."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(folder.glob("*.docx")):
        data = path.read_bytes()
        entry = {"sha256": hashlib.sha256(data).hexdigest()}
        try:
            body = canonical(output.paragraphs(read_docx(data)))
            entry["paragraphsSha256"] = hashlib.sha256(body).hexdigest()
        except DocxRefusedError as refused:
            entry["refusal"] = refused.code
        out[path.name] = entry
    return out


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    """Write the locks, or with --check report whether they are current."""
    parser = argparse.ArgumentParser(description="Write or check the version and corpus locks.")
    parser.add_argument("--check", action="store_true", help="fail rather than write")
    args = parser.parse_args()
    lock = json.loads(VERSIONS.read_text("utf-8")) if VERSIONS.exists() else {}
    wanted = {VERSIONS: _json(locked_versions(lock))}
    for folder in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        wanted[folder / "expected.json"] = _json(expected(folder))
    stale = [p for p, text in wanted.items() if not p.exists() or p.read_text("utf-8") != text]
    if args.check:
        for path in stale:
            sys.stderr.write(f"out of date: {path.relative_to(ROOT)}\n")
        return 1 if stale else 0
    for path in stale:
        path.write_text(wanted[path], "utf-8")
        sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
