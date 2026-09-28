import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

import { CONTRACTS } from "../../src/contracts/index.js";
import { publishedSchema, structureSha256 } from "../../src/contracts/json-schema.js";

// The contract version lock (audit C-6; ADR 0002, "Versioning"). For every published contract
// version it records the structure hash of the schema that version names: the SHA-256 of the
// published document without its `$id` (src/contracts/json-schema.ts, structureSha256). A version
// names one structure. The one exception, ADR 0002's amendment of 2026-09-28, is a re-spelling
// that accepts the same documents under the schema's declared dialect and says the same things of
// them; it is appended to its version's list, naming the change record that argues it, and never
// replaces what was there. So a list only grows, and the test (test/contracts/versions-lock.test.ts)
// holds every list main has released to be a prefix of the one checked in.

export const VERSIONS_LOCK = "contracts/versions.lock.json";

// A structure a version was published with, and the change record under docs/validation/changes/
// the entry was made under: the one that introduced the version, or argued the re-spelling, or,
// for a version already published when the lock began, the record that began it.
export type LockedStructure = { sha256: string; record: string };

// name -> version -> the structures that version has been published with, oldest first.
export type VersionsLock = Record<string, Record<string, LockedStructure[]>>;

export function readVersionsLock(text: string = readFileSync(VERSIONS_LOCK, "utf8")): VersionsLock {
  return JSON.parse(text) as VersionsLock;
}

// Each published contract's name, version and current structure hash.
export function currentStructures(): { name: string; version: string; sha256: string }[] {
  return CONTRACTS.map((contract) => ({
    name: contract.name,
    version: contract.version,
    sha256: structureSha256(publishedSchema(contract)),
  }));
}

export function recordExists(record: string): boolean {
  return /^docs\/validation\/changes\/[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9.-]+\.md$/.test(record)
    ? existsSync(record)
    : false;
}

function git(args: string[]): string {
  return execFileSync("git", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    maxBuffer: 64 * 1024 * 1024,
  });
}

// Every lock in the first-parent history of `commit` (scripts/ci/lock-base.sh names it in CI; the
// importer lock reads main the same way, scripts/authority/lock-hashes.ts). A commit whose tree
// has no lock holds none; a shallow clone, whose history is cut short, throws.
export function releasedLocks(commit: string): { commit: string; lock: VersionsLock }[] {
  if (git(["rev-parse", "--is-shallow-repository"]).trim() === "true") {
    throw new Error("the contract version lock reads main's whole history; fetch it");
  }
  return git(["log", "--first-parent", "--format=%H", commit, "--", VERSIONS_LOCK])
    .split("\n")
    .filter((line) => line.length > 0)
    .flatMap((released) =>
      git(["ls-tree", "--name-only", released, "--", VERSIONS_LOCK]).trim() === ""
        ? []
        : [
            {
              commit: released,
              lock: readVersionsLock(git(["show", `${released}:${VERSIONS_LOCK}`])),
            },
          ],
    );
}
