import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

import { IMPORTER_VERSION } from "../../src/authority/import.js";
import { LOCK, lockHashes, type LockEntry } from "./lock-hashes.js";

// Records the importer's version with the hashes of its code, data and vectors
// (docs/design/authority-import-contract.md, D10). It refuses to change an entry main already
// has: a change of behaviour or data on main comes with a new IMPORTER_VERSION. The test
// test/authority/lock.test.ts fails whenever the recorded entry is stale.

type Lock = Record<string, LockEntry>;

const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Lock;
let released: Lock = {};
try {
  released = JSON.parse(
    execFileSync("git", ["show", `origin/main:${LOCK}`], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }),
  ) as Lock;
} catch {
  // main has no lock yet
}
if (released[IMPORTER_VERSION] !== undefined) {
  console.error(`importer ${IMPORTER_VERSION} is on main already; change IMPORTER_VERSION`);
  process.exit(1);
}
lock[IMPORTER_VERSION] = lockHashes();
writeFileSync(LOCK, `${JSON.stringify(lock, null, 2)}\n`);
console.log(`locked importer ${IMPORTER_VERSION}`);
