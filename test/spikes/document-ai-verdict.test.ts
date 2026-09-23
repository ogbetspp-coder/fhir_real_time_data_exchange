import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import type { protos } from "@google-cloud/documentai";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  generateSyntheticSmpcPdf,
  type SyntheticPdfManifest,
} from "../../scripts/spikes/document-ai/generate-pdf.js";
import { extractTextLayer, type PageText } from "../../scripts/spikes/document-ai/text-layer.js";
import {
  buildPartAReport,
  syntheticSections,
  type PageCharacterDiff,
  type PartAReport,
} from "../../scripts/spikes/document-ai/report.js";

// The verdict of docs/design/extractor-spike.md, executable. The recorded Layout Parser response
// for the synthetic SmPC PDF is the regression fixture the design promised; this test replays it
// against the regenerated PDF's own embedded text layer and asserts the exact numbers the verdict
// was written from. Nothing here touches the network or credentials: the response is on disk, the
// PDF is generated locally, and the text layer is read by pdf.js.
//
// Assertion messages carry source keys, counts, category names, and hashes only (AGENTS.md; spike
// design, "Constraints"). test/spikes/document-ai-adapter.test.ts covers the adapter's own rules
// on this same recording (the hand-authored response it once used has been removed).

const RECORDED_RESPONSE_PATH = "test/fixtures/spikes/document-ai-layout-response.recorded.json";
const ADAPTER_VERSION = "1.0.0";
const EXPECTED_TIMEOUT_MS = 60_000;
const LIGATURE = "ﬁ";

// The `reportHash` the verdict quotes for Part A.
const VERDICT_FIDELITY_REPORT_HASH =
  "12449bee74d936f9950b27378c2aaa9d3cd04789cae73bd9473878dd8c3a6a46";

type LayoutDocument = protos.google.cloud.documentai.v1.IDocument;

let manifest: SyntheticPdfManifest;
let textLayer: PageText[];
let report: PartAReport;
let directory = "";

beforeAll(async () => {
  const parsed = JSON.parse(await readFile(RECORDED_RESPONSE_PATH, "utf8")) as {
    document: LayoutDocument;
  };
  directory = await mkdtemp(path.join(tmpdir(), "ema-flow-verdict-"));
  manifest = await generateSyntheticSmpcPdf(path.join(directory, "synthetic-smpc.pdf"));
  textLayer = await extractTextLayer(manifest.outputPath);
  report = buildPartAReport({
    document: parsed.document,
    sections: await syntheticSections(),
    adapterVersion: ADAPTER_VERSION,
    textLayer,
  }).report;
}, EXPECTED_TIMEOUT_MS);

afterAll(async () => {
  if (directory.length > 0) await rm(directory, { recursive: true, force: true });
});

function pageDiff(page: number): PageCharacterDiff {
  const found = report.characterFidelity.pages.find((candidate) => candidate.page === page);
  if (found === undefined) throw new Error(`character fidelity has no page ${String(page)}`);
  return found;
}

function countCodePoint(text: string, codePoint: string): number {
  return Array.from(text).filter((character) => character === codePoint).length;
}

describe("recorded Document AI response, the regression fixture", () => {
  it(
    "was recorded for the document this fixture set regenerates",
    () => {
      // If the generator ever drifts, every number below would be measuring a different document
      // against a recording of the old one. The guard is the document's content, never the PDF's
      // bytes: pdfkit's output is byte-identical only on one platform and Node version (the first
      // CI run of this test proved it — same text, different bytes on Linux), so a byte hash would
      // assert something no one promised. The content facts below, and the exact character
      // differences asserted further down, would all move if the document did.
      expect(manifest.pageCount).toBe(3);
      expect(manifest.sectionCount).toBe(32);
      expect(textLayer).toHaveLength(3);
    },
    EXPECTED_TIMEOUT_MS,
  );

  // Proves: Part A of the verdict — "32 of 32 sections located and verified, status `passed`,
  // zero issues". The extractor contract (spec section 7) and the real verifier are satisfiable
  // by a real extractor's output.
  it("verifies 32 of 32 fixture sections through the real verifier", () => {
    expect(report.summary).toEqual({ total: 32, located: 32, verified: 32 });
    expect(report.fidelityStatus).toBe("passed");
    expect(report.issueCount).toBe(0);
    expect(report.sections.filter(({ status }) => status !== "verified")).toEqual([]);
    expect(report.fidelityReportHash).toBe(VERDICT_FIDELITY_REPORT_HASH);
  });

  // Proves: the verdict's structural findings — headers and footers classified on all three
  // pages, the drawn table returned as one table block of 4 rows and 8 cells and serialised
  // row-major, the forced line-end hyphen kept as a glyph and counted rather than rewritten.
  // This is the half of the verdict that says Document AI is a usable structure classifier.
  it("counts the structure the verdict credits Document AI with", () => {
    expect(report.counters.pagesWithHeader).toBe(3);
    expect(report.counters.pagesWithFooter).toBe(3);
    expect(report.counters.tableBlocks).toBe(1);
    expect(report.counters.tableRows).toBe(4);
    expect(report.counters.tableCells).toBe(8);
    expect(report.counters.lineEndHyphens).toBe(1);
  });

  // Proves: the ligature half of the decisive measurement, from the counter's side. The PDF
  // carries exactly one U+FB01 and the text layer reads it back as U+FB01; Document AI's own text
  // has none, because it expanded the glyph to "fi" before the adapter ever saw it.
  it("shows the ligature glyph in the text layer and gone from the extractor", () => {
    expect(report.counters.ligatureGlyphs).toBe(0);
    const inTextLayer = textLayer.reduce(
      (total, page) => total + countCodePoint(page.text, LIGATURE),
      0,
    );
    expect(inTextLayer).toBe(1);
  });
});

describe("character fidelity against the embedded text layer", () => {
  // Proves: the comparison covers the whole document, three pages of extractor text against three
  // pages of text layer, and the extractor is one code point longer overall — the net effect of
  // one U+FB01 becoming two letters with nothing else added or dropped.
  it("compares all three pages and ends one code point long", () => {
    expect(report.characterFidelity.available).toBe(true);
    expect(report.characterFidelity.pagesCompared).toBe(3);
    expect(report.characterFidelity.extractorCodePoints).toBe(4116);
    expect(report.characterFidelity.textLayerCodePoints).toBe(4115);
  });

  // Proves: the differences are not noise spread over the document. Pages 2 and 3 carry no probes
  // and agree perfectly, in content and in order — so reading order is right and the adapter adds
  // nothing of its own. Every difference in this document is on the probe page.
  it("scores the two probe-free pages at zero, in order", () => {
    for (const page of [2, 3]) {
      const diff = pageDiff(page);
      expect([diff.page, diff.differingCodePoints]).toEqual([page, 0]);
      expect([diff.page, diff.orderedAgreementRatio]).toEqual([page, 1]);
    }
  });

  // Proves: the decisive finding. Page 1 differs by exactly 11 code points, and the categories
  // decode without ambiguity:
  //   Pi 2 and Pf 2 lost against Po 4 gained — the four typographic quotes straightened to ASCII;
  //   Ll 3 with the extractor one code point longer — U+FB01 expanded to "fi".
  // Nothing else differs, so no other class of change is hiding in the total. Quote straightening
  // is a change to approved text under spec section 4, which is why the verdict is a no-go for
  // Document AI as the source of characters.
  it("attributes all 11 page-1 differences to straightened quotes and an expanded ligature", () => {
    const page1 = pageDiff(1);
    expect(page1.differingCodePoints).toBe(11);
    expect(page1.onlyInExtractor).toBe(6);
    expect(page1.onlyInTextLayer).toBe(5);
    expect(page1.byCategory).toEqual({ Ll: 3, Po: 4, Pi: 2, Pf: 2 });
    // The document total is the page total: the probe page is the only page that differs.
    expect(report.characterFidelity.differingCodePoints).toBe(11);
    expect(report.characterFidelity.byCategory).toEqual({ Ll: 3, Po: 4, Pi: 2, Pf: 2 });
  });
});
