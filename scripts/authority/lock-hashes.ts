import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

// What the importer's lock records (docs/design/authority-import-contract.md, D10): the hash of
// every file git tracks under src/authority (code and data, the lock itself excepted) and the
// fidelity files T reads with, and of its vectors; and what main has released.

export const LOCK = "src/authority/importer.lock.json";
export const VECTORS = "test/fixtures/authority/vectors.json";

// T reads with the fidelity scanner's own tokens, entities, list markers and invisible code points
// (docs/design/authority-import-t.md, T1), so a change there changes the importer too.
const SHARED = ["src/fidelity/normalize.ts", "src/fidelity/xhtml.ts"];

// git's output; any failure throws (the lock fails closed on a git it cannot read).
function git(args: string[], cwd?: string): string {
  return execFileSync("git", args, {
    cwd,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    maxBuffer: 64 * 1024 * 1024,
  });
}

function nulSeparated(output: string): string[] {
  return output.split("\0").filter((path) => path.length > 0);
}

// The files the lock hashes, in order: what git tracks under src/authority, so a file only on one
// machine (an editor's or the OS's) never changes the hash, and the shared fidelity files.
export function lockedFiles(): string[] {
  const tracked = nulSeparated(git(["ls-files", "-z", "--", "src/authority"]))
    .filter((file) => file !== LOCK)
    .sort();
  return [...tracked, ...SHARED];
}

// Files under src/authority git does not track and does not ignore: the importer could load one,
// and the lock would not hash it, so the test and `authority:lock` refuse while any exists.
export function untrackedImporterFiles(): string[] {
  return nulSeparated(
    git(["ls-files", "-z", "--others", "--exclude-standard", "--", "src/authority"]),
  ).sort();
}

export type LockEntry = { sourceSha256: string; vectorsSha256: string };

export function lockHashes(): LockEntry {
  const source = createHash("sha256");
  for (const file of lockedFiles()) {
    source.update(`${file}\0`);
    source.update(readFileSync(file));
    source.update("\0");
  }
  return {
    sourceSha256: source.digest("hex"),
    vectorsSha256: createHash("sha256").update(readFileSync(VECTORS)).digest("hex"),
  };
}

// The commit `base` names, or undefined where it names none (no such ref here: `rev-parse
// --verify --quiet` exits 1 and says nothing). Any other failure throws. `cwd` is for tests.
export function resolveBase(base: string, cwd?: string): string | undefined {
  try {
    return git(["rev-parse", "--verify", "--quiet", `${base}^{commit}`], cwd).trim();
  } catch (error) {
    const { status, stderr } = error as { status?: unknown; stderr?: unknown };
    if (status === 1 && (stderr ?? "") === "") return undefined;
    throw error;
  }
}

// Every importer version entry released: each lock in the first-parent history of `commit` (in CI,
// scripts/ci/lock-base.sh: the commit before a push, main otherwise; locally origin/main). Reading
// the whole history, not one commit, means neither a second push nor a manual run after a changed
// released entry can compare with the change itself. A commit whose tree has no lock (the one that
// deleted it, if any) holds none; any other git failure throws, and so does a shallow clone, whose
// history is cut short.
export function releasedEntries(
  commit: string,
): { version: string; entry: LockEntry; commit: string }[] {
  if (git(["rev-parse", "--is-shallow-repository"]).trim() === "true") {
    throw new Error(
      "the importer lock reads main's whole history; fetch it (git fetch --unshallow)",
    );
  }
  const commits = git(["log", "--first-parent", "--format=%H", commit, "--", LOCK])
    .split("\n")
    .filter((line) => line.length > 0);
  return commits.flatMap((released) => {
    if (git(["ls-tree", "--name-only", released, "--", LOCK]).trim() === "") return [];
    const lock = JSON.parse(git(["show", `${released}:${LOCK}`])) as Record<string, LockEntry>;
    return Object.entries(lock).map(([version, entry]) => ({ version, entry, commit: released }));
  });
}
