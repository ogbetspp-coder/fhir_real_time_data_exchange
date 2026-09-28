"""Zone A.

Consumes the published Zone B contracts (``contracts/generated/``), proves cross-language
canonical-hash parity, and re-implements the narrative fidelity check against the golden
vectors. Reads Word bodies (``zone_a.docx``) and EMA ePI Bundles (``zone_a.epi``) exactly or with
a refusal, builds the QRD template registry and checks labels against it (``zone_a.qrd``), and
decides what an underline can change (``zone_a.underline``). Nothing here authors, alters, or
logs regulated narrative.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
