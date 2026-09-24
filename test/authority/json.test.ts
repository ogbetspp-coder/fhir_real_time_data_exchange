import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  AuthorityBytesError,
  decodeUtf8,
  parseStrictJson,
  readAuthorityJson,
} from "../../src/authority/json.js";

// The authority's bytes are read one way only (docs/design/authority-import-contract.md, D1).

function reason(action: () => unknown): string {
  try {
    action();
  } catch (error) {
    if (error instanceof AuthorityBytesError) return error.reason;
    throw error;
  }
  return "accepted";
}

describe("strict UTF-8", () => {
  it("refuses a byte-order mark and an invalid byte", () => {
    expect(reason(() => decodeUtf8(new Uint8Array([0xef, 0xbb, 0xbf, 0x7b, 0x7d])))).toBe(
      "byte-order-mark",
    );
    expect(reason(() => decodeUtf8(new Uint8Array([0x7b, 0xff, 0x7d])))).toBe("invalid-utf-8");
    expect(decodeUtf8(new TextEncoder().encode('{"a":"é"}'))).toBe('{"a":"é"}');
  });
});

describe("strict JSON", () => {
  it("reads the EMA's pinned files exactly as JSON.parse does", () => {
    for (const file of [
      "labels/ema-epi/sources/imatinib-teva-smpc-en.json",
      "labels/ema-epi/lists/imatinib-teva-smpc-en.list.json",
    ]) {
      const bytes = readFileSync(file);
      expect(readAuthorityJson(bytes)).toEqual(JSON.parse(bytes.toString("utf8")));
    }
  });

  it("refuses a duplicate key at any depth", () => {
    expect(reason(() => parseStrictJson('{"a":1,"a":2}'))).toBe("duplicate-json-key");
    expect(reason(() => parseStrictJson('{"a":[{"b":1,"b":1}]}'))).toBe("duplicate-json-key");
  });

  it("reads every JSON value and escape", () => {
    expect(
      parseStrictJson(
        ' { "s": "\\"\\\\\\/\\b\\f\\n\\r\\t\\u00e9", "n": -1.5e2, "t": true, "f": false, "z": null, "e": {}, "a": [] } ',
      ),
    ).toEqual({ s: '"\\/\b\f\n\r\té', n: -150, t: true, f: false, z: null, e: {}, a: [] });
    expect(parseStrictJson("[1, [2, 3]]")).toEqual([1, [2, 3]]);
  });

  it("keeps a key named like an object property as data", () => {
    const parsed = parseStrictJson('{"__proto__":{"polluted":true}}') as Record<string, unknown>;
    expect(Object.keys(parsed)).toEqual(["__proto__"]);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("refuses what is not JSON, or nests too deeply", () => {
    for (const text of [
      "",
      "{",
      '{"a"}',
      '{"a":1,}',
      "[1,]",
      "[1 2]",
      '"unterminated',
      '"bad \\x escape"',
      '"bad \\u12 escape"',
      '"raw \u0001 control"',
      "01",
      "tru",
      "{} extra",
      "{1:2}",
    ]) {
      expect(
        reason(() => parseStrictJson(text)),
        text,
      ).toBe("invalid-json");
    }
    expect(reason(() => parseStrictJson("[".repeat(200) + "]".repeat(200)))).toBe("json-too-deep");
  });
});
