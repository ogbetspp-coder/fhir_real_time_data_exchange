import { createHash } from "node:crypto";

// Keys are ordered by UTF-16 code units (RFC 8785 canonical JSON), never by locale: every hash in
// the system must be reproducible on any host and in any language re-implementation.
function compareKeys(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

// JSON.stringify omits these as object members and writes them as null as array elements.
function isOmittedMember(child: unknown): boolean {
  return child === undefined || typeof child === "function" || typeof child === "symbol";
}

// The canonical string is built directly, never by sorting keys into a JavaScript object and
// serialising that: a JavaScript object re-emits integer-like keys ("2", "10") first and in
// numeric order whatever the insertion order, which silently discards the sort and yields a
// hash no other language reproduces. Scalars and strings go through JSON.stringify so their
// formatting (numbers, escapes, lone surrogates as \udXXX) is exactly its own.
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((child) => canonicalJson(child)).join(",")}]`;
  }

  if (value !== null && typeof value === "object") {
    const members = Object.entries(value as Record<string, unknown>)
      .filter(([, child]) => !isOmittedMember(child))
      .sort(([left], [right]) => compareKeys(left, right))
      .map(([key, child]) => `${JSON.stringify(key)}:${canonicalJson(child)}`);
    return `{${members.join(",")}}`;
  }

  // JSON.stringify is typed as always returning a string; it returns undefined for these, and
  // an array element of that kind is written as null.
  if (isOmittedMember(value)) return "null";
  return JSON.stringify(value);
}

export function sha256(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

// Digest of the raw UTF-8 bytes of a string. `sha256()` hashes the canonical JSON encoding,
// so it would hash `"abc"` with its quotes; span and narrative hashes must use this instead.
export function sha256Utf8(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function stableUuid(namespace: string, value: string): string {
  const digest = createHash("sha256").update(`${namespace}:${value}`).digest("hex");
  return [
    digest.slice(0, 8),
    digest.slice(8, 12),
    `5${digest.slice(13, 16)}`,
    `a${digest.slice(17, 20)}`,
    digest.slice(20, 32),
  ].join("-");
}
