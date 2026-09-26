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
  // A table centred with both margins `auto` is not compared side by side, but Chrome's used
  // margins must then be equal: `auto` cannot switch the comparison off for a table that is not
  // centred.
  if (model.margin.left === "auto" && model.margin.right === "auto") {
    const left = parsePixels(at("margin-left"));
    const right = parsePixels(at("margin-right"));
    check(
      "margin-left",
      left !== undefined &&
        right !== undefined &&
        Math.abs(left - right) <= tolerance(Math.max(left, right)) * 2 + 0.5,
      "auto, centred",
      `${at("margin-left")} / ${at("margin-right")}`,
    );
  }
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

const TOP_LEVEL = new Set(["format", "elements", "text", "markers", "folds", "waivers"]);
const ENTRY_FIELDS: Readonly<Record<string, readonly string[]>> = {
  elements: ["key", "name", "parent", "style"],
  text: ["key", "element", "start", "end", "interElement"],
  markers: ["element", "text", "style"],
  folds: ["element", "decision"],
  waivers: ["start", "end"],
};
const STYLE_FIELDS = [
  "fontSize",
  "fontWeight",
  "fontStyle",
  "colours",
  "lineHeight",
  "underline",
  "backgrounds",
  "textIndent",
  "margin",
  "padding",
  "borders",
  "borderCollapse",
  "borderSpacing",
  "display",
  "position",
  "top",
  "bottom",
  "verticalAlign",
];
const MARKER_STYLE_FIELDS = [
  "fontSize",
  "fontWeight",
  "fontStyle",
  "colours",
  "lineHeight",
  "backgrounds",
  "underline",
];
// The containers whose whitespace T's walk drops and the model flags `interElement` (M1).
const CONTAINERS = new Set(["table", "thead", "tbody", "tfoot", "tr", "ol", "ul"]);

const FONT_STYLES = new Set(["normal", "italic", "oblique"]);
const LINE_STYLES = new Set(["solid", "double", "dotted", "dashed", "inset", "outset"]);
const DISPLAYS = new Set([
  "block",
  "inline",
  "list-item",
  "table",
  "table-caption",
  "table-header-group",
  "table-row-group",
  "table-footer-group",
  "table-row",
  "table-cell",
]);
const ALIGN_KEYWORDS = new Set(["baseline", "super", "sub", "top", "middle", "bottom"]);

const isRange = (value: unknown): boolean =>
  shaped(value, ["lo", "hi"]) &&
  Number.isFinite((value as Range).lo) &&
  Number.isFinite((value as Range).hi) &&
  (value as Range).lo <= (value as Range).hi;
const isRgb = (value: unknown): boolean =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every((channel) => isInteger(channel) && channel >= 0 && channel <= 255);
const isColours = (value: unknown, allowEmpty = false): boolean =>
  Array.isArray(value) && (allowEmpty || value.length > 0) && value.every(isRgb);
// A text colour set: one colour, or both link colours, sorted by red, green, blue (M3).
const isColourSet = (value: unknown): boolean =>
  isColours(value) &&
  (value as number[][]).length <= 2 &&
  (value as number[][]).every(
    (colour, index, all) => index === 0 || compareRgb(all[index - 1] ?? [], colour) < 0,
  );
function compareRgb(first: readonly number[], second: readonly number[]): number {
  return (
    (first[0] ?? 0) - (second[0] ?? 0) ||
    (first[1] ?? 0) - (second[1] ?? 0) ||
    (first[2] ?? 0) - (second[2] ?? 0)
  );
}
const isSides = (value: unknown, side: (entry: unknown, name: string) => boolean): boolean =>
  shaped(value, ["top", "right", "bottom", "left"]) &&
  ["top", "right", "bottom", "left"].every((name) =>
    side((value as Record<string, unknown>)[name], name),
  );

// The values of a marker's style, as M2 and M3 state them.
function markerStyleValid(style: Record<string, unknown>): boolean {
  return (
    isRange(style.fontSize) &&
    (style.fontSize as Range).lo > 0 &&
    isInteger(style.fontWeight) &&
    style.fontWeight >= 100 &&
    style.fontWeight <= 900 &&
    FONT_STYLES.has(style.fontStyle as string) &&
    isColourSet(style.colours) &&
    (style.lineHeight === "normal" || isRange(style.lineHeight)) &&
    typeof style.underline === "boolean" &&
    isColours(style.backgrounds, true)
  );
}

// The values of an element's style: every one of M3's kinds, and `auto` only on a table's left
// and right margins, the one value that turns a comparison off.
function styleValid(style: Record<string, unknown>, name: string): boolean {
  const border = (value: unknown): boolean =>
    value === "none" ||
    (shaped(value, ["width", "style", "colours"]) &&
      Number.isFinite((value as { width: number }).width) &&
      (value as { width: number }).width >= 0 &&
      LINE_STYLES.has((value as { style: string }).style) &&
      isColours((value as { colours: unknown }).colours));
  return (
    markerStyleValid(style) &&
    isRange(style.textIndent) &&
    isSides(
      style.margin,
      (value, side) =>
        isRange(value) ||
        (value === "auto" && name === "table" && (side === "left" || side === "right")),
    ) &&
    isSides(style.padding, isRange) &&
    isSides(style.borders, border) &&
    (style.borderCollapse === "collapse" || style.borderCollapse === "separate") &&
    Number.isFinite(style.borderSpacing) &&
    (style.borderSpacing as number) >= 0 &&
    DISPLAYS.has(style.display as string) &&
    (style.position === "static" || style.position === "relative") &&
    (style.top === "auto" || isRange(style.top)) &&
    (style.bottom === "auto" || isRange(style.bottom)) &&
    (ALIGN_KEYWORDS.has(style.verticalAlign as string) || isRange(style.verticalAlign))
  );
}

// An object whose own fields are among `allowed`, with every one of `required`.
function shaped(value: unknown, allowed: readonly string[], required = allowed): boolean {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const fields = Object.keys(value);
  return (
    fields.every((field) => allowed.includes(field)) &&
    required.every((field) => fields.includes(field))
  );
}
const DECISIONS = new Set(["delete", "sup", "sub"]);
const isInteger = (value: unknown): value is number => Number.isInteger(value);

// The model read as data (M2, M4): its fields and no others, keys 0 to n − 1 in order with each
// parent before its child, text entries keyed by position and covering the offsets in order,
// one marker per list item and only on list items, folds and waivers within the section. Returns
// what is wrong, or undefined.
export function validateModel(model: Model): string | undefined {
  const fields = Object.keys(model);
  if (fields.some((field) => !TOP_LEVEL.has(field)) || fields.length !== TOP_LEVEL.size) {
    return `exactly the fields ${[...TOP_LEVEL].join(", ")}`;
  }
  const { elements, text, markers, folds, waivers } = model;
  if (![elements, text, markers, folds, waivers].every(Array.isArray)) return "arrays";
  for (const [field, entries] of Object.entries({ elements, text, markers, folds, waivers })) {
    const allowed = ENTRY_FIELDS[field] ?? [];
    const required = field === "text" ? allowed.filter((name) => name !== "interElement") : allowed;
    if (!(entries as unknown[]).every((entry) => shaped(entry, allowed, required))) {
      return `${field} entries of M2's shape`;
    }
  }
  if (
    !elements.every(
      ({ style, name }) =>
        shaped(style, STYLE_FIELDS) &&
        styleValid(style as unknown as Record<string, unknown>, name),
    )
  ) {
    return "styles of M3's shape and values";
  }
  if (
    !markers.every(
      ({ style }) =>
        shaped(style, MARKER_STYLE_FIELDS) &&
        markerStyleValid(style as unknown as Record<string, unknown>),
    )
  ) {
    return "marker styles of M2's shape and values";
  }
  const dense = elements.every(
    ({ key, name, parent }, index) =>
      typeof (name as unknown) === "string" &&
      key === index &&
      isInteger(parent) &&
      parent < index &&
      (index === 0 ? parent === -1 : parent >= 0),
  );
  if (!dense) return "keys 0 to n - 1, parents first";
  let offset = 0;
  for (const [index, entry] of text.entries()) {
    if (
      entry.key !== index ||
      entry.start !== offset ||
      !isInteger(entry.end) ||
      entry.end < offset
    ) {
      return "text entries keyed by position, covering the offsets in order";
    }
    if (!isInteger(entry.element) || entry.element < 0 || entry.element >= elements.length) {
      return "text entries naming its elements";
    }
    // `interElement` only as true, and only in a table or list container (M1): the judge compares
    // no style for such a node, so the flag is no exemption elsewhere.
    // The model arrives as JSON, whatever its type says: the flag is read as unknown.
    const flag: unknown = (entry as { interElement?: unknown }).interElement;
    if (
      flag !== undefined &&
      (flag !== true || !CONTAINERS.has(elements[entry.element]?.name ?? ""))
    ) {
      return "interElement only on whitespace in a table or list container";
    }
    offset = entry.end;
  }
  const items = elements.filter(({ name }) => name === "li").map(({ key }) => key);
  const marked = markers.map(({ element }) => element);
  if (marked.length !== items.length || marked.some((element, index) => element !== items[index])) {
    return "one marker per list item, in order";
  }
  const folded = folds.map(({ element }) => element);
  if (
    folds.some(
      ({ element, decision }) =>
        !isInteger(element) ||
        element <= 0 ||
        element >= elements.length ||
        !DECISIONS.has(decision),
    ) ||
    folded.some((element, index) => index > 0 && element <= (folded[index - 1] ?? -1))
  ) {
    return "folds on its elements, in order, each delete, sup or sub";
  }
  let previous = -1;
  for (const { start, end } of waivers) {
    if (!isInteger(start) || start <= previous || end !== start + 1 || end > offset) {
      return "waivers of single code points within the text, in order";
    }
    previous = start;
  }
  return undefined;
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
  const malformed = validateModel(model);
  if (malformed !== undefined) return structure(malformed, "(model malformed)");
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
    } else if (
      mode === "html" &&
      element.name === "tbody" &&
      expected?.name === "tr" &&
      model.elements[expected.parent]?.name === "table" &&
      placed[expected.parent] === element.parent
    ) {
      // Where the HTML parser inserted it: between a table and a row the model puts directly in
      // that table (the second code review: any other tbody is a structure mismatch).
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
  // Every list item Chrome draws a marker for has one in the model, and no other (M2, M4).
  const chromeMarkers = new Set<number>();
  for (const index of markers.keys()) {
    const key = keyOf.get(index);
    chromeMarkers.add(key ?? -1);
  }
  const modelMarkers = new Set(model.markers.map(({ element }) => element));
  for (const key of new Set([...chromeMarkers, ...modelMarkers])) {
    if (chromeMarkers.has(key) !== modelMarkers.has(key)) {
      mismatches.push({
        reason: "model-mismatch",
        key,
        property: "marker",
        model: modelMarkers.has(key) ? "a marker" : "none",
        chrome: chromeMarkers.has(key) ? "a marker" : "none",
      });
    }
  }
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
    try {
      compareElement(check, style, elements, index, ratio);
    } catch {
      // A style object of the wrong shape: the model is malformed, never a crash of the judge.
      check("style", false, "a style of M3's shape", "(model malformed)");
    }
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
    try {
      compareMarker(check, marker.style, elements[index]?.marker);
      const chain = backgroundChain(elements, index);
      check(
        "background-color",
        sameChain(marker.style.backgrounds, chain),
        marker.style.backgrounds,
        show(chain),
      );
    } catch {
      check("style", false, "a marker style of M2's shape", "(model malformed)");
    }
  }
  return mismatches;
}

// M1 in XML mode: the model's text nodes, their parents and their ranges equal the DOM's.
export function compareText(model: Model, texts: readonly ChromeText[]): Mismatch[] {
  const mismatches: Mismatch[] = [];
  const malformed = validateModel(model);
  if (malformed !== undefined) {
    return [
      {
        reason: "model-mismatch",
        key: -1,
        property: "structure",
        model: malformed,
        chrome: "(model malformed)",
      },
    ];
  }
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
  const points: string[] = [];
  texts.forEach((text, index) => {
    const entry = model.text[index];
    const expected = { element: text.parent, start: offset, end: offset + text.length };
    offset += text.length;
    points.push(...Array.from(text.data));
    // The judge's own reading of M1: a node directly in a table or list container is the
    // whitespace T's walk drops, and only such a node is flagged.
    // ... and holds only the ASCII whitespace T allows there.
    const between =
      CONTAINERS.has(model.elements[text.parent]?.name ?? "") && /^[\t\n\f\r ]*$/u.test(text.data);
    if (
      entry?.element !== expected.element ||
      entry.start !== expected.start ||
      entry.end !== expected.end ||
      (entry.interElement === true) !== between
    ) {
      mismatches.push({
        reason: "model-mismatch",
        key: entry?.element ?? -1,
        property: `text ${index}`,
        model: show(entry),
        chrome: show({ ...expected, interElement: between }),
      });
    }
  });
  // Each waived code point is a plus sign in the DOM's text.
  for (const { start } of model.waivers) {
    if (points[start] !== "+") {
      mismatches.push({
        reason: "model-mismatch",
        key: -1,
        property: `waiver ${start}`,
        model: "+",
        chrome: points[start] ?? "(none)",
      });
    }
  }
  return mismatches;
}
