#!/usr/bin/env bash
# The project's APIs match infra/main.tf, and nothing else is enabled (docs/foundations.md, C2).
#
# Terraform enables what the product uses but, with disable_on_destroy = false, never disables
# anything. So an API enabled by hand, by a console wizard or by an experiment stays enabled,
# unmonitored, until someone turns it off. This script is that someone.
#
# UNUSED names the APIs measured on 2026-09-22 as unused: no request in 30 days, no resource
# under them (no cluster, instance, zone, secret, pipeline, trigger, transfer or lake), and no
# reference in the repository. Each is disabled WITHOUT --force: if another enabled API depends
# on it, the API refuses and the script reports that, instead of disabling the dependent too.
# Re-enabling any of them is one `gcloud services enable` call.
#
# `--check` reports every enabled API that infra/main.tf does not declare, and exits 1 if any.
#
#   bash scripts/gcp/api-trim.sh            # disable UNUSED, then check
#   bash scripts/gcp/api-trim.sh --check
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-sage-ship-509104-b8}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNUSED=(
  autoscaling.googleapis.com
  bigquerydatatransfer.googleapis.com
  clouddeploy.googleapis.com
  container.googleapis.com
  containerfilesystem.googleapis.com
  containerregistry.googleapis.com
  dataplex.googleapis.com
  dns.googleapis.com
  eventarc.googleapis.com
  file.googleapis.com
  gkebackup.googleapis.com
  networkconnectivity.googleapis.com
  oslogin.googleapis.com
  secretmanager.googleapis.com
  sql-component.googleapis.com
)

enabled="$(gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)' | sort)"
declared="$(python3 -c "
import re,sys
text=open('${ROOT}/infra/main.tf').read()
block=text[text.index('resource \"google_project_service\" \"required\"'):]
block=block[:block.index('])')]
print('\n'.join(sorted(set(re.findall(r'\"([a-z0-9-]+\.googleapis\.com)\"', block)))))")"

if [[ "${1:-}" != "--check" ]]; then
  for api in "${UNUSED[@]}"; do
    if ! grep -qx "$api" <<<"$enabled"; then continue; fi
    if grep -qx "$api" <<<"$declared"; then
      echo "${api}: declared in infra/main.tf; not disabled" >&2
      continue
    fi
    if gcloud services disable "$api" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      echo "${api}: disabled"
    else
      echo "${api}: refused (another enabled API depends on it, or the call failed); left enabled"
    fi
  done
  enabled="$(gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)' | sort)"
fi

undeclared="$(comm -23 <(printf '%s\n' "$enabled") <(printf '%s\n' "$declared"))"
missing="$(comm -13 <(printf '%s\n' "$enabled") <(printf '%s\n' "$declared"))"
if [[ -n "$missing" ]]; then
  while read -r api; do echo "declared but not enabled (the next deploy enables it): ${api}"; done <<<"$missing"
fi
if [[ -n "$undeclared" ]]; then
  while read -r api; do echo "enabled but not declared in infra/main.tf: ${api}"; done <<<"$undeclared"
  echo "Drift found." >&2
  exit 1
fi
echo "No drift: every enabled API is declared."
