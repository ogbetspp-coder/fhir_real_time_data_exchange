import { createHash } from "node:crypto";

// Keys are ordered by UTF-16 code units (RFC 8785 canonical JSON), never by locale: every hash in
// the system must be reproducible on any host and in any language re-implementation.
function compareKeys(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }

  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => compareKeys(left, right))
        .map(([key, child]) => [key, canonicalize(child)]),
    );
  }

  return value;
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
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
