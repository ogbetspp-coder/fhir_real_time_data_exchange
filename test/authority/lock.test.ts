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

  it("keeps every entry main already has", () => {
    let released: string;
    try {
      released = execFileSync("git", ["show", `origin/main:${LOCK}`], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });
    } catch {
      // Without main's lock nothing is released yet, or the ref is missing: CI fetches it
      // (.github/workflows/ci.yml), so there a missing ref fails rather than passes.
      const mainExists = (() => {
        try {
          execFileSync("git", ["rev-parse", "--verify", "origin/main"], { stdio: "ignore" });
          return true;
        } catch {
          return false;
        }
      })();
      expect(mainExists || process.env.CI !== "true", "CI must fetch origin/main").toBe(true);
      return;
    }
    const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Record<string, LockEntry>;
    for (const [version, entry] of Object.entries(
      JSON.parse(released) as Record<string, LockEntry>,
    )) {
      expect(lock[version], `importer ${version} is on main; change IMPORTER_VERSION`).toEqual(
        entry,
      );
    }
  });
});
