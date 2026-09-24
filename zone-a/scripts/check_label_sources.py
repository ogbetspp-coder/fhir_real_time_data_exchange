"""Compare the pinned EMA ePI labels with what the EMA ePI API serves today.

On demand, never in CI.

    uv run --frozen python scripts/check_label_sources.py

For every label in labels/ema-epi/sources.lock.json it downloads the document and its List and
compares each SHA-256 with the lock. It writes nothing. Exit status 1 means at least one file
differs or could not be fetched: the EMA has republished that ePI, and pinning the new one is a
reviewed change (new bytes, new lock entry, regenerated check results). The offline check in
tests/test_qrd_check.py runs in CI.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "labels" / "ema-epi" / "sources.lock.json"


def main() -> int:
    """Downloads every locked label and its List and compares each SHA-256 with the lock.

    Returns:
        The exit status: 0 when every file is unchanged, 1 when one differs or could not be
        fetched.
    """
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    status = 0
    for entry in lock["sources"]:
        for url, file, sha256 in (
            (entry["url"], entry["file"], entry["sha256"]),
            (entry["list"], entry["listFile"], entry["listSha256"]),
        ):
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    digest = hashlib.sha256(response.read()).hexdigest()
            except OSError as error:
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
