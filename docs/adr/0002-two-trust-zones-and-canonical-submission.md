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

**Versioning.** Every contract root is published under a `$id` that embeds its version
(`contracts/generated/index.json`). Four also carry the version in the document, as a literal:
`schemaVersion` (`CanonicalSubmission`, `RunManifest`), `reportVersion` (`FidelityReport`) and
`version` (`QueryTools`). `IngestionProvenance`, `SourceDocumentText` and `RunRequest` carry
none, and `AgentTurnRecord` only an optional `contractVersion`, since agent-turn 1.2.0 (corrected
2026-09-28: this said every root carried a `schemaVersion` literal). Objects are
strict (unknown keys reject) so content cannot be smuggled in unnamed fields.
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

## Amendment (2026-09-25, a withheld section)

_Decided, not implemented: `NORMALIZATION_VERSION` is `fidelity-norm/3.3.0` (3.2.0 qualified a
certified Word source and 3.3.0 carries its grey, ADR 0006; this design takes the next minor,
3.4.0) and no contract has a
withheld status. It lands with 3c-W, deferred until after the demo (`docs/roadmap.md`, 3a)._

`docs/design/authority-import-withheld.md` (roadmap 3a, PR 3c; owner decisions of 2026-09-25)
lets an authority import carry a leaf section the renderer gate shows the authority drew unsound
as **withheld**: its heading and code, none of the authority's content, our own `emptyReason` code
and a fixed notice, `Composition.status` `partial`. Invariants 3, 4, 6 and 10 change for that case only:

- **4.** A withheld section still has its provenance entry (no spans; `narrativeDivSha256` the
  notice's hash, `normalizedTextSha256` that of the empty text), so the bijection holds; the
  fidelity counts add `sectionsWithheld`, and `sectionsChecked` is verified plus withheld.
- **6.** The notice is the one `text.div` outside the verified narratives that is accepted, and only
  on a section the recomputed provenance of an `authority-publication` source lists as withheld,
  byte for byte the constant; anywhere else, on any source, it refuses, as does any `emptyReason`,
  and on an `authority-publication` source any `generated` narrative.
- **3.** The binding recomputed from the Bundle takes the recomputed withheld list as its second
  input; a withheld section's entry uses the sentinel fidelity §7 defines (`fidelity-norm/3.4.0`).
- **10.** The notice is not narrative, so the rule that every synthetic narrative carrying text says
  "not for clinical use" does not apply to it; it applies to every other narrative as before.

The human decision that withholds is the import request's, hash-bound in the approved content as
every request is (invariant 2); before PR 5 lifts the dry run it is bound to an attested identity.

## Amendment (2026-09-25, the renderer gate's review)

_Decided, not implemented: `CanonicalSubmission` is still 2.0.0. The run manifest has since moved
to 5.0.0 for other reasons, so the bump below becomes the next major, with the current one
frozen._

`docs/design/authority-import-renderer.md` (R5) and `docs/design/authority-import-withheld.md`
(owner decisions of 2026-09-25) change the contract of an authority import:

- **`CanonicalSubmission` 3.0.0**, a major under this ADR's rule (fields required for an authority
  import; enums the gate branches on): the request's `renderEvidence`, `acknowledgedContacts` and
  `withheld`, and the source record's `rendering` and `withheldSections`, are approved content,
  covered by `approvedContentSha256` (invariant 2) as the request is. The run manifest moves to 3.0.0
  with it, its 2.0.0 ingestion block frozen.
- **Who judges content.** The 2026-09-24 amendment's "whether the words are right is what invariant 8
  proves, not what a person attests" stays true of the words. Two judgements of drawing now rest on
  a person, both stated exceptions: acknowledging a contact the renderer gate cannot prove harmless
  as legible, and withholding a section on a confirmed defect. So a request that acknowledges or
  withholds anything is bound to an attested identity before D1's dry run is lifted for it (PR 5); D8's
  placeholder requester does not suffice, an attested identity is one roadmap item 2 authenticates and records, signing a `request`
  statement in the `content-reviewer` role (`docs/design/approval.md`, amended 2026-09-25), and since the
  tablets label has contacts (as any label with contacts will), it is not persisted until that
  identity exists.

## Amendment (2026-09-28, versions held to their schemas, and numbers)

Audit batch B14 (C-6 to C-10) and the review of #145. The decision above is otherwise unchanged;
the change record is `docs/validation/changes/2026-09-28-contract-versions-lock-and-parity.md`.

**A version names one schema.** `contracts/versions.lock.json` records, for every contract version
`main` has ever published, the structure hash of each schema it was published with: the SHA-256 of
the published document without its `$id`. Retired versions are in it, with every structure `main`
published under them, so a retired version number can never be published again with a schema it
did not have. `test/contracts/versions-lock.test.ts` refuses a schema that differs from the one its
version is locked to, a structure `main` published that the lock lacks, and a lock that drops or
rewrites an entry `main` has released; `npm run contracts:lock` adds a new version's entry. Nothing
held this before: `ingestion-provenance` took a required `kind` and a union (2026-09-24, #113) and
two earlier tightenings under the `$id` of 1.0.0, and only the index's content hash moved. It is
2.0.0 now, the provenance of `CanonicalSubmission` 2.0.0; the documents it accepts are unchanged by
the bump.

**A re-spelling is not a version change** (decided in #145). A change to a schema's text that
leaves unchanged the set of documents it accepts under the dialect it declares (JSON Schema 2020-12,
whose `pattern` is an ECMA-262 regular expression) and every description is not a version change:
`\d` written as `[0-9]` is one. Its `$id` stays, the index's content hash moves, and the lock
appends the new structure hash to the version's entry (`npm run contracts:lock -- --respelling`),
naming the change record that records it; nothing recorded is replaced.

What counts as a re-spelling is decided mechanically, not argued (review of #148, round 2). Compared
with the schema `main` last published under the version, the only difference allowed is in `pattern`
keywords at schema positions (not a `pattern` member of a `const`, `default` or `examples` value,
which is data and must be equal), and two patterns are re-spellings of each other only when they are
equal after a fixed table of rewrites, each known to keep an ECMA-262 pattern's language. The table
has one entry today: `\d` as `[0-9]` outside a character class and `0-9` inside one
(`src/contracts/json-schema.ts`, `asciiDigits`). Adding an entry is a change to this ADR. The check
runs in `npm run contracts:lock` and in `test/contracts/versions-lock.test.ts` against `main`'s
history, whatever the lock says, so an entry appended to the lock by hand licenses nothing. A change
that alters an accepted document, or a description, is classified by the rule above.

**Refinements are published or named.** `z.toJSONSchema` drops every `.refine` silently, so a
generated schema accepted what Zod refuses. Every refinement a published contract carries is listed
in `src/contracts/json-schema.ts` (`REFINEMENTS`) with how many the schema carries, and generation
refuses an unlisted one or a count that differs. Those JSON
Schema can state are published as `if`/`then`/`else` (run manifest 5.0.0: a document run, and only
one, carries an ingestion block; an authority import, and only one, records what Zone B fetched);
the others (`startOffset < endOffset`, the manifest's package rules, the gate's recomputed hashes)
are named with where they are enforced. The conditional fields of a structuring decision could be
stated too; doing so would change `CanonicalSubmission` 2.0.0's published language, so it waits for
3.0.0.

**Python readers read the schema's dialect.** A reader of the published schemas in another language
must reproduce Zod's verdicts, not its own language's defaults. Zone A's models are generated with
strict types and refuse `null` for an absent field; the agent reads `pattern` as ECMA-262 does
(`$` is the end of the string, `.` excludes the line terminators, digits are ASCII).
`test/fixtures/contracts/contract-verdicts.json` carries Zod's verdicts on values and documents, and
both Python suites reproduce every one.

**Numbers.** A JSON number is its value: an IEEE 754 double, as RFC 8785 (I-JSON) reads it, and
every hash covers the value. So a FHIR decimal's written precision is not preserved: `2.50`, `2.5`
and `25e-1` are one value and hash alike, and the pipeline persists the value as JavaScript writes
it. Decided: precision is not carried, and it is not dropped silently either. A document part
(submission, fidelity report, page text) whose numbers are not all written as JavaScript writes them
(RFC 8785 section 3.2.2.3: no trailing zeros, no exponent where JavaScript places the digits, no
digit beyond a double, no `-0`) is refused by the submission reader (`non-canonical-number`), before
it is hashed. Zone A's canonical JSON writes a double by the same rule, so a 2.5 mg strength (the
smoke product's) hashes alike in both languages. An integer beyond `Number.MAX_SAFE_INTEGER` is
not treated alike (corrected in the review of #148, part A L4). Zone B's reader accepts one written
as JavaScript writes its nearest double (`9007199254740992`, `1e+21`) and refuses any other
(`9007199254740993`, whose double JavaScript writes `9007199254740992`). Zone A's canonical JSON
refuses every integer beyond the bound, since JSON gives Python the exact integer where JavaScript
holds the double. Every integer field of every contract is capped at the bound, so only the open
FHIR resources of a Bundle can carry one; a Zone A producer cannot hash, and so must not write, such
a Bundle. A product-graph value whose precision must survive would need a contract that carries it
as text; none does today.

## Amendment (2026-10-06, an ePI's versions and its EU numbers)

`docs/design/version-identity.md` settles the version model from the pinned Global ePI and EUePI
profiles, and the product's identity by EU number (ADR 0006 decision 5). It changes three things
the 2026-09-24 amendment above states; the contract does not change.

- **The Type 1 set.** One Composition, MedicinalProductDefinition and Organization, and one
  RegulatedAuthorization per authorisation (one per EU authorisation number), each linked to the
  product and the holder, named and identified. The importer still makes one.
- **EU numbers.** Both graph types' preflights refuse an EU authorisation number that is not
  `EU/1/YY/NNN/PPP`, carried by anything but a RegulatedAuthorization, two on one or one on two,
  and product numbers that are not exactly the authorisation numbers' products; the package's
  profile `eu-product-identity` states the same rules for the official validator.
- **The EMA List and Composition.** The List takes the holder, regulator and procedure only when
  every RegulatedAuthorization states the same ones, and the product's one EU product number as
  `ext-epi-eu-number`. The EMA Composition's identifier is one per version, derived from the source
  identifier and its content; every id stays as before.

## Amendment (2026-10-06, ADR 0006: a certified Word source)

ADR 0006 admits a company's Word label that the label reader read exactly, structured by the QRD
template's headings and made into sections by `zone_a.recompute`. The design is
`docs/design/certified-word-import.md` (D1, D2, D6); this section states what it changes here.

**The contract is `CanonicalSubmission` 2.1.0** (`ingestion-provenance` 2.1.0). Every 2.0.0
submission is refused. `provenance.sourceDocument` gains a third member, `certified-word`: the
uploaded .docx (SHA-256, length, file name, storage URI), the request the recompute made the
sections with (the document, the view, the part, the assigned headings and every version the
recompute names), the number of tracked changes the view settled, one page per section
(`sectionPages`) and the page text.

**The ingress invariants above, amended:**

- **Invariant 7.** A `certified-word` source carries a `type1` graph, an attestation (the approval
  placeholder a drawn source has), the extractor `certified-word` whose version is the SHA-256 of
  the canonical JSON of the recompute's versions (the token `certified-word/<hash>`), no model or
  prompt template, and the mapping its recompute used as its terminology service.
- **Invariant 10.** A synthetic certified Word submission's identifier value is `certified-word:`
  and a document id in the reserved block (`00000000-5979-4e74-8000-`), and every narrative that
  carries text carries the marker; a non-synthetic one carries none of these.
- **Invariant 11.** A certified Word source is admitted only once Zone B has made its sections
  again from its bytes (D2) and the renderer gate has drawn them (D3). Neither is built, so the
  gate accepts one only as a dry run: `src/certified-word/gate.ts` refuses it when `DRY_RUN` is
  false (`certified-word-not-recomputed`), and the ordinary gate accepts it only with
  `GateOptions.certifiedWordDryRun` bound to its hash, set only there. Nothing it makes is
  persisted.
- **Invariant 12.** Its page text has one page per section, each wholly body, and every page no
  narrative covers is blank, as an authority import's.
- **Invariant 13.** An identifier value beginning `certified-word:` is written only by its
  importer, as exactly `certified-word:` and the ePI's document id; every other source kind and
  route refuses it.

**The transform and the preflights.** A certified Word source's section titles are its label's
heading lines, carried as written: the crosswalk does not put the template's title in their place
and the EMA preflight does not hold them to it (ADR 0006 decision 4). Every other source keeps
the template's rule.

**Evidence.** `RunManifest` moves to 5.1.0 (its ingestion block's `sourceKind` may be
`certified-word`, and `contractVersion` follows 2.1.0); 5.0.0 stays readable. The FHIR Provenance
of an attested certified Word source has activity `structuring`, the attester, and the .docx by
its SHA-256.

**Not yet:** the invariant for D4's bytes (Zone B reads the .docx from the submissions bucket
under its own identity and requires the pinned hash and length), the recompute (D2) and the
drawing records (D3), each with the change that builds it.
