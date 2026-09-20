"""Canonical JSON and the two digest functions, ported from ``src/lib/hash.ts``.

Every hash in the system is the SHA-256 of canonical JSON: object keys sorted by UTF-16 code
unit order (RFC 8785), no insignificant whitespace, ``JSON.stringify`` number and string
formatting (``docs/fidelity-normalization.md`` section 1). Locale-aware sorting is never used.

Four places where Python's defaults are wrong and must not be relied on:

* ``sorted(d)`` orders strings by Unicode code point. That differs from UTF-16 code unit order
  for every astral character, which sorts *below* U+E000-U+FFFF once encoded as a surrogate
  pair. Keys are therefore ordered by ``key.encode("utf-16-be")``, whose byte order is exactly
  code unit order. ``tests/test_canonical_json_parity.py`` feeds the same objects through both
  implementations and compares the bytes, so the claim is tested rather than reasoned about.
* ``json.dumps`` accepts floats and would format them by Python's rules, not JavaScript's.
  Non-integral floats are rejected instead of guessed: no contract or vector value is a
  non-integer number, and a silently divergent float format would break a hash rather than a
  test. An *integral* float is a different matter: JSON has one number type and JavaScript has
  one number type, so ``1.0`` and ``1`` are the same value and ``JSON.stringify`` writes ``1``
  for both. ``json.loads`` is what makes them differ here, so an integral float within the
  safe-integer range is written as the integer it is. Outside that range the two languages
  disagree about the *digits* (JavaScript switches to exponential notation at 1e21 and loses
  precision above 2^53-1), so both ``int`` and ``float`` are refused there rather than guessed;
  the contracts cap every number at ``Number.MAX_SAFE_INTEGER`` for the same reason.
* ``json.dumps(..., ensure_ascii=False)`` emits an unpaired surrogate raw, and encoding that to
  UTF-8 raises ``UnicodeEncodeError``. ``JSON.stringify`` is well-formed (ES2019): it writes
  ``\\udXXX``, lower-case, for every unpaired surrogate. ``_escape_lone_surrogates`` does the
  same, so canonical JSON is byte-identical for such a string and ``sha256_json`` cannot raise.
  A surrogate can only ever occur inside a string literal, so rewriting the serialised form is
  safe. A Python ``str`` decoded from JSON already carries a *pair* as one astral code point,
  so every surrogate left in one is genuinely unpaired.
* ``str.encode("utf-8")`` has no equivalent of Node's replacement behaviour. Node's
  ``Buffer.from(text, "utf8")`` writes U+FFFD (``ef bf bd``) for each unpaired surrogate code
  unit, where Python either raises or, with ``surrogatepass``, writes WTF-8 (``ed a0 80``) and
  produces a digest Node never produces. ``sha256_utf8`` substitutes U+FFFD first.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Final

type JsonValue = bool | int | str | list["JsonValue"] | dict[str, "JsonValue"] | None

_LONE_SURROGATE: Final = re.compile("[\ud800-\udfff]")

# `Number.MAX_SAFE_INTEGER`, the bound every numeric field in `contracts/generated/` carries.
MAX_SAFE_INTEGER: Final = 9007199254740991


class CanonicalJsonError(TypeError):
    """A value that canonical JSON refuses to encode."""


def _utf16_key(item: tuple[str, JsonValue]) -> bytes:
    return item[0].encode("utf-16-be", errors="surrogatepass")


def _escape_lone_surrogates(serialized: str) -> str:
    """Rewrite every unpaired surrogate as ``JSON.stringify`` does: ``\\udXXX``, lower-case."""
    return _LONE_SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", serialized)


def _canonicalize(value: object) -> JsonValue:
    # bool before int: bool is a subclass of int in Python and must stay a JSON boolean.
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, float):
        # An integral float is the same JSON number as the integer; `JSON.stringify` writes the
        # integer for both. Anything else is JavaScript's shortest-round-trip formatting, which
        # is a normative reference to a JavaScript function rather than a rule, so it is refused.
        if not value.is_integer() or abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJsonError(
                "canonical JSON refuses a non-integral or unsafe float: JavaScript number "
                "formatting is not reproduced here"
            )
        return int(value)
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJsonError(
                "canonical JSON refuses an integer beyond Number.MAX_SAFE_INTEGER: JavaScript "
                "would write different digits for it"
            )
        return value
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        keys = [key for key in value if not isinstance(key, str)]
        if keys:
            raise CanonicalJsonError("canonical JSON requires string object keys")
        items: list[tuple[str, JsonValue]] = [(key, value[key]) for key in value]
        return {key: _canonicalize(child) for key, child in sorted(items, key=_utf16_key)}
    raise CanonicalJsonError(f"canonical JSON cannot encode {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialise ``value`` the way ``canonicalJson()`` in ``src/lib/hash.ts`` does."""
    return _escape_lone_surrogates(
        json.dumps(
            _canonicalize(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=False,
        )
    )


def sha256_json(value: object) -> str:
    """Hex SHA-256 of the canonical JSON encoding of ``value`` (``sha256()`` in hash.ts).

    The canonical form carries no unpaired surrogate, so this never raises on one.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_utf8(text: str) -> str:
    """Hex SHA-256 of the raw UTF-8 bytes of ``text`` (``sha256Utf8()`` in hash.ts).

    Node substitutes U+FFFD for an unpaired surrogate rather than raising or writing WTF-8, so
    the substitution happens here too and the digest is the one Node computes.
    """
    return hashlib.sha256(
        _LONE_SURROGATE.sub("�", text).encode("utf-8", errors="surrogatepass")
    ).hexdigest()
