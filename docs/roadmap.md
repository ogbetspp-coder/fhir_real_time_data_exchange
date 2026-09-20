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

| Item                                                                                                                      | Components                                       |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Deterministic Type 2 → EMA ePI transform, profile validation, persistence, signed evidence                                | Cloud Run, Cloud Healthcare API, GCS, Cloud KMS  |
| Near-real-time analytical projection                                                                                      | Healthcare API native BigQuery stream, Workflows |
| Zone A / Zone B trust boundary: `CanonicalSubmission` contract, hash-bound approval, ingress gate                         | Zod → generated JSON Schema, checked in CI       |
| Mechanical narrative fidelity check (`fidelity-norm/1.1.1`) with golden vectors and a cross-language differential harness | Pure library, no cloud dependency                |
| By-reference submission transport, `document` run source, Workflows document branch                                       | Cloud Storage, Cloud Run, Workflows              |
| Queryable transformation ledger incl. approval and fidelity columns                                                       | BigQuery                                         |
| Per-client retention as native Cloud Storage policy                                                                       | Cloud Storage, Terraform variables               |
| Deploy pipeline with a quality gate that runs before any cloud credential exists                                          | GitHub Actions, Terraform, Cloud Build           |

## Next, in order

| #   | Item                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Components                                                                     | Size |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ | ---- |
| 0   | **Extractor spike.** Can a real extractor produce page text that satisfies the extractor contract and passes the fidelity check? Controlled round trip on a synthetic SmPC PDF, plus a counts-only characterisation of a real-world PDF. Ends in a written go / conditional / no-go. See `docs/design/extractor-spike.md`                                                                                                                                                                                                                                                                                                      | Document AI (Layout Parser), Cloud Storage                                     | S    |
| 1   | **ePI query service, phase 1.** Read-only Model Context Protocol endpoint with `find_product`, `get_section`, `get_provenance`, `verify_quote`; entitlement resolved before the protocol; two credential kinds (ID token, Google access token); one audit record per call. **Status: built and reviewed (contract `query-tools` 2.0.0, `src/query/`, `test/query/`, `infra/query.tf`); deploy pending** — no apply has run, so the infrastructure is `terraform validate`-checked only. See `docs/design/epi-mcp-query-service.md`, "Phase 1 as built"                                                                         | Cloud Run, Cloud Armor (phase 2)                                               | M    |
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

| Rank | Item                                                                                                     | Components                                   | Size | Depends on                                |
| ---- | -------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ---- | ----------------------------------------- |
| 1    | Auditable AI structuring — the headline capability, mechanically verified rather than trusted            | Document AI, Vertex AI                       | L    | Items 0, 3, 6                             |
| 2    | Review-cycle-time prediction over the ledger, surfaced as a dashboard tile: the cheapest credible ML win | BigQuery ML linear regression, Looker        | S    | Real runs in the ledger (it has none yet) |
| 3    | Change-impact analysis — which documents, markets, and translations a variation touches, hash-linked     | Vertex AI embeddings, BigQuery vector search | M    | Converted content                         |
| 4    | Portfolio question answering with FHIR citations                                                         | Vertex AI Search                             | M    | Item 10                                   |
| 5    | Anomaly detection on numeric changes between versions (strengths, volumes, ages)                         | BigQuery ML                                  | S    | Version history                           |
| 6    | QRD compliance advisor: structural and terminology conformance advice before submission                  | Vertex AI, existing validation outcomes      | M    | Item 7                                    |
| 7    | Multilingual meaning-drift check between language versions of the same ePI                               | Vertex AI (Translation LLM), embeddings      | M    | Multilingual content                      |
| 8    | Patient-facing ePI assistant over the published leaflet                                                  | Vertex AI Search, Firebase App Hosting       | M    | Item 10                                   |

## Needs a person, not a model

### Before the first demonstration, in this order

1. **Deploy this branch.** The Provenance projection now writes the approver's role on the
   attester agent (`src/fhir/provenance.ts`), and `get_provenance` reads only that coding and
   never infers one. Every document already in the demonstrator's validated store was written
   before that change, so `get_provenance` answers `unavailable` for all of them until step 2.
   There is no worker-only deploy to run first: `scripts/gcp/deploy.sh` has one untargeted
   `terraform apply` that reconciles the worker and the query service together. What has to be
   ordered is the re-ingest, not the two services.
2. **Re-ingest the demonstration documents** with `scripts/demo/seed.ts` (rehearse with
   `--dry-run`). It needs `SUBMISSION_BUCKET` and `WORKER_URL` exported — it exits at load
   without them — and a `WORKER_ID_TOKEN` minted by impersonating a service account that holds
   `run.invoker` on the worker: a human's Application Default Credentials cannot mint an ID
   token for the worker's audience. The whole command is in README.md, "Re-ingesting with
   `scripts/demo/seed.ts`". A re-ingest adds a second `Provenance` resource rather than
   replacing the first, because its id is derived from the submission id.
3. **Check `get_provenance` before the meeting.** It resolves a Provenance with
   `Provenance?target=Bundle/<id>&_count=1` and no `_sort`, so which of two resources for the
   same document it returns is not fixed by the code. A fresh store or a fresh document id
   avoids the ambiguity. `docs/demo/verifiable-label.md` repeats this.
4. **Set the four GitHub Actions repository variables** (Settings → Secrets and variables →
   Actions → Variables). They are variables and not secrets: an IAM member string, an opaque
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
5. **Decide `query_oauth_client_ids` deliberately.** Two different ids could go in it. The
   Gemini Enterprise MCP connector's own internal OAuth client (created in the console, not by
   Terraform) is the production intent. The other is gcloud's client id,
   `32555940559.apps.googleusercontent.com` — the only way a human can call the service with
   their own Google account, because `gcloud auth print-identity-token --audiences=...` is
   refused for user accounts. That id is built into every gcloud installation worldwide, so it
   identifies the tool and never the caller: adding it means any Google identity with a gcloud
   login can present a credential the service will verify, and the only walls left are
   `run.invoker` and the per-subject entitlement. Decide it, do not drift into it.

### Standing items

- Enable GitHub branch protection on `main` requiring the CI check.
- Confirm the `approverId` policy: an opaque identity-provider subject id, never an e-mail
  address. The contract enforces the shape; the policy is an organisational decision.
- Set `enabled_run_sources` per environment (ADR 0002: production = `["document"]`). The
  variable and the worker's `ENABLED_RUN_SOURCES` check are merged into this tree and tested
  (`test/run-sources.test.ts`), and the default is still all three sources, so nothing narrows
  until an operator sets it. `scripts/gcp/deploy.sh` does not pass it: supply it through
  `TF_VAR_enabled_run_sources` or an `infra/*.auto.tfvars` file.
- Provide `alert_notification_email`: the entitlement-denial log metric is created on every
  apply, but the e-mail channel and the alert policy (more than five denials in a rolling hour)
  exist only when it is set.
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
- For item 1b: confirm the organisation has a Gemini Enterprise licence; confirm Agent Engine
  offers a 3.14 runtime in the intended region before the first deploy. All steps are written
  out in `agent/deploy/README.md`.
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
