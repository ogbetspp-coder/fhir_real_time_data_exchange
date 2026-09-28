import { describe, expect, it } from "vitest";

import { canonicalJson, sha256, sha256Utf8 } from "../src/lib/hash.js";

// canonicalJson is the definition of every hash in the system (spec section 1: RFC 8785 key
// order, JSON.stringify formatting). A second-language implementation must reproduce it byte for
// byte, so these cases pin the places where a JavaScript object would quietly disagree with it.

describe("canonical JSON", () => {
  it("orders integer-like keys by UTF-16 code units, not by numeric value", () => {
    // A JavaScript object would re-emit "2" and "10" first and in numeric order.
    expect(canonicalJson({ b: 1, "10": 2, "2": 3 })).toBe('{"10":2,"2":3,"b":1}');
  });

  it("orders keys by UTF-16 code units, not by code point", () => {
    // U+1F600 is the surrogate pair D83D DE00; its first unit sorts before U+FF5E's FF5E,
    // although its code point is larger. Code-point order would put "～" first.
    expect(canonicalJson({ "～": 1, "😀": 2 })).toBe('{"😀":2,"～":1}');
  });

  it("omits undefined members and writes undefined elements as null, like JSON.stringify", () => {
    expect(canonicalJson({ a: undefined, b: 1 })).toBe('{"b":1}');
    expect(canonicalJson([undefined, 1])).toBe("[null,1]");
  });

  it("delegates scalar formatting to JSON.stringify", () => {
    expect(canonicalJson(1e21)).toBe("1e+21");
    expect(canonicalJson(Number.NaN)).toBe("null");
    expect(canonicalJson("a b")).toBe(JSON.stringify("a b"));
    // A lone surrogate is written as an escape, never as a raw code unit.
    expect(canonicalJson("\uD800")).toBe('"\\ud800"');
  });

  it("agrees with JSON.stringify on already-sorted plain data", () => {
    const data = { a: [1, { b: "c", d: null }], e: "f" };
    expect(canonicalJson(data)).toBe(JSON.stringify(data));
  });

  it("sorts at every depth", () => {
    expect(canonicalJson({ z: { y: 1, x: 2 }, a: [{ c: 1, b: 2 }] })).toBe(
      '{"a":[{"b":2,"c":1}],"z":{"x":2,"y":1}}',
    );
  });

  it("writes a value nested far deeper than the call stack would allow a recursive walk", () => {
    // A request body of a few kilobytes of brackets reaches this depth; the hash of its
    // arguments is what the audit record carries, so it must be takeable.
    let nested: unknown = 1;
    for (let depth = 0; depth < 100_000; depth += 1)
      nested = depth % 2 === 0 ? [nested] : { k: nested };
    const written = canonicalJson(nested);
    expect(written.length).toBe(400_001);
    expect(written.startsWith('{"k":[{"k":[')).toBe(true);
    expect(written.endsWith("]}]}")).toBe(true);
  });

  it("refuses a value that contains itself, and writes one reached twice", () => {
    // No JSON form exists for either cycle; the recursive walk met one as a RangeError, and an
    // iterative walk that did not keep track would loop until memory ran out.
    const loop: Record<string, unknown> = { a: 1 };
    loop.self = loop;
    expect(() => canonicalJson(loop)).toThrow(TypeError);
    const ring: unknown[] = [1];
    ring.push([ring]);
    expect(() => sha256(ring)).toThrow(TypeError);

    // The same object under two keys is not a cycle, and is written each time.
    const shared = { z: 1 };
    expect(canonicalJson({ b: shared, a: [shared, shared] })).toBe(
      '{"a":[{"z":1},{"z":1}],"b":{"z":1}}',
    );
  });

  it("writes a hole in a sparse array as nothing, as the recursive form did", () => {
    // Not JSON, and never produced by parsing JSON: pinned so that the iterative walk is
    // byte-identical to the recursive one on every input, not only on JSON.
    // eslint-disable-next-line no-sparse-arrays
    expect(canonicalJson([1, , 3])).toBe("[1,,3]");
  });

  it("hashes canonical JSON for values and raw UTF-8 for spans", () => {
    expect(sha256("abc")).not.toBe(sha256Utf8("abc"));
    expect(sha256({ b: 1, a: 2 })).toBe(sha256({ a: 2, b: 1 }));
  });
});
