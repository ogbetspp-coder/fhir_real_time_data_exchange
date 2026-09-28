r"""Pin an EMA ePI label: fetch its document and List as the gate does and write their lock entry.

    uv run --frozen python scripts/pin_label.py FILE          # re-pin a label the lock holds
    uv run --frozen python scripts/pin_label.py FILE --url BUNDLE_URL --list LIST_URL \
        --product NAME --active-substance NAME --kind KIND --document TITLE --epar URL \
        [--list-file NAME]      # a List another label shares: its file name in lists/

The labels:pin script: the one way labels/ema-epi/sources.lock.json is written. It downloads the
document and its List with scripts/check_label_sources.py's ``fetch`` (the gate's URL template,
header, limits and no redirect), writes them to labels/ema-epi/sources/FILE and lists/, and
writes the entry with each file's SHA-256, size and the date it was retrieved, and with the EMA
ePI identifier, procedure number, marketing authorisation holder and composition date read from
those bytes (``metadata``), never typed in. Every date is the fetch's UTC date, and pinning a
file removes the entry's ``retrievedReconstructed`` or ``listRetrievedReconstructed`` flag, which
marks a date reconstructed from the commit that added a file pinned before this script existed.
A List two labels share is written once and every entry naming it takes its new hash and date,
only if the new List still lists each of those labels' documents with the metadata its entry
records; a new List URL for a shared List file needs --list-file. tests/test_qrd_check.py
derives the same metadata from the committed bytes and fails on any entry that disagrees.

Pinning new bytes is a reviewed change: afterwards run scripts/check_labels.py to regenerate the
QRD check results, and ``npm run contracts:check`` for the importer's vectors.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "labels" / "ema-epi"
LOCK = LABELS / "sources.lock.json"

_EPI_ID = "http://ema.europa.eu/fhir/epiId"
_PROCEDURE = "https://ema.europa.eu/fhir/extension/procedureNumber"
_HOLDER = "https://ema.europa.eu/fhir/extension/marketingAuthorisationHolder"


class MetadataError(Exception):
    """The pinned bytes do not carry a value the lock records, or carry it twice."""


def _one(values: list[Any], what: str) -> Any:
    if len(values) != 1:
        raise MetadataError(f"{len(values)} values for {what}, not one")
    return values[0]


def metadata(document: bytes, index: bytes) -> dict[str, str]:
    """The lock entry's values that the EMA's own bytes carry.

    Args:
        document: The document Bundle's bytes as the EMA serves them.
        index: The bytes of the List that indexes it.

    Returns:
        The document's Bundle id and the List's id (each the GUID its URL ends in), the EMA ePI
        identifier, procedure number and marketing authorisation holder (from the List), and the
        composition date (the date of the document's first entry).

    Raises:
        MetadataError: A value is missing or repeated, or the List does not list the document.
    """
    bundle = json.loads(document)
    listing = json.loads(index)
    extensions = (listing.get("subject") or {}).get("extension") or []
    by_url = {
        url: [each for each in extensions if each.get("url") == url]
        for url in (_PROCEDURE, _HOLDER)
    }
    document_id = bundle.get("id")
    if not isinstance(document_id, str):
        raise MetadataError("the document has no id")
    listed = [(each.get("item") or {}).get("reference") for each in listing.get("entry") or []]
    if f"Bundle/{document_id}" not in listed:
        raise MetadataError("the List does not list the document")
    entries = bundle.get("entry") or []
    date = (entries[0].get("resource") or {}).get("date") if entries else None
    if not isinstance(date, str):
        raise MetadataError("the document's first entry has no date")
    return {
        "documentId": document_id,
        "listId": str(listing.get("id")),
        "epiId": _one(
            [
                each.get("value")
                for each in listing.get("identifier") or []
                if each.get("system") == _EPI_ID
            ],
            "the EMA ePI identifier",
        ),
        "procedureNumber": _one(
            [(each.get("valueIdentifier") or {}).get("value") for each in by_url[_PROCEDURE]],
            "the procedure number",
        ),
        "marketingAuthorisationHolder": _one(
            [(each.get("valueCoding") or {}).get("display") for each in by_url[_HOLDER]],
            "the marketing authorisation holder",
        ),
        "compositionDate": date[:10],
    }


def _fetcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_label_sources", ROOT / "zone-a" / "scripts" / "check_label_sources.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("scripts/check_label_sources.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The order a lock entry's keys are written in.
_KEYS = (
    "product",
    "activeSubstance",
    "kind",
    "document",
    "file",
    "url",
    "sha256",
    "bytes",
    "retrieved",
    "retrievedReconstructed",
    "list",
    "listFile",
    "listSha256",
    "listBytes",
    "listRetrieved",
    "listRetrievedReconstructed",
    "epiId",
    "procedureNumber",
    "marketingAuthorisationHolder",
    "compositionDate",
    "epar",
)

# File names as src/render/sections.ts accepts them: a plain name, never a path.
FILE = re.compile(r"[a-z0-9-]+\.json")
LIST_FILE = re.compile(r"[a-z0-9-]+\.list\.json")

# The values metadata() reads from the bytes that a lock entry records.
_CARRIED = ("epiId", "procedureNumber", "marketingAuthorisationHolder", "compositionDate")


def _guid(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def _sibling_issues(
    entry: dict[str, Any], siblings: list[dict[str, Any]], index: bytes
) -> list[str]:
    """Why the new List cannot be written under the labels that share its file, if it cannot.

    Each sibling keeps its own pinned document, so the new List must still list that document
    and carry the metadata its entry records.
    """
    issues: list[str] = []
    for sibling in siblings:
        try:
            carried = metadata((LABELS / "sources" / sibling["file"]).read_bytes(), index)
        except (OSError, MetadataError, ValueError) as error:
            issues.append(f"{sibling['file']}: {error}")
            continue
        if carried["documentId"] != _guid(sibling["url"]) or carried["listId"] != _guid(
            entry["list"]
        ):
            issues.append(f"{sibling['file']}: the new List does not name its document")
        issues += [
            f"{sibling['file']}: the new List's {key} is not the one its entry records"
            for key in _CARRIED
            if carried[key] != sibling[key]
        ]
    return issues


def main() -> int:
    """Fetches one label and its List, writes both, and writes its lock entry.

    Returns:
        The exit status: 0 when the label was pinned, 1 when it could not be. Nothing is
        written unless every check passed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="the label's file name in labels/ema-epi/sources/")
    for flag in ("url", "list", "product", "active-substance", "kind", "document", "epar"):
        parser.add_argument(f"--{flag}", help="for a label the lock does not hold yet")
    parser.add_argument("--list-file", help="the List's file name in lists/, if not FILE's own")
    arguments = parser.parse_args()
    if not FILE.fullmatch(arguments.file):
        print(f"{arguments.file!r} is not a label file name ({FILE.pattern})")
        return 1
    if arguments.list_file is not None and not LIST_FILE.fullmatch(arguments.list_file):
        print(f"{arguments.list_file!r} is not a List file name ({LIST_FILE.pattern})")
        return 1
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    entry = next((each for each in lock["sources"] if each["file"] == arguments.file), None)
    given = {
        "url": arguments.url,
        "list": arguments.list,
        "product": arguments.product,
        "activeSubstance": arguments.active_substance,
        "kind": arguments.kind,
        "document": arguments.document,
        "epar": arguments.epar,
    }
    if entry is None:
        missing = [name for name, value in given.items() if value is None]
        if missing:
            print(f"{arguments.file} is not in the lock; a new label needs {missing}")
            return 1
        entry = {"file": arguments.file, **given}
        lock["sources"].append(entry)
    else:
        # A new List URL for a List file other labels share would move them to another List
        # unasked: the moved label needs a List file of its own.
        shares = any(
            each is not entry and each.get("listFile") == entry.get("listFile")
            for each in lock["sources"]
        )
        if (
            arguments.list is not None
            and arguments.list != entry["list"]
            and arguments.list_file is None
            and shares
        ):
            print(
                f"could not pin {arguments.file}: {entry['listFile']} is shared; a new List "
                "URL needs --list-file"
            )
            return 1
        entry.update({name: value for name, value in given.items() if value is not None})
    if arguments.list_file is not None:
        entry["listFile"] = arguments.list_file
    entry.setdefault("listFile", arguments.file.removesuffix(".json") + ".list.json")
    siblings = [
        each
        for each in lock["sources"]
        if each is not entry and each.get("listFile") == entry["listFile"]
    ]
    if any(sibling["list"] != entry["list"] for sibling in siblings):
        print(f"could not pin {arguments.file}: {entry['listFile']} holds another List")
        return 1

    fetcher = _fetcher()
    try:
        document = fetcher.fetch(entry["url"])
        index = fetcher.fetch(entry["list"])
        values = metadata(document, index)
    except (OSError, fetcher.RefusedError, MetadataError, ValueError) as error:
        print(f"could not pin {arguments.file}: {error}")
        return 1
    if _guid(entry["url"]) != values["documentId"]:
        print(f"could not pin {arguments.file}: the document's id is not its URL's")
        return 1
    if _guid(entry["list"]) != values["listId"]:
        print(f"could not pin {arguments.file}: the List's id is not its URL's")
        return 1
    issues = _sibling_issues(entry, siblings, index)
    if issues:
        print(f"could not pin {arguments.file}: the List is shared, and {'; '.join(issues)}")
        return 1

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    (LABELS / "sources" / entry["file"]).write_bytes(document)
    (LABELS / "lists" / entry["listFile"]).write_bytes(index)
    entry.update(
        sha256=hashlib.sha256(document).hexdigest(),
        bytes=len(document),
        retrieved=today,
        **{key: values[key] for key in _CARRIED},
    )
    # A date written here is the fetch's own, never reconstructed.
    entry.pop("retrievedReconstructed", None)
    for each in [entry, *siblings]:
        each.update(
            listSha256=hashlib.sha256(index).hexdigest(), listBytes=len(index), listRetrieved=today
        )
        each.pop("listRetrievedReconstructed", None)
    lock["sources"] = [
        {key: each[key] for key in _KEYS if key in each}
        | {key: value for key, value in each.items() if key not in _KEYS}
        for each in lock["sources"]
    ]
    LOCK.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"pinned {entry['file']} and {entry['listFile']}, retrieved {today}")
    print("next: scripts/check_labels.py, then npm run contracts:check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
