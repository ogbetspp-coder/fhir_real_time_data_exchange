import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

// What the importer's lock records (docs/design/authority-import-contract.md, D10): the hash of
// every file under src/authority (code and data, the lock itself excepted) and the fidelity files T
// reads with, and of its vectors.

export const LOCK = "src/authority/importer.lock.json";
export const VECTORS = "test/fixtures/authority/vectors.json";

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const full = path.join(directory, entry.name);
      return entry.isDirectory() ? files(full) : [full];
    })
    .filter((file) => file !== LOCK)
    .sort();
}

export type LockEntry = { sourceSha256: string; vectorsSha256: string };

// T reads with the fidelity scanner's own tokens, entities, list markers and invisible code points
// (docs/design/authority-import-t.md, T1), so a change there changes the importer too.
const SHARED = ["src/fidelity/normalize.ts", "src/fidelity/xhtml.ts"];

export function lockHashes(): LockEntry {
  const source = createHash("sha256");
  for (const file of [...files("src/authority"), ...SHARED]) {
    source.update(`${file}\0`);
    source.update(readFileSync(file));
    source.update("\0");
  }
  return {
    sourceSha256: source.digest("hex"),
    vectorsSha256: createHash("sha256").update(readFileSync(VECTORS)).digest("hex"),
  };
}
