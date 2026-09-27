import type { Page } from "./page.js";

// What the judge reads of a drawn section for R3 (docs/design/authority-import-renderer.md, and
// its model addendum, M4): every element of the div in pre-order with the computed values R3
// compares, its `::marker`'s where it is a list item, and each list marker's text from the
// accessibility tree. Read in the judge's isolated world; nothing on the page is changed.

export const STYLE_PROPERTIES = [
  "font-size",
  "font-weight",
  "font-style",
  "color",
  "line-height",
  "-webkit-text-decorations-in-effect",
  "background-color",
  "text-indent",
  "margin-top",
  "margin-right",
  "margin-bottom",
  "margin-left",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "border-top-width",
  "border-top-style",
  "border-top-color",
  "border-right-width",
  "border-right-style",
  "border-right-color",
  "border-bottom-width",
  "border-bottom-style",
  "border-bottom-color",
  "border-left-width",
  "border-left-style",
  "border-left-color",
  "border-collapse",
  "border-spacing",
  "display",
  "position",
  "top",
  "bottom",
  "vertical-align",
] as const;

export const MARKER_PROPERTIES = [
  "font-size",
  "font-weight",
  "font-style",
  "color",
  "line-height",
  "-webkit-text-decorations-in-effect",
] as const;

export type ChromeElement = {
  name: string;
  // The pre-order index of its parent element within the div, -1 for the div itself.
  parent: number;
  style: Record<string, string>;
  marker?: Record<string, string>;
};

// The page-side expression: the div is the body's only element child (R2's page).
const READ_ELEMENTS = `(() => {
  const properties = ${JSON.stringify(STYLE_PROPERTIES)};
  const markerProperties = ${JSON.stringify(MARKER_PROPERTIES)};
  const root = document.body.firstElementChild;
  const out = [];
  const visit = (element, parent) => {
    const computed = getComputedStyle(element);
    const style = {};
    for (const property of properties) style[property] = computed.getPropertyValue(property);
    const record = { name: element.localName, parent, style };
    if (computed.getPropertyValue("display") === "list-item") {
      const marker = getComputedStyle(element, "::marker");
      record.marker = {};
      for (const property of markerProperties) record.marker[property] = marker.getPropertyValue(property);
    }
    const index = out.length;
    out.push(record);
    for (const child of element.children) visit(child, index);
  };
  if (root !== null) visit(root, -1);
  return out;
})()`;

export function readElements(page: Page): Promise<ChromeElement[]> {
  return page.evaluate<ChromeElement[]>(READ_ELEMENTS);
}

type DomNode = {
  nodeId: number;
  nodeType: number;
  localName: string;
  backendNodeId: number;
  children?: DomNode[];
};
type AxNode = {
  role?: { value?: string };
  name?: { value?: string };
  parentId?: string;
  nodeId: string;
  backendDOMNodeId?: number;
};

// Each list item's marker text, by the item's pre-order index within the div: the name of the
// `ListMarker` node the accessibility tree gives it.
export async function readMarkers(page: Page): Promise<Map<number, string>> {
  const { root } = (await page.send("DOM.getDocument", { depth: -1 })) as { root: DomNode };
  const find = (node: DomNode): DomNode | undefined => {
    if (node.nodeType === 1 && node.localName === "body") return node;
    for (const child of node.children ?? []) {
      const found = find(child);
      if (found !== undefined) return found;
    }
    return undefined;
  };
  const body = find(root);
  const div = body?.children?.find((child) => child.nodeType === 1);
  const order = new Map<number, number>();
  const visit = (node: DomNode): void => {
    order.set(node.backendNodeId, order.size);
    for (const child of node.children ?? []) if (child.nodeType === 1) visit(child);
  };
  if (div !== undefined) visit(div);
  const { nodes } = (await page.send("Accessibility.getFullAXTree")) as { nodes: AxNode[] };
  const byId = new Map(nodes.map((node) => [node.nodeId, node]));
  const markers = new Map<number, string>();
  for (const node of nodes) {
    if (node.role?.value !== "ListMarker" || node.parentId === undefined) continue;
    const item = byId.get(node.parentId)?.backendDOMNodeId;
    const index = item === undefined ? undefined : order.get(item);
    if (index !== undefined) markers.set(index, node.name?.value ?? "");
  }
  return markers;
}

// `data` is the node's text as the DOM holds it, for the judge's own reading of which nodes are
// whitespace between table parts and list items, and of the waived signs.
export type ChromeText = { parent: number; length: number; data: string };

// Every text node of the div in pre-order, with its parent element's pre-order index and its
// length in code points: M1's index space, as the XML-mode DOM holds it.
const READ_TEXTS = `(() => {
  const root = document.body.firstElementChild;
  const elements = [];
  const texts = [];
  const visit = (node) => {
    if (node.nodeType === 1) {
      const index = elements.length;
      elements.push(node);
      for (const child of node.childNodes) {
        if (child.nodeType === 3) {
          texts.push({ parent: index, length: [...child.data].length, data: child.data });
        }
        else visit(child);
      }
    }
  };
  if (root !== null) visit(root);
  return texts;
})()`;

export function readTexts(page: Page): Promise<ChromeText[]> {
  return page.evaluate<ChromeText[]>(READ_TEXTS);
}

// A text node, for R3's font checks: its element's pre-order index within the div and that
// element's computed family, weight, style and size, its data, and the faces Chrome reports
// drawing it in. CSS.getPlatformFontsForNode is asked of the text node itself: asked of an
// element it reports its descendants' faces too (measured in the image: a paragraph holding an
// italic word reported both faces).
export type ChromeRun = {
  element: number;
  family: string;
  weight: number;
  style: string;
  size: string;
  text: string;
  drawnIn: string[];
};

const READ_RUNS = `(() => {
  const root = document.body.firstElementChild;
  const out = [];
  let index = 0;
  const visit = (element) => {
    const own = index;
    index += 1;
    const c = getComputedStyle(element);
    for (const child of element.childNodes) {
      // DevTools' DOM tree leaves out a text node of whitespace alone, which draws no face.
      if (child.nodeType === 3 && !/^[\\t\\n\\v\\f\\r ]*$/.test(child.data)) {
        out.push({ element: own, family: c.fontFamily, weight: Number(c.fontWeight), style: c.fontStyle, size: c.fontSize, text: child.data });
      }
    }
    for (const child of element.children) visit(child);
  };
  if (root !== null) visit(root);
  return out;
})()`;

// Each text node of the div, with the faces it is drawn in; `withFaces` false leaves `drawnIn`
// empty (the faces do not depend on the width or ratio).
export async function readRuns(page: Page, withFaces = true): Promise<ChromeRun[]> {
  const runs = await page.evaluate<Omit<ChromeRun, "drawnIn">[]>(READ_RUNS);
  if (!withFaces) return runs.map((run) => ({ ...run, drawnIn: [] }));
  await page.send("DOM.enable");
  await page.send("CSS.enable");
  const { root } = (await page.send("DOM.getDocument", { depth: -1 })) as { root: DomNode };
  const find = (node: DomNode): DomNode | undefined => {
    if (node.nodeType === 1 && node.localName === "body") return node;
    for (const child of node.children ?? []) {
      const found = find(child);
      if (found !== undefined) return found;
    }
    return undefined;
  };
  const div = find(root)?.children?.find((child) => child.nodeType === 1);
  // The text nodes in the page-side walk's order: each element's own text children, then its
  // element children in turn.
  const textIds: number[] = [];
  const visit = (node: DomNode): void => {
    for (const child of node.children ?? []) if (child.nodeType === 3) textIds.push(child.nodeId);
    for (const child of node.children ?? []) if (child.nodeType === 1) visit(child);
  };
  if (div !== undefined) visit(div);
  if (textIds.length !== runs.length) {
    throw new Error(`${runs.length} text nodes read, ${textIds.length} in the DOM tree`);
  }
  const found: ChromeRun[] = [];
  for (const [at, run] of runs.entries()) {
    const { fonts } = (await page.send("CSS.getPlatformFontsForNode", {
      nodeId: textIds[at],
    })) as { fonts: { postScriptName: string }[] };
    found.push({ ...run, drawnIn: fonts.map(({ postScriptName }) => postScriptName) });
  }
  return found;
}

// The section's page as R2 requires it: no parser error (XML mode), and a div with no padding or
// border of its own whose content box is the width drawn.
export type ChromePage = {
  parserError: boolean;
  divPadding: string;
  divBorder: string;
  divWidth: number;
};

const READ_PAGE = `(() => {
  const root = document.body.firstElementChild;
  const parserError = document.getElementsByTagNameNS("*", "parsererror").length > 0;
  if (root === null) return { parserError, divPadding: "", divBorder: "", divWidth: -1 };
  const c = getComputedStyle(root);
  const sides = ["top", "right", "bottom", "left"];
  return {
    parserError,
    divPadding: sides.map((s) => c.getPropertyValue("padding-" + s)).join(" "),
    divBorder: sides.map((s) => c.getPropertyValue("border-" + s + "-width")).join(" "),
    divWidth: root.getBoundingClientRect().width,
  };
})()`;

export function readPage(page: Page): Promise<ChromePage> {
  return page.evaluate<ChromePage>(READ_PAGE);
}

// Every drawn character's box height, by the element it stands in (R3: each must equal the box
// of its face at its size and ratio). Characters with no box (collapsed whitespace) are skipped.
export type ChromeHeights = { element: number; heights: number[] }[];

const READ_HEIGHTS = `(() => {
  const root = document.body.firstElementChild;
  const out = [];
  let index = 0;
  const visit = (element) => {
    const own = index;
    index += 1;
    const heights = [];
    for (const child of element.childNodes) {
      if (child.nodeType !== 3) continue;
      const data = child.data;
      for (let at = 0; at < data.length; ) {
        const size = data.codePointAt(at) > 0xffff ? 2 : 1;
        const range = new Range();
        range.setStart(child, at);
        range.setEnd(child, at + size);
        const rects = range.getClientRects();
        if (rects.length > 0 && !/^[\\t\\n\\v\\f\\r ]$/.test(data.slice(at, at + size))) heights.push(rects[0].height);
        at += size;
      }
    }
    if (heights.length > 0) out.push({ element: own, heights });
    for (const child of element.children) visit(child);
  };
  if (root !== null) visit(root);
  return out;
})()`;

export function readHeights(page: Page): Promise<ChromeHeights> {
  return page.evaluate<ChromeHeights>(READ_HEIGHTS);
}
