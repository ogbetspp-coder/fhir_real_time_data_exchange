"""The Word drawing build's steps that run in the drawing image, with no network.

    python word_drawing_build.py request < REQUEST.json
    python word_drawing_build.py record ENVIRONMENT COMMIT IMAGE_DIGEST KEY_VERSION \
        REQUEST.json DRAWN-1.json DRAWN-2.json

``docs/design/certified-word-drawing.md``, section 4: ``scripts/word-drawing/build.sh`` runs these
(its ``parse`` and ``record`` steps).

``request``: the request parsed strictly, before the .docx is read: the canonical JSON exactly, of
``{docxSha256, recompute}``, the recompute request of its own shape (``zone_a.recompute``), whose
assignments name only sections of the document's template and whose versions are this build's.
So a request it accepts holds a hash, section keys, counts and versions, and no other text. Writes
the .docx's SHA-256.

``record``: the record's fields as two drawings wrote them, byte for byte the same, each the
canonical JSON of exactly the fields ``python -m zone_a.drawing`` writes for this request, with the
environment, the commit, the image's digest (in ``drawing``) and the key version added. Writes the
record's canonical JSON, with no line feed: the bytes that are signed.

Either exits 1 with one closed reason on standard error and nothing written, never a word of the
label.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Final

from zone_a import drawing, leaflet, recompute, structure
from zone_a.canonical_json import CanonicalJsonError, canonical_json

ENVIRONMENTS: Final = frozenset({"dev", "validation", "prod"})
FIELDS: Final = frozenset(
    {"recordVersion", "request", "document", "recompute", "drawing", "sections"}
)
# The contracts' SourceKey (src/contracts/common.ts).
SOURCE_KEY: Final = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")


class RefusedError(Exception):
    """A request or a drawing the build signs nothing for."""


def request(raw: bytes, root: Path = recompute.ROOT) -> str:
    """The .docx's SHA-256 a strictly parsed request names; ``root`` holds the registry files.

    Raises:
        RefusedError: The bytes are not the canonical JSON of a drawing request.
    """
    try:
        value = json.loads(raw.decode("utf-8"))
        canonical = canonical_json(value).encode("utf-8")
    # A request nested too deep for the parser or for canonical JSON is refused, not a traceback.
    except ValueError, CanonicalJsonError, RecursionError:
        raise RefusedError("request") from None
    if canonical != raw or not isinstance(value, dict) or set(value) != {"docxSha256", "recompute"}:
        raise RefusedError("request")
    sha256 = value["docxSha256"]
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise RefusedError("request")
    try:
        document, _, _, assignments, versions = recompute._request(value["recompute"])
    # A list where the document or the view is named cannot even be looked up.
    except recompute.RefusedError, TypeError:
        raise RefusedError("request") from None
    registry, mapping = recompute._load(document, root)
    nodes = (structure._nodes if document == "smpc" else leaflet._nodes)(registry, mapping)
    keys = {node["key"] for node in nodes}
    if not all(key in keys and SOURCE_KEY.fullmatch(key) for key in assignments):
        raise RefusedError("request")
    if versions != recompute.versions(document, root):
        raise RefusedError("request")
    return sha256


def record(
    environment: str, commit: str, image_digest: str, key_version: str, asked: bytes, *drawn: bytes
) -> bytes:
    """The record's canonical bytes from two drawings of the request ``asked``.

    Raises:
        RefusedError: The drawings differ, or are not the fields of a drawing of this request, or
            an added field is malformed.
    """
    request(asked)
    if len(drawn) != 2 or drawn[0] != drawn[1]:
        raise RefusedError("the two drawings differ")
    if (
        environment not in ENVIRONMENTS
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest)
        or not re.fullmatch(r"[1-9][0-9]{0,8}", key_version)
    ):
        raise RefusedError("an added field is malformed")
    try:
        fields: Any = json.loads(drawn[0].decode("utf-8"))
        written = (canonical_json(fields) + "\n").encode("utf-8")
    except ValueError, CanonicalJsonError, RecursionError:
        raise RefusedError("the drawing is not canonical JSON") from None
    if (
        written != drawn[0]
        or not isinstance(fields, dict)
        or set(fields) != FIELDS
        or fields["recordVersion"] != drawing.RECORD_VERSION
        or fields["request"] != json.loads(asked)
        or not isinstance(fields["drawing"], dict)
        or set(fields["drawing"]) != {"version", "chrome"}
        or fields["drawing"]["version"] != drawing.DRAWING_VERSION
    ):
        raise RefusedError("the drawing is not this request's fields")
    fields |= {"environment": environment, "commitSha": commit, "keyVersion": int(key_version)}
    fields["drawing"] = fields["drawing"] | {"imageDigest": image_digest}
    return canonical_json(fields).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    """Runs the command named (the module docstring).

    Returns:
        The exit status: 0 with the output written, 1 with nothing written, 2 for a wrong command.
    """
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["request"]:
            root = Path(os.environ.get("ZONE_A_ROOT", recompute.ROOT))
            sys.stdout.write(request(sys.stdin.buffer.read(), root))
        elif len(args) == 8 and args[0] == "record":
            environment, commit, digest, version = args[1:5]
            asked, *drawn = (Path(name).read_bytes() for name in args[5:])
            sys.stdout.buffer.write(record(environment, commit, digest, version, asked, *drawn))
        else:
            sys.stderr.write("usage: word_drawing_build.py request | record ...\n")
            return 2
    except RefusedError as refused:
        sys.stderr.write(f"refused: {refused}\n")
        return 1
    except OSError:
        sys.stderr.write("refused: an input cannot be read\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
