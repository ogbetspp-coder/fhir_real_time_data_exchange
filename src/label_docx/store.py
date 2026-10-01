"""A write-once, content-addressed store of documents and what the reader made of them.

A document is named by the SHA-256 of its bytes. Its bytes are kept once, and the reader's
result (canonical JSON, read or refused) is kept once per reader and format version, beside the
results of every earlier version, which are never touched. The reader is the .docx reader, or
the ePI reader for JSON (``documents.kind``). Ingesting the same bytes again finds
what is there and returns it byte for byte; nothing is computed twice under the same versions,
and nothing stored is ever replaced.

    <root>/documents/<id[:2]>/<id>/source
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/result.json      (reader@1.8.0, ...)
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/receipt.json     (the result's SHA-256)
    <root>/documents/<id[:2]>/<id>/<reader>/<format>/browser/<version>.json  (an ePI's verdict)

Every write creates a file that did not exist (a hard link from a written, synced temporary
file), so two ingestions of the same document at once cannot interleave. Every write and every
read of a file that is already there compares the bytes; a difference means the store or the
reader has changed under the same name, and raises ``StoreError`` rather than serve either. A
result is served only if it hashes to the SHA-256 its receipt records, and ``verify`` reads every
kept source again and requires the kept result byte for byte, which no edit to the store, however
consistent, can pass.

An ePI read is also held to a browser where one is given (``browser``; the service gives
``label_docx.browser.verify_epi`` where Chrome is installed): the verdict is kept beside the
result, once per browser version, and the same browser must give the same verdict byte for
byte. ``disagreement`` names a kept verdict that found a section shown otherwise; the service
does not serve that result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
    # The browser's verdict on an ePI read (canonical JSON), or None where none was asked.
    verification: bytes | None = None


# Asked about an ePI and its result (JSON), it answers its verdict: application, agrees,
# differs (each section shown otherwise), refused, sections.
type Verifier = Callable[[bytes, dict[str, Any]], dict[str, Any]]


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
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def _same(path: Path, data: bytes) -> None:
    if path.read_bytes() != data:
        raise StoreError(f"{path} holds other bytes than the ones written under its name")


class Store:
    """Documents and results under ``root``."""

    def __init__(self, root: Path, browser: Verifier | None = None) -> None:
        self.root = root
        self.browser = browser

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
        created = _write_once(self._folder(document) / "source", data)
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
            created = _write_once(path, result) or created
        value = json.loads(result)
        receipt = _receipt(document, result, value, reading)
        created = _write_once(path.with_name("receipt.json"), receipt) or created
        verification = None
        if self.browser is not None and reading is EPI and "refusal" not in value:
            verdict = canonical(self.browser(data, value))
            name = re.sub(r"[^A-Za-z0-9.]+", "-", json.loads(verdict)["application"]).strip("-")
            _write_once(path.parent / "browser" / f"{name}.json", verdict)
            verification = verdict
        return Ingested(document, receipt, result, "refusal" not in value, created, verification)

    def verifications(self, document: str) -> list[Json]:
        """Every browser verdict kept for the current reader's result, by browser version."""
        source = self.source(document)
        if source is None:
            return []
        folder = self._result_path(document, kind(source)).parent / "browser"
        return [json.loads(p.read_bytes()) for p in sorted(folder.glob("*.json"))]

    def disagreement(self, document: str) -> dict[str, Json] | None:
        """A kept verdict that found a section shown otherwise than the result reads, or None."""
        found = [v for v in self.verifications(document) if isinstance(v, dict) and v["differs"]]
        return found[0] if found else None

    def documents(self) -> list[str]:
        """Every kept document's id, in order."""
        return sorted(p.parent.name for p in (self.root / "documents").glob("*/*/source"))

    def verify(self, document: str) -> None:
        """Read the kept source again; the kept result must be what the reader makes of it."""
        source = self.source(document)
        if source is None:
            raise KeyError(document)
        kept = self.result(document)
        if kept is not None and kept != kind(source).read(source)[0]:
            raise StoreError(f"the kept result for {document} is not what the reader makes of it")

    def result(self, document: str) -> bytes | None:
        """The current reader's result for a kept document, or None if there is none."""
        source = self.source(document)
        if source is None:
            return None
        reading = kind(source)
        path = self._result_path(document, reading)
        # A result is served only with its receipt; one without is ingested again first.
        if not path.with_name("receipt.json").exists():
            return None
        return self._checked(document, path, reading)

    def _checked(self, document: str, path: Path, reading: Kind) -> bytes:
        result = path.read_bytes()
        recorded = json.loads(path.with_name("receipt.json").read_bytes())["result"]["sha256"]
        if hashlib.sha256(result).hexdigest() != recorded:
            raise StoreError(f"{path} does not hash to the SHA-256 its receipt records")
        value = json.loads(result)
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

    The outcome is ``read``, ``refused``, or for an ePI with sections the reader refused,
    ``read-in-part``: those sections hold no text, and their refusals say why.
    """
    outcome = "refused" if "refusal" in value else "read"
    if value.get("refusedSections"):
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
    if "certificate" in value:
        # The independent check's account of the read: what the source held, what the output
        # holds, and what was set aside and why.
        receipt["certificate"] = value["certificate"]
    return canonical(receipt)
