import type { protos } from "@google-cloud/documentai";
import { describe, expect, it } from "vitest";

import { adapt, characterise, typeKey } from "../../scripts/spikes/document-ai/adapt.js";

// Structural half of the adapter tests (the round trip on the recorded response lives in
// document-ai-adapter.test.ts): small hand-built Layout Parser responses, one per adapter rule
// that an adversarial review found unpinned. Every string below is invented for this file.
// Assertion messages carry keys, counts, and rule names only — never narrative.

type LayoutDocument = protos.google.cloud.documentai.v1.IDocument;
type LayoutBlock = protos.google.cloud.documentai.v1.Document.DocumentLayout.IDocumentLayoutBlock;

const ADAPTER_VERSION = "1.0.0";
const TAB = "\t";
const LF = "\n";

function layoutDocument(blocks: LayoutBlock[]): LayoutDocument {
  return {
    revisions: [{ processor: "projects/s/locations/eu/processors/s/processorVersions/test-v1" }],
    documentLayout: { blocks },
  };
}

function onPage(page: number, block: LayoutBlock): LayoutBlock {
  return { pageSpan: { pageStart: page, pageEnd: page }, ...block };
}

function text(value: string, type: string, nested?: LayoutBlock[]): LayoutBlock {
  return {
    textBlock: nested === undefined ? { text: value, type } : { text: value, type, blocks: nested },
  };
}

function cell(
  value: string,
  spans?: { rowSpan?: number; colSpan?: number },
): {
  blocks: LayoutBlock[];
  rowSpan?: number;
  colSpan?: number;
} {
  return { blocks: [text(value, "paragraph")], ...spans };
}

function pageOne(document: LayoutDocument): { text: string; bodyStart: number; bodyEnd: number } {
  const page = adapt(document, ADAPTER_VERSION).source.pages[0];
  if (page === undefined) throw new Error("expected page 1");
  return { text: page.text, bodyStart: page.bodyStart, bodyEnd: page.bodyEnd };
}

function slice(value: string, start: number, end: number): string {
  return Array.from(value).slice(start, end).join("");
}

describe("image blocks", () => {
  const IMAGE_TEXT = "Dose 5 mg";

  it("renders ILayoutImageBlock.imageText into the page and counts it", () => {
    const { source, counters } = adapt(
      layoutDocument([
        onPage(1, text("Alpha", "paragraph")),
        onPage(1, { imageBlock: { mimeType: "image/png", imageText: IMAGE_TEXT } }),
      ]),
      ADAPTER_VERSION,
    );
    const page = source.pages[0];
    if (page === undefined) throw new Error("expected page 1");

    for (const character of IMAGE_TEXT) {
      const label = `u+${(character.codePointAt(0) ?? 0).toString(16)}`;
      expect([label, page.text.includes(character)]).toEqual([label, true]);
    }
    expect(page.text.includes(IMAGE_TEXT)).toBe(true);
    expect(counters.imageBlocks).toBe(1);
    expect(counters.imageTextBlocks).toBe(1);
    expect(counters.imageTextCodePoints).toBe(Array.from(IMAGE_TEXT).length);
  });

  it("counts an image block with no imageText without adding a character", () => {
    const { source, counters } = adapt(
      layoutDocument([
        onPage(1, text("Alpha", "paragraph")),
        onPage(1, { imageBlock: { mimeType: "image/png" } }),
      ]),
      ADAPTER_VERSION,
    );
    expect(source.pages[0]?.text).toBe(`Alpha${LF}`);
    expect(counters.imageBlocks).toBe(1);
    expect(counters.imageTextBlocks).toBe(0);
    expect(counters.imageTextCodePoints).toBe(0);
  });
});

describe("nested blocks", () => {
  it("classifies and places blocks nested under a heading", () => {
    const document = layoutDocument([
      onPage(
        1,
        text("Heading one", "heading-1", [
          text("Body sentence", "paragraph"),
          text("Sheet 1 of 1", "PAGE_FOOTER"),
          text("Running title", "page-header"),
        ]),
      ),
    ]);
    const { text: pageText, bodyStart, bodyEnd } = pageOne(document);
    const { counters } = adapt(document, ADAPTER_VERSION);
    const points = Array.from(pageText);

    expect(slice(pageText, 0, bodyStart)).toBe(`Running title${LF}`);
    expect(slice(pageText, bodyStart, bodyEnd)).toBe(`Heading one${LF}Body sentence${LF}`);
    expect(slice(pageText, bodyEnd, points.length)).toBe("Sheet 1 of 1");
    expect(counters.nestedBlocks).toBe(3);
    expect(counters.nestedHeaderFooterBlocks).toBe(2);
    expect(counters.pagesWithHeader).toBe(1);
    expect(counters.pagesWithFooter).toBe(1);
  });

  it("carries the parent's pageSpan down and lets a child override it", () => {
    const { source, counters } = adapt(
      layoutDocument([
        onPage(
          1,
          text("Heading one", "heading-1", [
            text("Stays on one", "paragraph"),
            { pageSpan: { pageStart: 2, pageEnd: 2 }, ...text("Moves to two", "paragraph") },
          ]),
        ),
      ]),
      ADAPTER_VERSION,
    );
    expect(source.pages).toHaveLength(2);
    expect(source.pages[0]?.text).toBe(`Heading one${LF}Stays on one${LF}`);
    expect(source.pages[1]?.text).toBe(`Moves to two${LF}`);
    expect(counters.nestedBlocks).toBe(2);
  });

  it("classifies blocks nested in a list entry", () => {
    const document = layoutDocument([
      onPage(1, {
        listBlock: {
          type: "unordered",
          listEntries: [
            { blocks: [text("First item", "paragraph")] },
            { blocks: [text("Sheet 1", "page-number")] },
          ],
        },
      }),
    ]);
    const { text: pageText, bodyStart, bodyEnd } = pageOne(document);
    expect(slice(pageText, bodyStart, bodyEnd)).toBe(`First item${LF}`);
    expect(slice(pageText, bodyEnd, Array.from(pageText).length)).toBe("Sheet 1");
  });

  it("hoists a header block nested inside a table cell out of the body", () => {
    const document = layoutDocument([
      onPage(1, {
        tableBlock: {
          bodyRows: [
            {
              cells: [
                { blocks: [text("Kept", "paragraph"), text("Running title", "page-header")] },
                cell("Also kept"),
              ],
            },
          ],
        },
      }),
    ]);
    const { text: pageText, bodyStart, bodyEnd } = pageOne(document);
    expect(slice(pageText, 0, bodyStart)).toBe(`Running title${LF}`);
    expect(slice(pageText, bodyStart, bodyEnd)).toBe(`Kept${TAB}Also kept${LF}`);
  });
});

describe("pages without a body block", () => {
  it("declares a body range that still obeys spec section 1", () => {
    const { source, counters } = adapt(
      layoutDocument([
        onPage(1, text("Running title", "header")),
        onPage(1, text("Body sentence", "paragraph")),
        onPage(1, text("Sheet 1 of 2", "footer")),
        onPage(2, text("Sheet 2 of 2", "footer")),
      ]),
      ADAPTER_VERSION,
    );

    expect(source.pages).toHaveLength(2);
    for (const page of source.pages) {
      const label = `page-${String(page.page)}`;
      const points = Array.from(page.text);
      expect([label, page.bodyStart === 0 || points[page.bodyStart - 1] === LF]).toEqual([
        label,
        true,
      ]);
      expect([label, page.bodyEnd === points.length || points[page.bodyEnd - 1] === LF]).toEqual([
        label,
        true,
      ]);
    }

    const second = source.pages[1];
    if (second === undefined) throw new Error("expected page 2");
    expect(second.bodyStart).toBe(0);
    expect(second.bodyEnd).toBe(Array.from(second.text).length);
    expect(counters.pagesWithoutBody).toBe(1);
    expect(counters.pagesExceedingExcludedCap).toBe(0);
  });
});

describe("block type keys", () => {
  it("folds U+005F and U+0020 so the role table matches", () => {
    expect(typeKey("PAGE_HEADER")).toBe("page-header");
    expect(typeKey("page_footer")).toBe("page-footer");
    expect(typeKey("Page Header")).toBe("page-header");
    expect(typeKey("RUNNING_FOOTER")).toBe("running-footer");
    expect(typeKey("paragraph")).toBe("paragraph");
    expect(typeKey(undefined)).toBe("untyped");
  });

  it("places PAGE_HEADER, page_footer and Page Header outside the body", () => {
    const document = layoutDocument([
      onPage(1, text("Running title", "PAGE_HEADER")),
      onPage(1, text("Second title", "Page Header")),
      onPage(1, text("Body sentence", "paragraph")),
      onPage(1, text("Sheet 1", "page_footer")),
    ]);
    const { text: pageText, bodyStart, bodyEnd } = pageOne(document);
    const { counters, blockTypes } = characterise(document, ADAPTER_VERSION);

    expect(slice(pageText, bodyStart, bodyEnd)).toBe(`Body sentence${LF}`);
    expect(counters.headerBlocks).toBe(2);
    expect(counters.footerBlocks).toBe(1);
    expect(counters.bodyBlocks).toBe(1);
    expect(blockTypes["text-page-header"]).toBe(2);
    expect(blockTypes["text-page-footer"]).toBe(1);
  });
});

describe("table cell spans", () => {
  it("pads a column-spanning header cell so every row has the same field count", () => {
    const document = layoutDocument([
      onPage(1, {
        tableBlock: {
          headerRows: [{ cells: [cell("Both columns", { colSpan: 2 })] }],
          bodyRows: [{ cells: [cell("A1"), cell("B1")] }, { cells: [cell("A2"), cell("B2")] }],
        },
      }),
    ]);
    const { text: pageText, bodyStart, bodyEnd } = pageOne(document);
    const { counters } = adapt(document, ADAPTER_VERSION);
    const rows = slice(pageText, bodyStart, bodyEnd)
      .split(LF)
      .filter((line) => line.length > 0);

    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.split(TAB).length)).toEqual([2, 2, 2]);
    expect(rows[0]).toBe(`Both columns${TAB}`);
    expect(counters.tableCellsSpanningColumns).toBe(1);
    expect(counters.tableCellsSpanningRows).toBe(0);
    expect(counters.tableCells).toBe(5);
  });

  it("reserves the column an earlier row-spanning cell occupies", () => {
    const document = layoutDocument([
      onPage(1, {
        tableBlock: {
          bodyRows: [
            { cells: [cell("Held", { rowSpan: 2 }), cell("B1")] },
            { cells: [cell("B2")] },
            { cells: [cell("A3"), cell("B3")] },
          ],
        },
      }),
    ]);
    const { text: pageText, bodyStart, bodyEnd } = pageOne(document);
    const { counters } = adapt(document, ADAPTER_VERSION);
    const rows = slice(pageText, bodyStart, bodyEnd)
      .split(LF)
      .filter((line) => line.length > 0);

    expect(rows.map((row) => row.split(TAB).length)).toEqual([2, 2, 2]);
    expect(rows[1]).toBe(`${TAB}B2`);
    expect(rows[2]).toBe(`A3${TAB}B3`);
    expect(counters.tableCellsSpanningRows).toBe(1);
  });
});

describe("page attribution after a block that spans pages", () => {
  it("continues on the page the spanning block ended on", () => {
    const { source, counters } = adapt(
      layoutDocument([
        { pageSpan: { pageStart: 1, pageEnd: 2 }, ...text("Spanning block", "paragraph") },
        text("Follows after", "paragraph"),
      ]),
      ADAPTER_VERSION,
    );

    expect(source.pages).toHaveLength(2);
    expect(source.pages[0]?.text).toBe(`Spanning block${LF}`);
    expect(source.pages[1]?.text).toBe(`Follows after${LF}`);
    expect(counters.blocksSpanningPages).toBe(1);
    expect(counters.blocksWithoutPageSpan).toBe(1);
  });
});

describe("line-end hyphen recurrence", () => {
  it("resolves against the whole document, not only the page the hyphen is on", () => {
    const { counters } = adapt(
      layoutDocument([
        onPage(1, text(`Given intra-${LF}venous once`, "paragraph")),
        onPage(2, text("The intravenous route", "paragraph")),
      ]),
      ADAPTER_VERSION,
    );
    expect(counters.lineEndHyphens).toBe(1);
    expect(counters.lineEndHyphensResolvableByRecurrence).toBe(1);
  });

  it("leaves a hyphen whose joined form occurs nowhere unresolved", () => {
    const { counters } = adapt(
      layoutDocument([
        onPage(1, text(`Given intra-${LF}venous once`, "paragraph")),
        onPage(2, text("The other route", "paragraph")),
      ]),
      ADAPTER_VERSION,
    );
    expect(counters.lineEndHyphens).toBe(1);
    expect(counters.lineEndHyphensResolvableByRecurrence).toBe(0);
  });
});
