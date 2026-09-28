r"""Canonical JSON and the two digest functions, ported from ``src/lib/hash.ts``.

Every hash in the system is the SHA-256 of canonical JSON: object keys sorted by UTF-16 code
unit order (RFC 8785), no insignificant whitespace, ``JSON.stringify`` number and string
formatting (``docs/fidelity-normalization.md`` section 1). Locale-aware sorting is never used.

Four places where Python's defaults are wrong and must not be relied on:

* ``sorted(d)`` orders strings by Unicode code point. That differs from UTF-16 code unit order
  for every astral character, which sorts *below* U+E000-U+FFFF once encoded as a surrogate
  pair. Keys are therefore ordered by ``key.encode("utf-16-be")``, whose byte order is exactly
  code unit order. ``tests/test_canonical_json_parity.py`` feeds the same objects through both
  implementations and compares the bytes, so the claim is tested rather than reasoned about.
* ``json.dumps`` formats a float by Python's rules (``1e-06``, ``1e+16``, ``1.0``), not
  JavaScript's (``0.000001``, ``10000000000000000``, ``1``). A float is written here by RFC 8785
  section 3.2.2.3, which is ECMAScript's ``Number::toString``: the shortest digits that round-trip
  (``repr`` finds the same digits, both choosing the closest), placed by the exponent's range.
  So a FHIR decimal (a 2.5 mg strength) hashes as Zone B hashes it (audit C-10; until 2026-09-28
  any non-integral float was refused). ``tests/test_canonical_json_parity.py`` reproduces
  JavaScript's text for the doubles of ``test/fixtures/contracts/canonical-json-numbers.json``,
  carried by their bits. An *integer* beyond ``Number.MAX_SAFE_INTEGER`` is still refused: JSON
  gives Python the exact integer and JavaScript the nearest double, so the two hold different
  values, and the contracts cap every integer there for the same reason. ``nan`` and the
  infinities have no JSON form and are refused.
* ``json.dumps(..., ensure_ascii=False)`` emits an unpaired surrogate raw, and encoding that to
  UTF-8 raises ``UnicodeEncodeError``. ``JSON.stringify`` is well-formed (ES2019): it writes
  ``\udXXX``, lower-case, for every unpaired surrogate. ``_escape_lone_surrogates`` does the
  same, so canonical JSON is byte-identical for such a string and ``sha256_json`` cannot raise.
  A Python ``str`` decoded from JSON already carries a *pair* as one astral code point, so every
  surrogate left in one is genuinely unpaired.
* ``str.encode("utf-8")`` has no equivalent of Node's replacement behaviour. Node's
  ``Buffer.from(text, "utf8")`` writes U+FFFD (``ef bf bd``) for each unpaired surrogate code
  unit, where Python either raises or, with ``surrogatepass``, writes WTF-8 (``ed a0 80``) and
  produces a digest Node never produces. ``sha256_utf8`` substitutes U+FFFD first.

A number is a value, not a text (RFC 8785, I-JSON): ``2.50`` and ``2.5`` are one double and hash
alike, in both languages. A FHIR decimal's written precision is therefore not carried by a hash,
and Zone B refuses a document part whose numbers are not written as JavaScript writes them (ADR
0002, "Numbers").
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from typing import Final

type JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None

_LONE_SURROGATE: Final = re.compile("[\ud800-\udfff]")

# `Number.MAX_SAFE_INTEGER`, the bound every integer field in `contracts/generated/` carries.
MAX_SAFE_INTEGER: Final = 9007199254740991

# ECMAScript Number::toString writes digits in place up to this decimal exponent, and with an
# exponent above it.
_MAX_PLACED_EXPONENT: Final = 21
# ... and down to this one (0.000001), below which it writes an exponent (1e-7).
_MIN_PLACED_EXPONENT: Final = -6


class CanonicalJsonError(TypeError):
    """A value that canonical JSON refuses to encode."""


def _utf16_key(item: tuple[str, JsonValue]) -> bytes:
    return item[0].encode("utf-16-be", errors="surrogatepass")


def _escape_lone_surrogates(serialized: str) -> str:
    r"""Rewrite every unpaired surrogate as ``JSON.stringify`` does: ``\udXXX``, lower-case."""
    return _LONE_SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", serialized)


def ecmascript_number(value: float) -> str:
    """A finite double as ``JSON.stringify`` writes it (RFC 8785 section 3.2.2.3).

    ECMAScript's ``Number::toString``: with ``s`` the shortest digits that round-trip (``k`` of
    them) and ``n`` the decimal exponent such that the value is ``0.s x 10**n``, the digits are
    placed in full when ``k <= n <= 21``, with a point inside them when ``0 < n <= 21``, after
    ``0.`` and ``-n`` zeros when ``-6 < n <= 0``, and otherwise as ``d[.ddd]e+x`` or ``e-x``.
    ``-0`` is written ``0``.

    Args:
        value: A finite float.

    Returns:
        The text JavaScript writes for it.

    Raises:
        CanonicalJsonError: ``value`` is ``nan`` or infinite.
    """
    if not math.isfinite(value):
        raise CanonicalJsonError("canonical JSON has no form for nan or an infinity")
    if value == 0:
        return "0"
    if value < 0:
        return "-" + ecmascript_number(-value)
    # `repr` is the shortest round-trip form; `normalize` drops its trailing zeros ("100.0").
    _, digit_tuple, exponent = Decimal(repr(value)).normalize().as_tuple()
    if not isinstance(exponent, int):  # pragma: no cover - a finite value has an int exponent
        raise CanonicalJsonError("canonical JSON cannot place this number")
    digits = "".join(str(digit) for digit in digit_tuple)
    k = len(digits)
    n = k + exponent
    if k <= n <= _MAX_PLACED_EXPONENT:
        return digits + "0" * (n - k)
    if 0 < n <= _MAX_PLACED_EXPONENT:
        return f"{digits[:n]}.{digits[n:]}"
    if _MIN_PLACED_EXPONENT < n <= 0:
        return "0." + "0" * -n + digits
    power = n - 1
    sign = "+" if power >= 0 else "-"
    mantissa = digits if k == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{mantissa}e{sign}{abs(power)}"


def _write(value: object, out: list[str]) -> None:
    # bool before int: bool is a subclass of int in Python and must stay a JSON boolean.
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(_escape_lone_surrogates(json.dumps(value, ensure_ascii=False)))
    elif isinstance(value, float):
        out.append(ecmascript_number(value))
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJsonError(
                "canonical JSON refuses an integer beyond Number.MAX_SAFE_INTEGER: JavaScript "
                "holds the nearest double, not this integer"
            )
        out.append(str(value))
    elif isinstance(value, list):
        out.append("[")
        for position, item in enumerate(value):
            if position > 0:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise CanonicalJsonError("canonical JSON requires string object keys")
        out.append("{")
        items: list[tuple[str, JsonValue]] = [(key, value[key]) for key in value]
        for position, (key, child) in enumerate(sorted(items, key=_utf16_key)):
            if position > 0:
                out.append(",")
            out.append(_escape_lone_surrogates(json.dumps(key, ensure_ascii=False)))
            out.append(":")
            _write(child, out)
        out.append("}")
    else:
        raise CanonicalJsonError(f"canonical JSON cannot encode {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialise ``value`` the way ``canonicalJson()`` in ``src/lib/hash.ts`` does."""
    out: list[str] = []
    _write(value, out)
    return "".join(out)


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
