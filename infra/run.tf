locals {
  # The digest part of the worker image reference: every signed run manifest names the exact
  # bytes that produced it (runtime.imageDigest, src/pipeline.ts), as the query service's audit
  # records do (infra/query.tf). null when the reference carries no digest; the precondition on
  # the service turns that into a plan-time error.
  worker_image_digest = try(regex("@(sha256:[0-9a-f]{64})$", var.worker_image)[0], null)
}

resource "google_cloud_run_v2_service" "worker" {
  name                = "${local.name_prefix}-worker"
  location            = var.region
  deletion_protection = var.deletion_protection
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels              = local.labels

  dynamic "binary_authorization" {
    for_each = var.enforce_binary_authorization ? [true] : []
    content {
      use_default = true
    }
  }

  template {
    service_account                  = google_service_account.worker.email
    timeout                          = "1800s"
    max_instance_request_concurrency = 4

    scaling {
      min_instance_count = var.environment == "prod" ? 1 : 0
      max_instance_count = 10
    }

    containers {
      name  = "validator"
      image = var.validator_image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle          = false
        startup_cpu_boost = true
      }

      # The HL7 validator_cli.jar re-fetches its IG packages from the network on
      # every cold start (observed ~35-40s typically); keep some margin over that.
      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 2
        period_seconds        = 5
        failure_threshold     = 60

        tcp_socket {
          port = 8090
        }
      }
    }

    containers {
      name       = "worker"
      image      = var.worker_image
      depends_on = ["validator"]

      ports {
        name           = "http1"
        container_port = 8080
      }

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
        name  = "DRY_RUN"
        value = "false"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_LOCATION"
        value = var.region
      }
      env {
        name  = "HEALTHCARE_DATASET_ID"
        value = google_healthcare_dataset.record.name
      }
      env {
        name  = "SOURCE_FHIR_STORE_ID"
        value = local.source_fhir_store_id
      }
      env {
        name  = "TARGET_FHIR_STORE_ID"
        value = local.target_fhir_store_id
      }
      env {
        name  = "EVIDENCE_BUCKET"
        value = google_storage_bucket.evidence.name
      }
      env {
        name  = "SUBMISSION_BUCKET"
        value = google_storage_bucket.submissions.name
      }
      env {
        name  = "ENABLED_RUN_SOURCES"
        value = join(",", var.enabled_run_sources)
      }
      env {
        name  = "FHIR_ANALYTICS_DATASET"
        value = google_bigquery_dataset.fhir_analytics.dataset_id
      }
      env {
        name  = "TRANSFORMATION_LEDGER_DATASET"
        value = google_bigquery_dataset.ledger.dataset_id
      }
      env {
        name  = "TRANSFORMATION_LEDGER_TABLE"
        value = google_bigquery_table.transformation_runs.table_id
      }
      # The key VERSION, not the key: Cloud KMS refuses an AsymmetricSign whose name stops at the
      # crypto key (see var.kms_manifest_key_version in infra/variables.tf for why the version is
      # named rather than looked up).
      env {
        name  = "KMS_MANIFEST_KEY"
        value = "${google_kms_crypto_key.manifest_signing_hsm.id}/cryptoKeyVersions/${var.kms_manifest_key_version}"
      }
      env {
        name  = "FHIR_VALIDATOR_URL"
        value = "http://localhost:8090"
      }
      env {
        name  = "GLOBAL_EPI_PACKAGE"
        value = "hl7.fhir.uv.emedicinal-product-info#1.0.0"
      }
      # Tie every signed run manifest to the code and the image that produced it
      # (runtime.sourceCommit and runtime.imageDigest, src/pipeline.ts). Without these the worker
      # recorded "development" for both. The commit is the value the query service records as
      # QUERY_SERVICE_VERSION: scripts/gcp/deploy.sh passes the full git SHA as service_version.
      # WORKFLOW_REVISION is deliberately not set: the workflow depends on this service's URI, so
      # naming the workflow's revision here would be a dependency cycle. runtime.workflowRevision
      # falls back to K_REVISION, the revision name Cloud Run sets on the container itself.
      env {
        name  = "GIT_COMMIT"
        value = var.service_version
      }
      env {
        name  = "IMAGE_DIGEST"
        value = local.worker_image_digest
      }
    }
  }

  lifecycle {
    precondition {
      condition     = local.worker_image_digest != null
      error_message = "worker_image must be an image reference by digest (…@sha256:<64 hex>) so IMAGE_DIGEST can name the exact image in every signed run manifest."
    }
  }

  # The ledger table is listed so its schema is patched before a revision that writes the new
  # columns is created. That orders the apply; it does not close the window entirely, because
  # BigQuery's streaming path caches a table's schema for a few minutes, so an insert naming a
  # freshly added column can still be rejected shortly after the patch. Columns are only
  # populated by document runs, which no Zone A service produces yet, so the window is currently
  # unreachable; a retry on writeLedger is the fix when it stops being.
  depends_on = [
    google_project_service.required,
    google_healthcare_dataset_iam_member.worker_fhir_editor,
    google_storage_bucket_iam_member.worker_evidence_writer,
    google_storage_bucket_iam_member.worker_submission_reader,
    google_kms_crypto_key_iam_member.worker_manifest_signer_hsm,
    google_bigquery_dataset_iam_member.worker_ledger_writer,
    google_bigquery_table.transformation_runs,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "workflow_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

# roles/run.invoker on the worker for the deployer, and nothing else: the smoke run POSTs one
# fixture run and reads the answer. Keyed on var.deployer_account (infra/variables.tf, which says
# why only a service account is accepted).
#
# Today this binding authorises nothing new. The deployer holds roles/run.admin at project
# level, which already contains run.routes.invoke — the permission Cloud Run's edge checks — so
# the smoke call would succeed without it. It is declared anyway for two reasons: it states at
# the resource level which service the deploy is entitled to call, and it is what the call falls
# back on if the deployer's project-level run.admin is ever narrowed, which the roadmap's
# least-privilege item contemplates. Treat the 403 retry in scripts/gcp/deploy.sh as insurance
# for that future, not as the propagation window of a binding the call currently depends on.
resource "google_cloud_run_v2_service_iam_member" "deployer_invoker" {
  count    = var.deployer_account == "" ? 0 : 1
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.deployer_account}"
}

resource "google_workflows_workflow" "epi" {
  name                = "${local.name_prefix}-pipeline"
  region              = var.region
  description         = "Deterministic Type 2 to EMA ePI conversion, validation, persistence, and stream proof"
  service_account     = google_service_account.workflow.id
  call_log_level      = "LOG_ERRORS_ONLY"
  deletion_protection = var.deletion_protection
  labels              = local.labels
  source_contents     = file("${path.module}/../workflows/epi-pipeline.yaml")

  user_env_vars = {
    EMA_FLOW_SERVICE_URL      = google_cloud_run_v2_service.worker.uri
    EMA_FLOW_BIGQUERY_DATASET = google_bigquery_dataset.fhir_analytics.dataset_id
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.workflow_invoker,
    google_bigquery_dataset_iam_member.workflow_analytics_reader,
    google_project_iam_member.workflow_bigquery_job_user,
  ]
}
