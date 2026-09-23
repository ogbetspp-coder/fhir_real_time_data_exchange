import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Which job of the deploy workflow may become the deployer. A job granted `id-token: write` can
// ask GitHub for an OIDC token that Workload Identity Federation exchanges for the deployer
// service account, and every process in the job can ask, before any auth step has run. Until
// 2026-09-22 the whole workflow held the grant and the quality gate ran in the deploy job, so an
// install script or a test dependency could have minted the deployer. These tests keep the gate
// in a job that cannot ask, and the one job that can from running install scripts.

const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");

function jobs(text: string): Map<string, string> {
  const body = text.slice(text.indexOf("\njobs:\n") + "\njobs:\n".length);
  const found = new Map<string, string>();
  const headers = [...body.matchAll(/^ {2}([a-z][a-z0-9-]*):\n/gm)];
  headers.forEach((header, index) => {
    const start = header.index + header[0].length;
    const end = headers[index + 1]?.index ?? body.length;
    found.set(header[1] ?? "", body.slice(start, end));
  });
  return found;
}

const byName = jobs(workflow);

describe("deploy workflow permissions", () => {
  it("grants nothing at the workflow level", () => {
    expect(workflow).toMatch(/^permissions: \{\}$/m);
  });

  it("lets exactly one job ask for an OIDC token: deploy", () => {
    const withToken = [...byName].filter(([, text]) => text.includes("id-token: write"));
    expect(withToken.map(([name]) => name)).toEqual(["deploy"]);
  });

  it("runs the quality gate only in jobs that cannot ask for a token", () => {
    const gates = [...byName].filter(([, text]) => /npm (run check|test)\b/.test(text));
    expect(gates.map(([name]) => name)).toEqual(["gate"]);
    for (const [, text] of gates) expect(text).not.toMatch(/id-token/);
  });

  it("deploys only after the gate, installing packages without their scripts", () => {
    const deploy = byName.get("deploy") ?? "";
    expect(deploy).toMatch(/^ {4}needs: gate$/m);
    const install = /- name: Install Node dependencies[^\n]*\n((?: {8}.*\n)+)/.exec(deploy)?.[1];
    expect(install).toBeDefined();
    expect(install).toMatch(/npm_config_ignore_scripts: "true"/);
    // Nothing installs anything else in the job that holds the grant.
    expect(deploy.match(/deploy\.sh deps|npm (ci|install)\b/g)).toHaveLength(1);
  });
});
