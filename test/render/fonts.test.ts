import { describe, expect, it } from "vitest";

import type { Font } from "../../src/render/font.js";
import { boundFace, checkTextNode, namedFamily } from "../../src/render/fonts.js";
import { allowedBoxes, boxHeight, checkHeights, checkPage } from "../../src/render/page-checks.js";

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

  it("computes R3's box as Chrome rounds it, and holds every box to it", () => {
    // Liberation Serif's hhea: 1825 and -443 of 2048.
    const metrics = { ...METRICS, hheaAscender: 1825, hheaDescender: -443 };
    // 8 pt at ratio 1: 10.667 px, quantised to 10.656 in FreeType's 26.6, gives 9 + 2 (the
    // rectangle-plus-ascent estimate said 12; Chrome draws 11, measured).
    expect(boxHeight(metrics, 32 / 3, 1)).toBe(11);
    expect(boxHeight(metrics, 16, 1)).toBe(17);
    expect(boxHeight(metrics, 16, 2.625)).toBeCloseTo(46 / 2.625, 10);
    // Both quantisations are allowed, a device pixel apart where a rounding falls (Chrome draws 11).
    expect(allowedBoxes(metrics, "10.6667px", 1)).toEqual([11, 12]);
    expect(allowedBoxes(metrics, "16px", 1)).toEqual([17]);
    // h6 (0.67 em of 16 px) at 1.1: quantised down it is 13 device pixels, to the nearest 14,
    // which Chrome draws (CI, measured).
    expect(boxHeight(metrics, 10.72, 1.1) * 1.1).toBeCloseTo(13, 9);
    expect(boxHeight(metrics, 10.72, 1.1, false) * 1.1).toBeCloseTo(14, 9);
    expect(
      allowedBoxes(metrics, "10.72px", 1.1)
        .map((box) => Math.round(box * 1.1))
        .sort(),
    ).toEqual([13, 14]);
    expect(allowedBoxes(metrics, "auto", 1)).toEqual([]);
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
    expect(checkHeights([run], [{ element: 2, heights: [17, 17.0005] }], faces, 1)).toEqual([]);
    expect(checkHeights([run], [{ element: 2, heights: [17, 18] }], faces, 1)[0]?.refusal).toBe(
      "char-height",
    );
    expect(checkHeights([run], [{ element: 3, heights: [17] }], faces, 1)[0]?.detail).toMatch(
      /no bound face/,
    );
  });
});
