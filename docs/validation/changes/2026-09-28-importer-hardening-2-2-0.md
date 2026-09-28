# Recorded change: the importer's hardening, importer 2.2.0, 2026-09-28

**What changed.** `IMPORTER_VERSION` moves from `2.1.2` to `2.2.0`. The synthetic publication's
submission, page text and report change only in the extractor name (`authority-import/2.2.0`);
no accepted section's T(div), normalised text or hash moves.

- **Dates at the shape stage** (`src/authority/shape.ts`). `Composition.date` must be a FHIR
  `dateTime` and `Bundle.timestamp` a FHIR `instant` (R5's grammars: a time has seconds and a
  zone), each a day and time the calendar has (the contract's `IsoDateTime`, which has no leap
  second). Anything else refuses as `shape: document-shape`. Before, both were any plain string:
  `next tuesday` reached the record, and a timestamp that is not a date made a submission that
  failed its own contract.
- **The gate checks the importer version first** (`src/authority/gate.ts`). A submission whose
  parser version or extractor is not the gate's own is refused before anything is fetched: "The
  submission was made by another importer version than the gate runs". Before, it cost two
  fetches and a full import and was reported as "not what the importer makes".
- **Pictures read from T's tree** (`src/authority/import.ts`). The pictures stage reads each
  `img`'s `src` from T's tree of the div, its references decoded, not from the tag's text with a
  regular expression that also matched `data-src` (or a `src=` inside another attribute's
  value). A div T cannot read into a tree names no picture; T refuses it at `narrative`.
- **Fetch errors** (`src/authority/fetch.ts`). A timeout before the headers arrive is `timeout`,
  not `unreachable`; a response refused for its status has its body cancelled.
- **T's font-size rules judged once** (`src/authority/t/transform.ts`). The copies at each text
  node and list marker are gone: they judged the very size the element's own check had judged
  (both are drawn in the element's style), so no input could reach them, and deleting either
  element check failed no test. The shifted-ancestor check and the edit's self-check stay as
  defences, with the reason each cannot be reached written beside it.
- **One two-pass routine** (`src/authority/t/document.ts`). `analyseDocument` runs T5's two passes
  once, for `transformDocument` and for the model output's `modelDocument`, which repeated them.
- **One scan per section** (`src/authority/import.ts`). The importer scans T(div) and normalises
  its text once per section and reuses both for the page, the drawn-nothing check and the
  provenance's `normalizedTextSha256` (it scanned four times and normalised twice).
- **Shared lists** (`src/fidelity/normalize.ts`, `xhtml.ts`). The importer's invisible-character
  check is `hasInvisibleFormatting`, step 1's own list (it held a copy, which would have let a new
  entry through); T's block elements are `BLOCK_ELEMENTS` and `br`; the synthetic ids are written
  from `SYNTHETIC_ID_BLOCK`. The scanner's and T's nesting bounds stay separate constants (they
  count at different points of the walk).
- **Dead code.** `ComputedStyle.hasWidthOrHeight`, which nothing read, is gone, and five exports
  nothing imported are no longer exported. Stale comments on pictures and on the rendering stage
  are rewritten.
- **The lock's tooling** (`scripts/authority/lock-hashes.ts`, `lock.ts`). The lock hashes the
  files git tracks under `src/authority` (a `.DS_Store` changed the hash before), and the test and
  `authority:lock` refuse while any file there is neither tracked nor ignored. `authority:lock`
  reads every entry in main's first-parent history, as the test does, not only main's tip, and
  fails closed: without main's commit, on a shallow clone or on any git failure but a commit whose
  tree has no lock, it records nothing (before, any failure read as "nothing released").

**Why.** The audit of 2026-09-27 (batch B10): A-1 (the `rendering` refusal and five T guards had
no test), A-2 (free-text dates, the late version check, the `data-src` match, the fetch errors),
A-3 (the lock's tooling), A-4 (duplicated work and lists), A-5 (stale comments and dead code).

**Impact assessment (step 0).**

- `src/authority/`: the importer and its gate. Only the importer imports it, with the render
  scripts (`scripts/render/`), which call `transformDocument` and `modelDocument` unchanged.
- `src/fidelity/normalize.ts`, `xhtml.ts`: two exports added; no behaviour changes, and
  `NORMALIZATION_VERSION` stays `fidelity-norm/3.1.0` (the code point table's hash does not
  move).
- `zone-a/`, `agent/`: not affected.

Previously produced evidence: a recorded import under importer 2.1.2 re-verifies only with a 2.1.2
worker (D10), as for every importer version; the gate now says so before fetching.

**Steps 1–6.**

1. `IMPORTER_VERSION` is `2.2.0`.
2. `test/fixtures/authority/vectors.json` and `src/authority/importer.lock.json` are regenerated.
3. Changed vectors: the synthetic import's three hashes (extractor name only); Nuvaxovid refuses at
   `narrative: markup`, not `pictures: pictures-not-enabled` (its one picture tag has an extra
   quote, `annotationsrc="…""`, so T reads no tree and names no picture; the QRD check records the
   same broken markup). New import vectors: `ema-shaped` (`rendering: renderer-evidence-missing`),
   `synthetic-date-free-text`, `synthetic-date-not-on-the-calendar`,
   `synthetic-timestamp-without-a-time` (each `shape: document-shape`) and
   `synthetic-picture-source-only-in-data-src` (`pictures: picture-without-a-source`). New T cases:
   `indent-on-inline` (`offset`) and `inherited-indent-restated-on-inline` (accepted).
4. Tests, each failing against the code before this change (or, for a guard, failing when the
   guard is deleted):
   - `test/authority/import.test.ts`: "refuses a date or a timestamp outside FHIR's grammar or the
     calendar"; "pictures, then the narrative" (the `data-src` and in-value decoys, a decoded
     reference); "refuses a publication in the EMA's form at the last stage" and the pinned labels'
     outcomes.
   - `test/authority/gate.test.ts`: "refuses an import another importer version made, and fetches
     nothing"; "refuses an authority's publication for want of the renderer's evidence" (deleting
     the `rendering` refusal fails it and the import test above).
   - `test/authority/fetch.test.ts`: the timeout before the headers; "releases the body of a
     response it refuses by status".
   - `test/authority/t.test.ts`: `indent-on-inline` (deleting the inline indent check fails it;
     with the text node's copies gone, deleting either element font-size check fails
     `text-under-five-points` or `text-under-half-its-block`).
   - `test/authority/lock.test.ts`: "hashes what git tracks, and nothing the importer could load is
     untracked".
   - `test/fidelity-code-points.test.ts`: "hold the importer's invisible-character check to step
     1's list", at every code point.
5. None.
6. None changed here.

**Blast radius.**

- A publication whose `Composition.date` is not a FHIR `dateTime`, or whose `Bundle.timestamp` is
  not a FHIR `instant`, on the calendar, refuses at `shape`. The five pinned EMA labels' dates and
  timestamps all pass (seven-digit fractions and `+00:00` included).
- A submission made by another importer version is refused before the gate fetches.
- An `img` with no `src` attribute of its own refuses as `picture-without-a-source`, whatever
  other attribute names one.

**Step 7.** Not applicable: no contract schema, normalisation version or approved content changes.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
