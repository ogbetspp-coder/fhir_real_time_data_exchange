"""The exhaustive parity sweep: the port against the TypeScript at every code point.

``scripts/fidelity/parity-sweep.ts`` writes, for every code point but the surrogates, a digest of
what the TypeScript scanner and normalisation give for it in five contexts; this computes the same
with the port and compares (audit 2026-09-27, F-4). It takes minutes, so it runs nightly
(``.github/workflows/parity-sweep.yml``) and only where ``PARITY_SWEEP`` names the TypeScript's
file; everywhere else it is skipped. A divergence is reported by code point, never by text.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from zone_a.fidelity import NormalizationError, XhtmlError, normalize_text, xhtml_to_text

_ROOT_OPEN = '<div xmlns="http://www.w3.org/1999/xhtml">'
_COMBINING_ACUTE = chr(0x0301)
_BULLET = chr(0x2022)


def _xhtml(div: str) -> str:
    try:
        return xhtml_to_text(div)
    except XhtmlError as error:
        return f"E:{error.code}"


def _normalize(text: str) -> str:
    try:
        return normalize_text(text)
    except NormalizationError as error:
        return f"E:{error.code}"


def sweep_digest(code_point: int) -> str:
    """What ``sweepDigest`` in the TypeScript computes, from the port."""
    reference = format(code_point, "x")
    character = chr(code_point)

    def root(inner: str) -> str:
        return f"{_ROOT_OPEN}{inner}</div>"

    parts = [
        _xhtml(root(f"<p>x<sup>&#x{reference};</sup></p>")),
        _xhtml(root(f"<p>x<sub>&#x{reference};</sub></p>")),
        _xhtml(root(f"<p>a<b>&#x{reference};</b></p>")),
        _normalize(f"\n{character} a{character}\t{character}"),
        _normalize(f"e{character}{_COMBINING_ACUTE}\n{_BULLET} {character}"),
    ]
    # `JSON.stringify` of the list: no spaces, and non-ASCII written as itself.
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def test_every_code_point_gives_what_the_typescript_gives() -> None:
    configured = os.environ.get("PARITY_SWEEP")
    if not configured:
        pytest.skip("PARITY_SWEEP is not set: the sweep runs nightly")
    expected = dict(
        line.split("\t")
        for line in Path(configured).read_text(encoding="utf-8").splitlines()
        if line
    )
    assert len(expected) == 0x110000 - 0x800
    differing = [
        reference
        for reference, digest in expected.items()
        if sweep_digest(int(reference, 16)) != digest
    ]
    assert differing == [], f"{len(differing)} code points differ, first {differing[:30]}"
