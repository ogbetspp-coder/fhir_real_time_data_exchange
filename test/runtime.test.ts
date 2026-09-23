import { describe, expect, it } from "vitest";

// NFC (docs/fidelity-normalization.md step 3) depends on the runtime's Unicode Character
// Database. ADR 0003 pins Unicode 16.0 and treats a runtime with a different version as a
// change to the normalisation version even when no code changes — which nothing would notice
// unless the build itself checks. This test is that check: a base-image or Node upgrade that
// moves the Unicode version fails CI instead of silently moving every NFC-dependent hash.

// Node reports major.minor ("16.0"); Python's unicodedata reports "16.0.0" for the same UCD.
const PINNED_UNICODE_VERSION = "16.0";
// ICU is pinned too (ADR 0003 names it with the Node image). The move from node:22.14.0 (ICU
// 76.1) to node:22.22.0 (ICU 77.1) on 2026-09-22 kept Unicode 16.0 and every NFC and NFD result,
// but changed word segmentation, which the ingress gate's unverified-text word bound uses
// (src/contracts/canonical-submission.ts): an ICU-only move is not a no-op, so it fails here.
const PINNED_ICU_VERSION = "77.1";

describe("runtime pins that the normalisation version depends on", () => {
  it(`runs on Unicode ${PINNED_UNICODE_VERSION}`, () => {
    expect(process.versions.unicode).toBe(PINNED_UNICODE_VERSION);
  });

  it(`runs on ICU ${PINNED_ICU_VERSION}`, () => {
    expect(process.versions.icu).toBe(PINNED_ICU_VERSION);
  });

  it("segments words as the pinned ICU does, colon between letters included", () => {
    // ICU 77 (CLDR 47) joins letter:letter into one word; ICU 76 split it into two. A digit
    // either side still splits. Recorded in docs/validation/README.md, runtime change 2026-09-22.
    const words = (text: string): string[] =>
      [...new Intl.Segmenter(undefined, { granularity: "word" }).segment(text)]
        .filter((segment) => segment.isWordLike === true)
        .map((segment) => segment.segment);
    expect(words("a:b")).toEqual(["a:b"]);
    expect(words("a：b")).toEqual(["a：b"]);
    expect(words("1:2")).toEqual(["1", "2"]);
    expect(words("dose: 5 mg")).toEqual(["dose", "5", "mg"]);
  });

  it("has full ICU, so NFC and the word segmenter are the international build", () => {
    // A small-icu Node build normalises and segments differently; the fidelity spec and the
    // ingress gate's word counting both assume full ICU.
    expect(typeof process.versions.icu).toBe("string");
    expect(new Intl.Segmenter("und", { granularity: "word" }).resolvedOptions().granularity).toBe(
      "word",
    );
    expect("é".normalize("NFC")).toBe("é");
  });
});
