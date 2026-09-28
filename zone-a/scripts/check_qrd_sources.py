"""Compare the pinned QRD sources with what the EMA serves today. Weekly and on demand.

    uv run --frozen python scripts/check_qrd_sources.py

For every file in qrd/sources.lock.json it downloads the URL and compares the SHA-256 with the
lock. It writes nothing. Exit status 1 means at least one file differs or could not be fetched:
the EMA has published a new version (QRD template 11 is in consultation), and pinning it is a
reviewed change (new bytes, new lock entry, regenerated registry, and a change record). It runs
weekly in .github/workflows/ema-drift.yml, which opens an issue when it fails and never gates a
pull request, so a slow or changed EMA website never breaks a build; the offline check in
tests/test_qrd_registry.py runs in CI.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "qrd" / "sources.lock.json"


def main() -> int:
    """Downloads every locked QRD source and compares its SHA-256 with the lock.

    Returns:
        The exit status: 0 when every file is unchanged, 1 when one differs or could not be
        fetched.
    """
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    status = 0
    for entry in lock["sources"]:
        request = urllib.request.Request(entry["url"], headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                digest = hashlib.sha256(response.read()).hexdigest()
        except OSError as error:
            print(f"UNREACHABLE {entry['file']}: {error}")
            status = 1
            continue
        if digest == entry["sha256"]:
            print(f"unchanged   {entry['file']}")
        else:
            print(f"CHANGED     {entry['file']}: {digest}")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
