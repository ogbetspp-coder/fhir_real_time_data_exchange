import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// Which commit a deploy names (audit B08, D-4). The image tag, every audit record's
// QUERY_SERVICE_VERSION, every run manifest's GIT_COMMIT and each image's revision label must name
// the code that was built. Until then the tag came from a `-d .git` test that a worktree fails
// (its .git is a file), so every worktree deploy shared the tag "manual", while the version came
// from `git rev-parse HEAD`; and neither noticed the uncommitted changes `gcloud builds submit .`
// uploads. These run the real functions from deploy.sh in throwaway repositories.

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

const GIT_ENV = {
  GIT_CONFIG_GLOBAL: "/dev/null",
  GIT_CONFIG_NOSYSTEM: "1",
  GIT_AUTHOR_NAME: "t",
  GIT_AUTHOR_EMAIL: "t@t",
  GIT_COMMITTER_NAME: "t",
  GIT_COMMITTER_EMAIL: "t@t",
};

function git(cwd: string, ...args: string[]): string {
  const run = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    env: { ...process.env, ...GIT_ENV },
  });
  if (run.status !== 0) throw new Error(`git ${args.join(" ")}: ${run.stderr}`);
  return run.stdout.trim();
}

// A repository with one commit, and a worktree of it; returns the worktree and its commit.
function worktree(): { dir: string; commit: string } {
  const root = mkdtempSync(path.join(tmpdir(), "deploy-provenance-"));
  dirs.push(root);
  const repo = path.join(root, "repo");
  git(root, "init", "-q", "-b", "main", repo);
  writeFileSync(path.join(repo, "a.txt"), "one\n");
  writeFileSync(path.join(repo, ".gitignore"), "ignored.log\n");
  git(repo, "add", "-A");
  git(repo, "commit", "-q", "-m", "one");
  const dir = path.join(root, "wt");
  git(repo, "worktree", "add", "-q", dir);
  return { dir, commit: git(dir, "rev-parse", "HEAD") };
}

// TAG, SERVICE_VERSION, and the exit status of require_provenance, in `cwd`.
function provenance(cwd: string, env: Record<string, string> = {}) {
  const run = spawnSync(
    "bash",
    [
      "-c",
      `set -euo pipefail
${extract("deploy_provenance")}
${extract("require_provenance")}
deploy_provenance
printf '%s %s\\n' "$TAG" "$SERVICE_VERSION"
require_provenance`,
    ],
    { cwd, encoding: "utf8", env: { PATH: process.env.PATH ?? "", ...GIT_ENV, ...env } },
  );
  const [tag = "", version = ""] = run.stdout.split("\n")[0]?.split(" ") ?? [];
  return { tag, version, status: run.status, out: `${run.stdout}${run.stderr}` };
}

describe("the commit a deploy names", () => {
  it("is HEAD in a worktree, for the tag and the version alike", () => {
    const { dir, commit } = worktree();
    const result = provenance(dir);
    expect(result.status).toBe(0);
    expect(result.tag).toBe(commit.slice(0, 12));
    expect(result.version).toBe(commit);
  });

  it("carries the same -dirty-<tree> on both when a tracked file differs from HEAD", () => {
    const { dir, commit } = worktree();
    writeFileSync(path.join(dir, "a.txt"), "two\n");
    const first = provenance(dir);
    expect(first.version).toMatch(new RegExp(`^${commit}-dirty-[0-9a-f]{12}$`));
    expect(first.tag).toBe(`${commit.slice(0, 12)}${first.version.slice(commit.length)}`);
    expect(first.status).toBe(0);
    expect(first.out).toContain("Uncommitted changes");
    // The same edits name the same tree; other edits another.
    expect(provenance(dir).version).toBe(first.version);
    writeFileSync(path.join(dir, "a.txt"), "three\n");
    expect(provenance(dir).version).not.toBe(first.version);
  });

  it("counts an untracked file the build would upload, and not one .gitignore excludes", () => {
    const { dir, commit } = worktree();
    writeFileSync(path.join(dir, "ignored.log"), "a deploy log\n");
    expect(provenance(dir).version).toBe(commit);
    writeFileSync(path.join(dir, "new.txt"), "uploaded\n");
    expect(provenance(dir).version).toMatch(/-dirty-[0-9a-f]{12}$/);
  });

  it("leaves the repository's own index alone", () => {
    const { dir } = worktree();
    writeFileSync(path.join(dir, "new.txt"), "uploaded\n");
    provenance(dir);
    expect(git(dir, "status", "--porcelain")).toBe("?? new.txt");
  });

  it("refuses to build or apply a checkout that is not GITHUB_SHA", () => {
    const { dir } = worktree();
    const result = provenance(dir, { GITHUB_SHA: "0".repeat(40) });
    expect(result.status).not.toBe(0);
    expect(result.out).toContain(`not GITHUB_SHA ${"0".repeat(40)}`);
  });

  it("takes GITHUB_SHA without git, and refuses with neither", () => {
    const outside = mkdtempSync(path.join(tmpdir(), "deploy-provenance-nogit-"));
    dirs.push(outside);
    const noGit = { GIT_DIR: path.join(outside, "no-such-git-dir") };
    const sha = "0123456789abcdef0123456789abcdef01234567";
    const actions = provenance(outside, { ...noGit, GITHUB_SHA: sha });
    expect([actions.status, actions.tag, actions.version]).toEqual([0, sha.slice(0, 12), sha]);
    const neither = provenance(outside, noGit);
    expect(neither.status).not.toBe(0);
    expect(neither.out).toContain("no commit names what would be built");
  });

  it("is required by the phases that build and apply", () => {
    for (const phase of ["phase_images", "phase_apply"]) {
      expect(extract(phase)).toMatch(/^ {2}require_provenance$/m);
    }
    expect(deploy).not.toMatch(/\[\[ -d \.git \]\]/);
  });
});
