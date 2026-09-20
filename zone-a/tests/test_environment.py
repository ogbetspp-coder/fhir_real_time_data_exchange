"""The runtime pins ADR 0003 requires.

NFC (normalisation step 3) is a function of the runtime's Unicode Character Database, not of the
code. Zone B pins node:22.14.0 / ICU 76.1 / Unicode 16.0; a Zone A runtime on a different UCD
would produce different normalised text from identical input and would be a change to
NORMALIZATION_VERSION even though no line of either implementation changed. So it is asserted,
not assumed.
"""

from __future__ import annotations

import sys
import unicodedata

from zone_a.fidelity import REQUIRED_UNICODE_VERSION


def test_unicode_character_database_is_pinned() -> None:
    assert unicodedata.unidata_version == REQUIRED_UNICODE_VERSION
    assert REQUIRED_UNICODE_VERSION == "16.0.0"


def test_python_minor_version_is_the_pinned_one() -> None:
    # requires-python = ">=3.14,<3.15": the UCD ships with the interpreter, so the minor version
    # is part of the normalisation pin.
    assert sys.version_info[:2] == (3, 14)
