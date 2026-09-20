locals {
  name_prefix = "ema-flow-${var.environment}"
  # The Google Terraform provider rejects R5 even though the Healthcare v1 API supports it.
  # scripts/gcp/reconcile-fhir-stores.sh manages these immutable-version stores through REST.
  source_fhir_store_id = "${local.name_prefix}-source-r5"
  target_fhir_store_id = "${local.name_prefix}-validated-r5"
  # google_project_service_identity.healthcare.email is unreliable (the provider can
  # return it empty on the same apply that creates the identity). Google's managed
  # service agent emails follow a fixed, documented format, so build it directly
  # instead of depending on that computed attribute.
  healthcare_service_identity_email = "service-${data.google_project.current.number}@gcp-sa-healthcare.iam.gserviceaccount.com"
  labels = {
    application = "ema-flow"
    environment = var.environment
    managed_by  = "terraform"
    data_class  = "regulated-product-information"
  }
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "binaryauthorization.googleapis.com",
    "bigquery.googleapis.com",
    "bigquerydatatransfer.googleapis.com",
    "cloudbuild.googleapis.com",
    "clouddeploy.googleapis.com",
    "cloudkms.googleapis.com",
    "eventarc.googleapis.com",
    "healthcare.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "workflows.googleapis.com",
    "workflowexecutions.googleapis.com",
    "datalineage.googleapis.com",
    "dataplex.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_service_identity" "healthcare" {
  provider = google-beta
  project  = var.project_id
  service  = "healthcare.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "ema-flow"
  description   = "Signed and provenance-attached ema-flow containers"
  format        = "DOCKER"
  labels        = local.labels

  depends_on = [google_project_service.required]
}

resource "google_bigquery_dataset" "fhir_analytics" {
  dataset_id                 = "ema_flow_fhir_${var.environment}"
  friendly_name              = "EMA Flow FHIR Analytics (${var.environment})"
  description                = "Native Cloud Healthcare API R5 ANALYTICS_V2 stream"
  location                   = var.region
  delete_contents_on_destroy = false
  labels                     = local.labels

  depends_on = [google_project_service.required]
}

resource "google_bigquery_dataset" "ledger" {
  dataset_id                 = "ema_flow_ledger_${var.environment}"
  friendly_name              = "EMA Flow transformation ledger (${var.environment})"
  description                = "Transformation, validation, and provenance index"
  location                   = var.region
  delete_contents_on_destroy = false
  labels                     = local.labels

  depends_on = [google_project_service.required]
}

resource "google_bigquery_table" "transformation_runs" {
  dataset_id          = google_bigquery_dataset.ledger.dataset_id
  table_id            = "transformation_runs"
  deletion_protection = var.deletion_protection
  description         = "One immutable summary row per deterministic interoperability run"

  time_partitioning {
    type  = "DAY"
    field = "completed_at"
  }

  # Columns added after the first release are NULLABLE so BigQuery evolves the schema in place
  # and existing rows stay valid; removing or retyping one would force a replacement, which
  # deletion_protection would (correctly) refuse.
  schema = jsonencode([
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "completed_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "source_hash", type = "STRING", mode = "REQUIRED" },
    { name = "output_hash", type = "STRING", mode = "REQUIRED" },
    { name = "manifest_hash", type = "STRING", mode = "REQUIRED" },
    { name = "signature_key_version", type = "STRING", mode = "NULLABLE" },
    { name = "manifest_json", type = "JSON", mode = "REQUIRED" },
    { name = "source_kind", type = "STRING", mode = "NULLABLE", description = "fixture, healthcare-api, or document" },
    { name = "contract_version", type = "STRING", mode = "NULLABLE", description = "CanonicalSubmission version; null outside document runs" },
    { name = "ingestion_source_hash", type = "STRING", mode = "NULLABLE", description = "SHA-256 of the approved source document" },
    { name = "fidelity_status", type = "STRING", mode = "NULLABLE", description = "Narrative fidelity outcome; only passed can be persisted" },
    { name = "approval_hash", type = "STRING", mode = "NULLABLE", description = "SHA-256 of the content a human approved" },
  ])
}

resource "google_pubsub_topic" "fhir_changes" {
  name   = "${local.name_prefix}-fhir-changes"
  labels = local.labels

  message_retention_duration = "604800s"
  depends_on                 = [google_project_service.required]
}

resource "google_pubsub_topic" "dead_letter" {
  name   = "${local.name_prefix}-dead-letter"
  labels = local.labels

  message_retention_duration = "1209600s"
  depends_on                 = [google_project_service.required]
}

resource "google_healthcare_dataset" "epi" {
  name     = "${local.name_prefix}-dataset"
  location = var.region

  depends_on = [google_project_service.required]
}

resource "google_bigquery_dataset_iam_member" "healthcare_stream_writer" {
  dataset_id = google_bigquery_dataset.fhir_analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${local.healthcare_service_identity_email}"
  depends_on = [google_project_service_identity.healthcare]
}

resource "google_project_iam_member" "healthcare_bigquery_job_user" {
  project    = var.project_id
  role       = "roles/bigquery.jobUser"
  member     = "serviceAccount:${local.healthcare_service_identity_email}"
  depends_on = [google_project_service_identity.healthcare]
}

resource "google_pubsub_topic_iam_member" "healthcare_publisher" {
  topic      = google_pubsub_topic.fhir_changes.name
  role       = "roles/pubsub.publisher"
  member     = "serviceAccount:${local.healthcare_service_identity_email}"
  depends_on = [google_project_service_identity.healthcare]
}
