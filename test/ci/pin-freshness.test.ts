import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  SNAPSHOT_MAX_AGE_DAYS,
  check,
  pins,
  report,
  verdict,
  type Pin,
} from "../../scripts/ci/pin-freshness.mjs";

// The pins Dependabot does not move (audit B07, S-2), each read from the file that defines it and
// compared weekly with upstream (.github/workflows/pin-freshness.yml). Offline here: every pin
// must be readable from the repository as it stands, and the verdict must never count a failed
// lookup as current.

describe("pin freshness", () => {
  it("reads every pin from the file that defines it", () => {
    const list = pins();
    expect(list.map(({ name }) => name)).toEqual([
      "HL7 FHIR validator (validator_cli.jar)",
      "chrome-headless-shell",
      "Liberation fonts",
      "Debian snapshot (renderer)",
      "OSV-Scanner",
      "gitleaks",
      "uv",
      "Terraform",
      "Google Cloud SDK (setup-gcloud version)",
      "Cloud Build docker builder",
      "google-cloud-agentplatform (Agent Engine deploy)",
      "google-cloud-aiplatform (Agent Engine deploy)",
      "cloudpickle (Agent Engine deploy)",
    ]);
    for (const pin of list) expect([pin.name, pin.read()]).toEqual([pin.name, expect.any(String)]);
  });

  it("counts a failed lookup and an unreadable pin as unchecked, never as current", async () => {
    const [first, second] = pins();
    if (first === undefined || second === undefined) throw new Error("no pins");
    const unreadable: Pin = {
      ...second,
      read: () => {
        throw new Error("gone");
      },
    };
    const rows = await check([first, unreadable], () => Promise.reject(new Error("HTTP 503")));
    expect(rows.map(({ status }) => status)).toEqual(["unchecked", "unchecked"]);
    expect(report(rows).behind).toBe(2);
  });

  it("reports a pin behind upstream, and one that matches as current", async () => {
    const [first] = pins();
    if (first === undefined) throw new Error("no pins");
    const pinned = first.read();
    expect((await check([first], () => Promise.resolve(pinned)))[0]?.status).toBe("current");
    expect((await check([first], () => Promise.resolve(`${pinned}.1`)))[0]?.status).toBe("behind");
    expect(report(await check([first], () => Promise.resolve(pinned)))).toMatchObject({
      behind: 0,
    });
  });

  it("holds the renderer's Debian snapshot to an age, not to upstream equality", () => {
    const snapshot = pins().find(({ name }) => name.startsWith("Debian snapshot"));
    if (snapshot === undefined) throw new Error("no snapshot pin");
    const day = 24 * 60 * 60 * 1000;
    const at = (days: number) => new Date(Date.UTC(2026, 8, 1) + days * day).toISOString();
    expect(verdict(snapshot, "20260901T000000Z", at(SNAPSHOT_MAX_AGE_DAYS)).status).toBe("current");
    expect(verdict(snapshot, "20260901T000000Z", at(SNAPSHOT_MAX_AGE_DAYS + 1)).status).toBe(
      "behind",
    );
    expect(verdict(snapshot, "yesterday", at(0)).status).toBe("unchecked");
  });

  // Unpinned, setup-gcloud installed the newest Cloud SDK on every deploy and plan.
  it("pins the Cloud SDK, to one version, wherever it is installed", () => {
    const versions = [".github/workflows/deploy.yml", ".github/workflows/plan.yml"].map((file) => {
      const text = readFileSync(file, "utf8");
      const step =
        /uses: google-github-actions\/setup-gcloud@[^\n]*\n(?:\s+#[^\n]*\n)*\s+with:\n\s+version: "(\d+\.\d+\.\d+)"/.exec(
          text,
        );
      return [file, step?.[1]];
    });
    expect(versions.every(([, version]) => version !== undefined)).toBe(true);
    expect(new Set(versions.map(([, version]) => version)).size).toBe(1);
  });

  it("is reported weekly and opens an issue from a job that checks nothing out", () => {
    const workflow = readFileSync(".github/workflows/pin-freshness.yml", "utf8");
    expect(workflow).toContain("run: node scripts/ci/pin-freshness.mjs --report");
    expect(workflow).toMatch(/schedule:\n\s+- cron:/);
    const reporter = workflow.slice(workflow.indexOf("\n  report:"));
    expect(reporter).toContain("issues: write");
    expect(reporter).not.toContain("actions/checkout");
  });
});
