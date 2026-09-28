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
//
// The walk is iterative, over an explicit stack, rather than recursive: a value nested a few
// thousand levels deep — six kilobytes of brackets in a request body — would otherwise exhaust
// the call stack with a RangeError, and a hash that cannot be taken is an audit record that
// cannot be written. The string is the one the recursive form wrote, for every value; only the
// depth it can reach changed.
//
// A value that contains itself has no JSON form. The recursive walk met one as a RangeError; this
// one would loop until memory ran out, so it keeps the containers it is inside and refuses a
// repeat with a TypeError, as JSON.stringify does. A container reached twice by different paths
// (not inside itself) is written twice, as before.
const VALUE = 0;
const TEXT = 1;
// Closes the container on the pending stack: writes its bracket and leaves it.
const CLOSE = 2;

export function canonicalJson(value: unknown): string {
  let out = "";
  // Two parallel stacks, popped last in, first out, so each container pushes its closing mark
  // first and its first child last: what to write next, and what kind of entry it is — a value
  // still to be written, literal text (a comma, a key), or a container to close.
  const pending: unknown[] = [value];
  const kind: number[] = [VALUE];
  const inside = new Set<object>();
  while (pending.length > 0) {
    const current = pending.pop();
    const entry = kind.pop();
    if (entry === TEXT) {
      out += current as string;
      continue;
    }
    if (entry === CLOSE) {
      inside.delete(current as object);
      out += Array.isArray(current) ? "]" : "}";
      continue;
    }

    if (current !== null && typeof current === "object") {
      if (inside.has(current)) throw new TypeError("canonicalJson: the value contains itself");
      inside.add(current);
      pending.push(current);
      kind.push(CLOSE);
    }

    if (Array.isArray(current)) {
      out += "[";
      for (let index = current.length - 1; index >= 0; index -= 1) {
        // A hole in a sparse array writes nothing, as Array.prototype.map left it.
        if (index in current) {
          pending.push(current[index]);
          kind.push(VALUE);
        }
        if (index > 0) {
          pending.push(",");
          kind.push(TEXT);
        }
      }
      continue;
    }

    if (current !== null && typeof current === "object") {
      const members = Object.entries(current as Record<string, unknown>)
        .filter(([, child]) => !isOmittedMember(child))
        .sort(([left], [right]) => compareKeys(left, right));
      out += "{";
      for (let index = members.length - 1; index >= 0; index -= 1) {
        const [key, child] = members[index] ?? ["", undefined];
        pending.push(child, `${JSON.stringify(key)}:`);
        kind.push(VALUE, TEXT);
        if (index > 0) {
          pending.push(",");
          kind.push(TEXT);
        }
      }
      continue;
    }

    // JSON.stringify is typed as always returning a string; it returns undefined for these, and
    // an array element of that kind is written as null.
    out += isOmittedMember(current) ? "null" : JSON.stringify(current);
  }
  return out;
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
