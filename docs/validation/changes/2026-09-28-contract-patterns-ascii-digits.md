# Recorded change: the published contract patterns name ASCII digits, 2026-09-28

**What changed.** Every `pattern` in `contracts/generated/*.schema.json` that said `\d` now says
`[0-9]`. Six contracts are affected:

- `canonical-submission`
- `ingestion-provenance`
- `fidelity-report`
- `query-tools`
- `agent-turn`
- `run-manifest`

Their regenerated Zone A models (`zone-a/src/zone_a/contracts/`) and the agent's vendored copies of
`query-tools` and `agent-turn` follow them.

The patterns the contracts write themselves are now spelled with `[0-9]`:

- `HttpUrl`'s port;
- `TargetPath`'s index;
- `PackageRef`;
- `NormalizationVersion`;
- the fidelity report's section path;
- the ingestion provenance's section and decision paths.

The ISO date-time pattern Zod writes for `z.iso.datetime` (`IsoDateTime`) is rewritten to the same
ASCII class as it is generated (`src/contracts/json-schema.ts`).

Generation now refuses any other shorthand class: `\w`, `\s` and `\b`, their negations, and `\D`.
The published schemas contain none today.

No Zod schema's behaviour changes. In JavaScript `\d` is `[0-9]`, so every `.regex()` accepts
exactly what it did.

**Why.** Audit B07's follow-up, from the second review of #142 (Low-2). Zone A's generated models
validate patterns with Pydantic, which compiles them with Rust's `regex` crate, where `\d` is any
Unicode decimal digit. Zod reads it as `[0-9]`, and so does JSON Schema's own dialect: 2020-12
patterns are ECMA-262 regular expressions, where `\d` is `[0-9]`. So Zone A accepted values that
Zone B refuses. Each of these was accepted by the model generated from main (measured 2026-09-28):

- a package version such as `hl7.terminology.r5#7.3.٣` (U+0663, an Arabic-Indic three);
- a normalisation version such as `fidelity-norm/3.1.３` (U+FF13, a fullwidth three);
- a date-time, a port or a section index written with such digits.

The agent's vendored schemas are read by Python's `jsonschema`, whose `re` also treats `\d` as
Unicode, so the same held there.

**Version classification under ADR 0002: no version changes.** ADR 0002 has three classes:

- patch changes alter descriptions only;
- minor changes add optional fields;
- anything else is a new major `$id`.

The three classes describe changes to a contract: to the set of documents it accepts, or to what it
says about them. This change is neither:

- **Accepted documents:** unchanged. Under the dialect the schemas declare (JSON Schema 2020-12,
  ECMA-262 patterns), `\d` and `[0-9]` are the same language, so every schema accepts exactly the
  documents it accepted before. Zone B, the contract's enforcing reader, accepts exactly what it did.
- **Descriptions:** none changed.

What changes is the spelling of patterns, so that validators which do not implement the declared
dialect (Rust `regex`, Python `re`) compute the language the contract always meant. That is the
correction of a generator defect, not a contract change. So no version moves, and no `$id` moves.
The contract index's content hashes do move, and the index records them. So the same `$id` now
names different bytes: an outside verifier that pinned the hashes in `contracts/generated/index.json`
will see them change for these six contracts, with no change of version to announce it.

A bump would be harmful as well as unrequired:

- `CanonicalSubmission`'s `schemaVersion` is part of `approvedContentSha256`. Any change to it,
  patch included, would refuse every submission made against 2.0.0 and require every approval to be
  made again, for a change that alters no accepted document.
- The run manifest, the query tools and the agent turn would each cut a version for no difference
  a reader could observe.

Nothing Zone A emitted can have relied on the wider acceptance. Zone B refuses such values at the
gate, so no approved submission, manifest or audit record holds one.

The ADR's text does not name this class of change. Its amendment belongs to the documentation batch
(B17): "a change to a schema's text that leaves the set of documents it accepts, under the dialect
it declares, and its descriptions unchanged, is not a version change".

**Impact assessment (step 0).**

- Readers of the published schemas:
  - Zone A's generated models, now narrower, to Zone B's language;
  - the agent's vendored schemas, now narrower, to the same;
  - outside verifiers.
- No fidelity vector, contract fixture, golden vector, importer vector or approved hash moves.
  `npm run contracts:check` regenerates all of them without a difference, apart from the schemas
  and the index.
- Neither the importer lock (`src/authority/**`, `src/fidelity/normalize.ts`,
  `src/fidelity/xhtml.ts`) nor the Zone A versions lock covers any file this change touches.

**Steps 1–6.**

1. No contract version changes (argued above).
2. `contracts/generated/*.schema.json` and `index.json`, `zone-a/src/zone_a/contracts/*.py` and
   `agent/src/verifiable_answer_agent/contracts/*.schema.json` are regenerated.
3. No fidelity vector changed.
4. Adversarial tests:
   - `test/contracts/ascii-digits.test.ts`: Zod accepts ASCII digits and refuses an Arabic-Indic
     and a fullwidth digit in `PackageRef`, `NormalizationVersion`, `IsoDateTime`, `HttpUrl`,
     `TargetPath`, `SectionPath`, `SourcePath` and `SectionResult.path`.
   - `zone-a/tests/test_ascii_digits.py`: the generated models give the same answers. They failed on
     main's models, which accepted them.
   - `test/contracts/schema-generation.test.ts`: no published pattern uses a shorthand class. The
     rewrite handles `\d` inside and outside a character class, leaves an escaped backslash alone,
     and refuses every other shorthand. It walks the whole generated document, so the patterns Zod
     emits under `allOf` for a string with two `.regex()` calls are rewritten and refused too (the
     review's L2-b; no current contract has one).
   - `zone-a/tests/test_ascii_digits.py`: no generated model's pattern uses one either.
5. ADR 0002's rule is unchanged. Its amendment, naming this class of change, is listed for B17.
6. None.

**Blast radius.** A Zone A or agent caller that sends a value with non-ASCII digits in one of these
fields is now refused by its own model, as Zone B already refused it.

**Step 7.** Not applicable: no approved content changes.

**Approval (step 8).** Not obtained: the author and the releaser are the same identity (see
"Release criteria").
