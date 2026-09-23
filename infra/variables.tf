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
  description = <<-EOT
    Protect the ledger table, the Cloud Run services and the workflow from deletion by an apply.
    On by default since 2026-09-21: while it defaulted to false, any change the provider treats as
    forcing replacement of the ledger table — a retyped column, an encryption key — would have
    been applied by the unattended deploy as a delete and a create. Set it false only for a
    deliberate, reviewed teardown.
  EOT
  type        = bool
  default     = true
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
  description = <<-EOT
    IAM members granted roles/run.invoker on the query service, in gcloud member syntax
    (user:, serviceAccount:, group:). No allUsers.

    Cloud Run resolves run.invoker against the identity inside the bearer token, so the entry
    here has to be the identity the token authenticates as, not the person who typed the
    command:

    - access-token path (a human calling with `gcloud auth print-access-token`): the token
      authenticates as the human, so list `user:<their e-mail>` here, and key their
      query_entitlements_json entry by their own `sub`.
    - impersonation path (`gcloud auth print-identity-token
      --impersonate-service-account=...`): the token authenticates as the service account, so
      nothing is needed here — infra/query.tf grants run.invoker to the caller service account
      it creates. The human needs roles/iam.serviceAccountTokenCreator on that account
      (var.query_token_creators) and the entitlement is keyed by the service account's `sub`.

    An agent or workload with its own service account identity belongs here as
    `serviceAccount:<its e-mail>`.
  EOT
  type        = list(string)
  default     = []
}

variable "query_token_creators" {
  description = <<-EOT
    IAM members granted roles/iam.serviceAccountTokenCreator on the caller service account
    created in infra/query.tf (`ema-flow-caller-<environment>`), in gcloud member syntax. That
    role is bound on that one service account, never on the project, and it is what lets a
    member run `gcloud auth print-identity-token --impersonate-service-account=<that account>
    --audiences=<query_audience> --include-email`.

    Project owner does not include this permission, so an operator who owns the project still
    has to name themselves here. Empty (the default) means nobody can impersonate the account
    and the ID-token recipe in README.md is unavailable.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for member in var.query_token_creators :
      can(regex("^(user|group|serviceAccount):[^\\s]+$", member))
    ])
    error_message = "Every query_token_creators entry must be user:, group:, or serviceAccount: followed by an identifier. allUsers and allAuthenticatedUsers are not accepted."
  }
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

  # Checks the same four things src/query/entitlements.ts checks at container start (a JSON
  # object; PrincipalId keys; each value an object whose only key is `bundles`; FhirId members),
  # including the strictObject rule that rejects an unknown key such as a carried-forward
  # `organisation`. The decode and each shape check are wrapped in can()/try() so a malformed
  # value produces this error message rather than an evaluation error.
  validation {
    condition = (
      can(keys(jsondecode(var.query_entitlements_json)))
      && alltrue([
        for principal, entitlement in try(jsondecode(var.query_entitlements_json), {}) :
        can(regex("^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$", principal))
        && try(keys(entitlement), []) == ["bundles"]
        && can(tolist(entitlement.bundles))
        && alltrue([
          for bundle in try(tolist(entitlement.bundles), ["invalid bundle id"]) :
          can(regex("^[A-Za-z0-9.-]{1,64}$", bundle))
        ])
      ])
    )
    error_message = "query_entitlements_json must be a JSON object whose keys match ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$ and whose values are objects carrying exactly one key, \"bundles\", a list of FHIR ids (^[A-Za-z0-9.-]{1,64}$). An extra key such as \"organisation\" is rejected here because the service rejects it at startup."
  }
}

variable "query_oauth_client_ids" {
  description = <<-EOT
    OAuth 2.0 client ids whose opaque Google access tokens the query service accepts, joined
    with "," into QUERY_OAUTH_CLIENT_IDS. src/query/auth.ts sends a non-JWT bearer token to
    Google's tokeninfo endpoint and accepts it only when the returned `aud` or `azp` is one of
    these ids; ID tokens take the other path and are checked against QUERY_AUDIENCE alone, so
    an id here never widens what ID token audiences are accepted. Empty (the default) omits the
    variable and every access token is rejected.

    Adding "32555940559.apps.googleusercontent.com", the client id of the gcloud CLI, makes
    `gcloud auth print-access-token` a working credential for this service: a human who holds
    roles/run.invoker on it (var.query_invokers) and an entitlement keyed by their Google
    account's `sub` (var.query_entitlements_json) can then call it with their own account.
    `gcloud auth print-identity-token --audiences=...` cannot serve that case: Google refuses
    the flag for user accounts. That client id is built into every gcloud installation
    worldwide, so naming it proves only that a token came from gcloud and never who presented
    it: the controls that decide access stay Cloud Run IAM on the caller's own identity and the
    per-subject entitlement map.
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
    hostname; the URL assertion on the service is skipped in that case. The `query_audience`
    output reports the value the container was given, whichever branch applied.
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
    audit record and as GIT_COMMIT (runtime.sourceCommit) in every signed run manifest the
    worker writes. scripts/gcp/deploy.sh passes the git commit SHA; the default marks an apply
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

variable "query_log_rejection_reason" {
  description = "Log why the query service refused a credential, as a category. Dev only; never returned to the caller."
  type        = bool
  default     = false
}
