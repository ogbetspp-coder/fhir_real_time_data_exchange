import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { readInfra } from "../support/terraform.js";

// The deploy completes a pending `moved` block in the state before its targeted applies. Terraform
// refuses a -target apply that leaves out either end of a pending move, and the first deploy after
// #125 stopped there ("Moved resource instances excluded by targeting"), before applying anything.
// These run the real functions from deploy.sh against a stand-in terraform.

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

// Runs the two functions in bash, in `cwd`, with a stand-in terraform whose `state list` prints
// `state` and which logs every call.
function run(
  state: string[],
  cwd = process.cwd(),
): { status: number | null; out: string; calls: string[] } {
  const dir = mkdtempSync(path.join(tmpdir(), "pending-moves-"));
  dirs.push(dir);
  const calls = path.join(dir, "calls.log");
  writeFileSync(calls, "");
  writeFileSync(path.join(dir, "state"), state.map((line) => `${line}\n`).join(""));
  const terraform = path.join(dir, "terraform");
  writeFileSync(
    terraform,
    `#!/usr/bin/env bash
echo "$*" >>"${calls}"
[ "$2 $3" = "state list" ] && cat "${path.join(dir, "state")}"
exit 0
`,
  );
  chmodSync(terraform, 0o755);
  const result = spawnSync(
    "bash",
    [
      "-c",
      `set -euo pipefail\n${extract("moved_pairs")}\n${extract("complete_pending_moves")}\ncomplete_pending_moves`,
    ],
    { cwd, encoding: "utf8", env: { PATH: `${dir}:${process.env.PATH ?? ""}` } },
  );
  return {
    status: result.status,
    out: `${result.stdout}${result.stderr}`,
    calls: readFileSync(calls, "utf8").split("\n").filter(Boolean),
  };
}

const moves = (calls: string[]) => calls.filter((call) => call.includes("state mv"));

describe("the deploy's pending moves", () => {
  it("reads every moved block in infra/", () => {
    const blocks = readInfra().match(/^moved \{/gm) ?? [];
    expect(blocks.length).toBeGreaterThan(0);
    const result = spawnSync("bash", ["-c", `${extract("moved_pairs")}\nmoved_pairs`], {
      encoding: "utf8",
    });
    expect(result.status).toBe(0);
    const pairs = result.stdout.trim().split("\n");
    expect(pairs).toHaveLength(blocks.length);
    expect(pairs).toContain(
      "google_monitoring_alert_policy.query_entitlement_denials[0] google_monitoring_alert_policy.query_entitlement_denials",
    );
    for (const pair of pairs) expect(pair).toMatch(/^\S+ \S+$/);
  });

  // A configuration of its own, so the cases do not depend on which moves infra/ holds today: a
  // whole-resource move, an instance move, and a removed block that is not a move.
  const fixture = () => {
    const cwd = mkdtempSync(path.join(tmpdir(), "pending-moves-infra-"));
    dirs.push(cwd);
    mkdirSync(path.join(cwd, "infra"));
    writeFileSync(
      path.join(cwd, "infra", "x.tf"),
      `moved {
  from = google_monitoring_notification_channel.old_email
  to   = google_monitoring_notification_channel.alert_email
}

moved {
  from = google_monitoring_alert_policy.key_availability[0]
  to   = google_monitoring_alert_policy.key_availability
}

removed {
  from = google_monitoring_notification_channel.forgotten

  lifecycle {
    destroy = false
  }
}
`,
    );
    return cwd;
  };

  it("completes each move whose old address, or an instance of it, is in the state", () => {
    const result = run(
      [
        "google_monitoring_alert_policy.key_availability[0]",
        "google_monitoring_notification_channel.old_email[0]",
        "google_monitoring_notification_channel.forgotten[0]",
        "google_monitoring_alert_policy.pipeline_failures",
      ],
      fixture(),
    );
    expect(result.status).toBe(0);
    expect(moves(result.calls)).toEqual([
      "-chdir=infra state mv google_monitoring_notification_channel.old_email google_monitoring_notification_channel.alert_email",
      "-chdir=infra state mv google_monitoring_alert_policy.key_availability[0] google_monitoring_alert_policy.key_availability",
    ]);
  });

  it("does nothing once the moves are done", () => {
    const result = run(
      [
        "google_monitoring_alert_policy.key_availability",
        "google_monitoring_notification_channel.alert_email[0]",
      ],
      fixture(),
    );
    expect(result.status).toBe(0);
    expect(moves(result.calls)).toEqual([]);
  });

  it("does not take a resource whose name only begins with an old address", () => {
    const result = run(
      [
        "google_monitoring_notification_channel.old_email_other[0]",
        "google_monitoring_alert_policy.key_availability[1]",
      ],
      fixture(),
    );
    expect(result.status).toBe(0);
    expect(moves(result.calls)).toEqual([]);
  });

  // Audit I-11 gave dev's legacy resources a count, moving X to X[0]. X[0] is where such a move
  // arrives, so it is pending only while X itself is in the state; read as "an instance of X",
  // every deploy after the first would have tried to move it again, and failed.
  it("completes a move onto an instance of the same resource once, and then leaves it", () => {
    const cwd = mkdtempSync(path.join(tmpdir(), "pending-moves-infra-"));
    dirs.push(cwd);
    mkdirSync(path.join(cwd, "infra"));
    writeFileSync(
      path.join(cwd, "infra", "x.tf"),
      "moved {\n  from = google_kms_crypto_key.manifest_signing\n  to   = google_kms_crypto_key.manifest_signing[0]\n}\n",
    );
    const pending = run(["google_kms_crypto_key.manifest_signing"], cwd);
    expect(pending.status).toBe(0);
    expect(moves(pending.calls)).toEqual([
      "-chdir=infra state mv google_kms_crypto_key.manifest_signing google_kms_crypto_key.manifest_signing[0]",
    ]);
    const done = run(["google_kms_crypto_key.manifest_signing[0]"], cwd);
    expect([done.status, moves(done.calls)]).toEqual([0, []]);
    const absent = run(["google_kms_crypto_key.manifest_signing_hsm"], cwd);
    expect([absent.status, moves(absent.calls)]).toEqual([0, []]);
  });

  it("refuses a moved block with no destination instead of passing over it", () => {
    const cwd = mkdtempSync(path.join(tmpdir(), "pending-moves-infra-"));
    dirs.push(cwd);
    mkdirSync(path.join(cwd, "infra"));
    writeFileSync(path.join(cwd, "infra", "x.tf"), "moved {\n  from = a.b\n}\n");
    const result = run(["a.b"], cwd);
    expect(result.status).not.toBe(0);
    expect(result.out).toContain("has no from or no to");
    expect(moves(result.calls)).toEqual([]);
  });

  it("runs before the first targeted apply", () => {
    const apis = extract("phase_apis");
    const complete = apis.indexOf("complete_pending_moves");
    expect(complete).toBeGreaterThan(-1);
    expect(complete).toBeLessThan(apis.indexOf("-target="));
  });
});
