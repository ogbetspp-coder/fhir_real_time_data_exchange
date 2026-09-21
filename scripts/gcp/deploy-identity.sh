#!/usr/bin/env bash
# Who may become the deployer (docs/foundations.md, finding B1).
#
# The deployer service account holds project IAM admin among nineteen roles, so whoever can
# assume it can grant themselves anything. GitHub Actions assumes it through the Workload
# Identity provider below, and the provider's attribute condition is the only thing deciding
# which workflow runs qualify. Until 2026-09-21 that condition named the repository and nothing
# else, so a workflow on any branch qualified.
#
# It now requires all three of:
#   - the repository, by numeric id: a name can be re-registered after a rename or transfer,
#     an id cannot;
#   - the ref `refs/heads/main`: a workflow dispatched on, or pushed to, any other branch is
#     refused at the token exchange, before any credential exists;
#   - the deploy workflow file on main: another workflow in the repository, even on main, is
#     refused.
#
# The pool and provider were created by hand at bootstrap and are not in Terraform, like the
# deployer's own roles. This script is the source of truth for the condition. It is idempotent:
# it prints the condition in force, sets the intended one if they differ, and prints it again.
#
#   bash scripts/gcp/deploy-identity.sh            # apply
#   bash scripts/gcp/deploy-identity.sh --check    # report only; exit 1 if it differs
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-sage-ship-509104-b8}"
POOL="github-pool"
PROVIDER="github-provider"
REPOSITORY="ogbetspp-coder/fhir_real_time_data_exchange"
REPOSITORY_ID="1376667427"

CONDITION="assertion.repository_id=='${REPOSITORY_ID}' && assertion.ref=='refs/heads/main' && assertion.workflow_ref.startsWith('${REPOSITORY}/.github/workflows/deploy.yml@refs/heads/main')"

current="$(gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --workload-identity-pool="$POOL" --location=global --project="$PROJECT_ID" \
  --format='value(attributeCondition)')"
echo "in force: ${current}"

if [[ "$current" == "$CONDITION" ]]; then
  echo "The deploy identity condition is as intended."
  exit 0
fi
if [[ "${1:-}" == "--check" ]]; then
  echo "intended: ${CONDITION}" >&2
  echo "The deploy identity condition differs from the intended one." >&2
  exit 1
fi

gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" \
  --workload-identity-pool="$POOL" --location=global --project="$PROJECT_ID" \
  --attribute-condition="$CONDITION" >/dev/null
echo "now:      $(gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --workload-identity-pool="$POOL" --location=global --project="$PROJECT_ID" \
  --format='value(attributeCondition)')"
