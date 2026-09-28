import { beforeEach, describe, expect, it, vi } from "vitest";

import { XhtmlError, xhtmlToText } from "../src/fidelity/xhtml.js";

// How often the scanner composes a window: `composeText` is what the comparison calls.
const composed = vi.hoisted(() => ({ calls: 0 }));
vi.mock("../src/fidelity/normalize.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/fidelity/normalize.js")>();
  return {
    ...actual,
    composeText: (text: string): string => {
      composed.calls += 1;
      return actual.composeText(text);
    },
  };
});

// The scanner's composition check (docs/fidelity-normalization.md section 5) composes a window on
// each side of an inline tag and compares. It skips the comparison when the code point after the
// tag is below U+0300 (U+00AD aside, which never reaches the text): such a boundary is stable, so
// the comparison could only agree. This is the proof, over the runtime's own Unicode data, that
// every such code point is a starter NFC leaves alone and never composes with what precedes it.

const STABLE_BELOW = 0x0300;
const CP = (...points: number[]): string => String.fromCodePoint(...points);
// Combining marks of canonical combining class 240 and 1.
const YPOGEGRAMMENI = CP(0x0345);
const TILDE_OVERLAY = CP(0x0334);

describe("a boundary before a code point below U+0300", () => {
  it("is before a starter that NFC leaves as it is", () => {
    for (let codePoint = 0; codePoint < STABLE_BELOW; codePoint += 1) {
      const character = CP(codePoint);
      // NFC_QC is not No: the code point is its own NFC form.
      expect([codePoint, character.normalize("NFC")]).toEqual([codePoint, character]);
      // Canonical combining class 0: a mark of class 240 stays before it, and one of class 1
      // after it. A code point of any other class would be reordered past one of them.
      const before = `a${YPOGEGRAMMENI}${character}`;
      const after = `a${character}${TILDE_OVERLAY}`;
      expect([codePoint, before.normalize("NFC")]).toEqual([codePoint, before]);
      expect([codePoint, after.normalize("NFC")]).toEqual([codePoint, after]);
    }
  });

  it("is before a code point no canonical composition has as its second part", () => {
    // NFC composes a starter only with what comes second in some code point's canonical
    // decomposition. Every such second part (or the first code point of its own decomposition)
    // appears after the first code point of that code point's NFD form; none is below U+0300.
    const seconds: number[] = [];
    for (let codePoint = 0; codePoint <= 0x10ffff; codePoint += 1) {
      if (codePoint >= 0xd800 && codePoint <= 0xdfff) continue;
      const decomposed = Array.from(CP(codePoint).normalize("NFD"));
      for (const part of decomposed.slice(1)) {
        const point = part.codePointAt(0) ?? 0;
        if (point < STABLE_BELOW) seconds.push(point);
      }
    }
    expect(seconds).toEqual([]);
  });

  const root = (inner: string): string =>
    `<div xmlns="http://www.w3.org/1999/xhtml"><p>${inner}</p></div>`;

  beforeEach(() => {
    composed.calls = 0;
  });

  // Audit 2026-09-27 (F-3): the window was composed three times at every inline tag, which made
  // markup-heavy text scan 20 to 30 times slower per byte (six hundred thousand empty tags took
  // about 9 s, and 22 s in Python).
  it("is not composed", () => {
    const text = xhtmlToText(root(`${"a<b></b>".repeat(1_000)}e<b>e</b>${CP(0x00e9)}<i>A</i>`));
    expect(text).toBe(`\n\n${"a".repeat(1_000)}ee${CP(0x00e9)}A\n\n`);
    expect(composed.calls).toBe(0);
  });

  it("leaves the window where it can matter", () => {
    // Hangul jamo compose without being marks, so they still take the window.
    expect(() => xhtmlToText(root(`${CP(0x1100)}<b>${CP(0x1161)}</b>`))).toThrow(XhtmlError);
    expect(composed.calls).toBe(3);
  });
});
