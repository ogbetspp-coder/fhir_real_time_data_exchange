import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

import { CONTRACTS } from "../../src/contracts/index.js";
import { asciiDigits, publishedSchema, structureSha256 } from "../../src/contracts/json-schema.js";
import { canonicalJson } from "../../src/lib/hash.js";

// The contract version lock (audit C-6; ADR 0002, "Versioning"). For every contract version ever
// published it records the structure hash of each schema that version was published with: the
// SHA-256 of the published document without its `$id` (src/contracts/json-schema.ts,
// structureSha256), oldest first. From this change on a version names one structure. The one
// exception, ADR 0002's amendment of 2026-09-28, is a re-spelling: the same schema with nothing
// changed but the spelling of `pattern` keywords, each by a rewrite of a fixed table known to keep
// an ECMA-262 pattern's language (respellingIssues); it is appended to its version's list, naming
// the change record that records it.
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

// The keywords of JSON Schema 2020-12 whose value is a schema, an array of schemas, or a map of
// names to schemas. Only these are walked as schemas; every other keyword's value is data (`const`,
// `default`, `enum`, `examples`, `required`, a bound, a description) and is compared whole, so a
// `pattern` member of a `const` object is data, not a pattern (review of #148, round 2, L-1).
const SCHEMA_KEYWORDS = new Set([
  "additionalItems",
  "additionalProperties",
  "contains",
  "else",
  "if",
  "items",
  "not",
  "propertyNames",
  "then",
  "unevaluatedItems",
  "unevaluatedProperties",
]);
const SCHEMA_ARRAY_KEYWORDS = new Set(["allOf", "anyOf", "oneOf", "prefixItems"]);
const SCHEMA_MAP_KEYWORDS = new Set([
  "$defs",
  "definitions",
  "dependentSchemas",
  "patternProperties",
  "properties",
]);

// The re-spellings a pattern may undergo without a version change: a fixed table of rewrites
// each known to leave the language of an ECMA-262 pattern unchanged, applied to both sides before
// they are compared. Today it has one entry: `\d` as `[0-9]` outside a class and `0-9` inside one
// (asciiDigits, #145). A pattern that uses any other shorthand has no entry and is never a
// re-spelling. Two patterns that differ after the table are a change of language, whatever the
// change record argues: `[0-9]` to `[0-9a]` is refused, as is `^[0-9]+$` to `.*`.
function respelt(pattern: string): string | undefined {
  try {
    return asciiDigits(pattern);
  } catch {
    return undefined;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sameData(before: unknown, after: unknown): boolean {
  return canonicalJson(before) === canonicalJson(after);
}

// Where the schema `after` differs from `before` other than by a re-spelling of a `pattern`
// keyword (the root `$id` aside). Paths only, never values. Empty exactly when every difference is
// a `pattern` of a schema whose two spellings are equal after the table above: the rule of ADR
// 0002's amendment of 2026-09-28, made checkable.
export function respellingIssues(before: unknown, after: unknown, at = "$"): string[] {
  if (!isObject(before) || !isObject(after)) {
    return sameData(before, after) ? [] : [`${at} changed`];
  }
  const keys = (value: Record<string, unknown>): string[] =>
    Object.keys(value)
      .filter((key) => !(at === "$" && key === "$id"))
      .sort();
  const left = keys(before);
  if (JSON.stringify(left) !== JSON.stringify(keys(after))) return [`${at} has other keywords`];
  return left.flatMap((key): string[] => {
    const path = `${at}.${key}`;
    const [one, other] = [before[key], after[key]];
    if (key === "pattern" && typeof one === "string" && typeof other === "string") {
      const [a, b] = [respelt(one), respelt(other)];
      return a !== undefined && a === b ? [] : [`${path} is not a re-spelling`];
    }
    if (SCHEMA_KEYWORDS.has(key)) return respellingIssues(one, other, path);
    if (SCHEMA_ARRAY_KEYWORDS.has(key) && Array.isArray(one) && Array.isArray(other)) {
      if (one.length !== other.length) return [`${path} has another length`];
      return one.flatMap((item, index) =>
        respellingIssues(item, other[index], `${path}[${String(index)}]`),
      );
    }
    if (SCHEMA_MAP_KEYWORDS.has(key) && isObject(one) && isObject(other)) {
      const names = Object.keys(one).sort();
      if (JSON.stringify(names) !== JSON.stringify(Object.keys(other).sort())) {
        return [`${path} has other members`];
      }
      return names.flatMap((name) => respellingIssues(one[name], other[name], `${path}.${name}`));
    }
    return sameData(one, other) ? [] : [`${path} changed`];
  });
}

// Why `current` may not be published under its version, or undefined when it may: main published
// that version, last with another structure, and the change is not a re-spelling. Checked
// whatever the lock says, so an entry appended to the lock by hand passes nothing (review of #148,
// round 2, M-1).
export function unpublishedChange(current: Structure, history: Structure[]): string | undefined {
  const base = history
    .filter((structure) => structure.name === current.name && structure.version === current.version)
    .at(-1);
  if (base === undefined || base.sha256 === current.sha256) return undefined;
  const issues = respellingIssues(base.document, current.document);
  return issues.length === 0
    ? undefined
    : `${current.name}@${current.version} is not a re-spelling of the schema main published: ${issues.join(", ")}`;
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

  for (const current of update.current) {
    const { name, version, sha256 } = current;
    const entries = entriesOf(name, version);
    // Before anything the lock says: a structure appended to it by hand is checked as well.
    const refused = unpublishedChange(current, update.history);
    if (refused !== undefined) throw new VersionsLockError(refused);
    if (entries.at(-1)?.sha256 === sha256) continue;
    const published = update.history.filter(
      (structure) => structure.name === name && structure.version === version,
    );
    // What main published, kept; anything only this branch locked, dropped.
    const kept = entries.filter((entry) =>
      published.some(({ sha256: seen }) => seen === entry.sha256),
    );
    const base = published.at(-1);
    if (base !== undefined && base.sha256 !== sha256 && !update.respelling) {
      throw new VersionsLockError(
        `${name}@${version} was published with another schema: a re-spelling is appended with --respelling`,
      );
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
