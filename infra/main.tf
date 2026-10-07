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

# The project's APIs, and the only list of them (docs/foundations.md, C2). An API enabled but
# not listed here is drift. Measured 2026-09-22 against 30 days of request counts: each API below
# either served requests or is a dependency Google enables for one that did. disable_on_destroy is
# false, so removing a line stops managing an API without disabling it; disabling is a separate,
# deliberate `gcloud services disable`.
resource "google_project_service" "required" {
  for_each = toset([
    # The product.
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudkms.googleapis.com",
    "datalineage.googleapis.com",
    "documentai.googleapis.com",
    "healthcare.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
    # Deploy, identity and governance.
    "binaryauthorization.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "containeranalysis.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "orgpolicy.googleapis.com",
    "serviceusage.googleapis.com",
    "sts.googleapis.com",
    # The approver's surface: a Google Chat app built as a Workspace add-on, whose HTTP endpoint is
    # the approval signer (docs/design/approval.md, D2). Configured by the owner in the console.
    "chat.googleapis.com",
    # The assistant: Gemini Enterprise's connector, and Vertex AI for the agent (roadmap 1b).
    "aiplatform.googleapis.com",
    "discoveryengine.googleapis.com",
    # Enabled by Google as dependencies of the above; listed so the list is complete. Compute
    # served 5 requests in 30 days from a Google-managed caller and holds no instance.
    "analyticshub.googleapis.com",
    "bigqueryconnection.googleapis.com",
    "bigquerydatapolicy.googleapis.com",
    "bigquerymigration.googleapis.com",
    "bigqueryreservation.googleapis.com",
    "bigquerystorage.googleapis.com",
    "cloudapis.googleapis.com",
    "cloudtrace.googleapis.com",
    "compute.googleapis.com",
    "dataform.googleapis.com",
    "servicemanagement.googleapis.com",
    "storage-api.googleapis.com",
    "storage-component.googleapis.com",
    "telemetry.googleapis.com",
    # Refused when disabling on 2026-09-22 because a declared service holds them: cloudapis holds
    # bigquerydatatransfer, dataplex and sql-component; binaryauthorization holds container;
    # binaryauthorization, compute and container hold oslogin. Disabling would take the holder too.
    "bigquerydatatransfer.googleapis.com",
    "container.googleapis.com",
    "dataplex.googleapis.com",
    "oslogin.googleapis.com",
    "sql-component.googleapis.com",
    # Re-enabled by Cloud Build on the next deploy after it was disabled, so a dependency of it.
    "containerregistry.googleapis.com",
    # Kept, unused today: the entitlement store's candidate home (Firestore in Datastore mode is
    # one option), decided before a second tenant.
    "datastore.googleapis.com",
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

# Created before the artifacts key is granted to it; Google otherwise creates it on first use
# (keys.tf; audit I-10).
resource "google_project_service_identity" "artifact_registry" {
  provider = google-beta
  project  = var.project_id
  service  = "artifactregistry.googleapis.com"

  depends_on = [google_project_service.required]
}

# The image repository on the `artifacts` key (CMEK step 4). A repository's encryption is fixed at
# creation, so in dev this is a second repository, made beside the Google-managed `ema-flow` one,
# which was deleted once a deploy from this one was verified (2026-09-21). Nothing signs the images
# or attaches provenance to them yet.
resource "google_artifact_registry_repository" "images_cmek" {
  location      = var.region
  repository_id = "ema-flow-images"
  description   = "ema-flow container images, encrypted with the artifacts key"
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

  # Every table the stream creates is encrypted with our key (CMEK step 3). In dev, the tables made
  # before the key existed were converted to it in place, once (docs/design/cmek-rollout.md).
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

  # The provider treats this as forcing replacement. Dev's table was converted in place to exactly
  # this key before it was set (docs/design/cmek-rollout.md, step 3), so the plan shows no change;
  # were the string to differ in form, the plan would show a replacement, and deletion_protection
  # and prevent_destroy make that plan fail rather than delete the ledger.
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
    { name = "ingestion_source_hash", type = "STRING", mode = "NULLABLE", description = "SHA-256 of the source document: a drawn document, or the authority's pinned document" },
    { name = "fidelity_status", type = "STRING", mode = "NULLABLE", description = "Narrative fidelity outcome; only passed can be persisted" },
    { name = "approval_hash", type = "STRING", mode = "NULLABLE", description = "SHA-256 of the approved content: approved by a person, or an authority's publication imported at a person's request" },
    { name = "transaction_sha256", type = "STRING", mode = "NULLABLE", description = "SHA-256 of the FHIR transaction the signed manifest authorised and the run committed; null before run manifest 3.0.0" },
    { name = "target_bundle_version_id", type = "STRING", mode = "NULLABLE", description = "The EMA document Bundle version the transaction wrote, as its response named it" },
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

# The FHIR dataset on the fhir-record key (CMEK step 5), holding the two stores
# scripts/gcp/reconcile-fhir-stores.sh creates. A Healthcare dataset's encryption is fixed at
# creation, so in dev this was made beside the Google-managed `epi` dataset; the stores were
# reconciled into it, the services switched to it, and `epi` was released from Terraform and then
# deleted by hand (2026-09-21, docs/design/cmek-rollout.md).
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

# The two stores in the form a store-level IAM grant names them. The stores themselves are created
# by scripts/gcp/reconcile-fhir-stores.sh, which scripts/gcp/deploy.sh runs before an apply when
# either is missing, so a grant on a store never precedes the store.
locals {
  source_fhir_store_path = "${var.project_id}/${var.region}/${google_healthcare_dataset.record.name}/${local.source_fhir_store_id}"
  target_fhir_store_path = "${var.project_id}/${var.region}/${google_healthcare_dataset.record.name}/${local.target_fhir_store_id}"
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
