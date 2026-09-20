output "region" {
  value = var.region
}

output "healthcare_dataset_id" {
  value = google_healthcare_dataset.epi.name
}

output "source_fhir_store_id" {
  value = local.source_fhir_store_id
}

output "target_fhir_store_id" {
  value = local.target_fhir_store_id
}

output "fhir_analytics_dataset" {
  value = google_bigquery_dataset.fhir_analytics.dataset_id
}

output "transformation_ledger_dataset" {
  value = google_bigquery_dataset.ledger.dataset_id
}

output "evidence_bucket" {
  value = google_storage_bucket.evidence.name
}

output "profile_staging_bucket" {
  value = google_storage_bucket.profiles.name
}

output "submission_bucket" {
  description = "Bucket Zone A writes approved canonical submissions to"
  value       = google_storage_bucket.submissions.name
}

output "fhir_changes_topic" {
  value = google_pubsub_topic.fhir_changes.id
}

output "cloud_run_service_uri" {
  value = google_cloud_run_v2_service.worker.uri
}

output "workflow_name" {
  value = google_workflows_workflow.epi.name
}

output "workflow_console_url" {
  value = "https://console.cloud.google.com/workflows/workflow/${var.region}/${google_workflows_workflow.epi.name}/executions?project=${var.project_id}"
}

output "bigquery_console_url" {
  value = "https://console.cloud.google.com/bigquery?project=${var.project_id}&ws=!1m4!1m3!3m2!1s${var.project_id}!2s${google_bigquery_dataset.fhir_analytics.dataset_id}"
}
