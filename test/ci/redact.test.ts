import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// What a failed deploy publishes (audit B08, D-6). Its log's tail became a GitHub issue verbatim,
// and a plan or apply prints IAM members in resource addresses (query_invoker["user:…"]), the
// alert address and the entitlement map: the values deploy.sh itself logs only as sizes. The
// issue now carries the tail redacted by scripts/ci/redact.py, and the variables are sensitive.

const dirs: string[] = [];
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function redact(text: string, keep?: number) {
  const dir = mkdtempSync(path.join(tmpdir(), "redact-"));
  dirs.push(dir);
  const file = path.join(dir, "deploy-run.log");
  writeFileSync(file, text);
  return spawnSync(
    "python3",
    ["scripts/ci/redact.py", file, ...(keep === undefined ? [] : [String(keep)])],
    { encoding: "utf8" },
  );
}

const LOG = [
  '  # google_cloud_run_v2_service_iam_member.query_invoker["user:alice@company.eu"] will be created',
  '      + member = "serviceAccount:ema-flow-worker-dev@p-one.iam.gserviceaccount.com"',
  "      + email_address = oncall@company.eu",
  "Authorization: Bearer ya29.a0AfH6SMBx-secret_token.value",
  "id_token=eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl",
  "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.",
].join("\n");

describe("the deploy's failure log, as published", () => {
  it("names no member, address or token, and keeps the rest", () => {
    const run = redact(LOG);
    expect(run.status).toBe(0);
    for (const secret of [
      "alice@company.eu",
      "ema-flow-worker-dev@p-one",
      "oncall@company.eu",
      "ya29.",
      "eyJhbGci",
    ]) {
      expect([secret, run.stdout.includes(secret)]).toEqual([secret, false]);
    }
    expect(run.stdout).toContain('google_cloud_run_v2_service_iam_member.query_invoker["sha256:');
    expect(run.stdout).toContain("Apply complete! Resources: 1 added");
    expect(run.stdout.match(/\[token redacted\]/g)).toHaveLength(2);
  });

  it("hashes one member the same way everywhere, so two can be told apart", () => {
    const run = redact("user:a@x.eu user:a@x.eu user:b@x.eu");
    const hashes = run.stdout.split(" ");
    expect(hashes[0]).toBe(hashes[1]);
    expect(hashes[0]).not.toBe(hashes[2]);
  });

  it("cuts the tail after redacting, so no cut leaves part of a value", () => {
    const run = redact(`${"x".repeat(100)} user:someone@company.eu`, 30);
    expect(run.stdout).toHaveLength(30);
    expect(run.stdout).not.toContain("company");
  });

  it("fails, printing nothing, when there is no log", () => {
    const run = spawnSync("python3", ["scripts/ci/redact.py", "/nonexistent/deploy-run.log"], {
      encoding: "utf8",
    });
    expect([run.status, run.stdout]).toEqual([1, ""]);
  });

  it("is what the workflow publishes, and nothing unredacted", () => {
    const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");
    const step = workflow.slice(workflow.indexOf("- name: Publish failure log"));
    expect(step).toContain('tail="$(python3 scripts/ci/redact.py deploy-run.log 50000)"');
    expect(step).not.toMatch(/read_text|cat deploy-run\.log|tail -c/);
  });

  it("is the same redaction the pull request's plan summary uses", () => {
    expect(readFileSync("scripts/ci/plan-summary.py", "utf8")).toContain(
      "from redact import redact",
    );
  });

  it("goes with sensitive variables, so a plan prints the address and the map as (sensitive)", () => {
    const variables = readFileSync("infra/variables.tf", "utf8");
    for (const name of ["alert_notification_email", "query_entitlements_json"]) {
      const body = new RegExp(`^variable "${name}" \\{\\n([\\s\\S]*?)^\\}`, "m").exec(
        variables,
      )?.[1];
      expect([name, body]).toEqual([name, expect.stringMatching(/^\s+sensitive\s+=\s+true$/m)]);
    }
    // The member lists key for_each, which Terraform refuses to key by a sensitive value; they
    // stay plain, and the redaction above is what keeps them out of the issue.
    for (const name of ["query_invokers", "query_token_creators"]) {
      const body = new RegExp(`^variable "${name}" \\{\\n([\\s\\S]*?)^\\}`, "m").exec(
        variables,
      )?.[1];
      expect([name, body]).toEqual([name, expect.not.stringMatching(/sensitive\s+=\s+true/)]);
    }
  });
});
