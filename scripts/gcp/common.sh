# Shared noninteractive helpers for GCP deploy/bootstrap scripts.
# shellcheck shell=bash

export CLOUDSDK_CORE_DISABLE_PROMPTS="${CLOUDSDK_CORE_DISABLE_PROMPTS:-1}"
export CLOUDSDK_SUPPRESS_GOOG_CREDS_WARNING="${CLOUDSDK_SUPPRESS_GOOG_CREDS_WARNING:-true}"
export TF_IN_AUTOMATION="${TF_IN_AUTOMATION:-1}"
export TF_INPUT="${TF_INPUT:-0}"

ema_flow_resolve_project() {
  local project="${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT_ID:-}}"
  if [[ -z "$project" || "$project" == "(unset)" ]]; then
    project="$(gcloud --quiet config get-value project 2>/dev/null || true)"
  fi
  if [[ -z "$project" || "$project" == "(unset)" ]]; then
    echo "Set GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID, or configure a gcloud project." >&2
    exit 1
  fi
  printf '%s' "$project"
}

ema_flow_access_token() {
  local token=""
  # Workload Identity Federation in GitHub Actions authenticates gcloud itself.
  token="$(gcloud --quiet auth print-access-token 2>/dev/null || true)"
  if [[ -z "$token" ]]; then
    token="$(gcloud --quiet auth application-default print-access-token 2>/dev/null || true)"
  fi
  if [[ -z "$token" ]]; then
    echo "Unable to obtain a Google Cloud access token." >&2
    exit 1
  fi
  printf '%s' "$token"
}
