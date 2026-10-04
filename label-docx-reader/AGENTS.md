# Agent instructions

This repository is a reader whose one job is to give the exact text of a label (a Word .docx, or
an EMA ePI) or refuse. Every rule below serves that.

- **Fail closed.** When the reader meets something whose text it cannot produce exactly, it
  raises `DocxRefusedError` with a code. Never add a fallback that keeps going: a reader that
  skips what it does not understand is how a label loses a character without anyone noticing.
- **Never normalise.** No Unicode normalisation, whitespace trimming, quote straightening, case
  change or "cleanup" of text. Appearance that changes meaning is a mark over the text, never an
  edit to it.
- **Every rule has a test.** A new rule or refusal comes with a minimal in-memory .docx in
  `tests/test_reader.py` that fails without it. A new Symbol or Wingdings mapping is a reviewed
  change with a test.
- **List numbering follows Word, not the specification.** A change to how labels are counted or
  drawn starts with a case in `scripts/numbering_cases.py` and Word's answer to it
  (`scripts/word_oracle.py record corpus/numbering-cases`, macOS with Word). Where Word's answer
  is not on record, refuse.
- **ePI follows the browser.** A change to how an ePI section is read is held to headless
  Chrome (`scripts/browser_oracle.py record corpus/ema-epi`); where the browser's answer is not
  on record, refuse the section.
- **Never serve an uncertified read.** Every result passes `label_docx.certify`, which reads
  the source on its own; never make it share logic with the readers, and never let it accept
  more than one reading of a token. A change it does not catch is a missing check, not a flaky
  test, and every mutant of it (`scripts/mutate_checker.py`) must be killed or recorded as
  equivalent with the reason.
- **Trace every test.** `docs/requirements.md` names, for each requirement, the tests that prove
  it; a new test goes there, and `tests/test_traceability.py` fails on a test that proves nothing
  or a requirement that names a test that is gone.
- **Version every change.** `versions.lock.json` ties each version to the files that decide it
  (`scripts/lock.py`, `current_versions`): `reader.py` to `READER_VERSION`; `output.py` and
  `certify.py` to `label-docx-json`; `epi.py` to the ePI reader; `epi_output.py`, `output.py`,
  `documents.py` and `certify.py` to `label-epi-json`. Bump every version whose files changed,
  run `scripts/lock.py` and review the diff of `corpus/*/expected.json`. Never re-lock a version
  to other code; a version in any lock on main (released) is never changed or dropped.
- **Public or synthetic documents only** in `corpus/` and in tests, each set with a
  `sources.json`. No client or confidential labels in the repository.
- **Tests never print label text.** A failure names a file, a code or a digest.
- **The store is write-once.** Never add a code path that rewrites or deletes a kept source,
  result or receipt; a new reader version adds results beside the old ones.
- **No runtime dependencies.** The standard library only; development tools are pinned exactly
  in `pyproject.toml` and `uv.lock`.

Run the five checks in the README before calling a change done.
