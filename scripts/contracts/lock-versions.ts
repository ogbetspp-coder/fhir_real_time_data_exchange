import { writeFileSync } from "node:fs";

import { resolveBase } from "../authority/lock-hashes.js";
import {
  VERSIONS_LOCK,
  VersionsLockError,
  currentStructures,
  publishedHistory,
  readVersionsLock,
  recordExists,
  releasedLocks,
  updateLock,
} from "./versions-lock.js";

// Records each contract version's structure hashes in contracts/versions.lock.json
// (./versions-lock.ts says what the lock is, and updateLock what this may change).
//
//   npm run contracts:lock -- --record docs/validation/changes/<date>-<name>.md
//   npm run contracts:lock -- --record <change record> --respelling
//
// A version main never published is (re)locked to what it publishes now, naming the change
// record. A version main published whose schema changed is refused: a changed schema is a new
// version (ADR 0002). With `--respelling`, the new structure is appended instead, and only when
// nothing but `pattern` values differs from the schema main last published for it. It reads main's
// history (LOCK_BASE, or origin/main) and fails closed without it.

function fail(message: string): never {
  console.error(message);
  process.exit(1);
}

const args = process.argv.slice(2);
const recordAt = args.indexOf("--record");
const record = recordAt === -1 ? undefined : args[recordAt + 1];
if (record === undefined || !recordExists(record)) {
  fail("name the change record: --record docs/validation/changes/<date>-<name>.md (it must exist)");
}

const base = process.env.LOCK_BASE ?? "origin/main";
const commit = resolveBase(base) ?? fail(`${base} names no commit here; fetch main, then lock`);

try {
  const { lock, changed } = updateLock({
    lock: readVersionsLock(),
    history: publishedHistory(commit),
    current: currentStructures(),
    released: releasedLocks(commit).map(({ lock: released }) => released),
    record,
    respelling: args.includes("--respelling"),
  });
  writeFileSync(VERSIONS_LOCK, `${JSON.stringify(lock, null, 2)}\n`);
  console.log(changed.length === 0 ? "the lock holds every version" : changed.join("\n"));
} catch (error) {
  if (error instanceof VersionsLockError) fail(error.message);
  throw error;
}
