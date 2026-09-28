// Fail-closed XHTML narrative scanner (docs/fidelity-normalization.md section 5). It is not a
// general HTML parser: it accepts only the closed lists below so that no element or attribute
// can hide, carry, or reorder narrative text, and it rejects everything else. Where a renderer
// would draw markup differently from the text this scanner emits — a `/` an HTML parser ignores,
// a raised digit, content a table moves, a soft hyphen before a visible line break — the markup
// is either folded into the text or rejected.

import { sha256Utf8 } from "../lib/hash.js";
import {
  composeText,
  findForbiddenCharacter,
  isDefaultIgnorable,
  isForbiddenCodePoint,
  isGap,
} from "./normalize.js";

export type XhtmlErrorCode =
  | "forbidden-character"
  | "root-not-div"
  | "multiple-roots"
  | "text-outside-root"
  | "unknown-element"
  | "uppercase-element"
  | "forbidden-attribute"
  | "void-element"
  | "malformed-tag"
  | "stray-lt"
  | "stray-amp"
  | "unknown-entity"
  | "comment"
  | "processing-instruction"
  | "cdata"
  | "doctype"
  | "unbalanced-tag"
  | "misnested-tag"
  | "script-content"
  | "unmappable-script"
  | "table-content"
  | "list-content"
  | "reserved-character"
  | "invisible-character"
  | "nesting-depth"
  | "combining-across-markup"
  | "table-section-order"
  | "table-structure"
  | "table-shape"
  | "table-size";

// `offset` is in code points into the div, for every code: where the markup or text that is
// refused begins.
export class XhtmlError extends Error {
  public constructor(
    public readonly code: XhtmlErrorCode,
    public readonly offset: number,
  ) {
    super(`XHTML narrative rejected (${code}) at offset ${offset}`);
    this.name = "XhtmlError";
  }
}

const XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml";

// `pre` is not here: a renderer keeps its whitespace and so draws columns the check cannot see.
const BLOCK_ELEMENTS = new Set([
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
]);

const INLINE_ELEMENTS = new Set([
  "span",
  "b",
  "i",
  "em",
  "strong",
  "sup",
  "sub",
  "small",
  "abbr",
  "cite",
  "code",
  "img",
]);

// `q` is excluded: a renderer draws quotation marks the source may not contain. `u` and `a` are
// excluded from 3.0.0: a renderer underlines both (`a` with a target), and an underline turns a
// sign into another, "<" into "≤", ">" into "≥", "+" into "±", and "1" `u`"a" into "1ª", which no
// closed list of code points can bound. `ol` is allowed because the numbers a renderer draws are
// emitted as text (below).

// The only elements that may be, and must be, self-closing. An HTML parser ignores the `/` of
// `<sup/>`, so any other element written that way opens around the text that follows it; and a
// `<br>` without `/` is a start tag whose content an XML renderer does not draw.
const VOID_ELEMENTS = new Set(["br", "hr", "img"]);

// Table parts and the parents each may have. A renderer moves anything else it finds directly
// inside a table container out of the table, so that is rejected (`table-content`).
const TABLE_PART_PARENTS = new Map<string, ReadonlySet<string>>([
  ["caption", new Set(["table"])],
  ["thead", new Set(["table"])],
  ["tbody", new Set(["table"])],
  ["tfoot", new Set(["table"])],
  ["tr", new Set(["table", "thead", "tbody", "tfoot"])],
  ["td", new Set(["tr"])],
  ["th", new Set(["tr"])],
]);
const TABLE_CONTAINERS = new Set(["table", "thead", "tbody", "tfoot", "tr"]);
const ROW_GROUPS = new Set(["thead", "tbody", "tfoot"]);

// A renderer numbers only the `li` children of a list, and moves nothing out of it; anything else
// directly inside `ol` or `ul` is drawn outside the numbering (`list-content`).
const LIST_CONTAINERS = new Set(["ol", "ul"]);

// Reserved code points (section 2 of 3.0.0's proposal, section 5): the scanner emits them for
// table grids and pictures, so they never occur in narrative text itself.
export const TABLE_START = "\ufdd0";
export const TABLE_END = "\ufdd1";
export const ROW_START = "\ufdd2";
export const CELL_START = "\ufdd3";
export const COVERED_LEFT = "\ufdd4";
export const COVERED_ABOVE = "\ufdd5";
export const PICTURE = "\ufffc";

// The grid markers: structure, not text a reader sees.
export function isGridMarker(codePoint: number): boolean {
  return codePoint >= 0xfdd0 && codePoint <= 0xfdd5;
}

// Whether normalised narrative text holds anything a reader sees: a table of empty cells, whose
// text is only grid markers, and text of only gaps (a thin space, a blank glyph, a code point
// Unicode says to ignore) draw nothing inked (section 5, `empty-narrative`).
export function hasDrawnText(normalized: string): boolean {
  for (const character of normalized) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (!isGap(codePoint) && !isGridMarker(codePoint)) return true;
  }
  return false;
}

// The slots all tables of one narrative may cover together. A small table can span a large grid
// (`colspan="1000" rowspan="1000"` is a million slots, each a marker in the text), so the grid is
// bounded, and a real table is far inside the bound (`table-size`).
export const TABLE_SLOT_LIMIT = 50_000;

// A soft hyphen and a zero-width space are break opportunities a renderer may use at a narrow
// width, drawing "2-" / "10 mg" or "2" / "10 mg" where the check reads "210 mg", so narrative
// holds neither (section 2; one-sided, since page text marks a hyphenated line end with U+00AD).
export function isInvisibleBreak(codePoint: number): boolean {
  return codePoint === 0x00ad || codePoint === 0x200b;
}

function findInvisibleBreak(text: string): number | undefined {
  let offset = 0;
  for (const character of text) {
    if (isInvisibleBreak(character.codePointAt(0) ?? 0)) return offset;
    offset += 1;
  }
  return undefined;
}

// How deep markup may nest (section 5). An HTML parser stops nesting at 512 open elements and
// moves what follows elsewhere; `small` inside `small` and a heading inside a heading shrink text
// towards illegible; and every indenting container moves text further right, off a narrow page.
const MAX_DEPTH = 32;
const MAX_INDENTS = 6;
const HEADINGS = new Set(["h1", "h2", "h3", "h4", "h5", "h6"]);
// The elements a renderer draws smaller than the text around them: at most one open at once, so
// `<h6><small>` (drawn at about 9 px, and 7 px with a `sup`) is refused.
const SHRINKING = new Set(["small", "code", "h5", "h6"]);
const INDENTING = new Set(["blockquote", "ul", "ol", "dd"]);

function checkNesting(name: string, stack: readonly string[], offset: number): void {
  if (stack.length > MAX_DEPTH) throw new XhtmlError("nesting-depth", offset);
  if (SHRINKING.has(name) && stack.some((open) => SHRINKING.has(open))) {
    throw new XhtmlError("nesting-depth", offset);
  }
  if (HEADINGS.has(name) && stack.some((open) => HEADINGS.has(open))) {
    throw new XhtmlError("nesting-depth", offset);
  }
  if (INDENTING.has(name)) {
    const indents = stack.filter((open) => INDENTING.has(open)).length + 1;
    if (indents > MAX_INDENTS) throw new XhtmlError("nesting-depth", offset);
  }
}

// The inline elements whose tags split text without emitting anything: a renderer draws the text
// on each side in its own run, so a mark after the tag does not combine with the letter before it.
const SPLITTING_INLINE = new Set([
  "span",
  "b",
  "i",
  "em",
  "strong",
  "sup",
  "sub",
  "small",
  "abbr",
  "cite",
  "code",
]);
// How far each side of such a tag is composed to find a composition across it. A combining mark
// right after the tag is refused whatever its distance from the letter, so the window only has
// to catch the Hangul jamo that compose without being marks.
const COMPOSE_WINDOW = 64;
const MARK = /^\p{M}$/u;

// Output positions (array indexes) to code point offsets in the joined text, for positions
// asked in increasing order.
function pointOffsets(output: readonly string[]): (position: number) => number {
  let offset = 0;
  let piece = 0;
  return (position) => {
    for (; piece < position; piece += 1) offset += Array.from(output[piece] ?? "").length;
    return offset;
  };
}

// A `sub` holding ½, by output positions (array indexes) of its content, and the offset in the div
// of its first ½ (the character or its reference).
type LoweredHalf = { start: number; end: number; offset: number };
// The neighbours of a kept lowered ½ (section 5): the half-life, `t<sub>½</sub>`, as a word.
const BEFORE_HALF_LIFE = new Set(["\n", "\t", " ", "("]);
const AFTER_HALF = new Set(["\n", "\t", " ", ")", ".", ",", ";", ":"]);

// A lowered ½ is kept only as the half-life: a `sub`'s whole content, right after a `t` that
// starts a word (after a break, a space, `(` or nothing), and right before a break, a space,
// `) . , ; :` or nothing, every neighbour drawn on the line (section 5). Anywhere else it can join
// a number or an index, and the text cannot say which: `log<sub>2½</sub>` and `log<sub>2</sub>½`
// both read `log₂½`, and a letter before it can be a number or an operator (`VIII<sub>½</sub>`,
// `log<sub>½</sub>`). Nothing is read past: each neighbour is the adjacent emitted code point.
function checkLoweredHalves(
  output: readonly string[],
  halves: readonly LoweredHalf[],
  scriptPieces: ReadonlySet<number>,
): void {
  // A code point of the text as (piece, index in the piece), found by stepping over the pieces
  // that emit nothing; a piece emitted inside `sup` or `sub` holds one code point. The steps of
  // different halves do not overlap, so the rule stays linear without a copy of the text.
  type At = { point: string; piece: number; index: number } | undefined;
  const last = (piece: number): At => {
    let position = piece;
    while (position >= 0 && output[position] === "") position -= 1;
    const points = Array.from(output[position] ?? "");
    return position < 0
      ? undefined
      : { point: points.at(-1) ?? "", piece: position, index: points.length - 1 };
  };
  const previous = (at: NonNullable<At>): At =>
    at.index > 0
      ? {
          point: Array.from(output[at.piece] ?? "")[at.index - 1] ?? "",
          piece: at.piece,
          index: at.index - 1,
        }
      : last(at.piece - 1);
  const next = (piece: number): At => {
    let position = piece;
    while (position < output.length && output[position] === "") position += 1;
    const point = Array.from(output[position] ?? "")[0];
    return point === undefined ? undefined : { point, piece: position, index: 0 };
  };
  const onLine = (at: At, allowed: ReadonlySet<string>): boolean =>
    at === undefined || (allowed.has(at.point) && !scriptPieces.has(at.piece));
  for (const { start, end, offset } of halves) {
    const letter = last(start - 1);
    if (
      output.slice(start, end).join("") !== String.fromCodePoint(HALF) ||
      letter?.point !== "t" ||
      scriptPieces.has(letter.piece) ||
      !onLine(previous(letter), BEFORE_HALF_LIFE) ||
      !onLine(next(end), AFTER_HALF)
    ) {
      throw new XhtmlError("unmappable-script", offset);
    }
  }
}

// The boundaries are code point offsets in the text, each with the UTF-16 index in the div of
// the tag that makes it, which an error reports.
function checkComposition(
  text: string,
  boundaries: readonly number[],
  tags: readonly number[],
): void {
  const points = Array.from(text);
  // The first code point at or after each boundary that is not a Default_Ignorable code point
  // other than a mark: a word joiner or a zero-width joiner between the tag and a mark is drawn as
  // nothing, and the mark after it is still drawn apart from the letter before the tag. Boundaries
  // only increase, so one cursor reads each run of ignorables once.
  let cursor = 0;
  for (const [position, boundary] of boundaries.entries()) {
    const tag = tags[position] ?? 0;
    if (cursor < boundary) cursor = boundary;
    for (; cursor < points.length; cursor += 1) {
      const point = points[cursor] ?? "";
      if (MARK.test(point) || !isDefaultIgnorable(point.codePointAt(0) ?? 0)) break;
    }
    if (MARK.test(points[cursor] ?? "")) {
      throw new XhtmlError("combining-across-markup", tag);
    }
    // A boundary before a code point below U+0300, U+00AD aside, is stable: every such code point
    // is a starter that NFC never composes with what precedes it (canonical combining class 0,
    // NFC_QC=Yes; test/fidelity-composition.test.ts checks each one), so the two sides compose the
    // same apart as together. U+00AD, which step 1 removes, never reaches the text (section 2).
    const next = points[boundary]?.codePointAt(0);
    if (next === undefined || (next < 0x0300 && next !== 0x00ad)) continue;
    const before = points.slice(Math.max(0, boundary - COMPOSE_WINDOW), boundary).join("");
    const after = points.slice(boundary, boundary + COMPOSE_WINDOW).join("");
    if (composeText(before + after) !== composeText(before) + composeText(after)) {
      throw new XhtmlError("combining-across-markup", tag);
    }
  }
}

export function isReservedCodePoint(codePoint: number): boolean {
  return codePoint === 0xfffc || (codePoint >= 0xfdd0 && codePoint <= 0xfdef);
}

// Code-point offset of the first reserved code point, if any.
function findReservedCharacter(text: string): number | undefined {
  let offset = 0;
  for (const character of text) {
    if (isReservedCodePoint(character.codePointAt(0) ?? 0)) return offset;
    offset += 1;
  }
  return undefined;
}

type TableState = {
  caption: boolean;
  head: boolean;
  body: boolean;
  foot: boolean;
  rows: boolean;
  // The grid, laid out by the HTML table model, and kept sparse so that a row costs only the
  // slots it covers: for each column a cell above still covers, how many rows from the current
  // one it covers.
  above: Map<number, number>;
  // The current row: the slots covered, the highest of them, the next slot not yet emitted, for
  // each column how many rows below it a cell placed in this row covers, and whether a cell that
  // spans no rows starts in it.
  covered: Set<number>;
  last: number;
  cursor: number;
  down: Map<number, number>;
  single: boolean;
  // The columns in which a cell that spans no columns starts, over the whole table.
  singleColumns: Set<number>;
  // The colspan of the open cell, whose covered-left slots its end tag emits.
  openSpan: number;
  // Slots covered by every row of this table, or -1 for a row with a hole.
  widths: number[];
};

type ListState = { style: string; next: number };

export const NAMED_ENTITIES: ReadonlyMap<string, string> = new Map<string, string>([
  ["amp", "&"],
  ["lt", "<"],
  ["gt", ">"],
  ["quot", '"'],
  ["apos", "'"],
]);

// Super- and subscript folding (section 5): the characters that change what a number means are
// folded to their script code points, so `10<sup>6</sup>` is `10⁶` and never equals `106`.
const SUPERSCRIPT_DIGITS = [
  0x2070, 0x00b9, 0x00b2, 0x00b3, 0x2074, 0x2075, 0x2076, 0x2077, 0x2078, 0x2079,
];
const SUBSCRIPT_DIGITS = [
  0x2080, 0x2081, 0x2082, 0x2083, 0x2084, 0x2085, 0x2086, 0x2087, 0x2088, 0x2089,
];
// Plus, minus, equals, open and close, in that order.
type ScriptSigns = readonly [number, number, number, number, number];
const SUPERSCRIPT_SIGNS: ScriptSigns = [0x207a, 0x207b, 0x207c, 0x207d, 0x207e];
const SUBSCRIPT_SIGNS: ScriptSigns = [0x208a, 0x208b, 0x208c, 0x208d, 0x208e];
const PLUS_SIGNS = [0x002b, 0xfe62, 0xff0b, 0x2795];
const MINUS_SIGNS = [
  0x002d, 0x2212, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x02d7, 0xfe58, 0xfe63, 0xff0d,
  0x2796,
];

// The script letters of each kind: a subscript letter raised is not a superscript one.
const SUPERSCRIPT_LETTERS = [0x2071, 0x207f];
const SUBSCRIPT_LETTERS = [
  0x2090, 0x2091, 0x2092, 0x2093, 0x2094, 0x2095, 0x2096, 0x2097, 0x2098, 0x2099, 0x209a, 0x209b,
  0x209c,
];

// Kept unchanged inside `sub` from fidelity-norm/3.1.0: U+00BD VULGAR FRACTION ONE HALF and
// U+221E INFINITY, as in `t<sub>½</sub>` and `AUC<sub>(0-∞)</sub>`. Neither has a subscript form;
// raised, `2<sup>½</sup>` is a root. ½ is a number, so it is kept only where it cannot join a
// number on either side (checkLoweredHalves); ∞ never joins a number, and loses its position as
// a letter does (section 5's stated residual).
const HALF = 0x00bd;
const KEPT_IN_SUBSCRIPT = [HALF, 0x221e];

type ScriptRule = {
  folding: ReadonlyMap<number, number>;
  // The element's own script digits and signs, and what it keeps unchanged, kept as they are.
  own: ReadonlySet<number>;
  // The other script's digits, signs and letters.
  foreign: ReadonlySet<number>;
};

function scriptRule(
  digits: readonly number[],
  signs: ScriptSigns,
  foreign: readonly number[],
  kept: readonly number[] = [],
): ScriptRule {
  const [plus, minus, equals, open, close] = signs;
  const folding = new Map<number, number>();
  digits.forEach((target, digit) => folding.set(0x0030 + digit, target));
  for (const sign of PLUS_SIGNS) folding.set(sign, plus);
  for (const sign of MINUS_SIGNS) folding.set(sign, minus);
  folding.set(0x003d, equals);
  folding.set(0x0028, open);
  folding.set(0x0029, close);
  return { folding, own: new Set([...digits, ...signs, ...kept]), foreign: new Set(foreign) };
}

const SCRIPT_RULES: Readonly<Record<"sup" | "sub", ScriptRule>> = {
  sup: scriptRule(SUPERSCRIPT_DIGITS, SUPERSCRIPT_SIGNS, [
    ...SUBSCRIPT_DIGITS,
    ...SUBSCRIPT_SIGNS,
    ...SUBSCRIPT_LETTERS,
  ]),
  sub: scriptRule(
    SUBSCRIPT_DIGITS,
    SUBSCRIPT_SIGNS,
    [...SUPERSCRIPT_DIGITS, ...SUPERSCRIPT_SIGNS, ...SUPERSCRIPT_LETTERS],
    KEPT_IN_SUBSCRIPT,
  ),
};

// The element's own script digits and signs are kept, and so is ∞ inside `sub` (and ½ there, as
// the half-life only: checkLoweredHalves); the other script's digits, signs and letters, every
// other number (a non-ASCII digit, another fraction, a numeral), a plus-minus sign, and every
// other mathematical symbol, bracket or dash (general category Sm, Ps, Pe, Pd: `＝`, `﹙`, `⸺`)
// have no script form here and reject.
const UNMAPPABLE_SIGNS = new Set([0x00b1, 0x2213]);
const NUMBER = /^\p{N}$/u;
const SIGN_OR_BRACKET = /^[\p{Sm}\p{Ps}\p{Pe}\p{Pd}]$/u;

// What `sup` or `sub` makes of one code point of text: the code point it is folded to, the code
// point itself when it is kept, or undefined when it has no script form there
// (`unmappable-script`).
export function scriptCodePoint(element: "sup" | "sub", codePoint: number): number | undefined {
  const rule = SCRIPT_RULES[element];
  const folded = rule.folding.get(codePoint);
  if (folded !== undefined) return folded;
  const character = String.fromCodePoint(codePoint);
  if (
    UNMAPPABLE_SIGNS.has(codePoint) ||
    rule.foreign.has(codePoint) ||
    ((NUMBER.test(character) || SIGN_OR_BRACKET.test(character)) && !rule.own.has(codePoint))
  ) {
    return undefined;
  }
  return codePoint;
}

// Whitespace inside a tag is U+0009, U+000A, U+000D and U+0020 only, never `\s`: an HTML
// parser reads any other code point (U+00A0, U+3000, U+FEFF) as part of the tag name, so
// `<sup\u00a0>` is an unknown element to a renderer and must not be `sup` here.
const END_TAG = /<\/([A-Za-z][A-Za-z0-9]*)[\t\n\r ]*>/y;
const START_TAG =
  /<([A-Za-z][A-Za-z0-9]*)((?:[\t\n\r ]+[A-Za-z_:][-A-Za-z0-9_:.]*[\t\n\r ]*=[\t\n\r ]*(?:"[^"<]*"|'[^'<]*'))*)[\t\n\r ]*(\/?)>/y;
const ATTRIBUTE = /([A-Za-z_:][-A-Za-z0-9_:.]*)[\t\n\r ]*=[\t\n\r ]*(?:"([^"<]*)"|'([^'<]*)')/g;
const ENTITY = /&(?:([A-Za-z]+)|#(\d{1,7})|#x([0-9A-Fa-f]{1,6}));/y;

// The tokeniser's grammar, for the authority importer's T (src/authority/t), which must read a div
// exactly as this scanner does. Sources, not the objects: each reader keeps its own `lastIndex`.
export const XHTML_TOKENS = {
  endTag: END_TAG.source,
  startTag: START_TAG.source,
  attribute: ATTRIBUTE.source,
  entity: ENTITY.source,
} as const;

function isAsciiWhitespace(character: string): boolean {
  return character === " " || character === "\t" || character === "\n" || character === "\r";
}

// Attribute values are never compared against the source, so they must not be able to carry
// text: each allowed attribute is restricted to a short token alphabet, and to the one element
// that needs it. Nothing a viewer's stylesheet or script could key on to hide text (`class`, `id`,
// a language tag below the root, a link) is allowed.
const TOKEN_VALUE = /^[A-Za-z0-9_.:-]{1,32}$/;
export const LIST_TYPE_VALUE = /^[1aAiI]$/;
export const LIST_START_VALUE = /^(?:0|-?[1-9][0-9]{0,3})$/;
export const SPAN_VALUE = /^(?:[1-9][0-9]{0,2}|1000)$/;
// A picture's source is a PNG or JPEG `data:` URI: the picture's own bytes, compared with the
// source through the hash `img` emits. A reference (a path or a URL) is refused: what it draws is
// whatever the viewer's origin serves, or nothing, and neither is bound by the check.
const PICTURE_DATA_PREFIXES = ["data:image/png;base64,", "data:image/jpeg;base64,"];
// The base64 length of 1 MiB.
const PICTURE_DATA_LIMIT = 1_398_104;
const BASE64_ALPHABET = /^[A-Za-z0-9+/]*$/;

// A `data:` body is counted and tested directly, not matched by one regular expression over a
// value of up to 1.4 million code points: non-empty, at most the limit, a multiple of 4 long, the
// base64 alphabet, and `=` only as the last one or two code points.
function isPictureData(value: string): boolean {
  const prefix = PICTURE_DATA_PREFIXES.find((candidate) => value.startsWith(candidate));
  if (prefix === undefined) return false;
  const body = value.slice(prefix.length);
  if (body.length === 0 || body.length > PICTURE_DATA_LIMIT || body.length % 4 !== 0) return false;
  const padding = body.endsWith("==") ? 2 : body.endsWith("=") ? 1 : 0;
  return BASE64_ALPHABET.test(body.slice(0, body.length - padding));
}

function attributeAllowed(name: string, value: string, element: string, isRoot: boolean): boolean {
  if (name === "xml:lang" || name === "lang") return isRoot && TOKEN_VALUE.test(value);
  if (name === "scope") return element === "th" && TOKEN_VALUE.test(value);
  if (name === "type") return element === "ol" && LIST_TYPE_VALUE.test(value);
  if (name === "start") return element === "ol" && LIST_START_VALUE.test(value);
  if (name === "colspan" || name === "rowspan") {
    return (element === "td" || element === "th") && SPAN_VALUE.test(value);
  }
  if (name === "src") {
    return element === "img" && isPictureData(value);
  }
  return false;
}

// Checks the attributes in document order and returns their values.
function checkAttributes(
  element: string,
  attributeSource: string,
  isRoot: boolean,
  offset: number,
): Map<string, string> {
  ATTRIBUTE.lastIndex = 0;
  let sawNamespace = false;
  const values = new Map<string, string>();
  for (const match of attributeSource.matchAll(ATTRIBUTE)) {
    const name = match[1] ?? "";
    const value = match[2] ?? match[3] ?? "";
    if (values.has(name)) throw new XhtmlError("forbidden-attribute", offset);
    values.set(name, value);
    if (name === "xmlns") {
      if (!isRoot || value !== XHTML_NAMESPACE) throw new XhtmlError("forbidden-attribute", offset);
      sawNamespace = true;
      continue;
    }
    if (!attributeAllowed(name, value, element, isRoot)) {
      throw new XhtmlError("forbidden-attribute", offset);
    }
  }
  if (isRoot && !sawNamespace) throw new XhtmlError("root-not-div", offset);
  // A picture without a source would be drawn as nothing, or as the renderer's broken-image mark.
  if (element === "img" && !values.has("src")) throw new XhtmlError("forbidden-attribute", offset);
  return values;
}

// The decoded code point of a character reference, checked on its own: a reference to half a
// surrogate pair rejects even when the next reference would complete it.
function decodeEntity(match: RegExpExecArray, offset: number): number {
  const named = match[1];
  if (named !== undefined) {
    const decoded = NAMED_ENTITIES.get(named);
    if (decoded === undefined) throw new XhtmlError("unknown-entity", offset);
    return decoded.codePointAt(0) ?? 0;
  }
  const decimal = match[2];
  const hex = match[3];
  const codePoint =
    decimal !== undefined ? Number.parseInt(decimal, 10) : Number.parseInt(hex ?? "", 16);
  if (!Number.isFinite(codePoint) || codePoint > 0x10ffff) {
    throw new XhtmlError("unknown-entity", offset);
  }
  if (isForbiddenCodePoint(codePoint)) throw new XhtmlError("forbidden-character", offset);
  if (isReservedCodePoint(codePoint)) throw new XhtmlError("reserved-character", offset);
  if (isInvisibleBreak(codePoint)) throw new XhtmlError("invisible-character", offset);
  return codePoint;
}

// The elements a rule (`hr`) may not be drawn in (below).
const RULE_BREAKS_FRACTION = new Set(["td", "th", "caption"]);

// The parent check at a start tag: nothing but text inside `sup` and `sub`; each table part, and
// `li`, in its own parent; nothing but table parts inside a table container, and nothing but `li`
// inside a list.
function checkParent(name: string, parent: string | undefined, offset: number): void {
  if (parent === "sup" || parent === "sub") throw new XhtmlError("script-content", offset);
  const allowedParents = name === "li" ? LIST_CONTAINERS : TABLE_PART_PARENTS.get(name);
  if (allowedParents !== undefined) {
    if (parent === undefined || !allowedParents.has(parent)) {
      throw new XhtmlError("misnested-tag", offset);
    }
    return;
  }
  if (parent !== undefined && TABLE_CONTAINERS.has(parent)) {
    throw new XhtmlError("table-content", offset);
  }
  if (parent !== undefined && LIST_CONTAINERS.has(parent)) {
    throw new XhtmlError("list-content", offset);
  }
}

// Renderers place table parts by role, not by document position: a caption always renders
// first, sections render head → body → foot, and rows placed directly under `table` are
// wrapped in an implicit body. Only the one document order that renders as written is
// accepted, so displayed text order equals the order the source was verified in. Parents are
// already checked. A table inside an open table (in a cell or in a caption) is refused, so the
// grid text below never nests.
function enterTableStructure(
  name: string,
  parent: string | undefined,
  state: TableState | undefined,
  offset: number,
): void {
  if (name === "table") {
    if (state !== undefined) throw new XhtmlError("table-structure", offset);
    return;
  }
  if (state === undefined) return;
  if (name === "tr") {
    if (parent === "table") {
      if (state.head || state.body || state.foot) throw new XhtmlError("table-structure", offset);
      state.rows = true;
    }
    return;
  }
  if (name !== "caption" && !ROW_GROUPS.has(name)) return;
  if (name === "caption") {
    if (state.caption || state.head || state.body || state.foot || state.rows) {
      throw new XhtmlError("table-structure", offset);
    }
    state.caption = true;
    return;
  }
  if (state.rows) throw new XhtmlError("table-structure", offset);
  if (name === "thead" && (state.head || state.body || state.foot)) {
    throw new XhtmlError("table-section-order", offset);
  }
  if (name === "tbody" && state.foot) throw new XhtmlError("table-section-order", offset);
  if (name === "tfoot" && state.foot) throw new XhtmlError("table-section-order", offset);
  state[name === "thead" ? "head" : name === "tbody" ? "body" : "foot"] = true;
}

function newTable(): TableState {
  return {
    caption: false,
    head: false,
    body: false,
    foot: false,
    rows: false,
    above: new Map(),
    covered: new Set(),
    last: -1,
    cursor: 0,
    down: new Map(),
    single: false,
    singleColumns: new Set(),
    openSpan: 1,
    widths: [],
  };
}

// One slot a cell covers but does not start in, as its own whitespace-delimited token.
function coveredSlot(marker: string): string {
  return `\t${marker}\t`;
}

function startRow(state: TableState): void {
  state.covered = new Set(state.above.keys());
  state.last = -1;
  for (const column of state.above.keys()) state.last = Math.max(state.last, column);
  state.cursor = 0;
  state.down = new Map();
  state.single = false;
}

// Places a cell by the HTML table model: in the first slot of its row that no cell covers. The
// slots before it that a cell above covers are emitted first. A cell that would cover a slot
// already covered overlaps it, which a renderer draws as two texts on top of each other.
function placeCell(
  state: TableState,
  colspan: number,
  rowspan: number,
  grid: { slots: number },
  offset: number,
): string {
  let before = "";
  while (state.covered.has(state.cursor)) {
    before += coveredSlot(COVERED_ABOVE);
    state.cursor += 1;
  }
  for (let column = state.cursor; column < state.cursor + colspan; column += 1) {
    if (state.covered.has(column)) throw new XhtmlError("table-shape", offset);
  }
  grid.slots += colspan * rowspan;
  if (grid.slots > TABLE_SLOT_LIMIT) throw new XhtmlError("table-size", offset);
  for (let column = state.cursor; column < state.cursor + colspan; column += 1) {
    state.covered.add(column);
    if (rowspan > 1) state.down.set(column, rowspan - 1);
  }
  if (rowspan === 1) state.single = true;
  if (colspan === 1) state.singleColumns.add(state.cursor);
  state.last = Math.max(state.last, state.cursor + colspan - 1);
  state.cursor += colspan;
  state.openSpan = colspan;
  return before;
}

// Ends a row: the slots after its last cell that a cell above covers are emitted, and the row's
// width is recorded, or -1 when a slot inside the row is covered by nothing. A row that covers a
// slot but in which no cell spanning no rows starts is drawn at zero height, its cells' text in
// the rows around it, so it rejects.
function endRow(state: TableState, offset: number): string {
  const trailing = [...state.above.keys()].filter((column) => column >= state.cursor);
  trailing.sort((left, right) => left - right);
  const after = trailing.map(() => coveredSlot(COVERED_ABOVE)).join("");
  if (state.covered.size > 0 && !state.single) throw new XhtmlError("table-shape", offset);
  const hole = state.last + 1 !== state.covered.size;
  state.widths.push(hole ? -1 : state.covered.size);
  const above = new Map<number, number>();
  for (const [column, rows] of state.above) if (rows > 1) above.set(column, rows - 1);
  for (const [column, rows] of state.down) above.set(column, rows);
  state.above = above;
  return after;
}

// The end of a row group: a cell whose rows run past its last row is clipped by a renderer,
// silently, so it rejects.
function endRowGroup(state: TableState, offset: number): void {
  if (state.above.size > 0) throw new XhtmlError("table-shape", offset);
}

// The end of a table: every row as wide as the first, and in every column a cell that spans no
// columns starts; a renderer draws a column without one at zero width, its cells' text in the
// columns around it.
function endTable(state: TableState, offset: number): void {
  if (state.rows) endRowGroup(state, offset);
  const width = state.widths[0] ?? 0;
  if (state.widths.some((covered) => covered !== width || covered < 0)) {
    throw new XhtmlError("table-shape", offset);
  }
  for (let column = 0; column < width; column += 1) {
    if (!state.singleColumns.has(column)) throw new XhtmlError("table-shape", offset);
  }
}

// A list item's marker as a renderer draws it (CSS counter styles decimal, lower- and
// upper-alpha, lower- and upper-roman), followed by `.` and a space.
const ROMAN: readonly (readonly [number, string])[] = [
  [1000, "m"],
  [900, "cm"],
  [500, "d"],
  [400, "cd"],
  [100, "c"],
  [90, "xc"],
  [50, "l"],
  [40, "xl"],
  [10, "x"],
  [9, "ix"],
  [5, "v"],
  [4, "iv"],
  [1, "i"],
];

export function listMarker(style: string, ordinal: number): string {
  let marker = String(ordinal);
  if ((style === "a" || style === "A") && ordinal >= 1) {
    marker = "";
    let rest = ordinal;
    while (rest > 0) {
      rest -= 1;
      marker = String.fromCodePoint(0x61 + (rest % 26)) + marker;
      rest = Math.floor(rest / 26);
    }
  } else if ((style === "i" || style === "I") && ordinal >= 1 && ordinal <= 3999) {
    marker = "";
    let rest = ordinal;
    for (const [value, letters] of ROMAN) {
      while (rest >= value) {
        marker += letters;
        rest -= value;
      }
    }
  }
  if (style === "A" || style === "I") marker = marker.toUpperCase();
  return `${marker}. `;
}

// One code point of text inside the root, raw or decoded, as the scanner emits it: rejected
// directly inside a table container unless it is raw whitespace, folded or rejected inside
// `sup` and `sub`, and otherwise kept as it is.
function emitText(
  codePoint: number,
  parent: string | undefined,
  output: string[],
  offset: number,
  isReference: boolean,
): void {
  const character = String.fromCodePoint(codePoint);
  // A line feed or carriage return in text is a space to a renderer: only a block boundary or
  // `br` is a line break. Emitting it as U+0020 keeps a bullet after it from reading as a list
  // item (section 3 step 4) and keeps a soft hyphen before it from joining a word (step 1).
  const emitted = codePoint === 0x000a || codePoint === 0x000d ? " " : character;
  if (parent !== undefined && (TABLE_CONTAINERS.has(parent) || LIST_CONTAINERS.has(parent))) {
    if (isReference || !isAsciiWhitespace(character)) {
      throw new XhtmlError(TABLE_CONTAINERS.has(parent) ? "table-content" : "list-content", offset);
    }
    output.push(emitted);
    return;
  }
  if (emitted !== character) {
    output.push(emitted);
    return;
  }
  if (parent === "sup" || parent === "sub") {
    const scripted = scriptCodePoint(parent, codePoint);
    if (scripted === undefined) throw new XhtmlError("unmappable-script", offset);
    output.push(String.fromCodePoint(scripted));
    return;
  }
  output.push(character);
}

// A structural break: U+000A, except that a table cell and everything inside one is on one line
// of U+0009-separated text, as the extractor writes a table row (section 7). A bullet in a cell
// is therefore on a line with U+0009 and is never read as a list item (section 3 step 4).
function structuralBreak(name: string, cellDepth: number): string {
  return name === "td" || name === "th" || cellDepth > 0 ? "\t" : "\n";
}

// Converts a FHIR narrative `div` to text. Block boundaries become U+000A; inline markup is
// dropped except that `sup` and `sub` fold their digits and signs; the result still needs
// `normalizeText()` before comparison.
export function xhtmlToText(div: string): string {
  // Section 2 applies to the div as received, markup and attribute values included, before the
  // scan: an unpaired surrogate split by markup would otherwise be joined in the output.
  const forbidden = findForbiddenCharacter(div);
  if (forbidden !== undefined) throw new XhtmlError("forbidden-character", forbidden);
  // The code points the scanner emits for grids and pictures never occur in the narrative itself.
  const reserved = findReservedCharacter(div);
  if (reserved !== undefined) throw new XhtmlError("reserved-character", reserved);
  const invisible = findInvisibleBreak(div);
  if (invisible !== undefined) throw new XhtmlError("invisible-character", invisible);
  try {
    return scan(div);
  } catch (error) {
    if (error instanceof XhtmlError) {
      throw new XhtmlError(error.code, codePointOffset(div, error.offset));
    }
    throw error;
  }
}

// The number of code points before a UTF-16 index of the text.
function codePointOffset(text: string, index: number): number {
  let offset = 0;
  for (let unit = 0; unit < index; unit += (text.codePointAt(unit) ?? 0) > 0xffff ? 2 : 1) {
    offset += 1;
  }
  return offset;
}

// The scan of a div section 2 has accepted. It reads the div by UTF-16 index, and an error it
// throws carries that index, which `xhtmlToText` reports as a code point offset.
function scan(div: string): string {
  const output: string[] = [];
  // The output positions (array indexes) where an inline tag splits the text, and the index in
  // the div of each tag.
  const splits: number[] = [];
  const splitTags: number[] = [];
  // Each `sub` holding ½, checked after the scan; and the one open now.
  const halves: LoweredHalf[] = [];
  let lowered: LoweredHalf | undefined;
  // The output positions of code points emitted inside `sup` or `sub`.
  const scriptPieces = new Set<number>();
  const stack: string[] = [];
  const tables: TableState[] = [];
  const lists: ListState[] = [];
  const grid = { slots: 0 };
  let rootSeen = false;
  let rootClosed = false;
  let cellDepth = 0;
  let index = 0;

  while (index < div.length) {
    const character = div[index] ?? "";

    if (character === "<") {
      if (div.startsWith("<!--", index)) throw new XhtmlError("comment", index);
      if (div.startsWith("<![CDATA[", index)) throw new XhtmlError("cdata", index);
      if (div.startsWith("<!", index)) throw new XhtmlError("doctype", index);
      if (div.startsWith("<?", index)) throw new XhtmlError("processing-instruction", index);

      if (div.startsWith("</", index)) {
        END_TAG.lastIndex = index;
        const end = END_TAG.exec(div);
        if (end === null) throw new XhtmlError("malformed-tag", index);
        const name = end[1] ?? "";
        if (name !== name.toLowerCase()) throw new XhtmlError("uppercase-element", index);
        const open = stack.pop();
        if (open === undefined) throw new XhtmlError("unbalanced-tag", index);
        if (open !== name) throw new XhtmlError("misnested-tag", index);
        if (SPLITTING_INLINE.has(name)) {
          splits.push(output.length);
          splitTags.push(index);
        }
        if (name === "sub" && lowered !== undefined) {
          if (lowered.offset >= 0) halves.push({ ...lowered, end: output.length });
          lowered = undefined;
        }
        const table = tables[tables.length - 1];
        if (table !== undefined && ROW_GROUPS.has(name)) endRowGroup(table, index);
        if (name === "table" && table !== undefined) {
          endTable(table, index);
          tables.pop();
          // On a line of its own, so an empty table's two markers are two tokens.
          output.push("\n", TABLE_END);
        }
        if (name === "tr" && table !== undefined) output.push(endRow(table, index));
        if (name === "ol" || name === "ul") lists.pop();
        if (name === "td" || name === "th") cellDepth -= 1;
        if (BLOCK_ELEMENTS.has(name)) output.push(structuralBreak(name, cellDepth));
        if ((name === "td" || name === "th") && table !== undefined) {
          output.push(coveredSlot(COVERED_LEFT).repeat(table.openSpan - 1));
        }
        if (stack.length === 0) rootClosed = true;
        index = END_TAG.lastIndex;
        continue;
      }

      START_TAG.lastIndex = index;
      const start = START_TAG.exec(div);
      if (start === null) {
        const next = div[index + 1] ?? "";
        throw new XhtmlError(/[A-Za-z]/.test(next) ? "malformed-tag" : "stray-lt", index);
      }
      const name = start[1] ?? "";
      if (name !== name.toLowerCase()) throw new XhtmlError("uppercase-element", index);
      if (!BLOCK_ELEMENTS.has(name) && !INLINE_ELEMENTS.has(name) && name !== "br") {
        throw new XhtmlError("unknown-element", index);
      }
      const isRoot = stack.length === 0;
      if (isRoot) {
        if (rootSeen || rootClosed) throw new XhtmlError("multiple-roots", index);
        if (name !== "div") throw new XhtmlError("root-not-div", index);
        rootSeen = true;
      }
      const attributes = checkAttributes(name, start[2] ?? "", isRoot, index);
      const selfClosing = (start[3] ?? "") === "/";
      if (VOID_ELEMENTS.has(name) !== selfClosing) throw new XhtmlError("void-element", index);
      if (!isRoot) checkNesting(name, stack, index);
      const parent = stack[stack.length - 1];
      checkParent(name, parent, index);
      // A rule in a cell or a caption is as narrow as its column, so a renderer draws "1", the
      // rule and "2" as a stacked fraction, ½, where the text says "1 2".
      if (name === "hr" && stack.some((open) => RULE_BREAKS_FRACTION.has(open))) {
        throw new XhtmlError("table-content", index);
      }
      if (SPLITTING_INLINE.has(name)) {
        splits.push(output.length);
        splitTags.push(index);
      }
      const table = tables[tables.length - 1];
      enterTableStructure(name, parent, table, index);
      let slotsBefore = "";
      if ((name === "td" || name === "th") && table !== undefined) {
        slotsBefore = placeCell(
          table,
          Number(attributes.get("colspan") ?? "1"),
          Number(attributes.get("rowspan") ?? "1"),
          grid,
          index,
        );
      }

      const lineBreak = structuralBreak(name, cellDepth);
      output.push(slotsBefore);
      if (BLOCK_ELEMENTS.has(name) || name === "br") output.push(lineBreak);
      // What a renderer draws for the element itself: the grid markers of a table, a row and a
      // cell, a numbered item's marker, and a picture.
      if (name === "table") output.push(TABLE_START);
      if (name === "tr" && table !== undefined) {
        startRow(table);
        output.push(ROW_START);
      }
      if (name === "td" || name === "th") output.push(CELL_START, "\t");
      const list = lists[lists.length - 1];
      if (name === "li" && parent === "ol" && list !== undefined) {
        output.push(listMarker(list.style, list.next));
        list.next += 1;
      }
      // U+FFFC, the hash, U+FFFC: closed, so a combining mark after the picture cannot compose
      // with its last digit.
      if (name === "img") output.push(PICTURE, sha256Utf8(attributes.get("src") ?? ""), PICTURE);
      // A self-closing element is `br`, `hr` or `img`; `hr`, a block, also emits its closing break.
      if (selfClosing) {
        if (BLOCK_ELEMENTS.has(name)) output.push(lineBreak);
      } else {
        stack.push(name);
        if (name === "sub") lowered = { start: output.length, end: output.length, offset: -1 };
        if (name === "td" || name === "th") cellDepth += 1;
        if (name === "table") tables.push(newTable());
        if (name === "ol" || name === "ul") {
          lists.push({
            style: attributes.get("type") ?? "1",
            next: Number(attributes.get("start") ?? "1"),
          });
        }
      }
      index = START_TAG.lastIndex;
      continue;
    }

    if (character === "&") {
      ENTITY.lastIndex = index;
      const entity = ENTITY.exec(div);
      if (entity === null) throw new XhtmlError("stray-amp", index);
      if (stack.length === 0) throw new XhtmlError("text-outside-root", index);
      const codePoint = decodeEntity(entity, index);
      const parent = stack[stack.length - 1];
      emitText(codePoint, parent, output, index, true);
      if (parent === "sup" || parent === "sub") scriptPieces.add(output.length - 1);
      if (codePoint === HALF && lowered !== undefined && lowered.offset < 0) lowered.offset = index;
      index = ENTITY.lastIndex;
      continue;
    }

    // Raw text, one code point at a time (section 2 has already rejected unpaired surrogates).
    const codePoint = div.codePointAt(index) ?? 0;
    const point = String.fromCodePoint(codePoint);
    if (stack.length === 0) {
      if (!isAsciiWhitespace(point)) throw new XhtmlError("text-outside-root", index);
    } else {
      const parent = stack[stack.length - 1] ?? "";
      // "]]>" ends a CDATA section to an XML parser, which refuses the document: an XML renderer
      // draws none of the narrative (text directly in a table or list part is refused first).
      if (
        div.startsWith("]]>", index) &&
        !TABLE_CONTAINERS.has(parent) &&
        !LIST_CONTAINERS.has(parent)
      ) {
        throw new XhtmlError("cdata", index);
      }
      emitText(codePoint, parent, output, index, false);
      if (parent === "sup" || parent === "sub") scriptPieces.add(output.length - 1);
      if (codePoint === HALF && lowered !== undefined && lowered.offset < 0) lowered.offset = index;
    }
    index += point.length;
  }

  if (!rootSeen) throw new XhtmlError("root-not-div", 0);
  if (stack.length > 0) throw new XhtmlError("unbalanced-tag", div.length);
  const text = output.join("");
  // A combining mark after an inline tag is drawn in its own run, apart from the letter before
  // the tag, while NFC would join them ("<" and U+0338 across `b` is drawn "</", read "≮").
  if (halves.length > 0) checkLoweredHalves(output, halves, scriptPieces);
  checkComposition(text, splits.map(pointOffsets(output)), splitTags);
  return text;
}
