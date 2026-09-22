import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The pull-request plan's summary (docs/foundations.md, B4). The deploy applies unattended on
// merge, so this summary is what a reviewer sees before an infrastructure change reaches the
// record. The verdict is read from `terraform show -json`, because the human-readable text has
// several phrasings for a destroy and a missed one would pass it. These tests pin: every change
// named, every form of destroy caught, a crash failing closed, and no account ever copied.

const script = path.resolve("scripts/ci/plan-summary.py");
const dirs: string[] = [];

type Change = { address: string; actions: string[]; deposed?: string };

function summarise(
  changes: Change[] | null,
  text: string,
  code: number,
): { status: number | null; summary: string } {
  const dir = mkdtempSync(path.join(tmpdir(), "plan-summary-"));
  dirs.push(dir);
  const planText = path.join(dir, "plan.txt");
  const planJson = path.join(dir, "plan.json");
  const output = path.join(dir, "summary.md");
  writeFileSync(planText, text);
  if (changes !== null) {
    writeFileSync(
      planJson,
      JSON.stringify({
        resource_changes: changes.map(({ address, actions, deposed }) => ({
          address,
          ...(deposed === undefined ? {} : { deposed }),
          change: { actions },
        })),
      }),
    );
  }
  const run = spawnSync(
    "python3",
    [script, changes === null ? "-" : planJson, planText, output, String(code)],
    { encoding: "utf8" },
  );
  let summary = "";
  try {
    summary = readFileSync(output, "utf8");
  } catch {
    summary = "";
  }
  return { status: run.status, summary };
}

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("the pull-request plan summary", () => {
  it("names each changed resource, skips unchanged ones, and passes without a destroy", () => {
    const { status, summary } = summarise(
      [
        { address: "google_monitoring_dashboard.operations", actions: ["update"] },
        {
          address: 'google_project_iam_audit_config.x["discoveryengine.googleapis.com"]',
          actions: ["create"],
        },
        { address: "google_storage_bucket.evidence", actions: ["no-op"] },
        { address: "data.google_project.current", actions: ["read"] },
      ],
      "Plan: 1 to add, 1 to change, 0 to destroy.",
      2,
    );
    expect(status).toBe(0);
    expect(summary).toContain("**Plan: 1 to add, 1 to change, 0 to destroy.**");
    expect(summary).toContain("`google_monitoring_dashboard.operations` — updated in place");
    expect(summary).toContain(
      '`google_project_iam_audit_config.x["discoveryengine.googleapis.com"]` — created',
    );
    expect(summary).not.toContain("google_storage_bucket.evidence");
    expect(summary).not.toContain("data.google_project");
  });

  it.each([
    ["a destroy", { address: "google_bigquery_dataset.ledger", actions: ["delete"] }],
    ["a replace", { address: "google_storage_bucket_iam_member.w", actions: ["delete", "create"] }],
    [
      "a create-before-destroy replace",
      { address: "google_storage_bucket_iam_member.w", actions: ["create", "delete"] },
    ],
    [
      "a deposed object",
      { address: "google_storage_bucket.b", actions: ["delete"], deposed: "abc123" },
    ],
  ])("fails on %s", (_name, change) => {
    const { status, summary } = summarise(
      [change],
      "Plan: 0 to add, 0 to change, 1 to destroy.",
      2,
    );
    expect(status).toBe(4);
    expect(summary).toContain("**1 destroy or replace.**");
  });

  it("lists a resource removed from Terraform but left in place, without failing", () => {
    const { status, summary } = summarise(
      [{ address: "google_healthcare_dataset.epi", actions: ["forget"] }],
      "Plan: 0 to add, 0 to change, 0 to destroy, 1 to forget.",
      2,
    );
    expect(status).toBe(0);
    expect(summary).toContain("removed from Terraform, left in place");
  });

  it("never copies an account or address into the pull request", () => {
    const { summary } = summarise(
      [
        { address: 'google_x.a["user:someone@example.com"]', actions: ["create"] },
        { address: 'google_x.b["domain:example.com"]', actions: ["create"] },
        { address: 'google_x.c["group:admins"]', actions: ["create"] },
      ],
      "Plan: 3 to add, 0 to change, 0 to destroy.",
      2,
    );
    expect(summary).not.toMatch(/someone|example\.com|admins/);
    expect(summary.match(/sha256:[0-9a-f]{12}/g)).toHaveLength(3);
  });

  it("fails a failed plan, with its errors redacted", () => {
    const { status, summary } = summarise(
      null,
      "│ Error: googleapi: Error 403: Permission denied for user:someone@example.com",
      1,
    );
    expect(status).toBe(1);
    expect(summary).toContain("**The plan failed.**");
    expect(summary).toContain("Error 403");
    expect(summary).not.toContain("someone@example.com");
  });

  it("fails closed when the plan cannot be read", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "plan-summary-"));
    dirs.push(dir);
    writeFileSync(path.join(dir, "plan.txt"), "Plan: 0 to add, 0 to change, 0 to destroy.");
    writeFileSync(path.join(dir, "plan.json"), "{ not json");
    const run = spawnSync(
      "python3",
      [
        script,
        path.join(dir, "plan.json"),
        path.join(dir, "plan.txt"),
        path.join(dir, "s.md"),
        "2",
      ],
      { encoding: "utf8" },
    );
    expect(run.status).not.toBe(0);
    expect(run.status).not.toBe(4);
  });

  it("reports no changes", () => {
    const { status, summary } = summarise(
      [],
      "No changes. Your infrastructure matches the configuration.",
      0,
    );
    expect(status).toBe(0);
    expect(summary).toContain("No resource changes.");
  });
});
