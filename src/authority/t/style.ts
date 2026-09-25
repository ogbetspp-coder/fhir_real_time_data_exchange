import {
  absolute,
  colour,
  CssRefusal,
  declarations,
  length,
  type Length,
  type Rgb,
} from "./css.js";

// T's model of the style a browser draws an element with (docs/design/authority-import-t.md, T1
// and T3): the browser's defaults for the elements T accepts, the element's inline declarations
// in order, and what inherits. Every value outside T3's lists is a refusal with a closed reason.

// A font size under `smaller` is known only within a range (T1): `lo` and `hi` in points. A size
// object passed down unchanged is the same computed size, so a comparison of it with itself is
// exact.
export type Size = { readonly lo: number; readonly hi: number };

export type LineHeight =
  | { kind: "normal" }
  | { kind: "number"; value: number }
  // A length; `of` and `factor` where it was computed from a size (`em`, `%`), so a comparison
  // with that same size is exact (T1: one computed size takes one factor).
  | { kind: "length"; lo: number; hi: number; of?: Size; factor?: number };

// A border and the colours it may be drawn in; `colours` undefined is the element's own final
// `color` (CSS `currentcolor`), resolved once every declaration is read: both link colours where
// an `a` with `href` sets it.
export type Border = { widthPt: number; style: string; colours: readonly Rgb[] | undefined };

export type Shift =
  | { kind: "none" }
  // A length in points, positive raises; `em` shifts are kept relative to the element's own size.
  | { kind: "points"; raise: number }
  | { kind: "em"; raise: number }
  | { kind: "super" }
  | { kind: "sub" };

export type ComputedStyle = {
  size: Size;
  lineHeight: LineHeight;
  // The author's colour, if one is declared on this element or inherited; undefined is black.
  colour: Rgb | undefined;
  // Whether an author colour is declared on the path from the nearest `a` with `href` down.
  colourSinceLink: boolean;
  underLink: boolean;
  // This element's own background, and every painted background in its ancestor chain.
  background: Rgb | undefined;
  backgrounds: readonly Rgb[];
  textIndent: number;
  underline: boolean;
  // The element's own box values in points (0 where not declared and no default).
  margin: { top: number; right: number; bottom: number; left: number };
  padding: { top: number; right: number; bottom: number; left: number };
  borders: { top?: Border; right?: Border; bottom?: Border; left?: Border };
  borderCollapse: "collapse" | "separate" | undefined;
  shift: Shift;
  // Which of the shift-bearing properties the element declared (T3: not both).
  declaredPosition: boolean;
  declaredVerticalAlign: string | undefined;
  hasWidthOrHeight: boolean;
  // Every property the element declares, shorthands expanded to their longhands.
  declared: ReadonlySet<string>;
};

export const ROOT_SIZE: Size = { lo: 12, hi: 12 };
const WHITE: Rgb = [255, 255, 255];
export const LINK_COLOURS: readonly Rgb[] = [
  [0x00, 0x00, 0xee],
  [0x55, 0x1a, 0x8b],
];

export const BLOCKS = new Set([
  "div",
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "ul",
  "ol",
  "li",
  "table",
  "thead",
  "tbody",
  "tfoot",
  "tr",
  "td",
  "th",
  "caption",
  "blockquote",
  "dl",
  "dt",
  "dd",
  "hr",
  "br",
]);
export const TABLE_PARTS = new Set([
  "table",
  "thead",
  "tbody",
  "tfoot",
  "tr",
  "td",
  "th",
  "caption",
]);
const HEADING_EM: Readonly<Record<string, number>> = {
  h1: 2,
  h2: 1.5,
  h3: 1.17,
  h4: 1,
  h5: 0.83,
  h6: 0.67,
};

// The font families T accepts (T3): named fonts by name, ASCII case-insensitively, and the two
// generics unquoted only.
const FAMILIES = new Set(["times new roman", "times", "arial", "helvetica", "calibri", "cambria"]);
const GENERICS = new Set(["serif", "sans-serif"]);

function families(value: string): void {
  for (const part of value.split(",")) {
    const family = part.trim();
    const quote = family[0];
    if (quote === "'" || quote === '"') {
      if (family.length < 2 || family.at(-1) !== quote) throw new CssRefusal("css-value");
      const name = family.slice(1, -1);
      if (/\t| {2}/u.test(name) || !FAMILIES.has(name.toLowerCase())) throw new CssRefusal("font");
      continue;
    }
    const name = family
      .split(/[\t\n\r\f ]+/u)
      .join(" ")
      .toLowerCase();
    if (!FAMILIES.has(name) && !GENERICS.has(name)) throw new CssRefusal("font");
  }
}

function box(value: string): number[] {
  const parts = value.trim().split(/[\t\n\r\f ]+/u);
  if (parts.length < 1 || parts.length > 4) throw new CssRefusal("css-value");
  const values = parts.map((part) => absolute(part));
  const [top = 0, right = top, bottom = top, left = right] = values;
  return [top, right, bottom, left];
}

// T3's border styles; `hidden`, `groove`, `ridge`, `inset` and `outset` refuse.
const BORDER_STYLES = new Set(["none", "solid", "double", "dotted", "dashed"]);
const BORDER_KEYWORD_WIDTH: Readonly<Record<string, number>> = {
  thin: 0.75,
  medium: 2.25,
  thick: 3.75,
};

// A `border` shorthand or `border-<side>`: each of a width, a style and a colour at most once.
function border(value: string): Border {
  let width: number | undefined;
  let style: string | undefined;
  let colourValue: Rgb | undefined;
  let sawColour = false;
  const tokens = value.trim().match(/rgb\([^)]*\)|[^\t\n\r\f ]+/giu) ?? [];
  if (tokens.length === 0 || tokens.length > 3) throw new CssRefusal("css-value");
  for (const token of tokens) {
    const lower = token.toLowerCase();
    if (BORDER_STYLES.has(lower)) {
      if (style !== undefined) throw new CssRefusal("css-value");
      style = lower;
    } else if (lower in BORDER_KEYWORD_WIDTH || /^[+-]?[\d.]/u.test(lower)) {
      if (width !== undefined) throw new CssRefusal("css-value");
      width = BORDER_KEYWORD_WIDTH[lower] ?? absolute(lower);
      if (width < 0) throw new CssRefusal("css-value");
    } else {
      if (sawColour) throw new CssRefusal("css-value");
      const parsed = colour(lower);
      colourValue = parsed === "transparent" ? undefined : parsed;
      sawColour = true;
    }
  }
  return {
    widthPt: width ?? 2.25,
    style: style ?? "none",
    colours: sawColour && colourValue !== undefined ? [colourValue] : undefined,
  };
}

const SIDES = ["top", "right", "bottom", "left"] as const;
type Side = (typeof SIDES)[number];

const ANY_VALUE =
  /^(?:mso-[a-z-]+|tab-stops|page-break-(?:before|after|inside)|break-(?:before|after|inside)|widows|orphans)$/u;

export type ElementContext = {
  name: string;
  parent: ComputedStyle;
  // `href` present on an `a`.
  isLink: boolean;
  // The `cellpadding` of the enclosing table, in points, for a `td` or `th`.
  cellPadding: number | undefined;
  style: string | undefined;
};

function smaller(size: Size): Size {
  return { lo: size.lo * 0.75, hi: size.hi * 0.9 };
}

// The computed style of an element, from its parent's, the browser's defaults and its inline
// declarations (T1's model); a refusal where a declaration is outside T3.
export function computeStyle(context: ElementContext): ComputedStyle {
  const { name, parent } = context;
  const isBlock = BLOCKS.has(name);
  const isTablePart = TABLE_PARTS.has(name);
  let size: Size = parent.size;
  if (name === "sup" || name === "sub" || name === "small") size = smaller(size);
  const heading = HEADING_EM[name];
  if (heading !== undefined) size = { lo: size.lo * heading, hi: size.hi * heading };
  const cellPadding = context.cellPadding ?? 0.75;
  const style: ComputedStyle = {
    size,
    lineHeight: parent.lineHeight,
    colour: parent.colour,
    colourSinceLink: context.isLink ? false : parent.colourSinceLink,
    underLink: context.isLink || parent.underLink,
    background: undefined,
    backgrounds: parent.backgrounds,
    textIndent: name === "table" ? 0 : parent.textIndent,
    underline: parent.underline || context.isLink || name === "u",
    margin: {
      top: 0,
      right: name === "blockquote" ? 30 : 0,
      bottom: 0,
      left: name === "blockquote" || name === "dd" ? 30 : 0,
    },
    padding:
      name === "td" || name === "th"
        ? { top: cellPadding, right: cellPadding, bottom: cellPadding, left: cellPadding }
        : { top: 0, right: 0, bottom: 0, left: name === "ol" || name === "ul" ? 30 : 0 },
    borders: {},
    borderCollapse: undefined,
    shift: name === "sup" ? { kind: "super" } : name === "sub" ? { kind: "sub" } : { kind: "none" },
    declaredPosition: false,
    declaredVerticalAlign: undefined,
    hasWidthOrHeight: false,
    declared: new Set(),
  };
  const declaredNames = new Set<string>();

  const declared = context.style === undefined ? [] : declarations(context.style);
  let position: string | undefined;
  let top: Length | undefined;
  let bottom: Length | undefined;
  let lineHeight: { kind: "normal" } | { kind: "number"; value: number } | Length | undefined;

  for (const { property, value } of declared) {
    const keyword = value.toLowerCase();
    declaredNames.add(property);
    if (property === "margin" || property === "padding") {
      for (const which of SIDES) declaredNames.add(`${property}-${which}`);
    }
    if (ANY_VALUE.test(property)) continue;
    switch (property) {
      case "font-family":
        families(value);
        break;
      case "font-size": {
        const parsed = length(value, true);
        if ("points" in parsed) {
          if (parsed.points <= 0) throw new CssRefusal("font-size");
          size = { lo: parsed.points, hi: parsed.points };
        } else {
          const factor = "em" in parsed ? parsed.em : parsed.percent / 100;
          if (factor <= 0) throw new CssRefusal("font-size");
          size = { lo: parent.size.lo * factor, hi: parent.size.hi * factor };
        }
        break;
      }
      case "font-style":
        if (!["normal", "italic", "oblique"].includes(keyword)) throw new CssRefusal("css-value");
        break;
      case "font-weight":
        if (!/^(?:normal|bold|bolder|lighter|[1-9]00)$/u.test(keyword)) {
          throw new CssRefusal("css-value");
        }
        break;
      case "color": {
        const parsed = colour(value);
        style.colour = parsed === "transparent" ? undefined : parsed;
        style.colourSinceLink = true;
        break;
      }
      case "background":
      case "background-color": {
        const parsed = colour(value, true);
        style.background = parsed === "transparent" ? undefined : parsed;
        break;
      }
      case "margin":
      case "padding": {
        const [t, r, b, l] = box(value);
        style[property] = { top: t ?? 0, right: r ?? 0, bottom: b ?? 0, left: l ?? 0 };
        break;
      }
      case "margin-top":
      case "margin-right":
      case "margin-bottom":
      case "margin-left":
      case "padding-top":
      case "padding-right":
      case "padding-bottom":
      case "padding-left": {
        const [which, side] = property.split("-") as ["margin" | "padding", Side];
        style[which] = { ...style[which], [side]: absolute(value) };
        break;
      }
      case "text-indent":
        style.textIndent = absolute(value);
        break;
      case "line-height":
        if (keyword === "normal") lineHeight = { kind: "normal" };
        else if (/^[+]?(?:\d+(?:\.\d+)?|\.\d+)$/u.test(value)) {
          lineHeight = { kind: "number", value: Number(value) };
        } else lineHeight = length(value, true);
        break;
      case "text-align":
        if (!["left", "right", "center", "justify", "start", "end"].includes(keyword)) {
          throw new CssRefusal("css-value");
        }
        break;
      case "width":
      case "height": {
        // A caption's height can draw its text over the first row, in any layout.
        if (!isTablePart || (name === "caption" && property === "height")) {
          throw new CssRefusal("css-property");
        }
        const parsed = length(value, true);
        if ("em" in parsed) throw new CssRefusal("css-value");
        if (("points" in parsed ? parsed.points : parsed.percent) < 0)
          throw new CssRefusal("css-value");
        style.hasWidthOrHeight = true;
        break;
      }
      case "border-collapse":
        if (name !== "table" || !["collapse", "separate"].includes(keyword)) {
          throw new CssRefusal("css-value");
        }
        style.borderCollapse = keyword as "collapse" | "separate";
        break;
      case "border-image":
        if (!["none", "initial"].includes(keyword)) throw new CssRefusal("css-value");
        break;
      case "position":
        if (isBlock || keyword !== "relative") throw new CssRefusal("css-value");
        position = keyword;
        break;
      case "top":
        top = length(value, true);
        if ("percent" in top) throw new CssRefusal("css-value");
        break;
      case "bottom":
        bottom = length(value, true);
        if ("percent" in bottom) throw new CssRefusal("css-value");
        break;
      case "vertical-align":
        style.declaredVerticalAlign = keyword;
        break;
      case "text-decoration":
      case "text-decoration-line":
        if (keyword === "underline") style.underline = true;
        else if (keyword !== "none") throw new CssRefusal("css-value");
        break;
      case "text-autospace":
        if (keyword !== "none") throw new CssRefusal("css-value");
        break;
      default: {
        const side = /^border(?:-(top|right|bottom|left))?(?:-(width|style|color))?$/u.exec(
          property,
        );
        if (side === null) throw new CssRefusal("css-property");
        if (!isTablePart) throw new CssRefusal("css-property");
        const sides: readonly Side[] = side[1] === undefined ? SIDES : [side[1] as Side];
        const part = side[2];
        for (const which of sides) {
          const current = style.borders[which] ?? {
            widthPt: 2.25,
            style: "none",
            colours: undefined,
          };
          if (part === undefined) style.borders[which] = border(value);
          else if (part === "width") {
            const width = BORDER_KEYWORD_WIDTH[keyword] ?? absolute(value);
            if (width < 0) throw new CssRefusal("css-value");
            style.borders[which] = { ...current, widthPt: width };
          } else if (part === "style") {
            if (!BORDER_STYLES.has(keyword)) throw new CssRefusal("css-value");
            style.borders[which] = { ...current, style: keyword };
          } else {
            const parsed = colour(value);
            style.borders[which] = {
              ...current,
              colours: parsed === "transparent" ? undefined : [parsed],
            };
          }
        }
      }
    }
  }

  // Borders: at most 3 pt wide (T3); a colour left out is the colour the element's text is drawn
  // in: both link colours under a link with no author colour since it, else its `color`.
  for (const which of SIDES) {
    const drawn = style.borders[which];
    if (drawn === undefined) continue;
    if (drawn.style !== "none" && drawn.widthPt > 3) throw new CssRefusal("css-value");
    if (drawn.colours === undefined) {
      const current =
        style.underLink && !style.colourSinceLink
          ? LINK_COLOURS
          : [style.colour ?? ([0, 0, 0] as const)];
      style.borders[which] = { ...drawn, colours: current };
    }
  }
  style.size = size;
  style.declared = declaredNames;
  // A line height in `em` or `%` resolves against the element's own final size; a number is kept
  // as the number (it applies to every descendant's size); a length inherits as computed.
  if (lineHeight !== undefined) {
    if ("kind" in lineHeight) style.lineHeight = lineHeight;
    else if ("points" in lineHeight) {
      style.lineHeight = { kind: "length", lo: lineHeight.points, hi: lineHeight.points };
    } else {
      const factor = "em" in lineHeight ? lineHeight.em : lineHeight.percent / 100;
      style.lineHeight = {
        kind: "length",
        lo: size.lo * factor,
        hi: size.hi * factor,
        of: size,
        factor,
      };
    }
  }
  if (style.background !== undefined) style.backgrounds = [...parent.backgrounds, style.background];

  // Shifts (T4): `position: relative` with exactly one of `top`, `bottom`; `vertical-align` on
  // an inline element other than `sup` and `sub`; not both.
  if ((top !== undefined || bottom !== undefined) && position === undefined) {
    throw new CssRefusal("css-value");
  }
  if (position !== undefined) {
    if (top !== undefined && bottom !== undefined) throw new CssRefusal("css-value");
    const offset = top ?? bottom;
    if (offset === undefined) throw new CssRefusal("css-value");
    style.declaredPosition = true;
    const sign = top !== undefined ? -1 : 1;
    style.shift =
      "em" in offset
        ? { kind: "em", raise: sign * offset.em }
        : { kind: "points", raise: sign * ("points" in offset ? offset.points : Number.NaN) };
  }
  if (style.declaredVerticalAlign !== undefined) {
    const align = style.declaredVerticalAlign;
    if (name === "td" || name === "th") {
      if (!["top", "middle", "bottom", "baseline"].includes(align))
        throw new CssRefusal("css-value");
    } else {
      if (isBlock || name === "sup" || name === "sub") throw new CssRefusal("css-value");
      if (style.declaredPosition) throw new CssRefusal("css-value");
      if (align === "super") style.shift = { kind: "super" };
      else if (align === "sub") style.shift = { kind: "sub" };
      else if (align !== "baseline") {
        const parsed = length(align, true);
        if ("percent" in parsed) throw new CssRefusal("css-value");
        style.shift =
          "em" in parsed
            ? { kind: "em", raise: parsed.em }
            : { kind: "points", raise: "points" in parsed ? parsed.points : Number.NaN };
      }
    }
  }
  if ((name === "sup" || name === "sub") && style.declaredPosition)
    throw new CssRefusal("baseline-shift");
  return style;
}

export function rootStyle(): ComputedStyle {
  return {
    size: ROOT_SIZE,
    lineHeight: { kind: "normal" },
    colour: undefined,
    colourSinceLink: false,
    underLink: false,
    background: undefined,
    backgrounds: [],
    textIndent: 0,
    underline: false,
    margin: { top: 0, right: 0, bottom: 0, left: 0 },
    padding: { top: 0, right: 0, bottom: 0, left: 0 },
    borders: {},
    borderCollapse: undefined,
    shift: { kind: "none" },
    declaredPosition: false,
    declaredVerticalAlign: undefined,
    hasWidthOrHeight: false,
    declared: new Set(),
  };
}

export const ROOT_BACKGROUND = WHITE;
