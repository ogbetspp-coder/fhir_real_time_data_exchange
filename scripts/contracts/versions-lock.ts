import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

import { CONTRACTS } from "../../src/contracts/index.js";
import { publishedSchema, structureSha256 } from "../../src/contracts/json-schema.js";

// The contract version lock (audit C-6; ADR 0002, "Versioning"). For every contract version ever
// published it records the structure hash of each schema that version was published with: the
// SHA-256 of the published document without its `$id` (src/contracts/json-schema.ts,
// structureSha256), oldest first. From this change on a version names one structure. The one
// exception, ADR 0002's amendment of 2026-09-28, is a re-spelling: the same schema with nothing
// changed but the spelling of `pattern` values, accepting the same documents under the schema's
// declared dialect; it is appended to its version's list, naming the change record that argues it.
// Nothing released is ever replaced, and the test (test/contracts/versions-lock.test.ts) holds
// every list main has released to be a prefix of the one checked in, and every structure main has
// published to be in the lock.
//
// Before the lock existed some versions were published with more than one structure
// (`ingestion-provenance` 1.0.0 with four, and every version that #145 respelt); the lock records
// them all, as main published them, so that a retired version number can never be published again
// with a schema it was not published with (review of #148, part A L2).

export const VERSIONS_LOCK = "contracts/versions.lock.json";
const GENERATED = "contracts/generated";

// A structure a version was published with, and the change record under docs/validation/changes/
// the entry was made under: the one that introduced the version, or argued the re-spelling, or,
// for a structure main published before the lock began, the record that began it.
export type LockedStructure = { sha256: string; record: string };

// name -> version -> the structures that version has been published with, oldest first.
export type VersionsLock = Record<string, Record<string, LockedStructure[]>>;

// A contract version's published document and its structure hash.
export type Structure = { name: string; version: string; sha256: string; document: unknown };

export function readVersionsLock(text: string = readFileSync(VERSIONS_LOCK, "utf8")): VersionsLock {
  return JSON.parse(text) as VersionsLock;
}

// Each published contract as this checkout generates it.
export function currentStructures(): Structure[] {
  return CONTRACTS.map((contract) => {
    const document = publishedSchema(contract);
    return {
      name: contract.name,
      version: contract.version,
      sha256: structureSha256(document),
      document,
    };
  });
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

function requireFullHistory(): void {
  if (git(["rev-parse", "--is-shallow-repository"]).trim() === "true") {
    throw new Error("the contract version lock reads main's whole history; fetch it");
  }
}

function lines(output: string): string[] {
  return output.split("\n").filter((line) => line.length > 0);
}

// Every lock in the first-parent history of `commit` (scripts/ci/lock-base.sh names it in CI; the
// importer lock reads main the same way, scripts/authority/lock-hashes.ts). A commit whose tree
// has no lock holds none; a shallow clone, whose history is cut short, throws.
export function releasedLocks(commit: string): { commit: string; lock: VersionsLock }[] {
  requireFullHistory();
  return lines(git(["log", "--first-parent", "--format=%H", commit, "--", VERSIONS_LOCK])).flatMap(
    (released) =>
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

// The name and version a published document's `$id` gives it.
export function identify(document: unknown): { name: string; version: string } {
  const id = (document as { $id?: unknown } | null)?.$id;
  const match =
    typeof id === "string"
      ? /^https:\/\/khs\.dev\/contracts\/([a-z0-9-]+)\/([0-9]+\.[0-9]+\.[0-9]+)\/schema\.json$/.exec(
          id,
        )
      : null;
  if (match?.[1] === undefined || match[2] === undefined) {
    throw new Error("a published schema's $id names no contract version");
  }
  return { name: match[1], version: match[2] };
}

// Every structure each contract version was published with in the first-parent history of
// `commit`, in the order main first published them, each once.
export function publishedHistory(commit: string): Structure[] {
  requireFullHistory();
  const found: Structure[] = [];
  const commits = lines(
    git(["log", "--first-parent", "--reverse", "--format=%H", commit, "--", GENERATED]),
  );
  for (const published of commits) {
    const files = lines(git(["ls-tree", "--name-only", `${published}:${GENERATED}`])).filter(
      (file) => file.endsWith(".schema.json"),
    );
    for (const file of files.sort()) {
      const document = JSON.parse(git(["show", `${published}:${GENERATED}/${file}`])) as unknown;
      const { name, version } = identify(document);
      const sha256 = structureSha256(document as Record<string, unknown>);
      const known = (seen: Structure): boolean =>
        seen.name === name && seen.version === version && seen.sha256 === sha256;
      if (!found.some(known)) {
        found.push({ name, version, sha256, document });
      }
    }
  }
  return found;
}

// Where `after` differs from `before` in anything but the value of a `pattern` (the `$id` aside):
// a keyword added, removed or changed, a description, a bound, a type. Paths only, never values.
// Empty for a re-spelling (ADR 0002, amendment of 2026-09-28); whether each respelt pattern
// accepts the same language is the change record's argument, not something this can decide.
export function respellingIssues(before: unknown, after: unknown, at = "$"): string[] {
  if (Array.isArray(before) && Array.isArray(after)) {
    if (before.length !== after.length) return [`${at} has another length`];
    return before.flatMap((item, index) => respellingIssues(item, after[index], `${at}[${index}]`));
  }
  const isObject = (value: unknown): value is Record<string, unknown> =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  if (isObject(before) && isObject(after)) {
    const keys = (value: Record<string, unknown>): string[] =>
      Object.keys(value)
        .filter((key) => !(at === "$" && key === "$id"))
        .sort();
    const left = keys(before);
    const right = keys(after);
    if (JSON.stringify(left) !== JSON.stringify(right)) return [`${at} has other keywords`];
    return left.flatMap((key) =>
      key === "pattern" && typeof before[key] === "string" && typeof after[key] === "string"
        ? []
        : respellingIssues(before[key], after[key], `${at}.${key}`),
    );
  }
  return before === after ? [] : [`${at} changed`];
}

export class VersionsLockError extends Error {}

export type LockUpdate = {
  // The lock as checked in.
  lock: VersionsLock;
  // What main has published (publishedHistory), oldest first.
  history: Structure[];
  // What this checkout publishes (currentStructures).
  current: Structure[];
  // Every lock main has released (releasedLocks).
  released: VersionsLock[];
  record: string;
  respelling: boolean;
};

// The lock with every published and current structure in it, or the reason it cannot be:
//
// - a structure main published is added where missing, in the order main published it;
// - a version main never published names whatever it names now: its entries are replaced;
// - a version main published whose schema changed is refused, unless `respelling`, and then only
//   when the change from the last structure main published for it is a re-spelling
//   (respellingIssues), when the new structure is appended after the published ones;
// - an entry main released is never changed or dropped.
export function updateLock(update: LockUpdate): { lock: VersionsLock; changed: string[] } {
  const lock = structuredClone(update.lock);
  const changed: string[] = [];
  const entriesOf = (name: string, version: string): LockedStructure[] =>
    ((lock[name] ??= {})[version] ??= []);

  for (const { name, version, sha256 } of update.history) {
    const entries = entriesOf(name, version);
    if (!entries.some((entry) => entry.sha256 === sha256)) {
      entries.push({ sha256, record: update.record });
      changed.push(`${name}@${version} published structure ${sha256.slice(0, 12)} recorded`);
    }
  }

  for (const { name, version, sha256, document } of update.current) {
    const entries = entriesOf(name, version);
    if (entries.at(-1)?.sha256 === sha256) continue;
    const published = update.history.filter(
      (structure) => structure.name === name && structure.version === version,
    );
    // What main published, kept; anything only this branch locked, dropped.
    const kept = entries.filter((entry) =>
      published.some(({ sha256: seen }) => seen === entry.sha256),
    );
    const base = published.at(-1);
    if (base !== undefined && base.sha256 !== sha256) {
      if (!update.respelling) {
        throw new VersionsLockError(
          `${name}@${version} was published with another schema: a changed schema is a new version (ADR 0002); a re-spelling is appended with --respelling`,
        );
      }
      const issues = respellingIssues(base.document, document);
      if (issues.length > 0) {
        throw new VersionsLockError(
          `${name}@${version} is not a re-spelling of the schema main published: ${issues.join(", ")}`,
        );
      }
    }
    lock[name] = {
      ...lock[name],
      [version]: base?.sha256 === sha256 ? kept : [...kept, { sha256, record: update.record }],
    };
    if (base === undefined) changed.push(`${name}@${version} locked`);
    else if (base.sha256 === sha256)
      changed.push(`${name}@${version} restored to what main published`);
    else changed.push(`${name}@${version} re-spelling appended`);
  }

  for (const released of update.released) {
    for (const [name, versions] of Object.entries(released)) {
      for (const [version, entries] of Object.entries(versions)) {
        const now = lock[name]?.[version] ?? [];
        if (entries.some((entry, index) => now[index]?.sha256 !== entry.sha256)) {
          throw new VersionsLockError(
            `${name}@${version}: an entry main released was changed; restore it`,
          );
        }
      }
    }
  }

  const sorted = Object.fromEntries(
    Object.entries(lock)
      .sort(([left], [right]) => (left < right ? -1 : 1))
      .map(([name, versions]) => [
        name,
        Object.fromEntries(
          Object.entries(versions).sort(([left], [right]) => compare(left, right)),
        ),
      ]),
  );
  return { lock: sorted, changed };
}

// Versions in numeric order: 1.1.0 before 1.10.0 before 2.0.0.
function compare(left: string, right: string): number {
  const a = left.split(".").map(Number);
  const b = right.split(".").map(Number);
  for (let index = 0; index < 3; index += 1) {
    const difference = (a[index] ?? 0) - (b[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}
