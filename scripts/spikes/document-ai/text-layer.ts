import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as pdfjs from "pdfjs-dist/legacy/build/pdf.mjs";

// The character-fidelity reference for the extractor spike (docs/design/extractor-spike.md,
// "The measurement that decides most"). For a born-digital PDF the truth is the embedded text
// layer, so this module reads that layer and changes nothing about it: no whitespace
// collapsing, no ligature expansion, no de-hyphenation, no NFC. It is deliberately not an
// implementation of the extractor contract in docs/fidelity-normalization.md section 7 — it
// declares no body range, emits no U+00AD, and does not serialise tables with TAB. It is only
// the character ground truth that Document AI's text is diffed against.

export const TEXT_LAYER_VERSION = "spike-text-layer/1.0.0";

export type PageText = {
  page: number;
  text: string;
};

// Two baselines are the same line when they differ by less than this many PDF units. pdfkit
// writes every line of a paragraph at an exact baseline, so the tolerance only absorbs the
// floating-point noise of the text matrix.
const BASELINE_EPSILON = 0.1;

type Positioned = {
  str: string;
  x: number;
  y: number;
};

/**
 * The join rule, stated exactly, because everything downstream is a diff against its output.
 *
 * 1. Items are taken in the order pdf.js yields them, which is the order the content stream
 *    draws them. That order — not geometry — is the reading order: it is what puts a
 *    two-column page's left column before its right column even though the two columns share
 *    baselines. Marked-content entries are not requested, so every entry is a text item.
 * 2. Items whose `str` is empty are dropped. pdf.js emits zero-width end-of-line markers whose
 *    baseline already equals that of the item that follows, so dropping them removes no
 *    character and changes no line boundary.
 * 3. A new line starts when either
 *      a. the item's baseline y differs from the previous kept item's by more than
 *         BASELINE_EPSILON, or
 *      b. the baseline is unchanged but the item starts strictly to the left of where the
 *         previous item started (a backward jump on one baseline, which is a new column or a
 *         new line, never a continuation).
 *    Otherwise the item continues the current line.
 * 4. Items on one line are concatenated with nothing between them. pdf.js already materialises
 *    a horizontal gap as an item whose `str` is a single U+0020 (this is how the drawn table's
 *    cells arrive), so inserting anything here would be adding a character the page does not
 *    have. Lines are joined with a single U+000A. No terminator is appended after the last
 *    line, and no leading or trailing whitespace is trimmed.
 *
 * What this rule does not do, on purpose: it does not detect the running header or the footer
 * (they are simply the first and last lines), it does not reorder anything by position, and it
 * does not merge a line that a PDF happens to draw in two pieces. A page drawn in an order that
 * is not its reading order will come out in the drawn order; that is a property of the document
 * and the spike should see it rather than have it hidden.
 */
function joinItems(items: Positioned[]): string {
  const lines: string[] = [];
  let current = "";
  let previous: Positioned | undefined;

  for (const item of items) {
    const startsNewLine =
      previous !== undefined &&
      (Math.abs(item.y - previous.y) > BASELINE_EPSILON || item.x < previous.x);
    if (startsNewLine) {
      lines.push(current);
      current = "";
    }
    current += item.str;
    previous = item;
  }
  if (previous !== undefined) lines.push(current);
  return lines.join("\n");
}

/**
 * Extract the embedded text layer of every page, in page order.
 *
 * `disableNormalization: true` is essential and not a tuning knob: with pdf.js's default
 * normalisation on, U+FB01 is expanded to "fi" and some spacing is rewritten, which would make
 * this module silently commit exactly the offence it exists to detect.
 */
export async function extractTextLayer(pdfPath: string): Promise<PageText[]> {
  const bytes = await readFile(pdfPath);
  // pdf.js transfers (and neuters) the buffer it is given, so it gets a copy of its own.
  const data = new Uint8Array(bytes.byteLength);
  data.set(bytes);

  const loadingTask = pdfjs.getDocument({
    data,
    // No system-font fallback and no network fetches: the document must be read exactly as the
    // bytes on disk describe it, with the font it embeds.
    useSystemFonts: false,
    useWorkerFetch: false,
  });
  const document = await loadingTask.promise;
  try {
    const pages: PageText[] = [];
    for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
      const page = await document.getPage(pageNumber);
      const content = await page.getTextContent({
        disableNormalization: true,
        includeMarkedContent: false,
      });
      const positioned: Positioned[] = [];
      for (const item of content.items) {
        if (!("str" in item)) continue;
        if (item.str === "") continue;
        // `transform` is typed as `any[]` by pdf.js; read it as unknown and narrow.
        const transform = item.transform as unknown[];
        const x = transform[4];
        const y = transform[5];
        if (typeof x !== "number" || typeof y !== "number") continue;
        positioned.push({ str: item.str, x, y });
      }
      pages.push({ page: pageNumber, text: joinItems(positioned) });
      page.cleanup();
    }
    return pages;
  } finally {
    await loadingTask.destroy();
  }
}

async function main(): Promise<void> {
  const target = process.argv[2];
  if (target === undefined)
    throw new Error("Usage: tsx scripts/spikes/document-ai/text-layer.ts <pdf>");
  const pages = await extractTextLayer(target);
  // Counts only: the text itself is the thing this module must never print.
  console.log(
    JSON.stringify(
      {
        textLayerVersion: TEXT_LAYER_VERSION,
        pageCount: pages.length,
        pages: pages.map(({ page, text }) => ({
          page,
          codePoints: Array.from(text).length,
          lines: text.split("\n").length,
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
