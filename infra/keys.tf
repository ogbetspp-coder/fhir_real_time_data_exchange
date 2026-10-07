# Customer-managed keys for the record (docs/design/cmek-rollout.md).
#
# One key per purpose, each granted only to the one Google service agent that uses it: a mistake
# or a compromise on one key then affects one kind of data, and data can be crypto-shredded by
# kind. Every resource that holds the record, the images or the Terraform state is encrypted with
# one of them; the evidence and submission buckets use the evidence ring's key (security.tf).
#
# Losing a key loses the data. A Cloud Healthcare dataset whose key is unavailable is disabled
# after one hour and deleted, stores and all, after 30 days. So every key here:
#   - carries prevent_destroy, so no apply can destroy it;
#   - waits 120 days between a destruction request and the destruction, the longest Cloud KMS
#     allows — fixed at creation, which is why the two keys in security.tf keep their 30;
#   - is watched by the alert in this file, which fires on any destruction request, any version
#     disabled, and any change to a key's grants, because the dataset gives an hour, not 30 days.
# Rotation never re-encrypts existing data, so no version is ever destroyed either.

locals {
  # Service agents, most created by Google only on first use; a key cannot be granted to one that
  # does not exist yet (audit I-10). Healthcare's and Artifact Registry's are created by
  # google_project_service_identity (main.tf), BigQuery's by deploy.sh asking for it before the
  # first apply. Spelled-out e-mails: the provider's email attribute has proved unreliable
  # (healthcare_service_identity_email in main.tf).
  bigquery_encryption_agent = "bq-${data.google_project.current.number}@bigquery-encryption.iam.gserviceaccount.com"
  logging_agent             = "service-${data.google_project.current.number}@gcp-sa-logging.iam.gserviceaccount.com"
  artifact_registry_agent   = "service-${data.google_project.current.number}@gcp-sa-artifactregistry.iam.gserviceaccount.com"

  # 120 days, the maximum wait between a destruction request and the destruction.
  key_destroy_wait = "10368000s"

  record_keys = {
    fhir-record = {
      purpose = "Cloud Healthcare dataset holding the FHIR stores"
      agent   = local.healthcare_service_identity_email
    }
    ledger-analytics = {
      purpose = "BigQuery ledger and analytical projection"
      agent   = local.bigquery_encryption_agent
    }
    audit-logs = {
      purpose = "Retained regulated audit log bucket"
      agent   = local.logging_agent
    }
    artifacts = {
      purpose = "Artifact Registry repository for the product's images"
      agent   = local.artifact_registry_agent
    }
    platform-storage = {
      purpose = "Profiles, build-staging and Terraform state buckets"
      agent   = data.google_storage_project_service_account.gcs.email_address
    }
  }
}

resource "google_kms_key_ring" "record" {
  name     = "${local.name_prefix}-record"
  location = var.region

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "record" {
  for_each = local.record_keys

  name                       = each.key
  key_ring                   = google_kms_key_ring.record.id
  purpose                    = "ENCRYPT_DECRYPT"
  rotation_period            = "7776000s"
  destroy_scheduled_duration = local.key_destroy_wait
  labels                     = merge(local.labels, { purpose = replace(each.key, "_", "-") })

  lifecycle {
    prevent_destroy = true
  }
}

# Each agent may use its own key and no other.
resource "google_kms_crypto_key_iam_member" "record_agent" {
  for_each = local.record_keys

  crypto_key_id = google_kms_crypto_key.record[each.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${each.value.agent}"

  depends_on = [
    google_project_service_identity.healthcare,
    google_project_service_identity.artifact_registry,
  ]
}

# Run manifests are signed in an HSM, with this key (run.tf, KMS_MANIFEST_KEY). Its algorithm is
# the one dev's earlier software key used, so manifests signed with either verify the same way; a
# key's protection level cannot change after creation, which is why this is a second key. The
# software key (security.tf, dev only) stays enabled so every manifest it signed stays verifiable.
resource "google_kms_crypto_key" "manifest_signing_hsm" {
  name                       = "manifest-signing-hsm"
  key_ring                   = google_kms_key_ring.evidence.id
  purpose                    = "ASYMMETRIC_SIGN"
  destroy_scheduled_duration = local.key_destroy_wait
  labels                     = merge(local.labels, { purpose = "manifest-signing" })

  version_template {
    algorithm        = "RSA_SIGN_PSS_2048_SHA256"
    protection_level = "HSM"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "worker_manifest_signer_hsm" {
  crypto_key_id = google_kms_crypto_key.manifest_signing_hsm.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.worker.email}"
}

# Approvals are signed in an HSM with this key, used by nothing else (docs/design/approval.md, D3;
# ADR 0004): RSA-PSS with SHA-256, whose salt Cloud KMS makes the digest's length, 32 bytes, on a
# 3072-bit key, since a statement must stay verifiable as long as the evidence it approves. The
# signer alone signs (signer.tf); the worker and the query service only read its public keys, to
# verify every statement before they publish or answer (D8, D9).
resource "google_kms_crypto_key" "approval_signing_hsm" {
  name                       = "approval-signing-hsm"
  key_ring                   = google_kms_key_ring.evidence.id
  purpose                    = "ASYMMETRIC_SIGN"
  destroy_scheduled_duration = local.key_destroy_wait
  labels                     = merge(local.labels, { purpose = "approval-signing" })

  version_template {
    algorithm        = "RSA_SIGN_PSS_3072_SHA256"
    protection_level = "HSM"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "signer_approval_signer" {
  crypto_key_id = google_kms_crypto_key.approval_signing_hsm.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.signer.email}"
}

resource "google_kms_crypto_key_iam_member" "worker_approval_public_key" {
  crypto_key_id = google_kms_crypto_key.approval_signing_hsm.id
  role          = "roles/cloudkms.publicKeyViewer"
  member        = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_kms_crypto_key_iam_member" "query_approval_public_key" {
  crypto_key_id = google_kms_crypto_key.approval_signing_hsm.id
  role          = "roles/cloudkms.publicKeyViewer"
  member        = "serviceAccount:${google_service_account.query.email}"
}

# A key made unavailable pages within minutes. Cloud KMS Admin Activity audit logs are always on,
# so this needs no logging configuration. It fires on:
#   - a request to destroy a version;
#   - a version updated to any state other than ENABLED — which is how a version is disabled;
#   - any change to the IAM policy of a key or key ring, since removing an agent's grant makes its
#     key unavailable to it just as surely. Terraform's own grant changes also match; they are
#     rare and deliberate, and an alert on one is a confirmation, not noise.
# If the audit-logs key itself is the one disabled, log entries buffer for about three hours and
# are then discarded — one more reason this must fire in minutes.
#
# Always declared, and never without a channel. Until 2026-09-27 it existed only when an e-mail
# or channel was supplied, so a deploy without one removed the one-hour guard silently; now such an
# apply is refused (the precondition below; observability.tf holds the channel list), except in
# dev, whose policies exist but page no one (require_alert_recipient).
resource "google_monitoring_alert_policy" "key_availability" {
  display_name          = "EMA Flow encryption key made unavailable (${var.environment})"
  combiner              = "OR"
  notification_channels = local.alert_notification_channels

  lifecycle {
    precondition {
      condition     = !var.require_alert_recipient || length(local.alert_notification_channels) > 0
      error_message = "No alert notification channel: set alert_notification_email (ALERT_NOTIFICATION_EMAIL) or alert_notification_channels. A key made unavailable gives one hour before the FHIR dataset is disabled; it must page someone."
    }
  }

  conditions {
    display_name = "A key version was disabled, scheduled for destruction, or a key's grants changed"
    condition_matched_log {
      filter = join(" AND ", [
        "protoPayload.serviceName=\"cloudkms.googleapis.com\"",
        "logName=\"projects/${var.project_id}/logs/cloudaudit.googleapis.com%2Factivity\"",
        join(" OR ", [
          "(protoPayload.methodName=\"DestroyCryptoKeyVersion\")",
          "(protoPayload.methodName=\"UpdateCryptoKeyVersion\" AND NOT protoPayload.request.cryptoKeyVersion.state=\"ENABLED\")",
          "(protoPayload.methodName=\"SetIamPolicy\")",
        ]),
      ])
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      An encryption key version was disabled or scheduled for destruction, or a key's grants
      changed. A Cloud Healthcare dataset whose key is unavailable is disabled after **one hour**
      and deleted after 30 days. Within the hour: restore the version
      (`gcloud kms keys versions restore` / `enable`) or restore the grant, then confirm the
      dataset answers. Runbook: docs/design/cmek-rollout.md, "Protection against losing a key".
    EOT
  }
}

moved {
  from = google_monitoring_alert_policy.key_availability[0]
  to   = google_monitoring_alert_policy.key_availability
}
