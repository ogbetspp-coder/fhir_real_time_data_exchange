# Customer-managed keys on the record — rollout plan

Status: **plan, revised after independent review; step 0 in progress.** Foundations review
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
4. **The log bucket did not need recreating.** Its key can be set in place.

Each is fixed below. Step 0 exists because of the first three.

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
| Audit log bucket | In place: `cmek_settings` on the existing bucket                                                       | None; the key applies to new entries                                                            | None                   |
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
to change, 0 to destroy` — four resources switching deletion protection on, and one pre-existing
   perpetual difference on the monitoring dashboard (the API reformats its JSON; not introduced
   here, noted so it does not hide a real change in later plans).
1. **Keys, grants, protections.** New ring and five keys at 120-day destruction, the HSM signing
   key, the service agent grants (each agent created with `google_project_service_identity` where
   it may not exist yet), the alert. Then the deny policy, applied by the organisation
   administrator. _Verify:_ every key exists with its protection level and destroy duration; each
   agent holds exactly its grant; the deny policy is in force; disabling a version of a throwaway
   key raises the alert (the throwaway key cannot be deleted afterwards — it is named as such).
2. **Signing on the HSM key.** Worker's `KMS_MANIFEST_KEY` points at `manifest-signing-hsm`
   version 1; its grant on the old key is removed. _Verify:_ the deploy's smoke run persists, and
   its manifest signature verifies against the new key's public key.
3. **BigQuery in place.** Pause deploys (`gh workflow disable`), confirm no run is in flight, and
   wait until `bq show` reports no streaming buffer on any table. Record each table's row count and
   a content hash; snapshot each table; convert each to the `ledger-analytics` key in place;
   recompute count and hash. Set both datasets' default key and the ledger table's
   `encryption_configuration` in Terraform to the **exact key string** the tables now report, plan,
   and require no replacement — a different string form would plan one, and step 0 would make that
   plan fail rather than apply. Merge, then re-enable deploys. _Verify:_ identical counts and
   hashes; `bq show` reports the key on every table; the plan shows no replacement.
4. **Registry.** A second repository block, `ema-flow-images`, with the `artifacts` key, created
   after the Artifact Registry agent holds its grant. Every hard-coded `ema-flow` repository name
   switches: `deploy.sh` (repository, import, digest lookup), both Cloud Build files'
   `_REPOSITORY`, and the build identity's writer grant. _Verify:_ services run images from the new
   repository by digest. Then the old repository is deleted — from which point rolling back to a
   revision older than the switch is no longer possible, which is stated in the pull request.
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
6. **Audit log bucket in place.** The logging service account's grant, then `cmek_settings` on the
   existing bucket. _Verify:_ `gcloud logging buckets describe` reports the key; new entries
   arrive.
7. **Storage.** Default key on the profiles, build-staging and state buckets; existing objects
   rewritten. The state bucket is created by `deploy.sh`, not Terraform, so its key and IAM are set
   by script: the legacy project viewer, editor and owner bindings removed, leaving the deployer
   (through its project `storage.admin`) and the owner. _Verify:_ every object reports the key; a
   project viewer can no longer read state.

After step 7, with the product folder in place (foundations A1), the organisation policies
`gcp.restrictNonCmekServices` and `gcp.restrictCmekCryptoKeyProjects` on the folder make a
Google-managed-key resource impossible to create by accident — the policy form of this plan.

## Rollback

Steps 0, 1, 3, 6 and 7 are additive or in place: revert the pull request, and for step 3 restore
from the snapshot if a count or hash differs. For steps 2 and 5 the old key or dataset is kept
until the new one is verified, so rolling back is pointing the configuration at the old one and
deploying. Step 4's rollback ends when the old repository is deleted, and says so.

## Not in scope

Cloud External Key Manager (keys held outside Google), key access justifications, and per-client
keys for a multi-tenant deployment. Each is a real option for a client that asks; none is needed
to prove the pattern.
