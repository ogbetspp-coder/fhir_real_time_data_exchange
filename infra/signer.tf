# The approval signer (docs/design/approval.md, D3 and D8; phase 1, build step 3). ADR 0004: a
# component with a different trust level from the worker is its own deployable, with its own
# service account holding least-privilege IAM, its own configuration and its own image. It is the
# HTTP endpoint of the Google Chat app built as a Workspace add-on through which a named person
# approves; it alone holds approval-signing-hsm (keys.tf), and it writes the heads, the reviews and
# the statements, never the FHIR store.

locals {
  signer_service_name = "${local.name_prefix}-signer"
  # As the query service's: Cloud Run's deterministic URL, known before the service exists. It is
  # the URL the add-on is configured to call, and so the audience of every system ID token.
  signer_url = "https://${local.signer_service_name}-${data.google_project.current.number}.${var.region}.run.app"

  signer_image_digest          = try(regex("@(sha256:[0-9a-f]{64})$", var.signer_image)[0], null)
  approval_signing_key_version = "${google_kms_crypto_key.approval_signing_hsm.id}/cryptoKeyVersions/${var.kms_approval_key_version}"

  # Object-name conditions on the evidence bucket (it has uniform bucket-level access, which IAM
  # conditions need). A condition on a name cannot grant a listing, which is a bucket-level call.
  evidence_objects = "projects/_/buckets/${google_storage_bucket.evidence.name}/objects/"
}

resource "google_service_account" "signer" {
  account_id   = "ema-flow-signer-${var.environment}"
  display_name = "EMA Flow approval signer (${var.environment})"
  description  = "Signs approval statements with approval-signing-hsm; writes the heads, reviews and statements, never the FHIR store."
}

# The heads (the design's amendment, "Heads under retention, in their own bucket"): one
# append-only chain per document, `docs/<sha256(document)>/<twelve-digit sequence>`, each entry a
# signed statement written create-if-absent, and the consumed tokens under `tokens/`. No versioning,
# so nothing can be made noncurrent and hidden from a listing; a retention policy, so nothing can be
# replaced or deleted before it ages out. Retention is not locked: before the production gate locks
# it, an administrator could shorten or remove it (stated in the design).
resource "google_storage_bucket" "approval_heads" {
  name                        = "${var.project_id}-${local.name_prefix}-approval-heads"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = false
  }

  retention_policy {
    retention_period = var.evidence_retention_days * 86400
    is_locked        = false
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.evidence_encryption.id
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs_evidence_encryption]

  # Never destroyed by an apply: every approval's head is here.
  lifecycle {
    prevent_destroy = true
  }
}

# The signer creates heads and consumed tokens, and reads and lists the bucket to find a head.
resource "google_storage_bucket_iam_member" "signer_heads_creator" {
  bucket = google_storage_bucket.approval_heads.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.signer.email}"
}

resource "google_storage_bucket_iam_member" "signer_heads_reader" {
  bucket = google_storage_bucket.approval_heads.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.signer.email}"
}

# The worker reads the head before it publishes (D8), and the query service on every answer (D9).
resource "google_storage_bucket_iam_member" "worker_heads_reader" {
  bucket = google_storage_bucket.approval_heads.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "query_heads_reader" {
  bucket = google_storage_bucket.approval_heads.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.query.email}"
}

# In the evidence bucket the signer creates reviews and statement copies, and nothing else.
resource "google_storage_bucket_iam_member" "signer_evidence_creator" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.signer.email}"

  condition {
    title       = "approval-records-only"
    description = "Create under reviews/ and approvals/ only."
    expression  = "resource.name.startsWith(\"${local.evidence_objects}reviews/\") || resource.name.startsWith(\"${local.evidence_objects}approvals/\")"
  }
}

# And reads a review it finds already stored, to hash it before it is shown (D6).
resource "google_storage_bucket_iam_member" "signer_reviews_reader" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.signer.email}"

  condition {
    title       = "reviews-only"
    description = "Read under reviews/ only."
    expression  = "resource.name.startsWith(\"${local.evidence_objects}reviews/\")"
  }
}

# It reads the submissions it reviews, as the worker does.
resource "google_storage_bucket_iam_member" "signer_submission_reader" {
  bucket = google_storage_bucket.submissions.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.signer.email}"
}

resource "google_project_iam_member" "signer_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.signer.email}"
}

# Each approver opens their review through Cloud Storage's authenticated browser download
# (storage.cloud.google.com), a Google surface: read on `reviews/` only, never a listing.
resource "google_storage_bucket_iam_member" "approver_review_reader" {
  for_each = var.approvers

  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectViewer"
  member = "user:${each.value.email}"

  condition {
    title       = "reviews-only"
    description = "Open a review under reviews/ only."
    expression  = "resource.name.startsWith(\"${local.evidence_objects}reviews/\")"
  }
}

resource "google_cloud_run_v2_service" "signer" {
  name                = local.signer_service_name
  location            = var.region
  deletion_protection = var.deletion_protection
  # Google's Workspace add-on service calls it from outside the project; Cloud Run IAM still
  # admits only the add-on's service account (signer_addon_invoker), and the signer verifies both
  # of Google's tokens itself.
  ingress = "INGRESS_TRAFFIC_ALL"
  labels  = local.labels

  template {
    service_account                  = google_service_account.signer.email
    timeout                          = "60s"
    max_instance_request_concurrency = 4

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      name  = "signer"
      image = var.signer_image

      ports {
        name           = "http1"
        container_port = 8080
      }

      # A review parses and hashes a whole submission, as a worker run does.
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 5
        failure_threshold     = 12

        http_get {
          path = "/healthz"
          port = 8080
        }
      }

      env {
        name  = "NODE_ENV"
        value = "production"
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "SUBMISSION_BUCKET"
        value = google_storage_bucket.submissions.name
      }
      env {
        name  = "EVIDENCE_BUCKET"
        value = google_storage_bucket.evidence.name
      }
      env {
        name  = "APPROVAL_HEADS_BUCKET"
        value = google_storage_bucket.approval_heads.name
      }
      env {
        name  = "APPROVAL_SIGNING_KEY_VERSION"
        value = local.approval_signing_key_version
      }
      env {
        name  = "APPROVER_MAP_JSON"
        value = jsonencode(var.approvers)
      }
      env {
        name  = "ADDON_ENDPOINT_URL"
        value = local.signer_url
      }
      env {
        name  = "ADDON_SERVICE_ACCOUNT"
        value = var.approval_addon_service_account
      }
      env {
        name  = "ADDON_OAUTH_CLIENT_ID"
        value = var.approval_addon_oauth_client_id
      }
      env {
        name  = "ALLOW_SYNTHETIC_SOURCES"
        value = tostring(var.allow_synthetic_sources)
      }
      env {
        name  = "IMAGE_DIGEST"
        value = local.signer_image_digest
      }
    }
  }

  lifecycle {
    precondition {
      condition     = local.signer_image_digest != null
      error_message = "signer_image must be an image reference by digest (…@sha256:<64 hex>) so IMAGE_DIGEST can name the exact image in every statement it signs."
    }

    postcondition {
      condition     = contains(self.urls, local.signer_url)
      error_message = "The signer's deterministic URL is not one Cloud Run reports for it; the add-on's audience would not match."
    }
  }

  depends_on = [
    google_kms_crypto_key_iam_member.signer_approval_signer,
    google_storage_bucket_iam_member.signer_heads_creator,
    google_storage_bucket_iam_member.signer_heads_reader,
    google_storage_bucket_iam_member.signer_evidence_creator,
    google_storage_bucket_iam_member.signer_reviews_reader,
    google_storage_bucket_iam_member.signer_submission_reader,
    google_project_iam_member.signer_log_writer,
  ]
}

# Only the add-on's service account may call the signer, once the owner has created the add-on.
resource "google_cloud_run_v2_service_iam_member" "signer_addon_invoker" {
  count = var.approval_addon_service_account == "" ? 0 : 1

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.signer.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.approval_addon_service_account}"
}
