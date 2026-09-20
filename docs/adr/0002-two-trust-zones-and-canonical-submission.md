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
  deployment that handles anything but synthetic content must disable them (a run-source
  allowlist is the planned control, roadmap "needs a person"); until it does, the sentence
  above describes the `document` path, not a property of the deployed system.
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
