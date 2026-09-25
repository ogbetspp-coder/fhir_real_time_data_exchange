import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { isUnderlineLetter, underlineChanges } from "../../src/authority/underline.js";

// The port of zone_a.underline answers every shared case as the Python does
// (zone-a/scripts/generate_underline_cases.py writes the cases with the Python's answers).

type Case = {
  text: string;
  start: number;
  end: number;
  also?: string[];
  hyphensInWords?: boolean;
  changes: boolean;
};

const cases = JSON.parse(
  readFileSync("test/fixtures/authority/underline-cases.json", "utf8"),
) as Case[];

describe("what an underline can change", () => {
  it("answers every shared case as zone_a.underline does", () => {
    expect(cases.length).toBeGreaterThan(100);
    for (const testCase of cases) {
      const answer = underlineChanges(Array.from(testCase.text), testCase.start, testCase.end, {
        ...(testCase.also === undefined ? {} : { also: new Set(testCase.also) }),
        ...(testCase.hyphensInWords === undefined
          ? {}
          : { hyphensInWords: testCase.hyphensInWords }),
      });
      expect([testCase.text, testCase.start, testCase.end, answer]).toEqual([
        testCase.text,
        testCase.start,
        testCase.end,
        testCase.changes,
      ]);
    }
  });

  it("reads letters by name, not by script property", () => {
    expect(isUnderlineLetter("a")).toBe(true);
    expect(isUnderlineLetter("\u03b1")).toBe(true);
    expect(isUnderlineLetter("\u0436")).toBe(true);
    // Latin by script property, but not a LATIN letter by name.
    expect(isUnderlineLetter("\u00aa")).toBe(false);
    expect(isUnderlineLetter("\u1d43")).toBe(false);
    expect(isUnderlineLetter("")).toBe(false);
    expect(isUnderlineLetter("1")).toBe(false);
  });
});
