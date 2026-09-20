import { readFile } from "node:fs/promises";

import type { protos } from "@google-cloud/documentai";
import { beforeAll, describe, expect, it } from "vitest";

import {
  normalizeNarrative,
  type NarrativeSection,
  type SourceDocumentText,
} from "../../src/fidelity/index.js";
import {
  adapt,
  characterise,
  type AdapterCounters,
} from "../../scripts/spikes/document-ai/adapt.js";
import {
  ReportTextError,
  assertTextFree,
  buildPartAReport,
  diffCharacters,
  syntheticSections,
  type PartAReport,
} from "../../scripts/spikes/document-ai/report.js";

// Network-free half of the extractor spike (docs/design/extractor-spike.md, Part A): the
// hand-authored Layout Parser response goes through the adapter and the real fidelity check.
// Assertion messages here carry keys, counts, and rule names only — never narrative.

const FIXTURE_PATH = "test/fixtures/spikes/document-ai-layout-response.json";
const ADAPTER_VERSION = "1.0.0";

type LayoutDocument = protos.google.cloud.documentai.v1.IDocument;

const TAB = "\t";
const LF = "\n";

let document: LayoutDocument;
let sections: NarrativeSection[];
let source: SourceDocumentText;
let counters: AdapterCounters;
let report: PartAReport;

// Every string the response feeds the adapter: a text block's own text and a table's caption.
// Cell text lives in nested blocks and is collected by the same walk.
function inputStrings(value: unknown, into: string[]): void {
  if (Array.isArray(value)) {
    for (const item of value) inputStrings(item, into);
    return;
  }
  if (typeof value !== "object" || value === null) return;
  const record = value as Record<string, unknown>;
  const textBlock = record.textBlock;
  if (typeof textBlock === "object" && textBlock !== null) {
    const text = (textBlock as { text?: unknown }).text;
    if (typeof text === "string") into.push(text);
  }
  const tableBlock = record.tableBlock;
  if (typeof tableBlock === "object" && tableBlock !== null) {
    const caption = (tableBlock as { caption?: unknown }).caption;
    if (typeof caption === "string") into.push(caption);
  }
  for (const child of Object.values(record)) inputStrings(child, into);
}

// A multiset of code points with the two separators the adapter is allowed to add removed.
function codePointCensus(texts: string[]): Record<string, number> {
  const census: Record<string, number> = {};
  for (const text of texts) {
    for (const character of text) {
      if (character === TAB || character === LF) continue;
      census[character] = (census[character] ?? 0) + 1;
    }
  }
  return census;
}

beforeAll(async () => {
  const parsed = JSON.parse(await readFile(FIXTURE_PATH, "utf8")) as { document: LayoutDocument };
  document = parsed.document;
  sections = await syntheticSections();
  const adapted = adapt(document, ADAPTER_VERSION);
  source = adapted.source;
  counters = adapted.counters;
  report = buildPartAReport({ document, sections, adapterVersion: ADAPTER_VERSION }).report;
});

describe("Document AI adapter, Part A round trip", () => {
  it("verifies every fixture section through verifyNarrativeFidelity", () => {
    expect(sections).toHaveLength(32);
    expect(report.summary).toEqual({ total: 32, located: 32, verified: 32 });
    expect(report.fidelityStatus).toBe("passed");
    expect(report.issueCount).toBe(0);
    expect(report.sections.filter(({ status }) => status !== "verified")).toEqual([]);
    expect(report.coverage.coveredCodePoints).toBeGreaterThan(0);
  });

  it("pins the extractor identity to the processor version and the adapter version", () => {
    expect(source.extractorVersion.endsWith(`+adapter/${ADAPTER_VERSION}`)).toBe(true);
    expect(report.processorVersionId).toBe("hand-authored-layout-v1");
  });
});

describe("page body ranges", () => {
  it("obeys spec section 1 on every page", () => {
    expect(source.pages).toHaveLength(3);
    for (const page of source.pages) {
      const points = Array.from(page.text);
      const label = `page-${String(page.page)}`;
      // bodyStart is 0 or immediately follows U+000A; bodyEnd is the page length or the code
      // point before it is U+000A; a page excludes at most 240 code points.
      expect([label, page.bodyStart === 0 || points[page.bodyStart - 1] === LF]).toEqual([
        label,
        true,
      ]);
      expect([label, page.bodyEnd === points.length || points[page.bodyEnd - 1] === LF]).toEqual([
        label,
        true,
      ]);
      expect([label, points.length - (page.bodyEnd - page.bodyStart) <= 240]).toEqual([
        label,
        true,
      ]);
      expect([label, page.bodyEnd > page.bodyStart]).toEqual([label, true]);
    }
    expect(report.pages.every(({ bodyStartOnLineBoundary }) => bodyStartOnLineBoundary)).toBe(true);
    expect(report.pages.every(({ bodyEndOnLineBoundary }) => bodyEndOnLineBoundary)).toBe(true);
  });

  it("places header and footer blocks outside the body range", () => {
    expect(counters.pagesWithHeader).toBe(3);
    expect(counters.pagesWithFooter).toBe(3);
    expect(counters.pagesExceedingExcludedCap).toBe(0);
    for (const page of source.pages) {
      const label = `page-${String(page.page)}`;
      const body = Array.from(page.text).slice(page.bodyStart, page.bodyEnd).join("");
      expect([label, body.includes("Page ")]).toEqual([label, false]);
    }
  });
});

describe("character faithfulness", () => {
  it("adds no character other than U+0009 and U+000A", () => {
    const received: string[] = [];
    inputStrings(document, received);
    expect(received.length).toBeGreaterThan(70);
    const before = codePointCensus(received);
    const after = codePointCensus(source.pages.map(({ text }) => text));
    // Multiset equality: nothing was dropped, added, expanded, straightened, or collapsed.
    expect(after).toEqual(before);
  });

  it("leaves line-end hyphens alone and counts them instead", () => {
    const joined = source.pages.map(({ text }) => text).join("");
    expect(joined.includes("­")).toBe(false);
    expect(counters.lineEndHyphens).toBe(2);
    expect(counters.lineEndHyphensResolvableByRecurrence).toBe(1);
  });

  it("keeps the ligature glyph and reports it", () => {
    expect(counters.ligatureGlyphs).toBe(1);
    expect(counters.forbiddenCharacters).toBe(0);
  });

  it("serialises the table row-major from its row and cell structure", () => {
    expect(counters.tableBlocks).toBe(1);
    expect(counters.tableRows).toBe(3);
    expect(counters.tableCells).toBe(6);
    expect(counters.tabsAdded).toBe(3);
    const page = source.pages[1];
    if (page === undefined) throw new Error("expected page 2");
    const rows = page.text.split(LF).filter((line) => line.includes(TAB));
    expect(rows).toHaveLength(3);
    expect(rows.every((row) => row.split(TAB).length === 2)).toBe(true);
  });
});

describe("counters", () => {
  it("are all integers", () => {
    for (const [name, value] of Object.entries(counters)) {
      expect([name, Number.isInteger(value)]).toEqual([name, true]);
    }
    expect(counters.separatorsAdded).toBe(counters.tabsAdded + counters.lineFeedsAdded);
  });

  it("describe the block population for Part B without naming block content", () => {
    const { blockTypes, pageFacts } = characterise(document, ADAPTER_VERSION);
    expect(Object.values(blockTypes).every(Number.isInteger)).toBe(true);
    expect(blockTypes.table).toBe(1);
    expect(pageFacts.map(({ hasHeader }) => hasHeader)).toEqual([true, true, true]);
    expect(pageFacts.map(({ hasFooter }) => hasFooter)).toEqual([true, true, true]);
    expect(pageFacts.reduce((total, { tableCells }) => total + tableCells, 0)).toBe(6);
  });
});

describe("assertTextFree", () => {
  it("passes on the Part A report", () => {
    expect(() => {
      assertTextFree(report);
    }).not.toThrow();
  });

  it("rejects a report carrying a fixture sentence", () => {
    const section = sections[0];
    if (section === undefined) throw new Error("expected at least one fixture section");
    const normalized = normalizeNarrative(section.div);
    if (!("text" in normalized)) throw new Error("expected a normalisable fixture narrative");

    const poisoned = { ...report, sections: [...report.sections] };
    const first = poisoned.sections[0];
    if (first === undefined) throw new Error("expected at least one section outcome");
    poisoned.sections[0] = { ...first, reason: normalized.text };

    expect(() => {
      assertTextFree(poisoned);
    }).toThrow(ReportTextError);
  });

  it("rejects a long string and a prose-shaped string wherever they hide", () => {
    expect(() => {
      assertTextFree({ nested: [{ value: "x".repeat(65) }] });
    }).toThrow(ReportTextError);
    expect(() => {
      assertTextFree({ nested: { value: "four separate little words" } });
    }).toThrow(ReportTextError);
    expect(() => {
      assertTextFree({ nested: { value: "three-little-words" } });
    }).not.toThrow();
  });

  it("rejects narrative separated by anything but U+0020", () => {
    for (const value of [
      "Adults\t500 mg\tTwice daily",
      "Adults 500 mg Twice daily",
      "Adults\n500 mg\nTwice daily",
    ]) {
      expect(() => {
        assertTextFree({ nested: { value } });
      }).toThrow(ReportTextError);
    }
  });

  it("rejects a short phrase that is under the prose threshold", () => {
    expect(() => {
      assertTextFree({ nested: { value: "hypersensitivity to the active substance" } });
    }).toThrow(ReportTextError);

    const poisoned = { ...report, sections: [...report.sections] };
    const first = poisoned.sections[0];
    if (first === undefined) throw new Error("expected at least one section outcome");
    poisoned.sections[0] = { ...first, reason: "Paracetamol 500 mg" };
    expect(() => {
      assertTextFree(poisoned);
    }).toThrow(ReportTextError);
  });

  it("accepts the shapes a report is allowed to carry", () => {
    expect(() => {
      assertTextFree({
        rule: "body-boundary",
        sourceKey: "smpc.4.1",
        version: "fidelity-norm/1.1.0",
        path: "Composition.section[3].section[1]",
        hash: "a".repeat(64),
        at: "2026-09-20T08:00:00Z",
      });
    }).not.toThrow();
  });
});

describe("character fidelity against the embedded text layer", () => {
  const page = (text: string, bodyStart: number, bodyEnd: number): SourceDocumentText => ({
    extractorVersion: "test/1.0.0",
    pages: [{ page: 1, text, bodyStart, bodyEnd }],
  });

  // A real text layer carries the running header and footer too, so the comparison is whole
  // page against whole page; only the separators the adapter itself added are excluded.
  it("scores a faithful extractor at zero, header and footer included", () => {
    const source = page(`Running title${LF}Body${TAB}cells${LF}Sheet 1`, 14, 25);
    const fidelity = diffCharacters(
      source,
      [{ page: 1, text: `Running title${LF}Body cells${LF}Sheet 1` }],
      3,
    );

    expect(fidelity.pagesCompared).toBe(1);
    expect(fidelity.differingCodePoints).toBe(0);
    expect(fidelity.separatorsAdded).toBe(3);
    expect(fidelity.pages[0]?.multisetAgreementRatio).toBe(1);
    expect(fidelity.pages[0]?.orderedAgreementRatio).toBe(1);
  });

  it("charges a header the extractor dropped to the extractor, not to placement", () => {
    const source = page(`Body${TAB}cells${LF}Sheet 1`, 0, 11);
    const fidelity = diffCharacters(
      source,
      [{ page: 1, text: `Running title${LF}Body cells${LF}Sheet 1` }],
      2,
    );

    // The dropped header plus the one collapsed space that followed it.
    expect(fidelity.onlyInTextLayer).toBe(Array.from("Running title ").length);
    expect(fidelity.onlyInExtractor).toBe(0);
  });

  // Whitespace is normalised, not stripped: a separator represented differently on the two
  // sides costs nothing, but an inter-word space the extractor lost is a real difference.
  it("still catches a dropped inter-word space", () => {
    const source = page(`Bodycells${LF}`, 0, 10);
    const fidelity = diffCharacters(source, [{ page: 1, text: "Body cells" }], 1);

    expect(fidelity.differingCodePoints).toBe(1);
    expect(fidelity.byCategory).toEqual({ Zs: 1 });
  });

  it("reports a difference by Unicode category and nothing else", () => {
    const source = page(`Body${LF}`, 0, 5);
    const fidelity = diffCharacters(source, [{ page: 1, text: "Body." }], 1);

    expect(fidelity.differingCodePoints).toBe(1);
    expect(fidelity.byCategory).toEqual({ Po: 1 });
    expect(fidelity.onlyInTextLayer).toBe(1);
    // Category keys are the only strings this part of a report carries.
    expect(() => {
      assertTextFree(fidelity);
    }).not.toThrow();
  });

  it("sees a reordering the multiset cannot", () => {
    const source = page(`abcd${LF}`, 0, 5);
    const fidelity = diffCharacters(source, [{ page: 1, text: "dcba" }], 1);
    const first = fidelity.pages[0];
    if (first === undefined) throw new Error("expected one compared page");

    expect(first.differingCodePoints).toBe(0);
    expect(first.multisetAgreementRatio).toBe(1);
    expect(first.orderedAgreementComputed).toBe(true);
    expect(first.orderedAgreementRatio).toBeLessThan(1);
    expect(fidelity.orderedAgreementRatio).toBeLessThan(1);
  });
});
