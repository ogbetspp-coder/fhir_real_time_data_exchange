import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { IMPORTER_VERSION } from "../../src/certified-word/import.js";
import {
  CERTIFIED_WORD_LOCK,
  lockedFiles,
  lockHashes,
  releasedEntries,
  resolveBase,
  untrackedImporterFiles,
  type LockEntry,
} from "../../scripts/authority/lock-hashes.js";

// A change to the certified Word importer's code or vectors must change its version, which the
// extractor token names with the recompute's versions (review of #193): one token, one behaviour.

const read = (): Record<string, LockEntry> =>
  JSON.parse(readFileSync(CERTIFIED_WORD_LOCK.lock, "utf8")) as Record<string, LockEntry>;

describe("the certified Word importer lock", () => {
  it("records this version with the hashes of its code and vectors", () => {
    expect(
      read()[IMPORTER_VERSION],
      "run npm run certified-word:lock after changing IMPORTER_VERSION",
    ).toEqual(lockHashes(CERTIFIED_WORD_LOCK));
  });

  it("hashes what git tracks, the shared readers included, and not the lock", () => {
    expect(untrackedImporterFiles(CERTIFIED_WORD_LOCK), "git add or remove these").toEqual([]);
    const files = lockedFiles(CERTIFIED_WORD_LOCK);
    expect(files).toContain("src/certified-word/import.ts");
    expect(files).toContain("src/certified-word/shape.ts");
    expect(files).toContain("src/authority/json.ts");
    expect(files).toContain("src/fidelity/xhtml.ts");
    expect(files).not.toContain(CERTIFIED_WORD_LOCK.lock);
    expect(files.filter((file) => file.startsWith("src/authority/"))).toEqual([
      "src/authority/json.ts",
    ]);
  });

  it("changes its hash when a vector changes", () => {
    const vectors = lockHashes(CERTIFIED_WORD_LOCK).vectorsSha256;
    expect(vectors).toMatch(/^[0-9a-f]{64}$/u);
    expect(vectors).not.toBe(lockHashes().vectorsSha256);
  });

  it("keeps every entry ever released", () => {
    const base = process.env.LOCK_BASE ?? "origin/main";
    const commit = resolveBase(base);
    if (process.env.CI === "true") {
      expect(commit, "CI must name the lock's base").toBeDefined();
    }
    if (commit === undefined) return;
    const lock = read();
    for (const released of releasedEntries(commit, CERTIFIED_WORD_LOCK)) {
      expect(
        lock[released.version],
        `certified Word importer ${released.version} was released (${released.commit}); change IMPORTER_VERSION`,
      ).toEqual(released.entry);
    }
  });
});
