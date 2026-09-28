import { readFileSync, writeFileSync } from "node:fs";

import { IMPORTER_VERSION } from "../../src/authority/import.js";
import {
  LOCK,
  lockHashes,
  releasedEntries,
  resolveBase,
  untrackedImporterFiles,
  type LockEntry,
} from "./lock-hashes.js";

// Records the importer's version with the hashes of its code, data and vectors
// (docs/design/authority-import-contract.md, D10). It refuses to change an entry main has ever
// released: a change of behaviour or data on main comes with a new IMPORTER_VERSION. It fails
// closed: without main's history (LOCK_BASE, or origin/main) or with a file under src/authority
// git does not track, it records nothing. The test test/authority/lock.test.ts fails whenever the
// recorded entry is stale.

function fail(message: string): never {
  console.error(message);
  process.exit(1);
}

const untracked = untrackedImporterFiles();
if (untracked.length > 0) {
  fail(`git does not track ${untracked.join(", ")}; add or remove it, then lock`);
}
const base = process.env.LOCK_BASE ?? "origin/main";
const commit = resolveBase(base) ?? fail(`${base} names no commit here; fetch main, then lock`);
const released = releasedEntries(commit).find(({ version }) => version === IMPORTER_VERSION);
if (released !== undefined) {
  fail(
    `importer ${IMPORTER_VERSION} is on main already (${released.commit}); change IMPORTER_VERSION`,
  );
}
const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Record<string, LockEntry>;
lock[IMPORTER_VERSION] = lockHashes();
writeFileSync(LOCK, `${JSON.stringify(lock, null, 2)}\n`);
console.log(`locked importer ${IMPORTER_VERSION}`);
