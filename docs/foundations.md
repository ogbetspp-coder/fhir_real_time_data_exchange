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
- **Data Access audit logs are on** for Cloud Healthcare, Cloud Storage, BigQuery, Cloud KMS,
  Discovery Engine and IAM, reads and writes.
- **A regulated audit sink** copies those services' audit logs and the services' own structured
  logs into `ema-flow-dev-regulated-audit-cmek`, in `europe-west4`, retained 2,555 days.
- **The evidence and submission buckets are CMEK-encrypted**, the evidence bucket carries a
  seven-year retention policy, and the encryption key rotates every 90 days.
- **Every bucket enforces uniform access**, the organisation enforces it, and every container
  image is pinned by digest and checked in CI.
- **Eight required checks gate every merge** to `main` — `Check`, `Official validation`,
  `Renderer`, `Images`, `Zone A`, `Agent`, `Plan` and `Vulnerabilities` — and the deploy of a
  commit waits for every job of CI's run on it to succeed.
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

_Status 2026-09-22: closed, run and verified._ `scripts/gcp/landing-zone.sh` creates
`EMA Flow` with `non-production` and `production` folders beneath it, moves `dev` into
`non-production`, and creates `khs-ema-flow-prod` empty and without billing in `production`. The
location and key policies go on the **production** folder, not the product folder: `dev` hosts the
Gemini Enterprise trial, whose app and data store are `global`, and a location policy would refuse
the next global resource it needs. EU residency of the assistant is already a production gate
item, so the policy goes where production lives, and any future production project inherits it.
Reviewed adversarially before any run: the first draft moved `dev` before creating production
and setting its policies, so a taken project id would have left `dev` moved with production
unguarded, and it reported a failed move as a success. Both fixed; the order is now folders,
policies, prod project, and the `dev` move last, polled to its end. Consequences recorded for the
first production deploy: with `storage` in the key policy, the buckets Google creates on its own
(Cloud Build, Cloud Run source uploads) must be created with a key first; every BigQuery dataset
needs a key; and a production Gemini Enterprise app must be in `eu`, not `global`.
The first run showed the Cloud Healthcare API is not a value `gcp.restrictNonCmekServices`
accepts; it was removed, and the FHIR dataset's key is held by Terraform and
`test/infra/keys.test.ts` instead. The script stopped before creating production or moving `dev`,
as the reviewed order intends. The second run completed with no drift: `EMA Flow`
(`folders/784801785369`), `non-production` (`folders/964747511982`) holding `dev`, `production`
(`folders/678854787982`) holding `khs-ema-flow-prod`, empty and without billing. After the move,
`dev`'s connector policy override is still effective, the key guard and bucket keys report no
drift, and both services are ready.

**A2. Data residency is not enforced, and something already lives outside the EU.**
`constraints/gcp.resourceLocations` allows every location. The project's Cloud Build staging
bucket, `sage-ship-509104-b8_cloudbuild`, is in `US`: every image build uploads the repository's
source there. The Gemini Enterprise app is `global`. For an EU regulatory-data product the
residency promise has to be a policy, not a habit. _Before features:_ move image builds to
regional Cloud Build in `europe-west4` with an EU staging bucket, then set
`gcp.resourceLocations` to `in:eu-locations` on the product folder. The order matters: the
policy first would break the build.

_Status 2026-09-22: closed for production; `dev` by decision without a policy._ Image builds
run in `europe-west4`, staged in an EU bucket, since PR #44 (2026-09-21) — proved on build
`07fb41b9…` — and the US staging bucket is deleted; every bucket in the project is in
`europe-west4`. `in:eu-locations` is set on the production folder (A1). `dev` carries no location
policy by decision: it hosts the global Gemini Enterprise trial.

**A3. The regulated record itself is on Google-managed keys.** The evidence bucket is CMEK;
the FHIR dataset (`encryptionSpec` empty), both BigQuery datasets, the regulated audit log
bucket, the Artifact Registry repository and the Terraform state bucket are not. The store and
the ledger are the record, so this is the wrong way round. A Cloud Healthcare dataset's
encryption is fixed when it is created, so in `dev` this means recreating the dataset and
re-seeding — which the project has done twice already and has a script for. _Before features:_
decide it; if yes, do it in `dev` now so production is built the proven way.

_Status 2026-09-22: closed._ All seven steps of the rollout are done and proven live: the FHIR
dataset, both BigQuery datasets (12 of 12 tables, fingerprints identical before and after), the
audit log, the image registry and every bucket in the project are on keys in
`ema-flow-dev-record`; the keys carry `prevent_destroy`, a 120-day destruction wait, an alert on
any version disabled or grant changed, and a deny policy that refuses key destruction and update
to every identity. The old dataset and the US build bucket are gone. The rollout is
`docs/design/cmek-rollout.md`. Its first draft was reviewed adversarially before anything ran,
and four findings would each have destroyed data — an ordinary deploy deleting the ledger after
an in-place key change, rows lost from BigQuery's streaming buffer, a renamed dataset destroying
its stores unseen, and a needless log bucket rebuild. Step 0 closes the first three for good:
nothing that holds a record can now be destroyed by an apply.

**A4. The manifest signing key is software-protected.** `manifest-signing` is
`ASYMMETRIC_SIGN` at protection level `SOFTWARE`. For evidence a regulator may rely on, the
best-in-class answer is an HSM-protected key. Manifests already signed stay verifiable against
the existing key version; new signing moves to a new HSM key. _Production gate_, and cheap
enough to do in `dev` at the same time as A3.

_Status 2026-09-21: closed._ `manifest-signing-hsm` (HSM, same algorithm) signs every run since
step 2; a signature was verified with openssl against the key's public key and a tampered hash
was rejected. The software key stays enabled so earlier manifests stay verifiable.

### B. The deploy path — who can change production

**B1. The deploy identity can be assumed from any branch.** The Workload Identity provider's
condition is `assertion.repository=='ogbetspp-coder/fhir_real_time_data_exchange'` and nothing
else, so a workflow on any branch of the repository can become the deployer. The deployer holds
`resourcemanager.projectIamAdmin`, `iam.serviceAccountAdmin`, `iam.serviceAccountUser`,
`cloudkms.admin` and fifteen other roles, nineteen in all — enough to grant itself anything. _Before
features:_ restrict the condition to `refs/heads/main` and the deploy workflow, and put the
deploy job behind a GitHub environment.

_Status 2026-09-21: closed._ The condition now requires the repository by numeric id, the ref
`refs/heads/main`, and the deploy workflow on main, all three. `scripts/gcp/deploy-identity.sh` is
its source of truth and `--check` reports drift; `test/infra/deploy-identity.test.ts` pins it. A
GitHub environment was not added: protection rules on environments need a paid plan for a
private repository, and the ref and workflow clauses already refuse what an environment would. Proved both ways on 2026-09-21: the deploy of PR #45's merge authenticated from main (run
`35632667284`), and the same workflow dispatched from a branch `probe/wif-refusal` was refused at
the token exchange — `failed to generate Google Cloud federated token` — and never reached the
build or the apply (run `35632866108`).

**B2. Third-party GitHub Actions are pinned by tag, not by commit.** Nine actions, including
`google-github-actions/auth`, the step that mints the deployer's credentials. A moved tag runs
new code with those credentials. _Before features:_ pin every action to a full commit SHA, and
let an update bot propose moves (C3).

_Status 2026-09-21: closed._ Every `uses:` line is pinned to the commit of the latest
release in their major version, with that release in a comment; the test fails on any tag.

**B3. Image builds run as the default compute service account** with
`cloudbuild.builds.builder`. A dedicated build service account with only what building needs is
the standard shape. _Before features_, together with A2's regional builds. Separately, the
legacy Cloud Build service account (`<number>@cloudbuild.gserviceaccount.com`) also holds
`cloudbuild.builds.builder`, granted by Google when the API was enabled and outside Terraform.
Nothing runs as it once builds name their own identity; removing the grant is part of C2's
trim.

_Status 2026-09-21: closed._ Builds run as `ema-flow-build-dev`, which holds exactly three
roles; the default compute account holds no project role.

**B4. Infrastructure changes are not planned before merge.** Added 2026-09-22. The deploy applies
unattended on merge, and every plan against live state this week was run by hand on a laptop. A
shared foundation cannot rely on that. The plan must run on every pull request, as an identity
that can read but not change, and must stop a merge that destroys anything until a person has
acknowledged it.

_Status 2026-09-22: closed (PR #74)._ The identity exists, and `Plan` runs on every pull request as a
required check. Since audit B15 it waits for a deploy that is changing live state to finish before
it plans, and fails, marked unreliable at the top of its summary, if a deploy changed live state
while it planned: a plan read mid-deploy proposed reverting that deploy (#141). `.github/workflows/plan.yml` plans every pull request with the deploy's own inputs
(one shared function in `scripts/gcp/deploy.sh`) against the deployed images and version, posts a
summary of resource addresses and actions, never values, and fails on any destroy or replace unless
the pull request is labelled `allow-replace`. It runs as `ema-flow-planner-dev`
(`scripts/gcp/plan-identity.sh`), in its own Workload Identity pool, `github-plan-pool`: the
deployer's grant covers every identity in the deployer's pool that names this repository, so a
pull-request provider there would have admitted pull requests to the deployer. The planner's custom
role was derived from a trace of every API call a live plan makes — all reads — and holds no
permission that returns a stored record (`test/infra/plan-identity.test.ts`).

Reviewed adversarially before enabling. No path from a pull request to the deployer or to any
write was found. Two defects were fixed: the verdict failed open if the summariser crashed, and
matching the plan's text missed some phrasings of a destroy (a tainted resource, a deposed
object). The verdict now comes from `terraform show -json` and fails closed. Two limits are
accepted and stated. A branch pull request can rewrite the plan workflow and use the planner's
token, so everything the planner can read (IAM policy, service configuration including the
entitlement map, the state's history) is readable by anyone who can push a branch: today that
is the owner alone, and the planner reads no record. And while one person both writes and
approves, the `allow-replace` label is a deliberate acknowledgement, not a second pair of eyes;
it becomes a control when a second reviewer is required through `CODEOWNERS`.

### C. Hardening — cheap, and overdue

**C1. Artifact Registry vulnerability scanning is disabled.** An image with a known critical
vulnerability would deploy without anyone being told. Turn scanning on. Binary Authorization
is already wired as the `enforce_binary_authorization` variable, off; turn it on once scanning
results exist to attest to.

_Status 2026-09-22: closed for development; production gets registry scanning._ The owner chose
the free route for `dev`: Google's open-source OSV-Scanner, pinned by version and SHA-256
(`scripts/ci/vuln-scan.sh`), on every pull request, on main and weekly. Any known vulnerability
in the npm or uv lockfiles fails the check (512 packages, none today; proved to fail on a lockfile
pinning a vulnerable lodash). The pinned base images are scanned and reported without failing,
since most operating-system advisories have no fixed package and fixes arrive as Dependabot
digest bumps. A weekly failure opens an issue. Artifact Analysis registry scanning, and Binary
Authorization on its results, move to the production gate, where deploys are rare.

**C2. Fifty-four APIs are enabled; Terraform declares twenty, and not all of those are used.**
Enabled with no declaration anywhere: GKE, GKE Backup, Filestore, Cloud SQL, Dataform, Cloud DNS,
Compute, OS Login, Network Connectivity, Datastore, Analytics Hub, BigQuery Migration and
Reservation, and more — several with their service agents already provisioned. Declared by
Terraform but referenced by nothing else in the repository: Secret Manager, Cloud Deploy,
Dataplex, BigQuery Data Transfer and Eventarc. Each is surface nothing monitors. Trim both lists
to what the product uses, and make Terraform the only list.

_Status 2026-09-22: closed._ Fifty-four enabled became forty-five, every one declared, and no API was enabled outside `infra/main.tf`. Of the fifteen measured unused, eight are disabled; five turned out to be held by a declared service (Google's umbrella service, Binary Authorization, Compute) and one is re-enabled by Cloud Build on every deploy, so those six are declared as dependencies rather than forced off with their holder. The list the deploy enables before Terraform runs is tested to be a subset of Terraform's. 30 days of request counts
per API decided it, not a reading of names: an API that served requests, or that Google enables
as a dependency of one that did, is declared in `infra/main.tf`, which is now the complete list
(39). Fifteen served no request, hold no resource and are referenced nowhere; they are dropped
from Terraform (without disabling — `disable_on_destroy` is false) and disabled once, without
forcing past a dependency, by a one-shot script deleted in refactor R1 (in history at `250d8a2`,
`scripts/gcp/api-trim.sh`); compare `gcloud services list --enabled` with `infra/main.tf` to look
for drift. Kept although idle: Vertex AI, for the agent, and Datastore, a
candidate home for the entitlement store.

**C3. No dependency update automation.** No Dependabot or Renovate configuration exists for npm,
Python, Docker base-image digests, Terraform providers or Actions. Digest pinning without an
update path means pinned and slowly rotting.

_Status 2026-09-21: closed._ `.github/dependabot.yml` proposes weekly, grouped updates for
Actions, npm, both uv projects, Docker base images and Terraform providers, each as a pull
request through the required checks.

**C4. The worker's Healthcare role is project-wide** (`healthcare.fhirResourceEditor` at
project level) while the query service's reader is dataset-scoped. Already on the roadmap's
production gate; do it with A3, since the dataset is being recreated anyway.

_Status 2026-09-21: closed._ The worker holds `fhirResourceEditor` on the record dataset only;
the project-level grant and the Document AI grant are gone (`test/infra/worker-identity.test.ts`).

**C5. The Terraform state bucket is readable by every project viewer and writable by every
editor**, through legacy project roles. State holds the entitlement map and every resource's
configuration. Restrict it to the deployer and the owner; CMEK it with A3.

_Status 2026-09-22: closed._ The state bucket is on the `platform-storage` key with every object
rewritten, its legacy project bindings are removed, and access is the deployer plus an explicit
bucket-level grant for the owner; old state generations expire (`scripts/gcp/storage-keys.sh`).

**C6. The Gemini Enterprise connector is outside the audit trail.** Data Access logs are off for
Discovery Engine, and the regulated sink does not include it. The query service's own audit
records do capture every tool call, so the evidence exists — but the connector's side of each
call does not. Add Discovery Engine, and Vertex AI once the agent deploys, to both.

_Status 2026-09-22: closed._ Discovery Engine has Data Access logs on and is in the regulated
sink's filter (`test/infra/audit-trail.test.ts`). Vertex AI joins when the agent deploys.

**C7. No essential contacts and no visible budget.** Google's security and billing
notifications have no named recipient (the Essential Contacts API is not enabled), and budgets
could not be read — the owner's account lacks access to the billing account from this project.
Set essential contacts at the organisation, and a budget with alerts on the billing account.

_Status 2026-09-23: open._ Needs billing account access (owner decision 4 below).

**C8. The validated store does not enforce profiles itself.** `disableProfileValidation` is
`true` on both stores; every write is validated by the worker (official validator and the
Healthcare API's `$validate`) before it is sent, so nothing unvalidated has been written. But a
write that bypassed the worker would not be checked. Enabling store-level enforcement against
the imported EMA profiles is defence in depth. It needs a test first, because it will refuse
writes the worker currently accepts if the two validators disagree.

_Status 2026-09-23: open._ Last in the order of work (9).

**C9. The official validator is not hermetic, and not locale-pinned.** Closed 2026-09-21 (PR #52);
the status below. Measured 2026-09-21 in a clean sandbox: it downloads **nine** packages on every
clean run, not the seven previously recorded — the two missed are the FHIR R5 core specification
itself and `hl7.fhir.xver-extensions`. Every content file of each registry download is
byte-identical to what the validator installs; only its own `.index.json` files differ. With the
network blocked and an empty cache it refuses to run, which is the check the fix will use. The same
run showed the validator adopting the machine's locale and jurisdiction (Denmark on the owner's
laptop), which is a second reproducibility gap to pin.

_Status 2026-09-21: closed, verified deployed._ Proved three ways: the CI gate passed offline
(every resource: twelve packages loaded, every one pinned, zero errors); Cloud Build started the
production image with `--network none` (`Jurisdiction: Global (Whole world)`, full package
summary, service started — build `eb873394…`); and the deploy's smoke run through the worker
persisted, with the sidecar's log showing no install and only policy refusals. It did not make
cold starts faster; see E1. Worse than first recorded: the deployed
sidecar downloaded all nine packages **on every cold start** and validated EMA content under
United States jurisdiction. The packages are now
pinned in `fhir/validator-packages.lock`, installed into the image, and the validator runs with
`-no-http-access`, a JVM proxy to nowhere and `-jurisdiction uv -locale en-US` — which also
closes a request-forgery path from submitted content to internal addresses (in the application
only: the deployed sidecar keeps Cloud Run's egress and the worker's service account,
`docs/roadmap.md`, "Open gaps"); the CI gate uses the same list and
the same JVM properties and fails on any download attempt; every image build proves the
validator starts with networking disabled. Details in `docs/validation/README.md`, "Official
validation gate".

**C10. A perpetual plan difference on the monitoring dashboard.** Every plan shows
`google_monitoring_dashboard.operations` changing: the API reformats the dashboard JSON
(adds `targetAxis`, drops zero positions) and Terraform re-applies it. Harmless, but noise in a
plan is where a real change hides. Write the JSON in the form the API returns.

_Status 2026-09-28: closed (audit B04, #125)._ Terraform ignores the JSON and
`scripts/ci/dashboard-drift.py` compares its meaning; the deploy replaces a dashboard that drifted.

**C11. Deploy failure issues were never closed.** The deploy workflow opens an issue on every
failure and nothing closed them: 27 were open on 2026-09-21, the oldest from the first day, each
for a failure long since fixed — noise that would bury a real one. Closed by hand, each with the
reason; the workflow should close its own once a later deploy succeeds.

_Status 2026-09-22: closed._ A successful deploy closes every open `deploy-failure` issue the
workflow opened, each with a link to the run that superseded it.

**C12. The worker's evidence-bucket role is object admin.** It creates objects; `objectCreator`
would do. The bucket's seven-year retention policy already stops it deleting or overwriting
evidence, so this is least privilege rather than a live exposure.

_Status 2026-09-22: closed._ `objectCreator`, granted before the old binding is removed; the
worker's only storage call on evidence is a single simple upload per artefact.

**C13. Project viewers can read the evidence and the approved submissions.** Every bucket still
carries Cloud Storage's legacy convenience bindings: `projectViewer` has read, `projectEditor` and
`projectOwner` have write, on every object. So anyone granted viewer on the project — for any
reason — can read signed evidence and approved submissions. Removing the bindings is the fix, but
the project owner's own storage access comes from them (removing them from the state bucket locked
the owner out of its permissions on 2026-09-21), so each bucket needs its explicit grants in place
first. Decide who reads the record, grant that explicitly, then remove the bindings.

_Status 2026-09-22: closed; enforced by every deploy._ The owner's decision: the record is read by
the services Terraform names and by the project's owners, nobody else. `scripts/gcp/record-readers.sh`
removes the project viewer and editor paths on every bucket (the four Terraform owns and the
agent's staging bucket) and the project reader and writer groups on both BigQuery datasets, and
keeps the owners' paths, whose removal locked the owner out of the state bucket before. It runs
in every deploy right after the apply, so a bucket or dataset created later is brought into line
on its first deploy. Reviewed before it ran: a failed read now fails the deploy instead of being
skipped, and a dataset's access list is replaced only if unchanged since it was read (an `If-Match` precondition on the API call; the `bq` tool was replaced after it printed non-JSON on a fresh CI runner and stopped the first deploy at this step, after the buckets and before anything else).
Cloud Healthcare has no per-dataset equivalent: project Viewer or Editor reads every FHIR store,
so the script reports how many principals hold either. Today none do.

### D. The repository

**D1. No licence.** The repository has no `LICENSE`. For a product whose value is a small,
sellable component, what a client may do with the code is the first question legal will ask.
The owner decides the terms; the default of no licence means all rights reserved, which is
defensible but should be stated.

_Status 2026-09-22: closed._ `LICENSE` states the code is proprietary to KHS Advisory LLC, all
rights reserved, use only under a separate signed agreement. `package.json` had declared
`Apache-2.0` — an open-source grant contradicting the owner's decision; it now says `UNLICENSED`
and `private: true`, so the package cannot be published to a registry by accident.

**D2. No `SECURITY.md` and no `CODEOWNERS`.** Cheap, and expected by any vendor assessment.

_Status 2026-09-22: closed._ `SECURITY.md` gives a private reporting route, scope, and pointers to
the recorded controls; its address, `security@khsadvisory.com`, is a placeholder marked as such
and confirming it is on the production gate. `.github/CODEOWNERS` names the owner on every path.

**D3. Administrators can bypass branch protection** (`enforce_admins` is off). Acceptable for
one person; stated so that it is a decision and not a default.

_Status 2026-09-28 (audit B15): still off, the owner's decision._ What the bypass reaches is
narrower: a push to `main` by an administrator deploys only once CI's run on that commit has
succeeded in every job but `Renderer` (`scripts/ci/workflow-runs.mjs`, in the deploy job before any
credential is taken), so the bypass skips the pull request and CI's checks do not, but only CI's:
`Plan` and `Vulnerabilities` are other workflows, and the deploy does not wait for them. `Plan` runs
on pull requests only, so a direct push has none; the push's own `Vulnerabilities` run may be red
while its deploy goes on. `Renderer` has been a required pull-request check since 2026-09-28; since
refactor R1 the deploy no longer waits for it, since nothing the deploy ships reads its verdict.
Branch protection still requires no review, does not require a branch to be up to date with `main`,
and binds the `Agent` check to no app.

**D4. Secret scanning and push protection are unavailable** on a private repository without
GitHub's paid secret protection. Nothing secret is committed today — the repository holds
identifiers, never credentials — but nothing would stop one.

_Status 2026-09-23: closed for development; push protection stays unavailable._ The free route,
as for C1: gitleaks, pinned by version and SHA-256 (`scripts/ci/secret-scan.sh`,
`test/ci/scanner-pins.test.ts`), runs in the required `Vulnerabilities` check on every pull request
(its commits and the tree), on every push to main (the pushed commits) and weekly (the whole
history), and fails on any finding. The whole history and the tree scanned clean on 2026-09-23 but
for one false positive, a `curl --user` line in `deploy.sh` whose password is a command
substitution; `.gitleaks.toml` exempts exactly that rule, file and captured text. The fake tokens in
the tests and the `GOCSPX-` patterns in `agent/deploy/authorization.sh` match no default rule and
need no exception. Proved to fail on a planted fake GitHub token, AWS key and OAuth client secret.
It does not stop a push, so a secret that reaches a branch must still be rotated.

### E. Performance

**E1. Both services scale to zero.** Measured 2026-09-21.

- **Query service** — what a Gemini Enterprise user waits for. Cold start about 3 to 5 seconds
  from process start to ready; a warm tool call 4 to 10 milliseconds, one that reads the FHIR
  store about 200 milliseconds. For production, a minimum of one instance removes the cold start
  for a small, fixed monthly cost; in `dev` it can stay at zero.
- **Worker** — 57 seconds to ready, of which the validator sidecar takes 49, before and after C9.
  It is package loading and indexing, not the download. Startup CPU boost is **already on** for
  the validator container, so the 49 seconds are measured with it; an earlier draft of this
  document listed it as a candidate. The real candidates are building the validator's package
  indexes into the image at build time, so they are not regenerated on every start, and a
  minimum instance. Nothing waits on the worker interactively today.

**E2. Every deploy did work that nothing had asked for.** Added 2026-09-22. Every merge
deployed, including documentation: 41 deploys in two days. Each re-imported the full profile
set. After the profile bucket moved to a customer-managed key, that step went from 4 to 23
minutes: Cloud Storage omits checksums from listings of CMEK objects, so every sync fetched
5,074 objects one at a time.

_Status 2026-09-22: closed; first proof on the next two deploys._ Merges touching only docs,
tests, the agent or Zone A no longer deploy (`test/ci/deploy-trigger.test.ts` pins that no
deployable path is ignored). The profile sync and import run only when the generated set's
fingerprint differs from the one recorded after the last successful import, or the target
store does not hold the expected 753 StructureDefinitions, or `FORCE_PROFILE_IMPORT` is set
(`test/ci/profile-import.test.ts`). The first deploy after this records the fingerprint; the
second should skip both steps.

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

1. ~~**Landing zone.**~~ **Decided and done 2026-09-22** (A1): the `EMA Flow` folder, `dev` in
   `non-production`, `khs-ema-flow-prod` empty in `production`.
2. ~~**CMEK on the record in `dev`**~~ **Decided and done 2026-09-22** (A3): the dataset rebuilt
   on a key and re-seeded.
3. ~~**HSM signing key** in `dev`~~ **Decided and done 2026-09-21** (A4).
4. **A budget** on the billing account, and who receives its alerts. Needs billing account access
   the deploy identity does not and should not have. Open (C7).
5. ~~**The licence**~~ **Decided 2026-09-22** (D1): proprietary, all rights reserved.
6. **The real address for security alerts** — the repository variable still holds the example
   `you@khsadvisory.com`, so the deploy of 2026-09-21 created an alert channel that points at
   it. Open; on the production gate.
