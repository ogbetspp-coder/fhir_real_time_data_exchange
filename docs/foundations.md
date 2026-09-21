# Foundations review — 2026-09-21

Owner's instruction, recorded because it governs the order of work: **the foundations are made
right, including the Google Cloud estate, before any new feature is added.** This document is the
plan of record for that. Nothing on the roadmap's feature list moves until the items marked
_before features_ below are closed or explicitly deferred by the owner.

Every finding below was observed, not assumed: read from the live project `sage-ship-509104-b8`
with `gcloud`, `bq` and the GitHub API on 2026-09-21. The environment is the owner's
experimental `dev` environment; the question asked of each finding is whether production would
be built the same way, and if so whether that is acceptable.

## What is already right

Stated first, because the gaps below should not obscure it.

- **No service account keys exist or can be created.** Deploys authenticate through Workload
  Identity Federation; the organisation enforces `iam.managed.disableServiceAccountKeyCreation`
  and `iam.disableServiceAccountKeyUpload`.
- **Data Access audit logs are on** for Cloud Healthcare, Cloud Storage, BigQuery and Cloud KMS,
  reads and writes.
- **A regulated audit sink** copies those services' audit logs and the services' own structured
  logs into `ema-flow-dev-regulated-audit`, in `europe-west4`, retained 2,555 days.
- **The evidence and submission buckets are CMEK-encrypted**, the evidence bucket carries a
  seven-year retention policy, and the encryption key rotates every 90 days.
- **Every bucket enforces uniform access**, the organisation enforces it, and every container
  image is pinned by digest and checked in CI.
- **Four required checks gate every merge** to `main`, including the official HL7 validator.
- **The FHIR stores keep full version history and enforce referential integrity**, and writes are
  atomic transactions.

## Findings

Ordered by how expensive the finding becomes if it is fixed later rather than now. "Before
features" means before the next feature is started. "Production gate" means it must hold in any
environment that receives a client's real content, and is already listed in the roadmap's
production gate.

### A. Structural — costly to change after real data exists

**A1. One project holds everything, and it is not in a folder.** The whole estate lives in a
project named "My First Project", directly under the organisation, alongside whatever else is
tried there. Production must be a separate project so that its IAM, its organisation policies,
its audit sink and its bills are its own. The Terraform is already parameterised by environment;
what is missing is the landing zone: a folder for the product, `dev` and `prod` projects inside
it, and the organisation policies below applied at the folder. _Before features:_ create the
folder and move `dev` into it. The `prod` project can be created empty.

**A2. Data residency is not enforced, and something already lives outside the EU.**
`constraints/gcp.resourceLocations` allows every location. The project's Cloud Build staging
bucket, `sage-ship-509104-b8_cloudbuild`, is in `US`: every image build uploads the repository's
source there. The Gemini Enterprise app is `global`. For an EU regulatory-data product the
residency promise has to be a policy, not a habit. _Before features:_ move image builds to
regional Cloud Build in `europe-west4` with an EU staging bucket, then set
`gcp.resourceLocations` to `in:eu-locations` on the product folder. The order matters: the
policy first would break the build.

**A3. The regulated record itself is on Google-managed keys.** The evidence bucket is CMEK;
the FHIR dataset (`encryptionSpec` empty), both BigQuery datasets, the regulated audit log
bucket, the Artifact Registry repository and the Terraform state bucket are not. The store and
the ledger are the record, so this is the wrong way round. A Cloud Healthcare dataset's
encryption is fixed when it is created, so in `dev` this means recreating the dataset and
re-seeding — which the project has done twice already and has a script for. _Before features:_
decide it; if yes, do it in `dev` now so production is built the proven way.

**A4. The manifest signing key is software-protected.** `manifest-signing` is
`ASYMMETRIC_SIGN` at protection level `SOFTWARE`. For evidence a regulator may rely on, the
best-in-class answer is an HSM-protected key. Manifests already signed stay verifiable against
the existing key version; new signing moves to a new HSM key. _Production gate_, and cheap
enough to do in `dev` at the same time as A3.

### B. The deploy path — who can change production

**B1. The deploy identity can be assumed from any branch.** The Workload Identity provider's
condition is `assertion.repository=='ogbetspp-coder/fhir_real_time_data_exchange'` and nothing
else, so a workflow on any branch of the repository can become the deployer. The deployer holds
`resourcemanager.projectIamAdmin`, `iam.serviceAccountAdmin`, `iam.serviceAccountUser`,
`cloudkms.admin` and fifteen other roles, nineteen in all — enough to grant itself anything. _Before
features:_ restrict the condition to `refs/heads/main` and the deploy workflow, and put the
deploy job behind a GitHub environment.

**B2. Third-party GitHub Actions are pinned by tag, not by commit.** Nine actions, including
`google-github-actions/auth`, the step that mints the deployer's credentials. A moved tag runs
new code with those credentials. _Before features:_ pin every action to a full commit SHA, and
let an update bot propose moves (C3).

**B3. Image builds run as the default compute service account** with
`cloudbuild.builds.builder`. A dedicated build service account with only what building needs is
the standard shape. _Before features_, together with A2's regional builds. Separately, the
legacy Cloud Build service account (`<number>@cloudbuild.gserviceaccount.com`) also holds
`cloudbuild.builds.builder`, granted by Google when the API was enabled and outside Terraform.
Nothing runs as it once builds name their own identity; removing the grant is part of C2's
trim.

### C. Hardening — cheap, and overdue

**C1. Artifact Registry vulnerability scanning is disabled.** An image with a known critical
vulnerability would deploy without anyone being told. Turn scanning on. Binary Authorization
is already wired as the `enforce_binary_authorization` variable, off; turn it on once scanning
results exist to attest to.

**C2. Fifty-four APIs are enabled; Terraform declares twenty, and not all of those are used.**
Enabled with no declaration anywhere: GKE, GKE Backup, Filestore, Cloud SQL, Dataform, Cloud DNS,
Compute, OS Login, Network Connectivity, Datastore, Analytics Hub, BigQuery Migration and
Reservation, and more — several with their service agents already provisioned. Declared by
Terraform but referenced by nothing else in the repository: Secret Manager, Cloud Deploy,
Dataplex, BigQuery Data Transfer and Eventarc. Each is surface nothing monitors. Trim both lists
to what the product uses, and make Terraform the only list.

**C3. No dependency update automation.** No Dependabot or Renovate configuration exists for npm,
Python, Docker base-image digests, Terraform providers or Actions. Digest pinning without an
update path means pinned and slowly rotting.

**C4. The worker's Healthcare role is project-wide** (`healthcare.fhirResourceEditor` at
project level) while the query service's reader is dataset-scoped. Already on the roadmap's
production gate; do it with A3, since the dataset is being recreated anyway.

**C5. The Terraform state bucket is readable by every project viewer and writable by every
editor**, through legacy project roles. State holds the entitlement map and every resource's
configuration. Restrict it to the deployer and the owner; CMEK it with A3.

**C6. The Gemini Enterprise connector is outside the audit trail.** Data Access logs are off for
Discovery Engine, and the regulated sink does not include it. The query service's own audit
records do capture every tool call, so the evidence exists — but the connector's side of each
call does not. Add Discovery Engine, and Vertex AI once the agent deploys, to both.

**C7. No essential contacts and no visible budget.** Google's security and billing
notifications have no named recipient (the Essential Contacts API is not enabled), and budgets
could not be read — the owner's account lacks access to the billing account from this project.
Set essential contacts at the organisation, and a budget with alerts on the billing account.

**C8. The validated store does not enforce profiles itself.** `disableProfileValidation` is
`true` on both stores; every write is validated by the worker (official validator and the
Healthcare API's `$validate`) before it is sent, so nothing unvalidated has been written. But a
write that bypassed the worker would not be checked. Enabling store-level enforcement against
the imported EMA profiles is defence in depth. It needs a test first, because it will refuse
writes the worker currently accepts if the two validators disagree.

**C9. The official validator is not hermetic, and not locale-pinned.** In progress. Measured
2026-09-21 in a clean sandbox: it downloads **nine** packages on every clean run, not the seven
previously recorded — the two missed are the FHIR R5 core specification itself and
`hl7.fhir.xver-extensions`. Every content file of each registry download is byte-identical to
what the validator installs; only its own `.index.json` files differ. With the network blocked
and an empty cache it refuses to run, which is the check the fix will use. The same run showed
the validator adopting the machine's locale and jurisdiction (Denmark on the owner's laptop),
which is a second reproducibility gap to pin.

### D. The repository

**D1. No licence.** The repository has no `LICENSE`. For a product whose value is a small,
sellable component, what a client may do with the code is the first question legal will ask.
The owner decides the terms; the default of no licence means all rights reserved, which is
defensible but should be stated.

**D2. No `SECURITY.md` and no `CODEOWNERS`.** Cheap, and expected by any vendor assessment.

**D3. Administrators can bypass branch protection** (`enforce_admins` is off). Acceptable for
one person; stated so that it is a decision and not a default.

**D4. Secret scanning and push protection are unavailable** on a private repository without
GitHub's paid secret protection. Nothing secret is committed today — the repository holds
identifiers, never credentials — but nothing would stop one.

### E. Performance

**E1. Both services scale to zero.** A Gemini Enterprise user's first question after an idle
period waits for a cold start of the query service. Measure it; if it is more than about a
second, set a minimum of one instance on the query service in production. The worker can stay
at zero: nothing waits on it interactively.

## Order of work

1. **Decisions the owner makes** (below).
2. **A2 and B3 together**: regional Cloud Build in `europe-west4` with its own service account.
3. **B1 and B2**: deploy identity restricted to `main` and the deploy workflow; actions pinned by
   SHA.
4. **C9**: hermetic, locale-pinned validator, proved in Cloud Build with networking off.
5. **A3, A4, C4, C5 together, if decided**: CMEK on the record, HSM signing key, worker role
   scoped, state bucket locked down — one rebuild of the `dev` dataset, then re-seed.
6. **A1 and A2's policy**: product folder, `dev` moved in, `prod` created empty, EU location
   policy on the folder.
7. **C1, C2, C3, C6, C7, D1, D2**: scanning, API trim, update bot, connector audit, contacts and
   budget, licence and security files.
8. **E1**: measure the cold start and decide.
9. **C8**: store-level profile enforcement, behind a test.

## Decisions only the owner can make

1. **Landing zone.** Create a product folder under `khsadvisory.com`, move `dev` into it, and
   create an empty `prod` project — now, or when the first client is signed.
2. **CMEK on the record in `dev`**, which means rebuilding the dataset and re-seeding once.
3. **HSM signing key** in `dev` now, so production copies a proven setup.
4. **A budget** on the billing account, and who receives its alerts. Needs billing account access
   the deploy identity does not and should not have.
5. **The licence** under which the repository and the sellable component are offered.
6. **The real address for security alerts** — the repository variable still holds the example
   `you@khsadvisory.com`, so the deploy of 2026-09-21 created an alert channel that points at
   it.
