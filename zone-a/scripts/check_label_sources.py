"""Compare the pinned EMA ePI labels with what the EMA ePI API serves today.

Weekly and on demand (.github/workflows/ema-drift.yml, which never gates a pull request).

    uv run --frozen python scripts/check_label_sources.py

For every label in labels/ema-epi/sources.lock.json it downloads the document and its List (a
List two labels share once) and compares each SHA-256 with the lock. It fetches as the gate's
fetcher does (src/authority/fetch.ts, ``emaFetcher``): one ``Accept: application/fhir+json``
header, no redirect followed, HTTP 200 only, a 30 s timeout and a 4 MiB limit on the body, so
"unchanged" means the gate would read the same bytes; tests/test_label_sources.py keeps the
values in step with fetch.ts. It writes nothing. Exit status 1 means at least one file differs
or could not be fetched: the EMA has republished that ePI (or serves it otherwise), and pinning
the new one is a reviewed change (new bytes, new lock entry, regenerated check results). The
offline check in tests/test_qrd_check.py runs in CI.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOCK = Path(__file__).resolve().parents[2] / "labels" / "ema-epi" / "sources.lock.json"

# As src/authority/fetch.ts fetches.
ACCEPT = "application/fhir+json"
TIMEOUT_SECONDS = 30
MAX_BYTES = 4 * 1024 * 1024


class RefusedError(Exception):
    """The response is not one the gate would read."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any) -> None:
        raise RefusedError("a redirect")


def fetch(url: str) -> bytes:
    """The body the gate's fetcher would read at the URL, or ``RefusedError``/``OSError``."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"Accept": ACCEPT})
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise RefusedError(f"HTTP {response.status}")
            body: bytes = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise RefusedError(f"HTTP {error.code}") from error
    if len(body) > MAX_BYTES:
        raise RefusedError("over the size limit")
    return body


def files(lock: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(url, file, sha256) for every document and List in the lock, each URL once."""
    out: dict[str, tuple[str, str, str]] = {}
    for entry in lock["sources"]:
        for url, file, sha256 in (
            (entry["url"], entry["file"], entry["sha256"]),
            (entry["list"], entry["listFile"], entry["listSha256"]),
        ):
            out.setdefault(url, (url, file, sha256))
    return list(out.values())


def main() -> int:
    """Downloads every locked label and its List and compares each SHA-256 with the lock.

    Returns:
        The exit status: 0 when every file is unchanged, 1 when one differs or could not be
        fetched.
    """
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    status = 0
    for url, file, sha256 in files(lock):
        try:
            digest = hashlib.sha256(fetch(url)).hexdigest()
        except (OSError, RefusedError) as error:
            print(f"UNREACHABLE {file}: {error}")
            status = 1
            continue
        if digest == sha256:
            print(f"unchanged   {file}")
        else:
            print(f"CHANGED     {file}: {digest}")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
