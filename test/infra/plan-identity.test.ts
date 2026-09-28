import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The pull-request planner (docs/foundations.md, B4). A pull request's workflow runs with the
// planner's credentials, so the planner must be unable to reach the deployer and unable to read
// the record. These tests pin both, from the script that is the source of truth.

const script = readFileSync("scripts/gcp/plan-identity.sh", "utf8");
const deployScript = readFileSync("scripts/gcp/deploy-identity.sh", "utf8");
const value = (text: string, name: string): string =>
  new RegExp(`^${name}="(.+)"$`, "m").exec(text)?.[1] ?? "";
const permissions = [
  ...(/^PERMISSIONS=\(\n([\s\S]*?)\n\)$/m.exec(script)?.[1] ?? "").matchAll(/^\s+(\S+)$/gm),
].map((match) => match[1] ?? "");

describe("the pull-request planner", () => {
  it("has its own pool, never the deployer's", () => {
    // The deployer's impersonation grant covers every identity in its pool that names this
    // repository; a pull-request provider in that pool would admit pull requests to the deployer.
    expect(value(deployScript, "POOL")).toBe("github-pool");
    expect(value(script, "POOL")).not.toBe(value(deployScript, "POOL"));
  });

  it("admits only this repository's pull_request runs of the plan workflow", () => {
    const condition = value(script, "CONDITION");
    expect(condition).toContain("assertion.repository_id=='${REPOSITORY_ID}'");
    expect(condition).toContain("assertion.event_name=='pull_request'");
    expect(condition).toContain(
      "assertion.workflow_ref.startsWith('${REPOSITORY}/.github/workflows/plan.yml@refs/pull/')",
    );
  });

  it("reads metadata and policy only", () => {
    expect(permissions.length).toBeGreaterThan(20);
    for (const permission of permissions) {
      expect([permission, /\.(get|list|getIamPolicy)$/.test(permission)]).toEqual([
        permission,
        true,
      ]);
    }
  });

  it("holds no permission that returns a stored record", () => {
    const forbidden = [
      /^storage\.objects\./, // evidence, submissions; state is read through one bucket grant
      /^bigquery\.tables\.getData$/, // the ledger and the analytical projection
      // The FHIR stores' content. A store's IAM policy is the one store permission a plan needs
      // (the services' grants are bound on each store); it says who may read, never what is there.
      /^healthcare\.fhirResources\./,
      /^healthcare\.fhirStores\.(?!getIamPolicy$)/,
      /^logging\.(logEntries|privateLogEntries)\./, // log content, including audit logs
      /^cloudkms\.cryptoKeyVersions\.use/, // decrypt or sign
      /^secretmanager\.versions\.access$/,
    ];
    for (const permission of permissions) {
      expect([permission, forbidden.some((rule) => rule.test(permission))]).toEqual([
        permission,
        false,
      ]);
    }
  });

  it("can read the policy of every kind of IAM grant infra/ declares", () => {
    // A plan refreshes each grant by reading the policy it is part of; a kind the planner cannot
    // read fails every plan after the first apply that creates one.
    const infra =
      readFileSync("infra/security.tf", "utf8") + readFileSync("infra/query.tf", "utf8");
    const kinds: [RegExp, string][] = [
      [/resource "google_healthcare_fhir_store_iam_member"/, "healthcare.fhirStores.getIamPolicy"],
      [/resource "google_bigquery_table_iam_member"/, "bigquery.tables.getIamPolicy"],
      [/resource "google_project_iam_custom_role"/, "iam.roles.get"],
    ];
    for (const [kind, permission] of kinds) {
      expect(kind.test(infra)).toBe(true);
      expect(permissions).toContain(permission);
    }
  });

  it("can read the Logging settings the audit-logs key's grant is taken from", () => {
    // infra/keys.tf reads them on every plan (audit I-10); without this every plan fails.
    expect(readFileSync("infra/keys.tf", "utf8")).toContain(
      'data "google_logging_project_settings" "current"',
    );
    expect(permissions).toContain("logging.settings.get");
  });

  it("reads the state through the state bucket alone", () => {
    expect(script).toContain('--member="serviceAccount:${SA}" --role=roles/storage.objectViewer');
    expect(script).toContain("gs://${STATE_BUCKET}");
  });
});

describe("the plan workflow", () => {
  const workflow = readFileSync(".github/workflows/plan.yml", "utf8");

  it("runs on pull requests as the planner, never the deployer", () => {
    expect(workflow).toMatch(/^on:\n {2}pull_request:/m);
    expect(workflow).toContain("vars.GCP_PLAN_WORKLOAD_IDENTITY_PROVIDER");
    expect(workflow).toContain("vars.GCP_PLAN_SERVICE_ACCOUNT");
    expect(workflow).not.toContain("vars.GCP_WORKLOAD_IDENTITY_PROVIDER");
  });

  it("fails on a destroy unless the pull request is labelled allow-replace", () => {
    expect(workflow).toContain(
      "ALLOW_REPLACE: ${{ contains(github.event.pull_request.labels.*.name, 'allow-replace') }}",
    );
    const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
    // The verdict fails closed: only 0 and 4 are verdicts, anything else fails the check.
    expect(deploy).toMatch(
      /if \[\[ "\$\{ALLOW_REPLACE:-false\}" == "true" \]\]; then return 0; fi/,
    );
    expect(deploy).toMatch(/case "\$verdict" in[\s\S]*?\*\) return 1 ;;/);
  });

  it("plans with exactly the inputs the deploy applies with", () => {
    const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
    // The deploy plans with them and applies exactly that plan (plan_reviewed; audit B08, D-2).
    const applyUses =
      /plan_reviewed apply "\$\{TF_DEPLOY_VARS\[@\]\}"\n\s+if ! terraform -chdir=infra apply -input=false "\$REVIEWED_PLAN"; then/;
    const planUses =
      /terraform -chdir=infra plan [^\n]*\\\n\s+-out="\$plan_file" "\$\{TF_DEPLOY_VARS\[@\]\}"/;
    expect(deploy).toMatch(applyUses);
    expect(deploy).toMatch(planUses);
  });
});
