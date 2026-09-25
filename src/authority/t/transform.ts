import {
  LIST_START_VALUE,
  LIST_TYPE_VALUE,
  listMarker,
  SPAN_VALUE,
  TABLE_SLOT_LIMIT,
} from "../../fidelity/xhtml.js";
import { isDefaultIgnorable, isGap } from "../../fidelity/normalize.js";
import { isUnderlineLetter, underlineChanges } from "../underline.js";
import { contrast, CssRefusal, type Rgb } from "./css.js";
import {
  BLOCKS,
  computeStyle,
  type Border,
  type ComputedStyle,
  LINK_COLOURS,
  ROOT_BACKGROUND,
  rootStyle,
  type Size,
  TABLE_PARTS,
} from "./style.js";
import {
  descendants,
  type ElementNode,
  MarkupRefusal,
  readTree,
  type TextNode,
  type TreeNode,
} from "./tree.js";

// T, the authority import's lexical transform (docs/design/authority-import-t.md). It reads a
// section's div into a tree over the scanner's own tokens, judges it by T1–T5's closed lists and
// model, and writes T(div) by editing the div string: attributes deleted, `span`, `u` and `a`
// unwrapped, a folded `span` renamed `sup` or `sub`. Text and character references are never
// touched. The scanner reads T(div) afterwards (src/authority/import.ts).

export class TRefusal extends Error {
  public constructor(public readonly reason: string) {
    super(reason);
    this.name = "TRefusal";
  }
}

function refuse(reason: string): never {
  throw new TRefusal(reason);
}

// The elements T accepts: the scanner's, but `code` (T2).
const ELEMENTS = new Set([
  ...BLOCKS,
  "span",
  "b",
  "i",
  "u",
  "em",
  "strong",
  "sup",
  "sub",
  "small",
  "a",
  "abbr",
  "cite",
  "img",
]);
const UNWRAPPED = new Set(["span", "u", "a"]);
const HEADINGS = new Set(["h1", "h2", "h3", "h4", "h5", "h6"]);
const ROW_LEVEL = new Set(["tr", "thead", "tbody", "tfoot"]);
const CELLS = new Set(["td", "th", "caption"]);
const EU_LANGUAGES = new Set([
  "bg",
  "cs",
  "da",
  "de",
  "el",
  "en",
  "es",
  "et",
  "fi",
  "fr",
  "ga",
  "hr",
  "hu",
  "it",
  "lt",
  "lv",
  "mt",
  "nl",
  "pl",
  "pt",
  "ro",
  "sk",
  "sl",
  "sv",
]);
// Right-to-left and Arabic-number code points, by block (a superset of bidi classes R, AL, AN).
const RIGHT_TO_LEFT =
  /[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff\u{10800}-\u{10fff}\u{1e800}-\u{1efff}]/u;
const DASHES_UNDER_UNDERLINE = new Set(["-", "\u2010", "\u2011"]);
const MARK = /^\p{M}$/u;
const WHITE: Rgb = ROOT_BACKGROUND;
const SPACE_OR_NBSP = new Set([" ", "\u00a0"]);
const BOUNDARY: Point = { element: undefined, underlined: false, inScript: false, boundary: true };
const LINE_BREAK: Point = { ...BOUNDARY, lineBreak: true };

type Info = {
  element: ElementNode;
  style: ComputedStyle;
  blockSize: Size;
  // T3b: offsets from the frame's origin, and whether the frame is a table cell.
  s: number;
  sr: number;
  inCell: boolean;
  inTable: boolean;
  inList: boolean;
  inScript: boolean;
  // Shifted ancestors, nearest first (T4).
  shiftedAncestors: ElementNode[];
  positionedAncestor: boolean;
};

// What T4 and T5 need to know of a code point of the section as the scanner emits it: one record,
// shared by every code point of a text node, of a marker or of a boundary, so T's memory is a
// reference per code point (the fourth code review: a record each took 300 bytes per byte).
type Point = {
  element: ElementNode | undefined;
  underlined: boolean;
  inScript: boolean;
  // A block, `br` or cell boundary, or a list marker: not text of any element.
  boundary: boolean;
  // The boundary a `br` draws.
  lineBreak?: boolean;
};

export type SectionResult = {
  div: string;
  // Tokens such as `Ph+` the section writes with a plain `+` outside every underline (T5).
  plainTokens: ReadonlySet<string>;
};

function attribute(element: ElementNode, name: string): string | undefined {
  return element.attributes.find((candidate) => candidate.name === name)?.value;
}

function isBlock(name: string): boolean {
  return BLOCKS.has(name);
}

// --- the tag-tied checks ---------------------------------------------------------------------

function checkAttributes(element: ElementNode, isRoot: boolean, rightToLeft: boolean): void {
  const { name } = element;
  for (const { name: attributeName, value } of element.attributes) {
    const lower = value.trim().toLowerCase();
    switch (attributeName) {
      case "xmlns":
        if (!isRoot) refuse("attribute");
        break;
      case "lang":
      case "xml:lang":
        if (!isRoot && !EU_LANGUAGES.has((lower.split("-")[0] ?? "").toLowerCase())) {
          refuse("attribute");
        }
        break;
      case "style":
      case "class":
      case "id":
        break;
      case "name":
        if (name !== "a") refuse("attribute");
        break;
      case "title":
        if (name === "abbr") refuse("attribute");
        break;
      case "dir":
        if (lower !== "ltr" || rightToLeft) refuse("attribute");
        break;
      case "valign":
        if (!(CELLS.has(name) || ROW_LEVEL.has(name)) || name === "caption") refuse("attribute");
        if (!["top", "middle", "bottom", "baseline"].includes(lower)) refuse("attribute");
        break;
      case "width":
      case "height":
      case "nowrap":
        if (!TABLE_PARTS.has(name) || name === "caption") refuse("attribute");
        break;
      case "align":
        if (name === "table") {
          if (lower !== "center") refuse("attribute");
        } else if (
          !(
            name === "td" ||
            name === "th" ||
            name === "tr" ||
            name === "p" ||
            name === "div" ||
            HEADINGS.has(name)
          ) ||
          !["left", "right", "center", "justify"].includes(lower)
        ) {
          refuse("attribute");
        }
        break;
      case "border":
      case "cellspacing":
      case "cellpadding":
      case "summary":
        if (name !== "table") refuse("attribute");
        if (attributeName !== "summary" && !/^\d+$/u.test(value.trim())) refuse("attribute");
        break;
      case "href":
        if (name !== "a") refuse("attribute");
        break;
      case "src":
        if (name !== "img") refuse("attribute");
        break;
      case "colspan":
      case "rowspan":
        if ((name !== "td" && name !== "th") || !SPAN_VALUE.test(value)) refuse("attribute");
        break;
      case "scope":
        if (name !== "th") refuse("attribute");
        break;
      case "type":
        if (name !== "ol" || !LIST_TYPE_VALUE.test(value)) refuse("attribute");
        break;
      case "start":
        if (name !== "ol" || !LIST_START_VALUE.test(value)) refuse("attribute");
        break;
      default:
        refuse("attribute");
    }
  }
}

// The attributes T(div) keeps (T2); every other is deleted.
function kept(element: ElementNode, isRoot: boolean): boolean[] {
  return element.attributes.map(({ name }) => {
    if (isRoot) return name === "xmlns" || name === "lang" || name === "xml:lang";
    if (name === "colspan" || name === "rowspan")
      return element.name === "td" || element.name === "th";
    if (name === "scope") return element.name === "th";
    if (name === "type" || name === "start") return element.name === "ol";
    return name === "src" && element.name === "img";
  });
}

function lineHeightAtLeast(style: ComputedStyle, size: Size, times: number): boolean {
  const lineHeight = style.lineHeight;
  // `normal` passes both bounds T judges (T3c's 1 em; an inline background's, T3's row).
  if (lineHeight.kind === "normal") return true;
  if (lineHeight.kind === "number") return lineHeight.value >= times;
  if (lineHeight.of === size && lineHeight.factor !== undefined) return lineHeight.factor >= times;
  return lineHeight.lo >= times * size.hi;
}

function colours(style: ComputedStyle): Rgb[] {
  if (style.underLink && !style.colourSinceLink) return [...LINK_COLOURS];
  return [style.colour ?? [0, 0, 0]];
}

// T3a, T3c and the font-size rules at a text node or a list marker drawn in `style`.
function checkDrawn(style: ComputedStyle, blockSize: Size): void {
  for (const text of colours(style)) {
    for (const background of [...style.backgrounds, WHITE]) {
      if (contrast(text, background) < 4.5) refuse("contrast");
    }
  }
  if (!lineHeightAtLeast(style, style.size, 1)) refuse("line-height");
  if (style.size.lo < 5 || style.size.hi > 24) refuse("font-size");
  if (style.size !== blockSize && style.size.lo < 0.5 * blockSize.hi) refuse("font-size");
}

function hasDrawnText(node: TextNode): boolean {
  return node.points.some((point) => !/^[\t\n\r\f ]$/u.test(point));
}

// --- the section -----------------------------------------------------------------------------

type Walk = {
  infos: Map<ElementNode, Info>;
  // The code points, and what is known of each, index by index.
  codes: string[];
  points: Point[];
  // The index of each element's first and one-past-last point, for T4 and T5.
  ranges: Map<ElementNode, { start: number; end: number }>;
};

function walkSection(root: ElementNode, rightToLeft: boolean): Walk {
  const infos = new Map<ElementNode, Info>();
  const codes: string[] = [];
  const points: Point[] = [];
  const push = (code: string, point: Point): void => {
    codes.push(code);
    points.push(point);
  };
  const ranges = new Map<ElementNode, { start: number; end: number }>();

  const visit = (element: ElementNode, parent: Info | undefined): void => {
    const isRoot = parent === undefined;
    const { name } = element;
    if (isRoot ? name !== "div" : !ELEMENTS.has(name)) refuse(isRoot ? "markup" : "element");
    checkAttributes(element, isRoot, rightToLeft);
    let cellPadding: number | undefined;
    if ((name === "td" || name === "th") && parent !== undefined) {
      let ancestor: ElementNode | undefined = element.parent;
      while (ancestor !== undefined && ancestor.name !== "table") ancestor = ancestor.parent;
      const padding = ancestor === undefined ? undefined : attribute(ancestor, "cellpadding");
      if (padding !== undefined) cellPadding = Number(padding) * 0.75;
    }
    let style: ComputedStyle;
    try {
      style = computeStyle({
        name,
        parent: parent?.style ?? rootStyle(),
        isLink: name === "a" && attribute(element, "href") !== undefined,
        cellPadding,
        style: attribute(element, "style"),
      });
    } catch (error) {
      if (error instanceof CssRefusal) refuse(error.reason);
      throw error;
    }
    const block = isBlock(name);
    // Every element's size is bounded, not only where text stands: an element with no text of its
    // own sets its children's raise and T4's ceiling (T3's font-size row).
    const blockSize = block ? style.size : (parent?.blockSize ?? style.size);
    if (style.size.lo < 5 || style.size.hi > 24) refuse("font-size");
    if (!block && style.size !== blockSize && style.size.lo < 0.5 * blockSize.hi)
      refuse("font-size");
    // Inline elements take no offsets (T3).
    if (!block) {
      for (const side of ["top", "right", "bottom", "left"] as const) {
        if (style.margin[side] !== 0 || style.padding[side] !== 0) refuse("offset");
      }
      if (
        style.declared.has("text-indent") &&
        style.textIndent !== (parent?.style.textIndent ?? 0)
      ) {
        refuse("offset");
      }
    }
    // T3b's bounds on the element's own values; a caption's box is not pulled out of its table.
    for (const side of ["top", "right", "bottom", "left"] as const) {
      if (Math.abs(style.margin[side]) > 144 || style.padding[side] > 144) refuse("offset");
      if (name === "caption" && style.margin[side] < 0) refuse("offset");
      if (style.padding[side] < 0) refuse(name === "li" ? "list" : "offset");
    }
    if (style.margin.top < 0 || style.margin.bottom < 0) refuse(name === "li" ? "list" : "offset");
    if (Math.abs(style.textIndent) > 144) refuse("offset");
    if (isRoot && (style.margin.left < 0 || style.margin.right < 0)) refuse("offset");
    // A cell's or caption's own lines start at its content box: no indent there (T3b), and the
    // root's lines start at the section's.
    if (CELLS.has(name) && style.textIndent !== 0) refuse("offset");
    if (isRoot && (style.textIndent < 0 || style.textIndent > 144)) refuse("offset");
    // A raised or lowered element is no larger than its parent's text (T4).
    if (
      (name === "sup" || name === "sub") &&
      parent !== undefined &&
      style.size.hi > parent.style.size.lo
    ) {
      refuse("baseline-shift");
    }
    // Row-level table parts take no box offsets (a browser ignores them): refuse any.
    if (ROW_LEVEL.has(name)) {
      for (const side of ["top", "right", "bottom", "left"] as const) {
        if (style.margin[side] !== 0 || style.padding[side] !== 0) refuse("offset");
      }
    }
    // T3d: a list keeps its marker padding.
    if (
      (name === "ol" || name === "ul") &&
      (style.declared.has("padding-left") || style.margin.left < 0)
    ) {
      refuse("list");
    }

    const entersCell = CELLS.has(name);
    const inCell = entersCell || (parent?.inCell ?? false);
    const inTable = name === "table" || (parent?.inTable ?? false);
    const inList = name === "ol" || name === "ul" || name === "li" || (parent?.inList ?? false);
    const counts =
      block && !entersCell && !ROW_LEVEL.has(name) && !isRoot && name !== "br" && name !== "hr";
    const baseS = entersCell || parent === undefined ? 0 : parent.s;
    const baseSR = entersCell || parent === undefined ? 0 : parent.sr;
    const s = counts ? baseS + style.margin.left + style.padding.left : baseS;
    const sr = counts ? baseSR + style.margin.right + style.padding.right : baseSR;
    if (counts) {
      const indent = style.textIndent;
      if (s > 144 || s + indent > 144 || sr > 144) refuse("offset");
      const insideLi = hasLiAncestor(element);
      if (name === "li") {
        if (style.margin.left < 0 || style.margin.right < 0 || indent !== 0) refuse("list");
      } else if (insideLi && (style.margin.left < 0 || style.margin.right < 0 || indent < 0)) {
        refuse("list");
      } else if (inCell) {
        if (s < 0 || s + indent < 0 || sr < 0 || indent > 0) refuse("offset");
      } else if (s < 0 || s + indent < 0 || sr < -1.2) {
        refuse("offset");
      }
    }
    const info: Info = {
      element,
      style,
      blockSize: block ? style.size : (parent?.blockSize ?? style.size),
      s,
      sr,
      inCell,
      inTable,
      inList,
      inScript: name === "sup" || name === "sub" || (parent?.inScript ?? false),
      shiftedAncestors:
        parent === undefined
          ? []
          : [...(isShifted(parent) ? [parent.element] : []), ...parent.shiftedAncestors],
      positionedAncestor:
        (parent?.style.declaredPosition ?? false) || (parent?.positionedAncestor ?? false),
    };
    infos.set(element, info);

    // A list item's marker is drawn text, judged at its tag (T3d, T6).
    if (name === "li") checkDrawn(style, info.blockSize);
    const start = points.length;
    if (block) push("\n", name === "br" ? LINE_BREAK : BOUNDARY);
    if (name === "li" && parent?.element.name === "ol") {
      for (const code of markerFor(element)) push(code, BOUNDARY);
    }
    const own: Point = {
      element,
      underlined: style.underline,
      inScript: info.inScript,
      boundary: false,
    };
    // A picture is drawn and emitted (U+FFFC), so it is no letter and no gap to T5.
    if (name === "img") push("\ufffc", own);
    for (const child of element.children) {
      if (child.kind === "text") {
        if (hasDrawnText(child)) checkDrawn(style, info.blockSize);
        for (const code of child.points) push(code, own);
      } else {
        visit(child, info);
      }
    }
    if (block) push("\n", BOUNDARY);
    ranges.set(element, { start, end: points.length });
    // A background, judged at the element's end tag with its descendants' sizes known (T3): never
    // on or inside a positioned element; on an inline element, a line height of at least 1.2 times
    // the largest of its own and its descendants' sizes.
    if (style.background !== undefined) {
      if (style.declaredPosition || info.positionedAncestor) refuse("css-value");
      if (!block) {
        let largest = style.size;
        for (const node of descendants(element)) {
          if (node.kind !== "element") continue;
          const size = infos.get(node)?.style.size;
          if (size !== undefined && size.hi > largest.hi) largest = size;
        }
        if (!lineHeightAtLeast(style, largest, 1.2)) refuse("line-height");
      }
    }
  };
  visit(root, undefined);
  return { infos, codes, points, ranges };
}

function hasLiAncestor(element: ElementNode): boolean {
  for (let node = element.parent; node !== undefined; node = node.parent) {
    if (node.name === "li") return true;
    if (CELLS.has(node.name)) return false;
  }
  return false;
}

function isShifted(info: Info): boolean {
  return (
    info.style.shift.kind !== "none" && info.element.name !== "sup" && info.element.name !== "sub"
  );
}

// The markers each `ol` draws, as the scanner writes them, computed once per list.
const MARKERS = new WeakMap<ElementNode, Map<ElementNode, string>>();

function markersOf(list: ElementNode): Map<ElementNode, string> {
  const cached = MARKERS.get(list);
  if (cached !== undefined) return cached;
  const items = list.children.filter(
    (child): child is ElementNode => child.kind === "element" && child.name === "li",
  );
  const start = Number(attribute(list, "start") ?? "1");
  const type = attribute(list, "type") ?? "1";
  const markers = new Map(items.map((li, index) => [li, listMarker(type, start + index)]));
  MARKERS.set(list, markers);
  return markers;
}

// The marker an `li` of an `ol` draws.
function markerFor(li: ElementNode): string {
  const list = li.parent;
  return list === undefined ? "" : (markersOf(list).get(li) ?? "");
}

// --- T4 ----------------------------------------------------------------------------------------

type Interval = { lo: number; hi: number };

// The magnitude of an element's own shift, signed by direction.
function shiftOf(info: Info, parentSize: Size): { raise: Interval; em?: number } {
  const shift = info.style.shift;
  switch (shift.kind) {
    case "points":
      return { raise: { lo: shift.raise, hi: shift.raise } };
    case "em":
      return {
        raise: { lo: shift.raise * info.style.size.lo, hi: shift.raise * info.style.size.hi },
        em: shift.raise,
      };
    case "super":
      return { raise: { lo: parentSize.lo / 3 + 0.75, hi: parentSize.hi / 3 + 0.75 } };
    case "sub":
      return { raise: { lo: -(parentSize.hi / 5 + 0.75), hi: -(parentSize.lo / 5 + 0.75) } };
    default:
      return { raise: { lo: 0, hi: 0 } };
  }
}

function magnitudeHi(raise: Interval): number {
  return Math.max(Math.abs(raise.lo), Math.abs(raise.hi));
}

function magnitudeLo(raise: Interval): number {
  if (raise.lo <= 0 && raise.hi >= 0) return 0;
  return Math.min(Math.abs(raise.lo), Math.abs(raise.hi));
}

function textNodesUnder(element: ElementNode): { node: TextNode; parent: ElementNode }[] {
  const found: { node: TextNode; parent: ElementNode }[] = [];
  const visit = (node: TreeNode, parent: ElementNode): void => {
    if (node.kind === "text") found.push({ node, parent });
    else for (const child of node.children) visit(child, node);
  };
  for (const child of element.children) visit(child, element);
  return found;
}

function containsImage(element: ElementNode): boolean {
  return element.children.some(
    (child) => child.kind === "element" && (child.name === "img" || containsImage(child)),
  );
}

type ShiftDecision = "delete" | "sup" | "sub";

function decideShifts(walk: Walk): Map<ElementNode, ShiftDecision> {
  const decisions = new Map<ElementNode, ShiftDecision>();
  const shifted = [...walk.infos.values()].filter(isShifted);
  // First: which shifts are deleted (every text node under them moves by under 0.1 of its size).
  const deletable = (info: Info): boolean => {
    const texts = textNodesUnder(info.element);
    if (texts.length === 0 || containsImage(info.element)) return false;
    for (const { parent } of texts) {
      const textInfo = walk.infos.get(parent);
      if (textInfo === undefined) return false;
      const size = textInfo.style.size;
      const chain = [
        ...(isShifted(textInfo) ? [textInfo] : []),
        ...textInfo.shiftedAncestors
          .map((element) => walk.infos.get(element))
          .filter((x): x is Info => x !== undefined),
      ];
      // Exact where every shift in the chain is `em` of this very size (T1).
      if (chain.every((link) => link.style.shift.kind === "em" && link.style.size === size)) {
        const sum = chain.reduce(
          (total, link) => total + (link.style.shift.kind === "em" ? link.style.shift.raise : 0),
          0,
        );
        if (Math.abs(sum) >= 0.1) return false;
        continue;
      }
      let lo = 0;
      let hi = 0;
      for (const link of chain) {
        const parentSize =
          walk.infos.get(link.element.parent ?? link.element)?.style.size ?? link.style.size;
        const { raise } = shiftOf(link, parentSize);
        lo += raise.lo;
        hi += raise.hi;
      }
      if (magnitudeHi({ lo, hi }) >= 0.1 * size.lo) return false;
    }
    return true;
  };
  for (const info of shifted) {
    if (deletable(info)) decisions.set(info.element, "delete");
  }
  for (const info of shifted) {
    if (decisions.get(info.element) === "delete") continue;
    const { element } = info;
    const parent = element.parent === undefined ? undefined : walk.infos.get(element.parent);
    if (parent === undefined || element.name !== "span" || info.inScript) refuse("baseline-shift");
    // No ancestor and no descendant carries a shift.
    if (info.shiftedAncestors.length > 0) refuse("baseline-shift");
    const texts = textNodesUnder(element);
    if (texts.length === 0 || containsImage(element)) refuse("baseline-shift");
    for (const node of descendants(element)) {
      if (node.kind !== "element") continue;
      const nodeInfo = walk.infos.get(node);
      if (nodeInfo === undefined || isShifted(nodeInfo) || !UNWRAPPED.has(node.name))
        refuse("baseline-shift");
    }
    const { raise, em } = shiftOf(info, parent.style.size);
    const kind = info.style.shift.kind;
    for (const { parent: textParent } of texts) {
      const size = walk.infos.get(textParent)?.style.size;
      if (size === undefined) refuse("baseline-shift");
      if (size !== parent.style.size && size.hi > parent.style.size.lo) refuse("baseline-shift");
      if (kind !== "super" && kind !== "sub") {
        const floor =
          em !== undefined && size === info.style.size
            ? Math.abs(em) >= 0.2
            : magnitudeLo(raise) >= 0.2 * size.hi;
        if (!floor) refuse("baseline-shift");
      }
    }
    if (magnitudeHi(raise) > 0.5 * parent.style.size.lo) refuse("baseline-shift");
    const range = walk.ranges.get(element);
    if (range === undefined) refuse("baseline-shift");
    const own = walk.codes.slice(range.start, range.end);
    const drawn = own.filter((point) => !SPACE_OR_NBSP.has(point));
    if (drawn.length < 1 || drawn.length > 4) refuse("baseline-shift");
    if (!hasUnshiftedNeighbour(walk, range)) refuse("baseline-shift");
    decisions.set(
      element,
      raise.hi > 0 && raise.lo >= 0 ? "sup" : raise.hi <= 0 ? "sub" : refuse("baseline-shift"),
    );
  }
  return decisions;
}

// T4's neighbour: the code point before the run's first non-whitespace code point, or after its
// last, is unshifted text, or one U+0020 or U+00A0 away from it, in the same block.
function hasUnshiftedNeighbour(walk: Walk, range: { start: number; end: number }): boolean {
  const { codes, points } = walk;
  const isWhite = (index: number): boolean => SPACE_OR_NBSP.has(codes[index] ?? "");
  let first = range.start;
  while (first < range.end && isWhite(first)) first += 1;
  let last = range.end - 1;
  while (last >= range.start && isWhite(last)) last -= 1;
  const unshifted = (index: number): boolean => {
    const point = points[index];
    const code = codes[index] ?? "";
    if (point === undefined || point.boundary || point.element === undefined) return false;
    // A neighbour draws ink: a letter, number, punctuation or symbol (no space, control, format,
    // private-use, unassigned or mark code point), no picture, and none the fidelity layer knows
    // is drawn blank (a thin space, U+2800 BRAILLE PATTERN BLANK, a default-ignorable).
    if (
      !/^[\p{L}\p{N}\p{P}\p{S}]$/u.test(code) ||
      code === "\ufffc" ||
      isGap(code.codePointAt(0) ?? 0)
    ) {
      return false;
    }
    // It sits on the baseline: under no shift, deleted or not, and in no `sup` or `sub`, so the
    // run's offset from it is the run's own.
    const info = walk.infos.get(point.element);
    return (
      info !== undefined &&
      !point.inScript &&
      !isShifted(info) &&
      info.shiftedAncestors.length === 0
    );
  };
  const look = (from: number, step: number): boolean => {
    let index = from;
    const point = points[index];
    if (point !== undefined && !point.boundary && SPACE_OR_NBSP.has(codes[index] ?? "")) {
      index += step;
    }
    return unshifted(index);
  };
  return look(first - 1, -1) || look(last + 1, 1);
}

// --- T3d, T3e ----------------------------------------------------------------------------------

function markerWidth(marker: string): number {
  let width = 1.25; // ". "
  for (const point of marker.replace(/\. $/u, "")) width += /[0-9-]/u.test(point) ? 0.65 : 1;
  return width;
}

const WIDEST = new WeakMap<ElementNode, number>();

function widestMarker(list: ElementNode): number {
  const cached = WIDEST.get(list);
  if (cached !== undefined) return cached;
  let widest = 0;
  for (const marker of markersOf(list).values()) widest = Math.max(widest, markerWidth(marker));
  WIDEST.set(list, widest);
  return widest;
}

function checkLists(walk: Walk): void {
  for (const info of walk.infos.values()) {
    if (info.element.name !== "li") continue;
    const list = info.element.parent;
    const width = list?.name === "ol" ? widestMarker(list) : 1;
    // A browser hangs the marker off the item's border box, not its content box.
    if (info.s - info.style.padding.left - width * info.style.size.hi < 0) refuse("list");
  }
}

type Placed = { cell: ElementNode; info: Info };

const DRAWN_STYLES = ["double", "solid", "dashed", "dotted"];

function isDrawn(border: Border | undefined, backgrounds: readonly Rgb[]): boolean {
  if (border === undefined || !DRAWN_STYLES.includes(border.style) || border.widthPt <= 0) {
    return false;
  }
  const colours = border.colours ?? [[0, 0, 0] as const];
  return colours.every((colour) =>
    backgrounds.every((background) => contrast(colour, background) >= 3),
  );
}

// Whether the edge between two cells side by side is drawn (T3e). Under `separate` each cell draws
// its own border; under `collapse` only the winner of CSS 2.1's conflict resolution is drawn, and
// which one wins turns on widths snapped to device pixels (a 0.5 pt and a 1.4 pt border both draw
// one pixel wide at 96 dpi, and the left cell's then wins): so the edge counts as drawn only if
// every facing border other than `none` is.
function drawnEdge(left: Info, right: Info, collapse: boolean): boolean {
  const backgrounds = [...left.style.backgrounds, ...right.style.backgrounds, WHITE];
  const leftBorder = left.style.borders.right;
  const rightBorder = right.style.borders.left;
  if (!collapse) return isDrawn(leftBorder, backgrounds) || isDrawn(rightBorder, backgrounds);

  const facing = [leftBorder, rightBorder].filter(
    (border): border is Border => border !== undefined && border.style !== "none",
  );
  return facing.length > 0 && facing.every((border) => isDrawn(border, backgrounds));
}

const LARGEST = new WeakMap<ElementNode, number>();

function largestSize(walk: Walk, cell: ElementNode): number {
  const cached = LARGEST.get(cell);
  if (cached !== undefined) return cached;
  let largest = walk.infos.get(cell)?.style.size.hi ?? 0;
  for (const node of descendants(cell)) {
    if (node.kind === "element") {
      const info = walk.infos.get(node);
      if (
        info !== undefined &&
        (node.name === "li" ||
          node.children.some((child) => child.kind === "text" && hasDrawnText(child)))
      ) {
        largest = Math.max(largest, info.style.size.hi);
      }
    }
  }
  LARGEST.set(cell, largest);
  return largest;
}

function checkTables(walk: Walk): void {
  // The slots every table's cells cover, bounded across the section as the scanner bounds them,
  // before any grid is laid.
  let slots = 0;
  for (const node of walk.infos.keys()) {
    if (node.name === "td" || node.name === "th") {
      slots +=
        Number(attribute(node, "colspan") ?? "1") * Number(attribute(node, "rowspan") ?? "1");
    }
  }
  if (slots > TABLE_SLOT_LIMIT) refuse("attribute");
  for (const info of walk.infos.values()) {
    if (info.element.name !== "table") continue;
    const table = info.element;
    const collapse = info.style.borderCollapse === "collapse";
    const spacingAttribute = attribute(table, "cellspacing");
    const spacing = collapse
      ? 0
      : spacingAttribute === undefined
        ? 1.5
        : Number(spacingAttribute) * 0.75;

    const rows: ElementNode[] = [];
    for (const child of table.children) {
      if (child.kind !== "element") continue;
      if (child.name === "tr") rows.push(child);
      else if (ROW_LEVEL.has(child.name)) {
        for (const row of child.children)
          if (row.kind === "element" && row.name === "tr") rows.push(row);
      }
    }
    // The grid, by the HTML table model: each cell covers colspan × rowspan slots.
    const grid: (Placed | undefined)[][] = [];
    rows.forEach((row, rowIndex) => {
      const current = (grid[rowIndex] ??= []);
      let column = 0;
      for (const cell of row.children) {
        // A row holds only cells (T1's one tree).
        if (cell.kind !== "element") continue;
        const cellInfo = walk.infos.get(cell);
        if (cellInfo === undefined) continue;
        while (current[column] !== undefined) column += 1;
        const colspan = Number(attribute(cell, "colspan") ?? "1");
        const rowspan = Number(attribute(cell, "rowspan") ?? "1");
        for (let down = 0; down < rowspan; down += 1) {
          grid[rowIndex + down] ??= [];
          for (let across = 0; across < colspan; across += 1) {
            const target = grid[rowIndex + down];
            if (target !== undefined) target[column + across] = { cell, info: cellInfo };
          }
        }
        column += colspan;
      }
    });
    for (const row of grid) {
      for (let column = 1; column < row.length; column += 1) {
        const left = row[column - 1];
        const right = row[column];
        if (left === undefined || right === undefined || left.cell === right.cell) continue;
        if (drawnEdge(left.info, right.info, collapse)) continue;
        const gap = left.info.style.padding.right + right.info.style.padding.left + spacing;
        const larger = Math.max(largestSize(walk, left.cell), largestSize(walk, right.cell));
        if (gap < 0.25 * larger) refuse("table-edge");
      }
    }
  }
}

// --- T5 ----------------------------------------------------------------------------------------

type Run = { start: number; end: number };

function underlineRuns(points: readonly Point[]): Run[] {
  const runs: Run[] = [];
  let start = -1;
  points.forEach((point, index) => {
    const underlined = point.underlined && !point.boundary;
    if (underlined && start === -1) start = index;
    if (!underlined && start !== -1) {
      runs.push({ start, end: index });
      start = -1;
    }
  });
  if (start !== -1) runs.push({ start, end: points.length });
  return runs;
}

function blockOf(element: ElementNode | undefined): ElementNode | undefined {
  for (let node = element; node !== undefined; node = node.parent)
    if (isBlock(node.name)) return node;
  return undefined;
}

// A `+` T5 may waive: after at least two letters, before a space, a no-break space or the end of
// its block, with no mark or default-ignorable next to it. Returns its token (`Ph+`), if any.
function plusToken(
  codes: readonly string[],
  points: readonly Point[],
  index: number,
): string | undefined {
  if (codes[index] !== "+") return undefined;
  let start = index;
  while (start > 0 && isUnderlineLetter(codes[start - 1] ?? "") && !points[start - 1]?.boundary)
    start -= 1;
  if (index - start < 2) return undefined;
  // Nothing drawn joins the token: no letter, mark or default-ignorable before it, and after it
  // a space, a no-break space or its block's end (not a `br`).
  const before = points[start - 1];
  const previous = codes[start - 1] ?? "";
  if (
    before !== undefined &&
    !before.boundary &&
    (isUnderlineLetter(previous) ||
      MARK.test(previous) ||
      isDefaultIgnorable(previous.codePointAt(0) ?? 0))
  )
    return undefined;
  const after = points[index + 1];
  const next = codes[index + 1] ?? "";
  if (after?.lineBreak === true) return undefined;
  if (after !== undefined && !after.boundary && !SPACE_OR_NBSP.has(next)) return undefined;
  if (MARK.test(next) || isDefaultIgnorable(next.codePointAt(0) ?? 0)) return undefined;
  return codes.slice(start, index + 1).join("");
}

function plainTokens(codes: readonly string[], points: readonly Point[]): Set<string> {
  const tokens = new Set<string>();
  points.forEach((point, index) => {
    if (codes[index] !== "+" || point.underlined || point.inScript || point.boundary) return;
    const token = plusToken(codes, points, index);
    if (token === undefined) return;
    const letters = points.slice(index - (Array.from(token).length - 1), index);
    if (letters.some((letter) => letter.underlined || letter.inScript)) return;
    tokens.add(token);
  });
  return tokens;
}

function waivable(walk: Walk, run: Run, evidence: ReadonlySet<string>, text: string[]): boolean {
  const { points } = walk;
  const first = points[run.start];
  const block = blockOf(first?.element);
  if (block === undefined || !(block.name === "p" || HEADINGS.has(block.name))) return false;
  const blockInfo = walk.infos.get(block);
  if (blockInfo === undefined || blockInfo.inTable || blockInfo.inList) return false;
  if ([...descendants(block)].some((node) => node.kind === "element" && isBlock(node.name)))
    return false;
  const range = walk.ranges.get(block);
  if (range === undefined) return false;
  for (let index = range.start; index < range.end; index += 1) {
    const point = points[index];
    if (point === undefined || point.boundary || /^[\t\n\r\f \u00a0]$/u.test(text[index] ?? ""))
      continue;
    if (index < run.start || index >= run.end) return false;
  }
  // The waived signs are masked in place on the section's text, and restored.
  const masked: number[] = [];
  let accepted = true;
  for (let index = run.start; index < run.end; index += 1) {
    if (text[index] !== "+") continue;
    const token = plusToken(walk.codes, points, index);
    if (token === undefined || !evidence.has(token)) {
      accepted = false;
      break;
    }
    text[index] = " ";
    masked.push(index);
  }
  accepted =
    accepted &&
    masked.length > 0 &&
    !underlineChanges(text, run.start, run.end, { hyphensInWords: true });
  for (const index of masked) text[index] = "+";
  return accepted;
}

function checkUnderlines(walk: Walk, evidence: ReadonlySet<string> | undefined): void {
  const { codes, points } = walk;
  // A copy: the waiver masks signs on it in place.
  const text = [...codes];
  for (const run of underlineRuns(points)) {
    for (let index = run.start; index < run.end; index += 1) {
      const code = codes[index] ?? "";
      if (points[index]?.inScript === true) refuse("underline");
      if (/^\p{Pd}$/u.test(code) && !DASHES_UNDER_UNDERLINE.has(code)) refuse("underline");
    }
    if (!underlineChanges(text, run.start, run.end, { hyphensInWords: true })) continue;
    if (evidence === undefined || !waivable(walk, run, evidence, text)) refuse("underline");
  }
}

// --- the edits -------------------------------------------------------------------------------

function edit(
  div: string,
  root: ElementNode,
  shifts: ReadonlyMap<ElementNode, ShiftDecision>,
): string {
  const replacements: { start: number; end: number; text: string }[] = [];
  const visit = (element: ElementNode, isRoot: boolean): void => {
    const decision = shifts.get(element);
    const folded = decision === "sup" || decision === "sub" ? decision : undefined;
    if (folded !== undefined) {
      replacements.push({ ...element.startTag, text: `<${folded}>` });
      if (element.endTag !== undefined)
        replacements.push({ ...element.endTag, text: `</${folded}>` });
    } else if (UNWRAPPED.has(element.name)) {
      replacements.push({ ...element.startTag, text: "" });
      if (element.endTag !== undefined) replacements.push({ ...element.endTag, text: "" });
    } else {
      const keep = kept(element, isRoot);
      const selfClosing = element.endTag === undefined;
      const attributes = element.attributes
        .filter((_, index) => keep[index] === true)
        .map(({ span }) => ` ${div.slice(span.start, span.end)}`)
        .join("");
      const text = `<${element.name}${attributes}${selfClosing ? "/" : ""}>`;
      if (text !== div.slice(element.startTag.start, element.startTag.end)) {
        replacements.push({ ...element.startTag, text });
      }
    }
    for (const child of element.children) if (child.kind === "element") visit(child, false);
  };
  visit(root, true);
  replacements.sort((first, second) => first.start - second.start);
  let output = "";
  let cursor = 0;
  for (const { start, end, text } of replacements) {
    output += div.slice(cursor, start) + text;
    cursor = end;
  }
  return output + div.slice(cursor);
}

// The block tags of a div, in order (ADR 0005's line check: T never adds, removes or renames one).
function blockTags(root: ElementNode): string[] {
  const tags: string[] = [];
  const visit = (element: ElementNode): void => {
    if (isBlock(element.name)) tags.push(element.name);
    for (const child of element.children) if (child.kind === "element") visit(child);
  };
  visit(root);
  return tags;
}

// T for one section's div. `evidence` is given in the waiver's second pass (T5): the tokens the
// document writes with a plain `+` in the sections the first pass accepted.
export function transformSection(div: string, evidence?: ReadonlySet<string>): SectionResult {
  let root: ElementNode;
  try {
    root = readTree(div);
  } catch (error) {
    if (error instanceof MarkupRefusal) refuse(error.reason);
    throw error;
  }
  // Right-to-left text anywhere in the section, decoded (a reference to U+05D0 counts).
  const rightToLeft = [root, ...descendants(root)].some((node) =>
    node.kind === "text"
      ? RIGHT_TO_LEFT.test(node.points.join(""))
      : node.attributes.some(({ value }) => RIGHT_TO_LEFT.test(value)),
  );
  const walk = walkSection(root, rightToLeft);
  const shifts = decideShifts(walk);
  // A folded run is raised or lowered text from here on (T5's runs and the waiver's evidence).
  const scripted = new Map<Point, Point>();
  for (const [element, decision] of shifts) {
    if (decision === "delete") continue;
    const range = walk.ranges.get(element);
    if (range === undefined) continue;
    for (let index = range.start; index < range.end; index += 1) {
      const point = walk.points[index];
      if (point !== undefined && !point.inScript) {
        let raised = scripted.get(point);
        if (raised === undefined) {
          raised = { ...point, inScript: true };
          scripted.set(point, raised);
        }
        walk.points[index] = raised;
      }
    }
  }
  checkLists(walk);
  checkTables(walk);
  checkUnderlines(walk, evidence);
  const output = edit(div, root, shifts);
  const after = readTree(output);
  if (blockTags(after).join(" ") !== blockTags(root).join(" ")) refuse("markup");
  return { div: output, plainTokens: plainTokens(walk.codes, walk.points) };
}
