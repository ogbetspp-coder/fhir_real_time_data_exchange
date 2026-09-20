import { describe, expect, it } from "vitest";

// NFC (docs/fidelity-normalization.md step 3) depends on the runtime's Unicode Character
// Database. ADR 0003 pins Unicode 16.0 and treats a runtime with a different version as a
// change to the normalisation version even when no code changes — which nothing would notice
// unless the build itself checks. This test is that check: a base-image or Node upgrade that
// moves the Unicode version fails CI instead of silently moving every NFC-dependent hash.

// Node reports major.minor ("16.0"); Python's unicodedata reports "16.0.0" for the same UCD.
const PINNED_UNICODE_VERSION = "16.0";

describe("runtime pins that the normalisation version depends on", () => {
  it(`runs on Unicode ${PINNED_UNICODE_VERSION}`, () => {
    expect(process.versions.unicode).toBe(PINNED_UNICODE_VERSION);
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
