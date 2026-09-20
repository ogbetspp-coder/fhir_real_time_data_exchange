import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import PDFDocument from "pdfkit";

import { collectNarrativeSections } from "../../../src/fidelity/index.js";
import { loadEmaMapping, type EmaMapping, type SectionRule } from "../../../src/fhir/mapping.js";
import { isComposition } from "../../../src/fhir/types.js";
import { createSyntheticSubmission } from "../../../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../../../src/fixtures/synthetic.js";

// Part A input for the extractor spike (docs/design/extractor-spike.md): a synthetic SmPC PDF
// laid out the way a real one is. Every sentence of body narrative comes from the fixture
// `src/fixtures/synthetic-submission.ts`; nothing here invents product information. The
// generator adds only layout: a running header, a page footer, QRD headings taken from the
// pinned mapping, a drawn table of identifiers, a two-column leaflet page, and a small block of
// deliberate typographic probes (a word broken over a line end, a U+FB01 ligature, typographic
// quotes) whose words are named in the manifest so the report can refer to them by identifier.

export const GENERATOR_VERSION = "spike-generate-pdf/1.0.0";
export const DEFAULT_OUTPUT_PATH = ".cache/spikes/document-ai/synthetic-smpc.pdf";

// The deliberate line-break hyphenation probe: "intra-" ends a line, "venous" begins the next.
// pdfkit does not hyphenate, so the break is forced by drawing two separate lines.
const HYPHEN_HEAD = "intra-";
const HYPHEN_TAIL = "venous";
// U+FB01 LATIN SMALL LIGATURE FI, written as a literal ligature code point inside one word.
const LIGATURE_WORD = "veriﬁcation";
const LIGATURE_CODE_POINT = "U+FB01";
// U+201C/U+201D and U+2018/U+2019, which section 4 of the specification refuses to normalise.
const QUOTE_PROBE = "“quoted” ‘quoted’";
const QUOTE_CODE_POINTS = ["U+201C", "U+201D", "U+2018", "U+2019"];

// A justified block that wraps over several lines so inter-word spacing is really exercised.
// Generator-authored text about the document itself; it carries no product information.
const JUSTIFIED_PROBE =
  "This block exists to exercise justified line breaking and inter-word spacing across " +
  "several lines of a single measure. It carries no product information and is not part of " +
  "any narrative section; the extractor spike uses it only to observe how a justified " +
  "paragraph reaches the embedded text layer of a born-digital document.";

const PAGE_WIDTH = 595.28;
const PAGE_HEIGHT = 841.89;
const SIDE_MARGIN = 45;
const HEADER_BASELINE = 26;
const BODY_TOP = 62;
const FOOTER_BASELINE = PAGE_HEIGHT - 34;
const BODY_WIDTH = PAGE_WIDTH - 2 * SIDE_MARGIN;

const BODY_FONT_SIZE = 9;
const CHROME_FONT_SIZE = 8;
const COLUMN_GAP = 16;
const COLUMN_SIDE_MARGIN = 30;
const COLUMN_WIDTH = (PAGE_WIDTH - 2 * COLUMN_SIDE_MARGIN - COLUMN_GAP) / 2;
// Tried largest-first; the first size at which every column sentence fits one line wins. The
// list is fixed so the choice is deterministic for a given fixture.
const COLUMN_FONT_SIZES = [9, 8.5, 8, 7.5, 7, 6.5, 6, 5.5, 5];

const TABLE_COLUMN_WIDTHS = [200, 120];
const TABLE_ROW_HEIGHT = 16;

export type PlacedHeading = {
  sourceKey: string;
  /** The QRD heading from the pinned mapping, not narrative. */
  title: string;
  page: number;
  /** True when the placement sits between the running header and the footer. */
  inBody: boolean;
  /** 1 on a single-column page; 1 or 2 on the two-column page. */
  column: number;
};

export type PlacedProbe = {
  page: number;
  inBody: boolean;
};

export type SyntheticPdfManifest = {
  generatorVersion: string;
  outputPath: string;
  pageCount: number;
  pageSize: { width: number; height: number };
  font: {
    family: string;
    embedded: boolean;
    source: string;
    ligatureGlyphAvailable: boolean;
  };
  runningHeaderPages: number[];
  footerPages: number[];
  headings: PlacedHeading[];
  sectionCount: number;
  /** Code-point length of each section sentence as drawn, keyed by sourceKey. */
  sectionCodePoints: { sourceKey: string; page: number; codePoints: number }[];
  table: { page: number; rowCount: number; columnCount: number; inBody: boolean };
  twoColumnPage: number;
  columnFontSize: number;
  justifiedBlocks: number;
  hyphenation: PlacedProbe & { head: string; tail: string };
  ligature: PlacedProbe & { word: string; codePoint: string };
  quotes: PlacedProbe & { codePoints: string[] };
  byteLength: number;
  sha256: string;
};

type PlannedSection = {
  sourceKey: string;
  title: string;
  page: number;
  startOffset: number;
  text: string;
};

type PlannedPage = {
  page: number;
  header: string;
  footer: string;
  sections: PlannedSection[];
};

type Plan = {
  pages: PlannedPage[];
  sectionCount: number;
};

function titleIndex(rule: SectionRule, into: Map<string, string>): Map<string, string> {
  into.set(rule.sourceKey, rule.title);
  for (const child of rule.children ?? []) titleIndex(child, into);
  return into;
}

function slice(codePoints: string[], start: number, end: number): string {
  return codePoints.slice(start, end).join("");
}

// The plan is read out of the fixture, never invented: page assignment, the sentence drawn for
// each section, the running header and the footer all come from the fixture's SourceDocumentText
// and its section provenance, so the PDF is a rendering of that document and not a new one.
function buildPlan(mapping: EmaMapping): Plan {
  const bundle = createSyntheticType2Bundle(mapping);
  const composition = bundle.entry[0]?.resource;
  if (composition === undefined || !isComposition(composition)) {
    throw new Error("Synthetic Type 2 fixture must have Composition as its first entry");
  }
  const titles = titleIndex(mapping.root, new Map<string, string>());
  const order = new Map(
    collectNarrativeSections(composition, mapping.sourceCodeSystem).map((section, index) => [
      section.sourceKey,
      index,
    ]),
  );

  const { submission, sourceText } = createSyntheticSubmission(mapping);
  const pageText = new Map(sourceText.pages.map((page) => [page.page, Array.from(page.text)]));

  const planned: PlannedSection[] = [];
  for (const provenance of submission.provenance.sections) {
    const span = provenance.spans[0];
    if (span === undefined || provenance.spans.length !== 1) {
      throw new Error(`Section ${provenance.sourceKey} must have exactly one span in the fixture`);
    }
    const codePoints = pageText.get(span.page);
    const title = titles.get(provenance.sourceKey);
    if (codePoints === undefined || title === undefined) {
      throw new Error(`Section ${provenance.sourceKey} has no page or no mapping title`);
    }
    planned.push({
      sourceKey: provenance.sourceKey,
      title,
      page: span.page,
      startOffset: span.startOffset,
      text: slice(codePoints, span.startOffset, span.endOffset),
    });
  }

  const pages: PlannedPage[] = sourceText.pages.map((page) => {
    const codePoints = pageText.get(page.page) ?? [];
    return {
      page: page.page,
      // The fixture's header ends in its own U+000A; the drawn running header is that line.
      header: slice(codePoints, 0, page.bodyStart).replace(/\n$/, ""),
      footer: slice(codePoints, page.bodyEnd, codePoints.length),
      sections: planned
        .filter((section) => section.page === page.page)
        .sort(
          (left, right) =>
            left.startOffset - right.startOffset ||
            (order.get(left.sourceKey) ?? 0) - (order.get(right.sourceKey) ?? 0),
        ),
    };
  });

  return { pages, sectionCount: planned.length };
}

type FontChoice = {
  family: string;
  regular: string;
  bold: string;
  embedded: boolean;
  source: string;
};

// The 14 standard PDF fonts are WinAnsi-encoded and have no U+FB01 glyph, so a ligature written
// as a literal code point cannot survive in them. Liberation Sans ships with the pinned
// `pdfjs-dist` dependency and does have U+FB01, so it is embedded and subset by pdfkit. If it
// cannot be resolved the generator falls back to Helvetica and records `embedded: false` plus
// `ligatureGlyphAvailable: false` in the manifest rather than pretending the glyph is there.
function chooseFont(): FontChoice {
  const require = createRequire(import.meta.url);
  try {
    const pkg = require.resolve("pdfjs-dist/package.json");
    const dir = path.join(path.dirname(pkg), "standard_fonts");
    return {
      family: "LiberationSans",
      regular: path.join(dir, "LiberationSans-Regular.ttf"),
      bold: path.join(dir, "LiberationSans-Bold.ttf"),
      embedded: true,
      source: "pdfjs-dist/standard_fonts",
    };
  } catch {
    return {
      family: "Helvetica",
      regular: "Helvetica",
      bold: "Helvetica-Bold",
      embedded: false,
      source: "pdfkit-standard-14",
    };
  }
}

type Doc = PDFKit.PDFDocument;

function drawRunningHeader(doc: Doc, text: string): void {
  doc
    .font("chrome")
    .fontSize(CHROME_FONT_SIZE)
    .text(text, SIDE_MARGIN, HEADER_BASELINE, { width: BODY_WIDTH, lineBreak: false });
  doc
    .moveTo(SIDE_MARGIN, HEADER_BASELINE + 12)
    .lineTo(PAGE_WIDTH - SIDE_MARGIN, HEADER_BASELINE + 12)
    .lineWidth(0.5)
    .stroke();
}

function drawFooter(doc: Doc, text: string): void {
  // pdfkit starts a new page when text is written below the bottom margin; the footer is drawn
  // there on purpose, so the margin is lifted for the single call and restored straight after.
  const bottom = doc.page.margins.bottom;
  doc.page.margins.bottom = 0;
  doc
    .font("chrome")
    .fontSize(CHROME_FONT_SIZE)
    .text(text, SIDE_MARGIN, FOOTER_BASELINE, { width: BODY_WIDTH, lineBreak: false });
  doc.page.margins.bottom = bottom;
}

function drawSection(doc: Doc, section: PlannedSection, x: number, width: number, size: number) {
  doc.font("heading").fontSize(size).text(section.title, x, doc.y, { width, lineBreak: true });
  doc
    .font("body")
    .fontSize(size)
    .text(section.text, x, doc.y, { width, align: "justify", lineBreak: true });
  doc.y += size * 0.5;
}

function drawTable(doc: Doc, rows: string[][], x: number, top: number): void {
  const widths = TABLE_COLUMN_WIDTHS;
  doc.lineWidth(0.5).font("body").fontSize(CHROME_FONT_SIZE);
  for (const [rowIndex, row] of rows.entries()) {
    const y = top + rowIndex * TABLE_ROW_HEIGHT;
    let cellX = x;
    for (const [cellIndex, cell] of row.entries()) {
      const width = widths[cellIndex] ?? 0;
      doc.rect(cellX, y, width, TABLE_ROW_HEIGHT).stroke();
      doc.font(rowIndex === 0 ? "heading" : "body");
      doc.text(cell, cellX + 4, y + 4, { width: width - 8, lineBreak: false });
      cellX += width;
    }
  }
  doc.x = x;
  doc.y = top + rows.length * TABLE_ROW_HEIGHT + 10;
}

function tableRows(mapping: EmaMapping): string[][] {
  // Identifiers from the pinned mapping only: a QRD source key and its EMA target code.
  const clinical = mapping.root.children?.find((child) => child.sourceKey === "smpc.4");
  const rules = (clinical?.children ?? []).slice(0, 3);
  if (rules.length !== 3) throw new Error("Mapping must expose at least three smpc.4 subsections");
  return [
    ["QRD section key", "Target code"],
    ...rules.map((rule) => [rule.sourceKey, rule.targetCode]),
  ];
}

function fitColumnFontSize(doc: Doc, sections: PlannedSection[]): number {
  for (const size of COLUMN_FONT_SIZES) {
    doc.font("body").fontSize(size);
    const fits = sections.every(
      (section) => doc.widthOfString(section.text, { lineBreak: false }) <= COLUMN_WIDTH - 1,
    );
    if (fits) return size;
  }
  // Every candidate wrapped: the smallest is used and the manifest records it, so a caller can
  // see that column sentences may be split over two lines.
  return COLUMN_FONT_SIZES[COLUMN_FONT_SIZES.length - 1] ?? BODY_FONT_SIZE;
}

export async function generateSyntheticSmpcPdf(
  outputPath: string = DEFAULT_OUTPUT_PATH,
  mappingPath?: string,
): Promise<SyntheticPdfManifest> {
  const mapping = await loadEmaMapping(mappingPath);
  const plan = buildPlan(mapping);
  const font = chooseFont();

  const firstPage = plan.pages[0];
  const lastPage = plan.pages[plan.pages.length - 1];
  if (firstPage === undefined || lastPage === undefined) {
    throw new Error("Fixture source document has no pages");
  }
  const twoColumnPage = lastPage.page;
  const tablePage =
    plan.pages.length > 1 ? (plan.pages[1]?.page ?? firstPage.page) : firstPage.page;

  const doc = new PDFDocument({
    size: [PAGE_WIDTH, PAGE_HEIGHT],
    margins: {
      top: BODY_TOP,
      bottom: PAGE_HEIGHT - FOOTER_BASELINE + 8,
      left: SIDE_MARGIN,
      right: SIDE_MARGIN,
    },
    autoFirstPage: false,
    compress: true,
    // Fixed document information so two runs of the same fixture differ in no metadata.
    info: {
      Title: "Synthetic SmPC (extractor spike, Part A)",
      Author: "ema-flow extractor spike",
      Creator: GENERATOR_VERSION,
      Producer: GENERATOR_VERSION,
      CreationDate: new Date(0),
      ModDate: new Date(0),
    },
  });
  doc.registerFont("body", font.regular);
  doc.registerFont("heading", font.bold);
  doc.registerFont("chrome", font.bold);

  const chunks: Buffer[] = [];
  let drawnPages = 0;
  doc.on("pageAdded", () => {
    drawnPages += 1;
  });
  const finished = new Promise<void>((resolve, reject) => {
    doc.on("data", (chunk: Buffer) => chunks.push(chunk));
    doc.on("end", () => resolve());
    doc.on("error", (error: Error) => reject(error));
  });

  const headings: PlacedHeading[] = [];
  const sectionCodePoints: SyntheticPdfManifest["sectionCodePoints"] = [];
  const runningHeaderPages: number[] = [];
  const footerPages: number[] = [];
  let justifiedBlocks = 0;
  let table = { page: tablePage, rowCount: 0, columnCount: 0, inBody: true };

  const columnFontSize = fitColumnFontSize(doc, lastPage.sections);

  for (const page of plan.pages) {
    doc.addPage();
    drawRunningHeader(doc, page.header);
    runningHeaderPages.push(page.page);
    doc.x = SIDE_MARGIN;
    doc.y = BODY_TOP;

    if (page.page === twoColumnPage) {
      // A package-leaflet page: two columns drawn side by side, column one filled before column
      // two, so the content stream order is the reading order.
      const half = Math.ceil(page.sections.length / 2);
      for (const column of [1, 2]) {
        const slice0 = page.sections.slice((column - 1) * half, column * half);
        const x = COLUMN_SIDE_MARGIN + (column - 1) * (COLUMN_WIDTH + COLUMN_GAP);
        doc.x = x;
        doc.y = BODY_TOP;
        for (const section of slice0) {
          headings.push({
            sourceKey: section.sourceKey,
            title: section.title,
            page: page.page,
            inBody: true,
            column,
          });
          sectionCodePoints.push({
            sourceKey: section.sourceKey,
            page: page.page,
            codePoints: Array.from(section.text).length,
          });
          drawSection(doc, section, x, COLUMN_WIDTH, columnFontSize);
          justifiedBlocks += 1;
        }
      }
    } else {
      for (const section of page.sections) {
        headings.push({
          sourceKey: section.sourceKey,
          title: section.title,
          page: page.page,
          inBody: true,
          column: 1,
        });
        sectionCodePoints.push({
          sourceKey: section.sourceKey,
          page: page.page,
          codePoints: Array.from(section.text).length,
        });
        drawSection(doc, section, SIDE_MARGIN, BODY_WIDTH, BODY_FONT_SIZE);
        justifiedBlocks += 1;
      }
    }

    if (page.page === firstPage.page) {
      // The typographic probes: a forced line-end hyphen, a literal ligature glyph, and
      // typographic quotes, each on its own line so the extracted text shows the break.
      doc
        .font("heading")
        .fontSize(BODY_FONT_SIZE)
        .text("Layout probes", SIDE_MARGIN, doc.y + 6, {
          width: BODY_WIDTH,
          lineBreak: false,
        });
      doc.font("body").fontSize(BODY_FONT_SIZE);
      doc.text(`Hyphenation probe: ${HYPHEN_HEAD}`, SIDE_MARGIN, doc.y, {
        width: BODY_WIDTH,
        lineBreak: false,
      });
      doc.text(HYPHEN_TAIL, SIDE_MARGIN, doc.y, { width: BODY_WIDTH, lineBreak: false });
      doc.text(`Ligature probe: ${LIGATURE_WORD}`, SIDE_MARGIN, doc.y, {
        width: BODY_WIDTH,
        lineBreak: false,
      });
      doc.text(`Quote probe: ${QUOTE_PROBE}`, SIDE_MARGIN, doc.y, {
        width: BODY_WIDTH,
        lineBreak: false,
      });
    }

    if (page.page === tablePage) {
      const rows = tableRows(mapping);
      doc
        .font("heading")
        .fontSize(BODY_FONT_SIZE)
        .text("Section code table", SIDE_MARGIN, doc.y + 6, {
          width: BODY_WIDTH,
          lineBreak: false,
        });
      drawTable(doc, rows, SIDE_MARGIN, doc.y + 4);
      table = {
        page: tablePage,
        rowCount: rows.length,
        columnCount: TABLE_COLUMN_WIDTHS.length,
        inBody: true,
      };
      doc
        .font("body")
        .fontSize(BODY_FONT_SIZE)
        .text(JUSTIFIED_PROBE, SIDE_MARGIN, doc.y + 4, {
          width: BODY_WIDTH,
          align: "justify",
          lineBreak: true,
        });
      justifiedBlocks += 1;
    }

    drawFooter(doc, page.footer);
    footerPages.push(page.page);
  }

  doc.end();
  await finished;

  // A page pdfkit added on its own means the layout overflowed and the fixture's page
  // assignment no longer holds; that is a generator bug, not a fixture to hand on.
  if (drawnPages !== plan.pages.length) {
    throw new Error(
      `Layout overflowed: drew ${drawnPages} pages for ${plan.pages.length} fixture pages`,
    );
  }

  const bytes = Buffer.concat(chunks);
  const resolved = path.resolve(outputPath);
  await mkdir(path.dirname(resolved), { recursive: true });
  await writeFile(resolved, bytes);

  return {
    generatorVersion: GENERATOR_VERSION,
    outputPath: resolved,
    pageCount: plan.pages.length,
    pageSize: { width: PAGE_WIDTH, height: PAGE_HEIGHT },
    font: {
      family: font.family,
      embedded: font.embedded,
      source: font.source,
      ligatureGlyphAvailable: font.embedded,
    },
    runningHeaderPages,
    footerPages,
    headings,
    sectionCount: plan.sectionCount,
    sectionCodePoints,
    table,
    twoColumnPage,
    columnFontSize,
    justifiedBlocks,
    hyphenation: {
      page: firstPage.page,
      inBody: true,
      head: HYPHEN_HEAD,
      tail: HYPHEN_TAIL,
    },
    ligature: {
      page: firstPage.page,
      inBody: true,
      word: LIGATURE_WORD,
      codePoint: LIGATURE_CODE_POINT,
    },
    quotes: { page: firstPage.page, inBody: true, codePoints: QUOTE_CODE_POINTS },
    byteLength: bytes.byteLength,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}

async function main(): Promise<void> {
  const target = process.argv[2] ?? DEFAULT_OUTPUT_PATH;
  const manifest = await generateSyntheticSmpcPdf(target);
  // Counts, identifiers, and hashes only.
  console.log(
    JSON.stringify(
      {
        ...manifest,
        headings: manifest.headings.map(({ sourceKey, page, inBody, column }) => ({
          sourceKey,
          page,
          inBody,
          column,
        })),
      },
      null,
      2,
    ),
  );
}

const invokedDirectly =
  process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) await main();
