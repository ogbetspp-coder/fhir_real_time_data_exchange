import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

// Who can change production (docs/foundations.md, findings B1, B2, C3). The deployer can grant
// itself anything, so the two ways to become it — the Workload Identity condition and the code
// that runs holding its credentials — are pinned here.

const workflowsDir = path.resolve(".github/workflows");
const workflows = readdirSync(workflowsDir)
  .filter((name) => name.endsWith(".yml") || name.endsWith(".yaml"))
  .map((name) => ({ name, text: readFileSync(path.join(workflowsDir, name), "utf8") }));

describe("third-party code that runs in CI and deploys", () => {
  it("is pinned to a full commit SHA, with the release it came from", () => {
    const uses = workflows.flatMap(({ name, text }) =>
      [...text.matchAll(/^\s*-?\s*uses:\s*(.+)$/gm)].map((match) => ({
        name,
        ref: (match[1] ?? "").trim(),
      })),
    );
    expect(uses.length).toBeGreaterThan(0);
    for (const { name, ref } of uses) {
      // A tag or branch can be moved to new code; a commit cannot.
      expect([name, ref]).toEqual([
        name,
        expect.stringMatching(/^[\w.-]+\/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$/),
      ]);
    }
  });

  it("has an update path, so a pin does not quietly rot", () => {
    const dependabot = readFileSync(".github/dependabot.yml", "utf8");
    for (const ecosystem of ["github-actions", "npm", "uv", "docker", "terraform"]) {
      expect(dependabot).toContain(`package-ecosystem: ${ecosystem}`);
    }
  });
});

describe("the deploy identity", () => {
  const script = readFileSync("scripts/gcp/deploy-identity.sh", "utf8");
  const condition = /^CONDITION="(.+)"$/m.exec(script)?.[1] ?? "";

  it("admits only this repository, by id, on main, running the deploy workflow", () => {
    expect(condition).toContain("assertion.repository_id=='${REPOSITORY_ID}'");
    expect(condition).toContain("assertion.ref=='refs/heads/main'");
    expect(condition).toContain(
      "assertion.workflow_ref.startsWith('${REPOSITORY}/.github/workflows/deploy.yml@refs/heads/main')",
    );
    // Every clause is required: an `||` anywhere would let one clause admit what the others
    // refuse.
    expect(condition).not.toContain("||");
    expect(script).toMatch(/^REPOSITORY_ID="\d+"$/m);
  });

  it("is only ever requested by the deploy workflow", () => {
    // The condition names deploy.yml. Any other workflow asking for Google credentials would
    // either fail, or mean the condition had been widened to admit it.
    // Since 2026-09-22 one other workflow authenticates: the pull-request plan, as the read-only
    // planner through its own pool (test/infra/plan-identity.test.ts). It must never name the
    // deployer's provider or account.
    const authenticating = workflows
      .filter(({ text }) => text.includes("google-github-actions/auth@"))
      .map(({ name }) => name);
    expect(authenticating).toEqual(["deploy.yml", "plan.yml"]);
    const namingDeployer = workflows
      .filter(
        ({ text }) =>
          text.includes("vars.GCP_WORKLOAD_IDENTITY_PROVIDER") ||
          text.includes("service_account: ${{ vars.GCP_DEPLOY_SERVICE_ACCOUNT }}"),
      )
      .map(({ name }) => name);
    expect(namingDeployer).toEqual(["deploy.yml"]);
  });

  it("runs the deploy workflow only from main", () => {
    const deploy = workflows.find(({ name }) => name === "deploy.yml")?.text ?? "";
    const on = /^on:\n([\s\S]*?)^\S/m.exec(deploy)?.[1] ?? "";
    expect(on).toMatch(/push:\s*\n\s*branches:\s*\n\s*-\s*main\s*\n/);
    // workflow_dispatch can target any branch; the ref clause of the condition is what refuses
    // those, which is why it is tested above rather than trusted to the trigger.
    expect(on).not.toMatch(/pull_request/);
  });
});
