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
    "documentai.googleapis.com",
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

# The image repository on the `artifacts` key (CMEK step 4). A repository's encryption is fixed at
# creation, so this was created as a second repository beside the Google-managed `ema-flow` one,
# not a change to it. The old repository was removed in step 5a, after a deploy from this one was
# verified on 2026-09-21; revisions built before the switch can no longer be rolled back to.
resource "google_artifact_registry_repository" "images_cmek" {
  location      = var.region
  repository_id = "ema-flow-images"
  description   = "Signed and provenance-attached ema-flow containers, encrypted with the artifacts key"
  format        = "DOCKER"
  kms_key_name  = google_kms_crypto_key.record["artifacts"].id
  labels        = local.labels

  # The Artifact Registry service agent must hold its grant before a CMEK repository is created.
  depends_on = [google_project_service.required, google_kms_crypto_key_iam_member.record_agent]
}

resource "google_bigquery_dataset" "fhir_analytics" {
  dataset_id                 = "ema_flow_fhir_${var.environment}"
  friendly_name              = "EMA Flow FHIR Analytics (${var.environment})"
  description                = "Native Cloud Healthcare API R5 ANALYTICS_V2 stream"
  location                   = var.region
  delete_contents_on_destroy = false
  labels                     = local.labels

  # Every table the stream creates is encrypted with our key (CMEK step 3). Existing tables were
  # converted in place by scripts/gcp/bq-cmek-convert.sh before this was set.
  default_encryption_configuration {
    kms_key_name = google_kms_crypto_key.record["ledger-analytics"].id
  }

  depends_on = [google_project_service.required]
}

resource "google_bigquery_dataset" "ledger" {
  dataset_id                 = "ema_flow_ledger_${var.environment}"
  friendly_name              = "EMA Flow transformation ledger (${var.environment})"
  description                = "Transformation, validation, and provenance index"
  location                   = var.region
  delete_contents_on_destroy = false
  labels                     = local.labels

  default_encryption_configuration {
    kms_key_name = google_kms_crypto_key.record["ledger-analytics"].id
  }

  depends_on = [google_project_service.required]

  # Never destroyed by an apply (docs/foundations.md; docs/design/cmek-rollout.md, step 0).
  # Holds the transformation ledger, which is a record, not a projection.
  lifecycle {
    prevent_destroy = true
  }
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

  # The provider treats this as forcing replacement. It is set only after the live table was
  # converted in place to exactly this key (scripts/gcp/bq-cmek-convert.sh), so the plan shows no
  # change; were the string to differ in form, the plan would show a replacement, and
  # deletion_protection and prevent_destroy make that plan fail rather than delete the ledger.
  encryption_configuration {
    kms_key_name = google_kms_crypto_key.record["ledger-analytics"].id
  }

  # Columns added after the first release are NULLABLE so BigQuery evolves the schema in place
  # and existing rows stay valid. Removing or retyping one is a different matter: the provider
  # would plan a replacement. `deletion_protection` (on by default) and `prevent_destroy` below
  # now make that plan fail rather than apply; treat an edit to an existing column as a migration
  # requiring an explicit plan review, not as an ordinary schema change.
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

  # Never destroyed by an apply (docs/foundations.md; docs/design/cmek-rollout.md, step 0).
  # The ledger. deletion_protection covers a destroy; this also refuses a plan that would replace it.
  lifecycle {
    prevent_destroy = true
  }
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

# The Google-managed dataset the stores lived in until CMEK step 5c. Released from Terraform's
# management without being destroyed: the dataset keeps its stores until the switch to `record` is
# verified, and is then deleted by hand, deliberately, as the plan records. A `removed` block
# rather than deleting the resource block, because deleting the block would plan its destruction —
# which prevent_destroy would refuse, and which would take every store inside it with it.
removed {
  from = google_healthcare_dataset.epi

  lifecycle {
    destroy = false
  }
}

# The FHIR dataset on the fhir-record key (CMEK step 5). A Healthcare dataset's encryption is
# fixed at creation, so this is a second dataset beside `epi`, not a change to it; renaming `epi`
# in place would destroy it and every store inside it. Created empty by step 5a; its stores are
# reconciled into it by hand (scripts/gcp/reconcile-fhir-stores.sh with
# HEALTHCARE_DATASET_OVERRIDE) and checked before step 5c switches the services to it.
#
# Its key must stay available: a dataset whose key is disabled, scheduled for destruction or
# ungranted is disabled after one hour and deleted, with every store, after 30 days. keys.tf and
# scripts/gcp/key-guard.sh exist for that reason.
resource "google_healthcare_dataset" "record" {
  name     = "${local.name_prefix}-fhir-record"
  location = var.region

  encryption_spec {
    kms_key_name = google_kms_crypto_key.record["fhir-record"].id
  }

  # The Healthcare service agent must hold its grant on the key before the dataset is created.
  depends_on = [google_project_service.required, google_kms_crypto_key_iam_member.record_agent]

  lifecycle {
    prevent_destroy = true
  }
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
