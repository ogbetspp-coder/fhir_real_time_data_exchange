# Technical roadmap

This is the engineering plan for the hub, in the order the pieces should be built. It names
the Google Cloud component intended for each item, because the standing decision is to use
fit-for-purpose managed services rather than build what Google already operates.

Sizes are rough build effort: **S** days, **M** a couple of weeks, **L** longer. Nothing here
is a delivery commitment, and nothing here claims regulatory or GxP compliance; see
`docs/validation/README.md` for what would be required before regulated use.

## How the order was chosen

Three rules decided the sequence below.

1. **Test the load-bearing assumption first.** The entire Zone A design rests on an extractor
   producing page text that satisfies `docs/fidelity-normalization.md` section 7. That
   contract was written before any extractor met it. Item 0 exists to find out, cheaply,
   before anything is built on it.
2. **Reach a demonstrable end-to-end path before hardening further.** Four adversarial
   review rounds have made the deterministic pipe solid. The larger risk is now building
   nothing on it. The query service (item 1) is demonstrable on the store as it stands today
   and is deliberately placed ahead of the structuring work it does not depend on.
3. **Math first, AI on the remainder.** SmPC section headings are standardised and numbered.
   A deterministic segmenter sections most CAP documents with no model and passes fidelity by
   construction; AI structuring is then added as a measured improvement over it, not as the
   foundation. That is a stronger story for a regulated buyer, and it is the brand rule
   ("AI proposes, math proves, humans decide") applied to the build order.

## Delivered

**"Delivered" means it has run in the deployed environment and there is a run id, an object, or a
row to point at.** Code that exists and passes tests is not delivered; it is built. That
distinction is not pedantry. Until 2026-09-21 this table claimed a working pipeline while the
deployed worker had never completed a single run — four separate defects, each hidden behind the
one before it, and the deploy had been green throughout because nothing outside the worker ever
asked it to do its job. Anything below whose evidence column says "not yet run in the deployed
environment" is built, not delivered.

| Item                                                                                                                      | Components                                        | Evidence it has actually run                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deterministic Type 2 → EMA ePI transform, profile validation, persistence, signed evidence                                | Cloud Run, Cloud Healthcare API, GCS, Cloud KMS   | Run `c0648d78-5a4e-403e-af70-31c3775ccd92`, 2026-09-21: `persisted`, 0 preflight/official/Cloud validation errors, 32 mapping decisions, 7 evidence artefacts, manifest signature verified against the KMS public key                                                                                                                                                                                                                                                                                                                                                                                               |
| Queryable transformation ledger incl. approval and fidelity columns                                                       | BigQuery                                          | One row in `ema_flow_ledger_dev.transformation_runs` for that run, `manifest_hash` matching the signed manifest                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Near-real-time analytical projection                                                                                      | Healthcare API native BigQuery stream             | 11 resource tables and their views in `ema_flow_fhir_dev`, populated by that run's persistence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Read-only ePI query service over the published store                                                                      | Cloud Run, Model Context Protocol                 | All four tools answered live 2026-09-21 over an impersonated caller ID token: `find_product`, `get_section` (div, both hashes, normalisation version), `get_provenance` (source, fidelity and approval hashes, extractor, approver and role), and `verify_quote`                                                                                                                                                                                                                                                                                                                                                    |
| Gemini Enterprise answering from the verified label through the query service (assistant rollout step 1, no agent code)   | Gemini Enterprise custom MCP connector, Cloud Run | 2026-09-21, a person in the Gemini Enterprise web app asked for synthetic paracetamol section 4.4. Gemini called `find_product` then `get_section`; both audit records carry `credentialType = access-token`, the person's own subject `100989313163422160533`, outcome `ok`. The answer quoted the version 2 sentence verbatim with bundle `0c18c50e…` and its version id. Asked the same question with web search on and the connector not selected, Gemini answered from third-party paracetamol labels found on the web instead — the connector must be selected for the answer to come from the verified label |
| Verifiable quoting: a quote is machine-checked against the published narrative                                            | Cloud Run, `fidelity-norm/1.1.1`                  | `verify_quote` 2026-09-21 on `2ee34ea0…` §4.4: verbatim span → `match` at offsets 0–82; the same sentence with 10 mg changed to 20 mg → `no-match`; the same sentence with the negation removed → `no-match`                                                                                                                                                                                                                                                                                                                                                                                                        |
| Zone A / Zone B trust boundary: `CanonicalSubmission` contract, hash-bound approval, ingress gate                         | Zod → generated JSON Schema, checked in CI        | Four `document` runs persisted 2026-09-21 through the real ingress gate (`23481d23…`, `30f08ec0…`, `a6ea3c5d…`, `24e8aa7d…`), each writing 11 evidence artefacts including the canonical submission and the approval-bound provenance                                                                                                                                                                                                                                                                                                                                                                               |
| Mechanical narrative fidelity check (`fidelity-norm/1.1.1`) with golden vectors and a cross-language differential harness | Pure library, no cloud dependency                 | Fully covered by tests, and now exercised in the deployed pipeline: all four document runs recorded `fidelity_status = passed` in the ledger                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| By-reference submission transport, `document` run source                                                                  | Cloud Storage, Cloud Run                          | Those same four runs: three hand-off objects per product written to `gs://…-submissions/demo/<product>/v<n>/` and named by URI and hash in the run request. The Workflows `document` branch was exercised 2026-09-21 as a negative case (execution `8a14b585…`): the branch passed the reference through and the gate refused a wrong hash with `submission-unreadable` / `hash-mismatch`, writing nothing. A successful `document` run through Workflows is still deferred — it would publish another version of a seeded label                                                                                    |
| Per-client retention as native Cloud Storage policy                                                                       | Cloud Storage, Terraform variables                | Applied by Terraform; no object has yet aged to test expiry                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Official HL7 validation before merge, and one proven run after every deploy                                               | GitHub Actions, HL7 validator 6.10.4              | CI job "Official validation" green 2026-09-20 (run 35545408243); deploy smoke step green 2026-09-21 with `status: persisted`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Deploy pipeline with a quality gate that runs before any cloud credential exists                                          | GitHub Actions, Terraform, Cloud Build            | Green end to end, repeatedly                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Local full-pipeline runner against the deployed environment's own configuration                                           | `scripts/dev/run-pipeline.ts`, pinned validator   | Run `627d1dbb-348f-43c3-9535-1d2a64594286`, 2026-09-21, `--persist` from a laptop: official and Cloud Healthcare validation both executed with 0 errors, `persisted`, manifest signed, 7 artefacts, **13.7 seconds** against a twelve-minute deploy                                                                                                                                                                                                                                                                                                                                                                 |

## Open gaps in delivered controls

Things above that work but are weaker than they look. Listed here rather than buried, because a
control believed to be stronger than it is is worse than a control known to be weak.

- **Official validation is not hermetic.** The validator loads the four checksum-pinned packages
  and then resolves seven further package versions from the FHIR registry over the network at
  run time, and the pinned extensions package currently contributes nothing because the same
  package is fetched first. A validation result is therefore reproducible only as far as the
  registry is stable. The fix is to pre-seed the validator's package cache with pinned dependency
  tarballs and prove no fetch occurs. Measured and written up in `docs/validation/README.md`,
  "Official validation gate". **S**
- ~~The deploy's smoke run writes to the demonstrator on every deploy.~~ **Closed 2026-09-21.**
  The `fixture` run source now builds `synthetic-smoketest`, a product that exists only for that
  path, so the smoke run proves the pipeline after every deploy without writing a version over a
  label the demonstration is about. Before this, the store could be continuously proven or
  demonstration-ready, but not both. The official validation gate follows the same product,
  because it exists to validate what a `fixture` run actually sends.
- ~~`get_provenance` resolves with `_count=1` and no `_sort`.~~ **Closed 2026-09-21.** The
  search now asks the store for `recorded` descending over a bounded page, and the answer is
  then chosen in code under a total order — newest approval first, ties broken by resource id —
  so the same set of Provenance resources always yields the same one. The store's sort was
  verified honoured rather than assumed: reversing it swapped the two paracetamol records. The
  tie-break lives in code and not in a second sort key because a tie could not be produced
  against the live store without writing into it, and the validated store holds only what the
  pipeline published. Verified deployed: on revision `ema-flow-dev-query-00013-2fv`, three
  `get_provenance` calls for `0c18c50e…` — the document with two approvals — each answered with
  `17774cb7-3424-5580-a631-fdf871c00270`, `recorded` 2026-09-20, the version 2 record. Residual,
  stated rather than hidden: a document with more approvals than one page that all share the
  newest timestamp could still see the page composed differently.

## Next, in order

**Foundations come first.** As of 2026-09-21 the owner's standing instruction is that the
foundations, including the Google Cloud estate, are made right before any item below is started.
The findings, the order of work and the owner's open decisions are in `docs/foundations.md`.
Nothing in this table moves until the items marked _before features_ there are closed or
explicitly deferred.

| #   | Item                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Components                                                                     | Size |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ | ---- |
| 0   | **Extractor spike.** Can a real extractor produce page text that satisfies the extractor contract and passes the fidelity check? Controlled round trip on a synthetic SmPC PDF, plus a counts-only characterisation of a real-world PDF. Ends in a written go / conditional / no-go. See `docs/design/extractor-spike.md`                                                                                                                                                                                                                                                                                                      | Document AI (Layout Parser), Cloud Storage                                     | S    |
| 1   | **ePI query service, phase 1.** Read-only Model Context Protocol endpoint with `find_product`, `get_section`, `get_provenance`, `verify_quote`; entitlement resolved before the protocol; two credential kinds (ID token, Google access token); one audit record per call. **Status: deployed 2026-09-20** to `ema-flow-dev-query` (europe-west4); `tools/list` and `find_product` answered over a caller-account ID token. `get_provenance` and `verify_quote` are not yet proved against a published label, because nothing has been published. See `docs/design/epi-mcp-query-service.md`, "Phase 1 as built"               | Cloud Run, Cloud Armor (phase 2)                                               | M    |
| 1b  | **Verifiable-answer agent.** A small ADK agent (own deployable) that answers only from the query service's tools, quotes verbatim with citations, re-checks every quoted span through `verify_quote` after composing, and writes one `AgentTurnRecord` (contract `agent-turn` 1.0.0) per turn joined to the service's audit records by `X-Query-Turn-Id`. **Status: built and reviewed (`agent/`, see `agent/README.md`); deploy pending** — the Agent Engine deploy, Agent Gallery registration and `run.invoker` grant are documented and scripted in `agent/deploy/`, not executed. See `docs/design/verifiable-answers.md` | Agent Development Kit, Vertex AI Agent Engine, Gemini Enterprise / Google Chat | M    |
| 1c  | **Demonstration enablers.** A second version of the synthetic label differing by one sentence, two or three more synthetic products, a seeding script that publishes them through the real document path, and a written demo script: "one truth, three windows", "change one word, watch the world know", "a question a regulator cannot ask a PDF". Fixtures, seeding script (`scripts/demo/seed.ts`) and script (`docs/demo/verifiable-label.md`) delivered 2026-09-20                                                                                                                                                       | Existing pipeline, BigQuery, Looker Studio                                     | S    |
| 2   | **Zone A skeleton** in a `zone-a/` directory of this repository: pydantic models generated from `contracts/generated/`, a CI gate mirroring `npm run check`, and the fidelity check re-implemented with a byte-for-byte golden-vector equivalence test — the first real test of the "language-neutral specification" claim                                                                                                                                                                                                                                                                                                     | Cloud Run, Artifact Registry                                                   | M    |
| 3   | **Parser and deterministic segmenter.** Per the spike's verdict, a hybrid: characters from the PDF's embedded text layer through a pinned deterministic library (character-exact by construction), structure — headers, footers, heading levels, table cells — from Document AI's blocks aligned onto it, Document AI's own text never emitted; batch processing for documents past the online page limit; QRD-heading-based sectioning with no model; every component version pinned and recorded                                                                                                                             | pdf.js / PyMuPDF, Document AI (Layout Parser, batch), Cloud Storage            | M    |
| 4   | **Approval.** A command-line attestation first (enough to demonstrate the hand-off), then the approval API with an append-only record store and an explicit authorization model                                                                                                                                                                                                                                                                                                                                                                                                                                                | Cloud Run, Identity Platform, Firestore or Spanner, Cloud KMS                  | M    |
| 5   | **Minimal reviewer UI.** Source document beside sections, span highlighting, hash badges, a signed approve action; narrative never editable. The point at which human-in-the-loop becomes visible to a buyer                                                                                                                                                                                                                                                                                                                                                                                                                   | Angular Material 3 or Flutter, Firebase App Hosting, Identity Platform         | L    |
| 6   | **AI structuring** as a measured improvement over item 3: constrained extraction into the contract types for what the segmenter cannot do, codes only from terminology lookups with receipts, an adversarial evaluation harness reporting coverage with and without the model                                                                                                                                                                                                                                                                                                                                                  | Vertex AI (Gemini, controlled generation), Vertex AI Evaluation                | L    |
| 7   | **Terminology facade** (`$validate-code`, `$expand`) over the client's ontology platform with versioned lookup receipts — needed once item 6 assigns product codes, not before                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Cloud Run, Memorystore, Healthcare API terminology                             | M    |

Zone A lives in this repository for now: a separate repository would add coordination cost
and no benefit while one person builds it. Split it when a second team exists.

**User interfaces are Google's.** No custom frontend is built where a Google surface serves
the purpose — Gemini Enterprise or Google Chat for the assistant, Looker for analytics,
AppSheet or a Material 3 shell only where a workflow has no Google surface at all. The
product's own code stays small and critical: the deterministic pipe, the contracts, the
fidelity check, the query service, and the agent logic that makes answers verifiable.

## Later — reach and reuse

| #   | Item                                                                                                                                | Components                            | Size |
| --- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- | ---- |
| 8   | Adapter framework: one contract plus a conformance test suite, so each system integration is a configuration rather than a project  | Cloud Run, Eventarc, Pub/Sub          | M    |
| 9   | First adapters — Veeva Vault RIM, then SAP and LIMS — proving the framework against real integration depth                          | Application Integration, Cloud Run    | L    |
| 10  | Query service phases 2–4: version history and diffs from the ledger, semantic section search, signed results                        | BigQuery, Vertex AI Search, Cloud KMS | M    |
| 11  | Adoption metrics that double as the commercial case: share of fields auto-extracted, corrections per document, minutes per document | BigQuery, Looker                      | S    |

## AI and analytics

Ranked by business value against demonstrable effect. The rule for every item: **AI proposes,
math proves, humans decide.** Nothing on this list is permitted to author, alter, or summarise
regulated narrative inside the system of record.

| Rank | Item                                                                                                                                                                                                                                                                 | Components                                   | Size | Depends on                                |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ---- | ----------------------------------------- |
| 1    | Auditable AI structuring — the headline capability, mechanically verified rather than trusted                                                                                                                                                                        | Document AI, Vertex AI                       | L    | Items 0, 3, 6                             |
| 2    | Review-cycle-time prediction over the ledger, surfaced as a dashboard tile: the cheapest credible ML win                                                                                                                                                             | BigQuery ML linear regression, Looker        | S    | Real runs in the ledger (it has none yet) |
| 3    | Change-impact analysis — which documents, markets, and translations a variation touches, hash-linked                                                                                                                                                                 | Vertex AI embeddings, BigQuery vector search | M    | Converted content                         |
| 4    | Portfolio question answering with FHIR citations                                                                                                                                                                                                                     | Vertex AI Search                             | M    | Item 10                                   |
| 5    | Anomaly detection on numeric changes between versions (strengths, volumes, ages)                                                                                                                                                                                     | BigQuery ML                                  | S    | Version history                           |
| 6    | QRD compliance advisor: structural and terminology conformance advice before submission                                                                                                                                                                              | Vertex AI, existing validation outcomes      | M    | Item 7                                    |
| 7    | Multilingual meaning-drift check between language versions of the same ePI                                                                                                                                                                                           | Vertex AI (Translation LLM), embeddings      | M    | Multilingual content                      |
| 8    | Patient-facing ePI assistant over the published leaflet                                                                                                                                                                                                              | Vertex AI Search, Firebase App Hosting       | M    | Item 10                                   |
| 9    | **Information-request evidence pack.** Given a question, one artefact holding each verbatim extract with its document id, version, section code, hash, approver and approval date, referencing the signed manifest. Deterministic assembly of what is already stored | Existing query service, Cloud Storage        | S    | Items 1b, 10                              |

### The health-authority information request, as the framing for items 4, 9 and 10

Items 4, 9 and 10 are easier to justify against one concrete situation than in the abstract: a
health authority asks a question about a product's labelling and the answer is due on a short
clock. The instinct that this is a retrieval problem is worth resisting. Finding the paragraph
is the quick part. What consumes the time is proving the wording is the _current approved_ text
for _that market_ — which version, approved by whom, superseded by which variation. That proof
is what this system holds and a document store does not.

So the claim is narrow and should stay narrow:

- It covers **labelling and product information**. Most information requests concern
  manufacturing, nonclinical or pharmacovigilance data, which live in other systems. If a client
  wants those, the adapter framework (item 8) is the honest route, not an extension of this
  store.
- **Semantic search belongs inside the query service** (item 10), returning a bundle id, section
  key and hash. A separate retrieval index that returns ranked chunks would give answers that
  cannot be verified alongside answers that can, which weakens the only claim that distinguishes
  this system.
- The system supplies **extracts and evidence**; a person writes the response. The moment it
  drafts submission prose it is authoring regulated narrative, which the whole design exists to
  prevent (see "Deliberately not on this roadmap").

## Needs a person, not a model

### Before the first demonstration, in this order

1. **(Done 2026-09-21.)** Deployed, and the validated store was deleted and rebuilt first, so its
   version history contains no hand-written writes — only what the pipeline published.
2. **(Done 2026-09-21.)** The demonstration set was seeded through the real document path:
   `synthetic-paracetamol` v1 and v2, `synthetic-demoxetine` v1, `synthetic-placebolol` v1, all
   `persisted`. Three documents and four `Provenance` resources are in the store. Re-running
   `scripts/demo/seed.ts` would add a second `Provenance` per document rather than replacing the
   first, because its id derives from the submission id, so do not re-seed without rebuilding.
3. **(Closed 2026-09-21 — no longer a thing to check on the day.)** `get_provenance` used to
   resolve a Provenance without asking for an order, so which of paracetamol's two records it
   returned was the store's choice and not the code's. It now asks for the newest approval and
   decides ties itself, so the v2 record is the answer by construction. Re-seeding no longer
   changes it.
4. **Deploying between seeding and demonstrating is safe** as of 2026-09-21. The smoke step
   publishes `synthetic-smoketest`, which nothing demonstrates, so a deploy no longer writes over
   a seeded label. It does leave a fourth document in the store — but the entitlement map names
   only the three demonstration bundles, so the query service never returns it: `find_product`
   on the deliberately broad query "synthetic" answers with exactly the three labels and
   `truncated: false`. Entitlement, not tidiness, is what keeps it out of sight.
5. **Set the four GitHub Actions repository variables** — done 2026-09-20; all four are set
   (Settings → Secrets and variables → Actions → Variables). They are variables and not secrets: an IAM member string, an opaque
   subject id, a bundle id and an OAuth client id are identifiers, and holding one grants
   nothing. Each is optional and an unset one leaves the Terraform default, so a deploy with
   none of them set succeeds and authorises nobody.
   - `QUERY_INVOKERS` — comma-separated IAM members that receive `run.invoker` on the query
     service, e.g. `user:you@example.com,serviceAccount:agent@proj.iam.gserviceaccount.com`.
     This is for callers presenting a credential that authenticates as themselves: a human on
     the access-token path, or an agent with its own service account. A caller using the
     impersonation recipe does not belong here — the caller service account created in
     `infra/query.tf` already holds `run.invoker`. Default `[]`.
   - `QUERY_TOKEN_CREATORS` — comma-separated IAM members that receive
     `roles/iam.serviceAccountTokenCreator` on that caller service account, and so may mint ID
     tokens as it. Bound on that one account, never on the project. Project owner does not
     carry the permission, so an owner who wants the recipe still names themselves here.
     Default `[]`, which makes the ID-token recipe in README.md unavailable.
   - `QUERY_ENTITLEMENTS_JSON` — the raw JSON map, e.g.
     `{"112233445566778899000":{"bundles":["synthetic-type2-smpc"]}}`; the key is the `sub` of
     the identity the token authenticates as — the caller service account's on the
     impersonation path, the human's on the access-token path — never an e-mail address (an
     e-mail-shaped key fails startup). `bundles` is the only key a value may carry; a
     carried-forward `organisation` is now rejected by the Terraform variable validation as
     well as at startup. Default `{}`.
   - `QUERY_OAUTH_CLIENT_IDS` — comma-separated OAuth 2.0 client ids whose access tokens are
     accepted. Default `[]`, which rejects every access token.
6. **(Decided 2026-09-21: the connector's client only.)** For the record of why it was a decision:
   **Decide `query_oauth_client_ids` deliberately.** Two different ids could go in it. The
   Gemini Enterprise MCP connector's own internal OAuth client (created in the console, not by
   Terraform) is the production intent. The other is gcloud's client id,
   `32555940559.apps.googleusercontent.com` — the only way a human can call the service with
   their own Google account, because `gcloud auth print-identity-token --audiences=...` is
   refused for user accounts. That id is built into every gcloud installation worldwide, so it
   identifies the tool and never the caller: adding it means any Google identity with a gcloud
   login can present a credential the service will verify, and the only walls left are
   `run.invoker` and the per-subject entitlement. Decide it, do not drift into it. In `dev` the
   gcloud id was set while no connector client existed and was removed on 2026-09-21, leaving
   the Gemini Enterprise connector's internal client as the only accepted id.

### Standing items

- ~~Enable GitHub branch protection on `main` requiring the CI check.~~ **Done.** Required checks
  are `Check`, `Zone A`, `Agent` and, since 2026-09-21, `Official validation` — until then the
  official HL7 gate ran on every pull request without being able to block a merge.
- Confirm the `approverId` policy: an opaque identity-provider subject id, never an e-mail
  address. The contract enforces the shape; the policy is an organisational decision.
- Set `enabled_run_sources` per environment (ADR 0002: production = `["document"]`). The
  variable and the worker's `ENABLED_RUN_SOURCES` check are merged into this tree and tested
  (`test/run-sources.test.ts`), and the default is still all three sources, so nothing narrows
  until an operator sets it. `scripts/gcp/deploy.sh` does not pass it: supply it through
  `TF_VAR_enabled_run_sources` or an `infra/*.auto.tfvars` file.
- ~~Provide `alert_notification_email`.~~ **Plumbed 2026-09-21.** The variable existed but no
  deploy passed it, so the alert could not fire from any deploy. It now flows from the
  `ALERT_NOTIFICATION_EMAIL` repository variable through `deploy.yml` and `deploy.sh`; the
  metric already existed, so the channel and the policy (more than five denials in a rolling
  hour) are created on the first deploy after the address is set.

### Production gate — required before any environment holds a client's real content

Deliberately not done in `dev`, which is the owner's own experimental environment. Each is a
deploy-time setting of a production environment, not a code change, and a production deploy
that skips one is not a production deploy.

- **Gemini Enterprise app in an EU location.** The `dev` app is `global`, which is acceptable
  for synthetic labels only. The location of an app cannot be changed after creation, so this
  is a new app, a new connector, and a new internal OAuth client — not a setting. The Agent
  Engine region follows the app (an EU app takes `europe-*`).
- **Lock the retained audit log bucket** (below).
- **Narrow run sources to `["document"]`** (`enabled_run_sources`, under Standing items).
- ~~**The worker's Healthcare role scoped to the dataset**~~ done 2026-09-21 with the CMEK
  rollout: the worker holds `fhirResourceEditor` on the record dataset only.
- **Real addresses for alerts and security reports.** The alert channel and `SECURITY.md` carry
  placeholders (`you@khsadvisory.com`, `security@khsadvisory.com`); production needs monitored ones.
- **Registry vulnerability scanning and Binary Authorization in production.** `dev` scans with
  OSV-Scanner in CI (foundations C1); production turns on Artifact Analysis, where it is charged
  per image and deploys are rare.
- **Deploy into `khs-ema-flow-prod`**, which exists empty in the `production` folder under the EU
  and key policies. Before the first deploy there: buckets Google would create itself (Cloud
  Build, Cloud Run source uploads) created with a key first, since the folder refuses unkeyed
  buckets.
- **Web grounding off** on the production assistant, as in `dev` since 2026-09-21.
- **Only the connector's OAuth client** in `query_oauth_client_ids`, as in `dev` since 2026-09-21.

- Decide whether to set `lock_regulated_audit_log_bucket = true`. It is irreversible: the
  retained audit log bucket's retention can then never be changed and Terraform will not
  unlock it. Take the decision after the retention period, legal basis, and costs are approved;
  it is `false` and not applied today. Separately, no reader role scoped to that bucket exists
  — who can read the retained audit log is whoever the project's logging roles allow, and
  narrowing that is the same person's decision.
- Tighten the worker's Healthcare role to the dataset. Today
  `google_project_iam_member.worker_healthcare` (`infra/security.tf`) binds
  `roles/healthcare.fhirResourceEditor` at project level, so the worker can edit FHIR resources
  in every dataset in the project, while the query service's reader is dataset-scoped
  (`google_healthcare_dataset_iam_member.query_fhir_reader`). The change is a
  `google_healthcare_dataset_iam_member` on `google_healthcare_dataset.epi` plus updating the
  worker service's `depends_on`; it is a project-level role removal, so it needs a plan review
  and a check that `scripts/gcp/reconcile-fhir-stores.sh` and `bootstrap.sh` do not rely on
  project-wide Healthcare access through the worker's service account.
- Confirm on the first real deploy that the effective-IAM export actually uploaded. It runs
  after a successful apply and writes to
  `gs://<evidence bucket>/deploy-evidence/<YYYY>/<MM>/<DD>/<stamp>-<env>-<commit>/`, but every
  step is warning-only, so a deployer lacking `resourcemanager.projects.getIamPolicy`,
  `healthcare.datasets.getIamPolicy`, or write access to the evidence bucket leaves a
  `::warning::` in the log and no file in the bucket.
- For item 1b: ~~confirm the organisation has a Gemini Enterprise licence~~ **confirmed 2026-09-21** —
  a free trial, 50 seats, active until 2026-10-20, with the app already created. Still open:
  confirm Agent Engine offers a 3.14 runtime in `europe-west4` at the first real deploy; the
  deploy script's dry run passes but does not ask the service. Steps are in
  `agent/deploy/README.md`.
- (Done 2026-09-20 for item 0: the deployer was granted `roles/documentai.editor` by hand —
  its roles are bootstrapped outside Terraform by design — and the spike's real-world inputs
  were EudraLex Volume 2C documents, which carry no product information, so the synthetic-only
  rule needed no exception.)

## Deliberately not on this roadmap

- Authoring, drafting, or summarising regulated narrative anywhere in the system of record.
- Claiming Annex 11 or 21 CFR Part 11 compliance. Electronic signature, identity-provider
  binding of the approver, and segregation of duties are named as future control boundaries in
  ADR 0002, not as delivered controls.
- Replacing regulatory, medical, quality, or legal review.
- Direct submission to a regulator.
