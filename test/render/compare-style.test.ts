import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { readRendererPins } from "../../scripts/ci/renderer-pins.mjs";
import { modelSection, type Model } from "../../src/authority/t/model.js";
import {
  borderAsDrawn,
  compareModel,
  compareText,
  lengthMatches,
  parseColour,
  spacingAsDrawn,
  tolerance,
  validateModel,
} from "../../src/render/compare-style.js";
import type { ChromeElement, ChromeText } from "../../src/render/measure.js";
import { MODEL_CASES } from "../fixtures/render/model-cases.js";

// R3's comparison on recorded Chrome output (test/fixtures/render/recorded.json, made by
// scripts/render/record.ts with the pinned browser at ratio 1.25 and recorded again in CI's
// Renderer job), so `npm run check` needs no browser: T's model equals every recording, and each
// kind of model error is caught.

type Recording = {
  divSha256: string;
  elements: ChromeElement[];
  markers: [number, string][];
  texts: ChromeText[];
};
const recorded = JSON.parse(readFileSync("test/fixtures/render/recorded.json", "utf8")) as Record<
  string,
  Recording
> & { ratio: number; browser: string };
const RATIO = recorded.ratio;
const ROOT = '<div xmlns="http://www.w3.org/1999/xhtml">';
const MARKED = "<p>not for clinical use</p></div>";

function modelOf(name: string): Model {
  const modelCase = MODEL_CASES.find((candidate) => candidate.name === name);
  if (modelCase === undefined) throw new Error(name);
  return JSON.parse(modelSection(`${ROOT}${modelCase.inner}${MARKED}`)) as Model;
}
function recording(name: string, mode: "html" | "xml"): Recording {
  const found = recorded[`${name} ${mode}`];
  if (found === undefined) throw new Error(`${name} ${mode}`);
  return found;
}
function compare(model: Model, name: string, mode: "html" | "xml" = "html") {
  const { elements, markers } = recording(name, mode);
  return compareModel(model, elements, new Map(markers), RATIO, mode);
}
// The first element named `name` in the model, for a mutation.
function element(model: Model, name: string, nth = 0) {
  const found = model.elements.filter((candidate) => candidate.name === name)[nth];
  if (found === undefined) throw new Error(name);
  return found;
}

describe("R3 on the recorded drawings", () => {
  it("was recorded with the pinned browser, of every model case as it stands", () => {
    expect(recorded.browser).toBe(`HeadlessChrome/${readRendererPins().chromeVersion}`);
    const sha256 = (text: string): string =>
      createHash("sha256").update(text, "utf8").digest("hex");
    const expected = MODEL_CASES.flatMap(({ name, inner }) =>
      (["html", "xml"] as const).map((mode) => [
        `${name} ${mode}`,
        sha256(`${ROOT}${inner}${MARKED}`),
      ]),
    );
    // Every recording, in order, is of a model case's div as it stands, and nothing else.
    const cases = Object.keys(recorded).filter((key) => key !== "browser" && key !== "ratio");
    expect(cases.map((key) => [key, recorded[key]?.divSha256])).toEqual(expected);
  });

  for (const { name } of MODEL_CASES) {
    for (const mode of ["html", "xml"] as const) {
      it(`${name}, ${mode}: T's model equals Chrome's`, () => {
        const model = modelOf(name);
        expect(compare(model, name, mode)).toEqual([]);
        if (mode === "xml") expect(compareText(model, recording(name, mode).texts)).toEqual([]);
      });
    }
  }

  it.each([
    [
      "a weight",
      "weights-and-styles",
      (m: Model) => (element(m, "b").style.fontWeight = 700),
      "font-weight",
    ],
    [
      "a style",
      "weights-and-styles",
      (m: Model) => (element(m, "cite").style.fontStyle = "normal"),
      "font-style",
    ],
    [
      "a size",
      "sizes",
      (m: Model) => (element(m, "sup").style.fontSize = { lo: 9, hi: 9 }),
      "font-size",
    ],
    [
      "a line height",
      "sizes",
      (m: Model) => (element(m, "p").style.lineHeight = "normal"),
      "line-height",
    ],
    [
      "a colour",
      "backgrounds-and-colours",
      (m: Model) => (element(m, "a").style.colours = [[0, 0, 0]]),
      "color",
    ],
    [
      "a background",
      "backgrounds-and-colours",
      (m: Model) => (element(m, "span").style.backgrounds = []),
      "background-color",
    ],
    [
      "an underline",
      "backgrounds-and-colours",
      (m: Model) => (element(m, "u").style.underline = false),
      "-webkit-text-decorations-in-effect",
    ],
    [
      "a vertical margin",
      "headings-and-rules",
      (m: Model) => (element(m, "h5").style.margin.top = { lo: 0, hi: 0 }),
      "margin-top",
    ],
    [
      "a nested list's margin",
      "nested-lists",
      (m: Model) => (element(m, "ul", 1).style.margin.bottom = { lo: 12, hi: 12 }),
      "margin-bottom",
    ],
    [
      "an hr's colour",
      "headings-and-rules",
      (m: Model) => (element(m, "hr", 1).style.colours = [[128, 128, 128]]),
      "color",
    ],
    // 3 pt, not 0.75 pt: at 1.25 both 1 pt and 0.75 pt are drawn one device pixel wide.
    [
      "a border width",
      "bordered-table",
      (m: Model) =>
        (element(m, "td").style.borders.bottom = {
          width: 3,
          style: "solid",
          colours: [[51, 51, 51]],
        }),
      "border-bottom-width",
    ],
    [
      "a border style",
      "bordered-table",
      (m: Model) => (element(m, "th").style.borders.top = "none"),
      "border-top-style",
    ],
    [
      "a cell's padding",
      "bordered-table",
      (m: Model) => (element(m, "th").style.padding.left = { lo: 0.75, hi: 0.75 }),
      "padding-left",
    ],
    [
      "a row's alignment",
      "bordered-table",
      (m: Model) => (element(m, "tr").style.verticalAlign = "middle"),
      "vertical-align",
    ],
    [
      "the spacing",
      "bordered-table",
      (m: Model) => (element(m, "td").style.borderSpacing = 1.5),
      "border-spacing",
    ],
    [
      "a collapse",
      "collapsed-and-centred-tables",
      (m: Model) => (element(m, "td").style.borderCollapse = "separate"),
      "border-collapse",
    ],
    [
      "a shift's other side",
      "shifts",
      (m: Model) => (element(m, "span").style.bottom = "auto"),
      "bottom",
    ],
    [
      "a shift",
      "shifts",
      (m: Model) => (element(m, "span", 2).style.verticalAlign = "baseline"),
      "vertical-align",
    ],
    [
      "a position",
      "shifts",
      (m: Model) => (element(m, "span").style.position = "static"),
      "position",
    ],
    ["a display", "offsets", (m: Model) => (element(m, "dd").style.display = "inline"), "display"],
    [
      "an indent",
      "offsets",
      (m: Model) => (element(m, "p").style.textIndent = { lo: 0, hi: 0 }),
      "text-indent",
    ],
    [
      "a declared margin",
      "collapsed-and-centred-tables",
      (m: Model) => (element(m, "table", 3).style.margin.left = { lo: 0, hi: 0 }),
      "margin-left",
    ],
    [
      "a marker's text",
      "nested-lists",
      (m: Model) => ((m.markers[2] ?? { text: "" }).text = "▪ "),
      "marker text",
    ],
    [
      "a marker's style",
      "nested-lists",
      (m: Model) => ((m.markers[0] ?? { style: { underline: false } }).style.underline = true),
      "marker -webkit-text-decorations-in-effect",
    ],
  ])("catches %s", (_, name, mutate, property) => {
    const model = modelOf(name);
    mutate(model);
    expect(compare(model, name).map((mismatch) => mismatch.property)).toContain(property);
  });

  it("labels every mismatch a model mismatch, and refuses a model format it cannot read", () => {
    const model = modelOf("weights-and-styles");
    element(model, "b").style.fontWeight = 100;
    expect(
      compare(model, "weights-and-styles").every(({ reason }) => reason === "model-mismatch"),
    ).toBe(true);
    expect(compare({ ...modelOf("offsets"), format: "t-model/2.0.0" }, "offsets")).toEqual([
      { reason: "model-format", key: -1, property: "format", model: "t-model/2.0.0", chrome: "" },
    ]);
  });

  it("refuses a model that drops an authored tbody, has malformed keys or a wrong parent", () => {
    // The first code review: with the authored tbody removed and the keys renumbered, XML mode
    // matched. A tbody is skipped only where HTML mode inserted it.
    const model = modelOf("bordered-table");
    const tbody = model.elements.findIndex(({ name }) => name === "tbody");
    const renumber = new Map<number, number>();
    const kept = model.elements.filter((_, index) => index !== tbody);
    kept.forEach(({ key }, index) => renumber.set(key, index));
    const dropped: Model = {
      ...model,
      elements: kept.map((entry, index) => ({
        ...entry,
        key: index,
        parent:
          entry.parent === tbody
            ? (model.elements[tbody]?.parent ?? -1)
            : (renumber.get(entry.parent) ?? -1),
      })),
      text: [],
    };
    expect(
      compare(dropped, "bordered-table", "xml").some(({ property }) => property === "structure"),
    ).toBe(true);

    const gapped = structuredClone(model);
    const second = gapped.elements[1];
    if (second !== undefined) second.key = 7;
    expect(compare(gapped, "bordered-table")).toEqual([
      expect.objectContaining({ property: "structure", chrome: "(model malformed)" }),
    ]);
    const stray = { ...structuredClone(model), text: [{ key: 0, element: 999, start: 0, end: 1 }] };
    expect(compare(stray, "bordered-table")[0]?.chrome).toBe("(model malformed)");

    const reparented = structuredClone(model);
    const cell = reparented.elements.find(({ name }) => name === "td");
    if (cell !== undefined) cell.parent = 0;
    expect(
      compare(reparented, "bordered-table").some(({ property }) => property === "parent"),
    ).toBe(true);
  });

  it.each([
    ["a marker dropped", (m: Model) => m.markers.splice(1, 1)],
    ["every marker dropped", (m: Model) => m.markers.splice(0)],
    [
      "a marker duplicated",
      (m: Model) => m.markers.push(...structuredClone(m.markers.slice(0, 1))),
    ],
    ["a field 1.0.0 does not have", (m: Model) => Object.assign(m, { scanner: [] })],
    ["a text entry keyed out of place", (m: Model) => ((m.text[1] ?? { key: 0 }).key = 5)],
    [
      "inter-element text that does not cover its offsets",
      (m: Model) => ((m.text[2] ?? { start: 0 }).start += 1),
    ],
    ["a fold outside the section", (m: Model) => m.folds.push({ element: 9999, decision: "sup" })],
    ["a fold of another kind", (m: Model) => m.folds.push({ element: 1, decision: "raise" })],
    ["a waiver outside the text", (m: Model) => m.waivers.push({ start: 99999, end: 100000 })],
  ])("refuses a model with %s", (_, mutate) => {
    const model = modelOf("nested-lists");
    mutate(model);
    const found = compare(model, "nested-lists");
    expect(found.length).toBeGreaterThan(0);
    expect(found.every(({ reason }) => reason === "model-mismatch")).toBe(true);
  });

  it("accepts a well-formed waiver and refuses one out of order, and arrays that are not", () => {
    const model = modelOf("offsets");
    expect(
      validateModel({
        ...model,
        waivers: [
          { start: 0, end: 1 },
          { start: 2, end: 3 },
        ],
      }),
    ).toBeUndefined();
    expect(
      validateModel({
        ...model,
        waivers: [
          { start: 2, end: 3 },
          { start: 0, end: 1 },
        ],
      }),
    ).toMatch(/waivers/);
    expect(validateModel({ ...model, folds: {} as unknown as Model["folds"] })).toBe("arrays");
  });

  it("refuses a translucent background and a drawing with fewer elements than the model", () => {
    const model = modelOf("backgrounds-and-colours");
    const { elements, markers } = recording("backgrounds-and-colours", "html");
    const translucent = structuredClone(elements);
    const span = translucent.find(({ name }) => name === "span");
    if (span !== undefined) span.style["background-color"] = "rgba(255, 255, 0, 0.5)";
    expect(compareModel(model, translucent, new Map(markers), RATIO, "html")).toContainEqual(
      expect.objectContaining({ property: "background-color" }),
    );
    expect(
      compareModel(model, elements.slice(0, 3), new Map(markers), RATIO, "html"),
    ).toContainEqual(expect.objectContaining({ property: "structure" }));
  });

  it("refuses a list item Chrome draws without a marker style", () => {
    const model = modelOf("nested-lists");
    const { elements, markers } = recording("nested-lists", "html");
    const bare = structuredClone(elements);
    const item = bare.find(({ name }) => name === "li");
    if (item !== undefined) delete item.marker;
    expect(compareModel(model, bare, new Map(markers), RATIO, "html")).toContainEqual(
      expect.objectContaining({ property: "marker ::marker" }),
    );
  });

  it("names a malformed model a mismatch, never a crash", () => {
    const malformed = (mutate: (model: Model) => void) => {
      const model = modelOf("nested-lists");
      mutate(model);
      return compare(model, "nested-lists");
    };
    const refused: unknown = expect.arrayContaining([
      expect.objectContaining({ chrome: "(model malformed)" }),
    ]);
    expect(
      malformed(
        (m) =>
          delete (element(m, "p").style as Partial<Model["elements"][number]["style"]>).borders,
      ),
    ).toEqual(refused);
    expect(
      malformed((m) => delete (m.markers[0] as Partial<Model["markers"][number]>).style),
    ).toEqual(refused);
    expect(
      malformed(
        (m) => ((m.markers[0] ?? { style: {} }).style = {} as Model["markers"][number]["style"]),
      ),
    ).toEqual(refused);
    expect(malformed((m) => ((m.elements as unknown[])[1] = null))).toEqual(refused);
    expect(malformed((m) => ((m.text as unknown[])[0] = null))).toEqual(refused);
    expect(malformed((m) => ((m.elements[1] ?? { parent: 0 }).parent = -2))).toEqual(refused);
    expect(malformed((m) => Object.assign(m.elements[1] ?? {}, { extra: 1 }))).toEqual(refused);
    expect(malformed((m) => Object.assign(m.text[0] ?? {}, { interElement: true }))).toEqual(
      refused,
    );
  });

  it("allows auto margins only on a table's sides, and ranges only in order", () => {
    const refused: unknown = expect.arrayContaining([
      expect.objectContaining({ chrome: "(model malformed)" }),
    ]);
    const autoP = modelOf("offsets");
    element(autoP, "p").style.margin.left = "auto";
    expect(compare(autoP, "offsets")).toEqual(refused);
    const reversed = modelOf("offsets");
    element(reversed, "p").style.fontSize = { lo: 12, hi: 11 };
    expect(compare(reversed, "offsets")).toEqual(refused);
    const badColour = modelOf("offsets");
    element(badColour, "p").style.colours = [[0, 0, 256]];
    expect(compare(badColour, "offsets")).toEqual(refused);
  });

  it("reads which text is between table parts, and the waived signs, from the DOM itself", () => {
    const name = "line-ends-and-inter-element-text";
    const model = modelOf(name);
    const { texts } = recording(name, "xml");
    expect(compareText(model, texts)).toEqual([]);
    const flagged = structuredClone(model);
    const cell = flagged.text.find((entry) => entry.interElement !== true);
    if (cell !== undefined) cell.interElement = true;
    expect(compareText(flagged, texts).length).toBeGreaterThan(0);
    const unflagged = structuredClone(model);
    const between = unflagged.text.find((entry) => entry.interElement === true);
    if (between !== undefined) delete between.interElement;
    expect(compareText(unflagged, texts).length).toBeGreaterThan(0);
    expect(compareText({ ...model, waivers: [{ start: 0, end: 1 }] }, texts)).toContainEqual(
      expect.objectContaining({ property: "waiver 0", model: "+" }),
    );
  });

  it("compares a text node of any length", () => {
    const long = "a".repeat(300_000);
    const model = JSON.parse(modelSection(`${ROOT}<p>${long}</p>${MARKED}`)) as Model;
    const texts = model.text.map(({ element, start, end }) => ({
      parent: element,
      length: end - start,
      data: end - start === long.length ? long : "not for clinical use",
    }));
    expect(compareText(model, texts)).toEqual([]);
  });

  it("requires every marker Chrome draws to be in the model", () => {
    const model = modelOf("nested-lists");
    const { elements } = recording("nested-lists", "html");
    const extra = new Map(recording("nested-lists", "html").markers);
    const div = elements.findIndex(({ name }) => name === "div");
    extra.set(div, "• ");
    expect(compareModel(model, elements, extra, RATIO, "html")).toContainEqual(
      expect.objectContaining({ property: "marker", model: "none", chrome: "a marker" }),
    );
  });

  it("does not compare a centred table's auto margins, which depend on the width", () => {
    const model = modelOf("collapsed-and-centred-tables");
    expect(element(model, "table", 2).style.margin.left).toBe("auto");
    expect(element(model, "table", 3).style.margin.right).toBe("auto");
  });

  it("refuses a structure that differs, and a text range that does not match the DOM", () => {
    const model = modelOf("offsets");
    const dropped = { ...model, elements: model.elements.filter(({ name }) => name !== "dd") };
    expect(compare(dropped, "offsets").some(({ property }) => property === "structure")).toBe(true);
    const shifted = structuredClone(model);
    const first = shifted.text[1];
    if (first !== undefined) first.end += 1;
    expect(compareText(shifted, recording("offsets", "xml").texts).length).toBeGreaterThan(0);
    expect(
      compareText({ ...model, text: model.text.slice(1) }, recording("offsets", "xml").texts),
    ).toEqual([expect.objectContaining({ property: "structure", chrome: "(model malformed)" })]);
  });
});

describe("the comparison's arithmetic", () => {
  it("allows one unit in the sixth significant digit of Chrome's value", () => {
    expect(tolerance(14.6667)).toBeCloseTo(0.0001, 12);
    expect(tolerance(-6.66667)).toBeCloseTo(0.00001, 12);
    expect(lengthMatches({ lo: 11, hi: 11 }, "14.6667px")).toBe(true);
    expect(lengthMatches({ lo: 11, hi: 11 }, "14.6669px")).toBe(false);
    expect(lengthMatches({ lo: 7.5, hi: 9 }, "10.2778px")).toBe(true);
    expect(lengthMatches({ lo: 1, hi: 1 }, "auto")).toBe(false);
  });

  it("snaps borders with a one-pixel floor and spacing without, in exact arithmetic", () => {
    expect(borderAsDrawn(0.75, 1)).toBe(1);
    expect(borderAsDrawn(0.75, 0.8)).toBe(1.25);
    expect(borderAsDrawn(0.75, 1.25)).toBe(0.8);
    expect(borderAsDrawn(0.75, 2.625)).toBeCloseTo(0.761905, 6);
    // 1.2 pt × 4/3 × 1.25 is 2 exactly; in doubles it is 1.9999999999999998.
    expect(borderAsDrawn(1.2, 1.25)).toBe(1.6);
    expect(borderAsDrawn(0, 2)).toBe(0);
    expect(spacingAsDrawn(0.75, 0.8)).toBe(0);
    expect(spacingAsDrawn(1.5, 1.25)).toBe(1.6);
    expect(spacingAsDrawn(0, 1)).toBe(0);
  });

  it("reads colours with their alpha", () => {
    expect(parseColour("rgb(1, 2, 3)")).toEqual({ rgb: [1, 2, 3], alpha: 1 });
    expect(parseColour("rgba(0, 0, 0, 0)")).toEqual({ rgb: [0, 0, 0], alpha: 0 });
    expect(parseColour("rgba(0, 0, 0, 0.5)")?.alpha).toBe(0.5);
    expect(parseColour("red")).toBeUndefined();
  });
});
