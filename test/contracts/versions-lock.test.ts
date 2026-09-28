import { describe, expect, it } from "vitest";
import { z } from "zod";

import { resolveBase } from "../../scripts/authority/lock-hashes.js";
import {
  currentStructures,
  readVersionsLock,
  recordExists,
  releasedLocks,
} from "../../scripts/contracts/versions-lock.js";
import { CONTRACTS } from "../../src/contracts/index.js";
import { publishedSchema, structureSha256 } from "../../src/contracts/json-schema.js";

// A contract version names one schema (ADR 0002, "Versioning"; audit C-6). Until 2026-09-28
// nothing held it to that: `ingestion-provenance` took a required `kind` and a union under the
// `$id` of 1.0.0, and only the index's content hash moved. contracts/versions.lock.json records
// each version's structure hash; a changed schema under a recorded version fails here.

describe("the contract version lock", () => {
  it("records every published contract at its version with the structure it publishes", () => {
    const lock = readVersionsLock();
    for (const { name, version, sha256 } of currentStructures()) {
      const entries = lock[name]?.[version];
      expect(entries, `run npm run contracts:lock for ${name}@${version}`).toBeDefined();
      expect(
        entries?.at(-1)?.sha256,
        `${name}@${version} publishes a schema its version was not locked to: a changed schema is a new version`,
      ).toBe(sha256);
    }
  });

  it("names a change record that exists for every entry", () => {
    for (const versions of Object.values(readVersionsLock())) {
      for (const entries of Object.values(versions)) {
        for (const { record } of entries)
          expect([record, recordExists(record)]).toEqual([record, true]);
      }
    }
  });

  it("hashes the structure, not the name and version the $id restates", () => {
    const [contract] = CONTRACTS;
    if (contract === undefined) throw new Error("no contract");
    const document = publishedSchema(contract);
    expect(
      structureSha256({ ...document, $id: "https://khs.dev/contracts/x/9.9.9/schema.json" }),
    ).toBe(structureSha256(document));
    expect(structureSha256({ ...document, description: "changed" })).not.toBe(
      structureSha256(document),
    );
  });

  // The failure the lock exists for: the same version, a different schema.
  it("sees a changed schema under an unchanged version", () => {
    const [contract] = CONTRACTS;
    if (contract === undefined) throw new Error("no contract");
    const changed = { ...contract, schema: z.strictObject({ extra: z.string() }) };
    const lock = readVersionsLock();
    expect(lock[contract.name]?.[contract.version]?.at(-1)?.sha256).not.toBe(
      structureSha256(publishedSchema(changed)),
    );
  });

  it("keeps every entry main has released", () => {
    // Every lock in the first-parent history of LOCK_BASE (scripts/ci/lock-base.sh; locally
    // origin/main), read as the importer lock's test reads its own.
    const base = process.env.LOCK_BASE ?? "origin/main";
    const commit = resolveBase(base);
    if (process.env.CI === "true") expect(commit, "CI must name the lock's base").toBeDefined();
    if (commit === undefined) return;
    const lock = readVersionsLock();
    for (const { commit: released, lock: old } of releasedLocks(commit)) {
      for (const [name, versions] of Object.entries(old)) {
        for (const [version, entries] of Object.entries(versions)) {
          expect(
            lock[name]?.[version]?.slice(0, entries.length),
            `${name}@${version} was released (${released}); its entries only grow`,
          ).toEqual(entries);
        }
      }
    }
  });
});
