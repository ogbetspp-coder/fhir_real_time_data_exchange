# The drawing record of a certified Word label (docs/design/certified-word-drawing.md; ADR 0006 P4,
# D3): an identity, a key and a bucket used for nothing else, one topic the producer asks on, and
# the Cloud Build trigger that draws main's code under that identity and signs only a drawing in
# which every section agrees. test/infra/word-drawing.test.ts proves the identity's grants
# exhaustive and holds the trigger's source and values to what is written here.

locals {
  word_drawing_records = "${var.project_id}-${local.name_prefix}-word-drawings"
  # Object-name condition on the submissions bucket, as signer.tf's on the evidence bucket.
  submission_uploads = "projects/_/buckets/${google_storage_bucket.submissions.name}/objects/uploads/sha256/"

  # The two images the build's own steps run in, by digest. The Cloud SDK (588.0.0-slim, resolved
  # from gcr.io on 2026-10-07: git, curl, openssl and Python 3.13 beside gcloud) does everything
  # with the network; the Docker builder is the images build's (cloudbuild.images.yaml) and runs the
  # drawing image, hardened, with none.
  cloud_sdk_image = "gcr.io/google.com/cloudsdktool/cloud-sdk@sha256:c70885ba9f0450ea9987b1c5dc1ab80acc768bac2fe783962dc98a64ea74c806"
  docker_image    = "gcr.io/cloud-builders/docker@sha256:001fb4a870a84485cf198c80002f7af42f6456f2dbc261d70a0b5f0111470df4"

  # The steps after the first, in order, each one call of scripts/word-drawing/build.sh in main's
  # checkout (section 4, "The trigger and the build"). The two drawings run at once.
  word_drawing_steps = [
    { id = "pull", image = local.docker_image, args = ["pull"], after = ["exists"] },
    { id = "parse", image = local.docker_image, args = ["parse"], after = ["pull"] },
    { id = "fetch", image = local.cloud_sdk_image, args = ["fetch"], after = ["parse"] },
    { id = "draw-1", image = local.docker_image, args = ["draw", "1"], after = ["fetch"] },
    { id = "draw-2", image = local.docker_image, args = ["draw", "2"], after = ["fetch"] },
    { id = "record", image = local.docker_image, args = ["record"], after = ["draw-1", "draw-2"] },
    { id = "sign", image = local.cloud_sdk_image, args = ["sign"], after = ["record"] },
  ]
}

resource "google_service_account" "word_drawing" {
  account_id   = "ema-flow-word-drawing-${var.environment}"
  display_name = "EMA Flow Word drawing (${var.environment})"
  description  = "Draws a certified Word label as main's code does, and signs its record; nothing else."
}

# Records are signed in an HSM with this key, used by nothing else: RSA-PSS with SHA-256 on a
# 3072-bit key, the approval key's algorithm, since a record must stay verifiable as long as the
# evidence that keeps it. Watched by the key availability alert (keys.tf), as every key is.
resource "google_kms_crypto_key" "word_drawing_hsm" {
  name                       = "word-drawing-hsm"
  key_ring                   = google_kms_key_ring.evidence.id
  purpose                    = "ASYMMETRIC_SIGN"
  destroy_scheduled_duration = local.key_destroy_wait
  labels                     = merge(local.labels, { purpose = "word-drawing-signing" })

  version_template {
    algorithm        = "RSA_SIGN_PSS_3072_SHA256"
    protection_level = "HSM"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Sign, and no more: not signerVerifier. The public key is pinned in the repository (PR 3), so
# nothing reads it from Cloud KMS at run time.
resource "google_kms_crypto_key_iam_member" "word_drawing_signer" {
  crypto_key_id = google_kms_crypto_key.word_drawing_hsm.id
  role          = "roles/cloudkms.signer"
  member        = "serviceAccount:${google_service_account.word_drawing.email}"
}

# The records, at word/<key>/<drawing id>/<key version>.json. Only the drawing identity writes, and
# only to create: objectCreator and objectViewer, never objectAdmin, so replacing an object (which
# needs the delete permission) is refused by IAM itself. No versioning, so nothing is kept
# noncurrent out of a reader's sight. A record lost is drawn again on request; the worker keeps the
# bytes of each record it uses with the run's evidence.
resource "google_storage_bucket" "word_drawings" {
  name                        = local.word_drawing_records
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = false
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.evidence_encryption.id
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs_evidence_encryption]

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "word_drawing_record_creator" {
  bucket = google_storage_bucket.word_drawings.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.word_drawing.email}"
}

resource "google_storage_bucket_iam_member" "word_drawing_record_reader" {
  bucket = google_storage_bucket.word_drawings.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.word_drawing.email}"
}

# The worker reads a record at step 5 of its gate (PR 4). The producer does not read records.
resource "google_storage_bucket_iam_member" "worker_word_drawing_reader" {
  bucket = google_storage_bucket.word_drawings.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# The uploaded .docx, by its content address, and nothing else in the submissions bucket: never a
# submission, a page text or a report. A condition on an object's name cannot grant a listing.
resource "google_storage_bucket_iam_member" "word_drawing_upload_reader" {
  bucket = google_storage_bucket.submissions.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.word_drawing.email}"

  condition {
    title       = "uploads-only"
    description = "Read under uploads/sha256/ only."
    expression  = "resource.name.startsWith(\"${local.submission_uploads}\")"
  }
}

# The drawing image, pulled by the digest main pins (src/render/word-drawing/lock.json).
resource "google_artifact_registry_repository_iam_member" "word_drawing_image_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images_cmek.location
  repository = google_artifact_registry_repository.images_cmek.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.word_drawing.email}"
}

# A build under a user-specified service account logs to Cloud Logging only, which it must be
# allowed to write.
resource "google_project_iam_member" "word_drawing_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.word_drawing.email}"
}

# The one place a drawing is asked for: one message per request, whose JSON body's field `request`
# is the base64url (unpadded) of the DrawingRequest's canonical bytes. The producer publishes; today
# that is the operator, whose owner role already may, so no publisher is granted here until the
# label gateway exists.
resource "google_pubsub_topic" "word_drawing_requests" {
  name   = "${local.name_prefix}-word-drawing-requests"
  labels = local.labels

  depends_on = [google_project_service.required]
}

# The drawing build. Its source is main, fetched over HTTPS from the public repository by the first
# step (no repository connection: the design's "Step 2: PR 2 as built, and measured"), and its
# steps are written here, inline. The request reaches the first step alone, through `env`, never
# through a step's arguments or script; a message whose request is not base64url of at most 4,000
# characters starts no build (a request with every SmPC section assigned is about 1,250; 4,000
# reached the step whole when tested). A build left queued past queue_ttl expires rather than runs
# late.
resource "google_cloudbuild_trigger" "word_drawing" {
  name            = "${local.name_prefix}-word-drawing"
  location        = var.region
  description     = "Draws a certified Word label as main's code does and signs the record (docs/design/certified-word-drawing.md)."
  service_account = google_service_account.word_drawing.id

  pubsub_config {
    topic = google_pubsub_topic.word_drawing_requests.id
  }

  substitutions = {
    _REQUEST = "$(body.message.data.request)"
  }
  filter = "size(_REQUEST) > 0 && size(_REQUEST) <= 4000 && _REQUEST.matches(\"^[A-Za-z0-9_-]+$\")"

  build {
    timeout   = "600s"
    queue_ttl = "300s"

    options {
      logging = "CLOUD_LOGGING_ONLY"
      env = [
        "ENVIRONMENT=${var.environment}",
        "RECORDS=${google_storage_bucket.word_drawings.name}",
        "SUBMISSIONS=${google_storage_bucket.submissions.name}",
        "SIGNING_KEY=${google_kms_crypto_key.word_drawing_hsm.id}",
        "IMAGES=${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images_cmek.repository_id}",
      ]
    }

    # Step 1, slim: main's checkout, and the record looked for. If it exists, every later step ends
    # at once.
    step {
      id         = "exists"
      name       = local.cloud_sdk_image
      entrypoint = "bash"
      args = ["-c", join(" && ", [
        "git init -q .",
        "git remote add origin https://github.com/ogbetspp-coder/fhir_real_time_data_exchange.git",
        "git fetch -q --depth=1 origin refs/heads/main",
        "git checkout -q --detach FETCH_HEAD",
        "exec bash scripts/word-drawing/build.sh exists",
      ])]
      env = ["REQUEST=$${_REQUEST}"]
    }

    dynamic "step" {
      for_each = local.word_drawing_steps
      content {
        id         = step.value.id
        name       = step.value.image
        entrypoint = "bash"
        args       = concat(["scripts/word-drawing/build.sh"], step.value.args)
        wait_for   = step.value.after
      }
    }
  }

  depends_on = [
    google_kms_crypto_key_iam_member.word_drawing_signer,
    google_storage_bucket_iam_member.word_drawing_record_creator,
    google_storage_bucket_iam_member.word_drawing_record_reader,
    google_storage_bucket_iam_member.word_drawing_upload_reader,
    google_artifact_registry_repository_iam_member.word_drawing_image_reader,
    google_project_iam_member.word_drawing_log_writer,
  ]
}
