import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

// Operator scripts name the project they act on from the environment, never from a literal. Until
// 2026-09-22 eleven of them defaulted to the dev project id, so a script run in a shell set up for
// another project (production, once it exists) acted on dev without saying so. They now resolve
// it as deploy.sh does, through scripts/gcp/common.sh, and fail when nothing names a project.

const scripts = [
  ...readdirSync("scripts/gcp").map((name) => `scripts/gcp/${name}`),
  ...readdirSync("agent/deploy").map((name) => `agent/deploy/${name}`),
].filter((file) => file.endsWith(".sh") && !file.endsWith("/common.sh"));

const PROJECT_ID = /^\s*[A-Z_]*PROJECT[A-Z_]*=.*$/gm;

describe("operator scripts resolve the project, never default it", () => {
  it("carry no literal project id in any project assignment", () => {
    for (const file of scripts) {
      const assignments = readFileSync(file, "utf8").match(PROJECT_ID) ?? [];
      for (const line of assignments) {
        // The one literal left is PROD_PROJECT's name in landing-zone.sh, the project it creates.
        if (line.startsWith("PROD_PROJECT=")) continue;
        expect([file, line, /sage-ship-\d+-[a-z0-9]+/.test(line)]).toEqual([file, line, false]);
      }
    }
  });

  it("source common.sh and resolve through ema_flow_resolve_project when they name a project", () => {
    const resolving = scripts.filter((file) =>
      readFileSync(file, "utf8").includes("GCP_PROJECT_ID"),
    );
    expect(resolving.length).toBeGreaterThanOrEqual(11);
    for (const file of resolving) {
      const text = readFileSync(file, "utf8");
      const sourced =
        /^source "\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)(?:\/\.\.\/\.\.\/scripts\/gcp)?" && pwd\)\/common\.sh"$/m;
      expect([file, sourced.test(text) || file === "scripts/gcp/deploy.sh"]).toEqual([file, true]);
      expect([file, text.includes("ema_flow_resolve_project")]).toEqual([file, true]);
    }
  });

  it("parse as bash", () => {
    for (const file of scripts) {
      const run = spawnSync("bash", ["-n", file], { encoding: "utf8" });
      expect([file, run.status, run.stderr]).toEqual([file, 0, ""]);
    }
  });
});

describe("the resolution itself", () => {
  // Runs the exact line the scripts use, against the real common.sh, with a stub gcloud that
  // reports no configured project.
  const stub = mkdtempSync(path.join(tmpdir(), "ema-flow-gcloud-"));
  afterAll(() => rmSync(stub, { recursive: true, force: true }));
  writeFileSync(path.join(stub, "gcloud"), '#!/bin/sh\necho "(unset)"\n', "utf8");
  chmodSync(path.join(stub, "gcloud"), 0o755);

  function resolve(env: Record<string, string>): { status: number | null; out: string } {
    const run = spawnSync(
      "bash",
      [
        "-c",
        'set -euo pipefail; source scripts/gcp/common.sh; PROJECT_ID="${GCP_PROJECT_ID:-$(ema_flow_resolve_project)}"; printf %s "$PROJECT_ID"',
      ],
      { encoding: "utf8", env: { PATH: `${stub}:/usr/bin:/bin`, ...env } },
    );
    return { status: run.status, out: run.stdout };
  }

  it("keeps GCP_PROJECT_ID when it is set, even beside GOOGLE_CLOUD_PROJECT", () => {
    expect(resolve({ GCP_PROJECT_ID: "p-one", GOOGLE_CLOUD_PROJECT: "p-two" })).toEqual({
      status: 0,
      out: "p-one",
    });
  });

  it("falls back to GOOGLE_CLOUD_PROJECT", () => {
    expect(resolve({ GOOGLE_CLOUD_PROJECT: "p-two" })).toEqual({ status: 0, out: "p-two" });
  });

  it("fails when nothing names a project", () => {
    expect(resolve({})).toEqual({ status: 1, out: "" });
  });
});
