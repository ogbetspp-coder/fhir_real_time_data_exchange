import { describe, expect, it } from "vitest";

import { MAX_JSON_NODES, jsonShapeIssues } from "../src/lib/json-shape.js";
import { sha256 } from "../src/lib/hash.js";

// These bounds exist so that untrusted JSON is refused before anything recursive or expensive
// touches it. A bound that is only reached *after* the expensive step would be no bound at all.

describe("json shape bounds", () => {
  it("accepts an ordinary document", () => {
    expect(jsonShapeIssues("doc", { a: [1, 2, { b: "c" }], d: null })).toEqual([]);
  });

  it("rejects nesting deeper than the limit", () => {
    let nested: unknown = "x";
    for (let depth = 0; depth < 200; depth += 1) nested = [nested];

    expect(jsonShapeIssues("doc", nested)).toEqual(["doc nesting exceeds depth 48"]);
  });

  // The regression: a wide array must be rejected by counting it, not by expanding it onto the
  // work stack one entry at a time.
  it("rejects a wide array without expanding it", () => {
    const wide = Array.from({ length: MAX_JSON_NODES * 2 }, (_, index) => index);

    const before = process.memoryUsage().heapUsed;
    const issues = jsonShapeIssues("doc", wide);
    const grewBy = process.memoryUsage().heapUsed - before;

    expect(issues).toEqual([`doc exceeds ${MAX_JSON_NODES} JSON nodes`]);
    // One stack entry per element would be tens of megabytes for this input alone; the check
    // should allocate essentially nothing.
    expect(grewBy).toBeLessThan(8_000_000);
  });

  it("rejects a wide object", () => {
    const wide: Record<string, number> = {};
    for (let key = 0; key <= MAX_JSON_NODES; key += 1) wide[`k${key}`] = key;

    expect(jsonShapeIssues("doc", wide)).toEqual([`doc exceeds ${MAX_JSON_NODES} JSON nodes`]);
  });

  it("counts nodes across siblings rather than per branch", () => {
    const share = Math.ceil(MAX_JSON_NODES / 3);
    const branch = (): number[] => Array.from({ length: share }, (_, index) => index);

    expect(jsonShapeIssues("doc", [branch(), branch()])).toEqual([]);
    expect(jsonShapeIssues("doc", [branch(), branch(), branch()])).toEqual([
      `doc exceeds ${MAX_JSON_NODES} JSON nodes`,
    ]);
  });

  // The bound holds before hashing. Canonical hashing was recursive and overflowed at this
  // depth; it is iterative since 2026-09-27 and no longer does, so the bound is the
  // classification — a named rejection rather than an arbitrarily deep document accepted —
  // and no longer the only thing between such a value and a RangeError.
  it("refuses deep input the canonical hasher could take", () => {
    let nested: unknown = "x";
    for (let depth = 0; depth < 20_000; depth += 1) nested = [nested];

    expect(jsonShapeIssues("doc", nested)).toEqual(["doc nesting exceeds depth 48"]);
    expect(sha256(nested)).toMatch(/^[0-9a-f]{64}$/);
  });
});
