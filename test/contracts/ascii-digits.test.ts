import { describe, expect, it } from "vitest";

import {
  HttpUrl,
  IsoDateTime,
  NormalizationVersion,
  PackageRef,
  TargetPath,
} from "../../src/contracts/common.js";

// Zone B's half of the digit parity (audit B07 follow-up, Low-2). Each value below is refused by
// Zod, and zone-a/tests/test_ascii_digits.py holds Zone A's generated models to the same answer:
// until the published patterns said [0-9] instead of \d, Pydantic (Rust regex, where \d is any
// Unicode decimal digit) accepted every one of them.

const ARABIC_INDIC_THREE = String.fromCodePoint(0x0663);
const FULLWIDTH_THREE = String.fromCodePoint(0xff13);

// Each primitive with a valid value and the same value with one ASCII digit replaced.
const DIGIT_CASES: [
  string,
  { safeParse: (value: unknown) => { success: boolean } },
  string,
  string,
][] = [
  ["PackageRef", PackageRef, "hl7.terminology.r5#7.3.0", "hl7.terminology.r5#7.3.D"],
  ["NormalizationVersion", NormalizationVersion, "fidelity-norm/3.1.0", "fidelity-norm/3.1.D"],
  ["IsoDateTime", IsoDateTime, "2026-09-28T00:00:03Z", "2026-09-28T00:00:0DZ"],
  ["HttpUrl", HttpUrl, "https://example.org:8443/a", "https://example.org:844D/a"],
  ["TargetPath", TargetPath, "Composition.section[3]", "Composition.section[D]"],
];

describe("contract digits", () => {
  it.each(DIGIT_CASES)("%s accepts ASCII digits", (_, schema, valid) => {
    expect(schema.safeParse(valid).success).toBe(true);
  });

  it.each(
    DIGIT_CASES.flatMap(([name, schema, , template]) =>
      [ARABIC_INDIC_THREE, FULLWIDTH_THREE].map(
        (digit) =>
          [name, digit.codePointAt(0)?.toString(16), schema, template.replace("D", digit)] as const,
      ),
    ),
  )("%s refuses U+%s in place of a digit", (_, __, schema, value) => {
    expect(schema.safeParse(value).success).toBe(false);
  });
});
