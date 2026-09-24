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

  it("keeps every entry already released", () => {
    // What was released: LOCK_BASE in CI (scripts/ci/fetch-lock-base.sh: the pre-push commit for
    // a push, so a push to main is compared with main before it; main otherwise, which for a
    // manual run on main is this commit, already checked when it was pushed), origin/main
    // locally.
    const base = process.env.LOCK_BASE ?? "origin/main";
    const git = (args: string[]): string | undefined => {
      try {
        return execFileSync("git", args, {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "ignore"],
        }).trim();
      } catch {
        return undefined;
      }
    };
    const baseCommit = git(["rev-parse", "--verify", `${base}^{commit}`]);
    if (process.env.CI === "true") {
      expect(baseCommit, "CI must fetch the lock's base").toBeDefined();
    }
    if (baseCommit === undefined) return;
    const released = git(["show", `${baseCommit}:${LOCK}`]);
    if (released === undefined) return; // nothing released before this lock existed
    const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Record<string, LockEntry>;
    for (const [version, entry] of Object.entries(
      JSON.parse(released) as Record<string, LockEntry>,
    )) {
      expect(lock[version], `importer ${version} was released; change IMPORTER_VERSION`).toEqual(
        entry,
      );
    }
  });
});
