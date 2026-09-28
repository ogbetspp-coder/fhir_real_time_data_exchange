import { writeFileSync } from "node:fs";

import { resolveBase } from "../authority/lock-hashes.js";
import {
  VERSIONS_LOCK,
  currentStructures,
  readVersionsLock,
  recordExists,
  releasedLocks,
} from "./versions-lock.js";

// Records each published contract version's structure hash in contracts/versions.lock.json
// (./versions-lock.ts says what the lock is).
//
//   npm run contracts:lock -- --record docs/validation/changes/<date>-<name>.md
//   npm run contracts:lock -- --record <change record> --respelling
//
// A version not yet in the lock is added, naming the change record that introduced it. A version
// whose schema changed is refused: a changed structure is a new version (ADR 0002). With
// `--respelling`, the new structure is appended instead, for a change the record argues is a
// re-spelling (the same documents accepted under the declared dialect, the same descriptions);
// nothing recorded is ever replaced, and a version main has released keeps every entry it had.

function fail(message: string): never {
  console.error(message);
  process.exit(1);
}

const args = process.argv.slice(2);
const recordAt = args.indexOf("--record");
const record = recordAt === -1 ? undefined : args[recordAt + 1];
const respelling = args.includes("--respelling");
if (record === undefined || !recordExists(record)) {
  fail("name the change record: --record docs/validation/changes/<date>-<name>.md (it must exist)");
}

const base = process.env.LOCK_BASE ?? "origin/main";
const commit = resolveBase(base) ?? fail(`${base} names no commit here; fetch main, then lock`);
const released = releasedLocks(commit);

const lock = readVersionsLock();
const changed: string[] = [];
for (const { name, version, sha256 } of currentStructures()) {
  const versions = (lock[name] ??= {});
  const entries = versions[version];
  if (entries === undefined) {
    versions[version] = [{ sha256, record }];
    changed.push(`${name}@${version} added`);
    continue;
  }
  if (entries.at(-1)?.sha256 === sha256) continue;
  if (!respelling) {
    fail(
      `${name}@${version} is locked to another schema: a changed schema is a new version (ADR 0002); a re-spelling is appended with --respelling`,
    );
  }
  entries.push({ sha256, record });
  changed.push(`${name}@${version} re-spelling appended`);
}

// Never needed by the loop above, which only appends; checked so that a hand edit is caught here
// as well as by the test.
for (const { commit: at, lock: old } of released) {
  for (const [name, versions] of Object.entries(old)) {
    for (const [version, entries] of Object.entries(versions)) {
      const now = lock[name]?.[version] ?? [];
      if (entries.some((entry, index) => now[index]?.sha256 !== entry.sha256)) {
        fail(`${name}@${version} was released with other entries (${at}); restore them`);
      }
    }
  }
}

const sorted = Object.fromEntries(
  Object.entries(lock)
    .sort(([left], [right]) => (left < right ? -1 : 1))
    .map(([name, versions]) => [name, versions]),
);
writeFileSync(VERSIONS_LOCK, `${JSON.stringify(sorted, null, 2)}\n`);
console.log(changed.length === 0 ? "the lock holds every version" : changed.join("\n"));
