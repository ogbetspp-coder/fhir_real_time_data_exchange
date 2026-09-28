import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The deploy applies only a plan it has read (audit B08, D-2). Until then phase_apis and
// phase_apply ran `terraform apply -auto-approve`, and a destroy was stopped only on the pull
// request's plan, which a push to main by an administrator skips and a stale plan misdescribes.
// These run plan_reviewed from deploy.sh, with the real scripts/ci/plan-summary.py, against a
// stand-in terraform whose plan is whatever a case gives it.

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

const COMMIT = "0123456789abcdef0123456789abcdef01234567";
// common.sh's ema_flow_destroy_digest: the first 16 hex of SHA-256 over the sorted, unique lines.
const digest = (...addresses: string[]) =>
  createHash("sha256")
    .update(
      [...new Set(addresses)]
        .sort()
        .map((a) => `${a}\n`)
        .join(""),
    )
    .digest("hex")
    .slice(0, 16);
const change = (address: string, ...actions: string[]) => ({ address, change: { actions } });

// Runs plan_reviewed, then applies REVIEWED_PLAN as the phases do, and reports what terraform
// was asked to do.
function review(
  plan: { exit: number; changes?: object[]; showFails?: boolean },
  env: Record<string, string> = {},
) {
  const dir = mkdtempSync(path.join(tmpdir(), "apply-gate-"));
  dirs.push(dir);
  const calls = path.join(dir, "calls.log");
  writeFileSync(calls, "");
  writeFileSync(
    path.join(dir, "plan.json"),
    JSON.stringify({ resource_changes: plan.changes ?? [] }),
  );
  writeFileSync(
    path.join(dir, "terraform"),
    `#!/usr/bin/env bash
echo "terraform $*" >>"${calls}"
case "$2" in
  plan)
    for a in "$@"; do case "$a" in -out=*) echo planned >"\${a#-out=}" ;; esac; done
    echo "Plan: 1 to add, 0 to change, 0 to destroy."
    exit ${String(plan.exit)} ;;
  show) ${plan.showFails === true ? "exit 1" : `cat "${dir}/plan.json"`} ;;
  apply) [ "$(cat "$4")" = planned ] || { echo "applied something that is not the reviewed plan" >&2; exit 9; } ;;
esac
`,
  );
  chmodSync(path.join(dir, "terraform"), 0o755);
  const run = spawnSync(
    "bash",
    [
      "-c",
      `set -euo pipefail
source scripts/gcp/common.sh
DEPLOY_TEMP_FILES=()
deploy_provenance() { PROVENANCE_ERROR=""; SERVICE_VERSION="${COMMIT}"; }
${extract("plan_reviewed")}
plan_reviewed apply -var=x=1
terraform -chdir=infra apply -input=false "$REVIEWED_PLAN"
echo "applied"`,
    ],
    { encoding: "utf8", env: { PATH: `${dir}:${process.env.PATH ?? ""}`, ...env } },
  );
  const log = readFileSync(calls, "utf8");
  return {
    status: run.status,
    out: `${run.stdout}${run.stderr}`,
    applied: log.split("\n").filter((line) => line.includes(" apply ")),
    log,
  };
}

describe("the deploy's applies", { timeout: 30_000 }, () => {
  it("apply exactly the plan they reviewed when it destroys nothing", () => {
    const result = review({ exit: 2, changes: [change("google_x.a", "create")] });
    expect(result.status).toBe(0);
    expect(result.out).toContain("applied");
    expect(result.log).toMatch(
      /terraform -chdir=infra plan -input=false -no-color -detailed-exitcode -out=\S+ -var=x=1/,
    );
    expect(result.applied).toHaveLength(1);
    expect(result.applied[0]).not.toContain("-auto-approve");
    expect(result.applied[0]).not.toContain("-var=");
  });

  it.each([
    ["a destroy", [change("google_x.a", "delete")]],
    ["a replace", [change("google_x.a", "delete", "create")]],
  ])("refuse %s that nobody acknowledged, and apply nothing", (_label, changes) => {
    const result = review({ exit: 2, changes });
    expect(result.status).toBe(3);
    expect(result.applied).toEqual([]);
    // The refusal prints the exact acknowledgement: this commit and a digest of what it destroys.
    expect(result.out).toContain(`ALLOW_REPLACE_ACK=${COMMIT}:${digest("google_x.a")}`);
  });

  it("refuse a destroy acknowledged for another commit", () => {
    const result = review(
      { exit: 2, changes: [change("google_x.a", "delete")] },
      { ALLOW_REPLACE_ACK: `${"f".repeat(40)}:${digest("google_x.a")}` },
    );
    expect(result.status).toBe(3);
    expect(result.applied).toEqual([]);
  });

  it("refuse a destroy of anything but what was acknowledged, as an old run re-run would plan", () => {
    // Review round 1: re-running a manual run that acknowledged this commit re-plans it against
    // the state of that day. A plan that now also destroys google_x.b is not what was reviewed.
    const result = review(
      { exit: 2, changes: [change("google_x.a", "delete"), change("google_x.b", "delete")] },
      { ALLOW_REPLACE_ACK: `${COMMIT}:${digest("google_x.a")}` },
    );
    expect(result.status).toBe(3);
    expect(result.applied).toEqual([]);
  });

  it("apply a destroy acknowledged for this commit and exactly these, saying so", () => {
    const result = review(
      { exit: 2, changes: [change("google_x.a", "delete")] },
      { ALLOW_REPLACE_ACK: `${COMMIT}:${digest("google_x.a")}` },
    );
    expect(result.status).toBe(0);
    expect(result.applied).toHaveLength(1);
    expect(result.out).toContain("Destroy acknowledged");
  });

  it("leave no saved plan behind, however the run ends", () => {
    // A saved plan holds sensitive values; deploy.sh removes every one on exit.
    // Through common.sh's EXIT trap (ema_flow_on_exit), which also fails a run cut short.
    expect(deploy).toContain(
      `deploy_cleanup() { rm -f \${DEPLOY_TEMP_FILES[@]+"\${DEPLOY_TEMP_FILES[@]}"}; }\nema_flow_on_exit deploy_cleanup`,
    );
    expect(extract("plan_reviewed")).toContain(
      'DEPLOY_TEMP_FILES+=("$plan_file" "$plan_text" "$plan_json" "$summary")',
    );
  });

  it.each([
    ["a plan that fails", { exit: 1 }],
    ["a plan that cannot be shown", { exit: 2, showFails: true }],
  ])("apply nothing after %s", (_label, plan) => {
    const result = review(plan);
    expect(result.status).toBe(1);
    expect(result.applied).toEqual([]);
  });

  it("are never unreviewed: no destructive command anywhere but the dashboard's replace", () => {
    // Review round 1: the guard read only "-auto-approve \\" on a line of its own. Now any
    // auto-approved apply, -destroy, -replace, state rm or terraform destroy anywhere in deploy.sh
    // (comments aside) must be sync_dashboard's deliberate replacement of the dashboard.
    const code = deploy
      .split("\n")
      .map((line, index) => ({ line, index }))
      .filter(({ line }) => !/^\s*#/.test(line));
    const dashboard = extract("sync_dashboard");
    const start = deploy.split("\n").findIndex((line) => line === "sync_dashboard() {");
    const end = start + dashboard.split("\n").length;
    const destructive =
      /-auto-approve|-destroy\b|-replace=|\bstate (rm|replace-provider)\b|terraform\b[^\n]*\bdestroy\b/;
    const found = code.filter(({ line }) => destructive.test(line));
    expect(found.length).toBeGreaterThan(0);
    for (const { line, index } of found) {
      expect([line.trim(), index > start && index < end]).toEqual([line.trim(), true]);
    }
    for (const bad of [
      "terraform -chdir=infra apply -auto-approve",
      "terraform -chdir=infra apply -destroy",
      "terraform -chdir=infra state rm x",
      "terraform -chdir=infra destroy",
    ]) {
      expect([bad, destructive.test(bad)]).toEqual([bad, true]);
    }
  });

  it("are never unreviewed: no -auto-approve apply in phase_apis or phase_apply", () => {
    for (const phase of ["phase_apis", "phase_apply"]) {
      const body = extract(phase);
      expect([phase, body.includes("-auto-approve")]).toEqual([phase, false]);
      expect(body).toMatch(/plan_reviewed (apis|apply) /);
      expect(body).toContain('terraform -chdir=infra apply -input=false "$REVIEWED_PLAN"');
    }
    // The one left is the dashboard's, a deliberate targeted replace of that one resource.
    const unreviewed = [...deploy.matchAll(/^\s+-auto-approve \\\n[\s\S]*?\n\n/gm)].map(
      (m) => m[0],
    );
    expect(unreviewed).toHaveLength(1);
    expect(unreviewed[0]).toContain("-replace=google_monitoring_dashboard.operations");
    expect(unreviewed[0]).toContain("-target=google_monitoring_dashboard.operations");
  });

  it("can be acknowledged from the deploy workflow, per run", () => {
    const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");
    expect(workflow).toMatch(
      /workflow_dispatch:\n {4}inputs:\n(?: {6}#.*\n)* {6}allow_replace_ack:/,
    );
    expect(workflow).toContain("ALLOW_REPLACE_ACK: ${{ inputs.allow_replace_ack || '' }}");
    expect(workflow).not.toMatch(/allow_replace_commit|ALLOW_REPLACE_COMMIT/);
  });
});
