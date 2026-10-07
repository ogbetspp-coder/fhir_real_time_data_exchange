import { readFileSync, writeFileSync } from "node:fs";

import { IMPORTER_VERSION } from "../../src/certified-word/import.js";
import {
  CERTIFIED_WORD_LOCK,
  lockHashes,
  releasedEntries,
  resolveBase,
  untrackedImporterFiles,
  type LockEntry,
} from "../authority/lock-hashes.js";

// Records the certified Word importer's version with the hashes of its code and of its golden
// vectors (src/certified-word/importer.lock.json), as `npm run authority:lock` does for the
// authority importer: an entry main has released is never changed, so a change of what the
// importer makes comes with a new IMPORTER_VERSION, and so a new extractor token. It fails closed
// without main's history (LOCK_BASE, or origin/main) or with a file under src/certified-word git
// does not track. test/certified-word/lock.test.ts fails whenever the entry is stale.

function fail(message: string): never {
  console.error(message);
  process.exit(1);
}

const untracked = untrackedImporterFiles(CERTIFIED_WORD_LOCK);
if (untracked.length > 0) {
  fail(`git does not track ${untracked.join(", ")}; add or remove it, then lock`);
}
const base = process.env.LOCK_BASE ?? "origin/main";
const commit = resolveBase(base) ?? fail(`${base} names no commit here; fetch main, then lock`);
const released = releasedEntries(commit, CERTIFIED_WORD_LOCK).find(
  ({ version }) => version === IMPORTER_VERSION,
);
if (released !== undefined) {
  fail(
    `certified Word importer ${IMPORTER_VERSION} is on main already (${released.commit}); change IMPORTER_VERSION`,
  );
}
let lock: Record<string, LockEntry> = {};
try {
  lock = JSON.parse(readFileSync(CERTIFIED_WORD_LOCK.lock, "utf8")) as Record<string, LockEntry>;
} catch (error) {
  if ((error as { code?: unknown }).code !== "ENOENT") throw error;
}
lock[IMPORTER_VERSION] = lockHashes(CERTIFIED_WORD_LOCK);
writeFileSync(CERTIFIED_WORD_LOCK.lock, `${JSON.stringify(lock, null, 2)}\n`);
console.log(`locked certified Word importer ${IMPORTER_VERSION}`);
