resource "google_service_account" "worker" {
  account_id   = "ema-flow-worker-${var.environment}"
  display_name = "EMA Flow interoperability worker (${var.environment})"
}

resource "google_service_account" "workflow" {
  account_id   = "ema-flow-workflow-${var.environment}"
  display_name = "EMA Flow workflow orchestrator (${var.environment})"
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

# Cloud KMS signs with a crypto key VERSION, not a crypto key: AsymmetricSign's `name` must end
# in /cryptoKeyVersions/<n>. `google_kms_crypto_key.manifest_signing.id` stops at the key, so
# passing it to the worker meant every signing call was refused and no run ever produced a signed
# manifest.
#
# The version is named here rather than read with a `google_kms_crypto_key_version` data source,
# because that data source fetches the version's PUBLIC KEY and so requires
# `cloudkms.cryptoKeyVersions.viewPublicKey`. The deploy identity does not hold it, and granting
# it would widen the deployer's reach into key material to obtain a string that is already known.
# Cloud KMS does not rotate asymmetric signing keys automatically, so this only changes when a
# person deliberately creates a version — a configuration change, which is what this is.
variable "kms_manifest_key_version" {
  description = "Version of manifest-signing-hsm (keys.tf) the worker signs with. Cloud KMS does not rotate asymmetric signing keys automatically, so this changes only when a new version is created by hand."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.kms_manifest_key_version))
    error_message = "kms_manifest_key_version must be a positive integer, as Cloud KMS numbers crypto key versions from 1."
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

  # Never destroyed by an apply (docs/foundations.md; docs/design/cmek-rollout.md, step 0).
  # Signed evidence for every run.
  lifecycle {
    prevent_destroy = true
  }
}

# Landing zone for approved Zone A hand-offs. The worker reads submissions only from here, so
# this bucket is the boundary between the probabilistic structuring service and deterministic
# publishing: write access to it is write access to the ingress queue, never to the FHIR store.
# Versioning is on because a submission is evidence of what was approved.
resource "google_storage_bucket" "submissions" {
  name                        = "${var.project_id}-${local.name_prefix}-submissions"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = true
  }

  # Retention is a native Cloud Storage policy driven by configuration rather than by
  # application logic, because each client sets their own duration for approved content. The
  # demonstrator ships with it disabled so a project stays disposable: a retention policy makes
  # every object undeletable until it expires, which would outlive the demonstration by years.
  # Locking (Bucket Lock) stays a separate, deliberate, irreversible administrator action.
  dynamic "retention_policy" {
    for_each = var.submission_retention_days > 0 ? [1] : []

    content {
      retention_period = var.submission_retention_days * 86400
      is_locked        = false
    }
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.evidence_encryption.id
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs_evidence_encryption]

  # Never destroyed by an apply (docs/foundations.md; docs/design/cmek-rollout.md, step 0).
  # Approved submissions, the input every run is evidenced against.
  lifecycle {
    prevent_destroy = true
  }
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
  description    = "Retained audit log until 2026-09-21, Google-managed key; kept until its entries age out"
  # false by default. Setting lock_regulated_audit_log_bucket = true locks the bucket, which
  # cannot be undone: the retention period can no longer be changed and the bucket cannot be
  # deleted until every entry in it has aged past retention_days. Terraform will not unlock it
  # if the variable is later set back to false.
  locked = var.lock_regulated_audit_log_bucket

  # Receives nothing since CMEK step 6: the sink below writes to regulated_audit_cmek. A log
  # bucket's key can only be set at creation — the API refuses otherwise ("Cannot add a CMEK key to
  # a non-CMEK bucket. CMEK must be enabled at bucket creation.") — so the entries written before
  # the switch stay here, retained, until they age out.
  depends_on = [google_project_service.required]

  # Never destroyed by an apply (docs/foundations.md; docs/design/cmek-rollout.md, step 0).
  # Destroying this deletes the retained audit log; renaming bucket_id is a destroy.
  lifecycle {
    prevent_destroy = true
  }
}

# The retained audit log on the audit-logs key (CMEK step 6). Created with the key, because a log
# bucket's key cannot be added afterwards. The logging service account holds its grant on the key
# (keys.tf). If the key is ever unavailable, Cloud Logging buffers new entries for about three hours
# and then discards them — which is why the key availability alert must fire in minutes.
resource "google_logging_project_bucket_config" "regulated_audit_cmek" {
  project        = var.project_id
  location       = var.region
  retention_days = min(var.evidence_retention_days, 3650)
  bucket_id      = "${local.name_prefix}-regulated-audit-cmek"
  description    = "Regional retained application, workflow, and Cloud Audit Logs, on the audit-logs key"
  locked         = var.lock_regulated_audit_log_bucket

  cmek_settings {
    kms_key_name = google_kms_crypto_key.record["audit-logs"].id
  }

  depends_on = [google_project_service.required, google_kms_crypto_key_iam_member.record_agent]

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_logging_project_sink" "regulated_audit" {
  name                   = "${local.name_prefix}-regulated-audit"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.regulated_audit_cmek.id}"
  unique_writer_identity = true
  filter                 = <<-EOT
    resource.type=("cloud_run_revision" OR "workflows.googleapis.com/Workflow" OR "healthcare_fhir_store")
    OR protoPayload.serviceName=("healthcare.googleapis.com" OR "run.googleapis.com" OR "workflows.googleapis.com" OR "storage.googleapis.com" OR "bigquery.googleapis.com" OR "cloudkms.googleapis.com")
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

# Read-only, and only this bucket: the worker consumes submissions, it never produces or
# amends them.
resource "google_storage_bucket_iam_member" "worker_submission_reader" {
  bucket = google_storage_bucket.submissions.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# The worker signs with manifest-signing-hsm (keys.tf) since CMEK step 2, 2026-09-21. This
# software key stays enabled, and protected, so that every manifest it signed stays verifiable
# against its public key; nothing may sign with it any more, so the worker holds no grant on it.

# Bound on the one dataset the worker works in, never on the project (foundations C4). Until CMEK
# step 5c this was a project-level grant, so the worker could edit FHIR resources in any dataset in
# the project; the query service's reader was always dataset-scoped.
resource "google_healthcare_dataset_iam_member" "worker_fhir_editor" {
  dataset_id = google_healthcare_dataset.record.id
  role       = "roles/healthcare.fhirResourceEditor"
  member     = "serviceAccount:${google_service_account.worker.email}"
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

# The deploy writes FHIR resources itself: scripts/gcp/bootstrap.sh seeds the synthetic Type 2
# bundle into the source store. Until CMEK step 5c that permission was a grant made by hand on the
# old dataset, outside Terraform, and nothing recorded it but a note; the switch to the new
# dataset left the deployer without it and the first deploy's seeding step was refused (403).
# Declared here so it follows the dataset. Scoped to the one dataset, like the worker's.
resource "google_healthcare_dataset_iam_member" "deployer_fhir_editor" {
  count      = var.deployer_account == "" ? 0 : 1
  dataset_id = google_healthcare_dataset.record.id
  role       = "roles/healthcare.fhirResourceEditor"
  member     = "serviceAccount:${var.deployer_account}"
}

# No Document AI role: nothing the worker runs calls Document AI. The extractor spike does, as a
# person running scripts/spikes/document-ai by hand, not as this identity. Removed in CMEK step 5c.

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
