import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The plan workflow's first step (.github/workflows/plan.yml): a run Dependabot triggers cannot
// request the planner's identity token, so it plans nothing, and passes only when the pull request
// changes no file the plan may read. The step's own script is run here, as the workflow runs it,
// over a pull request's merge commit.

const workflow = readFileSync(".github/workflows/plan.yml", "utf8");

function stepScript(name: string): string {
  const start = workflow.indexOf(`- name: ${name}\n`);
  expect(start, `plan.yml has a step "${name}"`).toBeGreaterThan(-1);
  const run = workflow.indexOf("        run: |\n", start);
  const lines: string[] = [];
  for (const line of workflow.slice(run + "        run: |\n".length).split("\n")) {
    if (line !== "" && !line.startsWith("          ")) break;
    lines.push(line.slice(10));
  }
  return lines.join("\n");
}

const SCRIPT = stepScript("Decide whether this run can and must plan");
const directories: string[] = [];

afterEach(() => {
  for (const directory of directories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

// A repository whose HEAD is a pull request's merge commit: main, plus a branch changing <files>.
// With `rename`, main carries the first path and the branch moves it to the second, changed a
// little, which git's rename detection pairs up.
function pullRequest(
  files: string[],
  { merge = true, rename }: { merge?: boolean; rename?: [string, string] } = {},
): string {
  const repository = mkdtempSync(path.join(tmpdir(), "plan-scope-"));
  directories.push(repository);
  const git = (...args: string[]): string =>
    execFileSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", ...args], {
      cwd: repository,
      encoding: "utf8",
      env: { PATH: process.env.PATH, GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1" },
    }).trim();
  git("init", "-q", "-b", "main");
  writeFileSync(path.join(repository, "README"), "base\n");
  git("add", "README");
  if (rename !== undefined) {
    mkdirSync(path.dirname(path.join(repository, rename[0])), { recursive: true });
    const lines = Array.from({ length: 200 }, (_, index) => `resource "x" "r${index}" {}`);
    writeFileSync(path.join(repository, rename[0]), `${lines.join("\n")}\n`);
    git("add", rename[0]);
  }
  git("commit", "-q", "-m", "base");
  git("checkout", "-q", "-b", "update");
  if (rename !== undefined) {
    mkdirSync(path.dirname(path.join(repository, rename[1])), { recursive: true });
    git("mv", rename[0], rename[1]);
    const moved = path.join(repository, rename[1]);
    writeFileSync(moved, `${readFileSync(moved, "utf8")}# tweak\n`);
    git("add", rename[1]);
  }
  for (const file of files) {
    mkdirSync(path.dirname(path.join(repository, file)), { recursive: true });
    writeFileSync(path.join(repository, file), "changed\n");
    git("add", file);
  }
  git("commit", "-q", "-m", "update");
  if (merge) {
    git("checkout", "-q", "main");
    writeFileSync(path.join(repository, "MAIN"), "main moved on\n");
    git("add", "MAIN");
    git("commit", "-q", "-m", "main");
    git("merge", "-q", "--no-ff", "-m", "merge", "update");
  }
  return repository;
}

const REPOSITORY = "owner/repository";
const DEPENDABOT = { author: "dependabot[bot]", head: REPOSITORY };

function scope(
  repository: string,
  actor: string,
  pull = DEPENDABOT,
): { status: number; plan: string; log: string } {
  const output = path.join(repository, "..", `${path.basename(repository)}.output`);
  writeFileSync(output, "");
  directories.push(output);
  const run = spawnSync("bash", ["--noprofile", "--norc", "-eo", "pipefail", "-c", SCRIPT], {
    cwd: repository,
    env: {
      PATH: process.env.PATH,
      ACTOR: actor,
      AUTHOR: pull.author,
      HEAD_REPOSITORY: pull.head,
      REPOSITORY,
      GITHUB_OUTPUT: output,
    },
    encoding: "utf8",
  });
  const plan = /^plan=(.*)$/m.exec(readFileSync(output, "utf8"))?.[1] ?? "";
  return { status: run.status ?? -1, plan, log: `${run.stdout}${run.stderr}` };
}

describe("the plan workflow's Dependabot scope", () => {
  it("always plans a person's run, whatever it changes", () => {
    const repository = pullRequest(["package-lock.json"]);
    expect(scope(repository, "ogbetspp-coder")).toMatchObject({ status: 0, plan: "true" });
  });

  it("passes a Dependabot run without planning when nothing it changes is read by the plan", () => {
    const repository = pullRequest([
      "package.json",
      "package-lock.json",
      "agent/uv.lock",
      "zone-a/pyproject.toml",
      "label-docx-reader/uv.lock",
      "Dockerfile.validator",
      ".github/workflows/ci.yml",
      ".github/workflows/label-docx-reader.yml",
    ]);
    expect(scope(repository, "dependabot[bot]")).toMatchObject({ status: 0, plan: "false" });
  });

  it.each([
    ["a provider bump", "infra/.terraform.lock.hcl"],
    ["an Action in the plan workflow itself", ".github/workflows/plan.yml"],
    ["the script the plan runs", "scripts/gcp/deploy.sh"],
    ["a path it has never seen", "somewhere/new.txt"],
  ])("fails a Dependabot run that changes %s, naming the file", (_, file) => {
    const repository = pullRequest(["package-lock.json", file]);
    const result = scope(repository, "dependabot[bot]");
    expect(result).toMatchObject({ status: 1, plan: "" });
    expect(result.log).toContain(file);
    expect(result.log).toContain("Push any commit to its branch as a person");
  });

  it("fails closed when HEAD is not a merge commit", () => {
    const repository = pullRequest(["package-lock.json"], { merge: false });
    expect(scope(repository, "dependabot[bot]")).toMatchObject({ status: 1, plan: "" });
  });

  it("counts both sides of a rename, so moving a file the plan reads is still seen", () => {
    const repository = pullRequest(["package-lock.json"], {
      rename: ["infra/main.tf", "Dockerfile.evil"],
    });
    const result = scope(repository, "dependabot[bot]");
    expect(result).toMatchObject({ status: 1, plan: "" });
    expect(result.log).toContain("infra/main.tf");
  });

  it.each([
    ["a person's pull request Dependabot pushed to", { author: "someone", head: REPOSITORY }],
    ["a pull request from a fork", { author: "dependabot[bot]", head: "fork/repository" }],
  ])("fails a Dependabot run on %s, whatever it changes", (_, pull) => {
    const repository = pullRequest(["package-lock.json"]);
    const result = scope(repository, "dependabot[bot]", pull);
    expect(result).toMatchObject({ status: 1, plan: "" });
    expect(result.log).toContain("not Dependabot's own pull request from this repository");
  });

  it("gates every step that authenticates or plans on the decision", () => {
    for (const step of [
      "Authenticate to Google Cloud as the planner",
      "Set up Google Cloud CLI",
      "Set up Terraform",
      "Terraform init",
      "Plan against live state",
    ]) {
      const start = workflow.indexOf(`- name: ${step}\n`);
      const next = workflow.indexOf("- name:", start + 1);
      expect([step, workflow.slice(start, next)]).toEqual([
        step,
        expect.stringContaining("if: steps.scope.outputs.plan == 'true'"),
      ]);
    }
    expect(workflow.indexOf("id: scope")).toBeLessThan(
      workflow.indexOf("google-github-actions/auth"),
    );
  });
});
