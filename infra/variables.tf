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
  description = "Phase-1 entitlement map, JSON-encoded: principal subject to organisation and bundles."
  type        = string
  default     = "{}"
  sensitive   = false

  validation {
    condition     = can(jsondecode(var.query_entitlements_json))
    error_message = "query_entitlements_json must be valid JSON."
  }
}

variable "query_audience" {
  description = <<-EOT
    OIDC audience the query service checks incoming tokens against: its own Cloud Run URI.
    Unknown before the service first exists, so this is a two-apply bootstrap -- leave the
    default on the first apply, then re-apply with this set to the query_service_url output.
  EOT
  type        = string
  default     = ""
}
