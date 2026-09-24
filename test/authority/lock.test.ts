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
});
