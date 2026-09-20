// Document AI Layout Parser output -> SourceDocumentText, for the extractor spike
// (docs/design/extractor-spike.md, "Adapter contract"). Pure: no I/O, no clock, no logging, and
// no narrative in anything it returns other than the page text itself.
//
// Response shape relied on, as declared in
// node_modules/@google-cloud/documentai/build/protos/protos.d.ts. Line numbers are the v1
// declarations; the v1beta3 copies of the same interfaces start near line 23932 and are unused.
//
//   181   Document.documentLayout      -> Document.IDocumentLayout | null
//   4769  IDocumentLayout.blocks       -> DocumentLayout.IDocumentLayoutBlock[] | null
//   4868  IDocumentLayoutBlock         -> textBlock | tableBlock | listBlock | imageBlock,
//                                         blockId, pageSpan, boundingBox (all optional, nullable)
//   5006  ILayoutPageSpan              -> pageStart, pageEnd (numbers; 1-based page numbers)
//   5109  ILayoutTextBlock             -> text, type, blocks (nested blocks), annotations
//   5224  ILayoutTableBlock            -> headerRows, bodyRows, caption
//   5339  ILayoutTableRow              -> cells
//   5436  ILayoutTableCell             -> blocks, rowSpan, colSpan
//   5545  ILayoutListBlock             -> listEntries, type
//   5648  ILayoutListEntry             -> blocks
//   5745  ILayoutImageBlock            -> blobAssetId, gcsUri, dataUri, mimeType, imageText;
//                                         `imageText` is text the parser read out of the image
//                                         and is rendered into the page like any other text
//   4321  Document.IRevision           -> processor: the processor *version* resource name, the
//                                         only version string a response carries
//   9894  IProcessResponse             -> document
//
// Placement. The block tree is flattened in document order before placement, so that a block
// nested under `ILayoutTextBlock.blocks`, under a `listEntries[].blocks`, or inside a table cell
// is classified and attributed to a page by exactly the same rules as a top-level block: Layout
// Parser nests body blocks under headings, and a running footer can arrive nested under the
// heading that precedes it. One exception, which the row-major table contract forces: a
// body-classified block inside a table cell stays inside its cell (it is that cell's text); only
// a header/footer-classified block is hoisted out of the cell onto the page. A child without a
// `pageSpan` inherits its parent's.
//
// Two caveats, both about values rather than shape. `ILayoutTextBlock.type` is declared as a
// free-form `string` with no enum anywhere in the .d.ts, so the header/footer vocabulary in
// TEXT_BLOCK_ROLES below is a guess about the values a Layout Parser emits and is the one thing
// in this file a live response can refute. And `pageSpan` is optional on every block, so a block
// without one, and without a parent to inherit from, is attributed to the page of the previous
// block and counted.
//
// The adapter changes no character it receives. The only characters it introduces are the
// U+0009 and U+000A separators the contract requires, and every one of them is counted in
// `tabsAdded` / `lineFeedsAdded`.

import type { protos } from "@google-cloud/documentai";

import type { SourceDocumentText, SourcePage } from "../../../src/fidelity/index.js";

type LayoutDocument = protos.google.cloud.documentai.v1.IDocument;
type LayoutBlock = protos.google.cloud.documentai.v1.Document.DocumentLayout.IDocumentLayoutBlock;
type LayoutTableBlock =
  protos.google.cloud.documentai.v1.Document.DocumentLayout.DocumentLayoutBlock.ILayoutTableBlock;
type LayoutTableCell =
  protos.google.cloud.documentai.v1.Document.DocumentLayout.DocumentLayoutBlock.ILayoutTableCell;

const TAB = "\t";
const LF = "\n";

const UNKNOWN_PROCESSOR_VERSION = "unknown-processor-version";

// Values of ILayoutTextBlock.type that place a block outside the body range. Normalised by
// `typeKey` first, so "PAGE_HEADER", "page_header", "page-header" and "Page Header" all land here.
const TEXT_BLOCK_ROLES = new Map<string, "header" | "footer">([
  ["header", "header"],
  ["page-header", "header"],
  ["running-header", "header"],
  ["footer", "footer"],
  ["page-footer", "footer"],
  ["running-footer", "footer"],
  ["page-number", "footer"],
]);

const LIGATURE_RANGE_START = 0xfb00;
const LIGATURE_RANGE_END = 0xfb06;

const WORD_CHARACTER = /[\p{L}\p{N}\p{M}]/u;

// Spec section 2: characters the normalisation rejects outright. Mirrored here (not imported)
// because `findForbiddenCharacter` reports only the first one and the spike needs the count.
function isForbidden(codePoint: number): boolean {
  if (codePoint === 0xfffd || codePoint === 0xfffe || codePoint === 0xffff) return true;
  if (codePoint === 0x007f) return true;
  if (codePoint >= 0xd800 && codePoint <= 0xdfff) return true;
  if (codePoint < 0x0020) {
    return !(
      codePoint === 0x0009 ||
      codePoint === 0x000a ||
      codePoint === 0x000b ||
      codePoint === 0x000c ||
      codePoint === 0x000d
    );
  }
  return false;
}

export type AdapterCounters = {
  pages: number;
  blocks: number;
  nestedBlocks: number;
  nestedHeaderFooterBlocks: number;
  textBlocks: number;
  tableBlocks: number;
  listBlocks: number;
  imageBlocks: number;
  imageTextBlocks: number;
  imageTextCodePoints: number;
  unknownBlocks: number;
  headerBlocks: number;
  footerBlocks: number;
  bodyBlocks: number;
  blocksWithoutPageSpan: number;
  blocksSpanningPages: number;
  tableRows: number;
  tableCells: number;
  tableCellsSpanningColumns: number;
  tableCellsSpanningRows: number;
  tableCaptions: number;
  tableCellsWithInternalLineBreak: number;
  tabsAdded: number;
  lineFeedsAdded: number;
  separatorsAdded: number;
  pagesWithHeader: number;
  pagesWithFooter: number;
  pagesWithoutBody: number;
  pagesExceedingExcludedCap: number;
  pageCodePoints: number;
  bodyCodePoints: number;
  lineEndHyphens: number;
  lineEndHyphensResolvableByRecurrence: number;
  ligatureGlyphs: number;
  forbiddenCharacters: number;
};

// Per-page facts for the Part B characterisation. Integers and booleans only.
export type PageFacts = {
  page: number;
  blocks: number;
  pageCodePoints: number;
  bodyStart: number;
  bodyEnd: number;
  hasHeader: boolean;
  hasFooter: boolean;
  tables: number;
  tableCells: number;
};

export type AdapterResult = {
  source: SourceDocumentText;
  counters: AdapterCounters;
};

export type CharacterisationResult = AdapterResult & {
  pageFacts: PageFacts[];
  blockTypes: Record<string, number>;
};

// Spec section 1 caps what a page may exclude as running header and footer.
const MAX_EXCLUDED_CODE_POINTS_PER_PAGE = 240;

function newCounters(): AdapterCounters {
  return {
    pages: 0,
    blocks: 0,
    nestedBlocks: 0,
    nestedHeaderFooterBlocks: 0,
    textBlocks: 0,
    tableBlocks: 0,
    listBlocks: 0,
    imageBlocks: 0,
    imageTextBlocks: 0,
    imageTextCodePoints: 0,
    unknownBlocks: 0,
    headerBlocks: 0,
    footerBlocks: 0,
    bodyBlocks: 0,
    blocksWithoutPageSpan: 0,
    blocksSpanningPages: 0,
    tableRows: 0,
    tableCells: 0,
    tableCellsSpanningColumns: 0,
    tableCellsSpanningRows: 0,
    tableCaptions: 0,
    tableCellsWithInternalLineBreak: 0,
    tabsAdded: 0,
    lineFeedsAdded: 0,
    separatorsAdded: 0,
    pagesWithHeader: 0,
    pagesWithFooter: 0,
    pagesWithoutBody: 0,
    pagesExceedingExcludedCap: 0,
    pageCodePoints: 0,
    bodyCodePoints: 0,
    lineEndHyphens: 0,
    lineEndHyphensResolvableByRecurrence: 0,
    ligatureGlyphs: 0,
    forbiddenCharacters: 0,
  };
}

// A block type reduced to a short, safe key: lower case, only [a-z0-9-], at most 40 characters.
// The .d.ts declares `type` as an unconstrained string, so it is treated as untrusted input that
// must never widen into free text in a report. U+005F is folded to "-" as well, because the
// values a Layout Parser emits are SCREAMING_SNAKE_CASE and the role table is written in kebab.
export function typeKey(raw: string | null | undefined): string {
  if (typeof raw !== "string") return "untyped";
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .slice(0, 40);
  return cleaned.length === 0 ? "untyped" : cleaned;
}

type Role = "header" | "footer" | "body";

function roleOf(block: LayoutBlock): Role {
  const textBlock = block.textBlock;
  if (textBlock === undefined || textBlock === null) return "body";
  return TEXT_BLOCK_ROLES.get(typeKey(textBlock.type)) ?? "body";
}

function codePointLength(text: string): number {
  return Array.from(text).length;
}

// One flattened placement: a run of text, the page it belongs to, and whether it sits inside or
// outside the body range. `tables` / `tableCells` are what this run contributed, for `PageFacts`.
type Placed = {
  text: string;
  page: number;
  role: Role;
  tables: number;
  tableCells: number;
};

type PageSpan = { start: number; end: number };

type WalkContext = {
  counters: AdapterCounters;
  blockTypes: Record<string, number>;
  lastPage: number;
};

function spanOf(block: LayoutBlock): PageSpan | undefined {
  const span = block.pageSpan;
  const start = typeof span?.pageStart === "number" ? span.pageStart : undefined;
  if (start === undefined || start < 1) return undefined;
  const declaredEnd = typeof span?.pageEnd === "number" ? span.pageEnd : start;
  return { start, end: declaredEnd > start ? declaredEnd : start };
}

function countBlockType(block: LayoutBlock, context: WalkContext): void {
  const textBlock = block.textBlock;
  const key =
    textBlock !== undefined && textBlock !== null
      ? `text-${typeKey(textBlock.type)}`
      : block.tableBlock !== undefined && block.tableBlock !== null
        ? "table"
        : block.listBlock !== undefined && block.listBlock !== null
          ? "list"
          : block.imageBlock !== undefined && block.imageBlock !== null
            ? "image"
            : "unknown";
  context.blockTypes[key] = (context.blockTypes[key] ?? 0) + 1;
}

function spanCount(value: number | null | undefined): number {
  return typeof value === "number" && Number.isInteger(value) && value > 1 ? value : 1;
}

// A table cell's own text, plus any header/footer-classified block found inside it, which is
// hoisted onto the page rather than left in the cell.
function renderCell(
  cell: LayoutTableCell,
  context: WalkContext,
  inherited: PageSpan | undefined,
  depth: number,
): { text: string; hoisted: Placed[] } {
  const units = (cell.blocks ?? []).flatMap((block) =>
    flattenBlock(block, context, inherited, depth + 1),
  );
  const body = units.filter(({ role }) => role === "body");
  context.counters.lineFeedsAdded += Math.max(body.length - 1, 0);
  return {
    text: body.map(({ text }) => text).join(LF),
    hoisted: units.filter(({ role }) => role !== "body"),
  };
}

// Row-major from the row/cell structure, never from positional text (spec section 7). A cell that
// spans columns is followed by `colSpan - 1` empty fields and a cell that spans rows reserves its
// column in the rows below, so every serialised row of a table has the same number of fields.
function renderTable(
  table: LayoutTableBlock,
  context: WalkContext,
  inherited: PageSpan | undefined,
  depth: number,
): { text: string; hoisted: Placed[] } {
  const counters = context.counters;
  const lines: string[] = [];
  const hoisted: Placed[] = [];
  const caption = table.caption;
  if (typeof caption === "string" && caption.length > 0) {
    counters.tableCaptions += 1;
    lines.push(caption);
  }

  // column -> how many further rows an earlier rowSpan still occupies.
  const occupied = new Map<number, number>();

  for (const row of [...(table.headerRows ?? []), ...(table.bodyRows ?? [])]) {
    counters.tableRows += 1;
    const fields: string[] = [];
    const reserved = new Map<number, number>();
    let column = 0;
    const skipOccupied = (): void => {
      while ((occupied.get(column) ?? 0) > 0) {
        fields[column] = "";
        column += 1;
      }
    };

    for (const cell of row.cells ?? []) {
      counters.tableCells += 1;
      skipOccupied();
      const columns = spanCount(cell.colSpan);
      const rows = spanCount(cell.rowSpan);
      if (columns > 1) counters.tableCellsSpanningColumns += 1;
      if (rows > 1) counters.tableCellsSpanningRows += 1;
      const rendered = renderCell(cell, context, inherited, depth);
      if (rendered.text.includes(LF)) counters.tableCellsWithInternalLineBreak += 1;
      hoisted.push(...rendered.hoisted);
      fields[column] = rendered.text;
      for (let extra = 1; extra < columns; extra += 1) fields[column + extra] = "";
      if (rows > 1) {
        for (let held = column; held < column + columns; held += 1) reserved.set(held, rows - 1);
      }
      column += columns;
    }
    skipOccupied();

    // `fields` can be sparse where a span skipped a position; every hole is an empty field.
    const serialised = Array.from({ length: fields.length }, (_, at) => fields[at] ?? "");
    counters.tabsAdded += Math.max(serialised.length - 1, 0);
    lines.push(serialised.join(TAB));

    for (const [held, remaining] of [...occupied]) {
      if (remaining <= 1) occupied.delete(held);
      else occupied.set(held, remaining - 1);
    }
    for (const [held, remaining] of reserved) occupied.set(held, remaining);
  }

  counters.lineFeedsAdded += Math.max(lines.length - 1, 0);
  return { text: lines.join(LF), hoisted };
}

// One block and everything under it, flattened into placements in document order.
function flattenBlock(
  block: LayoutBlock,
  context: WalkContext,
  inherited: PageSpan | undefined,
  depth: number,
): Placed[] {
  const counters = context.counters;
  counters.blocks += 1;
  if (depth > 0) counters.nestedBlocks += 1;
  countBlockType(block, context);

  const own = spanOf(block);
  if (own === undefined) counters.blocksWithoutPageSpan += 1;
  else if (own.end > own.start) counters.blocksSpanningPages += 1;
  const effective = own ?? inherited;
  const page = effective?.start ?? context.lastPage;
  // After a block that spans pages the next block without a pageSpan continues on the page that
  // block ended on, not the one it started on.
  if (own !== undefined) context.lastPage = own.end;

  const role = roleOf(block);
  if (role === "header") counters.headerBlocks += 1;
  else if (role === "footer") counters.footerBlocks += 1;
  else counters.bodyBlocks += 1;
  if (depth > 0 && role !== "body") counters.nestedHeaderFooterBlocks += 1;

  const units: Placed[] = [];
  const emit = (text: string, tables = 0, tableCells = 0): void => {
    if (text.length > 0) units.push({ text, page, role, tables, tableCells });
  };

  const textBlock = block.textBlock;
  if (textBlock !== undefined && textBlock !== null) {
    counters.textBlocks += 1;
    emit(textBlock.text ?? "");
    for (const child of textBlock.blocks ?? []) {
      units.push(...flattenBlock(child, context, effective, depth + 1));
    }
    return units;
  }

  const tableBlock = block.tableBlock;
  if (tableBlock !== undefined && tableBlock !== null) {
    counters.tableBlocks += 1;
    const tablesBefore = counters.tableBlocks;
    const cellsBefore = counters.tableCells;
    const rendered = renderTable(tableBlock, context, effective, depth);
    emit(rendered.text, counters.tableBlocks - tablesBefore + 1, counters.tableCells - cellsBefore);
    units.push(...rendered.hoisted);
    return units;
  }

  const listBlock = block.listBlock;
  if (listBlock !== undefined && listBlock !== null) {
    counters.listBlocks += 1;
    for (const entry of listBlock.listEntries ?? []) {
      for (const child of entry.blocks ?? []) {
        units.push(...flattenBlock(child, context, effective, depth + 1));
      }
    }
    return units;
  }

  const imageBlock = block.imageBlock;
  if (imageBlock !== undefined && imageBlock !== null) {
    counters.imageBlocks += 1;
    const imageText = imageBlock.imageText ?? "";
    if (imageText.length > 0) {
      counters.imageTextBlocks += 1;
      counters.imageTextCodePoints += codePointLength(imageText);
    }
    emit(imageText);
    return units;
  }

  counters.unknownBlocks += 1;
  return units;
}

// Every line-end hyphen the adapter saw on this page, and how many of them a conservative
// recurrence rule could resolve. The joined form is searched for across the whole document's page
// texts, not just this page, so the number is a document-level count rather than a per-page lower
// bound. The adapter does not act on either number: it leaves the hyphen glyph exactly as
// received (docs/design/extractor-spike.md, "The hard problem").
function countLineEndHyphens(
  pageText: string,
  documentText: string,
): { total: number; resolvable: number } {
  const points = Array.from(pageText);
  let total = 0;
  let resolvable = 0;
  for (let position = 0; position < points.length - 2; position += 1) {
    if (points[position] !== "-" || points[position + 1] !== LF) continue;
    total += 1;
    let left = position - 1;
    while (left >= 0 && WORD_CHARACTER.test(points[left] ?? "")) left -= 1;
    let right = position + 2;
    while (right < points.length && WORD_CHARACTER.test(points[right] ?? "")) right += 1;
    const head = points.slice(left + 1, position).join("");
    const tail = points.slice(position + 2, right).join("");
    if (head.length === 0 || tail.length === 0) continue;
    if (documentText.includes(`${head}${tail}`)) resolvable += 1;
  }
  return { total, resolvable };
}

function countCharacters(pageText: string, documentText: string, counters: AdapterCounters): void {
  for (const character of pageText) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (codePoint >= LIGATURE_RANGE_START && codePoint <= LIGATURE_RANGE_END) {
      counters.ligatureGlyphs += 1;
    }
    if (isForbidden(codePoint)) counters.forbiddenCharacters += 1;
  }
  const hyphens = countLineEndHyphens(pageText, documentText);
  counters.lineEndHyphens += hyphens.total;
  counters.lineEndHyphensResolvableByRecurrence += hyphens.resolvable;
}

// The processor version resource name the response carries, or a fixed placeholder. This is the
// only identity a `Document` holds; `processDocument` does not echo the processor name back.
export function processorVersion(document: LayoutDocument): string {
  const revision = (document.revisions ?? []).find(
    (entry) => typeof entry.processor === "string" && entry.processor.length > 0,
  );
  return revision?.processor ?? UNKNOWN_PROCESSOR_VERSION;
}

// The last segment of the processor version resource name: short enough for a report line, and
// the part that actually identifies the model.
export function processorVersionId(document: LayoutDocument): string {
  const segments = processorVersion(document).split("/");
  return segments[segments.length - 1] ?? UNKNOWN_PROCESSOR_VERSION;
}

function buildPages(placed: Placed[], counters: AdapterCounters): SourcePage[] {
  const pageCount = placed.reduce((highest, { page }) => Math.max(highest, page), 0);
  const pages: SourcePage[] = [];

  for (let page = 1; page <= pageCount; page += 1) {
    const onPage = placed.filter((entry) => entry.page === page);
    const headers = onPage.filter(({ role }) => role === "header");
    const footers = onPage.filter(({ role }) => role === "footer");
    const body = onPage.filter(({ role }) => role === "body");

    // A header line is followed by its own U+000A, so bodyStart is 0 or preceded by U+000A.
    const headerText = headers.map(({ text }) => `${text}${LF}`).join("");
    counters.lineFeedsAdded += headers.length;

    // The body ends with its own line terminator (spec section 1).
    const bodyText = body.length === 0 ? "" : `${body.map(({ text }) => text).join(LF)}${LF}`;
    counters.lineFeedsAdded += body.length;

    const footerText = footers.map(({ text }) => text).join(LF);
    counters.lineFeedsAdded += Math.max(footers.length - 1, 0);

    const text = `${headerText}${bodyText}${footerText}`;
    const pageCodePoints = codePointLength(text);

    // A page with no body block at all (a page that holds only a running footer, say) cannot
    // declare an empty body range: bodyStart = bodyEnd = 0 on a non-empty page breaks spec
    // section 1, which requires bodyEnd to be the page length or to follow a U+000A. Such a page
    // declares the whole page as its body and excludes nothing.
    const hasBody = body.length > 0;
    const bodyStart = hasBody ? codePointLength(headerText) : 0;
    const bodyEnd = hasBody ? bodyStart + codePointLength(bodyText) : pageCodePoints;

    if (headers.length > 0) counters.pagesWithHeader += 1;
    if (footers.length > 0) counters.pagesWithFooter += 1;
    if (!hasBody) counters.pagesWithoutBody += 1;
    if (pageCodePoints - (bodyEnd - bodyStart) > MAX_EXCLUDED_CODE_POINTS_PER_PAGE) {
      counters.pagesExceedingExcludedCap += 1;
    }
    counters.pageCodePoints += pageCodePoints;
    counters.bodyCodePoints += bodyEnd - bodyStart;

    pages.push({ page, text, bodyStart, bodyEnd });
  }

  counters.pages = pages.length;
  return pages;
}

type Walked = {
  source: SourceDocumentText;
  counters: AdapterCounters;
  placed: Placed[];
  blockTypes: Record<string, number>;
};

function walk(document: LayoutDocument, adapterVersion: string): Walked {
  const context: WalkContext = { counters: newCounters(), blockTypes: {}, lastPage: 1 };
  const placed = [...(document.documentLayout?.blocks ?? [])].flatMap((block) =>
    flattenBlock(block, context, undefined, 0),
  );
  const pages = buildPages(placed, context.counters);

  // The recurrence rule (spec section 4, "the hard problem") searches the whole document.
  const documentText = pages.map(({ text }) => text).join(LF);
  for (const page of pages) countCharacters(page.text, documentText, context.counters);

  context.counters.separatorsAdded = context.counters.tabsAdded + context.counters.lineFeedsAdded;

  return {
    source: {
      extractorVersion: `${processorVersion(document)}+adapter/${adapterVersion}`,
      pages,
    },
    counters: context.counters,
    placed,
    blockTypes: context.blockTypes,
  };
}

export function adapt(document: LayoutDocument, adapterVersion: string): AdapterResult {
  const { source, counters } = walk(document, adapterVersion);
  return { source, counters };
}

// The same adaptation, plus the per-page and per-block-type facts Part B reports. Kept separate
// so `adapt` keeps exactly the contract shape the design note states.
export function characterise(
  document: LayoutDocument,
  adapterVersion: string,
): CharacterisationResult {
  const { source, counters, placed, blockTypes } = walk(document, adapterVersion);

  const pageFacts: PageFacts[] = source.pages.map((page) => {
    const onPage = placed.filter((entry) => entry.page === page.page);
    return {
      page: page.page,
      blocks: onPage.length,
      pageCodePoints: codePointLength(page.text),
      bodyStart: page.bodyStart,
      bodyEnd: page.bodyEnd,
      hasHeader: onPage.some(({ role }) => role === "header"),
      hasFooter: onPage.some(({ role }) => role === "footer"),
      tables: onPage.reduce((total, { tables }) => total + tables, 0),
      tableCells: onPage.reduce((total, { tableCells }) => total + tableCells, 0),
    };
  });

  return { source, counters, pageFacts, blockTypes };
}
