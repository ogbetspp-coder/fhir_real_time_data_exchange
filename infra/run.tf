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
        cpu_idle = false
      }

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
        value = google_healthcare_dataset.epi.name
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
      env {
        name  = "KMS_MANIFEST_KEY"
        value = google_kms_crypto_key.manifest_signing.id
      }
      env {
        name  = "FHIR_VALIDATOR_URL"
        value = "http://localhost:8090"
      }
      env {
        name  = "GLOBAL_EPI_PACKAGE"
        value = "hl7.fhir.uv.emedicinal-product-info#1.0.0"
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_project_iam_member.worker_healthcare,
    google_storage_bucket_iam_member.worker_evidence_writer,
    google_kms_crypto_key_iam_member.worker_manifest_signer,
    google_bigquery_dataset_iam_member.worker_ledger_writer,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "workflow_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
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
