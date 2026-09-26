import type { MarkerStyle, Model, ModelBorder, ModelStyle, Range } from "../authority/t/model.js";
import type { ChromeElement, ChromeText } from "./measure.js";

// R3's comparison (docs/design/authority-import-renderer.md, and its model addendum, M3 and M4):
// T's model of every element and list marker against Chrome's computed style. Pure: it reads
// T's model only as data (a type import, erased when compiled) and Chrome's values as strings.
// Every difference is a refusal of ours, `model-mismatch`.

// The model formats this judge reads (the addendum's M5); any other is refused, `model-format`.
export const KNOWN_FORMATS: ReadonlySet<string> = new Set(["t-model/1.0.0"]);

export type Mismatch = {
  // A refusal of ours: `model-mismatch`, or `model-format` for a model this judge cannot read.
  reason: "model-mismatch" | "model-format";
  // The element's key in the model's index space, or -1 where the structure itself differs.
  key: number;
  property: string;
  model: string;
  chrome: string;
};

type Rgb = readonly [number, number, number];

// One unit in the sixth significant digit of |v|, the precision Chrome serialises lengths to.
export function tolerance(value: number): number {
  const magnitude = Math.abs(value);
  if (magnitude === 0) return 1e-9;
  return 10 ** (Math.floor(Math.log10(magnitude)) - 5);
}

export function parsePixels(value: string): number | undefined {
  const match = /^(-?\d+(?:\.\d+)?(?:e-?\d+)?)px$/u.exec(value.trim());
  return match === null ? undefined : Number(match[1]);
}

// A modelled range in points against Chrome's pixels.
export function lengthMatches(range: Range, chrome: string): boolean {
  const pixels = parsePixels(chrome);
  if (pixels === undefined) return false;
  const t = tolerance(pixels);
  return (range.lo * 4) / 3 - t <= pixels && pixels <= (range.hi * 4) / 3 + t;
}

// Exact arithmetic for the snap: the product rounded to 1e-9 before the floor (M3), so binary
// floating point cannot floor 2 − 2⁻⁵² to 1.
function deviceFloor(points: number, ratio: number): number {
  return Math.floor(Math.round(((points * 4) / 3) * ratio * 1e9) / 1e9);
}

// A border's width as Chrome draws it: whole device pixels, at least one above zero.
export function borderAsDrawn(points: number, ratio: number): number {
  if (points <= 0) return 0;
  return Math.max(1, deviceFloor(points, ratio)) / ratio;
}

// `border-spacing` as drawn: whole device pixels, with no minimum.
export function spacingAsDrawn(points: number, ratio: number): number {
  if (points <= 0) return 0;
  return deviceFloor(points, ratio) / ratio;
}

function pixelsMatch(expected: number, chrome: string): boolean {
  const pixels = parsePixels(chrome);
  return pixels !== undefined && Math.abs(pixels - expected) <= tolerance(pixels);
}

export function parseColour(value: string): { rgb: Rgb; alpha: number } | undefined {
  const match = /^rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*(\d*(?:\.\d+)?))?\s*\)$/u.exec(
    value.trim(),
  );
  if (match === null) return undefined;
  return {
    rgb: [Number(match[1]), Number(match[2]), Number(match[3])],
    alpha: match[4] === undefined ? 1 : Number(match[4]),
  };
}

function colourIn(set: readonly Rgb[], chrome: string): boolean {
  const parsed = parseColour(chrome);
  if (parsed?.alpha !== 1) return false;
  return set.some(([r, g, b]) => r === parsed.rgb[0] && g === parsed.rgb[1] && b === parsed.rgb[2]);
}

const show = (value: unknown): string => JSON.stringify(value);

// The opaque background colours from the div down to `index`, outermost first; undefined where a
// translucent one stands in the chain.
function backgroundChain(elements: readonly ChromeElement[], index: number): Rgb[] | undefined {
  const chain: Rgb[] = [];
  for (let at = index; at >= 0; at = elements[at]?.parent ?? -1) {
    const parsed = parseColour(elements[at]?.style["background-color"] ?? "");
    if (parsed === undefined) return undefined;
    if (parsed.alpha === 0) continue;
    if (parsed.alpha !== 1) return undefined;
    chain.unshift(parsed.rgb);
  }
  return chain;
}

function sameChain(model: readonly Rgb[], chrome: readonly Rgb[] | undefined): boolean {
  return (
    chrome?.length === model.length &&
    chrome.every((colour, index) => colour.every((channel, c) => channel === model[index]?.[c]))
  );
}

function lineHeightMatches(model: "normal" | Range, chrome: string): boolean {
  if (model === "normal") return chrome === "normal";
  return lengthMatches(model, chrome);
}

// A keyword or a range, pt, against Chrome's value.
function keywordOrLength(model: string | Range, chrome: string): boolean {
  if (typeof model === "string") return chrome === model;
  return lengthMatches(model, chrome);
}

type Check = (property: string, ok: boolean, model: unknown, chrome: string) => void;

function compareTyped(
  check: Check,
  style: Pick<ModelStyle, "fontSize" | "fontWeight" | "fontStyle" | "colours" | "lineHeight"> & {
    underline: boolean;
  },
  chrome: Record<string, string>,
): void {
  const at = (property: string): string => chrome[property] ?? "";
  check(
    "font-size",
    lengthMatches(style.fontSize, at("font-size")),
    style.fontSize,
    at("font-size"),
  );
  check(
    "font-weight",
    Number(at("font-weight")) === style.fontWeight,
    style.fontWeight,
    at("font-weight"),
  );
  check("font-style", at("font-style") === style.fontStyle, style.fontStyle, at("font-style"));
  check("color", colourIn(style.colours, at("color")), style.colours, at("color"));
  check(
    "line-height",
    lineHeightMatches(style.lineHeight, at("line-height")),
    style.lineHeight,
    at("line-height"),
  );
  const decorations = at("-webkit-text-decorations-in-effect");
  check(
    "-webkit-text-decorations-in-effect",
    decorations === (style.underline ? "underline" : "none"),
    style.underline,
    decorations,
  );
}

function compareBorder(
  check: Check,
  side: string,
  model: ModelBorder,
  chrome: Record<string, string>,
  ratio: number,
): void {
  const width = chrome[`border-${side}-width`] ?? "";
  const style = chrome[`border-${side}-style`] ?? "";
  const colour = chrome[`border-${side}-color`] ?? "";
  if (model === "none") {
    check(`border-${side}-style`, style === "none", "none", style);
    check(`border-${side}-width`, pixelsMatch(0, width), 0, width);
    return;
  }
  check(`border-${side}-style`, style === model.style, model.style, style);
  check(
    `border-${side}-width`,
    pixelsMatch(borderAsDrawn(model.width, ratio), width),
    model.width,
    width,
  );
  check(`border-${side}-color`, colourIn(model.colours, colour), model.colours, colour);
}

function compareElement(
  check: Check,
  model: ModelStyle,
  elements: readonly ChromeElement[],
  index: number,
  ratio: number,
): void {
  const chrome = elements[index]?.style ?? {};
  const at = (property: string): string => chrome[property] ?? "";
  compareTyped(check, model, chrome);
  const chain = backgroundChain(elements, index);
  check("background-color", sameChain(model.backgrounds, chain), model.backgrounds, show(chain));
  check(
    "text-indent",
    lengthMatches(model.textIndent, at("text-indent")),
    model.textIndent,
    at("text-indent"),
  );
  for (const side of ["top", "right", "bottom", "left"] as const) {
    const margin = model.margin[side];
    if (margin !== "auto") {
      check(
        `margin-${side}`,
        lengthMatches(margin, at(`margin-${side}`)),
        margin,
        at(`margin-${side}`),
      );
    }
    const padding = model.padding[side];
    check(
      `padding-${side}`,
      lengthMatches(padding, at(`padding-${side}`)),
      padding,
      at(`padding-${side}`),
    );
    compareBorder(check, side, model.borders[side], chrome, ratio);
  }
  check(
    "border-collapse",
    at("border-collapse") === model.borderCollapse,
    model.borderCollapse,
    at("border-collapse"),
  );
  const spacing = at("border-spacing").split(/\s+/u);
  const drawn = spacingAsDrawn(model.borderSpacing, ratio);
  check(
    "border-spacing",
    spacing.length >= 1 &&
      spacing.length <= 2 &&
      spacing.every((value) => pixelsMatch(drawn, value)),
    model.borderSpacing,
    at("border-spacing"),
  );
  check("display", at("display") === model.display, model.display, at("display"));
  check("position", at("position") === model.position, model.position, at("position"));
  check("top", keywordOrLength(model.top, at("top")), model.top, at("top"));
  check("bottom", keywordOrLength(model.bottom, at("bottom")), model.bottom, at("bottom"));
  check(
    "vertical-align",
    keywordOrLength(model.verticalAlign, at("vertical-align")),
    model.verticalAlign,
    at("vertical-align"),
  );
}

function compareMarker(
  check: Check,
  model: MarkerStyle,
  marker: Record<string, string> | undefined,
): void {
  if (marker === undefined) {
    check("::marker", false, "a marker", "none");
    return;
  }
  compareTyped(check, model, marker);
}

// R3 for one drawing of one section. `elements` are Chrome's in pre-order (an element the HTML
// parser inserted, a `tbody`, among them); `markers` Chrome's marker texts by that pre-order
// index; `ratio` the drawing's device pixel ratio.
export function compareModel(
  model: Model,
  elements: readonly ChromeElement[],
  markers: ReadonlyMap<number, string>,
  ratio: number,
  mode: "html" | "xml",
): Mismatch[] {
  if (!KNOWN_FORMATS.has(model.format)) {
    return [
      { reason: "model-format", key: -1, property: "format", model: model.format, chrome: "" },
    ];
  }
  const mismatches: Mismatch[] = [];
  const structure = (model: string, chrome: string): Mismatch[] => {
    mismatches.push({ reason: "model-mismatch", key: -1, property: "structure", model, chrome });
    return mismatches;
  };
  // The model is read as data: its keys run 0 to n − 1 in order, each parent before its child,
  // and every text entry names an element it has.
  const keysDense = model.elements.every(
    ({ key, parent }, index) =>
      key === index && parent < index && (index === 0) === (parent === -1),
  );
  if (!keysDense) return structure("keys 0 to n - 1, parents first", "(model malformed)");
  if (model.text.some(({ element }) => element < 0 || element >= model.elements.length)) {
    return structure("text entries naming its elements", "(model malformed)");
  }
  // Chrome's index of each model key: the elements in order, skipping only a `tbody` the HTML
  // parser inserted, in HTML mode, where the model has no `tbody` at that position (M1).
  const placed: number[] = [];
  const inserted = new Set<number>();
  let next = 0;
  elements.forEach((element, index) => {
    const expected = model.elements[next];
    if (element.name === expected?.name) {
      placed.push(index);
      next += 1;
    } else if (mode === "html" && element.name === "tbody") {
      inserted.add(index);
    } else {
      structure(expected?.name ?? "(none)", element.name);
    }
  });
  if (next !== model.elements.length) {
    return structure(`${model.elements.length} elements`, `${next} matched`);
  }
  // Each element's parent, through an inserted `tbody`, is the model's.
  const keyOf = new Map(placed.map((index, key) => [index, key]));
  model.elements.forEach(({ key, parent }) => {
    let at = elements[placed[key] ?? -1]?.parent ?? -1;
    while (inserted.has(at)) at = elements[at]?.parent ?? -1;
    const chromeParent = at === -1 ? -1 : (keyOf.get(at) ?? -2);
    if (chromeParent !== parent) {
      mismatches.push({
        reason: "model-mismatch",
        key,
        property: "parent",
        model: String(parent),
        chrome: String(chromeParent),
      });
    }
  });
  model.elements.forEach(({ key, style }) => {
    const index = placed[key] ?? -1;
    const check: Check = (property, ok, modelValue, chrome) => {
      if (!ok) {
        mismatches.push({
          reason: "model-mismatch",
          key,
          property,
          model: show(modelValue),
          chrome,
        });
      }
    };
    compareElement(check, style, elements, index, ratio);
  });
  for (const marker of model.markers) {
    const index = placed[marker.element] ?? -1;
    const check: Check = (property, ok, modelValue, chrome) => {
      if (!ok)
        mismatches.push({
          reason: "model-mismatch",
          key: marker.element,
          property: `marker ${property}`,
          model: show(modelValue),
          chrome,
        });
    };
    const text = markers.get(index);
    check("text", text === marker.text, marker.text, text ?? "(none)");
    compareMarker(check, marker.style, elements[index]?.marker);
    const chain = backgroundChain(elements, index);
    check(
      "background-color",
      sameChain(marker.style.backgrounds, chain),
      marker.style.backgrounds,
      show(chain),
    );
  }
  return mismatches;
}

// M1 in XML mode: the model's text nodes, their parents and their ranges equal the DOM's.
export function compareText(model: Model, texts: readonly ChromeText[]): Mismatch[] {
  const mismatches: Mismatch[] = [];
  if (texts.length !== model.text.length) {
    mismatches.push({
      reason: "model-mismatch",
      key: -1,
      property: "text nodes",
      model: String(model.text.length),
      chrome: String(texts.length),
    });
    return mismatches;
  }
  let offset = 0;
  texts.forEach((text, index) => {
    const entry = model.text[index];
    const expected = { element: text.parent, start: offset, end: offset + text.length };
    offset += text.length;
    if (
      entry?.element !== expected.element ||
      entry.start !== expected.start ||
      entry.end !== expected.end
    ) {
      mismatches.push({
        reason: "model-mismatch",
        key: entry?.element ?? -1,
        property: `text ${index}`,
        model: show(entry),
        chrome: show(expected),
      });
    }
  });
  return mismatches;
}
