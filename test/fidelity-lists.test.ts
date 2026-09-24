import { describe, expect, it } from "vitest";

import { isForbiddenCodePoint, isGap } from "../src/fidelity/normalize.js";

// The closed lists fidelity-norm/3.0.0 states code point by code point
// (docs/fidelity-normalization.md sections 2 and 6), each held here as the specification writes
// it, so that dropping one entry from the code fails a test. The Python port
// (zone-a/tests/test_fidelity_lists.py) and the agent (agent/tests/test_quote_edge.py) hold the
// same lists.

// Section 2, from 3.0.0: the interlinear annotation controls and the prepended concatenation
// marks.
const FORBIDDEN_FROM_3_0_0 = [
  0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605, 0x06dd, 0x070f, 0x0890, 0x0891, 0x08e2, 0xfff9,
  0xfffa, 0xfffb, 0x110bd, 0x110cd,
];

// Section 6: the blank glyphs.
const BLANK_GLYPHS = [0x1878, 0x18aa, 0x2800, 0xa4a2, 0xa4a3, 0xa4b4, 0xa4c1, 0xa4c5];

describe("the closed lists of fidelity-norm/3.0.0", () => {
  it("refuses every code point section 2 adds, and not their neighbours", () => {
    for (const codePoint of FORBIDDEN_FROM_3_0_0) {
      expect([codePoint.toString(16), isForbiddenCodePoint(codePoint)]).toEqual([
        codePoint.toString(16),
        true,
      ]);
    }
    for (const codePoint of [0x05ff, 0x0606, 0x06dc, 0x06de, 0x070e, 0x08e1, 0x08e3, 0x110be]) {
      expect([codePoint.toString(16), isForbiddenCodePoint(codePoint)]).toEqual([
        codePoint.toString(16),
        false,
      ]);
    }
  });

  it("reads every blank glyph as a gap", () => {
    for (const codePoint of BLANK_GLYPHS) {
      expect([codePoint.toString(16), isGap(codePoint)]).toEqual([codePoint.toString(16), true]);
    }
    for (const codePoint of [0x1877, 0x1879, 0x2801, 0xa4a1, 0xa4c6]) {
      expect([codePoint.toString(16), isGap(codePoint)]).toEqual([codePoint.toString(16), false]);
    }
  });
});
