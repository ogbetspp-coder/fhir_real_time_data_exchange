import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The pull-request plan's summary (docs/foundations.md, B4). The deploy applies unattended on
// merge, so this summary is what a reviewer sees before an infrastructure change reaches the
// record. These tests pin the three things it must do: name every resource that changes, fail
// on any destroy or replace, and never copy an account or address into the pull request.

const script = path.resolve("scripts/ci/plan-summary.py");
const dirs: string[] = [];

function summarise(plan: string, code: number): { status: number | null; summary: string } {
  const dir = mkdtempSync(path.join(tmpdir(), "plan-summary-"));
  dirs.push(dir);
  const input = path.join(dir, "plan.txt");
  const output = path.join(dir, "summary.md");
  writeFileSync(input, plan);
  const run = spawnSync("python3", [script, input, output, String(code)], { encoding: "utf8" });
  return { status: run.status, summary: readFileSync(output, "utf8") };
}

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("the pull-request plan summary", () => {
  it("names each changed resource and passes an in-place change", () => {
    const { status, summary } = summarise(
      [
        "  # google_monitoring_dashboard.operations will be updated in-place",
        '  # google_project_iam_audit_config.regulated_data_access["discoveryengine.googleapis.com"] will be created',
        "Plan: 1 to add, 1 to change, 0 to destroy.",
      ].join("\n"),
      2,
    );
    expect(status).toBe(0);
    expect(summary).toContain("**Plan: 1 to add, 1 to change, 0 to destroy.**");
    expect(summary).toContain(
      "- `google_monitoring_dashboard.operations will be updated in-place`",
    );
    expect(summary).toContain(
      '- `google_project_iam_audit_config.regulated_data_access["discoveryengine.googleapis.com"] will be created`',
    );
  });

  it("fails on a replace, and says so", () => {
    const { status, summary } = summarise(
      [
        "  # google_storage_bucket_iam_member.worker_evidence_writer must be replaced",
        "Plan: 1 to add, 0 to change, 1 to destroy.",
      ].join("\n"),
      2,
    );
    expect(status).toBe(4);
    expect(summary).toContain("**1 destroy or replace.**");
  });

  it("fails on a destroy", () => {
    const { status } = summarise(
      "  # google_bigquery_dataset.ledger will be destroyed\nPlan: 0 to add, 0 to change, 1 to destroy.",
      2,
    );
    expect(status).toBe(4);
  });

  it("never copies an account into the pull request", () => {
    const { summary } = summarise(
      '  # google_cloud_run_v2_service_iam_member.query_invoker["user:someone@example.com"] will be created\nPlan: 1 to add, 0 to change, 0 to destroy.',
      2,
    );
    expect(summary).not.toContain("someone@example.com");
    expect(summary).toMatch(/query_invoker\["sha256:[0-9a-f]{12}"\]/);
  });

  it("reports a failed plan with its errors", () => {
    const { status, summary } = summarise(
      "│ Error: Error when reading or editing Project Service: googleapi: Error 403: Permission denied",
      1,
    );
    expect(status).toBe(0);
    expect(summary).toContain("**The plan failed.**");
    expect(summary).toContain("Error 403");
  });

  it("reports no changes", () => {
    const { status, summary } = summarise(
      "No changes. Your infrastructure matches the configuration.",
      0,
    );
    expect(status).toBe(0);
    expect(summary).toContain("No resource changes.");
  });
});
