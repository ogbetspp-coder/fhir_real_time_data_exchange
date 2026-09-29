"""Byte-level parity of the canonical JSON encoder with ``src/lib/hash.ts``.

``tests/test_contracts_parity.py`` proves the encoder reproduces the digests the TypeScript
wrote into the exported fixtures. That is a strong check over the shapes the fixtures happen to
contain, and no check at all over the two shapes where the languages' defaults disagree:

* **integer-like object keys.** A JavaScript object re-emits ``"2"`` and ``"10"`` first and in
  numeric order whatever the insertion order, so an implementation that sorts keys into an
  object and serialises that silently discards the sort. RFC 8785 and this encoder order keys by
  UTF-16 code unit, which puts ``"10"`` before ``"2"`` and an astral key *before* U+E000-U+FFFF.
  Python's ``sorted()`` orders by code point and gets the astral case wrong; a JavaScript object
  gets the integer-like case wrong. The cases below fail on either mistake.
* **unpaired surrogates.** ``JSON.stringify`` is well-formed and writes ``\\udXXX``; Python's
  ``json.dumps(ensure_ascii=False)`` writes the surrogate raw and encoding it raises. And Node's
  ``Buffer.from(text, "utf8")`` substitutes U+FFFD for an unpaired surrogate where Python would
  raise or emit WTF-8, which is a different ``sha256Utf8`` digest.

The expected values are the TypeScript's own output, produced by running ``canonicalJson``,
``sha256`` and ``sha256Utf8`` from ``src/lib/hash.ts`` under ``npx tsx`` and pasting the result.
They are written as code point escapes so that the parity claim does not depend on how an
editor, a terminal, or a Git filter treats an invisible character.
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

from zone_a.canonical_json import canonical_json, ecmascript_number, sha256_json, sha256_utf8

from .conftest import CONTRACT_FIXTURES, load_json

# name -> (value, canonicalJson(value), sha256(value)) as src/lib/hash.ts produces them.
CANONICAL_CASES: dict[str, tuple[Any, str, str]] = {
    # Integer-like keys sort as strings by UTF-16 code unit: "10" < "2" < "b".
    "integer-like-keys": (
        {"b": 1, "10": 2, "2": 3},
        '{"10":2,"2":3,"b":1}',
        "2bd9ed0f108f1e237b259812c8ea94840fb3d0a779598e395bad6337a1bc8ab6",
    ),
    # U+1F600 is the surrogate pair D83D DE00, so it sorts BEFORE U+FF5E, not after it.
    "astral-vs-bmp-keys": (
        {"\uff5e": 1, "\U0001f600": 2},
        '{"\U0001f600":2,"\uff5e":1}',
        "c44a324298283d910e4120a2ff3c2d7746b4a5c9f2d9625c67f6abb58748cf31",
    ),
    # Keys that look numeric but are not, an empty key, and an unpaired surrogate as a key.
    "mixed-keys": (
        {"0": 1, "00": 2, "1e3": 3, "-1": 4, "": 5, " ": 6, "\ud800": 7},
        '{"":5," ":6,"-1":4,"0":1,"00":2,"1e3":3,"\\ud800":7}',
        "5e9b0aef3046da2148f01fe3278c2f8a496698a8aeebc5c2f52e4604f767e281",
    ),
    "lone-surrogate-value": (
        {"a": "x\ud800y"},
        '{"a":"x\\ud800y"}',
        "5f7177a3d9ec518e1f64c4598ffa3b5e6c4bf277605c9ad0ae1a5d9559a07976",
    ),
    "lone-surrogate-key": (
        {"k\udfffz": 1},
        '{"k\\udfffz":1}',
        "6ed568ccedb42a547ff816b9fd5063b7f3b7ba1020b3f6d81eab36b9e91c117b",
    ),
    # A well-formed pair is one code point in Python and is emitted raw, not escaped.
    "surrogate-pair-value": (
        {"a": "\U0001f600"},
        '{"a":"\U0001f600"}',
        "8da70d6db48a3dc32fa9526602b4970d2eb551ffbbb8c497d97e4cb24f637d04",
    ),
    # U+2028 and U+2029 are emitted raw by JSON.stringify (they are not escaped in JSON).
    "nested": (
        {"z": [1, {"10": True, "9": None}], "2": "\u2028\u2029"},
        '{"2":"\u2028\u2029","z":[1,{"10":true,"9":null}]}',
        "6035f3dd0079b1814302de3855d6efae61de3a2e3aceafd8d803c39ea3ac3d93",
    ),
}

# text -> sha256Utf8(text) as Node computes it. Node writes EF BF BD for each unpaired
# surrogate CODE UNIT, which is neither an error nor the WTF-8 bytes Python's surrogatepass
# would produce; the last case pins that it is one replacement per code unit, not per run.
UTF8_CASES: dict[str, str] = {
    "plain": "a116c9ed46d6207734a43317d30fd88f52ac8634c37d904bbf4e41d865f90475",
    "a\ud800b": "05087813392efc16fe8ff448920c6328e53af865df39419436659d9ffda90f7b",
    "\U0001f600": "f0443a342c5ef54783a111b51ba56c938e474c32324d90c3a60c9c8e3a37e2d9",
    "\udfff\udfff": "52793f8dc1d85e409f8c88be99d8b31d58f676246340150f406289e04a11151e",
}


@pytest.mark.parametrize("name", sorted(CANONICAL_CASES))
def test_canonical_json_is_byte_identical_to_the_typescript(name: str) -> None:
    value, expected, _ = CANONICAL_CASES[name]
    assert canonical_json(value) == expected


@pytest.mark.parametrize("name", sorted(CANONICAL_CASES))
def test_sha256_json_reproduces_the_typescript_digest(name: str) -> None:
    value, _, digest = CANONICAL_CASES[name]
    assert sha256_json(value) == digest


@pytest.mark.parametrize("index", range(len(UTF8_CASES)))
def test_sha256_utf8_reproduces_the_node_digest(index: int) -> None:
    text, digest = sorted(UTF8_CASES.items())[index]
    assert sha256_utf8(text) == digest


def test_an_unpaired_surrogate_never_raises() -> None:
    # The failure this replaces was a UnicodeEncodeError, not a wrong hash: a Zone B report that
    # hashes a source containing an unpaired surrogate could not be checked here at all.
    assert len(sha256_json({"pages": [{"text": "\ud800\udbff"}]})) == 64
    assert len(sha256_utf8("\ud800\udbff")) == 64


def test_every_double_is_written_as_javascript_writes_it() -> None:
    """RFC 8785 number serialisation, held to JavaScript's own text (audit C-10).

    ``test/fixtures/contracts/canonical-json-numbers.json`` carries each double by its IEEE 754
    bits, so no JSON parser rounds it on the way, with ``JSON.stringify``'s text for it: the
    ranges where JavaScript switches between placed digits and an exponent, the smallest and
    largest doubles, and a seeded sweep of bit patterns. A mismatch reports the bits only.
    """
    cases = load_json(CONTRACT_FIXTURES / "canonical-json-numbers.json")["cases"]
    assert len(cases) > 1000
    wrong = [
        case["bits"]
        for case in cases
        if ecmascript_number(struct.unpack(">d", bytes.fromhex(case["bits"]))[0])
        != case["canonical"]
    ]
    assert wrong == []
    value = struct.unpack(">d", bytes.fromhex(cases[0]["bits"]))[0]
    assert canonical_json([value]) == f"[{cases[0]['canonical']}]"
