"""A write-once, content-addressed store of documents and what the reader made of them.

A document is named by the SHA-256 of its bytes. Its bytes are kept once, and the reader's
result (canonical JSON, read or refused) is kept once per reader and format version, beside the
results of every earlier version, which are never touched. The reader is the .docx reader, or
the ePI reader for JSON (``documents.kind``). Ingesting the same bytes again finds
what is there and returns it byte for byte; nothing is computed twice under the same versions,
and nothing stored is ever replaced.

    <root>/documents/<id[:2]>/<id>/source
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/result.json      (docx-reader@1.20.0, ...)
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/receipt.json     (the result's SHA-256)
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/browser/<version>@<verifier>.json  (an ePI's)
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/word/<version>@<verifier>.json     (a .docx's)

Every write creates a file that did not exist (a hard link from a written, synced temporary
file), so two ingestions of the same document at once cannot interleave. Every write and every
read of a file that is already there compares the bytes; a difference means the store or the
reader has changed under the same name, and raises ``StoreError`` rather than serve either. A
result is served only if it hashes to the SHA-256 its receipt records, and ``verify`` reads every
kept source again and requires the kept result and receipt byte for byte, which no edit to the
store, however consistent, can pass.

An ePI read is also held to a browser where one is given (``browser``), and a .docx read to Word
where it is given (``word``): a ``Checker`` names the application's version and gives its verdict.
The verdict is kept beside the result once per application version and verifier version (the
code that asks and judges, ``word.VERIFIER``), which it records, before the receipt is written,
so a result is never served while its check is still running; a pair already kept is not asked
again. An application that answers otherwise when asked again at once is kept too, beside the
first, under its digest. ``disagreement`` names a kept verdict that found the document shown
otherwise than the result reads, or two kept verdicts of one pair that differ; the service does
not serve that result.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from label_docx.documents import EPI, Kind, kind
from label_docx.output import Json, canonical

_ID = re.compile(r"[0-9a-f]{64}")


class StoreError(Exception):
    """The store holds bytes other than the ones its names promise."""


@dataclass(frozen=True)
class Ingested:
    """The outcome of ingesting one document."""

    document: str
    receipt: bytes
    # The label's text: never in a repr, which a failing test or a traceback would print.
    result: bytes = field(repr=False)
    read: bool
    created: bool
    # The application's verdict on the read (canonical JSON), or None where none was asked.
    verification: bytes | None = None


# Asked about a document and its result (JSON), it answers its verdict: at least the
# application and what it shows otherwise (``differs``, empty where it agrees).
type Verifier = Callable[[bytes, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Checker:
    """An application a read is held to: its name and version now, and its verdict on a read."""

    application: Callable[[], str]
    verify: Verifier
    # The version of the code that asks the application and judges its answers.
    verifier: str = ""


def _verdict_name(application: str, verifier: str | None) -> str:
    """The file a verdict is kept in; a verdict kept before verifiers were versioned has none."""

    def safe(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9.]+", "-", name).strip("-")

    return safe(application) + ("" if verifier is None else "@" + safe(verifier)) + ".json"


def _second_name(first: str, verdict: bytes) -> str:
    """The file of a verdict that differs from the one kept first under ``first``."""
    return first.removesuffix(".json") + "+" + hashlib.sha256(verdict).hexdigest()[:16] + ".json"


def _fsync(descriptor: int) -> None:
    """Flush to the disk itself: on macOS, fsync reaches only the drive's cache."""
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(descriptor, fcntl.F_FULLFSYNC)
    else:  # pragma: no cover - not macOS
        os.fsync(descriptor)


def _durable_folder(folder: Path) -> None:
    """Create ``folder`` and any missing parents, each made durable in its own parent."""
    if folder.is_dir():
        return
    _durable_folder(folder.parent)
    try:
        folder.mkdir()
    except FileExistsError:
        return  # another ingestion made it
    _sync_folder(folder.parent)


def _sync_folder(folder: Path) -> None:
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        _fsync(descriptor)
    finally:
        os.close(descriptor)


def _version(version: str) -> str:
    return version.replace("/", "@")


def _write_once(path: Path, data: bytes) -> bool:
    """Create ``path`` holding ``data``; if it exists, it must hold ``data``. True if created."""
    _durable_folder(path.parent)
    if path.exists():
        _same(path, data)
        return False
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".incoming-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            _fsync(handle.fileno())
        try:
            os.link(temporary, path)  # creates path, or fails if it exists
        except FileExistsError:
            # Another ingestion of the same document got there first; it must agree.
            _same(path, data)
            return False
        # The new name is durable only once its directory is.
        _sync_folder(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def _same(path: Path, data: bytes) -> None:
    if path.read_bytes() != data:
        raise StoreError(f"{path} holds other bytes than the ones written under its name")


class Store:
    """Documents and results under ``root``."""

    def __init__(
        self, root: Path, browser: Checker | None = None, word: Checker | None = None
    ) -> None:
        self.root = root
        self.browser = browser
        self.word = word

    def _folder(self, document: str) -> Path:
        if not _ID.fullmatch(document):
            raise KeyError(document)
        return self.root / "documents" / document[:2] / document

    def _result_path(self, document: str, reading: Kind) -> Path:
        reader, fmt = _version(reading.reader), _version(reading.format)
        return self._folder(document) / reader / fmt / "result.json"

    def ingest(self, data: bytes) -> Ingested:
        """Keep ``data`` and the reader's result for it, or find them kept already."""
        document = hashlib.sha256(data).hexdigest()
        _write_once(self._folder(document) / "source", data)
        reading = kind(data)
        path = self._result_path(document, reading)
        if path.exists() and path.with_name("receipt.json").exists():
            result = self._checked(document, path, reading)
        elif path.exists():
            # Kept without its receipt (the store stopped between the two): read the source
            # again, and receipt the kept result only if it is what the reader makes of it.
            result, _ = reading.read(data)
            _same(path, result)
        else:
            result, _ = reading.read(data)
            _write_once(path, result)
        value = json.loads(result)
        # The verdict first: a result is served only with its receipt, so never while its
        # application is still being asked.
        verification = self._verify(data, value, reading, path.parent)
        receipt = _receipt(document, result, value, reading)
        # Created means this ingestion wrote the receipt: the first to answer.
        created = _write_once(path.with_name("receipt.json"), receipt)
        return Ingested(document, receipt, result, "refusal" not in value, created, verification)

    def _verify(
        self, data: bytes, value: dict[str, Any], reading: Kind, folder: Path
    ) -> bytes | None:
        """The verdict of the application this kind is held to, asked once per its version."""
        checker = self.browser if reading is EPI else self.word
        if checker is None or "refusal" in value:
            return None
        verdicts = folder / ("browser" if reading is EPI else "word")
        try:
            kept = verdicts / _verdict_name(checker.application(), checker.verifier)
            if kept.exists():
                return kept.read_bytes()
            verdict = canonical(checker.verify(data, value) | {"verifier": checker.verifier})
        except Exception as failure:  # noqa: BLE001 - an application that fails verifies nothing
            # Nothing is verified and nothing kept; the reason goes to the log, never the text.
            sys.stderr.write(f"label-docx: {type(failure).__name__}: {failure}\n")
            return None
        name = verdicts / _verdict_name(json.loads(verdict)["application"], checker.verifier)
        try:
            _write_once(name, verdict)
        except StoreError:
            # Asked twice at once, the application answered otherwise: both are kept, and the
            # two are a disagreement (``disagreement``); this ingestion answers its own.
            _write_once(name.with_name(_second_name(name.name, verdict)), verdict)
        return verdict

    def verifications(self, document: str) -> list[Json]:
        """Every verdict kept for the current reader's result: Chrome's or Word's, by version."""
        reading = self.kind_of(document)
        if reading is None:
            return []
        folder = self._result_path(document, reading).parent
        found = [
            *sorted((folder / "browser").glob("*.json")),
            *sorted((folder / "word").glob("*.json")),
        ]
        return [json.loads(p.read_bytes()) for p in found]

    def disagreement(self, document: str) -> dict[str, Json] | None:
        """A kept verdict that found the document shown otherwise than the result reads, or None.

        A kept verdict the store cannot read counts as one: nothing unchecked is served. So do two
        verdicts of one application and verifier version that differ.
        """
        first: dict[tuple[Json, Json], dict[str, Json]] = {}
        for verdict in self.verifications(document):
            if not isinstance(verdict, dict):
                return {"differs": [{"where": "a kept verdict that is not one"}]}
            if verdict.get("differs"):
                return verdict
            pair = (verdict.get("application"), verdict.get("verifier"))
            if first.setdefault(pair, verdict) != verdict:
                return {
                    "application": verdict.get("application"),
                    "differs": [{"where": "the application answered otherwise when asked again"}],
                }
        return None

    def documents(self) -> list[str]:
        """Every kept document's id, in order."""
        return sorted(p.parent.name for p in (self.root / "documents").glob("*/*/source"))

    def verify(self, document: str) -> None:
        """Read the kept source again: the kept result and receipt must be the reader's.

        Every kept verdict must be one (an object naming its application and what it shows
        otherwise). Results of earlier reader versions are kept as they are; only the current
        reader can read again.
        """
        source = self.source(document)
        if source is None:
            raise KeyError(document)
        reading = kind(source)
        path = self._result_path(document, reading)
        result = reading.read(source)[0]
        if path.exists():
            _same(path, result)
        receipt = path.with_name("receipt.json")
        if receipt.exists():
            _same(receipt, _receipt(document, result, json.loads(result), reading))
        for verdict in sorted(path.parent.glob("*/*.json")):
            try:
                value = json.loads(verdict.read_bytes())
            except ValueError as error:
                raise StoreError(f"{verdict} is not a verdict") from error
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("application"), str)
                or not isinstance(value.get("differs"), list)
                or not isinstance(value.get("verifier", ""), str)
            ):
                raise StoreError(f"{verdict} is not a verdict")
            name = _verdict_name(value["application"], value.get("verifier"))
            if verdict.name not in (name, _second_name(name, verdict.read_bytes())):
                raise StoreError(f"{verdict} is not a verdict")

    def result(self, document: str) -> bytes | None:
        """The current reader's result for a kept document, or None if there is none."""
        reading = self.kind_of(document)
        if reading is None:
            return None
        path = self._result_path(document, reading)
        # A result is served only with its receipt; one without is ingested again first.
        if not path.with_name("receipt.json").exists():
            return None
        return self._checked(document, path, reading)

    def kind_of(self, document: str) -> Kind | None:
        """The kind of a kept document, from its first bytes alone, or None if it is not kept."""
        path = self._folder(document) / "source"
        if not path.exists():
            return None
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1 << 16)
                start = chunk.lstrip(b" \t\r\n")
                if start or not chunk:
                    return kind(start)

    def _checked(self, document: str, path: Path, reading: Kind) -> bytes:
        result = path.read_bytes()
        try:
            recorded = json.loads(path.with_name("receipt.json").read_bytes())["result"]["sha256"]
            value = json.loads(result)
        except (ValueError, KeyError, TypeError) as error:
            raise StoreError(f"{path} or its receipt is not one the store wrote") from error
        if hashlib.sha256(result).hexdigest() != recorded:
            raise StoreError(f"{path} does not hash to the SHA-256 its receipt records")
        # The result names the source it was read from and the versions that read it.
        if (
            value.get("source", {}).get("sha256") != document
            or value.get("reader") != reading.reader
            or value.get("format") != reading.format
            or canonical(value) != result
        ):
            raise StoreError(f"{path} is not this reader's result for {document}")
        return result

    def source(self, document: str) -> bytes | None:
        """A kept document's bytes, checked against its name, or None if it is not kept."""
        path = self._folder(document) / "source"
        if not path.exists():
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != document:
            raise StoreError(f"{path} does not hash to its name")
        return data


def outcome(value: dict[str, Json]) -> str:
    """A result's outcome: ``read``, ``refused``, or ``read-in-part``.

    Read in part: an ePI with sections, or a .docx (either view of one with tracked changes)
    with headers, footers or comments, the reader refused on their own. Those hold no text, and
    their refusals say why.
    """
    if "refusal" in value:
        return "refused"
    found = value.get("tracked")
    tracked: dict[str, Any] = found if isinstance(found, dict) else {}
    views = [tracked[v] for v in ("accepted", "original") if v in tracked]
    if value.get("refusedSections") or any(v.get("refusedParts") for v in (value, *views)):
        return "read-in-part"
    return "read"


def _receipt(document: str, result: bytes, value: dict[str, Json], reading: Kind) -> bytes:
    """What ingesting a document answers: the same bytes every time for the same document.

    Its outcome is ``outcome``'s. A .docx with tracked changes says how many (``trackedChanges``).
    """
    found = value.get("tracked")
    tracked: dict[str, Any] = found if isinstance(found, dict) else {}
    receipt: dict[str, Json] = {
        "document": document,
        "format": reading.format,
        "outcome": outcome(value),
        "reader": reading.reader,
        "result": {"bytes": len(result), "sha256": hashlib.sha256(result).hexdigest()},
    }
    if "refusal" in value:
        receipt["refusal"] = value["refusal"]
    if "tracked" in value:
        # Two texts, accepted and original: which one is the label is the reader's caller's call.
        receipt["trackedChanges"] = len(tracked["changes"])
    if "certificate" in value:
        # The independent check's account of the read: what the source held, what the output
        # holds, and what was set aside and why.
        receipt["certificate"] = value["certificate"]
    return canonical(receipt)
