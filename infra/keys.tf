# Customer-managed keys for the record (docs/design/cmek-rollout.md, step 1).
#
# One key per purpose, each granted only to the one Google service agent that uses it: a mistake
# or a compromise on one key then affects one kind of data, and data can be crypto-shredded by
# kind. This step only creates the keys and grants; nothing is encrypted with them until the steps
# that follow switch each resource over, one reviewed pull request at a time.
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
  # Service agents, spelled out from the project number: the provider's own email attributes for
  # some of them have proved unreliable (see healthcare_service_identity_email in main.tf).
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

  depends_on = [google_project_service_identity.healthcare]
}

# Run manifests are signed in an HSM. The algorithm is the one the software key used, so the
# verification code is unchanged; the protection level of a key cannot change after creation,
# hence a new key. The software key in security.tf stays enabled so every manifest it signed stays
# verifiable. The worker is switched to this key in step 2; it is granted here so that switch is
# one configuration change.
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

# A key made unavailable pages within minutes. Cloud KMS Admin Activity audit logs are always on,
# so this needs no logging configuration. It fires on:
#   - a request to destroy a version;
#   - a version updated to any state other than ENABLED — which is how a version is disabled;
#   - any change to the IAM policy of a key or key ring, since removing an agent's grant makes its
#     key unavailable to it just as surely. Terraform's own grant changes also match; they are
#     rare and deliberate, and an alert on one is a confirmation, not noise.
# If the audit-logs key itself is the one disabled, log entries buffer for about three hours and
# are then discarded — one more reason this must fire in minutes.
resource "google_monitoring_alert_policy" "key_availability" {
  count = var.alert_notification_email != "" || length(var.alert_notification_channels) > 0 ? 1 : 0

  display_name = "EMA Flow encryption key made unavailable (${var.environment})"
  combiner     = "OR"
  notification_channels = concat(
    google_monitoring_notification_channel.query_entitlement_denials_email[*].id,
    var.alert_notification_channels,
  )

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
