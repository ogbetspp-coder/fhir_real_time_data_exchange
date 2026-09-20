# The read-only ePI query service (docs/design/epi-mcp-query-service.md). ADR 0004: a
# component with a different trust level than the worker is its own deployable, with its own
# service account holding least-privilege IAM, its own configuration, and its own image. This
# service never writes: it holds a dataset-scoped reader role and nothing else.

locals {
  query_service_name = "${local.name_prefix}-query"

  # Cloud Run's deterministic URL, https://<service>-<project number>.<region>.run.app, is a
  # function of values known before the service exists, so the audience the container checks
  # tokens against can be set on the same apply that creates it. Cloud Run also serves the
  # service on a second, legacy hostname (https://<service>-<hash>-<region code>.a.run.app),
  # which is what google_cloud_run_v2_service.query.uri reports; both hostnames reach the
  # service, only this one is the audience. The outputs keep the two apart by name.
  query_deterministic_url = "https://${local.query_service_name}-${data.google_project.current.number}.${var.region}.run.app"

  query_audience = var.query_audience != "" ? var.query_audience : local.query_deterministic_url

  # The digest part of the image reference, and nothing else: an audit record names the exact
  # bytes that produced it (ADR 0004, decision 1). null when the reference carries no digest;
  # the precondition on the service turns that into a plan-time error.
  query_image_digest = try(regex("@(sha256:[0-9a-f]{64})$", var.query_image)[0], null)
}

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
  name                = local.query_service_name
  location            = var.region
  deletion_protection = var.deletion_protection
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels              = local.labels

  template {
    service_account = google_service_account.query.email
    # A tool call is a handful of FHIR reads; nothing here should take a minute. Concurrency
    # is capped against the single CPU for the same reason the worker sets 4.
    timeout                          = "60s"
    max_instance_request_concurrency = 8

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
      env {
        name  = "QUERY_AUDIENCE"
        value = local.query_audience
      }
      env {
        name  = "QUERY_ENTITLEMENTS_JSON"
        value = var.query_entitlements_json
      }
      env {
        name  = "QUERY_SERVICE_VERSION"
        value = var.service_version
      }
      env {
        name  = "IMAGE_DIGEST"
        value = local.query_image_digest
      }
      # Present only when at least one client id is configured; the container treats an absent
      # variable as "no additional audiences".
      dynamic "env" {
        for_each = length(var.query_oauth_client_ids) > 0 ? [1] : []
        content {
          name  = "QUERY_OAUTH_CLIENT_IDS"
          value = join(",", var.query_oauth_client_ids)
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = local.query_image_digest != null
      error_message = "query_image must be an image reference by digest (…@sha256:<64 hex>) so IMAGE_DIGEST can name the exact image in every audit record."
    }

    # Evaluated after every apply of this resource: the audience the container was given must
    # be a URL Cloud Run reports for the service, or the apply fails. Skipped when an operator
    # supplies query_audience explicitly, since a custom hostname is not in that list.
    postcondition {
      condition     = var.query_audience != "" || contains(self.urls, local.query_audience)
      error_message = "The computed QUERY_AUDIENCE is not one of the URLs Cloud Run reports for the query service; the deterministic-URL assumption does not hold here. Set query_audience explicitly."
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

# The identity the ID-token recipe in README.md impersonates. Google refuses
# `gcloud auth print-identity-token --audiences=...` for a user account, so a human who needs an
# ID token for this service mints one as a service account; this account exists for that and
# nothing else. It holds roles/run.invoker on the query service (granted below) and no other
# role: no Healthcare dataset role, no project role, no key. Who may mint tokens as it is
# var.query_token_creators, and a token minted this way authenticates as this account, so the
# entitlement map must be keyed by this account's `sub`, not by the human's.
#
# test/query/acceptance.test.ts asserts this account's roles are exactly one: run.invoker on
# the query service. It names both accounts by their Terraform resource name, so nothing here
# needs to be worded to avoid a test.
resource "google_service_account" "caller" {
  account_id   = "ema-flow-caller-${var.environment}"
  display_name = "EMA Flow MCP caller (${var.environment})"
  description  = "Impersonation-only identity for calling the read-only MCP service. Holds roles/run.invoker on that service and no other permission."
}

# Not a member of var.query_invokers: this grant is part of the account's definition, so the
# recipe works on any deploy that creates the account. A separate resource rather than an entry
# in the for_each above, because a for_each key that is only known after apply fails the plan.
resource "google_cloud_run_v2_service_iam_member" "caller_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.query.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.caller.email}"
}

# roles/iam.serviceAccountTokenCreator on the caller account alone, never on the project: these
# members may mint tokens as that one account. Project owner does not carry this permission —
# `gcloud auth print-identity-token --impersonate-service-account=...` run by the project owner
# of sage-ship-509104-b8 on 2026-09-20 was refused with
# "Permission 'iam.serviceAccounts.getAccessToken' denied" — so an operator who wants the
# impersonation recipe names themselves here.
resource "google_service_account_iam_member" "caller_token_creator" {
  for_each = toset(var.query_token_creators)

  service_account_id = google_service_account.caller.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}
