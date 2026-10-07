# infra

The Terraform configuration for one EMA Flow environment on Google Cloud: the worker, query and
approval signer Cloud Run services, the pipeline workflow, the Healthcare dataset, the evidence,
submission, profile and approval heads buckets, the BigQuery analytics and ledger datasets, the customer-managed keys, the
service accounts and their bindings, the audit log sink and bucket, monitoring, and the API list;
and the Word drawing's identity, key, record bucket, topic and Cloud Build trigger
(`word-drawing.tf`, `docs/design/certified-word-drawing.md`).
`docs/architecture.md` describes what these pieces do; this file describes the configuration.

Two things an environment needs are deliberately not here:

- **The R5 FHIR stores.** The Google provider rejects `version = "R5"`, so
  `scripts/gcp/reconcile-fhir-stores.sh` creates and patches them over REST inside the dataset
  Terraform owns, refuses any R4 substitution, and never deletes a store. The services' FHIR
  grants are bound on each store in Terraform, so `deploy.sh` creates a missing store before it
  applies; a project's first deploy creates the dataset and fails at those grants, and the next
  one completes. Until phase 2 of audit B04, the worker's and query service's older dataset-wide
  grants stay beside the narrow ones (marked TRANSITIONAL); the least-privilege tests prove the set
  that remains once they are removed (`test/infra/transitional-grants.ts`).
- **The identities that run Terraform.** The deployer and the read-only planner are bootstrapped
  outside this configuration (`scripts/gcp/deploy-identity.sh`, `scripts/gcp/plan-identity.sh`).

## How it is applied

Only by the deploy workflow. A merge to `main` that touches a deployable path runs
`.github/workflows/deploy.yml`, whose `deploy` job authenticates as the deployer through Workload
Identity Federation (admitted only for `main` and that workflow) and runs `scripts/gcp/deploy.sh`.
That script is the only place the inputs below are assembled (`tf_deploy_vars`): it builds the
images, resolves their digests, and applies with the deployed commit as `service_version`.
Before applying it refuses a placeholder alert recipient, and a missing one outside `dev` (whose
alerts page no one until it is given a real address; owner decision, 2026-09-28), and asks Resource Manager
(`testIamPermissions`) whether the deployer holds every permission the apply needs
(`APPLY_PERMISSIONS`): an apply missing one fails part-way, after its independent changes.

Every pull request is planned first, by `.github/workflows/plan.yml`, as the read-only planner,
with the same inputs and against the live state. The check fails on any destroy or replace until
the pull request carries the `allow-replace` label. Do not apply by hand; run `terraform fmt` and
`terraform validate` locally:

```bash
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false && terraform -chdir=infra validate
```

State is in `gs://<project>-ema-flow-tfstate`, prefix `terraform/state`, on a customer-managed
key, readable only by the deployer and the owner (the planner reads it through `objectViewer`);
`deploy.sh init` creates the bucket on that key and refuses one off it, and `apis` imports the key
ring and key.
Resources that hold the record (keys, the dataset, the ledger) carry `prevent_destroy`.

## Environments

| Environment  | Project               | State                                                                                           |
| ------------ | --------------------- | ----------------------------------------------------------------------------------------------- |
| `dev`        | `sage-ship-509104-b8` | Deployed on every deployable merge to `main`, in `europe-west4`; in the `non-production` folder |
| `validation` | none yet              | Accepted by the `environment` validation; not deployed                                          |
| `prod`       | `khs-ema-flow-prod`   | Created empty, without billing, in the `production` folder (EU location policy); not deployed   |

`environment` has no default: every plan, apply and import in `deploy.sh` passes it
(`EMA_FLOW_ENVIRONMENT`, `dev` in both workflows). `prod` also sets a minimum of one instance on
each Cloud Run service. An environment's own inputs (the one project it may deploy to,
`QUERY_LOG_REJECTION_REASON`, `ALLOW_SYNTHETIC_SOURCES`, `REQUIRE_ALERT_RECIPIENT`) are in
`scripts/gcp/environments/<environment>.env`, which `deploy.sh` reads for the plan and the deploy
alike; a missing file is refused.

The operations dashboard's JSON is ignored by Terraform, because the Monitoring API rewrites it
and every plan showed a change. `scripts/ci/dashboard-drift.py` compares its meaning instead: the
pull request's plan summary says whether the dashboard differs from its configuration, and the
deploy replaces it when it does.

## Inputs

Set by `deploy.sh` on every plan and apply unless marked "default".

| Name                              | Set by                                                                                    | Purpose                                                                                                                    |
| --------------------------------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `project_id`                      | `GCP_PROJECT_ID`                                                                          | Project for this environment                                                                                               |
| `region`                          | `GCP_REGION` (`europe-west4`)                                                             | Region of every regional resource                                                                                          |
| `environment`                     | `EMA_FLOW_ENVIRONMENT`                                                                    | `dev`, `validation` or `prod`; used in names and labels                                                                    |
| `worker_image`                    | built image, by digest                                                                    | Worker container; its digest becomes `IMAGE_DIGEST` in every signed run manifest                                           |
| `validator_image`                 | built image, by digest                                                                    | HL7 validator sidecar container; its digest becomes `VALIDATOR_IMAGE_DIGEST` in every signed run manifest                  |
| `query_image`                     | built image, by digest                                                                    | Query service container; its digest becomes `IMAGE_DIGEST` in every audit record                                           |
| `signer_image`                    | built image, by digest                                                                    | Approval signer container; its digest is named in every statement it signs (`signer.tf`)                                   |
| `service_version`                 | the deployed commit's full 40-hex SHA (a plan precondition)                               | `QUERY_SERVICE_VERSION` on the query service and `GIT_COMMIT` on the worker                                                |
| `deployer_account`                | the active service account                                                                | Granted run.invoker on the worker (smoke run), actAs on the build account, FHIR editor                                     |
| `query_invokers`                  | `QUERY_INVOKERS`                                                                          | Members granted run.invoker on the query service                                                                           |
| `query_token_creators`            | `QUERY_TOKEN_CREATORS`                                                                    | Members who may impersonate the caller service account                                                                     |
| `query_entitlements_json`         | `QUERY_ENTITLEMENTS_JSON`                                                                 | Entitlement map: principal to the bundles it may read                                                                      |
| `query_oauth_client_ids`          | `QUERY_OAUTH_CLIENT_IDS`                                                                  | OAuth client ids whose access tokens the query service accepts                                                             |
| `alert_notification_email`        | `ALERT_NOTIFICATION_EMAIL` (not passed to dev)                                            | Paged by every alert (key made unavailable, failed run, entitlement denials); this or a channel is required outside dev    |
| `alert_notification_channels`     | `ALERT_NOTIFICATION_CHANNELS` (optional; not passed to dev)                               | Existing notification channels paged by every alert, beside or instead of the e-mail                                       |
| `require_alert_recipient`         | `REQUIRE_ALERT_RECIPIENT`, from `scripts/gcp/environments/` (`false` in dev only)         | Whether an apply must name a recipient; refused as `false` outside dev, whose alerts then page no one                      |
| `query_log_rejection_reason`      | `QUERY_LOG_REJECTION_REASON`, from `scripts/gcp/environments/`                            | Log the category of a refused credential (dev only)                                                                        |
| `query_audience`                  | default (empty)                                                                           | Override for the query service's OIDC audience                                                                             |
| `allow_synthetic_sources`         | `ALLOW_SYNTHETIC_SOURCES`, from `scripts/gcp/environments/` (`true` in dev only)          | Whether the worker accepts synthetic content and the gate-bypassing sources (docs/design/authority-import-contract.md, D7) |
| `enabled_run_sources`             | default (null): every source where synthetic content is allowed, otherwise `["document"]` | Run sources the worker accepts; fixture and healthcare-api need `allow_synthetic_sources`                                  |
| `evidence_retention_days`         | default (2555)                                                                            | Retention policy on the evidence bucket                                                                                    |
| `submission_retention_days`       | default (0, none)                                                                         | Retention policy on the submission bucket                                                                                  |
| `deletion_protection`             | default (`true`)                                                                          | Deletion protection on the ledger table, the services and the workflow                                                     |
| `enforce_binary_authorization`    | default (`false`)                                                                         | Binary Authorization on the worker service (the query service has none yet)                                                |
| `lock_regulated_audit_log_bucket` | default (`false`)                                                                         | Bucket Lock on the regulated audit log bucket; irreversible                                                                |
| `kms_manifest_key_version`        | default (`1`)                                                                             | Version of `manifest-signing-hsm` the worker signs with                                                                    |
| `kms_approval_key_version`        | default (`1`)                                                                             | Version of `approval-signing-hsm` the signer signs with, and the one version the worker and query service trust            |
| `approvers`                       | `APPROVERS_JSON` (default `{}`: nobody approves)                                          | Approver map: Google subject to role, display name and e-mail (docs/design/approval.md, D2)                                |
| `approval_addon_service_account`  | `APPROVAL_ADDON_SERVICE_ACCOUNT` (default empty: nothing calls the signer)                | The Workspace add-on's service account: run.invoker on the signer, and the system ID token's email                         |
| `approval_addon_oauth_client_id`  | `APPROVAL_ADDON_OAUTH_CLIENT_ID` (default empty: the signer refuses every event)          | The add-on's OAuth client id: the user ID token's audience                                                                 |
| `approval_enforcement`            | `APPROVAL_ENFORCEMENT` (default `false`)                                                  | Whether the worker publishes a document only under its verified head approval, and links the version to it                 |
| `query_approval_verification`     | `QUERY_APPROVAL_VERIFICATION` (default `false`)                                           | Whether the query service verifies every answer's signed approval (`not-approved` without one)                             |

Each variable's full description, validation and reasoning are in `variables.tf`.

## Outputs

| Name                            | Used by                                                | Value                                                         |
| ------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------- |
| `region`                        | `bootstrap.sh`, `reconcile-fhir-stores.sh`             | Region of the regional resources                              |
| `healthcare_dataset_id`         | `bootstrap.sh`, `reconcile-fhir-stores.sh`, IAM export | Healthcare dataset holding both FHIR stores                   |
| `source_fhir_store_id`          | `bootstrap.sh`, `reconcile-fhir-stores.sh`             | Source (Type 2) R5 store id                                   |
| `target_fhir_store_id`          | `bootstrap.sh`, `reconcile-fhir-stores.sh`             | Target (validated EMA) R5 store id                            |
| `fhir_analytics_dataset`        | `reconcile-fhir-stores.sh`                             | BigQuery dataset of the store's ANALYTICS_V2 stream           |
| `transformation_ledger_dataset` | operators                                              | BigQuery dataset of the transformation ledger                 |
| `evidence_bucket`               | IAM export in `deploy.sh`                              | Run evidence and deploy evidence                              |
| `profile_staging_bucket`        | `bootstrap.sh`                                         | Staging for the profile import                                |
| `submission_bucket`             | Zone A                                                 | Approved canonical submissions                                |
| `record_readers_targets`        | `record-readers.sh`                                    | Every bucket and BigQuery dataset declared, to narrow readers |
| `fhir_changes_topic`            | `reconcile-fhir-stores.sh`                             | Target store change notifications                             |
| `cloud_run_service_uri`         | smoke run, deploy workflow                             | Worker URL                                                    |
| `query_service_url`             | callers                                                | Where to send query requests                                  |
| `query_service_urls`            | operators                                              | Every hostname Cloud Run serves the query service on          |
| `query_audience`                | callers                                                | The only audience an ID token for the query service may carry |
| `query_caller_service_account`  | callers                                                | The impersonation-only caller account                         |
| `signer_service_url`            | the Workspace add-on's HTTP endpoint                   | The approval signer's URL, and its system ID token's audience |
| `approval_heads_bucket`         | operators                                              | Every document's approval heads                               |
| `workflow_name`                 | `bootstrap.sh`                                         | Pipeline workflow name                                        |
| `workflow_console_url`          | operators                                              | Console link to workflow executions                           |
| `bigquery_console_url`          | operators                                              | Console link to the analytics dataset                         |
| `operations_dashboard_json`     | dashboard drift check in `deploy.sh`                   | The operations dashboard as configured                        |
| `operations_dashboard_id`       | dashboard drift check in `deploy.sh`                   | Resource name of the operations dashboard                     |
