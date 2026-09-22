#!/usr/bin/env bash
# The identity that plans pull requests against live state (docs/foundations.md, B4).
#
# Every infrastructure change is applied unattended by the deploy on merge, so the plan must be
# seen before the merge. Until 2026-09-22 that plan was run by hand on a laptop. This identity
# lets a pull request's own workflow run it and post the result, and it can do nothing else.
#
# It is deliberately NOT in the deployer's pool. The deployer's impersonation grant is to every
# identity in `github-pool` whose repository attribute names this repository — the provider's
# condition is what narrows that to the deploy workflow on main. A second provider in that pool,
# admitting pull requests, would therefore admit pull requests to the deployer. So the planner
# has its own pool, `github-plan-pool`, and its own provider, whose condition admits only:
#   - this repository, by numeric id;
#   - the `pull_request` event;
#   - the plan workflow file, as run for a pull request.
#
# The planner holds one custom role, `emaFlowPlanner`: the get/list and getIamPolicy
# permissions that refreshing this repository's Terraform state needs, measured by running a
# real plan as the planner, and no permission that reads a stored record — no object reads
# except the Terraform state, no table data, no FHIR resources, no log entries. It reads the
# state through objectViewer on the state bucket alone. It cannot take the state lock, so plans
# run with -lock=false, which is safe because a plan writes nothing.
#
# Idempotent; `--check` reports drift without changing anything.
#
#   bash scripts/gcp/plan-identity.sh
#   bash scripts/gcp/plan-identity.sh --check
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-sage-ship-509104-b8}"
ENVIRONMENT="${EMA_FLOW_ENVIRONMENT:-dev}"
REPOSITORY="ogbetspp-coder/fhir_real_time_data_exchange"
REPOSITORY_ID="1376667427"
POOL="github-plan-pool"
PROVIDER="github-plan-provider"
SA_ID="ema-flow-planner-${ENVIRONMENT}"
SA="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
ROLE_ID="emaFlowPlanner"
STATE_BUCKET="${PROJECT_ID}-ema-flow-tfstate"
CONDITION="assertion.repository_id=='${REPOSITORY_ID}' && assertion.event_name=='pull_request' && assertion.workflow_ref.startsWith('${REPOSITORY}/.github/workflows/plan.yml@refs/pull/')"

# Read-only, metadata and policy only. Each line is a resource type in infra/ or a call the plan
# workflow makes; nothing here returns a stored record's content.
PERMISSIONS=(
  resourcemanager.projects.get
  resourcemanager.projects.getIamPolicy
  serviceusage.services.list
  iam.serviceAccounts.get
  iam.serviceAccounts.getIamPolicy
  artifactregistry.repositories.get
  artifactregistry.repositories.getIamPolicy
  bigquery.datasets.get
  bigquery.datasets.getIamPolicy
  bigquery.tables.get
  run.services.get
  run.services.getIamPolicy
  documentai.processors.get
  healthcare.datasets.get
  healthcare.datasets.getIamPolicy
  cloudkms.keyRings.get
  cloudkms.cryptoKeys.get
  cloudkms.cryptoKeys.getIamPolicy
  logging.buckets.get
  logging.logMetrics.get
  logging.sinks.get
  monitoring.alertPolicies.get
  monitoring.dashboards.get
  monitoring.notificationChannels.get
  pubsub.topics.get
  pubsub.topics.getIamPolicy
  storage.buckets.get
  storage.buckets.getIamPolicy
  workflows.workflows.get
)

CHECK="false"
[[ "${1:-}" == "--check" ]] && CHECK="true"
drift=0
note() { drift=1; echo "$*"; }
apply() { [[ "$CHECK" == "false" ]]; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

# 1. The custom role.
want_perms="$(printf '%s\n' "${PERMISSIONS[@]}" | sort | paste -sd, -)"
have_perms="$(gcloud iam roles describe "$ROLE_ID" --project="$PROJECT_ID" \
  --format='value(includedPermissions)' 2>/dev/null | tr ';' '\n' | tr ',' '\n' | sed '/^$/d' | sort | paste -sd, - || true)"
if [[ "$have_perms" != "$want_perms" ]]; then
  note "role ${ROLE_ID}: permissions differ"
  if apply; then
    if [[ -z "$have_perms" ]]; then
      gcloud iam roles create "$ROLE_ID" --project="$PROJECT_ID" --quiet \
        --title="EMA Flow planner" --stage=GA \
        --description="Read-only metadata and IAM policy reads for terraform plan; no record content." \
        --permissions="$want_perms" >/dev/null
    else
      gcloud iam roles update "$ROLE_ID" --project="$PROJECT_ID" --quiet \
        --permissions="$want_perms" >/dev/null
    fi
    echo "role ${ROLE_ID}: set"
  fi
fi

# 2. The service account and its two grants.
if ! gcloud iam service-accounts describe "$SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  note "service account ${SA}: missing"
  if apply; then
    gcloud iam service-accounts create "$SA_ID" --project="$PROJECT_ID" --quiet \
      --display-name="EMA Flow planner (${ENVIRONMENT})" \
      --description="Plans pull requests against live state. Read-only; see scripts/gcp/plan-identity.sh." >/dev/null
    echo "service account ${SA}: created"
  fi
fi
project_roles="$(gcloud projects get-iam-policy "$PROJECT_ID" --flatten=bindings \
  --filter="bindings.members:serviceAccount:${SA}" --format='value(bindings.role)' 2>/dev/null | sort | paste -sd, -)"
if [[ "$project_roles" != "projects/${PROJECT_ID}/roles/${ROLE_ID}" ]]; then
  note "project roles of ${SA}: '${project_roles}'"
  if apply; then
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --quiet --condition=None \
      --member="serviceAccount:${SA}" --role="projects/${PROJECT_ID}/roles/${ROLE_ID}" >/dev/null
    echo "project role granted"
  fi
fi
state_roles="$(gcloud storage buckets get-iam-policy "gs://${STATE_BUCKET}" --format=json |
  python3 -c "import sys,json;print(','.join(sorted(b['role'] for b in json.load(sys.stdin).get('bindings',[]) if 'serviceAccount:${SA}' in b['members'])))")"
if [[ "$state_roles" != "roles/storage.objectViewer" ]]; then
  note "state bucket roles of ${SA}: '${state_roles}'"
  if apply; then
    gcloud storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" --quiet \
      --member="serviceAccount:${SA}" --role=roles/storage.objectViewer >/dev/null
    echo "state bucket objectViewer granted"
  fi
fi

# 3. The pool and provider, separate from the deployer's.
if ! gcloud iam workload-identity-pools describe "$POOL" --location=global --project="$PROJECT_ID" >/dev/null 2>&1; then
  note "pool ${POOL}: missing"
  if apply; then
    gcloud iam workload-identity-pools create "$POOL" --location=global --project="$PROJECT_ID" --quiet \
      --display-name="GitHub pull-request plans" >/dev/null
    echo "pool ${POOL}: created"
  fi
fi
current="$(gcloud iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool="$POOL" \
  --location=global --project="$PROJECT_ID" --format='value(attributeCondition)' 2>/dev/null || true)"
if [[ "$current" != "$CONDITION" ]]; then
  note "provider ${PROVIDER}: condition is '${current:-missing}'"
  if apply; then
    verb="update-oidc"; [[ -z "$current" ]] && verb="create-oidc"
    extra=()
    [[ "$verb" == "create-oidc" ]] && extra=(--issuer-uri="https://token.actions.githubusercontent.com")
    gcloud iam workload-identity-pools providers "$verb" "$PROVIDER" --workload-identity-pool="$POOL" \
      --location=global --project="$PROJECT_ID" --quiet "${extra[@]}" \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
      --attribute-condition="$CONDITION" >/dev/null
    echo "provider ${PROVIDER}: condition set"
  fi
fi

# 4. Only identities from the plan pool, for this repository, may become the planner.
member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPOSITORY}"
sa_members="$(gcloud iam service-accounts get-iam-policy "$SA" --project="$PROJECT_ID" --format=json 2>/dev/null |
  python3 -c "import sys,json;print(','.join(sorted(b['role']+' '+m for b in json.load(sys.stdin).get('bindings',[]) for m in b['members'])))" || true)"
if [[ "$sa_members" != "roles/iam.workloadIdentityUser ${member}" ]]; then
  note "who may become ${SA}: '${sa_members}'"
  if apply; then
    gcloud iam service-accounts add-iam-policy-binding "$SA" --project="$PROJECT_ID" --quiet \
      --role=roles/iam.workloadIdentityUser --member="$member" >/dev/null
    echo "workloadIdentityUser granted to the plan pool"
  fi
fi

if [[ "$CHECK" == "true" ]]; then
  if [[ "$drift" == "0" ]]; then echo "No drift."; else echo "Drift found." >&2; exit 1; fi
else
  echo "provider: projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
  echo "service account: ${SA}"
fi
