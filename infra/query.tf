# The read-only ePI query service (docs/design/epi-mcp-query-service.md). ADR 0004: a
# component with a different trust level than the worker is its own deployable, with its own
# service account holding least-privilege IAM, its own configuration, and its own image. This
# service never writes: it holds a dataset-scoped reader role and nothing else.

resource "google_service_account" "query" {
  account_id   = "ema-flow-query-${var.environment}"
  display_name = "EMA Flow query service (${var.environment})"
}

# Dataset-level, not project-level: the query service can read FHIR resources in this dataset
# and nothing outside it, unlike the worker's project-level editor role.
resource "google_healthcare_dataset_iam_member" "query_fhir_reader" {
  dataset_id = google_healthcare_dataset.epi.id
  role       = "roles/healthcare.fhirResourceReader"
  member     = "serviceAccount:${google_service_account.query.email}"
}

resource "google_project_iam_member" "query_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.query.email}"
}

resource "google_cloud_run_v2_service" "query" {
  name                = "${local.name_prefix}-query"
  location            = var.region
  deletion_protection = var.deletion_protection
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels              = local.labels

  template {
    service_account = google_service_account.query.email

    scaling {
      min_instance_count = var.environment == "prod" ? 1 : 0
      max_instance_count = 10
    }

    containers {
      name  = "query"
      image = var.query_image

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
        name  = "TARGET_FHIR_STORE_ID"
        value = local.target_fhir_store_id
      }
      # QUERY_AUDIENCE is the service's own URI, which the provider cannot resolve from
      # inside this same resource block (referencing google_cloud_run_v2_service.query.uri
      # here would be a self-reference cycle). This is a two-apply bootstrap: the first
      # apply ships with var.query_audience at its default; once it succeeds, re-apply with
      # -var="query_audience=$(terraform -chdir=infra output -raw query_service_url)" so the
      # running revision checks incoming tokens against its real URL. See README.md.
      env {
        name  = "QUERY_AUDIENCE"
        value = var.query_audience
      }
      env {
        name  = "QUERY_ENTITLEMENTS_JSON"
        value = var.query_entitlements_json
      }
      env {
        name  = "QUERY_SERVICE_VERSION"
        value = "terraform"
      }
    }
  }

  depends_on = [
    google_healthcare_dataset_iam_member.query_fhir_reader,
    google_project_iam_member.query_log_writer,
  ]
}

# No allUsers invoker: every caller is an explicit member of this list, granted
# roles/run.invoker and nothing broader.
resource "google_cloud_run_v2_service_iam_member" "query_invoker" {
  for_each = toset(var.query_invokers)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.query.name
  role     = "roles/run.invoker"
  member   = each.value
}
