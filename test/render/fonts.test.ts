import { describe, expect, it } from "vitest";

import type { Font } from "../../src/render/font.js";
import { boundFace, checkTextNode, namedFamily } from "../../src/render/fonts.js";
import { boxPairs, checkHeights, checkPage, resolveBox } from "../../src/render/page-checks.js";

// R3's fonts and scripts, R6's coverage, R2's page and R3's character boxes, as rules on what
// Chrome reports (the renderer image's run of scripts/render/check-fonts.ts draws them).

const METRICS = {
  unitsPerEm: 2048,
  hheaAscender: 0,
  hheaDescender: 0,
  hheaLineGap: 0,
  typoAscender: 0,
  typoDescender: 0,
  typoLineGap: 0,
  winAscent: 0,
  winDescent: 0,
  useTypoMetrics: false,
};
const face = (postScriptName: string, points: string): Font => ({
  postScriptName,
  codePoints: new Set(Array.from(points).map((character) => character.codePointAt(0) ?? 0)),
  metrics: METRICS,
});
const FACES = new Map([
  ["LiberationSerif", face("LiberationSerif", " -abcdo\u00a0\u03bc\u0416\u2010")],
  ["Carlito-Bold", face("Carlito-Bold", " abc")],
]);
const node = (
  text: string,
  family = '"Times New Roman"',
  drawnIn = ["LiberationSerif"],
  weight = 400,
  style = "normal",
) => ({
  family,
  weight,
  style,
  drawnIn,
  text,
});

describe("R3's fonts", () => {
  it("names the family the node names, and binds only R6's", () => {
    expect(namedFamily('"Times New Roman", serif')).toBe("Times New Roman");
    expect(namedFamily("Verdana, Arial")).toBe("Verdana");
    expect(boundFace('"Times New Roman"', 400, "normal")).toBe("LiberationSerif");
    expect(boundFace("TIMES", 600, "normal")).toBe("LiberationSerif-Bold");
    // Above 500 is bold (550 measured bold), 500 regular; an oblique with an angle upright.
    expect(boundFace("Times", 550, "normal")).toBe("LiberationSerif-Bold");
    expect(boundFace("Times", 500, "normal")).toBe("LiberationSerif");
    expect(boundFace("Times", 400, "oblique 10deg")).toBe("LiberationSerif");
    expect(boundFace("Arial", 700, "oblique")).toBe("LiberationSans-BoldItalic");
    expect(boundFace("sans-serif", 500, "italic")).toBe("LiberationSans-Italic");
    expect(boundFace("Calibri", 400, "normal")).toBe("Carlito-Regular");
    expect(boundFace("Cambria", 900, "italic")).toBe("Caladea-BoldItalic");
    for (const family of ["Verdana", "Verdana, Arial", "monospace", "'Segoe UI'", "Symbol"]) {
      expect(boundFace(family, 400, "normal")).toBeUndefined();
    }
  });

  it("passes text drawn in its bound face, whose map has every code point drawn", () => {
    expect(checkTextNode(node("a b\u00a0c-d \u00ad\u200b\n"), FACES)).toEqual([]);
    // The non-breaking hyphen is drawn with the hyphen's glyph (R6's substitutions).
    expect(
      checkTextNode(node("1\u20112", '"Times New Roman"', ["LiberationSerif"]), FACES),
    ).toEqual([
      expect.objectContaining({ refusal: "font-coverage", codePoint: 0x31 }),
      expect.objectContaining({ refusal: "font-coverage", codePoint: 0x32 }),
    ]);
    expect(checkTextNode(node("a\u2011b"), FACES)).toEqual([]);
    // ... in Liberation's faces only: Carlito draws it from another face, and lacks it.
    expect(checkTextNode(node("a\u2011b", "Calibri", ["Carlito-Bold"], 700), FACES)).toEqual([
      expect.objectContaining({ refusal: "font-coverage", codePoint: 0x2011 }),
    ]);
    // A Hangul filler is drawn as .notdef, never hidden (the first code review): judged by the map.
    // (It is Hangul too, outside R3's scripts.)
    expect(checkTextNode(node("\u3164"), FACES).map(({ refusal }) => refusal)).toEqual([
      "script",
      "font-coverage",
    ]);
    // The script bound holds whatever is drawn: a node of a character HarfBuzz hides, too.
    expect(checkTextNode(node("\u0416", "Verdana", []), FACES)[0]?.refusal).toBe("script");
    // A wide space alone is reported in no face; its map still judges it.
    expect(checkTextNode(node("\u2003", '"Times New Roman"', []), FACES)).toEqual([
      expect.objectContaining({ refusal: "font-coverage", codePoint: 0x2003 }),
    ]);
    // A node of code points drawn as nothing is still held to its face where one is reported.
    expect(checkTextNode(node("\u200b", '"Times New Roman"', ["Carlito-Regular"]), FACES)).toEqual([
      expect.objectContaining({ refusal: "font-face" }),
    ]);
    expect(checkTextNode(node("\u200b", '"Times New Roman"', []), FACES)).toEqual([]);
    // Whitespace alone is not judged: nothing of it is drawn in a face.
    expect(checkTextNode(node("\n  \t", "Verdana", []), FACES)).toEqual([]);
  });

  it("refuses an unbound family, a fallback face, a code point the face lacks and a script outside the bound", () => {
    expect(checkTextNode(node("abc", "Verdana"), FACES)).toEqual([
      { refusal: "font-unpinned", detail: "Verdana" },
    ]);
    expect(
      checkTextNode(
        node("a\u2070", '"Times New Roman"', ["LiberationSerif", "Carlito-Regular"]),
        FACES,
      ).map(({ refusal }) => refusal),
    ).toEqual(["font-face", "font-coverage"]);
    expect(checkTextNode(node("\u0416"), FACES)).toEqual([
      { refusal: "script", codePoint: 0x416, detail: "U+0416" },
    ]);
    expect(checkTextNode(node("abc", "Calibri", ["Carlito-Regular"]), FACES)).toEqual([
      { refusal: "font-unpinned", detail: "Carlito-Regular is not in the image" },
    ]);
    expect(checkTextNode(node("ab", "Calibri", ["Carlito-Bold"], 700), FACES)).toEqual([]);
  });
});

describe("R2's page and R3's character boxes", () => {
  const page = {
    parserError: false,
    divPadding: "0px 0px 0px 0px",
    divBorder: "0px 0px 0px 0px",
    divWidth: 813,
  };

  it("refuses a parser error, a div with its own box, and a content box not the width", () => {
    expect(checkPage(page, 813, "xml")).toEqual([]);
    expect(checkPage({ ...page, parserError: true }, 813, "xml")[0]?.refusal).toBe("parsererror");
    expect(checkPage({ ...page, parserError: true }, 813, "html")).toEqual([]);
    expect(checkPage({ ...page, divPadding: "4px 0px 0px 0px" }, 813, "html")[0]?.refusal).toBe(
      "div-box",
    );
    expect(checkPage({ ...page, divBorder: "0px 1px 0px 0px" }, 813, "html")[0]?.refusal).toBe(
      "div-box",
    );
    expect(checkPage({ ...page, divWidth: 812 }, 813, "html")[0]?.refusal).toBe("div-width");
  });

  it("names each box's exact ascent and descent from the bound face, or refuses it", () => {
    // Liberation Serif's hhea: 1825 and -443 of 2048; Sans's 1854 and -434; Carlito's 1950 and -550.
    const metrics = { ...METRICS, hheaAscender: 1825, hheaDescender: -443 };
    const sans = { ...METRICS, hheaAscender: 1854, hheaDescender: -434 };
    const carlito = { ...METRICS, hheaAscender: 1950, hheaDescender: -550 };
    const one = (value: number) => ({ low: value, high: value });
    // 16 px at ratio 1: 14.26 and 3.46 round to 14 and 3, and the descent rounded down takes a
    // pixel from the ascent (measured with a baseline marker): 13 above the baseline, 4 below.
    expect(boxPairs(metrics, 16, 1)).toEqual([[14, 3]]);
    expect(resolveBox(metrics, 16, 1, 17)).toEqual({ ascent: one(13), descent: one(4) });
    expect(resolveBox(metrics, 16, 1, 18)).toBeUndefined();
    expect(resolveBox(metrics, 16, 1, 19)).toBeUndefined();
    // The boxes the tolerance of 3c-B2a let through, each Chrome's (measured): the size floored
    // to 1/100 px, never to FreeType's 1/64.
    expect(resolveBox(metrics, 16 * 0.67, 1.1, 14 / 1.1)).toBeDefined();
    expect(resolveBox(metrics, 16 * 0.83, 3, 15)).toBeDefined();
    expect(resolveBox(sans, 21.4133, 0.8, 23.75)).toBeDefined();
    expect(resolveBox(carlito, 16.3, 0.8, 20)).toBeDefined();
    // Two sizes sharing Blink's font cache key are drawn in whichever the process drew first: a
    // 9.97333 px Carlito letter is 12 px alone and 13 px after a 9.984 px one (measured); both
    // are its face's.
    expect(resolveBox(carlito, 9.97333, 1, 12)).toBeDefined();
    expect(resolveBox(carlito, 9.97333, 1, 13)).toBeDefined();
    // A size whose ascent crosses a rounding step inside the interval has two pairs; the box
    // names one.
    const two = boxPairs(metrics, 17.4 / 0.9, 0.9);
    expect(two.length).toBe(2);
    expect(two.map(([a, d]) => resolveBox(metrics, 17.4 / 0.9, 0.9, (a + d) / 0.9))).not.toContain(
      undefined,
    );
    // 28.9 px at 0.8: the exact descent is 5.00 within the interval, so whether it took a pixel
    // is not decided; the ascent is known within one device pixel (Chrome drew 20).
    expect(resolveBox(metrics, 28.9, 0.8, 26 / 0.8)).toEqual({
      ascent: { low: 20, high: 21 },
      descent: { low: 5, high: 6 },
    });
    // 12 px: 10.69 and 2.60 round to 11 and 3, the descent rounded up, so nothing moves.
    expect(resolveBox(metrics, 12, 1, 14)).toEqual({ ascent: one(11), descent: one(3) });
    // The interval's edges: the ascent steps from 10 to 11 at 11.7832 px, which lies above
    // 11.77's interval (to 11.77 + 0.01) and inside 11.775's, and below 11.805's (from
    // 11.805 - 0.02) and inside 11.8's.
    expect(boxPairs(metrics, 11.77, 1)).toEqual([[10, 3]]);
    expect(boxPairs(metrics, 11.775, 1)).toEqual([
      [10, 3],
      [11, 3],
    ]);
    expect(boxPairs(metrics, 11.8, 1)).toEqual([
      [10, 3],
      [11, 3],
    ]);
    expect(boxPairs(metrics, 11.805, 1)).toEqual([[11, 3]]);
    expect(resolveBox(metrics, 11.77, 1, 14)).toBeUndefined();
    expect(resolveBox(metrics, 11.805, 1, 13)).toBeUndefined();
    // Below an ascent of 3 device pixels Blink keeps fractional metrics: refused.
    expect(boxPairs(metrics, 3, 1)).toEqual([[3, 1]]);
    expect(resolveBox(metrics, 3, 1, 4)).toBeUndefined();
    // A face the model does not hold for: no descent below the baseline, or typographic metrics
    // asked for and not the hhea's.
    expect(boxPairs({ ...metrics, hheaDescender: 0 }, 12, 1)).toEqual([]);
    expect(boxPairs({ ...metrics, useTypoMetrics: true }, 12, 1)).toEqual([]);
    expect(
      boxPairs(
        { ...metrics, useTypoMetrics: true, typoAscender: 1825, typoDescender: -443 },
        12,
        1,
      ),
    ).toEqual([[11, 3]]);
    // A height off Chrome's 1/64 CSS px grid is no box.
    expect(resolveBox(metrics, 16, 1, 17.03)).toBeUndefined();
    const face = { postScriptName: "LiberationSerif", codePoints: new Set<number>(), metrics };
    const faces = new Map([["LiberationSerif", face]]);
    const run = {
      element: 2,
      family: '"Times New Roman"',
      weight: 400,
      style: "normal",
      size: "16px",
      text: "x",
      drawnIn: [],
    };
    expect(checkHeights([run], [{ element: 2, heights: [17, 17] }], faces, 1)).toEqual([]);
    expect(checkHeights([run], [{ element: 2, heights: [17, 18] }], faces, 1)[0]).toEqual({
      refusal: "char-height",
      detail: "element 2: a box 18 px high, not the bound face's (17)",
    });
    expect(checkHeights([run], [{ element: 3, heights: [17] }], faces, 1)[0]?.detail).toMatch(
      /no bound face/,
    );
    expect(
      checkHeights([{ ...run, size: "auto" }], [{ element: 2, heights: [17] }], faces, 1)[0]
        ?.detail,
    ).toMatch(/no bound face/);
  });
});
