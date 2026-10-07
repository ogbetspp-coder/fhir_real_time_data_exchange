# EMA Flow architecture

## Strategic decision

The enterprise interchange baseline is the published HL7 Global ePI 1.0.0 profile plus a
Type 2 product graph; an authority's published ePI enters as a Type 1 record (ADR 0001's
amendment, ADR 0005). EMA EU ePI is a jurisdictional output contract. Neither preview or
trial-use profile is treated as a permanent internal master-data schema.

EMA-only organizations could author directly against EMA profiles. This architecture retains
a narrow canonical boundary because its purpose is to prove multi-jurisdiction interoperability.

## Zone A structuring boundary

Converting an authored label document into the Type 2 graph is not fully deterministic:
locating section boundaries, metadata, and codes requires judgement, today delivered by
AI-assisted extraction with human review. ADR 0002 and ADR 0003 split that concern into two
trust zones enforced mechanically rather than by instruction.

Zone A (a separate, probabilistic service) proposes section boundaries, metadata, and codes
from an approved source document; it may never author, alter, reorder, or omit narrative
words, and every code it assigns must cite a terminology lookup. Its output is a
`CanonicalSubmission` proposal, passed by reference, until a human approves it. An approved
submission is written to the submission bucket and named to `POST /v1/runs` as
`{uri, sha256}`; `src/gcp/submission-reader.ts` resolves that pointer and the two it contains
(fidelity report, extracted text), reading only from the configured bucket, capping object
size, and hash-checking every part. Status: the contract (`CanonicalSubmission` 3.0.0), the
ingress gate, the reader, the `document` route, and the Workflows `document` branch exist; no
Zone A service produces submissions yet. The producers are `src/fixtures/synthetic-submission.ts`
(a synthetic drawn document) and, for an authority's published ePI,
`scripts/authority/import.ts` (below). No drawn-document extractor is qualified
(`docs/fidelity-normalization.md` §7), so the gate accepts a drawn submission only as a
synthetic one, where the deployment sets `ALLOW_SYNTHETIC_SOURCES`.

The `fixture` and `healthcare-api` sources are pre-existing trusted inputs guarded by IAM, not
by this gate. The worker's run-source allowlist (`ENABLED_RUN_SOURCES`, Terraform
`enabled_run_sources`; a source outside it is `source-disabled` at HTTP and in `runPipeline`,
before any reader, fixture, or client is touched) follows `ALLOW_SYNTHETIC_SOURCES` (default false
in `src/config.ts` and in Terraform): with the flag off, only `document` is enabled and enabling
either ungated source fails startup (Terraform refuses the two variables disagreeing); with it
on, all three are enabled unless the allowlist says otherwise. The `dev` deploy sets the flag.
Either way the ungated sources cannot write into an authority import's resources: every id a run
persists derives from its source Bundle's identifier value, and only the importer may use the
reserved `authority-import:` namespace (`test/namespace.test.ts`). Zone B (this repository,
deterministic) accepts only an approved `CanonicalSubmission`, re-verifies hash, approval,
bijection, and terminology invariants at ingress, and only then runs the transform, validation,
persistence, and evidence pipeline. The transform and preflights changed once for authority
imports (ADR 0002's amendment: re-identified entries, the Type 1 preflight, permitted headings,
the List's product identity); they remain deterministic, and the mapping and generated
artifacts are versioned as before.

The engine that will produce submissions from real labels (roadmap item 8) has its first
component in `zone-a/`: a Word reader that refuses what it cannot read exactly, and the QRD
template registry (`qrd/registry/`), the EMA's SmPC template 10.4 and Appendices I–III as data
derived from the EMA's own files, pinned by hash in `qrd/sources.lock.json`
(`docs/design/qrd-registry.md`). Nothing in Zone B reads the registry yet.

Narrative fidelity is checked mechanically, not by prompt: a pure verifier
(`src/fidelity/`) recomputes provenance-span hashes, applies the versioned normalisation in
`docs/fidelity-normalization.md`, and requires an exact match before Zone B will transform a
document source. The `CanonicalSubmission` contract (`src/contracts/`) is Zod-first, with
generated JSON Schema checked into `contracts/generated/` and drift caught by
`npm run contracts:check`. On the `document` route — the only route that accepts Zone A output
— nothing reaches the FHIR store or the evidence bucket without a hash-bound human approval and
a passing fidelity check; the `fixture` and `healthcare-api` routes are not covered by that
gate and rest on IAM and the run-source allowlist instead. UR-09 through UR-16 in
`docs/validation/README.md` trace these controls to tests. See ADR 0002 and ADR 0003 for the
full invariant list and versioning rules.

### Authority imports

An authority's published ePI (ADR 0005; `docs/design/authority-import-contract.md`) takes its
own path to the same gate. A person requests the import, naming the authority, the document, the
List that indexes it and the language: that request is the human decision for an import. The
producer, `scripts/authority/import.ts`, fetches the document Bundle and the List, runs the
importer (`src/authority/`, shared pure TypeScript, ADR 0004's amendment) and writes the
submission, its page text (one page per section) and its fidelity report. The producer's
identity is not trusted: for a submission whose source is an `authority-publication`, the worker
fetches both files itself (for the EMA, a fixed URL template on `epi.ema.europa.eu`, one `Accept`
header, no redirect, HTTP 200 only, 30 seconds, 4 MiB), requires the pinned hash and length, runs
the importer its own build contains on those bytes, and accepts only the identical submission,
page text and report before the ordinary gate runs.

The record is a Global ePI Type 1 graph: one Composition, MedicinalProductDefinition,
Organization and RegulatedAuthorization, every value from the document or the List or a stated
rule; packs, ingredients and substances are declared not supplied. Its approval is
`authority-publication`: the publication it names, with `authorityStatus: pilot`, and who
requested the import (`requestedBy`, a placeholder until roadmap item 2). A synthetic authority,
in the EMA's live form with ids in a reserved block, is built by `src/authority/synthetic.ts`
and served only where synthetic sources are allowed.

Until roadmap 3a PR 5 an import runs only as a dry run: the gate refuses an authority import when
`DRY_RUN` is false, so nothing it makes is persisted, entitled to the query service or seen by
the agent. The importer's T removes presentation only where it cannot change what a reader sees
(`docs/design/authority-import-t.md`). Each of the five real labels pinned in `labels/ema-epi/`
is refused at a recorded stage (`test/fixtures/authority/vectors.json`), and an import that gets
past T is refused at `rendering`, since the renderer gate supplies no evidence yet.

### Certified Word labels

A company's Word label (ADR 0006; `docs/design/certified-word-import.md`) is a third source kind,
`certified-word`. Zone A reads it with the label reader, finds its QRD sections and writes each
section's narrative and page (`python -m zone_a.recompute`); the importer (`src/certified-word/`,
pure TypeScript) turns that result and what a person confirmed (the ePI's document id, the
canonical product, its name and holder chosen from the label's sections 1 and 7, its EU
authorisation numbers from section 8, and the approval; for a package leaflet, the name its
section 1 heading gives the medicine, the holder from section 6 and no EU number) into a Type 1
submission. Narratives,
pages and titles come only from the recompute; a section's title is its label's heading line,
which the crosswalk and the EMA preflight carry as written. The source pins the uploaded .docx,
the recompute's request and every version it names, and the importer's own version; the extractor
is `certified-word/` and the SHA-256 of the importer's and the recompute's versions. The gate
reads the uploaded .docx itself, from its content address in the submissions bucket (D4), runs
`python -m zone_a.recompute` on it in a subprocess (the worker image carries Python 3.14 and the
two packages, D2), runs the importer again on the result and requires the very submission, page
text and report. Until the drawing records exist (D3) it accepts one only as a dry run, and refuses
it when `DRY_RUN` is false with a closed code the caller learns (`certified-word-drawing-missing`
once the recompute has passed).

### Renderer gate

`docs/design/authority-import-renderer.md` designs it. Built: the renderer image
(`Dockerfile.renderer`, pinned Chrome and fonts, run by `scripts/render/run.mjs` with no network,
a read-only workspace and no credentials), the drawing and measuring code in `src/render/`, and
CI's required Renderer job (`npm run renderer:image`, then `renderer:smoke`, `record`, `check`,
`fonts`, `boxes` and `drawings`; skipped on a pull request that changes no renderer input). It is
frozen at that scope: its judge, signed layout record and Cloud Build (3c C3 to C5) are dropped.
For the demo a per-label sign-off record over side-by-side pinned-browser screenshots will stand
in for its evidence (`docs/roadmap.md`, 3a).

## Deterministic data flow

```mermaid
sequenceDiagram
  participant ZA as Zone A structuring
  participant WF as Cloud Workflows
  participant SRC as Healthcare API source R5
  participant APP as Cloud Run worker
  participant VAL as HL7 validator sidecar
  participant TGT as Healthcare API target R5
  participant BQ as BigQuery
  participant EV as Evidence and lineage

  ZA->>WF: CanonicalSubmission by reference
  WF->>APP: Execute using source Bundle id and run id
  APP->>SRC: Read Type 2 document Bundle
  APP->>APP: Ingress gate: hashes, approval, fidelity
  APP->>APP: Graph and mandatory-section preflight
  APP->>APP: Deterministic ConceptMap and structural mapping
  APP->>VAL: Validate Global and EMA profiles
  APP->>TGT: Healthcare API validate for each required profile
  APP->>TGT: Transaction conditioned on the stored version, if all gates pass
  TGT-->>BQ: Native ANALYTICS_V2 mutation stream
  APP->>EV: Artifacts, manifest signature, ledger, lineage
  WF->>BQ: Wait until target Bundle is queryable
  WF-->>WF: Record observed stream lag
```

An authority import (dry run only until roadmap 3a PR 5) reaches the same worker with one more
step at the gate:

```mermaid
sequenceDiagram
  participant P as Producer (scripts/authority/import.ts)
  participant AU as Authority ePI API
  participant APP as Cloud Run worker

  P->>AU: GET document Bundle and List
  P->>P: Import: request + bytes to submission, pages, report
  P->>APP: CanonicalSubmission by reference
  APP->>AU: GET the same two files (fixed template, no redirect)
  APP->>APP: Require pinned hashes; re-run the importer; require the same submission
  APP->>APP: Ingress gate, Type 1 preflight, crosswalk, EMA preflight
  APP-->>APP: validated, nothing persisted (DRY_RUN)
```

The transformer accepts two authoritative source categories:

- the structured product graph (Type 2, or for an authority import the Type 1 record); and
- an authored canonical SmPC or package leaflet Composition with stable section identifiers.

The source preflight is `validateCanonicalPreflight(bundle, graphType)`: Type 2 as strict as
ever, Type 1 exactly one Composition, MedicinalProductDefinition and Organization and one
RegulatedAuthorization per authorisation (a package leaflet states none, so its record may have
none), linked, named and identified. Both check EU numbers:
one RegulatedAuthorization per EU authorisation number (`EU/1/YY/NNN/PPP`), and the product's EU
product numbers (`EU/1/YY/NNN`) exactly theirs (`docs/design/version-identity.md`). The graph type
is the submission's, and the ungated sources are always Type 2.

It does not infer clinical narrative from ingredients or product properties. Every output
field is classified as copied, code-mapped, structurally moved, deterministically defaulted,
or rejected. Missing and duplicate required sections are errors. The crosswalk
(`transformType2ToEma`) also refuses, with one issue each, a source it would otherwise have to
drop from or rearrange:

- a section coded in the manifest's source code system that no rule maps, at any depth; a
  section carrying two such codes; a section with no `code` element;
- a section without such a code whose `text.div` shows any text, or that the fidelity XHTML
  scanner (`src/fidelity/xhtml.ts`) cannot read;
- a mapped section that is not directly under the section its parent rule maps (the root rule
  at the top level), or that comes before a sibling the manifest orders first;
- a section carrying any element besides `id`, `title`, `code`, `text` and `section` (for
  example `entry`, `extension`, `emptyReason`, `author`, `focus`, `orderedBy` or `mode`);
- a mapped section without narrative that the scanner can read and that shows some text, unless
  it is a bare heading over subsections the source has under it and its rule is not marked
  `"narrative": "required"` (4.8, whose own text sits above its reporting subsection); an optional
  section (mapping 1.4.0: 2.1, Pregnancy, 11. DOSIMETRY) is held to this when it is there;
- a source Composition or Bundle whose `language` is missing or is not an English BCP 47 tag
  (`en`, optionally `-Latn`, optionally a region);
- a source Bundle without `identifier.value`, and a reference in any entry that names no entry
  of the Bundle (relative, versioned, `#contained` or external); a reference by identifier alone
  is kept.

Every id the run persists derives from the source Bundle's identifier value: the EMA List,
Bundle and Composition as before, and each copied entry
`stableUuid("ema-entry:" + resourceType, identifierValue + ":" + position)` with a `urn:uuid`
fullUrl, its references rewritten to match; a reference naming no entry refuses, and so does
a source Bundle or entry element the crosswalk does not carry (a signature, an entry's request),
a `meta` element on any resource but `versionId`, `lastUpdated` and `profile` (an extension, a
tag, a source), and a contained resource or implicit rules on any resource; the output's `meta`
is its profile alone. A run therefore writes only into its own namespace, and every
`Reference.reference` it persists names its own output, whatever ids its source chose
(`test/namespace.test.ts`). Other address-like values a source writes (a Composition's `url`, an
extension's `valueUri`) are carried as written, a stated residual: no route that is not synthetic
can reach the crosswalk with one today, since a drawn source must be synthetic and an import
builds its own record.

Some things it still does without asking, by design: every mapped section takes a new id and, as its
heading, the source's heading when the rule permits it (the rule's `title` or one of its
`alternativeTitles`, the QRD template's forms without optional wording, since mapping 1.3.0) and otherwise
the rule's `title`; only the target coding is kept; the EMA List is titled by the product's name
(the document's title only where the graph names no product) and carries the holder, regulatory
agency and procedure number the graph states in their identifier systems, and its one EU product
number as `ext-epi-eu-number`, never a value it does not;
Composition.language and Bundle.language are written as `en`; and a section without a source code
and without narrative — an empty container — is dropped, title included. The `xml:lang` of a
narrative `div` is not checked yet. The manifest loader rejects a manifest in which two rules, or a
rule and an unmapped slot, share a `sourceKey` or a `targetCode`, and lineage names the mapping by
the version the loaded manifest declares, the same `mappingVersion` the run manifest records.

Since mapping 1.4.0 the manifest has a rule for each of the 59 sections of the EMA's profile
`EUQRD-CAP-template-new-SmPC-en`, 27 of them optional, and names the profile's custom subsection
slots (codes 200000044333 and 200000044347, 44 slots) as `unmapped`, with the reason: the crosswalk
carries the template's own sections only, each once, so it refuses a section with their keys.
`test/official/profile-slots.test.ts` reads the profile from the pinned EUePI package in CI and fails
on any slot that is neither. The ConceptMap publishes the rules as `equivalent` and the unmapped
slots as `noMap`. Every key, a rule's or an unmapped slot's, is a contract `SourceKey` (no hyphen),
or no submission could carry its section: mapping 1.5.0 renamed the two of 1.4.0 that were not,
Breast-feeding's and the pharmacokinetic/pharmacodynamic relationship's (`smpc.4.6.breastfeeding`,
`smpc.5.2.pkpd`), and `test/leaflet.test.ts` holds every key of every manifest to it.

The package leaflet has its own manifest, `fhir/mappings/cap-pl-en.json` (mapping 1.1.0): the
EMA's profile `EUQRD-CAP-template-new-Package-Leaflet-en` written out, 27 rules, 17 of them
required, and its custom subsection slot unmapped, held to the profile by the same test, with its
own code system (`canonical-pl-sections`) and ConceptMap. The worker loads both manifests and
takes, for each source, the one its `Composition.type` names, in our document type code system or
the EMA's (`loadEmaMappings`, `mappingFor` in `src/fhir/mapping.ts`), and refuses a source that
names none, two, or one no manifest maps; a caller that gives one manifest (the signer,
`scripts/dev/run-pipeline.ts`) maps an untyped source by its sections, as before, and the crosswalk
refuses a source that names another document than its manifest's. The EMA Composition is typed as
the manifest's document (`100000155532` SmPC, `100000155538` Package Leaflet, from the EMA's code
system in the pinned package), which the EMA preflight holds. A leaflet is carried only from a
certified Word source, its titles as written (a Type 2 leaflet's template titles would write X:
`leaflet-titles-not-carried`), and in a dry run only until the query service and the signer read
one (`leaflet-not-readable`; `docs/design/pl-structure.md`, "Zone B").

The StructureMap is the crosswalk's twin in the FHIR mapping language (`fhir/maps/`), compiled by the
pinned validator and executed on its transform engine in CI against this crosswalk on every fixture
(`test/official/structuremap-twin.test.ts`). It transforms; the ids, the reference rewrite and the
refusals stay here (`docs/design/structuremap-twin.md`). It is the SmPC's alone: the leaflet adds
data, not transform logic (that note, "The package leaflet: not twinned").

## Versions

An ePI's versions follow the Global ePI and EUePI profiles (`docs/design/version-identity.md`):
`Bundle.identifier`, `Bundle.timestamp` (the original approval date) and the EMA List's identifier
stay the same across versions, and so does every id the run persists, so the store keeps one
logical resource per document and its versions as resource versions. `Composition.identifier`
names one version: the EMA Composition's is derived from the source identifier and its content.
The run reads the version of its document Bundle the store holds before it signs, and the
transaction carries it as its precondition (`ifMatch`, or `ifNoneMatch: "*"` for a first
publication), so a transaction refused because the document changed since writes nothing. Whether
the Healthcare API honours these entry fields inside a transaction is to be checked live (that
note, "Needs a live check").

## Validation model

Validation is deliberately redundant:

0. for document sources, `docs/fidelity-normalization.md` defines the mechanical narrative
   fidelity check that gates ingress before any transformation (ADR 0003);
1. application checks verify graph completeness, uniqueness, and expected profiles for the
   submission's graph type (`src/fhir/preflight.ts`), and the crosswalk (`transformType2ToEma`)
   refuses a source whose coded sections are not in the manifest's hierarchy and order, as
   listed above; the EMA preflight then checks every target section's code and permitted title
   at its position;
2. the official HL7 Java validator evaluates the pinned packages, FHIRPath, slicing, and
   profile chain. It loads five packages with `-ig`: the four HL7 and EMA ones and the
   repository's own, `dev.khs.fhir.epi` (`fhir/generated/`, built by
   `scripts/fhir/generate-artifacts.ts`), which defines every `https://khs.dev/fhir/` code system,
   identifier system (as a NamingSystem) and extension the pipeline writes, and the EU number
   profile `eu-product-identity`. Besides the source and the EMA List, Bundle and Composition, it
   validates a `document` run's Provenance against base R5, and the source and the EMA Bundle
   against `eu-product-identity` too. The package also holds the `epi-published` SubscriptionTopic.
   Offline, the validator checks a code and its display only in a code system a pinned package
   holds completely; it cannot check UCUM, and it says nothing about SNOMED CT
   (`docs/design/terminology-server.md`);
3. Cloud Healthcare API `$validate?profile=` verifies each profile as deployed in the target
   store. The Provenance and the package's own profile are not sent there: the store's profile
   import takes the four HL7 and EMA packages only, so the store has no definition of them; and
4. a write is attempted only when all required outcomes contain no `fatal` or `error` issue.

The target store does not enable implementation-guide enforcement globally. Cloud Healthcare
API considers a resource valid when it matches any enabled global profile, whereas this
pipeline requires the Composition to satisfy all selected EMA profiles. Explicit profile-by-
profile validation is therefore the stronger gate.

## BigQuery synchronization

The validated store’s native `streamConfigs` is the synchronization mechanism. There is no
custom ETL hop between FHIR and BigQuery. The stream is configured for
`ANALYTICS_V2`, daily `meta.lastUpdated` partitions, all ePI business resource types, and
resource history.

Cloud Healthcare API supports R5, but the current first-party Google Terraform provider still
rejects R5 during provider-side validation. Terraform owns the Healthcare dataset and all
surrounding permissions/destinations. A reviewed, idempotent REST reconciler creates or patches
the two stores, verifies that every existing store is R5, refuses any R4 substitution, and does
not delete stores. This limitation and the reconciler output are part of deployment evidence.

Cloud Healthcare API creates one history table and current-state view per resource type.
Google documents expected stream lag as typically dozens of seconds. Workflows queries the
current `Bundle` view every ten seconds for up to two minutes and records the observed upper
bound. An absent row fails the workflow while preserving the successful FHIR write evidence.

`Bundle.entry.resource` is not exported in analytical schemas because it can hold every FHIR
resource type and exceed BigQuery column limits. The write transaction therefore stores every
document entry as a top-level FHIR resource in addition to the immutable document Bundle.

## Evidence and observability

Every run receives one UUID propagated as:

- Workflow execution input;
- structured application log `runId`;
- Healthcare API `X-Request-Id`;
- evidence object prefix;
- BigQuery ledger key;
- lineage run request ID; and
- transaction identifier.

Every run writes the following objects under `runs/<runId>/` in the evidence bucket:
`source-type2`, `ema-list`, `ema-document-bundle`, `mapping-decisions`, `validation-outcomes`,
`signed-manifest`, `lineage-resources`, in persist mode `persist-transaction` and `commit`, and, for
document sources, `canonical-submission`, `ingestion-provenance`, `fidelity-report`, and
`provenance-resource`. `source-type2`, `ema-list` (it carries the product name and identifiers,
no narrative), `ema-document-bundle`, `persist-transaction` and `canonical-submission` contain the
narrative XHTML — the evidence bucket, the submission bucket, and the FHIR store are the only places
narrative rests — while `fidelity-report`, `ingestion-provenance`, `provenance-resource`, the signed
manifest, and the BigQuery ledger row never do. FHIR payloads and narrative are not written to Cloud
Logging.

The signed manifest is `RunManifest` 6.0.0; every earlier version stays readable, and
`src/contracts/run-manifest.ts` says what each changed. It is signed before the FHIR transaction,
names the transaction it authorises (`authorised`), and names every FHIR package the validator
loaded by hash and the validator's image digest; only the ledger row says `persisted`. A `document`
run's ingestion block records the source kind (`drawn`, `authority-publication` or
`certified-word`), the graph type, whether the deployment accepted synthetic content
(`allowSyntheticSources`), the approval as
either union member, and, for an authority import, the importer version and each file the gate
fetched (URL, SHA-256, length, fetch time). The FHIR Provenance's id derives from the source
identifier value and the submission id, one per approval; its targets are the record's
identifier and the output's `Composition/<id>` and `Bundle/<id>`. An import's Provenance has
activity `authority-import`, the authority as agent of its source files, the requester as
`enterer`, and `recorded` at the gate's fetch time. While imports run dry none of this is
written for one; the design keeps the fetched bytes as evidence and re-verifies a recorded import
from them (`scripts/authority/verify-import.ts`, which reads no network) once imports persist.

The signed manifest's `runtime` block ties a run to what produced it: `sourceCommit` is the
deployed git commit (`GIT_COMMIT`, the same `service_version` the query service records),
`imageDigest` is the digest of the worker image (`IMAGE_DIGEST`), both set on the worker by
`infra/run.tf` (`test/infra/worker-provenance.test.ts`), which refuses a plan whose
`service_version` is not a full commit id; the worker refuses a malformed value at startup, and
off Cloud Run it records `development`. `workflowRevision` is the Cloud Run revision (`K_REVISION`):
the workflow's own revision cannot be passed to the worker without a Terraform dependency cycle.

Cloud Monitoring presents throughput, failures, validation rejections, service latency,
workflow executions, and architectural guidance. Data Access audit logging is enabled for
Healthcare API, Storage, BigQuery, KMS, Discovery Engine and IAM; those logs and the audit logs of
Cloud Run, Workflows, IAM credentials, Resource Manager and Logging are routed to a retained
regional log bucket (`infra/security.tf`).

After a successful `terraform apply`, `scripts/gcp/deploy.sh` exports the effective IAM
policies (project and ancestors, the Healthcare dataset and stores, every bucket, topic, image
repository, Cloud Run service, service account and BigQuery dataset and table, and the keys in
the region's key rings) and reports what each deployed identity holds, into the deploy log and
`gs://<evidence bucket>/deploy-evidence/<YYYY>/<MM>/<DD>/<UTC stamp>-<environment>-<commit>/`
(the newest folder is the latest deploy). This closes the gap between the role set a test
asserts and the policy in force (ADR 0004, decision 5), within limits: it counts only literal
`serviceAccount:` members, does not read key rings, Pub/Sub subscriptions, Secret Manager or
Workflows, and is evidence only: every failed read is a `::warning::`, never a failed deploy.

## Security boundaries

- Cloud Run requires IAM authentication on both services; no `allUsers` invoker is granted
  anywhere. On the worker, `run.invoker` is held by two service accounts: the Workflows
  account (`workflow_invoker`) and the deployer, for the post-deploy smoke run
  (`deployer_invoker`, `infra/run.tf`). On the query
  service, `run.invoker` is granted to the members listed in the `query_invokers` variable,
  which is empty by default, plus one service account the configuration creates for the
  purpose: `ema-flow-caller-<env>` (`google_service_account.caller`, `infra/query.tf`). That
  account exists only to be impersonated — a human cannot mint a Cloud Run ID token with their
  own Google account — and holds `run.invoker` on the query service and nothing else. Who may
  mint tokens as it is the `query_token_creators` variable, bound as
  `roles/iam.serviceAccountTokenCreator` on that one account and empty by default. A deploy
  that sets neither variable authorises no caller: the account exists and nobody can use it.
- The worker uses a dedicated service account that reads the source store and edits the
  validated store (store-level grants), appends to the ledger table (a custom role), and holds
  evidence-object, lineage-editor, logger and signing permissions
  (`test/infra/worker-identity.test.ts`). Its older dataset-wide FHIR editor and ledger grants,
  and the query service's dataset-wide reader, stay, marked TRANSITIONAL, until audit B04's
  phase 2.
- The deployer and the read-only planner, their Workload Identity pools and their roles are
  bootstrapped outside Terraform (`scripts/gcp/deploy-identity.sh`, `plan-identity.sh`); neither
  is used at runtime. Each apply runs only a plan it has read and refuses a destroy the operator
  has not acknowledged (`README.md`, Google Cloud deployment).
- Workflows can invoke the worker and query only the FHIR analytics dataset.
- The Cloud Healthcare service agent can publish change notices and edit only the analytics
  dataset. A change notice names a resource and never carries its content; what a downstream
  consumer receives is in `docs/design/epi-published-notifications.md`.
- No credentials are built into images or stored in the repository.
- Binary Authorization is configurable via `enforce_binary_authorization` and is disabled for
  the prototype; SLSA provenance and SBOM generation are not yet configured.
- Production should use separate projects, organization policies, VPC Service Controls,
  Assured Workloads where applicable, Access Transparency/Approval, and approved CMEK/HSM
  policies.

## Query service

`ema-flow-<env>-query` (ADR 0004, `docs/design/epi-mcp-query-service.md`, whose "Phase 1 as
built" section is the authoritative description) is a second, discrete Cloud Run deployable,
not a mode of the worker. It is tested under `test/query/` and deployed in `dev` as
`ema-flow-dev-query` (europe-west4): first on 2026-09-20, all four tools answered live on
2026-09-21, and redeployed from `main` by every deploy since.

- **Intended use.** A read-only Model Context Protocol endpoint (four tools: `find_product`,
  `get_section`, `get_provenance`, `verify_quote`; contract `query-tools`, versioned in
  `src/contracts/query-tools.ts`) that lets an
  AI assistant answer questions about product information with verifiable answers: every
  result names the FHIR resource and version it came from and carries the hashes needed to
  check it against the store without trusting the service.
- **Identity and image.** Its own service account, `ema-flow-query-<env>`, holds exactly four
  roles: `roles/healthcare.fhirResourceReader` on the validated store (and, until B04's phase 2,
  the dataset), `roles/logging.logWriter` on the project, and, for signed approvals,
  `roles/storage.objectViewer` on the approval heads bucket and `roles/cloudkms.publicKeyViewer` on
  `approval-signing-hsm`. No write role, no other bucket, no BigQuery, no key it can sign with.
  `test/query/acceptance.test.ts` ("least privilege, proven") reads every `infra/*.tf` and asserts
  that role set. Its image is `Dockerfile --target query` (the `query` path
  of the shared Artifact Registry repository), the worker's runtime: debian-slim with the pinned
  Node binary; `npm run images:check` fails a root `Dockerfile*` base without a digest, or two Node
  bases with different digests.
  `query_image` must be a by-digest reference when the service is planned; the digest part
  becomes `IMAGE_DIGEST`.
- **Authentication.** Cloud Run requires authentication at the edge (no `allUsers` invoker;
  invocation is per caller through `query_invokers` and the caller service account described
  under "Security boundaries"), and the service verifies the credential
  again itself, so the edge is not trusted alone. Two kinds arrive on `Authorization: Bearer`,
  distinguished by shape: a JWT-shaped bearer is verified as a Google-signed OIDC ID token for
  `QUERY_AUDIENCE` (`credentialType` `id-token`); any other bearer is treated as a Google
  OAuth 2.0 access token, verified through Google's tokeninfo endpoint and accepted only when
  its `aud` or `azp` is in `QUERY_OAUTH_CLIENT_IDS` (`credentialType` `access-token`; with the
  list empty, which is the default, every access token is rejected), with successes cached
  briefly in process memory by the token's hash. `QUERY_AUDIENCE` defaults to the deterministic
  Cloud Run URL (`https://ema-flow-<env>-query-<project number>.<region>.run.app`); a Terraform
  postcondition asserts after apply that the URL is one the service serves. Cloud Run also serves
  the service on a legacy `https://<service>-<hash>-<region code>.a.run.app` hostname. Both
  hostnames reach the service and only the audience string is accepted as an ID token's `aud`, so
  the outputs keep them apart by name: `query_service_url` (where to send requests) and
  `query_audience` (what to mint tokens for) are the same string unless `var.query_audience`
  overrides it, while `query_service_urls` lists every hostname and is not an audience.
- **Bounds.** Before any protocol message is dispatched (`src/query/app.ts`) the service answers
  `401 unauthenticated`, `403 not-entitled` (so an authenticated stranger never reaches
  `tools/list`), `405`, or `400 invalid-request` for a malformed or oversized request, and past
  that it bounds its own wait, a request's store reads and `find_product`'s scan, reporting
  `truncated: true` whenever an answer is shorter than the entitlement holds. The exact limits
  are stated once, in the design note's "Phase 1 as built".
- **Entitlements.** A Terraform-managed map, `{"<sub>": {"bundles": [...]}}`, parsed once at
  startup with a strict schema (an `organisation` key or an e-mail-shaped principal fails
  startup) and resolved once per request before any store read. Inside the protocol, a document
  outside the caller's entitlement is `document-not-found` from every tool; `not-entitled` is an
  audit outcome, never a returned error code.
- **What it must never do.** Write, amend, draft, rewrite, or summarise regulated narrative;
  return narrative without its hash; disclose a document outside a caller's entitlement.
- **`/readyz` (and `/healthz`).** Answers `status`, `service`, and `version` from the process's
  configuration only; it performs no token check of its own (Cloud Run IAM still applies),
  calls no store, and reads nothing at request time. It proves the container is up, not that
  the store is reachable. Call `/readyz` from outside: `/healthz` is the container's startup
  probe, and Google's frontend answers that exact path on a `*.run.app` hostname itself without
  forwarding it, so its 404 means nothing.
- **Audit.** One `QueryAuditRecord` (`src/contracts/query-tools.ts`) per dispatched `tools/call`
  request: `service` (`ema-flow-query`), `serviceVersion`, `imageDigest`, `at`, `principal`,
  `credentialType`, `tool`, `argumentsSha256`, `outcome`, `resultCount`, `truncated`
  (`find_product` only), `durationMs`, `bundleId`, `versionId` (the version actually read;
  absent when nothing was read), and `turnId` (when the caller declared one) — never narrative,
  never an argument value. A request the transport refused before the handler ran still gets one
  `invalid-request` record; a notification (no JSON-RPC id) gets none. When the service gives up
  waiting it writes the outstanding records itself — `unavailable` for a call that reached a
  handler and had not finished — and a tool finishing later adds no second record. Records go
  through the same logger as the worker; `credentialType` is on that logger's allow-list
  (`src/lib/logger.ts`, a shared library under ADR 0004 change control), so the written line
  carries every field of the record, and a test parses a written line back against
  `QueryAuditRecordSchema`.
- **Structured lines and alerting.** Every line carries `service: "ema-flow-query"`. The 401
  line is `stage: "query-http", event: "unauthenticated"` with nothing derived from the
  credential; the 403 line adds `principal`. A log-based metric
  (`ema_flow/query_entitlement_denials`) counts lines with `outcome` or `event` equal to
  `not-entitled`; an alert policy (more than 5 denials in a rolling hour) always exists and
  pages the environment's alert recipient, or, in `dev` only, no one. The retained audit log bucket
  can be locked with `lock_regulated_audit_log_bucket` (irreversible; default `false`, not set).
- **Turn correlation.** The ADK agent (`agent/`) generates a UUID when a turn starts, sends it
  as `X-Query-Turn-Id` on every request of the turn, and writes it as `turnId` in its own
  `AgentTurnRecord` (contract `agent-turn`, `src/contracts/agent-turn.ts`), so the two records
  join on that value without either carrying a word of what was asked or answered. Which agent
  build is live: `agent/deploy/README.md`, and the `serviceVersion` on audit records.

- **Signed approvals.** With `APPROVAL_VERIFICATION` on (`query_approval_verification`, off by
  default), every answer is verified against the signed statement linked to the version it serves,
  the whole stored Bundle and every served section are re-hashed against it, and a version without
  a valid approval is `not-approved` (`query-tools` 5.0.0; `docs/design/approval.md`, D9 and the
  amendment of 2026-10-06). Off, no answer carries `approval` and none is `not-approved`; audit
  records say `contractVersion` 5.0.0, `tools/list` shows the optional `approval` in the
  `get_section` and `get_provenance` output schemas and descriptions that state both modes, and the
  published contract, not `tools/list`, carries the `not-approved` code.

## Approval signer

`ema-flow-<env>-signer` (`docs/design/approval.md`; ADR 0004) is the third Cloud Run deployable:
the HTTP endpoint of the Google Chat app, built as a Workspace add-on, through which a named person
approves a submission. Built 2026-10-06 (phase 1, steps 2 to 4 of the design); the add-on itself is
the owner's to create (step 1), and until it is, nothing can call the signer and it refuses every
event.

- **Intended use.** Records a person's approval of one submission's content, as Google asserts the
  person's identity, as a statement signed with the environment's approval key. It is the approval
  service's signature over a statement naming the person, not the person's electronic signature, and
  it claims no 21 CFR Part 11 or Annex 11 compliance.
- **What it does.** On a `review <submission URI> <SHA-256>` message it reads the submission, runs
  the gate, the preflights and the crosswalk, reads the document's head, builds the review and stores
  it once under `reviews/<its SHA-256>`, and answers a card. On the Approve click it verifies both of
  Google's tokens again, rebuilds the review, refuses one whose hash is not the click's, consumes the
  person's token, signs with `approval-signing-hsm` (RSA-PSS, SHA-256, salt 32, HSM), verifies its own
  signature, and appends the head create-if-absent in the `approval-heads` bucket. It calls nothing:
  publishing is started by hand in phase 1.
- **Identity and image.** `ema-flow-signer-<env>` holds `roles/cloudkms.signerVerifier` on
  `approval-signing-hsm` (no other identity signs with it); create and read on the heads bucket;
  create on the evidence bucket's `reviews/` and `approvals/` and read on `reviews/` (IAM
  conditions); read on the submissions bucket; and the log writer role. No FHIR, BigQuery or other
  key role (`test/infra/signer-identity.test.ts`). Its image is `Dockerfile --target signer`, by
  digest, named in every statement it signs. Only the add-on's service account may invoke it
  (`approval_addon_service_account`, empty by default).
- **The worker's side.** With `APPROVAL_ENFORCEMENT` on (`approval_enforcement`, off by default
  until the design's step 6), every persisted run needs a verified head: a `fixture` or
  `healthcare-api` run is refused, and a `document` run publishes only under its document's head
  statement, whole published record included, and links the version it wrote to it after the commit
  (`docs/design/approval.md`, D5 and D8); off, it publishes as before. Every reader trusts exactly one
  approval key version (`kms_approval_key_version`). The worker holds read on the heads bucket and `roles/cloudkms.publicKeyViewer` on the
  approval key, and cannot sign or write an approval.

## Scale and failure behavior

Each document is an independent, idempotent run. Workflows provides retries and execution
history; Cloud Run scales horizontally with conservative concurrency because the validator
sidecar is memory intensive. Large payloads belong in the evidence bucket and are represented
in orchestration by identifiers and hashes.

The initial API is synchronous to make the architectural proof inspectable. Higher-volume
submission should publish document IDs to Pub/Sub and start executions through Eventarc.
Bulk historical conversion can reuse the pure mapping library from Dataflow without changing
profiles, evidence schemas, or validation rules.

## Known limitations

- EMA EUePI 1.0.0 is a preview and HL7 Global ePI 1.0.0 is trial-use.
- The synthetic SmPC is not medical advice or authorized product information.
- Terminology validation runs offline in the validator sidecar (`-tx n/a`) for reproducibility.
  UCUM and SNOMED CT codes are not checked; `docs/design/terminology-server.md` proposes the
  smallest pinned terminology service that would check them.
- Human content approval is built for phase 1 (the approval signer, above) and is not in force for
  the query service until `query_approval_verification` is on. Segregation of duties, `reject` and
  `withdraw`, re-authentication at signing and a regulated electronic signature are future control
  boundaries.
- An authority import trusts the authority's HTTPS server at gate time (it serves no
  signature); the EMA calls its ePI service a pilot, so an import carries `authorityStatus:
pilot` and an EMA outage refuses the run. Imports are dry-run only until roadmap 3a PR 5
  (`docs/design/authority-import-contract.md`, "Stated residuals").
- The FHIR store’s stream does not backfill resources written before streaming was enabled.
- This repository cannot establish organizational SOPs, training, supplier qualification, or
  validated state by itself.
