"""The narrative fidelity check, re-implemented in Python against the golden vectors."""

from .normalize import (
    NORMALIZATION_VERSION,
    REQUIRED_UNICODE_VERSION,
    NormalizationError,
    find_forbidden_character,
    normalize_text,
)
from .verify import (
    FidelityError,
    compute_narrative_binding,
    normalize_narrative,
    verify_narrative_fidelity,
    verify_report_hash,
)
from .xhtml import XhtmlError, xhtml_to_text

__all__ = [
    "NORMALIZATION_VERSION",
    "REQUIRED_UNICODE_VERSION",
    "FidelityError",
    "NormalizationError",
    "XhtmlError",
    "compute_narrative_binding",
    "find_forbidden_character",
    "normalize_narrative",
    "normalize_text",
    "verify_narrative_fidelity",
    "verify_report_hash",
    "xhtml_to_text",
]
