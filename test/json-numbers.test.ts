import { describe, expect, it } from "vitest";

import { hasNonCanonicalNumber } from "../src/lib/json-numbers.js";

// A number is stored as its value, re-serialised as JavaScript writes it (RFC 8785): a document
// part whose numbers are written otherwise would lose what it wrote (ADR 0002, "Numbers").

describe("numbers written as JavaScript writes them", () => {
  // 9007199254740992 is beyond Number.MAX_SAFE_INTEGER and still accepted: it is written as
  // JavaScript writes its double. Zone A's canonical JSON refuses it (ADR 0002, "Numbers").
  it.each([
    "0",
    "-1",
    "2.5",
    "500",
    "0.000001",
    "1e-7",
    "1e+21",
    "5e-324",
    "9007199254740991",
    "9007199254740992",
  ])("accepts %s", (written) => {
    expect(hasNonCanonicalNumber(`{"a":[${written}]}`)).toBe(false);
    expect(JSON.stringify(JSON.parse(written))).toBe(written);
  });

  it.each([
    ["-0", "which JavaScript writes 0"],
    ["1.0", "a trailing zero"],
    ["2.50", "a decimal's precision"],
    ["0.10", "a decimal's precision"],
    ["1e2", "an exponent JavaScript does not write"],
    ["1E21", "an upper-case exponent"],
    ["1e21", "an exponent without its sign"],
    ["0.0000001", "a small number JavaScript writes with an exponent"],
    ["9007199254740993", "a digit beyond a double"],
    ["0.1000000000000000055511151231257827", "digits beyond a double"],
    ["1e400", "a number beyond a double"],
  ])("refuses %s (%s)", (written) => {
    expect(hasNonCanonicalNumber(`{"a":[1, ${written}, 2]}`)).toBe(true);
  });

  it("reads no number inside a string, escaped quotes included", () => {
    expect(hasNonCanonicalNumber(String.raw`{"a":"1.0","b":"x\"2.50\"","c\"1.0":3}`)).toBe(false);
    expect(hasNonCanonicalNumber(String.raw`{"a":"x\\","b":1.0}`)).toBe(true);
  });

  it("accepts every number the synthetic fixtures carry, whitespace and all", () => {
    const value = { strength: 2.5, count: 12, nested: [{ offset: 0 }, { offset: 300 }] };
    expect(hasNonCanonicalNumber(JSON.stringify(value, null, 2))).toBe(false);
  });
});
