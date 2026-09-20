// Fail-closed XHTML narrative scanner (docs/fidelity-normalization.md section 5). It is not a
// general HTML parser: it accepts only the closed lists below so that no element or attribute
// can hide, carry, or reorder narrative text, and it rejects everything else.

export type XhtmlErrorCode =
  | "root-not-div"
  | "multiple-roots"
  | "text-outside-root"
  | "unknown-element"
  | "uppercase-element"
  | "forbidden-attribute"
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
  | "table-section-order";

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
  "pre",
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

type TableState = { head: boolean; body: boolean; foot: boolean };

const NAMED_ENTITIES = new Map<string, string>([
  ["amp", "&"],
  ["lt", "<"],
  ["gt", ">"],
  ["quot", '"'],
  ["apos", "'"],
]);

const END_TAG = /<\/([A-Za-z][A-Za-z0-9]*)\s*>/y;
const START_TAG =
  /<([A-Za-z][A-Za-z0-9]*)((?:\s+[A-Za-z_:][-A-Za-z0-9_:.]*\s*=\s*(?:"[^"<]*"|'[^'<]*'))*)\s*(\/?)>/y;
const ATTRIBUTE = /([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"<]*)"|'([^'<]*)')/g;
const ENTITY = /&(?:([A-Za-z]+)|#(\d{1,7})|#x([0-9A-Fa-f]{1,6}));/y;

function isAsciiWhitespace(character: string): boolean {
  return character === " " || character === "\t" || character === "\n" || character === "\r";
}

// Attribute values are never compared against the source, so they must not be able to carry
// text: each allowed attribute is restricted to a short token alphabet or a safe link form.
const TOKEN_VALUE = /^[A-Za-z0-9_.:-]{1,32}$/;
const TOKEN_LIST_VALUE = /^[A-Za-z0-9_.:-]{1,32}(?: [A-Za-z0-9_.:-]{1,32}){0,2}$/;
const SPAN_VALUE = /^[1-9][0-9]{0,2}$/;
const HREF_VALUE =
  /^(?:https:\/\/[A-Za-z0-9.-]{1,64}(?:\/[A-Za-z0-9._~-]{0,32}){0,8}\/?|#[A-Za-z0-9_.:-]{1,32})$/;

const ATTRIBUTE_VALUE_RULES = new Map<string, RegExp>([
  ["xml:lang", TOKEN_VALUE],
  ["lang", TOKEN_VALUE],
  ["id", TOKEN_VALUE],
  ["class", TOKEN_LIST_VALUE],
  ["colspan", SPAN_VALUE],
  ["rowspan", SPAN_VALUE],
  ["scope", TOKEN_VALUE],
  ["href", HREF_VALUE],
]);

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
    if (name === "href" && element !== "a") throw new XhtmlError("forbidden-attribute", offset);
    if (!ATTRIBUTE_VALUE_RULES.get(name)?.test(value)) {
      throw new XhtmlError("forbidden-attribute", offset);
    }
  }
  if (isRoot && !sawNamespace) throw new XhtmlError("root-not-div", offset);
}

function decodeEntity(match: RegExpExecArray, offset: number): string {
  const named = match[1];
  if (named !== undefined) {
    const decoded = NAMED_ENTITIES.get(named);
    if (decoded === undefined) throw new XhtmlError("unknown-entity", offset);
    return decoded;
  }
  const decimal = match[2];
  const hex = match[3];
  const codePoint =
    decimal !== undefined ? Number.parseInt(decimal, 10) : Number.parseInt(hex ?? "", 16);
  if (!Number.isFinite(codePoint) || codePoint > 0x10ffff) {
    throw new XhtmlError("unknown-entity", offset);
  }
  return String.fromCodePoint(codePoint);
}

// Table sections render in a fixed order (head, body, foot) regardless of document order, so
// only that document order is accepted; anything else would display rows in a different order
// from the source the text was verified against.
function enterTableSection(
  name: string,
  stack: string[],
  tables: TableState[],
  offset: number,
): void {
  if (name !== "thead" && name !== "tbody" && name !== "tfoot") return;
  const state = tables[tables.length - 1];
  if (stack[stack.length - 1] !== "table" || state === undefined) {
    throw new XhtmlError("misnested-tag", offset);
  }
  if (name === "thead" && (state.head || state.body || state.foot)) {
    throw new XhtmlError("table-section-order", offset);
  }
  if (name === "tbody" && state.foot) throw new XhtmlError("table-section-order", offset);
  if (name === "tfoot" && state.foot) throw new XhtmlError("table-section-order", offset);
  state[name === "thead" ? "head" : name === "tbody" ? "body" : "foot"] = true;
}

// Converts a FHIR narrative `div` to text. Block boundaries become U+000A; inline markup is
// dropped; the result still needs `normalizeText()` before comparison.
export function xhtmlToText(div: string): string {
  const output: string[] = [];
  const stack: string[] = [];
  const tables: TableState[] = [];
  let rootSeen = false;
  let rootClosed = false;
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
        if (name === "table") tables.pop();
        if (BLOCK_ELEMENTS.has(name) || name === "br") output.push("\n");
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
      enterTableSection(name, stack, tables, index);

      if (BLOCK_ELEMENTS.has(name) || name === "br") output.push("\n");
      if ((start[3] ?? "") === "/") {
        if (BLOCK_ELEMENTS.has(name)) output.push("\n");
        if (isRoot) rootClosed = true;
      } else {
        stack.push(name);
        if (name === "table") tables.push({ head: false, body: false, foot: false });
      }
      index = START_TAG.lastIndex;
      continue;
    }

    if (character === "&") {
      ENTITY.lastIndex = index;
      const entity = ENTITY.exec(div);
      if (entity === null) throw new XhtmlError("stray-amp", index);
      if (stack.length === 0) throw new XhtmlError("text-outside-root", index);
      output.push(decodeEntity(entity, index));
      index = ENTITY.lastIndex;
      continue;
    }

    if (stack.length === 0) {
      if (!isAsciiWhitespace(character)) throw new XhtmlError("text-outside-root", index);
    } else {
      output.push(character);
    }
    index += 1;
  }

  if (!rootSeen) throw new XhtmlError("root-not-div", 0);
  if (stack.length > 0) throw new XhtmlError("unbalanced-tag", div.length);
  return output.join("");
}
