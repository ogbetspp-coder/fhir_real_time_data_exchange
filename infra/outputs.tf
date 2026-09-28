output "region" {
  description = "Region of every regional resource; read by scripts/gcp/bootstrap.sh and reconcile-fhir-stores.sh."
  value       = var.region
}

output "healthcare_dataset_id" {
  description = "Name of the CMEK-encrypted Healthcare dataset that holds the source and target FHIR stores."
  value       = google_healthcare_dataset.record.name
}

output "source_fhir_store_id" {
  description = "ID of the R5 FHIR store holding Type 2 source bundles (created by scripts/gcp/reconcile-fhir-stores.sh, not Terraform)."
  value       = local.source_fhir_store_id
}

output "target_fhir_store_id" {
  description = "ID of the validated R5 FHIR store the worker writes EMA ePI resources to (created by scripts/gcp/reconcile-fhir-stores.sh, not Terraform)."
  value       = local.target_fhir_store_id
}

output "fhir_analytics_dataset" {
  description = "BigQuery dataset receiving the target store's native ANALYTICS_V2 stream."
  value       = google_bigquery_dataset.fhir_analytics.dataset_id
}

output "transformation_ledger_dataset" {
  description = "BigQuery dataset holding the transformation ledger (one row per run)."
  value       = google_bigquery_dataset.ledger.dataset_id
}

output "evidence_bucket" {
  description = "Bucket holding each run's evidence under runs/<runId>/ and the deploy's effective-IAM exports under deploy-evidence/."
  value       = google_storage_bucket.evidence.name
}

output "profile_staging_bucket" {
  description = "Bucket the deploy stages checksum-pinned FHIR profiles in before importing them into the target store."
  value       = google_storage_bucket.profiles.name
}

output "submission_bucket" {
  description = "Bucket Zone A writes approved canonical submissions to"
  value       = google_storage_bucket.submissions.name
}

output "record_readers_targets" {
  description = "Every Cloud Storage bucket and BigQuery dataset infra/ declares, read by scripts/gcp/record-readers.sh: the stores whose project viewer and editor access it removes after each apply. test/infra/record-readers.test.ts keeps this list equal to infra/'s bucket and dataset resources."
  value = {
    buckets = [
      google_storage_bucket.evidence.name,
      google_storage_bucket.submissions.name,
      google_storage_bucket.profiles.name,
      google_storage_bucket.build_staging.name,
    ]
    datasets = [
      google_bigquery_dataset.ledger.dataset_id,
      google_bigquery_dataset.fhir_analytics.dataset_id,
    ]
  }
}

output "fhir_changes_topic" {
  description = "Pub/Sub topic the target store publishes change notifications to (full resource id)."
  value       = google_pubsub_topic.fhir_changes.id
}

output "cloud_run_service_uri" {
  description = "URL of the worker Cloud Run service; called by Workflows and by the deploy's smoke run."
  value       = google_cloud_run_v2_service.worker.uri
}

# The three query outputs below describe two different hostnames. Cloud Run serves the service
# on both, but src/query/auth.ts accepts an ID token only when its `aud` is the QUERY_AUDIENCE
# string (output query_audience), so an ID token minted for the other hostname is answered 401
# by a service that is otherwise healthy. Send requests to query_service_url and mint ID tokens
# for query_audience; with var.query_audience unset (the default) those two strings are equal.

output "query_service_url" {
  description = "Endpoint to send query service requests to: Cloud Run's deterministic https://<service>-<project number>.<region>.run.app hostname. Equal to query_audience unless var.query_audience overrides the audience."
  value       = local.query_deterministic_url
}

output "query_service_urls" {
  description = "Every URL Cloud Run reports for the query service, including the legacy https://<service>-<hash>-<region code>.a.run.app hostname. Requests to any of them reach the service; only the query_audience value is accepted as an ID token audience, so do not mint tokens for these."
  value       = google_cloud_run_v2_service.query.urls
}

output "query_audience" {
  description = "The exact string the container receives as QUERY_AUDIENCE: the only value an ID token's `aud` may carry (gcloud ... print-identity-token --audiences=<this>)."
  value       = local.query_audience
}

output "query_caller_service_account" {
  description = "E-mail of the impersonation-only caller service account (infra/query.tf). It holds roles/run.invoker on the query service; `gcloud auth print-identity-token --impersonate-service-account=<this> --audiences=<query_audience> --include-email` mints a token for it, for members of var.query_token_creators."
  value       = google_service_account.caller.email
}

output "workflow_name" {
  description = "Name of the Cloud Workflows pipeline that runs a document end to end."
  value       = google_workflows_workflow.epi.name
}

output "workflow_console_url" {
  description = "Cloud console link to the pipeline workflow's executions."
  value       = "https://console.cloud.google.com/workflows/workflow/${var.region}/${google_workflows_workflow.epi.name}/executions?project=${var.project_id}"
}

output "bigquery_console_url" {
  description = "Cloud console link to the FHIR analytics dataset in BigQuery."
  value       = "https://console.cloud.google.com/bigquery?project=${var.project_id}&ws=!1m4!1m3!3m2!1s${var.project_id}!2s${google_bigquery_dataset.fhir_analytics.dataset_id}"
}

output "operations_dashboard_json" {
  description = "The operations dashboard as configured (infra/observability.tf), before the Monitoring API rewrites it. Terraform ignores the dashboard's text; scripts/ci/dashboard-drift.py compares this with the live dashboard instead."
  value       = jsonencode(local.operations_dashboard)
}

output "operations_dashboard_id" {
  description = "Resource name of the operations dashboard (projects/<number>/dashboards/<id>), read by the deploy's dashboard drift check."
  value       = google_monitoring_dashboard.operations.id
}
