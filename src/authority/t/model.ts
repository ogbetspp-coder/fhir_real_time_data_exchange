import { xhtmlToText, XhtmlError } from "../../fidelity/xhtml.js";
import { canonicalJson } from "../../lib/hash.js";
import type { Rgb } from "./css.js";
import type { ComputedStyle, Size } from "./style.js";
import { analyseDocument } from "./document.js";
import { analyseSection, colours, markerFor, type Analysis } from "./transform.js";
import { treeIndex, type ElementNode, type TextNode } from "./tree.js";

// T's model output (docs/design/authority-import-renderer-model.md): for a section T accepts,
// every element's modelled style, every text node's range, every list marker, T4's decisions and
// T5's waived signs, in the XML-mode DOM's index space (M1). The renderer gate's judge compares it
// with Chrome's computed style (the renderer note's R3) without loading T's code. It is computed
// from T's own analysis of the div; it has no path of its own through the markup.

export const MODEL_FORMAT = "t-model/1.0.0";

export type Range = { lo: number; hi: number };
export type ModelBorder = "none" | { width: number; style: string; colours: Rgb[] };
type Sides<T> = { top: T; right: T; bottom: T; left: T };

export type ModelStyle = {
  fontSize: Range;
  fontWeight: number;
  fontStyle: string;
  colours: Rgb[];
  lineHeight: "normal" | Range;
  underline: boolean;
  backgrounds: Rgb[];
  textIndent: Range;
  margin: Sides<Range | "auto">;
  padding: Sides<Range>;
  borders: Sides<ModelBorder>;
  borderCollapse: "collapse" | "separate";
  borderSpacing: number;
  display: string;
  position: "static" | "relative";
  top: Range | "auto";
  bottom: Range | "auto";
  verticalAlign: string | Range;
};

export type MarkerStyle = Pick<
  ModelStyle,
  "fontSize" | "fontWeight" | "fontStyle" | "colours" | "lineHeight" | "backgrounds" | "underline"
>;

export type Model = {
  format: string;
  // `parent` is the parent element's key, -1 for the div.
  elements: { key: number; name: string; parent: number; style: ModelStyle }[];
  text: { key: number; element: number; start: number; end: number; interElement?: true }[];
  markers: { element: number; text: string; style: MarkerStyle }[];
  folds: { element: number; decision: string }[];
  waivers: { start: number; end: number }[];
};

const GRAY: Rgb = [128, 128, 128];
const SIDES = ["top", "right", "bottom", "left"] as const;
type Side = (typeof SIDES)[number];
const HEADING_MARGIN_EM: Readonly<Record<string, number>> = {
  h1: 0.67,
  h2: 0.83,
  h3: 1,
  h4: 1.33,
  h5: 1.67,
  h6: 2.33,
};
const DISPLAY: Readonly<Record<string, string>> = {
  li: "list-item",
  table: "table",
  caption: "table-caption",
  thead: "table-header-group",
  tbody: "table-row-group",
  tfoot: "table-footer-group",
  tr: "table-row",
  td: "table-cell",
  th: "table-cell",
};
const BLOCK_DISPLAY = new Set([
  "div",
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "blockquote",
  "dl",
  "dt",
  "dd",
  "ol",
  "ul",
  "hr",
]);
const GROUPS = new Set(["thead", "tbody", "tfoot"]);
const LISTS = new Set(["dl", "ol", "ul"]);
const VALIGN = new Set(["top", "middle", "bottom", "baseline"]);

function exact(value: number): Range {
  return { lo: value, hi: value };
}

function scaled(factor: number, size: Size): Range {
  const first = factor * size.lo;
  const second = factor * size.hi;
  return { lo: Math.min(first, second), hi: Math.max(first, second) };
}

function sortedColours(list: readonly Rgb[]): Rgb[] {
  const unique = new Map(list.map((colour) => [colour.join(","), [...colour] as unknown as Rgb]));
  return [...unique.values()].sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
}

function attribute(element: ElementNode, name: string): string | undefined {
  return element.attributes.find((candidate) => candidate.name === name)?.value;
}

function nearestTable(element: ElementNode): ElementNode | undefined {
  for (let node: ElementNode | undefined = element; node !== undefined; node = node.parent) {
    if (node.name === "table") return node;
  }
  return undefined;
}

function hasListAncestor(element: ElementNode): boolean {
  for (let node = element.parent; node !== undefined; node = node.parent) {
    if (LISTS.has(node.name)) return true;
  }
  return false;
}

function tableBorder(table: ElementNode | undefined): number {
  const value = table === undefined ? undefined : attribute(table, "border");
  return value === undefined ? 0 : Number(value.trim());
}

type Context = {
  analysis: Analysis;
  styles: Map<ElementNode, ModelStyle>;
};

function styleOf(context: Context, element: ElementNode): ComputedStyle {
  const info = context.analysis.walk.infos.get(element);
  if (info === undefined) throw new Error(`no computed style for <${element.name}>`);
  return info.style;
}

function textColours(element: ElementNode, style: ComputedStyle): Rgb[] {
  if (element.name === "hr") {
    return style.declared.has("color") && style.colour !== undefined ? [style.colour] : [GRAY];
  }
  return sortedColours(colours(style));
}

function verticalMargin(element: ElementNode, style: ComputedStyle, side: "top" | "bottom"): Range {
  if (style.declared.has(`margin-${side}`)) return exact(style.margin[side]);
  const { name } = element;
  let em = HEADING_MARGIN_EM[name] ?? 0;
  if (name === "p" || name === "blockquote") em = 1;
  if (LISTS.has(name)) em = hasListAncestor(element) ? 0 : 1;
  if (name === "hr") em = 0.5;
  return scaled(em, style.size);
}

function horizontalMargin(
  element: ElementNode,
  style: ComputedStyle,
  side: "left" | "right",
): Range | "auto" {
  const centred =
    // Chrome matches the value without case but does not trim it (the fifth review).
    element.name === "table" && attribute(element, "align")?.toLowerCase() === "center";
  if (centred && !style.declared.has(`margin-${side}`)) return "auto";
  return exact(style.margin[side]);
}

type Part = "width" | "style" | "color";

// Whether the element declares one longhand of a border, through itself or a shorthand that
// covers it. T's `declared` set holds the properties as written; border shorthands are expanded
// here.
function declaresPart(style: ComputedStyle, side: string, part: Part): boolean {
  const { declared } = style;
  return (
    declared.has("border") ||
    declared.has(`border-${side}`) ||
    declared.has(`border-${part}`) ||
    declared.has(`border-${side}-${part}`)
  );
}

// A border colour longhand as computed: a colour set, or `currentcolor`, which each element
// resolves against its own colour (M3, "Tables").
type ColourValue = Rgb[] | "currentcolor";

const PARTS_INHERITING = new Set(["thead", "tbody", "tfoot", "tr"]);

// Chrome's own stylesheet gives row groups and rows `border-color: inherit`, and a table's
// `border` attribute gives its cells the same; every other element's undeclared colour is
// `currentcolor`. A declared colour is its value; a shorthand that omits it is `currentcolor`.
function borderColourValue(context: Context, element: ElementNode, side: Side): ColourValue {
  const style = styleOf(context, element);
  const declared = style.borders[side];
  if (declared !== undefined && declaresPart(style, side, "color")) {
    return declared.currentColour === true ? "currentcolor" : sortedColours(declared.colours ?? []);
  }
  const isCell = element.name === "td" || element.name === "th";
  const inherits =
    PARTS_INHERITING.has(element.name) || (isCell && tableBorder(nearestTable(element)) > 0);
  const parent = element.parent;
  if (inherits && parent !== undefined) return borderColourValue(context, parent, side);
  return "currentcolor";
}

// Each side's border, one longhand at a time (M3): a declared longhand over the presentational
// one (a table's `border` attribute on the table and its cells, `hr`'s own), over the initial
// value (`none`, `medium`, the element's colour).
function borders(
  context: Context,
  element: ElementNode,
  style: ComputedStyle,
  own: Rgb[],
): Sides<ModelBorder> {
  const isCell = element.name === "td" || element.name === "th";
  const attributeWidth =
    element.name === "table"
      ? tableBorder(element)
      : isCell && tableBorder(nearestTable(element)) > 0
        ? 1
        : 0;
  const result = {} as Sides<ModelBorder>;
  for (const side of SIDES) {
    let width = 2.25;
    let lineStyle = "none";
    if (element.name === "hr") {
      width = 0.75;
      lineStyle = "inset";
    } else if (element.name === "table" && attribute(element, "border") !== undefined) {
      // Any `border` attribute sets the table's widths, `0` included; its `outset` style only
      // where it is not 0 (the fifth review, measured).
      width = attributeWidth * 0.75;
      if (attributeWidth > 0) lineStyle = "outset";
    } else if (attributeWidth > 0) {
      width = attributeWidth * 0.75;
      lineStyle = "inset";
    }
    const declared = style.borders[side];
    if (declared !== undefined) {
      if (declaresPart(style, side, "width")) width = declared.widthPt;
      if (declaresPart(style, side, "style")) lineStyle = declared.style;
    }
    const colour = borderColourValue(context, element, side);
    result[side] =
      lineStyle === "none"
        ? "none"
        : { width, style: lineStyle, colours: colour === "currentcolor" ? own : colour };
  }
  return result;
}

function verticalAlign(
  context: Context,
  element: ElementNode,
  style: ComputedStyle,
): string | Range {
  const { name } = element;
  const valign = attribute(element, "valign")?.trim().toLowerCase();
  if (GROUPS.has(name)) return valign !== undefined && VALIGN.has(valign) ? valign : "middle";
  if (name === "tr") {
    if (valign !== undefined && VALIGN.has(valign)) return valign;
    const parent = element.parent;
    if (parent !== undefined && GROUPS.has(parent.name)) {
      return context.styles.get(parent)?.verticalAlign ?? "middle";
    }
    return "middle";
  }
  if (name === "td" || name === "th") {
    const declared = style.declaredVerticalAlign;
    if (declared !== undefined) return declared;
    if (valign !== undefined && VALIGN.has(valign)) return valign;
    const row = element.parent;
    return (row === undefined ? undefined : context.styles.get(row)?.verticalAlign) ?? "middle";
  }
  const shift = style.shift;
  if (style.declaredPosition) return "baseline";
  if (shift.kind === "super") return "super";
  if (shift.kind === "sub") return "sub";
  if (shift.kind === "points") return exact(shift.raise);
  if (shift.kind === "em") return scaled(shift.raise, style.size);
  return "baseline";
}

function offsets(style: ComputedStyle): { top: Range | "auto"; bottom: Range | "auto" } {
  if (!style.declaredPosition) return { top: "auto", bottom: "auto" };
  const shift = style.shift;
  // T reads `top: x` as a raise of −x and `bottom: x` as a raise of x (style.ts); Chrome resolves
  // the side not declared to the negation of the one declared.
  const raise =
    shift.kind === "points"
      ? exact(shift.raise)
      : shift.kind === "em"
        ? scaled(shift.raise, style.size)
        : exact(0);
  return { top: { lo: -raise.hi, hi: -raise.lo }, bottom: raise };
}

function modelStyle(context: Context, element: ElementNode): ModelStyle {
  const style = styleOf(context, element);
  const own = textColours(element, style);
  const table = nearestTable(element);
  const tableStyle = table === undefined ? undefined : styleOf(context, table);
  const spacing = table === undefined ? undefined : attribute(table, "cellspacing");
  const lineHeight = style.lineHeight;
  return {
    fontSize: { lo: style.size.lo, hi: style.size.hi },
    fontWeight: style.fontWeight,
    fontStyle: style.fontStyle,
    colours: own,
    lineHeight:
      lineHeight.kind === "normal"
        ? "normal"
        : lineHeight.kind === "number"
          ? scaled(lineHeight.value, style.size)
          : { lo: lineHeight.lo, hi: lineHeight.hi },
    underline: style.underline,
    backgrounds: style.backgrounds.map((colour) => [...colour] as unknown as Rgb),
    textIndent: exact(style.textIndent),
    margin: {
      top: verticalMargin(element, style, "top"),
      right: horizontalMargin(element, style, "right"),
      bottom: verticalMargin(element, style, "bottom"),
      left: horizontalMargin(element, style, "left"),
    },
    padding: {
      top: exact(style.padding.top),
      right: exact(style.padding.right),
      bottom: exact(style.padding.bottom),
      left: exact(style.padding.left),
    },
    borders: borders(context, element, style, own),
    borderCollapse: tableStyle?.borderCollapse ?? "separate",
    borderSpacing:
      table === undefined ? 0 : spacing === undefined ? 1.5 : Number(spacing.trim()) * 0.75,
    display: DISPLAY[element.name] ?? (BLOCK_DISPLAY.has(element.name) ? "block" : "inline"),
    position: style.declaredPosition ? "relative" : "static",
    ...offsets(style),
    verticalAlign: verticalAlign(context, element, style),
  };
}

function bullet(li: ElementNode): string {
  let depth = 0;
  for (let node = li.parent?.parent; node !== undefined; node = node.parent) {
    if (node.name === "ol" || node.name === "ul") depth += 1;
  }
  return depth === 0 ? "• " : depth === 1 ? "◦ " : "■ ";
}

// The model of an analysed section (T accepted it).
export function buildModel(analysis: Analysis): Model {
  const index = treeIndex(analysis.root);
  const keys = new Map(index.elements.map((element, key) => [element, key]));
  const key = (element: ElementNode): number => {
    const found = keys.get(element);
    if (found === undefined) throw new Error(`<${element.name}> has no key`);
    return found;
  };
  const context: Context = { analysis, styles: new Map() };
  const elements = index.elements.map((element) => {
    const style = modelStyle(context, element);
    context.styles.set(element, style);
    const parent = element.parent === undefined ? -1 : key(element.parent);
    return { key: key(element), name: element.name, parent, style };
  });

  const text: Model["text"] = [];
  const starts = new Map<TextNode, { start: number; pointToDom: number[] }>();
  let offset = 0;
  index.texts.forEach((dom, textKey) => {
    const entry: Model["text"][number] = {
      key: textKey,
      element: key(dom.parent),
      start: offset,
      end: offset + dom.data.length,
    };
    if (dom.interElement) entry.interElement = true;
    text.push(entry);
    if (dom.node !== undefined) starts.set(dom.node, { start: offset, pointToDom: dom.pointToDom });
    offset += dom.data.length;
  });

  const markers = index.elements
    .filter((element) => element.name === "li")
    .map((li) => {
      const style = context.styles.get(li);
      if (style === undefined) throw new Error("an li has no style");
      return {
        element: key(li),
        text: li.parent?.name === "ol" ? markerFor(li) : bullet(li),
        style: {
          fontSize: style.fontSize,
          fontWeight: style.fontWeight,
          fontStyle: style.fontStyle,
          colours: style.colours,
          lineHeight: style.lineHeight,
          backgrounds: style.backgrounds,
          underline: false,
        },
      };
    });

  const folds = [...analysis.shifts]
    .map(([element, decision]) => ({ element: key(element), decision }))
    .sort((first, second) => first.element - second.element);

  const waivers = analysis.waived
    .map((walkIndex) => {
      const source = analysis.walk.sources[walkIndex];
      const placed = source === undefined ? undefined : starts.get(source[0]);
      const within = source === undefined ? undefined : placed?.pointToDom[source[1]];
      if (placed === undefined || within === undefined) {
        throw new Error("a waived sign is not in a text node");
      }
      const start = placed.start + within;
      return { start, end: start + 1 };
    })
    .sort((first, second) => first.start - second.start);

  return { format: MODEL_FORMAT, elements, text, markers, folds, waivers };
}

// T's model output for one section's div, as canonical JSON (RFC 8785), or T's refusal thrown.
export function modelSection(div: string, evidence?: ReadonlySet<string>): string {
  return canonicalJson(buildModel(analyseSection(div, evidence, { model: true })));
}

// The model of each section of a document, in the order given (pre-order): T's two passes, the
// very routine that makes T(div) (T5), so a section accepted only with the plus-sign waiver is
// modelled with the evidence that accepted it. A section T refuses, whose T(div) the scanner
// refuses (the addendum's M2: T's "one tree" holds only with the scanner's nesting rules), or with
// no div, has none.
export function modelDocument(divs: readonly (string | undefined)[]): (string | undefined)[] {
  const scanned = (analysis: Analysis): string | undefined => {
    try {
      xhtmlToText(analysis.output);
    } catch (error) {
      if (error instanceof XhtmlError) return undefined;
      throw error;
    }
    return canonicalJson(buildModel(analysis));
  };
  return analyseDocument(divs, scanned, { model: true }).map((outcome) =>
    outcome !== undefined && "accepted" in outcome ? outcome.accepted : undefined,
  );
}
