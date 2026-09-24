import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

// The importer lock's base (docs/design/authority-import-contract.md, D10): both jobs that run
// `npm run check` in CI name it, from full history, before the checks; the script picks the
// commit before a push, and main otherwise.

const SCRIPT = path.resolve("scripts/ci/lock-base.sh");

function job(workflow: string, name: string): string {
  const text = readFileSync(`.github/workflows/${workflow}`, "utf8");
  const start = text.indexOf(`\n  ${name}:\n`);
  expect(start, `${workflow} has a ${name} job`).toBeGreaterThan(-1);
  const rest = text.slice(start + 1);
  const next = rest.slice(1).search(/\n {2}[a-z-]+:\n/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}

describe("the jobs that run the checks", () => {
  for (const [workflow, name] of [
    ["ci.yml", "check"],
    ["deploy.yml", "gate"],
  ] as const) {
    it(`${workflow} ${name} names the lock's base from full history before the checks`, () => {
      const text = job(workflow, name);
      expect(text).toMatch(/^\s+fetch-depth: 0$/m);
      expect(text).toMatch(/^\s+persist-credentials: false$/m);
      const step = text.indexOf("run: bash scripts/ci/lock-base.sh");
      expect(step).toBeGreaterThan(-1);
      expect(text.slice(0, step)).toMatch(/BEFORE_SHA: \$\{\{ github\.event\.before \}\}/);
      expect(step).toBeLessThan(text.indexOf("run: npm run check"));
      // The step runs unconditionally.
      const stepStart = text.lastIndexOf("- name:", step);
      expect(text.slice(stepStart, step)).not.toMatch(/\bif:/);
    });
  }
});

describe("scripts/ci/lock-base.sh", () => {
  const repository = mkdtempSync(path.join(tmpdir(), "lock-base-"));
  const git = (...args: string[]): string =>
    execFileSync("git", args, { cwd: repository, encoding: "utf8" }).trim();
  git("init", "-q");
  git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "one");
  const first = git("rev-parse", "HEAD");
  git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "two");
  const second = git("rev-parse", "HEAD");
  git("update-ref", "refs/remotes/origin/main", second);
  // HEAD is not main, so a base read from HEAD would show.
  git("checkout", "-q", "--detach", first);

  function base(environment: Record<string, string>): { status: number; base?: string } {
    const output = path.join(repository, `env-${String(Math.random()).slice(2)}`);
    writeFileSync(output, "EARLIER=kept\n");
    const run = spawnSync("bash", [SCRIPT], {
      cwd: repository,
      env: { PATH: process.env.PATH, GITHUB_ENV: output, ...environment },
      encoding: "utf8",
    });
    const lines = readFileSync(output, "utf8").trim().split("\n");
    // The step appends; what the file held before stays.
    expect(lines[0]).toBe("EARLIER=kept");
    const line = lines[1] ?? "";
    return {
      status: run.status ?? 1,
      ...(line === "" ? {} : { base: line.replace("LOCK_BASE=", "") }),
    };
  }

  it("takes the commit before a push, and main otherwise", () => {
    expect(base({ GITHUB_EVENT_NAME: "push", BEFORE_SHA: first })).toEqual({
      status: 0,
      base: first,
    });
    for (const environment of [
      { GITHUB_EVENT_NAME: "push", BEFORE_SHA: "0".repeat(40) },
      { GITHUB_EVENT_NAME: "push", BEFORE_SHA: "" },
      { GITHUB_EVENT_NAME: "pull_request", BEFORE_SHA: first },
      { GITHUB_EVENT_NAME: "workflow_dispatch" },
    ]) {
      expect(base(environment), JSON.stringify(environment)).toEqual({ status: 0, base: second });
    }
  });

  it("fails when the base is not in the history it has", () => {
    expect(base({ GITHUB_EVENT_NAME: "push", BEFORE_SHA: "1".repeat(40) }).status).not.toBe(0);
  });
});
