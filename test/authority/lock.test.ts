import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { IMPORTER_VERSION } from "../../src/authority/import.js";
import { LOCK, lockHashes, type LockEntry } from "../../scripts/authority/lock-hashes.js";

// A change to the importer's code, data or vectors must change its version
// (docs/design/authority-import-contract.md, D10): recomputation proves that the named version
// ran, so one version must always behave the same.

describe("the importer lock", () => {
  it("records this version with the hashes of its code, data and vectors", () => {
    const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Record<string, LockEntry>;
    expect(
      lock[IMPORTER_VERSION],
      "run npm run authority:lock after changing IMPORTER_VERSION",
    ).toEqual(lockHashes());
  });

  it("keeps every entry ever released", () => {
    // What was released: every importer lock in the first-parent history of LOCK_BASE (in CI,
    // scripts/ci/lock-base.sh: the commit before a push, main otherwise; locally origin/main).
    // Reading the whole history, not one commit, means neither a second push nor a manual run
    // after a changed released entry can compare with the change itself.
    const base = process.env.LOCK_BASE ?? "origin/main";
    const git = (args: string[]): string | undefined => {
      try {
        return execFileSync("git", args, {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "ignore"],
          maxBuffer: 64 * 1024 * 1024,
        }).trim();
      } catch {
        return undefined;
      }
    };
    const baseCommit = git(["rev-parse", "--verify", `${base}^{commit}`]);
    if (process.env.CI === "true") {
      expect(baseCommit, "CI must name the lock's base").toBeDefined();
    }
    if (baseCommit === undefined) return;
    const commits = (git(["log", "--first-parent", "--format=%H", baseCommit, "--", LOCK]) ?? "")
      .split("\n")
      .filter((commit) => commit.length > 0);
    const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Record<string, LockEntry>;
    for (const commit of commits) {
      const released = git(["show", `${commit}:${LOCK}`]);
      if (released === undefined) continue; // the commit that deleted the lock, if any
      for (const [version, entry] of Object.entries(
        JSON.parse(released) as Record<string, LockEntry>,
      )) {
        expect(
          lock[version],
          `importer ${version} was released (${commit}); change IMPORTER_VERSION`,
        ).toEqual(entry);
      }
    }
  });
});
