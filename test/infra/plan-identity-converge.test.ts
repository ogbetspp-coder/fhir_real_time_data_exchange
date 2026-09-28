import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The planner's identity converges (audit B08, D-7). Until then plan-identity.sh only added the
// grants it wanted: a role granted by hand beside them stayed, and the script still reported done.
// Now an apply removes what is not wanted and ends by running its own --check. The script runs
// for real here against a stand-in gcloud that keeps the policies in files.

const dirs: string[] = [];
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

const PROJECT = "p-one";
const SA = `ema-flow-planner-dev@${PROJECT}.iam.gserviceaccount.com`;
const ROLE = `projects/${PROJECT}/roles/emaFlowPlanner`;
const POOL_MEMBER = `principalSet://iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/github-plan-pool/attribute.repository/ogbetspp-coder/fhir_real_time_data_exchange`;

// `removes` false makes every remove a no-op, as if it had not taken effect.
const GCLOUD = (state: string, removes: boolean) => `#!/usr/bin/env bash
echo "gcloud $*" >>"${state}/calls.log"
arg() { local name="$1"; shift; for a in "$@"; do case "$a" in --$name=*) printf '%s' "\${a#--$name=}"; return;; esac; done; }
# A binding added twice is one binding, as in IAM.
add() { grep -qxF "$1" "$2" || echo "$1" >>"$2"; }
case "$*" in
  "projects describe"*) echo 123 ;;
  "iam roles describe"*"includedPermissions"*) cat "${state}/role_perms" 2>/dev/null || true ;;
  "iam roles describe"*"deleted"*) ;;
  "iam roles create"*|"iam roles update"*) arg permissions "$@" >"${state}/role_perms" ;;
  "iam service-accounts describe"*) ;;
  "projects get-iam-policy"*) sort -u "${state}/project_roles" ;;
  "projects add-iam-policy-binding"*) add "$(arg role "$@")" "${state}/project_roles" ;;
  "projects remove-iam-policy-binding"*)
    ${removes ? "" : "exit 0"}
    role="$(arg role "$@")"; grep -vxF "$role" "${state}/project_roles" >"${state}/t" || true; mv "${state}/t" "${state}/project_roles" ;;
  "storage buckets get-iam-policy"*)
    python3 -c "import json,sys;print(json.dumps({'bindings':[{'role':r,'members':['serviceAccount:${SA}']} for r in open(sys.argv[1]).read().split()]}))" "${state}/bucket_roles" ;;
  "storage buckets add-iam-policy-binding"*) add "$(arg role "$@")" "${state}/bucket_roles" ;;
  "storage buckets remove-iam-policy-binding"*)
    ${removes ? "" : "exit 0"}
    role="$(arg role "$@")"; grep -vxF "$role" "${state}/bucket_roles" >"${state}/t" || true; mv "${state}/t" "${state}/bucket_roles" ;;
  "iam workload-identity-pools describe"*) ;;
  "iam workload-identity-pools providers describe"*)
    [ -f "${state}/condition" ] || exit 1
    python3 -c "import json,sys;print(json.dumps({'attributeCondition':open(sys.argv[1]).read(),'state':'ACTIVE','oidc':{'issuerUri':'https://token.actions.githubusercontent.com'},'attributeMapping':{'google.subject':'assertion.sub','attribute.repository':'assertion.repository'}}))" "${state}/condition" ;;
  "iam workload-identity-pools providers create-oidc"*|"iam workload-identity-pools providers update-oidc"*)
    arg attribute-condition "$@" >"${state}/condition" ;;
  "iam service-accounts get-iam-policy"*)
    python3 -c "
import json,sys
bindings={}
for line in open(sys.argv[1]).read().splitlines():
    if line.strip():
        role,member=line.split(' ',1); bindings.setdefault(role,[]).append(member)
print(json.dumps({'bindings':[{'role':r,'members':m} for r,m in bindings.items()]}))" "${state}/sa_grants" ;;
  "iam service-accounts add-iam-policy-binding"*) add "$(arg role "$@") $(arg member "$@")" "${state}/sa_grants" ;;
  "iam service-accounts remove-iam-policy-binding"*)
    ${removes ? "" : "exit 0"}
    grep -vxF "$(arg role "$@") $(arg member "$@")" "${state}/sa_grants" >"${state}/t" || true; mv "${state}/t" "${state}/sa_grants" ;;
  *) echo "unexpected gcloud $*" >&2; exit 2 ;;
esac
`;

function run(options: { removes?: boolean; check?: boolean } = {}) {
  const state = mkdtempSync(path.join(tmpdir(), "plan-identity-"));
  dirs.push(state);
  // Beside the wanted grants: a basic role on the project, an admin role on the state bucket, and
  // a person who may mint the planner's tokens.
  writeFileSync(path.join(state, "project_roles"), `${ROLE}\nroles/viewer\n`);
  writeFileSync(
    path.join(state, "bucket_roles"),
    "roles/storage.objectViewer\nroles/storage.objectAdmin\n",
  );
  writeFileSync(
    path.join(state, "sa_grants"),
    `roles/iam.workloadIdentityUser ${POOL_MEMBER}\nroles/iam.serviceAccountTokenCreator user:someone@company.eu\n`,
  );
  writeFileSync(path.join(state, "calls.log"), "");
  writeFileSync(path.join(state, "gcloud"), GCLOUD(state, options.removes ?? true));
  chmodSync(path.join(state, "gcloud"), 0o755);
  const result = spawnSync(
    "bash",
    ["scripts/gcp/plan-identity.sh", ...(options.check === true ? ["--check"] : [])],
    {
      encoding: "utf8",
      env: {
        PATH: `${state}:${process.env.PATH ?? ""}`,
        GOOGLE_CLOUD_PROJECT: PROJECT,
        EMA_FLOW_ENVIRONMENT: "dev",
      },
    },
  );
  return {
    status: result.status,
    out: `${result.stdout}${result.stderr}`,
    calls: readFileSync(path.join(state, "calls.log"), "utf8"),
    file: (name: string) => readFileSync(path.join(state, name), "utf8"),
  };
}

describe("the planner's identity", () => {
  it("is reported as drift, grants beside the wanted ones included, by --check", () => {
    const result = run({ check: true });
    expect(result.status).toBe(1);
    expect(result.out).toContain("project roles of");
    expect(result.out).toContain("state bucket roles of");
    expect(result.out).toContain("who may become");
    expect(result.calls).not.toMatch(/(add|remove)-iam-policy-binding/);
  });

  it("converges: extra grants are removed, and the apply ends with a clean check", () => {
    const result = run();
    expect([result.status, result.out]).toEqual([0, expect.stringContaining("No drift.")]);
    expect(result.file("project_roles").split("\n").filter(Boolean)).toEqual([ROLE]);
    expect(result.file("bucket_roles").split("\n").filter(Boolean)).toEqual([
      "roles/storage.objectViewer",
    ]);
    expect(result.file("sa_grants").split("\n").filter(Boolean)).toEqual([
      `roles/iam.workloadIdentityUser ${POOL_MEMBER}`,
    ]);
    expect(result.out).toContain("Checking what was applied.");
  });

  it("fails when what it applied did not take, instead of reporting done", () => {
    const result = run({ removes: false });
    expect(result.status).toBe(1);
    expect(result.out).toContain("Drift found.");
  });
});
