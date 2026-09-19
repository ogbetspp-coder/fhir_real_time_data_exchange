resource "google_service_account" "worker" {
  account_id   = "ema-flow-worker-${var.environment}"
  display_name = "EMA Flow interoperability worker (${var.environment})"
}

resource "google_service_account" "workflow" {
  account_id   = "ema-flow-workflow-${var.environment}"
  display_name = "EMA Flow workflow orchestrator (${var.environment})"
}

resource "google_project_iam_member" "cloudbuild_default_compute_builder" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_audit_config" "regulated_data_access" {
  for_each = toset([
    "healthcare.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "cloudkms.googleapis.com",
  ])

  project = var.project_id
  service = each.value

  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

resource "google_kms_key_ring" "evidence" {
  name     = "${local.name_prefix}-evidence"
  location = var.region

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "evidence_encryption" {
  name            = "evidence-encryption"
  key_ring        = google_kms_key_ring.evidence.id
  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key" "manifest_signing" {
  name     = "manifest-signing"
  key_ring = google_kms_key_ring.evidence.id
  purpose  = "ASYMMETRIC_SIGN"

  version_template {
    algorithm        = "RSA_SIGN_PSS_2048_SHA256"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    prevent_destroy = true
  }
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id
}

resource "google_kms_crypto_key_iam_member" "gcs_evidence_encryption" {
  crypto_key_id = google_kms_crypto_key.evidence_encryption.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_storage_bucket" "evidence" {
  name                        = "${var.project_id}-${local.name_prefix}-evidence"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = true
  }

  retention_policy {
    retention_period = var.evidence_retention_days * 86400
    is_locked        = false
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.evidence_encryption.id
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs_evidence_encryption]
}

resource "google_storage_bucket" "profiles" {
  name                        = "${var.project_id}-${local.name_prefix}-profiles"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.environment != "prod"
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "healthcare_profile_reader" {
  bucket     = google_storage_bucket.profiles.name
  role       = "roles/storage.objectViewer"
  member     = "serviceAccount:${local.healthcare_service_identity_email}"
  depends_on = [google_project_service_identity.healthcare]
}

resource "google_logging_project_bucket_config" "regulated_audit" {
  project        = var.project_id
  location       = var.region
  retention_days = min(var.evidence_retention_days, 3650)
  bucket_id      = "${local.name_prefix}-regulated-audit"
  description    = "Regional retained application, workflow, and Cloud Audit Logs"

  depends_on = [google_project_service.required]
}

resource "google_logging_project_sink" "regulated_audit" {
  name                   = "${local.name_prefix}-regulated-audit"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.regulated_audit.id}"
  unique_writer_identity = true
  filter                 = <<-EOT
    resource.type=("cloud_run_revision" OR "workflows.googleapis.com/Workflow" OR "healthcare_fhir_store")
    OR protoPayload.serviceName=("healthcare.googleapis.com" OR "run.googleapis.com" OR "workflows.googleapis.com")
  EOT
}

# The sink's auto-provisioned writer_identity is not reliably readable back through
# this resource (two separate apply passes both left it empty, so this isn't just
# an apply-ordering race). scripts/gcp/deploy.sh grants roles/logging.bucketWriter
# to it directly via `gcloud logging sinks describe`, the same "manage outside
# Terraform" pattern already used for the R5 FHIR stores (see
# scripts/gcp/reconcile-fhir-stores.sh).

resource "google_storage_bucket_iam_member" "worker_evidence_writer" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_kms_crypto_key_iam_member" "worker_manifest_signer" {
  crypto_key_id = google_kms_crypto_key.manifest_signing.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_healthcare" {
  project = var.project_id
  role    = "roles/healthcare.fhirResourceEditor"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_bigquery_dataset_iam_member" "worker_ledger_writer" {
  dataset_id = google_bigquery_dataset.ledger.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_lineage_editor" {
  project = var.project_id
  role    = "roles/datalineage.editor"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_bigquery_dataset_iam_member" "workflow_analytics_reader" {
  dataset_id = google_bigquery_dataset.fhir_analytics.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_project_iam_member" "workflow_bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_project_iam_member" "workflow_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.workflow.email}"
}
