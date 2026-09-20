variable "project_id" {
  description = "Google Cloud project ID for this environment."
  type        = string
}

variable "region" {
  description = "EU region used for all regional resources."
  type        = string
  default     = "europe-west4"
}

variable "environment" {
  description = "Environment name used in labels and resource names."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "validation", "prod"], var.environment)
    error_message = "environment must be dev, validation, or prod."
  }
}

variable "worker_image" {
  description = "Immutable Artifact Registry worker image reference, preferably by digest."
  type        = string
}

variable "validator_image" {
  description = "Immutable Artifact Registry validator image reference, preferably by digest."
  type        = string
}

variable "evidence_retention_days" {
  description = "Minimum retention for qualification-supporting evidence."
  type        = number
  default     = 2555

  validation {
    condition     = var.evidence_retention_days >= 30
    error_message = "Evidence retention must be at least 30 days."
  }
}

variable "submission_retention_days" {
  description = "Retention for approved Zone A submissions. 0 disables the policy; set per client."
  type        = number
  default     = 0

  validation {
    condition     = var.submission_retention_days == 0 || var.submission_retention_days >= 30
    error_message = "Submission retention must be 0 (disabled) or at least 30 days."
  }
}

variable "bigquery_partition_expiration_days" {
  description = "FHIR history partition retention. Set null to retain indefinitely."
  type        = number
  default     = 2555
  nullable    = true
}

variable "alert_notification_channels" {
  description = "Existing Cloud Monitoring notification channel resource names."
  type        = list(string)
  default     = []
}

variable "deletion_protection" {
  description = "Protect Cloud Run and Healthcare resources from accidental deletion."
  type        = bool
  default     = false
}

variable "enforce_binary_authorization" {
  description = "Require Binary Authorization on Cloud Run. Leave false for the first prototype deploy."
  type        = bool
  default     = false
}

variable "enabled_run_sources" {
  description = <<-EOT
    Run sources the worker accepts on POST /v1/runs (ENABLED_RUN_SOURCES). fixture and
    healthcare-api bypass the document gate (ADR 0002); a deployment that handles anything but
    synthetic content sets this to ["document"]. Default: every source.
  EOT
  type        = list(string)
  default     = ["fixture", "healthcare-api", "document"]

  validation {
    condition = length(var.enabled_run_sources) > 0 && alltrue([
      for source in var.enabled_run_sources : contains(["fixture", "healthcare-api", "document"], source)
    ])
    error_message = "enabled_run_sources must be a non-empty subset of fixture, healthcare-api, document."
  }
}

variable "query_image" {
  description = "Immutable Artifact Registry query service image reference, preferably by digest."
  type        = string
}

variable "query_invokers" {
  description = "IAM members granted roles/run.invoker on the query service. No allUsers."
  type        = list(string)
  default     = []
}

variable "query_entitlements_json" {
  description = <<-EOT
    Phase-1 entitlement map, JSON-encoded, in the shape {"<principal>": {"bundles": ["<bundle id>", ...]}}.
    A principal is the token's `sub` claim (an opaque identifier, not an e-mail address); a
    bundle id is a FHIR resource id in the validated store.
  EOT
  type        = string
  default     = "{}"
  sensitive   = false

  # Mirrors what src/query/entitlements.ts enforces at container start (PrincipalId and FhirId
  # from src/contracts/common.ts), so a map the service would reject fails the plan instead of
  # the startup probe. The decode and each shape check are wrapped in can()/try() so a
  # malformed value produces this error message rather than an evaluation error.
  validation {
    condition = (
      can(keys(jsondecode(var.query_entitlements_json)))
      && alltrue([
        for principal, entitlement in try({ for k, v in jsondecode(var.query_entitlements_json) : k => v }, {}) :
        can(regex("^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$", principal))
        && can(tolist(entitlement.bundles))
        && alltrue([
          for bundle in try(tolist(entitlement.bundles), ["invalid bundle id"]) :
          can(regex("^[A-Za-z0-9.-]{1,64}$", bundle))
        ])
      ])
    )
    error_message = "query_entitlements_json must be a JSON object whose keys match ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$ and whose values are objects with a \"bundles\" list of FHIR ids (^[A-Za-z0-9.-]{1,64}$)."
  }
}

variable "query_oauth_client_ids" {
  description = <<-EOT
    OAuth 2.0 client ids the query service additionally accepts as the audience of a
    Google-signed ID token, joined with "," into QUERY_OAUTH_CLIENT_IDS. Empty omits the
    variable, so only the service URL audience is accepted.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for client_id in var.query_oauth_client_ids : can(regex("^[A-Za-z0-9._-]+$", client_id))
    ])
    error_message = "Every query_oauth_client_ids entry must match ^[A-Za-z0-9._-]+$."
  }
}

variable "query_audience" {
  description = <<-EOT
    OIDC audience the query service checks incoming tokens against. Empty (the default) means
    the service's deterministic Cloud Run URL,
    https://<service name>-<project number>.<region>.run.app, which is known before the service
    exists so no bootstrap apply is needed. Set it only to front the service with another
    hostname; the URL assertion on the service is skipped in that case.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.query_audience == "" || can(regex("^https://[^/\\s]+$", var.query_audience))
    error_message = "query_audience must be empty or an https:// origin without a path."
  }
}

variable "service_version" {
  description = <<-EOT
    Identifier of the code being deployed, recorded as QUERY_SERVICE_VERSION in every query
    audit record. scripts/gcp/deploy.sh passes the git commit SHA; the default marks an apply
    made outside that script.
  EOT
  type        = string
  default     = "local"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$", var.service_version))
    error_message = "service_version must match the contract Token alphabet ^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$."
  }
}

variable "alert_notification_email" {
  description = <<-EOT
    E-mail address that receives the query entitlement-denial alert. Empty (the default) creates
    the log-based metric but no notification channel and no alerting policy.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.alert_notification_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_notification_email))
    error_message = "alert_notification_email must be empty or an e-mail address."
  }
}

variable "lock_regulated_audit_log_bucket" {
  description = <<-EOT
    Lock the retained regulated-audit log bucket (Cloud Logging Bucket Lock). Locking is
    irreversible: a locked bucket's retention period cannot be changed and the bucket cannot be
    deleted until every entry in it has passed retention. Leave false until the retention
    period is the one the deployment owner requires.
  EOT
  type        = bool
  default     = false
}
