import { NAMED_ENTITIES, XHTML_TOKENS } from "../../fidelity/xhtml.js";

// T's reading of an authority's div (docs/design/authority-import-t.md, T1): the scanner's own
// tokens, as a tree that remembers where each tag stands in the div, so that T's edits (delete an
// attribute, unwrap an element, rename a tag) are made on the div string and nothing is
// re-serialised. The fidelity scanner reads T(div) afterwards and refuses what it refuses; this
// reader refuses only what it cannot place in a tree (reason `markup`).

export class MarkupRefusal extends Error {
  public constructor(public readonly reason: string) {
    super(reason);
    this.name = "MarkupRefusal";
  }
}

export type Span = { start: number; end: number };

export type Attribute = { name: string; value: string; span: Span };

export type TextNode = {
  kind: "text";
  span: Span;
  // The code points the scanner emits for it: references decoded, a raw CR or LF as a space.
  points: string[];
  parent: ElementNode;
};

export type ElementNode = {
  kind: "element";
  name: string;
  attributes: Attribute[];
  startTag: Span;
  // The end tag; undefined for a self-closing element.
  endTag: Span | undefined;
  children: TreeNode[];
  parent: ElementNode | undefined;
};

export type TreeNode = ElementNode | TextNode;

// T's "One tree" (T1): rules on the authority's own tags under which the HTML parser's tree and
// the markup's nesting agree, so T's model judges text in the style a browser draws it in.

// Every element HTML's "close a p element" applies to at its start tag (the HTML standard's tree
// construction, "in body"), T's blocks among them.
const CLOSES_P = new Set([
  "address",
  "article",
  "aside",
  "blockquote",
  "center",
  "details",
  "dialog",
  "dir",
  "div",
  "dl",
  "fieldset",
  "figcaption",
  "figure",
  "footer",
  "form",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "header",
  "hgroup",
  "hr",
  "li",
  "dd",
  "dt",
  "listing",
  "main",
  "menu",
  "nav",
  "ol",
  "p",
  "plaintext",
  "pre",
  "search",
  "section",
  "summary",
  "table",
  "ul",
  "xmp",
]);
// The elements that end HTML's button scope for `p`.
const BUTTON_SCOPE = new Set([
  "table",
  "td",
  "th",
  "caption",
  "html",
  "marquee",
  "object",
  "applet",
  "template",
  "button",
]);
const VOID = new Set(["br", "hr", "img"]);
const TABLE_CONTAINERS = new Set(["table", "thead", "tbody", "tfoot", "tr"]);
const TABLE_CHILDREN: ReadonlyMap<string, ReadonlySet<string>> = new Map([
  ["table", new Set(["caption", "thead", "tbody", "tfoot", "tr"])],
  ["thead", new Set(["tr"])],
  ["tbody", new Set(["tr"])],
  ["tfoot", new Set(["tr"])],
  ["tr", new Set(["td", "th"])],
]);
const LIST_CONTAINERS = new Set(["ol", "ul"]);

function openWithinButtonScope(stack: readonly ElementNode[], name: string): boolean {
  for (let position = stack.length - 1; position >= 0; position -= 1) {
    const open = stack[position]?.name ?? "";
    if (open === name) return true;
    if (BUTTON_SCOPE.has(open)) return false;
  }
  return false;
}

function checkOneTree(name: string, selfClosing: boolean, stack: readonly ElementNode[]): void {
  if (VOID.has(name) !== selfClosing) throw new MarkupRefusal("markup");
  if (CLOSES_P.has(name) && openWithinButtonScope(stack, "p")) throw new MarkupRefusal("markup");
  if (
    (name === "dd" || name === "dt") &&
    stack.some((open) => open.name === "dd" || open.name === "dt")
  ) {
    throw new MarkupRefusal("markup");
  }
  if (name === "a" && stack.some((open) => open.name === "a")) throw new MarkupRefusal("markup");
  const parent = stack.at(-1)?.name;
  if (parent !== undefined) {
    const allowed = TABLE_CHILDREN.get(parent);
    if (allowed !== undefined && !allowed.has(name)) throw new MarkupRefusal("markup");
    if (LIST_CONTAINERS.has(parent) && name !== "li") throw new MarkupRefusal("markup");
  }
}

const LOWER_NAME = /^[a-z][a-z0-9]*$/u;
// The scanner's own bound on nesting (fidelity §5, `nesting-depth`).
const MAX_DEPTH = 32;
// T's bound on a section's elements, so its records of them fit the worker's memory: ten times
// the most any pinned label's section holds (1 965, the Imatinib Teva tablets' 5.1).
export const MAX_ELEMENTS = 20_000;
const LOWER_ATTRIBUTE = /^[a-z_:][-a-z0-9_:.]*$/u;

const END_TAG = new RegExp(XHTML_TOKENS.endTag, "y");
const START_TAG = new RegExp(XHTML_TOKENS.startTag, "y");
const ATTRIBUTE = new RegExp(XHTML_TOKENS.attribute, "g");
const ENTITY = new RegExp(XHTML_TOKENS.entity, "y");

function decodeReference(match: RegExpExecArray): string {
  const named = match[1];
  if (named !== undefined) {
    const decoded = NAMED_ENTITIES.get(named);
    if (decoded === undefined) throw new MarkupRefusal("markup");
    return decoded;
  }
  const codePoint =
    match[2] !== undefined ? Number.parseInt(match[2], 10) : Number.parseInt(match[3] ?? "", 16);
  if (!Number.isFinite(codePoint) || codePoint > 0x10ffff) throw new MarkupRefusal("markup");
  if (codePoint >= 0xd800 && codePoint <= 0xdfff) throw new MarkupRefusal("markup");
  return String.fromCodePoint(codePoint);
}

// An attribute value with its references decoded (T1: `&` left after decoding refuses in a style).
export function decodeAttributeValue(raw: string): string {
  let result = "";
  let index = 0;
  while (index < raw.length) {
    const character = raw[index] ?? "";
    if (character === "&") {
      ENTITY.lastIndex = index;
      const match = ENTITY.exec(raw);
      if (match === null) throw new MarkupRefusal("markup");
      result += decodeReference(match);
      index = ENTITY.lastIndex;
      continue;
    }
    result += character;
    index += 1;
  }
  return result;
}

function attributes(source: string, offset: number): Attribute[] {
  const result: Attribute[] = [];
  const seen = new Set<string>();
  ATTRIBUTE.lastIndex = 0;
  for (const match of source.matchAll(ATTRIBUTE)) {
    const name = match[1] ?? "";
    if (!LOWER_ATTRIBUTE.test(name) || seen.has(name)) throw new MarkupRefusal("markup");
    seen.add(name);
    const start = offset + match.index;
    result.push({
      name,
      value: decodeAttributeValue(match[2] ?? match[3] ?? ""),
      span: { start, end: start + match[0].length },
    });
  }
  return result;
}

// Reads a section's div into a tree whose root is the div element. The div is the whole string,
// whitespace around the root aside.
export function readTree(div: string): ElementNode {
  const stack: ElementNode[] = [];
  let root: ElementNode | undefined;
  let elements = 0;
  let text: TextNode | undefined;
  let index = 0;

  const closeText = (): void => {
    text = undefined;
  };

  while (index < div.length) {
    const character = div[index] ?? "";
    if (character === "<") {
      closeText();
      if (div.startsWith("</", index)) {
        END_TAG.lastIndex = index;
        const end = END_TAG.exec(div);
        const open = stack.pop();
        if (end === null || open?.name !== (end[1] ?? "")) {
          throw new MarkupRefusal("markup");
        }
        open.endTag = { start: index, end: END_TAG.lastIndex };
        index = END_TAG.lastIndex;
        continue;
      }
      START_TAG.lastIndex = index;
      const start = START_TAG.exec(div);
      if (start === null) throw new MarkupRefusal("markup");
      const name = start[1] ?? "";
      if (!LOWER_NAME.test(name)) throw new MarkupRefusal("markup");
      const selfClosing = (start[3] ?? "") === "/";
      checkOneTree(name, selfClosing, stack);
      const attributeSource = start[2] ?? "";
      const attributeOffset = index + 1 + (start[1] ?? "").length;
      const parent = stack.at(-1);
      if (parent === undefined && root !== undefined) throw new MarkupRefusal("markup");
      elements += 1;
      if (elements > MAX_ELEMENTS) throw new MarkupRefusal("markup");
      const element: ElementNode = {
        kind: "element",
        name,
        attributes: attributes(attributeSource, attributeOffset),
        startTag: { start: index, end: START_TAG.lastIndex },
        endTag: undefined,
        children: [],
        parent,
      };
      if (parent === undefined) root = element;
      else parent.children.push(element);
      if (!selfClosing) {
        stack.push(element);
        if (stack.length > MAX_DEPTH) throw new MarkupRefusal("markup");
      }
      index = START_TAG.lastIndex;
      continue;
    }
    const parent = stack.at(-1);
    let point: string;
    let next: number;
    if (character === "&") {
      ENTITY.lastIndex = index;
      const match = ENTITY.exec(div);
      if (match === null) throw new MarkupRefusal("markup");
      point = decodeReference(match);
      next = ENTITY.lastIndex;
      // A line feed or carriage return, raw or by reference, is emitted as a space (fidelity §5).
      if (point === "\r" || point === "\n") point = " ";
    } else {
      const codePoint = div.codePointAt(index) ?? 0;
      point = String.fromCodePoint(codePoint);
      next = index + point.length;
      if (point === "\r" || point === "\n") point = " ";
    }
    const whitespace = /^[\t\n\r ]$/u.test(point) && character !== "&";
    if (
      parent === undefined ||
      TABLE_CONTAINERS.has(parent.name) ||
      LIST_CONTAINERS.has(parent.name)
    ) {
      // Only raw ASCII whitespace may stand outside the root or directly in a table or list
      // container (the parser moves anything else out of a table).
      if (!whitespace) throw new MarkupRefusal("markup");
      index = next;
      continue;
    }
    if (text?.parent !== parent) {
      text = { kind: "text", span: { start: index, end: next }, points: [], parent };
      parent.children.push(text);
    }
    text.points.push(point);
    text.span.end = next;
    index = next;
  }
  if (root === undefined || stack.length > 0) throw new MarkupRefusal("markup");
  return root;
}

// Every node under `node` in document order, `node` excepted.
export function* descendants(node: ElementNode): Generator<TreeNode> {
  for (const child of node.children) {
    yield child;
    if (child.kind === "element") yield* descendants(child);
  }
}
