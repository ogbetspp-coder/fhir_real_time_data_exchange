import namedColours from "../data/named-colours.json" with { type: "json" };

// T's reading of an inline `style` (docs/design/authority-import-t.md, T1 and T3): the
// declaration grammar, lengths and colours. Anything outside the grammar is a refusal with a
// closed reason; the caller turns it into ImportRefusedError.

export class CssRefusal extends Error {
  public constructor(public readonly reason: string) {
    super(reason);
    this.name = "CssRefusal";
  }
}

export type Declaration = { property: string; value: string };

// Printable ASCII only, no comment, escape, block, `!important`, `url(` or `&` (T1's cascade).
const STYLE_CHARACTERS = /^[\x20-\x7e\t\n\r\f]*$/u;
const FORBIDDEN = /\/\*|\\|[{}<>@]|!|url\(|&/iu;
const FUNCTION = /[a-z-]+\(/giu;
const QUOTED_FAMILY = /^(?:'[^'";]*'|"[^'";]*"|[^'";]*)$/u;
const GLOBAL_KEYWORDS = new Set(["inherit", "initial", "unset", "revert", "revert-layer"]);

// The declarations of a `style` value, in order, names lower-cased and values trimmed.
export function declarations(style: string): Declaration[] {
  if (!STYLE_CHARACTERS.test(style) || FORBIDDEN.test(style)) throw new CssRefusal("css-grammar");
  for (const match of style.matchAll(FUNCTION)) {
    if (match[0].toLowerCase() !== "rgb(") throw new CssRefusal("css-grammar");
  }
  // CSS reads a bracket or a function to its matching close, a `;` inside included: a bracket
  // other than a balanced `rgb(…)` holding no `;` refuses, so a split at `;` is Chrome's.
  if (/[()[\]]/u.test(style.replace(/rgb\([^;()[\]]*\)/giu, ""))) {
    throw new CssRefusal("css-grammar");
  }
  // A line break inside quotes is a bad string, which Chrome drops with what follows it; read in
  // one pass, so the check is linear.
  let quote: string | undefined;
  for (const character of style) {
    if (quote === undefined) {
      if (character === "'" || character === '"') quote = character;
    } else if (character === quote) quote = undefined;
    else if (character === "\r" || character === "\n" || character === "\f") {
      throw new CssRefusal("css-grammar");
    }
  }

  const result: Declaration[] = [];
  for (const part of style.split(";")) {
    if (part.trim() === "") continue;
    const colon = part.indexOf(":");
    if (colon === -1) throw new CssRefusal("css-grammar");
    const property = part.slice(0, colon).trim().toLowerCase();
    const value = part.slice(colon + 1).trim();
    if (!/^-?[a-z][a-z0-9-]*$/u.test(property) || value === "") {
      throw new CssRefusal("css-grammar");
    }
    if (/['"]/u.test(value)) {
      const families = value.split(",").map((family) => family.trim());
      if (property !== "font-family" || !families.every((family) => QUOTED_FAMILY.test(family))) {
        throw new CssRefusal("css-grammar");
      }
    }
    const keyword = value.toLowerCase();
    if (GLOBAL_KEYWORDS.has(keyword) && !(property === "border-image" && keyword === "initial")) {
      throw new CssRefusal("css-grammar");
    }
    if (/\bcurrentcolor\b/iu.test(value)) throw new CssRefusal("css-grammar");
    result.push({ property, value });
  }
  return result;
}

// --- lengths ---------------------------------------------------------------------------------

const POINTS_PER_UNIT: Readonly<Record<string, number>> = {
  pt: 1,
  px: 0.75,
  pc: 12,
  in: 72,
  cm: 72 / 2.54,
  mm: 72 / 25.4,
};
const LENGTH = /^([+-]?(?:\d+(?:\.\d+)?|\.\d+))(pt|px|pc|in|cm|mm|em|%)?$/iu;

export type Length = { points: number } | { em: number } | { percent: number };

// A length in T's units (T1): absolute units in points, `em` and `%` kept relative for the caller
// to resolve, and `0` without a unit. `allowRelative` says whether this property accepts them.
export function length(value: string, allowRelative: boolean): Length {
  const match = LENGTH.exec(value.trim());
  if (match === null) throw new CssRefusal("css-value");
  const number = Number(match[1]);
  const unit = (match[2] ?? "").toLowerCase();
  if (unit === "") {
    if (number !== 0) throw new CssRefusal("css-value");
    return { points: 0 };
  }
  if (unit === "em" || unit === "%") {
    if (!allowRelative) throw new CssRefusal("css-value");
    return unit === "em" ? { em: number } : { percent: number };
  }
  return { points: number * (POINTS_PER_UNIT[unit] ?? Number.NaN) };
}

// An absolute length in points (T1: `margin`, `padding`, `text-indent` and border widths take the
// absolute units only).
export function absolute(value: string): number {
  const parsed = length(value, false);
  return "points" in parsed ? parsed.points : Number.NaN;
}

// --- colours ---------------------------------------------------------------------------------

export type Rgb = readonly [number, number, number];

const NAMED: ReadonlyMap<string, string> = new Map(Object.entries(namedColours));
const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/iu;
const RGB = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/iu;

function hex(digits: string): Rgb {
  const full = digits.length === 3 ? Array.from(digits, (digit) => digit + digit).join("") : digits;
  return [
    Number.parseInt(full.slice(0, 2), 16),
    Number.parseInt(full.slice(2, 4), 16),
    Number.parseInt(full.slice(4, 6), 16),
  ];
}

// A colour of T3a's list: `#rgb`, `#rrggbb`, `rgb()` with integer channels, a CSS named colour,
// or `windowtext` (black). `transparent` is a background's only (the caller asks for it).
export function colour(value: string, allowTransparent = false): Rgb | "transparent" {
  const text = value.trim().toLowerCase();
  if (text === "transparent") {
    if (!allowTransparent) throw new CssRefusal("css-value");
    return "transparent";
  }
  if (text === "windowtext") return [0, 0, 0];
  const named = NAMED.get(text);
  if (named !== undefined) return hex(named);
  const hexMatch = HEX.exec(text);
  if (hexMatch !== null) return hex(hexMatch[1] ?? "");
  const rgbMatch = RGB.exec(text);
  if (rgbMatch !== null) {
    const channels = [rgbMatch[1], rgbMatch[2], rgbMatch[3]].map(Number);
    if (channels.some((channel) => channel > 255)) throw new CssRefusal("css-value");
    return [channels[0] ?? 0, channels[1] ?? 0, channels[2] ?? 0];
  }
  throw new CssRefusal("css-value");
}

function linear(channel: number): number {
  const value = channel / 255;
  return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function luminance([red, green, blue]: Rgb): number {
  return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue);
}

// WCAG 2's contrast ratio of two colours (T3a).
export function contrast(first: Rgb, second: Rgb): number {
  const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return ((light ?? 0) + 0.05) / ((dark ?? 0) + 0.05);
}
