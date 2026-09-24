# Recorded change: `CanonicalSubmission` 2.0.0 and the authority importer, 2026-09-24

_Follows the procedure (steps 0–8) in `docs/validation/README.md`, "Change control for shared,
evidenced libraries". The UR- rows it cites are in that file. The design is
`docs/design/authority-import-contract.md` (roadmap 3a, PR 2), revised through six independent
reviews._

**What changed.**

- **The contract.** `CANONICAL_SUBMISSION_VERSION` moved from `1.0.0` to `2.0.0`, a major under
  ADR 0002: union members Zone B branches on. The source document is a union on `kind` (`drawn`,
  the 1.0.0 fields; `authority-publication`, an authority's ePI with its import request, its
  document and List pinned by id, SHA-256 and length, its pictures and its page map); the
  approval is a union on `method` (an attestation, as before; `authority-publication`, with
  `requestedBy` and `requestedAt`); `graphType` (`type1` | `type2`) is part of the submission and
  of the approved content; `Bundle.identifier.value` is required; a structuring decision may name
  the field it read (`sourceField`). The Bundle definition `Type2Bundle` is renamed
  `CanonicalBundle` (`src/contracts/canonical-bundle.ts`).
- **The gate** (ADR 0002's amendment, invariants 7 to 13). Source, graph, approval and extractor
  must fit together; synthetic content is refused unless `ALLOW_SYNTHETIC_SOURCES`; no
  drawn-document extractor is qualified, so a drawn submission passes only as a synthetic one; a
  structured source's pages are one per section, wholly body, and every page without a span is
  blank; the `authority-import:` namespace is the importer's alone. For an authority import the
  worker fetches the authority's files itself, requires the pinned bytes, re-runs the importer
  and accepts only the identical submission, page text and report (`src/authority/gate.ts`);
  imports run only as a dry run until roadmap 3a PR 5.
- **The crosswalk and preflights** (stages A and C). Every persisted id derives from the source
  identifier value (copied entries re-identified, references rewritten, an unknown reference
  refused, no fallback to `Bundle.id`; a Bundle, entry or `meta` element the crosswalk does not
  carry, such as a signature, an entry's request or a `meta` extension, refused, and the output's
  `meta` the profile alone); `validateCanonicalPreflight(bundle, graphType)` adds the
  Type 1 set; the EMA List carries the holder, agency and procedure number the graph states and
  is titled by the product's name; a source heading the QRD template permits is kept.
- **The mapping** `cap-smpc-en` moved from `1.2.0` to `1.3.0`: `alternativeTitles` on 6.5 and 6.6
  (the headings without their optional wording) and `targetCodeSystemAliases` (the EMA's SPOR
  URI). The generated ConceptMap and StructureMap carry the new version.
- **The importer** (`src/authority/`, `IMPORTER_VERSION` 1.0.0): strict UTF-8 and duplicate-free
  JSON, the EMA's live shape closed, the document bound to the List that lists it, the mapping's
  exact section tree and permitted headings, pictures refused, T with empty lists, one page per
  section, the Type 1 record. A producer script (`scripts/authority/import.ts`), a re-verification
  command that reads no network (`scripts/authority/verify-import.ts`), golden vectors
  (`test/fixtures/authority/vectors.json`, under `contracts:check`) and a lock
  (`src/authority/importer.lock.json`).
- **The run manifest** moved from `1.1.0` to `2.0.0`: the ingestion approval is the union, and
  the block records the source kind, graph type, `allowSyntheticSources` and, for an import, the
  importer version and the fetched files. `AnyRunManifestSchema` still reads 1.0.0 and 1.1.0.
- **The Provenance** id is
  `stableUuid("ingestion-provenance", identifierValue + ":" + submissionId)` (was the submission
  id alone); its targets are the record's identifier and the
  output's `Composition/<id>` and `Bundle/<id>`; an import's names the authority, the requester
  (`enterer`) and the gate's fetch time.
- **Configuration.** `ALLOW_SYNTHETIC_SOURCES` (default false; Terraform `allow_synthetic_sources`,
  set by the `dev` deploy and plan) and `ENABLED_RUN_SOURCES` defaulting to follow it.
- **Pins.** `labels/ema-epi/` pins Imatinib Teva's hard-capsule SmPC and the four Lists, and
  Imatinib Teva joins the QRD check set; `fhir/standards.lock.json` pins the HL7 Type 1 example
  and three EMA downloads.

`NORMALIZATION_VERSION` is unchanged (`fidelity-norm/3.0.0`); §7 gained one sentence documenting
existing behaviour (a section without `text` has the empty page), with no vector change.

**Why.** ADR 0005 decided that an authority's published ePI may enter the record, and 1.0.0 could
not hold one: it accepted a PDF or Word source, spans on a drawn document's pages, a Type 2 graph
and a person's attestation. The design's reviews showed that every fact about an import would
otherwise be the producer's own claim, and that nothing tied a document to its product's index
or kept a synthetic publication out of production. It also closes the two duties
`fidelity-norm/3.0.0` left to PR 2: no drawn-document extractor supports an approval, and every
page without a span is blank.

**Impact assessment (step 0).** `src/contracts/` is imported by the worker, the query service,
the fixtures, the scripts and, through generated schemas, Zone A's Python models (regenerated;
`zone-a/tests/test_contracts_parity.py` and `test_contracts_strictness.py` updated) and the
agent (which vendors only `query-tools` and `agent-turn`, both unchanged). The fidelity library
and its vectors are unchanged. Previously produced evidence stays readable: manifests of 1.0.0
and 1.1.0 parse under `AnyRunManifestSchema`; their ledger rows keep `contract_version = 1.0.0`.
No 1.0.0 submission can be re-run.

**Steps 1–6.** 1: `CANONICAL_SUBMISSION_VERSION` 2.0.0, `RUN_MANIFEST_VERSION` 2.0.0, mapping
1.3.0, `IMPORTER_VERSION` 1.0.0. 2: `contracts/generated/**`, the contract fixtures
(`test/fixtures/contracts/canonical-submission.json`: `approvedContentSha256`
`d3c5a43a…` → `0484110f…`, `bundleSha256` unchanged; `run-request.json`: `1938c273…` →
`e154eadf…`), the importer vectors and the Zone A models regenerated. 3: no fidelity vector
changed. 4: the adversarial cases are in `test/contracts/authority-contract.test.ts`,
`test/authority/*.test.ts`, `test/namespace.test.ts`, `test/type1.test.ts`,
`test/provenance.test.ts` and `test/run-sources.test.ts`. 5: ADR 0002, ADR 0004 and ADR 0005
amended; `docs/fidelity-normalization.md` §7 and §9. 6: UR-09, UR-10, UR-11, UR-14, UR-16, UR-36,
UR-46 and UR-47 updated; UR-48 and UR-49 added.

**Verification.**

- The gate: a synthetic publication is imported by the importer, passes the gate's own fetch and
  recomputation with the flag on and is refused with it off, and runs through the pipeline dry
  (`test/authority/gate.test.ts`, `test/authority/pipeline.test.ts`); the importer's refusals
  are tested stage by stage (`test/authority/import.test.ts`) and the gate's in
  `test/contracts/authority-contract.test.ts`; a record that drops a section's words its page
  still holds is refused by the gate's blank-page rule (`test/authority/gate.test.ts`). The
  importer's own two checks of the same (`fidelity-not-passed`, `uncovered-page-not-blank`) hold
  by construction and stay as a defence.
- The pinned real labels are refused where the vectors record: Imatinib Teva at its picture
  references (`pictures: picture-reference-without-template-or-evidence`), Nuvaxovid at its
  pictures, Brukinsa at its headings, Jentadueto at the closed shape (`shape: document-shape`;
  the design had expected the tree).
- Official HL7 validation, run locally on the branch against the pinned validator: 0 errors
  across 8 resources (the fixture's four and the Type 1 case's four).
- `npm run contracts:check` regenerates the schemas, fixtures and importer vectors without drift;
  `test/authority/lock.test.ts` holds the lock.

**Consequences.**

- **Every 1.0.0 submission is refused** (the schema version is part of the approved content).
  The demonstration seed (`scripts/demo/seed.ts`) writes 2.0.0 submissions.
- **Migration.** Re-seeding the demonstration writes the re-identified product-graph resources
  beside the old ones in the target store and BigQuery (append-only), and new Provenance ids
  beside the old; the EMA List, Bundle and Composition ids are unchanged, because they already
  derived from the identifier value, so the entitlement map needs no change. The shared synthetic
  Organization becomes one per document. Nothing is migrated in place.
- **Run manifest 2.0.0**; 1.1.0 (and 1.0.0) stay readable.
- **Run sources.** A deployment without `ALLOW_SYNTHETIC_SOURCES` runs `document` only and
  accepts no drawn submission, so until roadmap 3a PR 5 it persists nothing through the gate;
  `dev` sets the flag. `npm run dev` without the flag is document-only.

**Residuals** (the design's "Stated residuals"): the gate trusts the authority's HTTPS server at
gate time, and the authority serves no signature; recomputation proves the importer ran, not that
it is right; the two English SmPCs of a product are told apart only by the request; a withdrawal
after an import is not noticed until a re-import; `RegulatedAuthorization.identifier` holds the
procedure number, which changes with every variation; the EMA's ePI service is a pilot and an
outage refuses runs; what the EMA's integer `0` status means is unknown; no other List can be
shown not to list the document; a picture's deletion rests on an evidence record of the
authority's viewer, not on anything the gate observes; the QRD template version in the EMA output
("10.4") is ours; imports are dry-run only until PR 5; `requestedBy` is a placeholder until
roadmap item 2; the EMA's language integers are read from observation; the
MedicinalProductDefinition's identifier is the ePI's id, not a product id. Address-like values other than
references (a Composition's `url`, a `valueUri`) are carried as written; the importer lock reads
main's first-parent history, so a rewritten main is not seen. Not yet built from
the design: the fetched bytes kept as evidence, first needed when imports persist (PR 5). The
ledger's column descriptions and an import's lineage name (`custom:authority-import.<authority>.<document id>`)
are.

**Step 7.** Every approved submission requires re-approval: the schema version and `graphType`
are part of the approved content. The demonstration's approvals are synthetic placeholders and
are re-seeded.

**Approval (step 8).** Not obtained: author and releaser are the same identity (see "Release
criteria").
