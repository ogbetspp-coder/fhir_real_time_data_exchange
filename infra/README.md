# infra

The Terraform configuration for one EMA Flow environment on Google Cloud: the worker and query
Cloud Run services, the pipeline workflow, the Healthcare dataset, the evidence, submission and
profile buckets, the BigQuery analytics and ledger datasets, the customer-managed keys, the
service accounts and their bindings, the audit log sink and bucket, monitoring, and the API list.
`docs/architecture.md` describes what these pieces do; this file describes the configuration.

Two things an environment needs are deliberately not here:

- **The R5 FHIR stores.** The Google provider rejects `version = "R5"`, so
  `scripts/gcp/reconcile-fhir-stores.sh` creates and patches them over REST inside the dataset
  Terraform owns, refuses any R4 substitution, and never deletes a store.
- **The identities that run Terraform.** The deployer and the read-only planner are bootstrapped
  outside this configuration (`scripts/gcp/deploy-identity.sh`, `scripts/gcp/plan-identity.sh`).

## How it is applied

Only by the deploy workflow. A merge to `main` that touches a deployable path runs
`.github/workflows/deploy.yml`, whose `deploy` job authenticates as the deployer through Workload
Identity Federation (admitted only for `main` and that workflow) and runs `scripts/gcp/deploy.sh`.
That script is the only place the inputs below are assembled (`tf_deploy_vars`): it builds the
images, resolves their digests, and applies with the deployed commit as `service_version`.

Every pull request is planned first, by `.github/workflows/plan.yml`, as the read-only planner,
with the same inputs and against the live state. The check fails on any destroy or replace until
the pull request carries the `allow-replace` label. Do not apply by hand; run `terraform fmt` and
`terraform validate` locally:

```bash
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false && terraform -chdir=infra validate
```

State is in `gs://<project>-ema-flow-tfstate`, prefix `terraform/state`, on a customer-managed
key, readable only by the deployer and the owner (the planner reads it through `objectViewer`).
Resources that hold the record (keys, the dataset, the ledger) carry `prevent_destroy`.

## Environments

| Environment  | Project               | State                                                                                           |
| ------------ | --------------------- | ----------------------------------------------------------------------------------------------- |
| `dev`        | `sage-ship-509104-b8` | Deployed on every deployable merge to `main`, in `europe-west4`; in the `non-production` folder |
| `validation` | none yet              | Accepted by the `environment` validation; not deployed                                          |
| `prod`       | `khs-ema-flow-prod`   | Created empty, without billing, in the `production` folder (EU location policy); not deployed   |

`environment` has no default: every plan, apply and import in `deploy.sh` passes it
(`EMA_FLOW_ENVIRONMENT`, `dev` in both workflows). `prod` also sets a minimum of one instance on
each Cloud Run service.

## Inputs

Set by `deploy.sh` on every plan and apply unless marked "default".

| Name                              | Set by                                                                                    | Purpose                                                                                                                    |
| --------------------------------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `project_id`                      | `GCP_PROJECT_ID`                                                                          | Project for this environment                                                                                               |
| `region`                          | `GCP_REGION` (`europe-west4`)                                                             | Region of every regional resource                                                                                          |
| `environment`                     | `EMA_FLOW_ENVIRONMENT`                                                                    | `dev`, `validation` or `prod`; used in names and labels                                                                    |
| `worker_image`                    | built image, by digest                                                                    | Worker container; its digest becomes `IMAGE_DIGEST` in every signed run manifest                                           |
| `validator_image`                 | built image, by digest                                                                    | HL7 validator sidecar container                                                                                            |
| `query_image`                     | built image, by digest                                                                    | Query service container; its digest becomes `IMAGE_DIGEST` in every audit record                                           |
| `service_version`                 | the deployed commit SHA                                                                   | `QUERY_SERVICE_VERSION` on the query service and `GIT_COMMIT` on the worker                                                |
| `deployer_account`                | the active service account                                                                | Granted run.invoker on the worker (smoke run), actAs on the build account, FHIR editor                                     |
| `query_invokers`                  | `QUERY_INVOKERS`                                                                          | Members granted run.invoker on the query service                                                                           |
| `query_token_creators`            | `QUERY_TOKEN_CREATORS`                                                                    | Members who may impersonate the caller service account                                                                     |
| `query_entitlements_json`         | `QUERY_ENTITLEMENTS_JSON`                                                                 | Entitlement map: principal to the bundles it may read                                                                      |
| `query_oauth_client_ids`          | `QUERY_OAUTH_CLIENT_IDS`                                                                  | OAuth client ids whose access tokens the query service accepts                                                             |
| `alert_notification_email`        | `ALERT_NOTIFICATION_EMAIL`                                                                | Recipient of the entitlement-denial alert; empty creates no alert                                                          |
| `query_log_rejection_reason`      | `QUERY_LOG_REJECTION_REASON`                                                              | Log the category of a refused credential (dev only)                                                                        |
| `query_audience`                  | default (empty)                                                                           | Override for the query service's OIDC audience                                                                             |
| `allow_synthetic_sources`         | default `false`; the dev deploy sets `true`                                               | Whether the worker accepts synthetic content and the gate-bypassing sources (docs/design/authority-import-contract.md, D7) |
| `enabled_run_sources`             | default (null): every source where synthetic content is allowed, otherwise `["document"]` | Run sources the worker accepts; fixture and healthcare-api need `allow_synthetic_sources`                                  |
| `evidence_retention_days`         | default (2555)                                                                            | Retention policy on the evidence bucket                                                                                    |
| `submission_retention_days`       | default (0, none)                                                                         | Retention policy on the submission bucket                                                                                  |
| `deletion_protection`             | default (`true`)                                                                          | Deletion protection on the ledger table, the services and the workflow                                                     |
| `enforce_binary_authorization`    | default (`false`)                                                                         | Binary Authorization on the worker service (the query service has none yet)                                                |
| `alert_notification_channels`     | default (empty)                                                                           | Existing notification channels for the operational and key alerts                                                          |
| `lock_regulated_audit_log_bucket` | default (`false`)                                                                         | Bucket Lock on the regulated audit log bucket; irreversible                                                                |
| `kms_manifest_key_version`        | default (`1`)                                                                             | Version of `manifest-signing-hsm` the worker signs with                                                                    |

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
| `fhir_changes_topic`            | `reconcile-fhir-stores.sh`                             | Target store change notifications                             |
| `cloud_run_service_uri`         | smoke run, deploy workflow                             | Worker URL                                                    |
| `query_service_url`             | callers                                                | Where to send query requests                                  |
| `query_service_urls`            | operators                                              | Every hostname Cloud Run serves the query service on          |
| `query_audience`                | callers                                                | The only audience an ID token for the query service may carry |
| `query_caller_service_account`  | callers                                                | The impersonation-only caller account                         |
| `workflow_name`                 | `bootstrap.sh`                                         | Pipeline workflow name                                        |
| `workflow_console_url`          | operators                                              | Console link to workflow executions                           |
| `bigquery_console_url`          | operators                                              | Console link to the analytics dataset                         |
