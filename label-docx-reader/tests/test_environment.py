"""The interpreter is the one the output is pinned to.

The reader decides what is blank with ``str.strip`` and ``str.split``, whose idea of whitespace
comes from the interpreter's Unicode Character Database. Python 3.14 ships Unicode 16.0.0.
"""

from __future__ import annotations

import sys
import unicodedata


def test_the_unicode_character_database_is_the_pinned_one() -> None:
    assert sys.version_info[:2] == (3, 14)
    assert unicodedata.unidata_version == "16.0.0"
