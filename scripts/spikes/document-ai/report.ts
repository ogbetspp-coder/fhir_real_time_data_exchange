// The spike's report, in two modes (docs/design/extractor-spike.md, "Two parts, one verdict").
//
//   Part A (default)          npx tsx scripts/spikes/document-ai/report.ts [--response <path>]
//   Part B (--characterise)   npx tsx scripts/spikes/document-ai/report.ts --characterise <pdf>
//
// Part A adapts a recorded Document AI response for the synthetic PDF, locates every fixture
// section's narrative in the adapted pages, and runs `verifyNarrativeFidelity`. Part B adapts a
// recorded response for any PDF and emits counts only.
//
// Safety by construction: both modes build one plain object and pass it through
// `assertTextFree` before anything is printed or written. That function rejects any string in
// the report longer than 64 code points, any string containing a run of four or more words
// separated by any whitespace (which is what a sentence of narrative looks like), and — the
// rule that does the real work — any string that is not on a closed allowlist of shapes: a
// token of `[A-Za-z0-9._:/+[]-]`, a 64-hex hash, or an ISO timestamp. Rule names, status codes,
// section keys, versions, FHIRPath-shaped paths, and hashes all pass; anything carrying a space,
// a tab, or a character prose needs does not. Nothing is printed except the validated report and
// the path it was written to.

import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import type { protos } from "@google-cloud/documentai";

import type { SectionProvenance, SourceSpan } from "../../../src/contracts/index.js";
import {
  NORMALIZATION_VERSION,
  collectNarrativeSections,
  normalizeNarrative,
  normalizeText,
  verifyNarrativeFidelity,
  type FidelityReport,
  type NarrativeSection,
  type SourceDocumentText,
  type SourcePage,
} from "../../../src/fidelity/index.js";
import { loadEmaMapping } from "../../../src/fhir/mapping.js";
import { isComposition } from "../../../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../../../src/fixtures/synthetic.js";
import { sha256Utf8 } from "../../../src/lib/hash.js";
import {
  adapt,
  characterise,
  processorVersion,
  processorVersionId,
  type AdapterCounters,
  type PageFacts,
} from "./adapt.js";

export const ADAPTER_VERSION = "1.0.0";

const CACHE_DIR = path.join(".cache", "spikes", "document-ai");
const REPORT_PATH = path.join(CACHE_DIR, "report.json");
const DEFAULT_PDF = path.join(CACHE_DIR, "synthetic-smpc.pdf");
const MAX_REPORT_STRING_CODE_POINTS = 64;

// Four or more runs of non-whitespace separated by whitespace: the shape of a sentence. Every
// whitespace class counts, not U+0020 alone — narrative joined by TAB, NBSP, or a line feed is
// still narrative.
const PROSE_PATTERN = /\S+(?:\s+\S+){3,}/u;

// The closed list of shapes a report string may take. Nothing here can hold a space, so nothing
// here can hold a phrase.
//   - a token: rule names, statuses, source keys (`smpc.4.1`), versions (`fidelity-norm/2.0.0`),
//     paths (`Composition.section[3].section[1]`), block-type keys, Unicode category names;
//   - a 64-hex SHA-256 digest;
//   - an ISO 8601 UTC timestamp.
const TOKEN_PATTERN = /^[A-Za-z0-9._:/+[\]-]{1,64}$/u;
const HASH_PATTERN = /^[0-9a-f]{64}$/u;
const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/u;

type LayoutDocument = protos.google.cloud.documentai.v1.IDocument;

// ---------------------------------------------------------------------------------------------
// assertTextFree
// ---------------------------------------------------------------------------------------------

export class ReportTextError extends Error {
  public constructor(
    public readonly location: string,
    public readonly rule: "string-too-long" | "prose-shaped-string" | "not-allowlisted",
  ) {
    super(`Report value at ${location} violates ${rule}`);
    this.name = "ReportTextError";
  }
}

// A usage failure whose message is written here, from fixed words plus at most a hash and a
// fixed path, and is therefore safe to print. Every other error prints its class name only.
export class SpikeUsageError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "SpikeUsageError";
  }
}

function isAllowlisted(value: string): boolean {
  return HASH_PATTERN.test(value) || ISO_TIMESTAMP_PATTERN.test(value) || TOKEN_PATTERN.test(value);
}

function checkString(value: string, location: string): void {
  if (Array.from(value).length > MAX_REPORT_STRING_CODE_POINTS) {
    throw new ReportTextError(location, "string-too-long");
  }
  if (PROSE_PATTERN.test(value)) throw new ReportTextError(location, "prose-shaped-string");
  if (!isAllowlisted(value)) throw new ReportTextError(location, "not-allowlisted");
}

// Walks a report and throws if any string value — or any object key — could be carrying text.
// The error names the location and the rule, never the offending value.
export function assertTextFree(report: unknown, location = "$"): void {
  if (typeof report === "string") {
    checkString(report, location);
    return;
  }
  if (Array.isArray(report)) {
    report.forEach((item, index) => {
      assertTextFree(item, `${location}[${String(index)}]`);
    });
    return;
  }
  if (typeof report === "object" && report !== null) {
    for (const [key, value] of Object.entries(report)) {
      checkString(key, `${location}.<key>`);
      assertTextFree(value, `${location}.${key}`);
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Locating narrative in adapted page text
// ---------------------------------------------------------------------------------------------

// The normalisation of docs/fidelity-normalization.md section 3, applied with provenance so a
// normalised match can be turned back into raw code-point offsets. It deliberately mirrors
// src/fidelity/normalize.ts rather than importing its private tables (the spike does not modify
// src/fidelity/), and it omits NFC, which cannot be tracked per code point. Both simplifications
// are safe in one direction only: a candidate span is accepted only after `normalizeText` of the
// raw slice is compared against the target, so a divergence here can lose a section, never
// fabricate a match.
const INVISIBLE_FORMATTING = new Set([0x00ad, 0x200b, 0xfeff, 0x2060]);

const LIGATURES = new Map<number, string>([
  [0xfb00, "ff"],
  [0xfb01, "fi"],
  [0xfb02, "fl"],
  [0xfb03, "ffi"],
  [0xfb04, "ffl"],
  [0xfb06, "st"],
]);

// The spec's step 4 list. The spec also replaces a bullet only at a line start and before
// whitespace; this locator replaces it everywhere, which can lose a candidate, never fabricate
// one (every candidate is re-checked with `normalizeText`, above).
const BULLET_GLYPHS = new Set([
  0x2022, 0x2023, 0x25a0, 0x25a1, 0x25aa, 0x25ab, 0x25cb, 0x25cf, 0x25e6,
]);

const WHITESPACE = new Set([
  0x0009, 0x000a, 0x000d, 0x0020, 0x00a0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

function isWhitespace(codePoint: number): boolean {
  return WHITESPACE.has(codePoint) || (codePoint >= 0x2000 && codePoint <= 0x200a);
}

type BodyIndex = {
  page: number;
  raw: string[];
  normalized: string[];
  offsets: number[];
};

function indexBody(page: SourcePage): BodyIndex {
  const raw = Array.from(page.text);
  const body = raw.slice(page.bodyStart, page.bodyEnd);
  const normalized: string[] = [];
  const offsets: number[] = [];

  for (let position = 0; position < body.length; position += 1) {
    const character = body[position] ?? "";
    const codePoint = character.codePointAt(0) ?? 0;
    if (INVISIBLE_FORMATTING.has(codePoint)) {
      if (codePoint === 0x00ad) {
        if (body[position + 1] === "\r" && body[position + 2] === "\n") position += 2;
        else if (body[position + 1] === "\n") position += 1;
      }
      continue;
    }
    const expansion = LIGATURES.get(codePoint);
    if (expansion !== undefined) {
      for (const expanded of expansion) {
        normalized.push(expanded);
        offsets.push(position);
      }
      continue;
    }
    if (BULLET_GLYPHS.has(codePoint) || isWhitespace(codePoint)) {
      if (normalized[normalized.length - 1] === " ") continue;
      normalized.push(" ");
      offsets.push(position);
      continue;
    }
    normalized.push(character);
    offsets.push(position);
  }

  let start = 0;
  let end = normalized.length;
  while (start < end && normalized[start] === " ") start += 1;
  while (end > start && normalized[end - 1] === " ") end -= 1;

  return {
    page: page.page,
    raw,
    normalized: normalized.slice(start, end),
    offsets: offsets.slice(start, end),
  };
}

function matchesAt(haystack: string[], needle: string[], at: number): boolean {
  for (let position = 0; position < needle.length; position += 1) {
    if (haystack[at + position] !== needle[position]) return false;
  }
  return true;
}

function findCodePoints(haystack: string[], needle: string[]): number {
  if (needle.length === 0) return -1;
  for (let at = 0; at + needle.length <= haystack.length; at += 1) {
    if (matchesAt(haystack, needle, at)) return at;
  }
  return -1;
}

// One span for the normalised `target` in one adapted page, or undefined. The span is built over
// raw code-point offsets and is returned only when the raw slice normalises back to the target.
export function locateSpan(page: SourcePage, target: string): SourceSpan | undefined {
  const index = indexBody(page);
  const needle = Array.from(target);
  const found = findCodePoints(index.normalized, needle);
  if (found < 0) return undefined;

  const firstOffset = index.offsets[found];
  const lastOffset = index.offsets[found + needle.length - 1];
  if (firstOffset === undefined || lastOffset === undefined) return undefined;

  const startOffset = page.bodyStart + firstOffset;
  const endOffset = page.bodyStart + lastOffset + 1;
  const slice = index.raw.slice(startOffset, endOffset).join("");
  if (normalizeText(slice) !== target) return undefined;

  return { page: page.page, startOffset, endOffset, textSha256: sha256Utf8(slice) };
}

export type LocatedSections = {
  provenance: SectionProvenance[];
  locatedCount: number;
  notLocated: string[];
};

// Builds the provenance Part A verifies against: for each fixture section, the span in the
// adapted page text whose raw slice normalises to that section's narrative.
export function locateSections(
  source: SourceDocumentText,
  sections: NarrativeSection[],
): LocatedSections {
  const provenance: SectionProvenance[] = [];
  const notLocated: string[] = [];

  for (const section of sections) {
    const normalized = normalizeNarrative(section.div);
    if (!("text" in normalized)) {
      notLocated.push(section.sourceKey);
      continue;
    }
    let span: SourceSpan | undefined;
    for (const page of source.pages) {
      span = locateSpan(page, normalized.text);
      if (span !== undefined) break;
    }
    if (span === undefined) {
      notLocated.push(section.sourceKey);
      continue;
    }
    provenance.push({
      sourceKey: section.sourceKey,
      spans: [span],
      narrativeDivSha256: sha256Utf8(section.div),
      normalizedTextSha256: sha256Utf8(normalized.text),
    });
  }

  return { provenance, locatedCount: provenance.length, notLocated };
}

// ---------------------------------------------------------------------------------------------
// Character fidelity against the PDF's own embedded text layer
// ---------------------------------------------------------------------------------------------

// scripts/spikes/document-ai/text-layer.ts is another agent's deliverable; at the time of
// writing it exports `extractTextLayer(pdfPath) => Promise<{ page, text }[]>`, which is the
// contract assumed here. It is imported dynamically and shape-checked at run time for two
// reasons that outlast the spike's parallel authorship: Part A runs from a recorded response
// alone, with no PDF and no pdf.js on the machine, and the network-free adapter test must not
// depend on pdf.js loading at all. When the module or the PDF is absent, or its shape is not
// the one below, every mode still runs and reports `characterFidelity.available === false`.
// TODO(spike): collapse this to a static import once the spike's verdict is written and the
// module boundary stops moving.
const TEXT_LAYER_MODULE = "./text-layer.js";
const TEXT_LAYER_EXPORTS = [
  "extractTextLayer",
  "extractTextLayerPages",
  "textLayerPages",
  "default",
];

type TextLayerPage = { page: number; text: string };
type TextLayerReader = (pdfPath: string) => Promise<TextLayerPage[]>;

const GENERAL_CATEGORIES = [
  "Lu",
  "Ll",
  "Lt",
  "Lm",
  "Lo",
  "Mn",
  "Mc",
  "Me",
  "Nd",
  "Nl",
  "No",
  "Pc",
  "Pd",
  "Ps",
  "Pe",
  "Pi",
  "Pf",
  "Po",
  "Sm",
  "Sc",
  "Sk",
  "So",
  "Zs",
  "Zl",
  "Zp",
  "Cc",
  "Cf",
  "Co",
];

const CATEGORY_TESTS = GENERAL_CATEGORIES.map((name) => ({
  name,
  pattern: new RegExp(`^\\p{General_Category=${name}}$`, "u"),
}));

function categoryOf(character: string): string {
  return CATEGORY_TESTS.find(({ pattern }) => pattern.test(character))?.name ?? "Cn";
}

// Differences on one page, by Unicode general category. `byCategory` keys are the two-letter
// category names; there is no key for a character and no character anywhere in this type.
//
// Two measures, because a multiset comparison cannot see reading order: `multisetAgreementRatio`
// is 1 when the two sides hold the same characters in any order, and `orderedAgreementRatio` is
// the longest common subsequence of the two body texts over the longer of them, which falls
// below 1 exactly when the extractor emitted the same characters in a different order.
export type PageCharacterDiff = {
  page: number;
  extractorCodePoints: number;
  textLayerCodePoints: number;
  differingCodePoints: number;
  onlyInExtractor: number;
  onlyInTextLayer: number;
  multisetAgreementRatio: number;
  orderedAgreementRatio: number;
  orderedAgreementComputed: boolean;
  byCategory: Record<string, number>;
};

export type CharacterFidelity = {
  available: boolean;
  pagesCompared: number;
  pagesOnlyInExtractor: number;
  pagesOnlyInTextLayer: number;
  extractorCodePoints: number;
  textLayerCodePoints: number;
  differingCodePoints: number;
  onlyInExtractor: number;
  onlyInTextLayer: number;
  multisetAgreementRatio: number;
  orderedAgreementRatio: number;
  // U+0009 and U+000A the adapter itself introduced. They are excluded from both sides of every
  // comparison above, so they are reported here instead of being counted as a difference.
  separatorsAdded: number;
  byCategory: Record<string, number>;
  pages: PageCharacterDiff[];
};

function unavailableFidelity(): CharacterFidelity {
  return {
    available: false,
    pagesCompared: 0,
    pagesOnlyInExtractor: 0,
    pagesOnlyInTextLayer: 0,
    extractorCodePoints: 0,
    textLayerCodePoints: 0,
    differingCodePoints: 0,
    onlyInExtractor: 0,
    onlyInTextLayer: 0,
    multisetAgreementRatio: 0,
    orderedAgreementRatio: 0,
    separatorsAdded: 0,
    byCategory: {},
    pages: [],
  };
}

async function importModule(specifier: string): Promise<unknown> {
  return (await import(specifier)) as unknown;
}

function isTextLayerPages(value: unknown): value is TextLayerPage[] {
  return (
    Array.isArray(value) &&
    value.every(
      (entry) =>
        typeof entry === "object" &&
        entry !== null &&
        typeof (entry as { page?: unknown }).page === "number" &&
        typeof (entry as { text?: unknown }).text === "string",
    )
  );
}

async function loadTextLayer(pdfPath: string): Promise<TextLayerPage[] | undefined> {
  let module: unknown;
  try {
    module = await importModule(TEXT_LAYER_MODULE);
  } catch {
    return undefined;
  }
  if (typeof module !== "object" || module === null) return undefined;
  const exported = module as Record<string, unknown>;
  const reader = TEXT_LAYER_EXPORTS.map((name) => exported[name]).find(
    (candidate) => typeof candidate === "function",
  ) as TextLayerReader | undefined;
  if (reader === undefined) return undefined;
  try {
    const pages: unknown = await reader(pdfPath);
    return isTextLayerPages(pages) ? pages : undefined;
  } catch {
    return undefined;
  }
}

function countCodePoints(characters: readonly string[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const character of characters) counts.set(character, (counts.get(character) ?? 0) + 1);
  return counts;
}

// The separators the adapter introduces are never part of a comparison: they are the adapter's
// own, they exist on one side only by construction, and they are reported as `separatorsAdded`.
// Whitespace class of docs/fidelity-normalization.md section 3 step 5, replicated here because
// the spike must not reach into src/fidelity's private tables and must not apply the rest of
// the normaliser (ligature expansion and NFC would hide exactly the differences it measures).
const WHITESPACE_CLASS = new Set([
  0x0009, 0x000a, 0x000d, 0x0020, 0x00a0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

function isWhitespaceClass(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  return WHITESPACE_CLASS.has(codePoint) || (codePoint >= 0x2000 && codePoint <= 0x200a);
}

// The two sides represent separators differently — the adapter emits U+0009 between cells and
// U+000A between blocks, pdf.js emits U+0020 for a wide gap and U+000A between lines — so raw
// whitespace cannot be compared. Stripping it all would hide a dropped inter-word space, which
// is a real fidelity defect. So both sides get step 5 of the normaliser and nothing else: every
// whitespace-class run becomes one U+0020, leading and trailing runs go. Representation
// differences then cost nothing; a lost or invented space still costs one.
function comparableCodePoints(text: string): string[] {
  const output: string[] = [];
  let pendingSpace = false;
  for (const character of text) {
    if (isWhitespaceClass(character)) {
      pendingSpace = output.length > 0;
      continue;
    }
    if (pendingSpace) output.push(" ");
    pendingSpace = false;
    output.push(character);
  }
  return output;
}

// Longest common subsequence length, rolling row. Page bodies are a few thousand code points, so
// O(n·m) is affordable; beyond the cap the measure is skipped rather than allowed to dominate the
// run, and the page says so.
const MAX_ORDERED_COMPARISON_CODE_POINTS = 20000;

export function longestCommonSubsequence(
  left: readonly string[],
  right: readonly string[],
): number {
  if (left.length === 0 || right.length === 0) return 0;
  let previous = new Int32Array(right.length + 1);
  let current = new Int32Array(right.length + 1);
  for (let row = 1; row <= left.length; row += 1) {
    const leftCharacter = left[row - 1];
    for (let column = 1; column <= right.length; column += 1) {
      current[column] =
        leftCharacter === right[column - 1]
          ? (previous[column - 1] ?? 0) + 1
          : Math.max(previous[column] ?? 0, current[column - 1] ?? 0);
    }
    const swap = previous;
    previous = current;
    current = swap;
  }
  return previous[right.length] ?? 0;
}

function ratio(part: number, whole: number): number {
  if (whole <= 0) return 1;
  return Math.round((part / whole) * 10000) / 10000;
}

// Per page, the difference between the adapter's *body* text and the embedded text layer,
// grouped by Unicode general category, plus an order-sensitive agreement ratio. Counts only: the
// differing characters are never reported. Only [bodyStart, bodyEnd) is compared — the header and
// footer the adapter deliberately places outside the body are not a character-fidelity failure —
// and U+0009 / U+000A are excluded from both sides.
export function diffCharacters(
  source: SourceDocumentText,
  textLayer: TextLayerPage[],
  separatorsAdded: number,
): CharacterFidelity {
  const fidelity = unavailableFidelity();
  fidelity.available = true;
  fidelity.separatorsAdded = separatorsAdded;
  const layerByPage = new Map(textLayer.map((entry) => [entry.page, entry.text]));
  let orderedCommon = 0;
  let orderedLongest = 0;

  for (const page of source.pages) {
    const layerText = layerByPage.get(page.page);
    if (layerText === undefined) {
      fidelity.pagesOnlyInExtractor += 1;
      continue;
    }
    fidelity.pagesCompared += 1;
    // Whole page on both sides, never body against page: the text layer has no body range, so
    // a body-only comparison would charge every real document's running header and footer as
    // characters "only in the text layer" and the headline number would be wrong in exactly the
    // way it is meant to detect. The adapter places header text first and footer text last, so
    // a faithful extractor scores zero here — and one that drops a header is caught, because
    // those characters then really are only in the text layer.
    const left = comparableCodePoints(page.text);
    const right = comparableCodePoints(layerText);
    const extracted = countCodePoints(left);
    const embedded = countCodePoints(right);
    const perPage: PageCharacterDiff = {
      page: page.page,
      extractorCodePoints: left.length,
      textLayerCodePoints: right.length,
      differingCodePoints: 0,
      onlyInExtractor: 0,
      onlyInTextLayer: 0,
      multisetAgreementRatio: 1,
      orderedAgreementRatio: 0,
      orderedAgreementComputed: false,
      byCategory: {},
    };
    for (const character of new Set([...extracted.keys(), ...embedded.keys()])) {
      const inExtractor = extracted.get(character) ?? 0;
      const inLayer = embedded.get(character) ?? 0;
      if (inExtractor === inLayer) continue;
      const difference = Math.abs(inExtractor - inLayer);
      const category = categoryOf(character);
      perPage.differingCodePoints += difference;
      perPage.onlyInExtractor += Math.max(inExtractor - inLayer, 0);
      perPage.onlyInTextLayer += Math.max(inLayer - inExtractor, 0);
      perPage.byCategory[category] = (perPage.byCategory[category] ?? 0) + difference;
      fidelity.differingCodePoints += difference;
      fidelity.onlyInExtractor += Math.max(inExtractor - inLayer, 0);
      fidelity.onlyInTextLayer += Math.max(inLayer - inExtractor, 0);
      fidelity.byCategory[category] = (fidelity.byCategory[category] ?? 0) + difference;
    }
    perPage.multisetAgreementRatio = ratio(
      left.length + right.length - perPage.differingCodePoints,
      left.length + right.length,
    );

    const longest = Math.max(left.length, right.length);
    if (longest <= MAX_ORDERED_COMPARISON_CODE_POINTS) {
      const common = longestCommonSubsequence(left, right);
      perPage.orderedAgreementRatio = ratio(common, longest);
      perPage.orderedAgreementComputed = true;
      orderedCommon += common;
      orderedLongest += longest;
    }

    fidelity.extractorCodePoints += left.length;
    fidelity.textLayerCodePoints += right.length;
    fidelity.pages.push(perPage);
  }

  fidelity.multisetAgreementRatio = ratio(
    fidelity.extractorCodePoints + fidelity.textLayerCodePoints - fidelity.differingCodePoints,
    fidelity.extractorCodePoints + fidelity.textLayerCodePoints,
  );
  fidelity.orderedAgreementRatio = ratio(orderedCommon, orderedLongest);

  const adapted = new Set(source.pages.map(({ page }) => page));
  fidelity.pagesOnlyInTextLayer = textLayer.filter(({ page }) => !adapted.has(page)).length;
  return fidelity;
}

// ---------------------------------------------------------------------------------------------
// Part A
// ---------------------------------------------------------------------------------------------

export type SectionOutcome = {
  sourceKey: string;
  path: string;
  status: string;
  reason: string;
  spanCount: number;
};

export type PageBoundary = {
  page: number;
  pageCodePoints: number;
  bodyStart: number;
  bodyEnd: number;
  excludedCodePoints: number;
  bodyStartOnLineBoundary: boolean;
  bodyEndOnLineBoundary: boolean;
};

export type PartAReport = {
  mode: "part-a";
  adapterVersion: string;
  normalizationVersion: string;
  processorVersionId: string;
  extractorVersionSha256: string;
  fidelityStatus: "passed" | "failed";
  fidelityReportHash: string;
  issueCount: number;
  summary: { total: number; located: number; verified: number };
  sections: SectionOutcome[];
  pages: PageBoundary[];
  coverage: FidelityReport["coverage"];
  counters: AdapterCounters;
  characterFidelity: CharacterFidelity;
};

// Spec section 1: bodyStart is 0 or immediately follows U+000A; bodyEnd is the page length or
// the code point before it is U+000A.
export function pageBoundaries(source: SourceDocumentText): PageBoundary[] {
  return source.pages.map((page) => {
    const points = Array.from(page.text);
    return {
      page: page.page,
      pageCodePoints: points.length,
      bodyStart: page.bodyStart,
      bodyEnd: page.bodyEnd,
      excludedCodePoints: points.length - (page.bodyEnd - page.bodyStart),
      bodyStartOnLineBoundary: page.bodyStart === 0 || points[page.bodyStart - 1] === "\n",
      bodyEndOnLineBoundary: page.bodyEnd === points.length || points[page.bodyEnd - 1] === "\n",
    };
  });
}

export async function syntheticSections(): Promise<NarrativeSection[]> {
  const mapping = await loadEmaMapping();
  const bundle = createSyntheticType2Bundle(mapping);
  const composition = bundle.entry[0]?.resource;
  if (composition === undefined || !isComposition(composition)) {
    throw new Error("Synthetic Type 2 fixture must have Composition as its first entry");
  }
  return collectNarrativeSections(composition, mapping.sourceCodeSystem);
}

export type PartAInput = {
  document: LayoutDocument;
  sections: NarrativeSection[];
  adapterVersion?: string | undefined;
  textLayer?: TextLayerPage[] | undefined;
};

export function buildPartAReport(input: PartAInput): {
  report: PartAReport;
  fidelity: FidelityReport;
  source: SourceDocumentText;
} {
  const adapterVersion = input.adapterVersion ?? ADAPTER_VERSION;
  const { source, counters } = adapt(input.document, adapterVersion);
  const located = locateSections(source, input.sections);
  const fidelity = verifyNarrativeFidelity({
    normalizationVersion: NORMALIZATION_VERSION,
    source,
    sections: input.sections,
    provenance: located.provenance,
  });

  const notLocated = new Set(located.notLocated);
  const report: PartAReport = {
    mode: "part-a",
    adapterVersion,
    normalizationVersion: NORMALIZATION_VERSION,
    processorVersionId: processorVersionId(input.document),
    extractorVersionSha256: sha256Utf8(processorVersion(input.document)),
    fidelityStatus: fidelity.status,
    fidelityReportHash: fidelity.reportHash,
    // The verifier's `issues` are English sentences, so the report carries their count only.
    issueCount: fidelity.issues.length,
    summary: {
      total: fidelity.summary.total,
      located: located.locatedCount,
      verified: fidelity.summary.verified,
    },
    sections: fidelity.sections.map((section) => ({
      sourceKey: section.sourceKey,
      path: section.path,
      status: section.status,
      reason: notLocated.has(section.sourceKey) ? "not-located" : (section.reason ?? "none"),
      spanCount: section.spanCount,
    })),
    pages: pageBoundaries(source),
    coverage: fidelity.coverage,
    counters,
    characterFidelity:
      input.textLayer === undefined
        ? unavailableFidelity()
        : diffCharacters(source, input.textLayer, counters.separatorsAdded),
  };

  return { report, fidelity, source };
}

// ---------------------------------------------------------------------------------------------
// Part B
// ---------------------------------------------------------------------------------------------

export type PartBReport = {
  mode: "part-b";
  adapterVersion: string;
  pdfSha256: string;
  processorVersionId: string;
  extractorVersionSha256: string;
  pages: PageFacts[];
  blockTypes: Record<string, number>;
  counters: AdapterCounters;
  characterFidelity: CharacterFidelity;
};

export function buildPartBReport(input: {
  document: LayoutDocument;
  pdfSha256: string;
  adapterVersion?: string | undefined;
  textLayer?: TextLayerPage[] | undefined;
}): PartBReport {
  const adapterVersion = input.adapterVersion ?? ADAPTER_VERSION;
  const { source, counters, pageFacts, blockTypes } = characterise(input.document, adapterVersion);
  return {
    mode: "part-b",
    adapterVersion,
    pdfSha256: input.pdfSha256,
    processorVersionId: processorVersionId(input.document),
    extractorVersionSha256: sha256Utf8(processorVersion(input.document)),
    pages: pageFacts,
    blockTypes,
    counters,
    characterFidelity:
      input.textLayer === undefined
        ? unavailableFidelity()
        : diffCharacters(source, input.textLayer, counters.separatorsAdded),
  };
}

// ---------------------------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------------------------

function flagValue(argv: string[], flag: string): string | undefined {
  const at = argv.indexOf(flag);
  if (at < 0) return undefined;
  return argv[at + 1];
}

async function readDocument(responsePath: string): Promise<LayoutDocument> {
  const content = await readFile(responsePath, "utf8");
  // The recorded file is either a ProcessResponse or the Document it wraps.
  const parsed = JSON.parse(content) as { document?: LayoutDocument } & LayoutDocument;
  return parsed.document ?? parsed;
}

// The most recently written recorded response, used when `--response` is not given. Response
// files are named by the SHA-256 of their PDF, so modification time is the only ordering that
// means anything.
async function newestResponse(): Promise<string> {
  const entries = await readdir(CACHE_DIR);
  const dated = await Promise.all(
    entries
      .filter((entry) => entry.endsWith(".response.json"))
      .map(async (entry) => {
        const full = path.join(CACHE_DIR, entry);
        return { full, modified: (await stat(full)).mtimeMs };
      }),
  );
  const chosen = dated.sort((left, right) => left.modified - right.modified).at(-1);
  if (chosen === undefined) {
    throw new Error("No recorded Document AI response found; run extract.ts first");
  }
  return chosen.full;
}

async function writeReport(report: unknown): Promise<void> {
  assertTextFree(report);
  await mkdir(CACHE_DIR, { recursive: true });
  await writeFile(REPORT_PATH, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(report, null, 2));
  console.log(`reportPath=${REPORT_PATH}`);
}

// `--characterise` takes the PDF to characterise and has no default: Part B is about a document
// nobody controlled, and silently falling back to the synthetic PDF would produce a Part B report
// that looks like a real-world characterisation and is not one.
function characterisePdfArgument(argv: string[]): string | undefined {
  if (!argv.includes("--characterise")) return undefined;
  const value = flagValue(argv, "--characterise");
  if (value === undefined || value.startsWith("--")) {
    throw new SpikeUsageError("--characterise requires the path of the PDF to characterise");
  }
  return value;
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  const characterisePdf = characterisePdfArgument(argv);

  if (characterisePdf !== undefined) {
    const content = await readFile(characterisePdf);
    const pdfSha256 = createHash("sha256").update(content).digest("hex");
    // Named by the PDF's own digest, so the message below carries a hash and a fixed directory
    // and nothing that came out of the document or off the command line.
    const responsePath = path.join(CACHE_DIR, `${pdfSha256}.response.json`);
    if (!existsSync(responsePath)) {
      throw new SpikeUsageError(`no recorded response at ${responsePath}; run extract.ts on it`);
    }
    const document = await readDocument(responsePath);
    const textLayer = await loadTextLayer(characterisePdf);
    await writeReport(
      buildPartBReport({
        document,
        pdfSha256,
        ...(textLayer === undefined ? {} : { textLayer }),
      }),
    );
    return;
  }

  const responsePath = flagValue(argv, "--response") ?? (await newestResponse());
  const document = await readDocument(responsePath);
  const pdfPath = flagValue(argv, "--pdf") ?? DEFAULT_PDF;
  const textLayer = await loadTextLayer(pdfPath);
  const { report } = buildPartAReport({
    document,
    sections: await syntheticSections(),
    ...(textLayer === undefined ? {} : { textLayer }),
  });
  await writeReport(report);
}

if (process.argv[1] !== undefined && process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    await main();
  } catch (error) {
    // A ReportTextError's message is a location and a rule name, and a SpikeUsageError's is
    // fixed words plus a hash-named path; both are safe to print. Any other error's message
    // could in principle quote the input, so only its class name is printed.
    if (error instanceof ReportTextError || error instanceof SpikeUsageError) {
      console.error(`report.ts failed: ${error.message}`);
    } else {
      console.error(`report.ts failed: ${error instanceof Error ? error.name : "unknown-error"}`);
    }
    process.exitCode = 1;
  }
}
