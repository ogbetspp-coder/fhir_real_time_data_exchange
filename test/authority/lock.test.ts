import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { IMPORTER_VERSION } from "../../src/authority/import.js";
import {
  LOCK,
  lockedFiles,
  lockHashes,
  releasedEntries,
  resolveBase,
  untrackedImporterFiles,
  type LockEntry,
} from "../../scripts/authority/lock-hashes.js";

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

  it("hashes what git tracks, and nothing the importer could load is untracked", () => {
    // A file only on this machine (an editor's, the OS's) would change a hash CI cannot
    // reproduce; a source file not yet added would be loaded but not hashed.
    expect(untrackedImporterFiles(), "git add or remove these, then lock").toEqual([]);
    const files = lockedFiles();
    expect(files).toContain("src/authority/import.ts");
    expect(files).toContain("src/authority/t/transform.ts");
    expect(files).toContain("src/fidelity/xhtml.ts");
    expect(files).not.toContain(LOCK);
  });

  it("keeps every entry ever released", () => {
    // What was released: every importer lock in the first-parent history of LOCK_BASE (in CI,
    // scripts/ci/lock-base.sh; locally origin/main), read as `npm run authority:lock` reads it.
    const base = process.env.LOCK_BASE ?? "origin/main";
    const commit = resolveBase(base);
    if (process.env.CI === "true") {
      expect(commit, "CI must name the lock's base").toBeDefined();
    }
    if (commit === undefined) return;
    const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Record<string, LockEntry>;
    for (const released of releasedEntries(commit)) {
      expect(
        lock[released.version],
        `importer ${released.version} was released (${released.commit}); change IMPORTER_VERSION`,
      ).toEqual(released.entry);
    }
  });
});
