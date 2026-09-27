import { describe, expect, it } from "vitest";

import type { Font } from "../../src/render/font.js";
import { boundFace, checkTextNode, namedFamily } from "../../src/render/fonts.js";
import { calibrationDiv, checkHeights, checkPage, styleKey } from "../../src/render/page-checks.js";

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
  ["LiberationSerif", face("LiberationSerif", " -abcdo μЖ")],
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
    expect(boundFace("Arial", 700, "oblique")).toBe("LiberationSans-BoldItalic");
    expect(boundFace("sans-serif", 500, "italic")).toBe("LiberationSans-Italic");
    expect(boundFace("Calibri", 400, "normal")).toBe("Carlito-Regular");
    expect(boundFace("Cambria", 900, "italic")).toBe("Caladea-BoldItalic");
    for (const family of ["Verdana", "Verdana, Arial", "monospace", "'Segoe UI'", "Symbol"]) {
      expect(boundFace(family, 400, "normal")).toBeUndefined();
    }
  });

  it("passes text drawn in its bound face, whose map has every code point drawn", () => {
    expect(checkTextNode(node("a b c-d ­​\n"), FACES)).toEqual([]);
    // The non-breaking hyphen is drawn with the hyphen's glyph (R6's substitutions).
    expect(checkTextNode(node("1‑2", '"Times New Roman"', ["LiberationSerif"]), FACES)).toEqual([
      expect.objectContaining({ refusal: "font-coverage", codePoint: 0x31 }),
      expect.objectContaining({ refusal: "font-coverage", codePoint: 0x32 }),
    ]);
    expect(checkTextNode(node("a‑b"), FACES)).toEqual([]);
    // Whitespace alone is not judged: nothing of it is drawn in a face.
    expect(checkTextNode(node("\n  \t", "Verdana", []), FACES)).toEqual([]);
  });

  it("refuses an unbound family, a fallback face, a code point the face lacks and a script outside the bound", () => {
    expect(checkTextNode(node("abc", "Verdana"), FACES)).toEqual([
      { refusal: "font-unpinned", detail: "Verdana" },
    ]);
    expect(
      checkTextNode(
        node("a⁰", '"Times New Roman"', ["LiberationSerif", "Carlito-Regular"]),
        FACES,
      ).map(({ refusal }) => refusal),
    ).toEqual(["font-face", "font-coverage"]);
    expect(checkTextNode(node("Ж"), FACES)).toEqual([
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

  it("draws a calibration of each face and size, and holds every box to it", () => {
    const run = {
      element: 2,
      family: '"Times New Roman"',
      weight: 700,
      style: "italic",
      size: "14.6667px",
      text: "x",
      drawnIn: [],
    };
    const key = styleKey(run);
    expect(calibrationDiv([key])).toBe(
      '<div xmlns="http://www.w3.org/1999/xhtml"><p style="margin:0"><span id="c0" style="font-family:\'Times New Roman\';font-weight:700;font-style:italic;font-size:14.6667px">x</span></p></div>',
    );
    const calibration = new Map([[key, 17]]);
    expect(checkHeights([run], [{ element: 2, heights: [17, 17.0001] }], calibration)).toEqual([]);
    expect(checkHeights([run], [{ element: 2, heights: [17, 18] }], calibration)[0]?.refusal).toBe(
      "char-height",
    );
    expect(checkHeights([run], [{ element: 3, heights: [17] }], calibration)[0]?.detail).toMatch(
      /no calibration/,
    );
  });
});
