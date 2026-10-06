# Recorded change: `fidelity-norm/3.2.0` → `fidelity-norm/3.3.0`, 2026-10-06

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file._

**What changed.** `NORMALIZATION_VERSION` moved from `fidelity-norm/3.2.0` to
`fidelity-norm/3.3.0` in `src/fidelity/normalize.ts`, `zone-a/src/zone_a/fidelity/normalize.py`
and the agent's `quote_edge.NORMALIZATION_VERSION`. In `docs/fidelity-normalization.md`:

- **Section 5** allows one attribute value: `style` exactly `background-color: silver;`, on
  `span` only. It is the EMA ePI style guide's form for the QRD template's grey "not printed"
  text. A background under text drawn in its own colour hides nothing; any other declaration
  could (`color: silver`, `display: none`), so any other value, element or spelling, a second
  declaration and a repeated attribute still reject (`forbidden-attribute`). Both scanners
  (`src/fidelity/xhtml.ts`, `zone-a/src/zone_a/fidelity/xhtml.py`) compare the value whole.
- **Section 7's certified Word rule** carries the template's grey (a light grey highlight or
  D9D9D9 shading) as that span; writes a list an HTML list cannot draw (labels other than all "•",
  or "1.", "2.", ... from one) as its labels' text, each item a `p` of its label, a space and its
  text, which is the line the page already writes; and writes as U+0020 the tab after a typed
  label (a bullet glyph or dash, one to three of the same footnote mark, or an enumerator with
  its punctuation), where 3.2.0 did so only after a step 4 bullet glyph.

The withheld section of an authority import (`docs/design/authority-import-withheld.md`,
decided, not built), which had taken 3.3.0, moves to 3.4.0; ADR 0002, ADR 0003, ADR 0006 and that
note say so. The authority importer moves to 2.3.2 (`IMPORTER_VERSION`,
`src/authority/importer.lock.json`), because its golden vectors carry the version.

**Why.** ADR 0006, owner decisions of 2026-10-06 (5 to 7). EMA's style guide gives grey "not
printed" text one sanctioned form, and labelling documents are full of it. EMA's stylesheet draws
every `ul` with discs and FHIR allows no `start` or `type` on `ol`, so a dash bullet or "a)"
numbering could only be refused or drawn with another marker; written as its labels' text it
reads as Word draws it. A typed label before a tab is a label, as a typed bullet is.

**Impact assessment (step 0).** Importers of `src/fidelity/`:

- **Worker gate** (`src/pipeline.ts`, `src/contracts/canonical-submission.ts`): refuses a
  submission or report that names another version. A 3.2.0 submission is refused, not
  re-evaluated. No gate admits a certified Word source yet (ADR 0006 P4).
- **Authority importer** (`src/authority/`): unchanged in what it does; 2.3.2, its vectors moving
  only in the version string and the hashes that embed it. An authority import drops styles
  (ADR 0005), so the new value never reaches its output.
- **Query service** (`src/query/tools.ts`): `get_section` and `verify_quote` report the new
  version. Every stored narrative normalises to the same text: none carries a `style`.
- **Agent** (`agent/`): its post-check accepts answers under one version, so its constant moves
  in this change. Deploy order (its README): the agent on Agent Engine is redeployed **before**
  the query service answers under 3.3.0; until then every block is flagged `checksum-mismatch`.
- **Zone A** (`zone-a/`): the port's constant and scanner move; `word-epi/1.3.0` and
  `word-drawing/1.1.0` carry the rules, the drawing check holding the grey to Chrome drawing the
  silver background under exactly the grey characters; the components locked to
  `fidelity/normalize.py` move a patch version.

**Steps 1–6.** 1: `NORMALIZATION_VERSION` is `fidelity-norm/3.3.0` everywhere it is held. 2:
`npm run vectors:generate`, `npm run contracts:fixtures`, `npm run contracts:quote-edge`,
`npm run differential:smoke` and `npm run authority:vectors` regenerated
`test/fixtures/fidelity/vectors.json` (638 → 651: XHTML 383 → 394, verify 184 → 186;
normalisation 71 unchanged), the contract fixtures, the quote-edge export, the smoke corpus and
the importer's vectors; `npm run authority:lock` added importer 2.3.2. 3: below. 4: eleven XHTML
vectors (the grey span accepted, single-quoted too; refused on `p` and `strong`, without its
semicolon, spaced otherwise, in capitals, in another colour, with a second declaration, through a
character reference, and repeated) and two verify vectors (a certified Word page with grey text
and labels written as text against its narrative, passed; the same dashes drawn as an HTML list,
failed), each reviewed by hand (`test/fixtures/fidelity/cases.ts`). The code point table's test
records 3.3.0 with 3.1.0's hash. 5: ADR 0002, 0003 and 0006 amended as above. 6: UR-09 and
`AGENTS.md` name 3.3.0.

**Changed vectors (step 3).** Compared by name with the 3.2.0 vectors: no normalisation vector
changed; no XHTML vector changed outcome and none was removed; the thirteen above were added.
Every other verify vector changed in exactly three fields and no other:
`input.normalizationVersion`, `expected.normalizationVersion` and `expected.reportHash`.

**Differential proof.** The two scanners compare the `style` value as one fixed string, and the
smoke corpus is regenerated; TypeScript and Python agree on every vector
(`zone-a/tests/test_golden_vectors.py`, `zone-a/tests/test_differential.py`).

**Measured** on the EMA SmPC cuts (internal corpus, counts only), by the accepted view with no
drawing check: sections carried 3,545 → 3,682 of 4,032, with 99 labels built as before. Refusals: list-label 64 → 0, formatting 133 → 89 (the grey), tab 173 → 141. Four sections freed from those reach a later refusal for a table these rules do not yet carry (narrative 10 → 12, table-shape 2 → 3). Of the 845 body paragraphs refused for a tab, the typed-label rule carries 228; a bare letter or number, a column, a leading tab or a second tab is still refused.

**Blast radius.** Every approved hash that embeds the version moves (step 7). No accepted text
changes.

**Step 7.** Previously approved submissions require re-approval: the version is part of the
approved content hash. The demonstration's approvals are synthetic placeholders and are
re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity. This change
touches an approved hash, so it needs a quality representative other than the author.
