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
    # The Gemini Enterprise connector's side of every tool call (foundations C6). The query
    # service's own audit record captures the call; this captures who asked Gemini, and what
    # Gemini sent to the connector.
    "discoveryengine.googleapis.com",
    # Who minted a token as whom. The IAM Service Account Credentials API (generateIdToken,
    # generateAccessToken, signJwt) writes Data Access logs under iamcredentials.googleapis.com
    # only when they are enabled here, for iam.googleapis.com. A query audit record names the
    # shared caller service account; without these entries nothing names the person who
    # impersonated it.
    "iam.googleapis.com",
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

# Dev's software signing key, which signed run manifests until CMEK step 2 (2026-09-21) moved the
# worker to manifest-signing-hsm (keys.tf). Dev only (audit I-11): it stays enabled there so every
# manifest it signed stays verifiable against its public key, and no other environment has ever
# signed with it, so none creates it.
resource "google_kms_crypto_key" "manifest_signing" {
  count    = var.environment == "dev" ? 1 : 0
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

moved {
  from = google_kms_crypto_key.manifest_signing
  to   = google_kms_crypto_key.manifest_signing[0]
}

# The worker signs with a named crypto key VERSION, var.kms_manifest_key_version
# (infra/variables.tf, which says why it is named rather than looked up).

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
  public_access_prevention    = "enforced"
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
  public_access_prevention    = "enforced"
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
  public_access_prevention    = "enforced"
  force_destroy               = var.environment != "prod"
  labels                      = local.labels

  versioning {
    enabled = true
  }

  # Old generations only. The live objects are the staged profile set and the import-fingerprint
  # marker (scripts/gcp/bootstrap.sh), which the deploy keeps current itself: rsync removes what is
  # no longer in the set. Until 2026-09-27 this rule deleted every object 30 days after upload, live
  # ones included, so about once a month the marker vanished and the next deploy re-staged and
  # re-imported every profile (some twenty minutes) for nothing.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }

  # On the platform-storage key (CMEK step 7). A default key applies to objects written after it
  # is set; objects already in the bucket were rewritten under it once, by hand.
  encryption {
    default_kms_key_name = google_kms_crypto_key.record["platform-storage"].id
  }

  depends_on = [google_kms_crypto_key_iam_member.record_agent]
}

resource "google_storage_bucket_iam_member" "healthcare_profile_reader" {
  bucket     = google_storage_bucket.profiles.name
  role       = "roles/storage.objectViewer"
  member     = "serviceAccount:${local.healthcare_service_identity_email}"
  depends_on = [google_project_service_identity.healthcare]
}

# Dev's retained audit log until CMEK step 6 (2026-09-21), on a Google-managed key. A log bucket's
# key can only be set at creation — the API refuses otherwise ("Cannot add a CMEK key to a non-CMEK
# bucket. CMEK must be enabled at bucket creation.") — so the sink moved to regulated_audit_cmek
# below, and this bucket receives nothing and keeps the entries written before the switch until
# they age out. Dev only (audit I-11): no other environment ever wrote to it, so none creates it.
resource "google_logging_project_bucket_config" "regulated_audit" {
  count          = var.environment == "dev" ? 1 : 0
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

  depends_on = [google_project_service.required]

  # Never destroyed by an apply (docs/foundations.md; docs/design/cmek-rollout.md, step 0).
  # Destroying this deletes the retained audit log; renaming bucket_id is a destroy.
  lifecycle {
    prevent_destroy = true
  }
}

moved {
  from = google_logging_project_bucket_config.regulated_audit
  to   = google_logging_project_bucket_config.regulated_audit[0]
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

# What is retained for as long as the evidence. Beside the services that touch the record: IAM
# (grants, deny policies, service accounts), IAM credentials (every token minted as a service
# account), Resource Manager (project policy changes) and Logging (a sink, bucket or exclusion
# changed — tampering with this trail). _Required keeps some of these for 400 days only; the
# evidence they explain is kept for evidence_retention_days.
resource "google_logging_project_sink" "regulated_audit" {
  name                   = "${local.name_prefix}-regulated-audit"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.regulated_audit_cmek.id}"
  unique_writer_identity = true
  filter                 = <<-EOT
    resource.type=("cloud_run_revision" OR "workflows.googleapis.com/Workflow" OR "healthcare_fhir_store")
    OR protoPayload.serviceName=("healthcare.googleapis.com" OR "run.googleapis.com" OR "workflows.googleapis.com" OR "storage.googleapis.com" OR "bigquery.googleapis.com" OR "cloudkms.googleapis.com" OR "discoveryengine.googleapis.com" OR "iam.googleapis.com" OR "iamcredentials.googleapis.com" OR "cloudresourcemanager.googleapis.com" OR "logging.googleapis.com")
  EOT
}

# The sink needs no grant. Its destination is a log bucket in this project, and a sink that writes
# to a log bucket in its own project has no writer identity: Cloud Logging writes the entries
# itself. That is why writer_identity reads back empty (audit I-11); until then
# scripts/gcp/deploy.sh looked for one to grant roles/logging.bucketWriter, and warned on every
# deploy when it found none.

# Create only (foundations C12). The worker writes each artefact once, with a simple upload, and
# never reads, lists, overwrites or deletes evidence; the retention policy would refuse the last
# two anyway.
resource "google_storage_bucket_iam_member" "worker_evidence_writer" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.worker.email}"

  # A role change replaces the binding; the new one is granted before the old is removed, so the
  # worker is never without evidence write access mid-apply.
  lifecycle {
    create_before_destroy = true
  }
}

# Read-only, and only this bucket: the worker consumes submissions, it never produces or
# amends them.
resource "google_storage_bucket_iam_member" "worker_submission_reader" {
  bucket = google_storage_bucket.submissions.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# The worker signs with manifest-signing-hsm (keys.tf). Nothing may sign with dev's software key
# (manifest_signing, above) any more, so the worker holds no grant on it.

# Bound on each store, for what the worker does there (foundations C4): it reads a source bundle
# from the source store, and validates and writes the EMA package in the validated store. Until
# CMEK step 5c the editor role was project-level; it is still also bound on the dataset (below,
# transitional), which lets the worker, a service that parses untrusted XHTML, rewrite the source
# store too. Phase 2 removes that.
resource "google_healthcare_fhir_store_iam_member" "worker_source_reader" {
  fhir_store_id = local.source_fhir_store_path
  role          = "roles/healthcare.fhirResourceReader"
  member        = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_healthcare_fhir_store_iam_member" "worker_validated_editor" {
  fhir_store_id = local.target_fhir_store_path
  role          = "roles/healthcare.fhirResourceEditor"
  member        = "serviceAccount:${google_service_account.worker.email}"
}

# Append rows to the ledger table, and nothing else. The worker streams one row per run
# (src/gcp/evidence.ts, tabledata.insertAll: bigquery.tables.updateData, with tables.get to find
# the table). It also holds bigquery.dataEditor on the ledger dataset (below, transitional, removed
# in phase 2), which deletes a table or sets its expiration — the ledger's, from a service that
# parses untrusted XHTML. The evidence bucket was cut down to create-only for the same reason.
resource "google_project_iam_custom_role" "ledger_appender" {
  role_id     = "emaFlowLedgerAppender_${var.environment}"
  title       = "EMA Flow ledger appender (${var.environment})"
  description = "Stream rows into one BigQuery table; no read, update or delete of the table itself."
  permissions = ["bigquery.tables.get", "bigquery.tables.updateData"]
}

resource "google_bigquery_table_iam_member" "worker_ledger_appender" {
  dataset_id = google_bigquery_table.transformation_runs.dataset_id
  table_id   = google_bigquery_table.transformation_runs.table_id
  role       = google_project_iam_custom_role.ledger_appender.name
  member     = "serviceAccount:${google_service_account.worker.email}"
}

# TRANSITIONAL (audit B04, phase 1 of 2). The broad grants the three above replace stay until the
# narrow ones are applied; phase 2 removes these blocks. Removing them in the same apply that
# creates the narrow ones would leave the worker without ledger or FHIR access if that apply
# stopped part-way (a create refused for want of a permission while the independent removals still
# ran), and for the seconds IAM takes to propagate even if it did not.
#
# Phase 1's smoke run cannot prove the narrow grants (audit B08, L2): while these broad ones are
# bound, a run succeeds whether the narrow ones work or not. The proof is phase 2's own deploy: it
# removes these, and its smoke run is then the first to exercise the narrow grants alone. If that
# run fails, the rollback is to restore these blocks (the pull request that removes them,
# reverted) and deploy again; nothing else changes, so it is quick.
# test/infra/worker-identity.test.ts proves, from the configuration, what remains once they are
# removed.
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
# Declared here so it follows the dataset. Scoped to the one dataset, never the project; it is
# dataset-wide, unlike the services' store-level grants, because the deploy seeds the source store
# and is the identity that creates both.
resource "google_healthcare_dataset_iam_member" "deployer_fhir_editor" {
  count      = var.deployer_account == "" ? 0 : 1
  dataset_id = google_healthcare_dataset.record.id
  role       = "roles/healthcare.fhirResourceEditor"
  member     = "serviceAccount:${var.deployer_account}"
}

# No Document AI role: nothing the worker runs calls Document AI. The extractor spike did, as a
# person running it by hand, not as this identity. Removed in CMEK step 5c.

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
