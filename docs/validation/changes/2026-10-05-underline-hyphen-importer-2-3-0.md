# Recorded change: a hyphen under an underline, importer 2.3.0, 2026-10-05

**What changed.** The shared underline rule (`zone-a/src/zone_a/underline.py`,
`src/authority/underline.ts`, held to the same cases in
`test/fixtures/authority/underline-cases.json`) allowed, where a caller asked
(`hyphens_in_words`, `hyphensInWords`), any dash between two letters under an underline. It now
allows only a hyphen (U+002D, U+2010, U+2011) inside the underlined run, with an underlined letter
on each side. A hyphen at the run's edge ("CL-CR" with only the hyphen underlined, drawn much as
"CL=CR") and every other dash ("⹀", "゠", an en dash, which already look like "=") change what the
text can say, and are refused. Found by the independent review of ADR 0006's narrative builder,
which uses the same rule.

- **Authority importer** (`IMPORTER_VERSION` 2.2.0 → 2.3.0). T already refused every dash under
  an underline but those three hyphens; it now also refuses a hyphen at a run's edge. The
  synthetic publication's outputs change only in the extractor name. Every EMA label pinned in
  `labels/` imports as before.
- **Zone A**: the QRD registry (1.2.3) and check (`qrd-check/1.4.3`), and the Word builder
  (`word-epi/1.0.1`, `word-drawing/1.0.1`), are locked to `underline.py` and move; the registry and
  the five label checks change only in those names. `smpc-structure/1.0.3` moves with the
  registry's file.

**Tests.** Eight new shared cases: the hyphen alone, at the run's start and at its end, inside a
run, and the three other dashes between letters; `zone-a/tests/test_word_epi.py` holds the
builder to them.
