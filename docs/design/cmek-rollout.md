# Customer-managed keys on the record — rollout plan

Status: **all seven steps done and proven in `dev`, 2026-09-22**; a new environment is created on
the keys from its first deploy ("A new environment", below; audit I-10, 2026-09-28). Revised after independent review. Foundations review
findings A3 (the record on Google-managed keys), A4 (software signing key), C4 (worker's
Healthcare role project-wide) and C5 (Terraform state open to project viewers). Owner decision
2026-09-21: do it in `dev` now, so production copies a setup that has been proven rather than
designed.

## What changes, and what it costs

Today the evidence and submission buckets are encrypted with our key; the record itself — the FHIR
dataset, the ledger, the analytical projection, the retained audit log — and the image registry
and Terraform state are on Google-managed keys. After this rollout every one of them is on a key
this product controls, in `europe-west4`, and the manifest signing key is held in an HSM.

The cost is a new way to lose everything, and the plan is shaped around it. From the Cloud
Healthcare API documentation: if a dataset's key becomes unavailable — disabled, scheduled for
destruction, or its grant removed — the dataset keeps serving for one hour, is then disabled, and
**after 30 days is permanently deleted with every store in it**. Scheduling a key version for
destruction makes it unusable at once, even though the destruction itself can be cancelled; so a
long destruction window protects the key, not the dataset. The dataset is protected only by making
the key hard to disable and by knowing within the hour if it happens.

## What the review changed

The first draft of this plan was reviewed adversarially against the provider source (v8.3.0, as
locked) and the live estate before anything ran. Four findings would each have destroyed data:

1. **An ordinary deploy would have deleted the ledger.** In the provider, a table's
   `encryption_configuration` forces replacement. After converting the live table by hand, the
   next unattended apply would have planned "destroy and create", and `deletion_protection`
   defaulted to `false`, so it would have gone ahead. Setting it in the same change would not
   have helped: the destroy check reads the old state.
2. **The in-place copy could lose rows.** The ledger had rows in BigQuery's streaming buffer, which
   a copy may not include for up to 90 minutes.
3. **"Recreate under a new name" would have destroyed the old resource.** Renaming a Healthcare
   dataset, a registry or a log bucket in Terraform is destroy-and-create — and because the FHIR
   stores live outside Terraform, the plan would have shown one dataset, never the stores in it,
   so the draft's own "no data-holding resource destroyed" rule could not have caught it.
4. ~~**The log bucket did not need recreating.** Its key can be set in place.~~ **Wrong, as the
   API showed:** applied in step 6, the update was refused — "Cannot add a CMEK key to a non-CMEK
   bucket. CMEK must be enabled at bucket creation." The draft's original approach, a new bucket,
   was right. Recorded rather than erased: the review was valuable and this finding of it was not.

Each of the first three is fixed below; step 0 exists because of them. The fourth was reversed by the API.

## Key design

One key per purpose, each granted only to the one service agent that uses it, so a mistake or a
compromise on one key affects one kind of data, and data can be crypto-shredded by kind.

| Key                    | Ring                                 | Purpose                                          | Granted to                          |
| ---------------------- | ------------------------------------ | ------------------------------------------------ | ----------------------------------- |
| `fhir-record`          | `ema-flow-<env>-record` (new)        | Cloud Healthcare dataset                         | Healthcare service agent            |
| `ledger-analytics`     | `ema-flow-<env>-record`              | Both BigQuery datasets                           | BigQuery encryption service account |
| `audit-logs`           | `ema-flow-<env>-record`              | Retained audit log bucket                        | Logging service account             |
| `artifacts`            | `ema-flow-<env>-record`              | Image registry                                   | Artifact Registry service agent     |
| `platform-storage`     | `ema-flow-<env>-record`              | Profiles, build staging, Terraform state buckets | Cloud Storage service agent         |
| `evidence-encryption`  | `ema-flow-<env>-evidence` (existing) | Evidence and submission buckets                  | Cloud Storage service agent         |
| `manifest-signing-hsm` | `ema-flow-<env>-evidence` (existing) | Signing run manifests, HSM                       | Worker (`signerVerifier`)           |

The existing `manifest-signing` key stays, enabled, so every manifest signed with it remains
verifiable; nothing new is signed with it. The algorithm is unchanged (`RSA_SIGN_PSS_2048_SHA256`,
available at HSM protection in `europe-west4`), so the verification code does not change. A key's
protection level cannot change after creation, hence a new key rather than a new version.

Every encryption key rotates every 90 days. Rotation never re-encrypts existing data, so **no key
version is ever destroyed**: every version stays enabled for the life of the data it encrypted.

The FHIR notification topics are deliberately left on Google-managed keys: they carry resource
names only (`sendFullResource: false`), never content. Stated so it is a decision, not an omission.

## Protection against losing a key

1. **Terraform will not destroy a key.** Every key carries `prevent_destroy` (the two existing keys
   already do).
2. **Destruction waits as long as allowed — on the new keys.** `destroy_scheduled_duration` is
   fixed when a key is created, so the five new keys get 120 days and the two existing keys keep 30. Changing it on an existing key would force a replacement that `prevent_destroy` refuses.
3. **No ordinary identity can destroy, disable, or ungrant a key.** An IAM deny policy on the
   project denies `cloudkms.googleapis.com/cryptoKeyVersions.destroy` and
   `cloudkms.googleapis.com/cryptoKeyVersions.update` (which is how a version is disabled) to every
   principal, and `cryptoKeys.setIamPolicy` and `keyRings.setIamPolicy` to every principal except
   the deployer, which manages the grants through reviewed Terraform. All four permissions are on
   IAM deny's supported list. Deny policies need `iam.denyAdmin`, which the deploy identity does
   not and should not hold, so the policy is applied by the organisation administrator with
   `scripts/gcp/key-guard.sh`, outside the unattended deploy, like the deploy identity's condition.
4. **Disabling, scheduling destruction of, or ungranting a key pages within minutes.** A log-based
   alert on Cloud KMS Admin Activity audit logs, which are always on: `DestroyCryptoKeyVersion`;
   `UpdateCryptoKeyVersion` where `protoPayload.request.cryptoKeyVersion.state` is not `ENABLED`;
   and `SetIamPolicy` on any key or ring. Delivered to the entitlement-denial alert's e-mail
   channel. The hour before the dataset is disabled is the window this alert exists for. If the
   `audit-logs` key itself is the one disabled, log entries buffer for about three hours and are
   then discarded, which is one more reason the alert must fire within minutes.
5. **The runbook says what to do**: restore the version, cancel the destruction, or restore the
   grant — and check the dataset within the hour.

## Per resource: in place or recreated

| Resource         | Method                                                                                                 | Data at risk                                                                                    | Downtime               |
| ---------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- | ---------------------- |
| Ledger table     | In place, with writers stopped, buffer empty, snapshot taken                                           | Ledger rows: guarded by step 0, a snapshot, and a count-and-hash check                          | Deploys paused         |
| Analytics tables | In place, same procedure                                                                               | None beyond the ledger's; a projection of the store                                             | Deploys paused         |
| FHIR dataset     | **New resource block** beside the old; stores reconciled into it; switch; old removed from state, kept | Store contents: synthetic, re-seeded by script; history resets, as on the two earlier rebuilds  | Minutes, at the switch |
| Audit log bucket | **New bucket** created with the key; sink moved to it; old one kept, receiving nothing (step 6)        | None; entries before the switch stay in the old bucket until they age out                       | None                   |
| Image registry   | **New resource block** beside the old; builds and deploy switched; old deleted after one good deploy   | None; images are rebuilt by every deploy — but rollback to revisions older than the switch ends | None                   |
| Storage buckets  | Default key set in place; existing objects rewritten                                                   | None                                                                                            | None                   |
| Signing key      | New HSM key; worker switched                                                                           | None; old key kept for verification                                                             | None                   |

## Order and verification

Each step is its own pull request. **Each is `terraform plan`ned against the live state, with the
deploy's own inputs, before merge, and the plan's summary line goes in the pull request.** A plan
that destroys or replaces anything the step does not intend blocks the merge. Step 0 makes such a
plan fail outright for every resource that holds a record, so the rule no longer rests on a person
reading the plan.

0. **Nothing that holds a record can be destroyed by an apply.** `deletion_protection` on by
   default; `prevent_destroy` on the FHIR dataset, the ledger dataset and table, the audit log
   bucket, and the evidence and submission buckets. _Verified by plan before merge:_ `0 to add, 5
to change, 0 to destroy` — four resources switching deletion protection on, and one
   pre-existing perpetual difference on the monitoring dashboard (the API reformats its JSON; not
   introduced here, noted so it does not hide a real change in later plans).
   _Done 2026-09-21 (PR #55); deploy and smoke run green. Proved live during step 3: a plan that
   would have replaced the ledger table failed with `Instance cannot be destroyed` instead of
   applying._
1. **Keys, grants, protections.** New ring and five keys at 120-day destruction, the HSM signing
   key, the service agent grants, the alert. (As first written, only the Healthcare agent was
   created before its grant; the others existed in `dev` and would not in a new project. Audit
   I-10 corrected that: "A new environment", below.) Then the deny policy, applied by the organisation
   administrator. _Verify:_ every key exists with its protection level and destroy duration; each
   agent holds exactly its grant; the deny policy is in force; disabling a version of a throwaway
   key raises the alert (the throwaway key cannot be deleted afterwards — it is named as such).
   _Done 2026-09-21 (PR #56), verified live: five record keys at 90-day rotation and a 120-day destroy wait (Cloud KMS accepted 120 days), `manifest-signing-hsm` version 1 enabled at HSM protection, each service agent holding exactly its one grant, the alert enabled. The deny policy awaits the organisation administrator (`scripts/gcp/key-guard.sh`)._
2. **Signing on the HSM key.** Worker's `KMS_MANIFEST_KEY` points at `manifest-signing-hsm`
   version 1; its grant on the old key is removed. _Verify:_ the deploy's smoke run persists, and
   its manifest signature verifies against the new key's public key.
   _Done 2026-09-21 (PR #57). Run `987e8ced-99c5-4bf4-9f5d-02568aea6e9d` (the deploy's smoke run): signed by `manifest-signing-hsm/cryptoKeyVersions/1`, protection HSM; its manifest hash recomputed with the repository's canonical JSON matches; the signature verifies with openssl against the version's public key; a hash altered by one bit is rejected. Run `c0648d78…`, signed by the software key before the switch, still verifies._
3. **BigQuery in place.** Pause deploys (`gh workflow disable`), confirm no run is in flight, and
   wait until `bq show` reports no streaming buffer on any table. Record each table's row count and
   a content hash; snapshot each table; convert each to the `ledger-analytics` key in place;
   recompute count and hash. Set both datasets' default key and the ledger table's
   `encryption_configuration` in Terraform to the **exact key string** the tables now report, plan,
   and require no replacement — a different string form would plan one, and step 0 would make that
   plan fail rather than apply. Merge, then re-enable deploys. _Verify:_ identical counts and
   hashes; `bq show` reports the key on every table; the plan shows no replacement.
   _Done 2026-09-21. Deploys paused at 19:37; every streaming buffer drained by 20:26 — the ledger's
   four buffered rows among them, which is why the wait mattered. All twelve tables converted by
   `scripts/gcp/bq-cmek-convert.sh` (retired once used, by audit I-11; it is in the history at
   `709e50d`), each with a snapshot first (expiring after 14 days, and on
   Google-managed encryption until then) and identical row count and content fingerprint before
   and after (ledger: 31 rows). Planned before conversion, the change failed with `Instance cannot
be destroyed` on the ledger table — step 0 refusing the replacement the provider wanted;
   planned after, `0 to add, 3 to change, 0 to destroy`: the two dataset defaults and the known
   dashboard difference._
4. **Registry.** A second repository block, `ema-flow-images`, with the `artifacts` key, created
   after the Artifact Registry agent holds its grant. Every hard-coded `ema-flow` repository name
   switches: `deploy.sh` (repository, import, digest lookup), both Cloud Build files'
   `_REPOSITORY`, and the build identity's writer grant. _Verify:_ services run images from the new
   repository by digest. Then the old repository is deleted — from which point rolling back to a
   revision older than the switch is no longer possible, which is stated in the pull request.
   _Done 2026-09-21 (PR #59). The first deploy failed in preflight on a transient GitHub error
   fetching its sign-in token ("upstream connect error … overflow"), not on the identity
   condition; re-run, it built all three images into `ema-flow-images` (Customer-managed key,
   `artifacts`), applied, and the smoke run persisted. The repository name now lives once, in
   `deploy.sh`. The old `ema-flow` repository is removed in step 5a._
5. **FHIR dataset, and the worker's role.** In three moves, so nothing points at an empty dataset:
   (a) a second dataset block with the `fhir-record` key, created empty, nothing switched;
   (b) the old target store's BigQuery stream detached, then the reconcile script run against the
   new dataset — stores, stream into the (now CMEK) analytics tables, notifications, profiles —
   and the demonstration re-seeded; (c) the switch: the dataset output, both services'
   environment, the query service's reader grant, and the worker's `fhirResourceEditor` bound on
   the new dataset only; the worker's unused `documentai.apiUser` removed, with a least-privilege
   test pinning its exact role set; the old dataset removed from state with
   `removed { lifecycle { destroy = false } }` and kept until verified, then deleted by hand.
   _Verify:_ smoke run persists; the four demonstration documents answer through the query service
   under the same bundle ids the entitlement map names; **new rows stream into CMEK tables — the one
   behaviour the documentation does not cover, and the reason (b) comes before (c)**; notifications
   still fire. The Healthcare API allows ten CMEK datasets per project per 30 days; this uses one.
   _(a) done 2026-09-21 (PR #61): `ema-flow-dev-fhir-record` created empty on the `fhir-record`
   key. (b) done by hand the same day: both stores reconciled into it; their configurations hash
   identically to the old stores'; the validated store holds the same 753 StructureDefinitions and
   969 CodeSystems; the source bundle seeded. Already observed before (c): the Healthcare stream
   writes into BigQuery tables on our key — step 3's smoke run landed a row in the converted
   `Bundle` table. (c) planned against the live state: 2 to add, 3 to change, 3 to destroy — the
   two dataset-scoped grants, both services' `HEALTHCARE_DATASET_ID` (the only attribute that
   changes on them) and the dashboard difference; the three destroyed are IAM bindings (the old
   reader grant being replaced, the worker's project-wide editor role, its unused Document AI
   role); and `epi` "will no longer be managed by Terraform, but will not be destroyed"._
   _(c) done 2026-09-21 (PR #62), and step 5 complete. The first deploy after the switch applied
   it and then failed seeding with a 403: the deploy identity's FHIR write access had been a grant
   made by hand on the old dataset, outside Terraform, and the plan and its review both missed it.
   Declared in Terraform on the new dataset (PR #64); the next deploy passed end to end. Then:
   the demonstration re-seeded (four runs, same bundle ids); `find_product` returns the three
   labels, `truncated: false`; `get_provenance` returns the version 2 record; **five rows streamed
   from the encrypted dataset into the encrypted `Bundle` table** — the behaviour the
   documentation does not cover; notifications fired (59 publish operations in the hour). The old
   dataset — holding only the superseded synthetic copy, referenced by nothing — was deleted. The
   project's only FHIR dataset is now on the `fhir-record` key. Lesson recorded: before migrating
   a resource, diff its live IAM against Terraform._ The `removed` block for `epi`, and
   `HEALTHCARE_DATASET_OVERRIDE`, which pointed the reconcile and bootstrap scripts at the new
   dataset during (b), were dropped by audit I-11 once nothing needed them.
6. **Audit log bucket, recreated.** A second bucket, `ema-flow-<env>-regulated-audit-cmek`,
   created with the `audit-logs` key, same retention and lock setting, `prevent_destroy`; the sink
   moved to it. The old bucket stays managed and protected, receiving nothing, keeping its entries
   until they age out. _Verify:_ the new bucket reports the key; new entries arrive in it.
   _2026-09-21: first attempted in place (PR #65); the deploy failed at the targeted apply because
   the API refuses a key on an existing bucket, which left every deploy failing at that step until
   this was fixed. No service was affected: the failure precedes the build and the apply._
   _Done 2026-09-21 (PR #67): `ema-flow-dev-regulated-audit-cmek` created with the `audit-logs`
   key, 2,555-day retention, `prevent_destroy`; the sink moved to it; the old bucket kept,
   managed and protected. Planned against live state first: 1 to add, 4 to change, 0 to destroy.
   Checked 2026-09-28: the new bucket reports the `audit-logs` key and holds entries from that
   day; the old one holds none written since 2026-09-22. Since audit I-11 the old bucket, like
   the software signing key, is declared for `dev` alone (`count`, with a `moved` block so `dev`'s
   is kept, not destroyed): no other environment ever wrote to it._
7. **Storage.** Default key on the profiles, build-staging and state buckets; existing objects
   rewritten. The state bucket is created by `deploy.sh`, not Terraform, so its key and IAM are set
   by script: the legacy project viewer, editor and owner bindings removed, leaving the deployer
   (through its project `storage.admin`) and the owner. _Verify:_ every object reports the key; a
   project viewer can no longer read state.
   _2026-09-21, in progress. The two buckets outside Terraform are done, by
   `scripts/gcp/storage-keys.sh`: the state and agent-staging buckets default to the
   `platform-storage` key and every live object is under it; old state generations now expire (20
   newer, or 30 days — there were 168, kept forever); the state bucket's legacy convenience
   bindings are gone, leaving the deployer (project `storage.admin`) and an explicit bucket-level
   `storage.admin` for the owner. The script's first run removed the owner's legacy binding before
   granting the explicit one and locked the owner out of the bucket's permissions — the project
   owner's storage access comes from those bindings; recovered with a temporary project grant,
   since removed, and the script now grants before it revokes._
   _2026-09-22, done. The profile and build-staging buckets gained their `encryption` block in
   Terraform (PR #68), planned against live state first: two in-place updates, nothing destroyed.
   After the deploy (run 35671971083, smoke run passed) every existing object was rewritten under
   the key — 5,074 in profiles, 15 in build-staging — and sampled objects report
   `platform-storage` version 1. `storage-keys.sh --check` reports no drift, and **no bucket in
   the project is left on a Google-managed key.** The key-guard deny policy was applied by the
   owner the same night and `key-guard.sh --check` reports it matches._

The policy form of this plan has been in force on the **production** folder since 2026-09-22
(`scripts/gcp/landing-zone.sh`, foundations A1): `gcp.restrictNonCmekServices` refuses a new
bucket, BigQuery dataset or image repository without a customer-managed key, and
`gcp.restrictCmekCryptoKeyProjects` a key from outside production. `dev`, in the non-production
folder, carries neither.

## A new environment

A new project deploys on the keys from its first deploy (audit I-10). `deploy.sh init` makes the
record ring and `platform-storage` key before the state bucket, which it creates on that key, and
`apis` imports them; `init` refuses a state bucket on another key. The Artifact Registry agent is
created by `google_project_service_identity`, Logging's is read from the project's Logging
settings, and `apis` asks BigQuery for its agent before the first apply; each is created on that
request. `apis` also creates the custom role before `apply` binds it. `dev`'s pre-step-6 audit
bucket and pre-step-2 software key are declared for `dev` alone. Not yet rehearsed in a project
under the production folder.

## Rollback

Steps 0, 1, 3 and 7 are additive or in place: revert the pull request, and for step 3 restore
from the snapshot if a count or hash differs. Step 6 made a new bucket and moved the sink to it;
the old bucket is kept, so rolling back is pointing the sink at it again. For steps 2 and 5 the
old key or dataset was kept until the new one was verified, so rolling back was pointing the
configuration at the old one and deploying; step 5's old dataset has since been deleted. Step 4's
rollback ended when the old repository was deleted, and said so.

## Not in scope

Cloud External Key Manager (keys held outside Google), key access justifications, and per-client
keys for a multi-tenant deployment. Each is a real option for a client that asks; none is needed
to prove the pattern.
