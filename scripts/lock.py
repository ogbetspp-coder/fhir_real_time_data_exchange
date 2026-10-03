"""Write, or check, the two locks that tie what the reader produces to the code producing it.

    uv run --frozen python scripts/lock.py            # lock the current versions and corpus
    uv run --frozen python scripts/lock.py --check    # fail if either lock is out of date

``versions.lock.json`` records, for every version of the reader and the output format, the
SHA-256 of the source file that decides it. A change to the file leaves the current version
locked to other code; ``tests/test_locks.py`` refuses that until the version is bumped. A version
already in the lock is never re-locked to other code: bump it instead.

``corpus/*/expected.json`` records, for every .docx and ePI in the corpus, its SHA-256 and the
SHA-256 of the whole result served for it (``resultSha256``: everything but the source and the
version names), so a change to anything served fails the tests until this script is run and the
change reviewed in the diff. Beside it, to show where a change is: the body paragraphs' SHA-256
(or the refusal code; or, with tracked changes, the views' and changes'), for an ePI its
sections' SHA-256 and how many sections were refused, and the certificate's SHA-256.

``tests/data/generated-outcomes.json`` records the outcome of every generated case the tests run
(``tests/test_generated_docx.py``, ``tests/test_generated_epi.py``): read, or the refusal code,
case by case, so a reader that refuses more fails them too.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import fuzz_docx
import fuzz_epi
from label_docx import certify, documents, epi, epi_output, output, reader
from label_docx.epi import EpiRefusedError, read_epi
from label_docx.output import FORMAT_VERSION, canonical
from label_docx.reader import READER_VERSION, read_docx

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "versions.lock.json"
CORPUS = ROOT / "corpus"
GENERATED = ROOT / "tests" / "data" / "generated-outcomes.json"
EPI_RECORD = ROOT / "tests" / "data" / "generated-epi.json"

type Lock = dict[str, dict[str, str]]


def _sha256(*paths: Path) -> str:
    return hashlib.sha256(b"".join(path.read_bytes() for path in paths)).hexdigest()


def current_versions() -> dict[str, tuple[str, str]]:
    """Each component's current version and the hash of the file that decides it."""
    return {
        "reader": (READER_VERSION, _sha256(Path(reader.__file__))),
        # From label-docx-json/1.4.0 the format is also decided by the check that certifies it.
        "format": (FORMAT_VERSION, _sha256(Path(output.__file__), Path(str(certify.__file__)))),
        "epi-reader": (epi.READER_VERSION, _sha256(Path(epi.__file__))),
        # The ePI format is written by epi_output with output's paragraphs, for the documents
        # documents.kind sends it.
        "epi-format": (
            epi_output.FORMAT_VERSION,
            _sha256(*(Path(str(m.__file__)) for m in (epi_output, output, documents, certify))),
        ),
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


# A corpus set's own records, beside its documents.
MANIFESTS = {"sources", "expected", "word", "browser"}


def _certificate(result: bytes) -> dict[str, str]:
    """The SHA-256 of a result's certificate (canonical JSON), where it was certified."""
    certificate = json.loads(result).get("certificate")
    if certificate is None:
        return {}
    return {"certificateSha256": hashlib.sha256(canonical(certificate)).hexdigest()}


def _result(result: bytes) -> dict[str, str]:
    """The SHA-256 of a served result, without its source and versions."""
    value = json.loads(result)
    for key in ("source", "reader", "format"):
        value.pop(key)
    return {"resultSha256": hashlib.sha256(canonical(value)).hexdigest()}


def expected(folder: Path) -> dict[str, dict[str, str]]:
    """What the readers produce for every .docx and every ePI (.json) in ``folder``."""
    out: dict[str, dict[str, str]] = {}
    # Hidden files (a recording's progress) are not documents.
    for path in sorted(folder.glob("[!.]*.json")):
        if path.stem in MANIFESTS:
            continue
        data = path.read_bytes()
        entry = {"sha256": hashlib.sha256(data).hexdigest()}
        entry |= _result(epi_output.read(data)[0])
        try:
            document = read_epi(data)
            body = canonical([epi_output.section(s) for s in document.sections])
            entry["sectionsSha256"] = hashlib.sha256(body).hexdigest()
            refused = sum(1 for s in epi.walk(document.sections) if s.refusal)
            entry["refusedSections"] = str(refused)
            entry |= _certificate(epi_output.read(data)[0])
        except EpiRefusedError as refused_document:
            entry["refusal"] = refused_document.code
        out[path.name] = entry
    for path in sorted(folder.glob("*.docx")):
        data = path.read_bytes()
        entry = {"sha256": hashlib.sha256(data).hexdigest()}
        result = output.read(data)[0]
        entry |= _result(result)
        value = json.loads(result)
        if "refusal" in value:
            # The refusal served: a tracked document's may come from one of its views.
            entry["refusal"] = value["refusal"]["code"]
        elif "tracked" in value:
            # Two texts: both views and the changes, held whole.
            entry["trackedSha256"] = hashlib.sha256(canonical(value["tracked"])).hexdigest()
            entry |= _certificate(result)
        else:
            body = canonical(output.paragraphs(read_docx(data)))
            entry["paragraphsSha256"] = hashlib.sha256(body).hexdigest()
            entry |= _certificate(result)
        out[path.name] = entry
    return out


def outcomes_sha256(outcomes: list[str]) -> str:
    """The SHA-256 of generated cases' outcomes, each with its index."""
    listed = "".join(f"{index} {outcome}\n" for index, outcome in enumerate(outcomes))
    return hashlib.sha256(listed.encode()).hexdigest()


def docx_outcome(result: bytes) -> str:
    """A generated .docx's outcome: ``certified`` or its refusal code."""
    value = json.loads(result)
    return value["refusal"]["code"] if "refusal" in value else "certified"


def epi_outcome(div: str) -> str:
    """A generated ePI section's outcome: ``read`` or its refusal code."""
    refusal = epi.read_div(div)[1]
    return "read" if refusal is None else refusal.code


def generated() -> dict[str, str]:
    """The digests of the generated cases' outcomes, as the tests make the cases."""
    record = json.loads(EPI_RECORD.read_text("utf-8"))
    seed = int(record["generator"].rsplit(" ", 1)[1])
    docx = [
        docx_outcome(output.read(data)[0])
        for seed_docx in (101, 102)
        for data in fuzz_docx.documents(seed_docx, 300)
    ]
    sections = [epi_outcome(div) for div in fuzz_epi.cases(seed, record["cases"])]
    return {"docx": outcomes_sha256(docx), "epi": outcomes_sha256(sections)}


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
    wanted[GENERATED] = _json(generated())
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
