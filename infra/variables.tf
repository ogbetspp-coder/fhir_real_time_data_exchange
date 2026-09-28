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
  description = "Environment name used in labels and resource names. No default: every plan, apply and import in scripts/gcp/deploy.sh passes it, so an apply that forgets it fails instead of silently targeting dev's resource names."
  type        = string

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

variable "alert_notification_channels" {
  description = <<-EOT
    Existing Cloud Monitoring notification channel resource names
    (projects/<project>/notificationChannels/<id>), paged by every alert policy beside the
    alert_notification_email channel. At least one of the two must be given: an apply with
    neither is refused.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for channel in var.alert_notification_channels :
      can(regex("^projects/[^/\\s]+/notificationChannels/[^/\\s]+$", channel))
    ])
    error_message = "Every alert_notification_channels entry must be a channel resource name, projects/<project>/notificationChannels/<id>."
  }
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

variable "allow_synthetic_sources" {
  description = <<-EOT
    Whether the worker accepts synthetic content (ALLOW_SYNTHETIC_SOURCES,
    docs/design/authority-import-contract.md D7). Off, the gate refuses anything synthetic and the
    gate-bypassing fixture and healthcare-api sources cannot be enabled. The dev deploy sets it.
  EOT
  type        = bool
  default     = false
}

variable "enabled_run_sources" {
  description = <<-EOT
    Run sources the worker accepts on POST /v1/runs (ENABLED_RUN_SOURCES). fixture and
    healthcare-api bypass the document gate (ADR 0002) and need allow_synthetic_sources.
    Default (null): every source where synthetic sources are allowed, otherwise ["document"].
  EOT
  type        = list(string)
  default     = null

  validation {
    condition = var.enabled_run_sources == null ? true : (length(var.enabled_run_sources) > 0 && alltrue([
      for source in var.enabled_run_sources : contains(["fixture", "healthcare-api", "document"], source)
    ]))
    error_message = "enabled_run_sources must be a non-empty subset of fixture, healthcare-api, document."
  }

  validation {
    condition = var.enabled_run_sources == null ? true : (var.allow_synthetic_sources || alltrue([
      for source in var.enabled_run_sources : source == "document"
    ]))
    error_message = "fixture and healthcare-api bypass the document gate and need allow_synthetic_sources = true."
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

  validation {
    condition = alltrue([
      for member in var.query_invokers :
      can(regex("^(user|group|serviceAccount):[^\\s]+$", member))
    ])
    error_message = "Every query_invokers entry must be user:, group:, or serviceAccount: followed by an identifier. allUsers and allAuthenticatedUsers are not accepted."
  }
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

  # Checks what src/query/entitlements.ts checks at container start (a JSON object; PrincipalId
  # keys; each value an object whose only key is `bundles`; at most 10,000 FhirId members, each a
  # JSON string), including the strictObject rule that rejects an unknown key such as a
  # carried-forward `organisation`. Each member's type is read from its own JSON encoding, because
  # regex() and tolist() convert a number or a bool to a string, which let `"bundles": [5]` pass
  # here and then stop the service at startup. The decode and each shape check are wrapped in
  # can()/try() so a malformed value produces this error message rather than an evaluation error.
  validation {
    condition = (
      can(keys(jsondecode(var.query_entitlements_json)))
      && alltrue([
        for principal, entitlement in try(jsondecode(var.query_entitlements_json), {}) :
        can(regex("^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$", principal))
        && try(keys(entitlement), []) == ["bundles"]
        && can(tolist(entitlement.bundles))
        && try(length(entitlement.bundles), 10001) <= 10000
        && try(alltrue([
          for bundle in entitlement.bundles :
          startswith(jsonencode(bundle), "\"") && can(regex("^[A-Za-z0-9.-]{1,64}$", bundle))
        ]), false)
      ])
    )
    error_message = "query_entitlements_json must be a JSON object whose keys match ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$ and whose values are objects carrying exactly one key, \"bundles\", a list of at most 10,000 FHIR ids, each a JSON string matching ^[A-Za-z0-9.-]{1,64}$. An extra key such as \"organisation\" is rejected here because the service rejects it at startup."
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
    E-mail address paged by every alert policy: an encryption key made unavailable, a failed
    pipeline run, and query entitlement denials. Empty (the default) creates no e-mail channel,
    and an apply with no alert_notification_channels either is refused: every alert policy is
    always declared and must reach someone.
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

# The deployer service account, which the post-apply smoke run authenticates as. It is
# bootstrapped outside this configuration (README.md), so it is named by the deploy rather than
# declared here: scripts/gcp/deploy.sh phase_apply passes the active gcloud account, and passes
# it only when that account is a service account. It is granted run.invoker on the worker
# (infra/run.tf), actAs on the build identity (infra/build.tf) and FHIR editor on the record
# dataset (infra/security.tf). Empty (the default) declares none of these bindings.
#
# A human's account is deliberately not accepted. gcloud refuses `print-identity-token
# --audiences=` for user credentials, so a person cannot present a token this binding would
# authorise, and granting one would leave a standing privilege on the worker that no documented
# path can exercise. A local operator supplies WORKER_ID_TOKEN instead, minted by impersonating
# a service account that already holds run.invoker.
variable "deployer_account" {
  description = "E-mail of the service account running the deploy, granted roles/run.invoker on the worker so the post-apply smoke run (scripts/gcp/deploy.sh phase_smoke) can call it. Empty declares no binding."
  type        = string
  default     = ""

  validation {
    condition     = var.deployer_account == "" || endswith(var.deployer_account, ".gserviceaccount.com")
    error_message = "deployer_account must be a service account e-mail or empty: a user account cannot mint an ID token for the worker's audience, so a binding for one would never be usable."
  }
}

# Cloud KMS signs with a crypto key VERSION, not a crypto key: AsymmetricSign's `name` must end
# in /cryptoKeyVersions/<n>. `google_kms_crypto_key.manifest_signing.id` stops at the key, so
# passing it to the worker meant every signing call was refused and no run ever produced a signed
# manifest.
#
# The version is named here rather than read with a `google_kms_crypto_key_version` data source,
# because that data source fetches the version's PUBLIC KEY and so requires
# `cloudkms.cryptoKeyVersions.viewPublicKey`. The deploy identity does not hold it, and granting
# it would widen the deployer's reach into key material to obtain a string that is already known.
# Cloud KMS does not rotate asymmetric signing keys automatically, so this only changes when a
# person deliberately creates a version — a configuration change, which is what this is.
variable "kms_manifest_key_version" {
  description = "Version of manifest-signing-hsm (keys.tf) the worker signs with. Cloud KMS does not rotate asymmetric signing keys automatically, so this changes only when a new version is created by hand."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.kms_manifest_key_version))
    error_message = "kms_manifest_key_version must be a positive integer, as Cloud KMS numbers crypto key versions from 1."
  }
}
