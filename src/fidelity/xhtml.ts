// Fail-closed XHTML narrative scanner (docs/fidelity-normalization.md section 5). It is not a
// general HTML parser: it accepts only the closed lists below so that no element or attribute
// can hide, carry, or reorder narrative text, and it rejects everything else. Where a renderer
// would draw markup differently from the text this scanner emits — a `/` an HTML parser ignores,
// a raised digit, content a table moves, a soft hyphen before a visible line break — the markup
// is either folded into the text or rejected.

import { findForbiddenCharacter, isForbiddenCodePoint } from "./normalize.js";

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
  | "table-section-order"
  | "table-structure"
  | "table-shape"
  | "soft-hyphen-at-boundary";

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
  "u",
  "em",
  "strong",
  "sup",
  "sub",
  "small",
  "a",
  "abbr",
  "cite",
  "code",
]);

// Elements whose renderer-generated characters (list numbers, quotation marks) would show text
// the source does not contain are excluded above: `ol` and `q`.

// The only elements that may be, and must be, self-closing. An HTML parser ignores the `/` of
// `<sup/>`, so any other element written that way opens around the text that follows it; and a
// `<br>` without `/` is a start tag whose content an XML renderer does not draw.
const VOID_ELEMENTS = new Set(["br", "hr"]);

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

type TableState = {
  caption: boolean;
  head: boolean;
  body: boolean;
  foot: boolean;
  rows: boolean;
  // Cell count (`td` and `th`) of every row of this table, in document order.
  widths: number[];
};

const NAMED_ENTITIES = new Map<string, string>([
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

type ScriptRule = {
  folding: ReadonlyMap<number, number>;
  // The element's own script digits and signs, kept as they are.
  own: ReadonlySet<number>;
  // The other script's digits, signs and letters.
  foreign: ReadonlySet<number>;
};

function scriptRule(
  digits: readonly number[],
  signs: ScriptSigns,
  foreign: readonly number[],
): ScriptRule {
  const [plus, minus, equals, open, close] = signs;
  const folding = new Map<number, number>();
  digits.forEach((target, digit) => folding.set(0x0030 + digit, target));
  for (const sign of PLUS_SIGNS) folding.set(sign, plus);
  for (const sign of MINUS_SIGNS) folding.set(sign, minus);
  folding.set(0x003d, equals);
  folding.set(0x0028, open);
  folding.set(0x0029, close);
  return { folding, own: new Set([...digits, ...signs]), foreign: new Set(foreign) };
}

const SCRIPT_RULES = new Map<string, ScriptRule>([
  [
    "sup",
    scriptRule(SUPERSCRIPT_DIGITS, SUPERSCRIPT_SIGNS, [
      ...SUBSCRIPT_DIGITS,
      ...SUBSCRIPT_SIGNS,
      ...SUBSCRIPT_LETTERS,
    ]),
  ],
  [
    "sub",
    scriptRule(SUBSCRIPT_DIGITS, SUBSCRIPT_SIGNS, [
      ...SUPERSCRIPT_DIGITS,
      ...SUPERSCRIPT_SIGNS,
      ...SUPERSCRIPT_LETTERS,
    ]),
  ],
]);

// The element's own script digits and signs are kept; the other script's digits, signs and
// letters, every other number (a non-ASCII digit, a fraction, a numeral), a plus-minus sign, and
// every other mathematical symbol, bracket or dash (general category Sm, Ps, Pe, Pd: `＝`, `﹙`,
// `⸺`) have no script form here and reject.
const UNMAPPABLE_SIGNS = new Set([0x00b1, 0x2213]);
const NUMBER = /^\p{N}$/u;
const SIGN_OR_BRACKET = /^[\p{Sm}\p{Ps}\p{Pe}\p{Pd}]$/u;

// Whitespace inside a tag is U+0009, U+000A, U+000D and U+0020 only, never `\s`: an HTML
// parser reads any other code point (U+00A0, U+3000, U+FEFF) as part of the tag name, so
// `<sup\u00a0>` is an unknown element to a renderer and must not be `sup` here.
const END_TAG = /<\/([A-Za-z][A-Za-z0-9]*)[\t\n\r ]*>/y;
const START_TAG =
  /<([A-Za-z][A-Za-z0-9]*)((?:[\t\n\r ]+[A-Za-z_:][-A-Za-z0-9_:.]*[\t\n\r ]*=[\t\n\r ]*(?:"[^"<]*"|'[^'<]*'))*)[\t\n\r ]*(\/?)>/y;
const ATTRIBUTE = /([A-Za-z_:][-A-Za-z0-9_:.]*)[\t\n\r ]*=[\t\n\r ]*(?:"([^"<]*)"|'([^'<]*)')/g;
const ENTITY = /&(?:([A-Za-z]+)|#(\d{1,7})|#x([0-9A-Fa-f]{1,6}));/y;

function isAsciiWhitespace(character: string): boolean {
  return character === " " || character === "\t" || character === "\n" || character === "\r";
}

// Attribute values are never compared against the source, so they must not be able to carry
// text: each allowed attribute is restricted to a short token alphabet or a safe link form, and
// to the one element that needs it. Nothing a viewer's stylesheet or script could key on to hide
// text (`class`, `id`, a language tag below the root, an in-page link) is allowed.
const TOKEN_VALUE = /^[A-Za-z0-9_.:-]{1,32}$/;
const HREF_VALUE = /^https:\/\/[A-Za-z0-9.-]{1,64}(?:\/[A-Za-z0-9._~-]{0,32}){0,8}\/?$/;

function attributeAllowed(name: string, value: string, element: string, isRoot: boolean): boolean {
  if (name === "xml:lang" || name === "lang") return isRoot && TOKEN_VALUE.test(value);
  if (name === "href") return element === "a" && HREF_VALUE.test(value);
  if (name === "scope") return element === "th" && TOKEN_VALUE.test(value);
  return false;
}

function checkAttributes(
  element: string,
  attributeSource: string,
  isRoot: boolean,
  offset: number,
): void {
  ATTRIBUTE.lastIndex = 0;
  let sawNamespace = false;
  const seen = new Set<string>();
  for (const match of attributeSource.matchAll(ATTRIBUTE)) {
    const name = match[1] ?? "";
    const value = match[2] ?? match[3] ?? "";
    if (seen.has(name)) throw new XhtmlError("forbidden-attribute", offset);
    seen.add(name);
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
  return codePoint;
}

// The parent check at a start tag: nothing but text inside `sup` and `sub`; each table part in
// its own parent; nothing but table parts inside a table container.
function checkParent(name: string, parent: string | undefined, offset: number): void {
  if (parent === "sup" || parent === "sub") throw new XhtmlError("script-content", offset);
  const allowedParents = TABLE_PART_PARENTS.get(name);
  if (allowedParents !== undefined) {
    if (parent === undefined || !allowedParents.has(parent)) {
      throw new XhtmlError("misnested-tag", offset);
    }
    return;
  }
  if (parent !== undefined && TABLE_CONTAINERS.has(parent)) {
    throw new XhtmlError("table-content", offset);
  }
}

// Renderers place table parts by role, not by document position: a caption always renders
// first, sections render head → body → foot, and rows placed directly under `table` are
// wrapped in an implicit body. Only the one document order that renders as written is
// accepted, so displayed text order equals the order the source was verified in. Parents are
// already checked; this also records rows and cells for the shape check at `</table>`.
function enterTableElement(
  name: string,
  parent: string | undefined,
  tables: TableState[],
  offset: number,
): void {
  const state = tables[tables.length - 1];
  if (state === undefined) return;
  if (name === "td" || name === "th") {
    const last = state.widths.length - 1;
    state.widths[last] = (state.widths[last] ?? 0) + 1;
    return;
  }
  if (name === "tr") {
    if (parent === "table") {
      if (state.head || state.body || state.foot) throw new XhtmlError("table-structure", offset);
      state.rows = true;
    }
    state.widths.push(0);
    return;
  }
  if (name !== "caption" && name !== "thead" && name !== "tbody" && name !== "tfoot") return;
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
  if (parent !== undefined && TABLE_CONTAINERS.has(parent)) {
    if (isReference || !isAsciiWhitespace(character)) {
      throw new XhtmlError("table-content", offset);
    }
    output.push(emitted);
    return;
  }
  if (emitted !== character) {
    output.push(emitted);
    return;
  }
  const rule = parent === undefined ? undefined : SCRIPT_RULES.get(parent);
  if (rule !== undefined) {
    const folded = rule.folding.get(codePoint);
    if (folded !== undefined) {
      output.push(String.fromCodePoint(folded));
      return;
    }
    if (
      UNMAPPABLE_SIGNS.has(codePoint) ||
      rule.foreign.has(codePoint) ||
      ((NUMBER.test(character) || SIGN_OR_BRACKET.test(character)) && !rule.own.has(codePoint))
    ) {
      throw new XhtmlError("unmappable-script", offset);
    }
  }
  output.push(character);
}

// U+00AD followed by U+000A in the emitted text: section 3 step 1 would join a word across what
// a renderer draws as a line break. The emitted text has U+000A only from a block boundary or
// `br`, and no U+000D at all (text line breaks are emitted as U+0020).
const SOFT_HYPHEN_BEFORE_BREAK = /\u00ad\n/u;

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

  const output: string[] = [];
  const stack: string[] = [];
  const tables: TableState[] = [];
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
        if (name === "table") {
          const widths = tables.pop()?.widths ?? [];
          if (widths.some((width) => width !== widths[0])) {
            throw new XhtmlError("table-shape", index);
          }
        }
        if (name === "td" || name === "th") cellDepth -= 1;
        if (BLOCK_ELEMENTS.has(name)) output.push(structuralBreak(name, cellDepth));
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
      checkAttributes(name, start[2] ?? "", isRoot, index);
      const selfClosing = (start[3] ?? "") === "/";
      if (VOID_ELEMENTS.has(name) !== selfClosing) throw new XhtmlError("void-element", index);
      const parent = stack[stack.length - 1];
      checkParent(name, parent, index);
      enterTableElement(name, parent, tables, index);

      const lineBreak = structuralBreak(name, cellDepth);
      if (BLOCK_ELEMENTS.has(name) || name === "br") output.push(lineBreak);
      // A self-closing element is `br` or `hr`; `hr`, a block, also emits its closing break.
      if (selfClosing) {
        if (BLOCK_ELEMENTS.has(name)) output.push(lineBreak);
      } else {
        stack.push(name);
        if (name === "td" || name === "th") cellDepth += 1;
        if (name === "table") {
          tables.push({
            caption: false,
            head: false,
            body: false,
            foot: false,
            rows: false,
            widths: [],
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
      emitText(codePoint, stack[stack.length - 1], output, index, true);
      index = ENTITY.lastIndex;
      continue;
    }

    // Raw text, one code point at a time (section 2 has already rejected unpaired surrogates).
    const codePoint = div.codePointAt(index) ?? 0;
    const point = String.fromCodePoint(codePoint);
    if (stack.length === 0) {
      if (!isAsciiWhitespace(point)) throw new XhtmlError("text-outside-root", index);
    } else {
      emitText(codePoint, stack[stack.length - 1], output, index, false);
    }
    index += point.length;
  }

  if (!rootSeen) throw new XhtmlError("root-not-div", 0);
  if (stack.length > 0) throw new XhtmlError("unbalanced-tag", div.length);
  const text = output.join("");
  const softHyphen = SOFT_HYPHEN_BEFORE_BREAK.exec(text);
  if (softHyphen !== null) throw new XhtmlError("soft-hyphen-at-boundary", softHyphen.index);
  return text;
}
