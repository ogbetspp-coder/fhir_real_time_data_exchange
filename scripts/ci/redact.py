#!/usr/bin/env python3
"""Redacts what a published log or summary must not carry (audit B08, D-6).

  redact.py <log file> [<characters to keep from the end>]

Prints the file, or its last N characters, with every IAM member ("user:…", "serviceAccount:…",
"principalSet:…") and every e-mail address replaced by a short hash of itself, and every OAuth
access token ("ya29.…") and JSON Web Token ("eyJ….….…") replaced by a fixed marker. A hash lets a
reader tell two members apart, and recognise one they already know, without the text naming
anyone. The tail is cut after redacting, so a cut can never leave half a value unredacted.

The deploy's failure issue carries its log this way (.github/workflows/deploy.yml): a plan or an
apply prints IAM members in resource addresses (query_invoker["user:…"]), which the job log shows
to those who can read the run and an issue shows to everyone who can read the repository.
scripts/ci/plan-summary.py redacts the pull request's plan summary with the same function.

Exits 1, printing nothing, when the file cannot be read.
"""
import hashlib
import re
import sys

MEMBER = re.compile(
    r"(?:(?:user|group|domain|serviceAccount|principal|principalSet|deleted:[a-z]+):[^\"\]\s,]+|[\w.+-]+@[\w.-]+)"
)
TOKEN = re.compile(r"\bya29\.[\w.-]+|\beyJ[\w-]+\.[\w-]+\.[\w-]*")


def redact(value: str) -> str:
    value = TOKEN.sub("[token redacted]", value)
    return MEMBER.sub(lambda m: "sha256:" + hashlib.sha256(m.group(0).encode()).hexdigest()[:12], value)


def main() -> int:
    try:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        keep = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    except (IndexError, OSError, ValueError):
        return 1
    redacted = redact(text)
    sys.stdout.write(redacted[-keep:] if keep > 0 else redacted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
