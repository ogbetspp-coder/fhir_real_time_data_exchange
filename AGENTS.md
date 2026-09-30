# Agent instructions

This repository is a reader whose one job is to give the exact text of a label's .docx body or
refuse. Every rule below serves that.

- **Fail closed.** When the reader meets something whose text it cannot produce exactly, it
  raises `DocxRefusedError` with a code. Never add a fallback that keeps going: a reader that
  skips what it does not understand is how a label loses a character without anyone noticing.
- **Never normalise.** No Unicode normalisation, whitespace trimming, quote straightening, case
  change or "cleanup" of text. Appearance that changes meaning is a mark over the text, never an
  edit to it.
- **Every rule has a test.** A new rule or refusal comes with a minimal in-memory .docx in
  `tests/test_reader.py` that fails without it. A new Symbol-font mapping is a reviewed change
  with a test.
- **Version every change.** Changing `src/label_docx/reader.py` means bumping `READER_VERSION`;
  changing `src/label_docx/output.py` means bumping `FORMAT_VERSION`. Then run
  `scripts/lock.py` and review the diff of `corpus/*/expected.json`. Never re-lock a version to
  other code.
- **Public or synthetic documents only** in `corpus/` and in tests, each set with a
  `sources.json`. No client or confidential labels in the repository.
- **Tests never print label text.** A failure names a file, a code or a digest.
- **No runtime dependencies.** The standard library only; development tools are pinned exactly
  in `pyproject.toml` and `uv.lock`.

Run the five checks in the README before calling a change done.
