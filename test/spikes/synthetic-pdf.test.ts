import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../../src/fixtures/synthetic-submission.js";
import {
  generateSyntheticSmpcPdf,
  type SyntheticPdfManifest,
} from "../../scripts/spikes/document-ai/generate-pdf.js";
import { extractTextLayer, type PageText } from "../../scripts/spikes/document-ai/text-layer.js";

// Part A of the extractor spike, closed loop: the synthetic SmPC PDF is generated from the
// fixture and read back through the embedded text layer. Every assertion below compares
// booleans, counts, and source keys. Narrative text is never put into an assertion message,
// a custom matcher message, or a console line (AGENTS.md; spike design, "Constraints").

const EXPECTED_TIMEOUT_MS = 60_000;
const LIGATURE = "ﬁ";

type ExpectedSection = {
  sourceKey: string;
  page: number;
  firstSentence: string;
};

// A section's first sentence: up to and including the first full stop that is followed by a
// space or by the end of the text. The synthetic fixture's sections are one sentence each, so
// this normally returns the whole line; the rule is written out so it stays true if the
// fixture ever grows a second sentence.
function firstSentence(text: string): string {
  const match = /^[\s\S]*?\.(?=\s|$)/.exec(text);
  return match?.[0] ?? text;
}

// Expected page and text are read from the fixture directly, not from the generator, so the
// test compares the PDF against the fixture rather than against the generator's own idea of it.
function expectedSections(mapping: EmaMapping): ExpectedSection[] {
  const { submission, sourceText } = createSyntheticSubmission(mapping);
  const pageText = new Map(sourceText.pages.map((page) => [page.page, Array.from(page.text)]));
  return submission.provenance.sections.map((provenance) => {
    const span = provenance.spans[0];
    if (span === undefined) throw new Error("fixture section without a span");
    const codePoints = pageText.get(span.page);
    if (codePoints === undefined) throw new Error("fixture span refers to an unknown page");
    return {
      sourceKey: provenance.sourceKey,
      page: span.page,
      firstSentence: firstSentence(codePoints.slice(span.startOffset, span.endOffset).join("")),
    };
  });
}

let manifest: SyntheticPdfManifest;
let pages: PageText[];
let sections: ExpectedSection[];
let directory = "";

beforeAll(async () => {
  const mapping = await loadEmaMapping();
  sections = expectedSections(mapping);
  directory = await mkdtemp(path.join(tmpdir(), "ema-flow-spike-"));
  manifest = await generateSyntheticSmpcPdf(path.join(directory, "synthetic-smpc.pdf"));
  pages = await extractTextLayer(manifest.outputPath);
}, EXPECTED_TIMEOUT_MS);

afterAll(async () => {
  if (directory.length > 0) await rm(directory, { recursive: true, force: true });
});

function textOfPage(page: number): string {
  const found = pages.find((candidate) => candidate.page === page);
  if (found === undefined) throw new Error(`extracted text has no page ${page}`);
  return found.text;
}

describe("synthetic SmPC PDF round trip through the embedded text layer", () => {
  it("extracts one text page per page the manifest reports", () => {
    expect(pages.length).toBe(manifest.pageCount);
    expect(pages.map((page) => page.page)).toEqual(
      Array.from({ length: manifest.pageCount }, (_unused, index) => index + 1),
    );
    expect(manifest.runningHeaderPages).toEqual(pages.map((page) => page.page));
    expect(manifest.footerPages).toEqual(pages.map((page) => page.page));
  });

  it("places every fixture section on the page the fixture assigns it", () => {
    expect(sections.length).toBe(manifest.sectionCount);
    const placed = new Map(
      manifest.sectionCodePoints.map((entry) => [entry.sourceKey, entry.page]),
    );
    const misplaced = sections
      .filter((section) => placed.get(section.sourceKey) !== section.page)
      .map((section) => section.sourceKey);
    expect(misplaced).toEqual([]);
  });

  it("carries every fixture section's first sentence verbatim on its expected page", () => {
    // Reported as source keys only: the sentences themselves never reach the failure output.
    const missing = sections
      .filter((section) => !textOfPage(section.page).includes(section.firstSentence))
      .map((section) => section.sourceKey);
    expect(missing).toEqual([]);
    expect(sections.length).toBeGreaterThan(0);

    // A sentence must not also turn up on a page it does not belong to, or "present on the
    // expected page" would be a weaker claim than it reads.
    const strayed = sections
      .filter((section) =>
        pages.some(
          (page) => page.page !== section.page && page.text.includes(section.firstSentence),
        ),
      )
      .map((section) => section.sourceKey);
    expect(strayed).toEqual([]);
  });

  it("keeps the forced line-end hyphen at a line end with its continuation on the next line", () => {
    const lines = textOfPage(manifest.hyphenation.page).split("\n");
    const head = lines.findIndex((line) => line.endsWith(manifest.hyphenation.head));
    expect(head).toBeGreaterThanOrEqual(0);
    expect(lines[head + 1]?.startsWith(manifest.hyphenation.tail)).toBe(true);
    // The hyphen is a real U+002D glyph on the page, not a soft hyphen the writer inserted.
    expect(manifest.hyphenation.head.endsWith("-")).toBe(true);
    expect(textOfPage(manifest.hyphenation.page).includes("­")).toBe(false);
  });

  it("survives the U+FB01 ligature as U+FB01, or records that the font substituted it", () => {
    const text = textOfPage(manifest.ligature.page);
    const survived = text.includes(LIGATURE) && text.includes(manifest.ligature.word);
    if (manifest.font.ligatureGlyphAvailable) {
      // Embedded ligature-capable font: the code point must reach the text layer unchanged.
      expect([survived, manifest.ligature.codePoint]).toEqual([true, "U+FB01"]);
    } else {
      // Documented outcome, not a silent pass: the standard-14 fallback has no U+FB01 glyph,
      // so the generator could not place one and the extracted text cannot contain it.
      expect([survived, manifest.font.embedded]).toEqual([false, false]);
    }
  });

  it("does not normalise typographic quotes away", () => {
    const text = textOfPage(manifest.quotes.page);
    const present = manifest.quotes.codePoints.map((name) =>
      text.includes(String.fromCodePoint(Number.parseInt(name.slice(2), 16))),
    );
    expect(present).toEqual(manifest.quotes.codePoints.map(() => true));
  });

  it("reads a two-column page in column order, not in baseline order", () => {
    const columns = manifest.headings.filter(
      (heading) => heading.page === manifest.twoColumnPage && heading.column === 2,
    );
    expect(columns.length).toBeGreaterThan(0);
    // Positions are taken from the section sentences, which the generator's column font-size
    // fit keeps on one line; a QRD heading may wrap inside a narrow column and then has no
    // single position to compare.
    const column = new Map(manifest.headings.map((heading) => [heading.sourceKey, heading.column]));
    const order = sections
      .filter((section) => section.page === manifest.twoColumnPage)
      .map((section) => ({
        column: column.get(section.sourceKey) ?? 0,
        at: textOfPage(manifest.twoColumnPage).indexOf(section.firstSentence),
      }));
    expect(order.every((entry) => entry.at >= 0)).toBe(true);
    // Every column-1 heading appears before every column-2 heading in the extracted text.
    const lastOfFirst = Math.max(
      ...order.filter((entry) => entry.column === 1).map((entry) => entry.at),
    );
    const firstOfSecond = Math.min(
      ...order.filter((entry) => entry.column === 2).map((entry) => entry.at),
    );
    expect(lastOfFirst < firstOfSecond).toBe(true);
  });

  it("renders the table as cells rather than as one run of text", () => {
    const text = textOfPage(manifest.table.page);
    const rows = text.split("\n").filter((line) => /^smpc\.\d[\d.]* \d{12}$/.test(line)).length;
    expect(rows).toBe(manifest.table.rowCount - 1);
    expect(manifest.table.columnCount).toBe(2);
  });

  it(
    "is deterministic: a second generation produces the same bytes",
    async () => {
      const again = await generateSyntheticSmpcPdf(path.join(directory, "again.pdf"));
      expect(again.sha256).toBe(manifest.sha256);
      expect(again.byteLength).toBe(manifest.byteLength);
    },
    EXPECTED_TIMEOUT_MS,
  );
});
