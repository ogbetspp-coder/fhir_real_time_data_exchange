# ADR 0002: Two trust zones and the CanonicalSubmission contract

- Status: Accepted for prototype
- Date: 2026-09-19

## Context

Most product information still exists as authored documents. Converting it into the canonical
Type 2 graph (ADR 0001) is the entry point for every downstream projection, and it cannot be
fully deterministic: locating section boundaries, metadata, and codes in a document requires
judgement that today is best delivered by AI-assisted extraction with human review.

The non-negotiable rules of this repository forbid generating, summarising, or inferring
regulated narrative and require fail-closed, deterministic, evidenced transformations. An LLM
structuring step satisfies none of these on its own. The two concerns must therefore be
separated by a boundary that is enforced mechanically rather than by instruction.

## Decision

The system is split into two trust zones with one versioned contract between them.

**Zone A — Structuring** (separate service; probabilistic, verifiable). Converts an existing,
approved label document into a Type 2 document Bundle. It may decide section boundaries,
metadata, and codes. It may never author, alter, reorder, or omit narrative words. Every code
it assigns must cite a terminology lookup. Its output is a proposal until a human approves it.

**Zone B — Publishing** (this repository; deterministic). Accepts only an approved
`CanonicalSubmission`, re-verifies the invariants below, and then runs the unchanged
transform, validation, persistence, and evidence pipeline. `src/fhir/transform.ts`,
`src/fhir/preflight.ts`, the mapping manifest, and the generated artifacts are not modified by
this decision; their determinism and hashes are frozen.

**The contract** (`src/contracts/`, Zod as the single source of truth; JSON Schema generated
into `contracts/generated/` and enforced by `npm run contracts:check`):

- `CanonicalSubmission` = `schemaVersion` + the Type 2 Bundle + `IngestionProvenance` +
  `Approval`, plus hashes of the Bundle and of the approved content.
- `IngestionProvenance` records the source document (byte hash, media type, size, optional
  source-system identity, required reference to the extracted text), the extraction tooling
  (run id, service version, parser, optional model, prompt template, terminology service), one
  provenance entry per narrative section (source spans and hashes), every structuring decision
  classified as `extracted-verbatim`, `code-mapped`, `defaulted-by-rule`, `human-edited`, or
  `rejected`, and the fidelity summary.
- `Approval` records an opaque approver id (never an e-mail address), role, timestamp, method
  (`api-attestation` or `manual-record`), an enumerated meaning
  (`reviewed-fidelity-and-structure`), and `approvedContentSha256`, the hash of
  `{ schemaVersion, bundle, provenance }` exactly as approved.

**Invariants Zone B enforces at ingress, before any transformation** (fail closed):

1. `bundleSha256` equals the canonical hash of the received Bundle.
2. `approval.approvedContentSha256` equals the recomputed hash of the received content.
3. The fidelity report parses against its own contract schema, is `passed`, recomputes its
   `reportHash`, was computed over the referenced extracted text (`extractedTextSha256`), and
   its narrative binding hash equals the one recomputed from the Bundle alone. Zone B then
   **always** re-executes the full fidelity check against the extracted text and requires the
   identical `reportHash` (ADR 0003): a report is a claim, the re-execution is the proof, and a
   submission whose extracted text is unavailable is rejected.
4. Sections carrying the canonical source code system and provenance entries are in bijection,
   each section's `narrativeDivSha256` matches its `text.div`, and the Composition's section
   tree is well formed.
5. Conditional decision fields are present (`ruleId` for defaults, `terminologyRef` for codes,
   `editorId` and `reason` for edits and rejections), and a terminology service is declared
   whenever any decision is `code-mapped`.
6. No free text exists anywhere in the Bundle outside the verified narratives: any other
   `text.div` rejects, as does any string longer than 300 characters, containing `<`, or longer
   than 20 words by ICU word segmentation, any property name that is not an identifier, and any
   Bundle whose unverified strings exceed the aggregate budget (3,000 strings, 40,000
   characters, 500 entries) or whose JSON nests deeper than 48. Product graph fields are names,
   codes, identifiers, and URLs; regulated prose must travel as a verified section. Field-level
   spans for structured strings are a known extension, not yet implemented.

**Transport.** Submissions are passed by reference (`{ uri, sha256 }`), never inline: real
bundles are multi-megabyte, orchestration payload limits are small, and request bodies transit
logs. Extracted source text travels separately by reference and is never logged.

A request names one object (`RunRequest`, `contracts/generated/run-request.schema.json`); that
submission names its own fidelity report and extracted text, each with the hash the resolver
must reproduce, so a single caller-supplied hash pins the whole hand-off.
`src/gcp/submission-reader.ts` resolves the chain and proves only that the objects it fetched
are the objects that were named: reads are confined to one configured bucket
(`SUBMISSION_BUCKET`), object size is capped, JSON depth is bounded before anything is hashed,
and every failure is a closed reason code carrying no document content. Whether a submission
_should_ be published stays entirely with `verifyDocumentSubmission`, which re-executes the
fidelity check whichever way the parts arrived. Status: the reader, the `/v1/runs` `document`
route, and the Workflows `document` branch exist; no Zone A service produces submissions yet,
so in practice the only producer is `src/fixtures/synthetic-submission.ts`.

The `fixture` and `healthcare-api` sources are pre-existing trusted inputs guarded by IAM, not
by this gate; deployments where Zone A is the only producer should disable them.

**Versioning.** Every contract root carries a `schemaVersion` literal and a `$id` that embeds
it. Objects are strict (unknown keys reject) so content cannot be smuggled in unnamed fields.
Patch changes alter descriptions only; minor changes add optional fields and require Zone B to
deploy before Zone A emits them; anything else is a new major `$id`. Enum additions on fields
that Zone B branches on are major. `zod` is pinned exactly to keep generated schemas stable.

**Offsets.** Span offsets are Unicode code points; span hashes are SHA-256 of the UTF-8 bytes of
the raw slice. See ADR 0003.

**String lengths.** A string bound in a contract is published as JSON Schema `maxLength`, which
counts Unicode code points. The TypeScript reference enforces the same bound in UTF-16 code
units, which is never fewer than the code points, so the reference is the stricter side: a value
it accepts is always within the published limit, and a value built against the schema alone
may carry astral-plane characters that Zone B then rejects. Zone B validates with the
reference; a producer that must agree with it exactly counts UTF-16 code units (in Python,
`len(value.encode("utf-16-le")) // 2`). No bound in any contract is close enough to a real
value for the difference to matter today; it is recorded so that it is never a surprise.

## Consequences

- No `document`-source content can reach the FHIR store or the evidence bucket without a
  hash-bound human approval and a passing mechanical fidelity check; UR-09 to UR-17 in
  `docs/validation/README.md` trace these controls to tests. The `fixture` and
  `healthcare-api` sources bypass this gate entirely and are controlled by IAM alone. A
  deployment that handles anything but synthetic content must disable them by setting the
  worker's run-source allowlist (`ENABLED_RUN_SOURCES`, Terraform `enabled_run_sources`) to
  `document`; until it does, the sentence above describes the `document` path, not a property
  of the deployed system.
- `RunManifest` moves to `schemaVersion` 1.1.0 with an optional `ingestion` block containing
  hashes, counts, enumerations, and identifiers only. Version 1.0.0 remains readable.
- A Zone A implementation in another language must generate its models from
  `contracts/generated/` and prove fidelity-check equivalence against the golden vectors.
- Real electronic signature, identity-provider binding of `approverId`, segregation of duties,
  and signature-to-record binding beyond hash reference are future control boundaries. Nothing
  here claims Annex 11 or 21 CFR Part 11 compliance.
- Strict objects make every minor contract change a Zone B-first deployment.
- The fidelity check proves the narrative sections only. Every other string in the Bundle
  (titles, display names, identifiers, property names) is bounded at ingress — token- or
  grammar-limited where it is an identifier or URL, and otherwise at most 300 characters and 20
  words (ICU word segmentation, so scripts without inter-word spaces are counted too) with no
  markup, at most 3,000 such strings and 40,000 characters per Bundle, at most 500 entries, and
  JSON nesting at most 48 deep — which limits how much unverified text can travel but does not
  prove it. Binding structured fields to master data with field-level provenance is the next
  control boundary.
- Provenance identifier fields (tool names, versions, model and prompt ids, decision targets,
  editor ids, `approverId`, `Bundle.identifier.system`, `fullUrl`) are token- or grammar-limited
  so that a manifest, ledger row, or Provenance resource can never carry prose; `recordRef` is a
  single URL-safe locator.

## Amendment (2026-09-24, ADR 0005: authority imports)

Roadmap item 3a lets an authority's published ePI enter the record (ADR 0005). The design is
`docs/design/authority-import-contract.md` (D1–D14); this section states what it changes here.
The decision above is otherwise unchanged, and so are the `drawn` path's invariants 1 to 6.

**The contract is `CanonicalSubmission` 2.0.0.** Every 1.0.0 submission is refused.

- `graphType` (`type1` | `type2`) is part of the submission and of the approved content:
  `approvedContentSha256` is the hash of `{ schemaVersion, graphType, bundle, provenance }`.
  `type1` goes only with an authority publication, and an authority publication is always
  `type1` in 2.0.0 (D9). The Bundle definition `Type2Bundle` is renamed `CanonicalBundle`.
- `provenance.sourceDocument` is a union on `kind`: `drawn` (the fields above, PDF or Word) or
  `authority-publication` (`application/fhir+json`; the authority, `EMA` or `synthetic`; the
  import request; the document and its List pinned by id, SHA-256 and length; the List's ePI id,
  version number, `meta.versionId` and status; the pictures the document references; one page
  per section, `sectionPages`; the extracted-text reference) (D3).
- `approval` is a union on `method`: an attestation (`api-attestation` | `manual-record`, the
  fields above) or `authority-publication` (meaning `authority-publication-imported`, the
  authority, `authorityStatus: pilot`, the publication it names, `requestedBy`, `requestedAt`,
  `approvedContentSha256`) (D8).
- `Bundle.identifier.value` is required for every source; a structuring decision may name the
  field it read (`sourceField`, a grammar-limited `SourcePath`).

**New ingress invariants**, after the six above:

7. Source, graph, approval and extractor fit together: a `drawn` source carries a `type2` graph
   and an attestation; an `authority-publication` source carries a `type1` graph, an
   `authority-publication` approval, the extractor `authority-import` and no model or prompt
   template, and its request, source and approval name the same authority, document, List, ePI
   and version. The extracted text's `extractorVersion` is the parser's `name/version`.
8. **For an authority import, Zone B fetches the authority's bytes itself and recomputes the
   import** (D1). It builds each URL from the submission's ids by a fixed template for the
   authority, on an allowlisted host, with one `Accept` header, no redirect followed, HTTP 200
   only, a 30-second timeout and a 4 MiB limit; requires the pinned SHA-256 and length; runs the
   importer this build contains (`src/authority/`) on those bytes with the submission's free
   fields; and accepts only the very submission, page text and fidelity report it makes. The
   ordinary gate then accepts an authority import only with that proof bound to the submission's
   hash (`GateOptions.recomputedImport`, set only by `src/authority/gate.ts`). An import requested
   after the gate fetched the files refuses. Until roadmap 3a PR 5 an authority import runs only
   as a dry run: the gate refuses one when `DRY_RUN` is false, so nothing it makes is persisted.
9. **The raw-bytes exception.** The authority's files are hashed as the raw bytes served (after
   HTTP content decoding), never as a re-serialised JSON value: the one exception to this ADR's
   hash-the-JSON-value convention. They are decoded as strict UTF-8 (an invalid byte or a
   byte-order mark refuses) and parsed with a parser that refuses a duplicate key.
10. **Synthetic content only where `ALLOW_SYNTHETIC_SOURCES`** (D7). A synthetic submission
    carries every mark of one and a non-synthetic one none: for a `drawn` source a `synthetic-*`
    extractor, terminology service (if any) and identifier value; for an import the `synthetic`
    authority and the identifier value `authority-import:synthetic:<id>`; for both, the marker
    "not for clinical use" in every narrative that carries text. With the flag off, any mark
    refuses.
11. **No drawn-document extractor is qualified** (`docs/fidelity-normalization.md` §7): a `drawn`
    submission is accepted only as a synthetic one, where the flag is on.
12. **A structured source's pages.** An authority import's page text has one page per section,
    each wholly body, and every page without a span is blank: the re-executed report's
    `coverage.uncoveredGaps` is 0 (D4).
13. **The reserved namespace.** An identifier value beginning `authority-import:` is written only
    by the importer, and every other route (the `drawn` gate, and the `fixture` and
    `healthcare-api` sources in the pipeline) refuses a source Bundle that has one (D7).

**The human decision for an import is its request** (D2): which publication to import, in which
language. The request is an input to the import and part of the approved content, and the
approval names who made it (`requestedBy`, a placeholder like `approverId` until roadmap item 2).
Whether the words are right is what invariant 8 proves, not what a person attests.

**The transform and the preflights change for this purpose.** The decision above said
`src/fhir/transform.ts`, `src/fhir/preflight.ts`, the mapping manifest and the generated
artefacts are not modified and their hashes are frozen. For authority imports they are, and stay
deterministic:

- the crosswalk derives every persisted id from the checked identifier value: each entry after
  the Composition gets `stableUuid("ema-entry:" + resourceType, identifierValue + ":" + position)`
  and a `urn:uuid` fullUrl, references between entries are rewritten, a reference that names no
  entry of the Bundle refuses, and the fallback to `Bundle.id` or a hash is gone (D7);
- `validateType2Preflight` is called through `validateCanonicalPreflight(bundle, graphType)`,
  whose Type 1 set is exactly one Composition, MedicinalProductDefinition, Organization and
  RegulatedAuthorization, linked, named and identified; Type 2 is unchanged (D9);
- the mapping (`cap-smpc-en` 1.3.0) lists the headings the QRD template permits without their
  optional wording (6.5, 6.6) and the SPOR alias of its code system; the crosswalk keeps a
  permitted source heading and the EMA preflight accepts any (D4);
- the EMA List carries the holder, regulatory agency and procedure number the graph states in
  their identifier systems, and is titled by the product's name (D11).

**Run sources.** The consequence below that a deployment handling anything but synthetic content
runs `document` only is amended. `ENABLED_RUN_SOURCES` now follows `ALLOW_SYNTHETIC_SOURCES`
(default false in `src/config.ts` and Terraform): with the flag off it defaults to `document`,
and enabling `fixture` or `healthcare-api` is a startup failure (Terraform refuses the two
variables disagreeing). With the flag on, the ungated sources may run beside public authority
imports, which they cannot reach: every id a run persists, and every reference in it, derives
from its checked identifier value, and no route but the importer's may use the
`authority-import:` namespace (D7). The `dev` deploy sets the flag. A deployment that holds a
client's content leaves it off.

**Evidence.** `RunManifest` moves to 2.0.0; 1.0.0 and 1.1.0 stay readable. Its ingestion block
records the source kind, the graph type, whether synthetic content was allowed, the approval as
either union member, and for an import the importer version and each fetched file's URL,
SHA-256, length and fetch time (D12). The FHIR Provenance id is
`stableUuid("ingestion-provenance", identifierValue + ":" + submissionId)`, and its targets are
the record's identifier and the output's `Composition/<id>` and `Bundle/<id>`, never a fullUrl
the submission chose. An import's Provenance has activity `authority-import`, the authority as
agent of its source files (each named by the authority's id and by hash), the requester as
`enterer`, and `recorded` at the gate's fetch time.

**Status.** Producers are `src/fixtures/synthetic-submission.ts` and, for authority imports,
`scripts/authority/import.ts`, whose identity is not trusted (invariant 8).
