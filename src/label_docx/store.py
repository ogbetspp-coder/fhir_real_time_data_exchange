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
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/browser/<version>.json  (an ePI's)
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/word/<version>.json     (a .docx's)

Every write creates a file that did not exist (a hard link from a written, synced temporary
file), so two ingestions of the same document at once cannot interleave. Every write and every
read of a file that is already there compares the bytes; a difference means the store or the
reader has changed under the same name, and raises ``StoreError`` rather than serve either. A
result is served only if it hashes to the SHA-256 its receipt records, and ``verify`` reads every
kept source again and requires the kept result and receipt byte for byte, which no edit to the
store, however consistent, can pass.

An ePI read is also held to a browser where one is given (``browser``), and a .docx read to Word
where it is given (``word``): a ``Checker`` names the application's version and gives its verdict.
The verdict is kept beside the result once per application version, before the receipt is
written, so a result is never served while its check is still running; a version already kept is
not asked again. ``disagreement`` names a kept verdict that found the document shown otherwise
than the result reads; the service does not serve that result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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
    result: bytes
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


def _verdict_name(application: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", application).strip("-") + ".json"


def _version(version: str) -> str:
    return version.replace("/", "@")


def _write_once(path: Path, data: bytes) -> bool:
    """Create ``path`` holding ``data``; if it exists, it must hold ``data``. True if created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _same(path, data)
        return False
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".incoming-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)  # creates path, or fails if it exists
        except FileExistsError:
            # Another ingestion of the same document got there first; it must agree.
            _same(path, data)
            return False
        # The new name is durable only once its directory is.
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
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
            kept = verdicts / _verdict_name(checker.application())
            if kept.exists():
                return kept.read_bytes()
            verdict = canonical(checker.verify(data, value))
        except Exception as failure:  # noqa: BLE001 - an application that fails verifies nothing
            # Nothing is verified and nothing kept; the reason goes to the log, never the text.
            sys.stderr.write(f"label-docx: {type(failure).__name__}: {failure}\n")
            return None
        name = verdicts / _verdict_name(json.loads(verdict)["application"])
        try:
            _write_once(name, verdict)
        except StoreError:
            # Asked twice at once, the application answered otherwise: the first verdict stands.
            return name.read_bytes()
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

        A kept verdict the store cannot read counts as one: nothing unchecked is served.
        """
        for verdict in self.verifications(document):
            if not isinstance(verdict, dict):
                return {"differs": [{"where": "a kept verdict that is not one"}]}
            if verdict.get("differs"):
                return verdict
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
                or verdict.name != _verdict_name(value["application"])
            ):
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


def _receipt(document: str, result: bytes, value: dict[str, Json], reading: Kind) -> bytes:
    """What ingesting a document answers: the same bytes every time for the same document.

    The outcome is ``read``, ``refused``, or ``read-in-part``: an ePI with sections, or a .docx
    with headers, footers or comments, the reader refused on their own. Those hold no text, and
    their refusals say why. A .docx with tracked changes says how many (``trackedChanges``).
    """
    outcome = "refused" if "refusal" in value else "read"
    found = value.get("tracked")
    tracked: dict[str, Any] = found if isinstance(found, dict) else {}
    views = [tracked[v] for v in ("accepted", "original") if v in tracked]
    if value.get("refusedSections") or any(v.get("refusedParts") for v in (value, *views)):
        outcome = "read-in-part"
    receipt: dict[str, Json] = {
        "document": document,
        "format": reading.format,
        "outcome": outcome,
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
