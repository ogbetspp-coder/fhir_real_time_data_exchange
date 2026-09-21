# Image builds run in the EU, under an identity that can do nothing but build
# (docs/foundations.md, findings A2 and B3).
#
# Until 2026-09-21 `gcloud builds submit` ran with gcloud's defaults: a global build, as the
# project's default compute service account, staging the repository's source in the
# auto-created `<project>_cloudbuild` bucket — which is in `US`. So every deploy copied the source
# out of the EU, and the identity doing it was the one every Compute Engine default runs as.
# Both are now explicit: the build runs in `var.region`, stages its source in a bucket in
# `var.region`, and runs as `ema-flow-build-<env>`, whose roles are proven exhaustive by
# test/infra/build-identity.test.ts.
#
# These resources are created by the targeted apply in `scripts/gcp/deploy.sh apis`, before
# `images` runs, because the build needs them to exist.

resource "google_service_account" "build" {
  account_id   = "ema-flow-build-${var.environment}"
  display_name = "EMA Flow image builds (${var.environment})"
}

# Build sources only — never a record, so a short life and no retention. Objects are deleted a
# week after upload; a build that needs its source again resubmits it.
resource "google_storage_bucket" "build_staging" {
  name                        = "${var.project_id}-${local.name_prefix}-build-staging"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = local.labels

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required]
}

# Reads the source tarball the deployer uploaded. Nothing else in the bucket, no write.
resource "google_storage_bucket_iam_member" "build_staging_reader" {
  bucket = google_storage_bucket.build_staging.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.build.email}"
}

# Pushes images to this product's repository and no other.
resource "google_artifact_registry_repository_iam_member" "build_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build.email}"
}

# A build with a user-specified service account must log somewhere it is allowed to write;
# cloudbuild.images.yaml sends it to Cloud Logging only.
resource "google_project_iam_member" "build_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.build.email}"
}

# The deployer submits builds that run as this account, which needs `actAs` on it. The deployer
# holds `iam.serviceAccountUser` at project level today, so this grants nothing new; it states
# the entitlement on the one account it is for, and is what remains when that project role is
# narrowed. Deliberately not in the `apis` phase's -target list: that apply does not pass
# `deployer_account`, and targeting this resource there would destroy it.
resource "google_service_account_iam_member" "deployer_acts_as_build" {
  count              = var.deployer_account == "" ? 0 : 1
  service_account_id = google_service_account.build.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_account}"
}
