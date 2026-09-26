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

export type ChromeText = { parent: number; length: number };

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
        if (child.nodeType === 3) texts.push({ parent: index, length: [...child.data].length });
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
