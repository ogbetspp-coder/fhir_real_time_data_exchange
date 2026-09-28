import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

// The deploy's own checks around the apply (audit B04, review round 1). An apply that lacks a
// permission does not stop cleanly: Terraform refuses the one create while carrying out every
// independent change, so a grant could be removed before its replacement exists. The deploy
// therefore asks which permissions it holds before applying. And the dashboard, whose text
// Terraform ignores, is compared after the apply, with a failure to compare treated as an error,
// never as drift. These run the real functions from deploy.sh against stand-in gcloud, curl and
// terraform commands.

const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function extract(name: string): string {
  const body = new RegExp(`^${name}\\(\\) \\{\\n[\\s\\S]*?^\\}$`, "m").exec(deploy)?.[0];
  if (body === undefined) throw new Error(`${name} not found in deploy.sh`);
  return body;
}

const permissionsArray = /^APPLY_PERMISSIONS=\(\n[\s\S]*?^\)$/m.exec(deploy)?.[0] ?? "";
const permissions = [...permissionsArray.matchAll(/^\s+([a-z][\w.]+)/gm)].map((m) => m[1] ?? "");

// Runs `script` in bash with stand-in commands on PATH; each stand-in is a small shell script.
function run(
  script: string,
  stubs: Record<string, string>,
): { status: number | null; out: string; calls: string } {
  const dir = mkdtempSync(path.join(tmpdir(), "deploy-preflight-"));
  dirs.push(dir);
  const calls = path.join(dir, "calls.log");
  writeFileSync(calls, "");
  for (const [name, body] of Object.entries(stubs)) {
    const file = path.join(dir, name);
    writeFileSync(file, `#!/usr/bin/env bash\necho "${name} $*" >>"${calls}"\n${body}\n`);
    chmodSync(file, 0o755);
  }
  const result = spawnSync("bash", ["-c", `set -euo pipefail\n${script}`], {
    encoding: "utf8",
    env: { PATH: `${dir}:${process.env.PATH ?? ""}`, PROJECT_ID: "test-project" },
  });
  return {
    status: result.status,
    out: `${result.stdout}${result.stderr}`,
    calls: readFileSync(calls, "utf8"),
  };
}

describe("the deploy's permission preflight", () => {
  // Each answer is [HTTP status, body]; the stand-in curl gives them in turn, the last one again
  // once they run out, writing the body where --output says and printing the status.
  const preflight = (...answers: [string, string][]) =>
    run(
      `${permissionsArray}
PREFLIGHT_RETRY_SECONDS=0
source scripts/gcp/common.sh
ema_flow_access_token() { printf token; }
${extract("apply_permission_role")}
${extract("preflight_apply_permissions")}
preflight_apply_permissions`,
      {
        curl: `here="$(dirname "$0")"
n=$(( $(cat "$here/n" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$here/n"
out=""; while [ $# -gt 0 ]; do [ "$1" = "--output" ] && out="$2"; shift; done
case $n in
${answers.map(([code, body], i) => `  ${String(i + 1)}) printf '%s' '${body}' >"$out"; printf '%s' '${code}' ;;`).join("\n")}
  *) printf '%s' '${answers.at(-1)?.[1] ?? ""}' >"$out"; printf '%s' '${answers.at(-1)?.[0] ?? "000"}' ;;
esac`,
        gcloud: "echo ema-flow-deployer@test-project.iam.gserviceaccount.com",
      },
    );
  const held = (list: string[]): [string, string] => ["200", JSON.stringify({ permissions: list })];

  it("names a permission for every kind of resource this batch added", () => {
    const infra = readInfra();
    const kinds: [string, string[]][] = [
      ["google_project_iam_custom_role", ["iam.roles.create", "iam.roles.update"]],
      ["google_bigquery_table_iam_member", ["bigquery.tables.setIamPolicy"]],
      ["google_healthcare_fhir_store_iam_member", ["healthcare.fhirStores.setIamPolicy"]],
      ["google_project_iam_audit_config", ["resourcemanager.projects.setIamPolicy"]],
    ];
    for (const [kind, needed] of kinds) {
      expect(terraformBlocks(infra).some(({ type }) => type === kind)).toBe(true);
      for (const permission of needed) expect(permissions).toContain(permission);
    }
  });

  it("passes when every permission is held", () => {
    const result = preflight(held(permissions));
    expect(result.status).toBe(0);
    expect(result.out).toContain(`all ${String(permissions.length)} the apply needs are held`);
    expect(result.calls).toContain(
      "https://cloudresourcemanager.googleapis.com/v1/projects/test-project:testIamPermissions",
    );
  });

  it("fails before the apply, naming what is missing and the command that grants it", () => {
    const result = preflight(held(permissions.filter((p) => !p.startsWith("iam.roles."))));
    expect(result.status).not.toBe(0);
    expect(result.out).toContain(
      "lacks iam.roles.create iam.roles.delete iam.roles.get iam.roles.update",
    );
    const commands = result.out.split("\n").filter((line) => line.includes("gcloud projects"));
    expect(commands).toEqual([
      "  gcloud projects add-iam-policy-binding test-project --member=serviceAccount:ema-flow-deployer@test-project.iam.gserviceaccount.com --role=roles/iam.roleAdmin --condition=None",
    ]);
  });

  it("names one role per kind of missing permission", () => {
    const result = preflight(held([]));
    const roles = [...result.out.matchAll(/--role=(\S+)/g)].map((match) => match[1]);
    expect(roles).toEqual([
      "roles/bigquery.dataOwner",
      "roles/healthcare.fhirStoreAdmin",
      "roles/iam.roleAdmin",
      "roles/resourcemanager.projectIamAdmin",
    ]);
  });

  it("asks again after no answer, a 429 or a 5xx, three times in all", () => {
    const recovered = preflight(["503", ""], ["429", ""], held(permissions));
    expect(recovered.status).toBe(0);
    expect(recovered.calls.match(/^curl /gm)).toHaveLength(3);
    const exhausted = preflight(["000", ""]);
    expect(exhausted.status).not.toBe(0);
    expect(exhausted.calls.match(/^curl /gm)).toHaveLength(3);
    expect(exhausted.out).toContain("answered HTTP 000");
  });

  it("fails at once on a refusal, and on an answer it cannot read", () => {
    const refused = preflight(["403", '{"error":{}}']);
    expect(refused.status).not.toBe(0);
    expect(refused.calls.match(/^curl /gm)).toHaveLength(1);
    expect(preflight(["200", "<html>"]).status).not.toBe(0);
  });

  it("runs in phase_apply before the stores are touched and before the apply", () => {
    const apply = extract("phase_apply");
    const at = (text: string) => apply.indexOf(text);
    expect(at("preflight_apply_permissions")).toBeGreaterThan(-1);
    expect(at("preflight_apply_permissions")).toBeLessThan(at("ensure_fhir_stores"));
    expect(at("ensure_fhir_stores")).toBeLessThan(at("terraform -chdir=infra apply"));
  });
});

// A timeout of its own: each case starts bash and its stubs, which took over vitest's 5 s default
// in a loaded full run (review of audit B15).
describe("the deploy's dashboard sync", { timeout: 30_000 }, () => {
  const sync = (drift: string, gcloud = "echo '{}'") =>
    run(
      `TF_DEPLOY_VARS=(-var=x=1)
${extract("sync_dashboard")}
sync_dashboard`,
      {
        terraform: `case "$*" in *operations_dashboard_id*) echo projects/1/dashboards/d ;; *operations_dashboard_json*) echo '{}' ;; esac`,
        gcloud,
        python3: drift,
      },
    );

  it("does nothing when the dashboard carries its configuration", () => {
    const result = sync("exit 0");
    expect(result.status).toBe(0);
    expect(result.calls).not.toContain("-replace");
  });

  it("replaces the dashboard on drift", () => {
    const result = sync("exit 1");
    expect(result.status).toBe(0);
    expect(result.calls).toContain("-replace=google_monitoring_dashboard.operations");
  });

  it("fails, and replaces nothing, when the checker errs (exit 2)", () => {
    const result = sync("exit 2");
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("could not compare");
    expect(result.calls).not.toContain("-replace");
  });

  it("fails when the live dashboard cannot be read", () => {
    const result = sync("exit 0", "exit 1");
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("Could not read the operations dashboard");
  });
});
