import { describe, expect, it } from "vitest";

import {
  QuoteVerificationSchema,
  QuoteVerificationWireSchema,
} from "../../src/contracts/query-tools.js";

// query-tools 4.0.0: a `match` that does not say where it matched is not a match a client can
// use, and under 2.x and 3.0.0 it validated (audit AG-4). Each case below was accepted then.

const base = {
  document: { bundleId: "synthetic-smpc", versionId: "1", lastUpdated: "2026-09-19T00:00:00Z" },
  normalizationVersion: "fidelity-norm/3.6.0",
  quoteSha256: "1".repeat(64),
};
const where = {
  sourceKey: "smpc.4.3",
  startOffset: 0,
  endOffset: 22,
  normalizedTextSha256: "2".repeat(64),
};

describe("QuoteVerification", () => {
  it("accepts a match with its location and a no-match without one", () => {
    expect(
      QuoteVerificationSchema.safeParse({
        ...base,
        result: "match",
        sectionsSearched: 1,
        match: where,
      }).success,
    ).toBe(true);
    expect(
      QuoteVerificationSchema.safeParse({ ...base, result: "no-match", sectionsSearched: 0 })
        .success,
    ).toBe(true);
  });

  it.each([
    ["a match with no location", { ...base, result: "match", sectionsSearched: 1 }],
    ["a match over no section", { ...base, result: "match", sectionsSearched: 0, match: where }],
    [
      "a match that ends where it starts",
      { ...base, result: "match", sectionsSearched: 1, match: { ...where, endOffset: 0 } },
    ],
    [
      "a match that ends before it starts",
      {
        ...base,
        result: "match",
        sectionsSearched: 1,
        match: { ...where, startOffset: 30, endOffset: 22 },
      },
    ],
    [
      "a no-match with a location",
      { ...base, result: "no-match", sectionsSearched: 1, match: where },
    ],
  ])("refuses %s", (_, answer) => {
    expect(QuoteVerificationSchema.safeParse(answer).success).toBe(false);
  });

  it("advertises an object for MCP that accepts everything the union accepts", () => {
    const answer = { ...base, result: "match", sectionsSearched: 1, match: where };
    expect(QuoteVerificationSchema.safeParse(answer).success).toBe(true);
    expect(QuoteVerificationWireSchema.safeParse(answer).success).toBe(true);
  });
});
