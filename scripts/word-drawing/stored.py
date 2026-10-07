"""Whether a stored drawing record is one this build accepts (scripts/word-drawing/build.sh).

    python3 stored.py STORED PEM ENVIRONMENT KEY_VERSION IMAGE_DIGEST VERSION REQUEST [RECORD]

STORED must be exactly ``{"record":<R>,"signatureBase64":"<S>"}``: S the one base64 spelling of a
signature that verifies with the pinned public key PEM over R's bytes (RSA-PSS, SHA-256, a 32-byte
salt, as Cloud KMS signs and the worker verifies); R the canonical JSON of a record of this
environment, key version, image digest and drawing version, for the request whose bytes are in
REQUEST. With RECORD, this build's own record, R must also be RECORD but for its commit: two builds
of one request on either side of a merge differ only there.

Anyone who may write the bucket at project level can plant an object at a record's path (the
design's "Where it is stored"), so an object is never taken on its path alone. Exits 0 when the
object is accepted, printing the commit it names, else 1 with one reason on standard error. Runs
in the Cloud SDK image's Python, so the standard library alone; every value a record holds is
ASCII (hashes, section keys, versions), where Python's sorted, compact JSON is RFC 8785's.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SHAPE = re.compile(rb'\{"record":(\{.*\}),"signatureBase64":"([A-Za-z0-9+/]+={0,2})"\}', re.S)
COMMIT = re.compile(rb'"commitSha":"([0-9a-f]{40})"')


def _float(text: str) -> float:
    raise ValueError("a record holds no fractions")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def problem(stored: bytes, pem: str, expected: dict, own: bytes | None = None) -> str | None:
    """Why the stored object is not accepted, or None (the module docstring)."""
    match = SHAPE.fullmatch(stored)
    if match is None:
        return "it is not {record, signatureBase64}"
    record, signature = match.groups()
    try:
        raw = base64.b64decode(signature, validate=True)
    except ValueError:
        return "its signature is not base64"
    if base64.b64encode(raw) != signature:
        return "its signature is not canonical base64"
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "record").write_bytes(record)
        (Path(folder) / "signature").write_bytes(raw)
        verified = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sigopt", "rsa_padding_mode:pss"]
            + ["-sigopt", "rsa_pss_saltlen:32", "-verify", pem]
            + ["-signature", f"{folder}/signature", f"{folder}/record"],
            capture_output=True,
            check=False,
        )
    if verified.returncode != 0:
        return "its signature does not verify against the pinned key"
    try:
        fields = json.loads(record, parse_float=_float)
    except ValueError:
        return "its record is not JSON"
    if not record.isascii() or not isinstance(fields, dict) or _canonical(fields) != record:
        return "its record is not canonical"
    drawing = fields.get("drawing")
    if (
        fields.get("environment") != expected["environment"]
        or type(fields.get("keyVersion")) is not int
        or fields["keyVersion"] != expected["keyVersion"]
        or not isinstance(drawing, dict)
        or drawing.get("imageDigest") != expected["imageDigest"]
        or drawing.get("version") != expected["version"]
        or "request" not in fields
        or _canonical(fields["request"]) != expected["request"]
        or len(COMMIT.findall(record)) != 1
    ):
        return "its record is not this request's, environment's, key's or image's"
    if own is not None and COMMIT.sub(b"", record) != COMMIT.sub(b"", own):
        return "its record differs from this build's"
    return None


def main(argv: list[str]) -> int:
    if len(argv) not in (7, 8):
        sys.stderr.write("usage: stored.py STORED PEM ENV KEY DIGEST VERSION REQUEST [RECORD]\n")
        return 2
    stored, pem, environment, key_version, digest, version, request = argv[:7]
    expected = {
        "environment": environment,
        "keyVersion": int(key_version),
        "imageDigest": digest,
        "version": version,
        "request": Path(request).read_bytes(),
    }
    data = Path(stored).read_bytes()
    own = Path(argv[7]).read_bytes() if len(argv) == 8 else None
    why = problem(data, pem, expected, own)
    if why is not None:
        named = COMMIT.search(data)
        commit = named.group(1).decode() if named else "none"
        sys.stderr.write(f"{why} (it names commit {commit})\n")
        return 1
    sys.stdout.write(COMMIT.search(data).group(1).decode() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
