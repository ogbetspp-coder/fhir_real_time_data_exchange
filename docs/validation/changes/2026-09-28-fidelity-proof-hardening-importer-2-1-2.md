# Recorded change: the fidelity proof's hardening, importer 2.1.2, 2026-09-28

**What changed.** No normalised text, extracted text, status, reason, coverage figure or hash of
any accepted input changes: `NORMALIZATION_VERSION` stays `fidelity-norm/3.1.0`, and none of the
634 golden vectors moves.

- **Linear verification** (`src/fidelity/verify.ts`, `zone-a/src/zone_a/fidelity/verify.py`).
  Each page's index now holds, per code point of the body, whether its line holds U+0009, and
  where a slice starting there is read from (`fromLineStart`). The line reads of the slice rules
  are then O(1). A gap between spans is normalised as U+000A, U+0009 if the whitespace before it
  on its line holds one, and the gap itself, not the whole whitespace run back to the line break:
  that run can change only whether the line holds U+0009 (step 4), and every other code point in
  it is a space step 5 collapses. `coverage` groups the verified spans by page once.
- **Bounds at the document gate** (`src/contracts/canonical-submission.ts`). A provenance section
  may carry at most 1,000 spans and a submission 10,000 (structural invariants, so the parse
  refuses more), and a verified narrative at most 8 MiB in UTF-16 code units, refused before any
  scan. The JSON Schema does not change, so `CanonicalSubmission` stays 2.0.0.
- **One scan fewer per section.** `verifyNarrativeFidelity` builds `narrativeBindingSha256` from
  the per-section hashes of its own loop rather than normalising every narrative again; the value
  is the one `computeNarrativeBinding` gives (a test holds every verify vector to that).
- **The composition check's fast path** (`src/fidelity/xhtml.ts`, `xhtml.py`). The window on each
  side of an inline tag is no longer composed when the code point after the tag is below U+0300,
  U+00AD aside: every such code point is a starter NFC leaves alone and never composes with what
  precedes it, so the comparison could only agree. `test/fidelity-composition.test.ts` and
  `zone-a/tests/test_composition_boundary.py` prove it over each runtime's Unicode data (every
  code point below U+0300 is its own NFC form of combining class 0; no code point's NFD form has
  one after its first). Six hundred thousand empty tags scanned in about 9 s (22 s in Python)
  before, 0.6 s (3 s) now.
- **Error offsets.** An `XhtmlError`'s offset is a code point offset into the div for every code
  in both languages (the TypeScript's tag errors used UTF-16 indexes, and a
  `combining-across-markup` error an offset in the scanned text). No vector records an offset.
- **Keys and versions that are not strings** (`verify.ts`, `verify.py`). A source key of a section
  or a provenance entry that is not a string is refused ("Source key is invalid", "Source section
  0 has no string key"), and a normalisation version that is not a string is reported as "received
  a value that is not a string", before any is written into a string or compared. Python's
  f-strings wrote `True`, `None` and `3.0` where JavaScript writes `true`, `null` and `3`, and its
  dictionaries merged the keys `1` and `true`, so the two reports differed.
- **The `codePoints` vector family** (`test/fixtures/fidelity/code-points.ts`, generated into
  `test/fixtures/fidelity/vectors.json`). For every code point U+0000–U+10FFFF, its classes under
  every closed list of `normalize.ts` and `xhtml.ts` (forbidden, whitespace, gap,
  Default_Ignorable, word character, removed by step 1, expanded, bullet, reserved, grid marker,
  invisible break, mark, and what `sup` and `sub` fold or refuse), run-length encoded (2,199
  runs), and the 30 code points `sup` or `sub` folds. `test/fidelity-code-points.test.ts` pins the
  table's hash to `fidelity-norm/3.1.0`, so a list change fails until it is given a version of its
  own. `zone-a/tests/test_code_points.py` reads each class through the port's own functions, and
  `agent/tests/test_quote_edge_code_points.py` holds the agent's copy of the gap and word classes
  to the table. To support this, `xhtml.ts` exports `scriptCodePoint` (what `emitText` applies
  inside `sup` and `sub`, unchanged) and `isInvisibleBreak`; the Python port has
  `script_code_point` and `is_invisible_break`.
- **The differential.** CI adds 2,000 cases at the run's own seed (its run id) to the 2,000 at
  20260920; the generator prints the seed, which every case records. A new class,
  `non-string-keys`, drawn from a stream of its own so every other case at a seed is unchanged.
  `.github/workflows/parity-sweep.yml` runs the exhaustive per-code-point sweep nightly
  (`scripts/fidelity/parity-sweep.ts`, `zone-a/tests/test_parity_sweep.py`).
- **Importer 2.1.2.** `src/fidelity/xhtml.ts` is under the importer lock, so `IMPORTER_VERSION`
  moves from `2.1.1` to `2.1.2` and `npm run authority:lock` records it. The importer's output
  changes only in its extractor name (`authority-import/2.1.2`).
- **Stale text.** The comments on the edge rules' whitespace (`verify.ts`, `verify.py`) and on
  the thin spaces (`normalize.py`), `zone-a/README.md` (the float rule, the whitespace class and
  bullet count of the differential alphabet), and the extractor spike's two copies of the 2.0.0
  whitespace class (`scripts/spikes/document-ai/report.ts`, which now imports `isWhitespace`).
  The Python package no longer re-exports `count_words`, `is_whitespace` and `is_word_character`,
  which nothing imported from it, and `xhtml.py`'s copy of `SOFT_HYPHEN` is gone (`verify.py`
  takes `normalize.py`'s).

**Why.** The audit of 2026-09-27 (batch B09):

- **F-1.** `lastLineHasTab` walked to both ends of the line for every gap and every verified span,
  and `fromLineStart` walked back through the whole whitespace run. Twenty thousand one-space spans
  across one whitespace line took about 50 s (TypeScript) and minutes (Python); a few-MB
  submission could hold the worker for minutes during Zone B's re-execution. The contract set no
  maximum span count.
- **F-2.** Dropping U+180B–U+180F and U+1D173–U+1D17A from `DEFAULT_IGNORABLE` and U+205F from
  `THIN_SPACES` moved no vector and failed no differential case, and the mutated build answered
  `verified` where the real one answers `word-cut`.
- **F-3.** The composition check dominated the scan of markup-heavy text, and each narrative was
  normalised twice by the verifier.
- **F-4.** CI ran one fixed seed; the differential's docstring claimed three seeds and 130 vectors.
- **F-5.** The report hash differed between the twins for a non-string key; error offsets mixed
  units.
- **F-6.** The stale comments, README lines and spike tables above.

**Impact assessment (step 0).**

- `src/fidelity/verify.ts`, `xhtml.ts`: the document gate, the authority importer and its gate,
  the synthetic fixture, the query service (`xhtmlToText`, `normalizeText`) and the demo. Every
  accepted and refused input gives the same text, status, reason, report and hash; only an
  `XhtmlError`'s offset (recorded nowhere) and the structural refusal of a non-string key or
  version change. Checked beyond the vectors: the old and new TypeScript give byte-identical
  differential corpora at six seeds (18,000 cases and their lowered-half cases), identical reports
  on 300,000 random pages of whitespace, tabs, bullets, soft hyphens and marks with random spans,
  and identical text or error code for every code point in five scanner contexts; the new Python
  reproduces the old TypeScript's reports on 7,882 of those pages.
- `src/contracts/canonical-submission.ts`: the document gate and the authority gate. The gate
  accepts less (above); the schema is byte-identical.
- `src/authority/`: importer 2.1.2; the three authority vector hashes move with the extractor
  name only.
- `zone-a/`: the port changes with the TypeScript, in the same pull request.
- `agent/`: a test only. `quote_edge.py` is unchanged; its copies of the lists agree with the
  table at every code point.

Previously produced evidence: every report and hash reproduces. A recorded authority import under
importer 2.1.1 re-verifies only with a 2.1.1 worker (D10), as for every importer version.

**Steps 1–6.**

1. `IMPORTER_VERSION` is `2.1.2`. `NORMALIZATION_VERSION` does not change (section 8: no vector's
   outcome, text or hash changes).
2. `test/fixtures/fidelity/vectors.json` gains the `codePoints` family; `test/fixtures/authority/vectors.json`
   and `src/authority/importer.lock.json` are regenerated.
3. No existing fidelity vector changed. The three authority vector hashes changed with the
   extractor name only.
4. Tests, each failing against the code before this change:
   - `test/fidelity.test.ts`: "verifies many spans on one long line in linear time", "refuses a
     source key or a normalisation version that is not a string", "reports every error at a code
     point offset into the div", and "binds the narratives it scanned, as computeNarrativeBinding
     does" (a guard on the refactor).
   - `test/fidelity-composition.test.ts`: "is not composed" (no window at a stable boundary); the
     proof that the boundary is stable.
   - `test/fidelity-code-points.test.ts`: the table and its pinned hash (both fail when U+205F
     leaves `THIN_SPACES`, which was tried).
   - `test/contracts/canonical-submission.test.ts`: "bounds the spans of a section and of the whole
     provenance", "bounds a narrative's length before scanning it".
   - `zone-a/tests/test_verify_hardening.py`, `test_composition_boundary.py`, `test_code_points.py`
     (fails at U+205F when it leaves the port's `THIN_SPACES`, which was tried), and the
     differential's `non-string-keys` class (24 cases at seed 20260920, each failing against the
     old port).
5. ADR 0002's invariants could name the span and narrative bounds; not amended here (the
   documentation batch owns the ADRs' wording).
6. None changed here.

**Blast radius.**

- A submission whose provenance carries more than 1,000 spans in a section or 10,000 in all, or
  whose verified narrative is longer than 8 MiB, is refused at the gate.
- A fidelity input whose source key or normalisation version is not a string throws
  `FidelityError` in both languages; the contract already required strings, so no submission that
  parsed is affected.

**Step 7.** Not applicable: `CanonicalSubmission` and the normalisation version stay as they are,
and no approved content changes.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
