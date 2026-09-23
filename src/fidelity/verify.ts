import type { SectionProvenance, SourceSpan } from "../contracts/ingestion-provenance.js";
import { sha256, sha256Utf8 } from "../lib/hash.js";
import {
  NORMALIZATION_VERSION,
  NormalizationError,
  countWords,
  findForbiddenCharacter,
  isWhitespace,
  normalizeText,
} from "./normalize.js";
import { XhtmlError, xhtmlToText } from "./xhtml.js";

// Pure, synchronous verifier for ADR 0003. Per-section problems become statuses in the report;
// only structurally unusable input (wrong normalisation version, duplicate keys, invalid pages)
// throws. Nothing here logs, performs I/O, or places narrative text in its outputs.

export type SourcePage = {
  page: number;
  text: string;
  bodyStart: number;
  bodyEnd: number;
};

export type SourceDocumentText = {
  extractorVersion: string;
  pages: SourcePage[];
};

export type NarrativeSection = {
  sourceKey: string;
  path: string;
  div: string;
};

export type FidelityInput = {
  normalizationVersion: string;
  source: SourceDocumentText;
  sections: NarrativeSection[];
  provenance: SectionProvenance[];
};

export type SectionStatus =
  | "verified"
  | "mismatch"
  | "span-not-found"
  | "invalid-provenance"
  | "missing-provenance"
  | "malformed-narrative";

export type DiffHint = {
  expectedLength: number;
  actualLength: number;
  firstDifferingOffset: number;
  commonSuffixLength: number;
  expectedWordCount: number;
  actualWordCount: number;
  expectedSha256: string;
  actualSha256: string;
};

export type SectionResult = {
  sourceKey: string;
  path: string;
  status: SectionStatus;
  spanCount: number;
  reason?: string | undefined;
  details?: DiffHint | undefined;
  normalizedTextSha256?: string | undefined;
};

export type NarrativeBinding = {
  sourceKey: string;
  normalizedTextSha256: string | null;
};

export type FidelityReport = {
  reportVersion: "1.0.0";
  normalizationVersion: string;
  extractedTextSha256: string;
  narrativeBindingSha256: string;
  status: "passed" | "failed";
  sections: SectionResult[];
  issues: string[];
  summary: { total: number; verified: number };
  coverage: {
    pageCodePoints: number;
    bodyCodePoints: number;
    coveredCodePoints: number;
    uncoveredGaps: number;
  };
  reportHash: string;
};

// Structural view of a Composition section as it arrives from an untrusted submission. Both the
// repository's FhirComposition and a Zod-validated loose object satisfy it.
export type SectionLike = {
  code?:
    | { coding?: { system?: string | undefined; code?: string | undefined }[] | undefined }
    | undefined;
  text?: { div: string } | undefined;
  section?: SectionLike[] | undefined;
};

export class FidelityError extends Error {
  public constructor(
    message: string,
    public readonly issues: string[],
  ) {
    super(message);
    this.name = "FidelityError";
  }
}

// Most text a page may exclude as running header/footer. The body range is declared by the
// extractor, so it is bounded and must sit on line boundaries rather than trusted outright.
const MAX_EXCLUDED_CODE_POINTS_PER_PAGE = 240;

const SOFT_HYPHEN = "\u00ad";

type PageIndex = {
  page: SourcePage;
  codePoints: string[];
  malformed: boolean;
  bodyIssue: string | undefined;
};

// True when the line break that ends just before `offset` follows U+00AD: normalisation step 1
// deletes such a break, so it is inside a word, not between words.
function softHyphenBreakBefore(codePoints: string[], offset: number): boolean {
  if (codePoints[offset - 1] !== "\n") return false;
  const before = codePoints[offset - 2] === "\r" ? offset - 3 : offset - 2;
  return codePoints[before] === SOFT_HYPHEN;
}

function bodyIssueFor(page: SourcePage, codePoints: string[]): string | undefined {
  const { bodyStart, bodyEnd } = page;
  if (
    bodyStart !== 0 &&
    (codePoints[bodyStart - 1] !== "\n" || softHyphenBreakBefore(codePoints, bodyStart))
  ) {
    return "body-boundary";
  }
  // A non-empty body always ends with its own line terminator, even at the end of the page:
  // otherwise the last word of one page and the first word of the next would read as one.
  if (bodyEnd !== bodyStart && codePoints[bodyEnd - 1] !== "\n") return "body-boundary";
  if (codePoints.length - (bodyEnd - bodyStart) > MAX_EXCLUDED_CODE_POINTS_PER_PAGE) {
    return "excluded-text";
  }
  return undefined;
}

function indexPages(source: SourceDocumentText): {
  pages: Map<number, PageIndex>;
  issues: string[];
} {
  const pages = new Map<number, PageIndex>();
  const structural: string[] = [];
  const issues: string[] = [];
  for (const [position, page] of source.pages.entries()) {
    if (pages.has(page.page)) structural.push(`Duplicate page number ${page.page}`);
    // Pages are numbered 1..N in array order, so no page can be left out of the document and the
    // text before a section's first span is always the text the document puts there.
    if (page.page !== position + 1)
      structural.push(`Page ${page.page} at position ${position + 1}`);
    const codePoints = Array.from(page.text);
    if (
      !Number.isInteger(page.page) ||
      page.page < 1 ||
      !Number.isInteger(page.bodyStart) ||
      !Number.isInteger(page.bodyEnd) ||
      page.bodyStart < 0 ||
      page.bodyEnd < page.bodyStart ||
      page.bodyEnd > codePoints.length
    ) {
      structural.push(`Invalid body range on page ${page.page}`);
      continue;
    }
    const bodyIssue = bodyIssueFor(page, codePoints);
    if (bodyIssue !== undefined) issues.push(`Page ${page.page}: ${bodyIssue}`);
    pages.set(page.page, {
      page,
      codePoints,
      malformed: findForbiddenCharacter(page.text) !== undefined,
      bodyIssue,
    });
  }
  if (structural.length > 0) throw new FidelityError("Source document text is invalid", structural);
  return { pages, issues };
}

function slice(index: PageIndex, start: number, end: number): string {
  return index.codePoints.slice(start, end).join("");
}

// Where a slice of page text that starts at `start` is read from: the U+000A that ends the
// previous line when only whitespace other than U+000A lies between it and `start` (before
// `bodyStart`, section 1 makes that code point U+000A), otherwise `start` itself. Normalisation
// does not treat the start of a text as the start of a line (section 3 step 4), so a slice that
// begins at a line start carries its line terminator with it.
function fromLineStart(index: PageIndex, start: number): number {
  let position = start;
  while (position > index.page.bodyStart) {
    const character = index.codePoints[position - 1] ?? "";
    if (character === "\n") return position - 1;
    if (!isWhitespace(character.codePointAt(0) ?? 0)) return start;
    position -= 1;
  }
  return index.codePoints[position - 1] === "\n" ? position - 1 : start;
}

function isBlankSlice(index: PageIndex, start: number, end: number): boolean {
  if (end <= start) return true;
  try {
    return normalizeText(slice(index, fromLineStart(index, start), end)) === "";
  } catch {
    return false;
  }
}

type SpanPiece = { index: PageIndex; start: number; end: number };

// Locates and hash-checks a section's spans. Returns one contiguous raw slice per page (so the
// source's own characters, never whitespace of ours, decide where words begin and end), or a
// failing status with a reason code (never text).
function resolveSpans(
  spans: SourceSpan[],
  pages: Map<number, PageIndex>,
): { raw: string[] } | { status: SectionStatus; reason: string } {
  const pieces: SpanPiece[] = [];
  let previous: SourceSpan | undefined;

  for (const span of spans) {
    const index = pages.get(span.page);
    if (index === undefined) return { status: "span-not-found", reason: "page-not-found" };
    if (index.malformed) return { status: "span-not-found", reason: "page-malformed" };
    if (index.bodyIssue !== undefined) return { status: "span-not-found", reason: index.bodyIssue };
    const { bodyStart, bodyEnd } = index.page;
    if (
      span.startOffset < bodyStart ||
      span.endOffset > bodyEnd ||
      span.startOffset >= span.endOffset
    ) {
      return { status: "span-not-found", reason: "outside-body" };
    }
    const text = slice(index, span.startOffset, span.endOffset);
    if (sha256Utf8(text) !== span.textSha256) {
      return { status: "span-not-found", reason: "hash-mismatch" };
    }

    const last = pieces[pieces.length - 1];
    if (previous !== undefined && last !== undefined) {
      if (span.page === previous.page) {
        if (span.startOffset < previous.endOffset) {
          return { status: "invalid-provenance", reason: "span-order" };
        }
        if (!isBlankSlice(index, previous.endOffset, span.startOffset)) {
          return { status: "invalid-provenance", reason: "non-contiguous" };
        }
        last.end = span.endOffset;
      } else if (span.page === previous.page + 1) {
        const previousIndex = pages.get(previous.page);
        if (
          previousIndex === undefined ||
          !isBlankSlice(previousIndex, previous.endOffset, previousIndex.page.bodyEnd) ||
          !isBlankSlice(index, bodyStart, span.startOffset)
        ) {
          return { status: "invalid-provenance", reason: "non-contiguous" };
        }
        // The blank tails and heads around a page break are part of the text, not discarded:
        // an invisible character hiding in them cannot change where a word ends.
        last.end = previousIndex.page.bodyEnd;
        pieces.push({ index, start: bodyStart, end: span.endOffset });
      } else {
        return { status: "invalid-provenance", reason: "non-contiguous" };
      }
    } else {
      pieces.push({ index, start: fromLineStart(index, span.startOffset), end: span.endOffset });
    }
    previous = span;
  }

  // The outer edges of a section must fall on word boundaries: a section may omit words, but
  // it may not begin or end inside one (spec section 6).
  const firstSpan = spans[0];
  const lastSpan = spans[spans.length - 1];
  const last = pieces[pieces.length - 1];
  if (firstSpan !== undefined && lastSpan !== undefined && last !== undefined) {
    if (startCutsWord(pages, firstSpan) || endCutsWord(last.index, lastSpan)) {
      return { status: "invalid-provenance", reason: "word-cut" };
    }
  }

  return { raw: pieces.map((piece) => slice(piece.index, piece.start, piece.end)) };
}

// Whitespace for the edge rules: section 3 step 5's list without U+00A0, U+2007 and U+202F,
// which join the groups of a number (`10 000`) and so are not a boundary between tokens.
const NUMBER_JOINERS = new Set([0x00a0, 0x2007, 0x202f]);

function isEdgeWhitespace(character: string | undefined): boolean {
  const codePoint = character?.codePointAt(0);
  return codePoint !== undefined && isWhitespace(codePoint) && !NUMBER_JOINERS.has(codePoint);
}

const DECIMAL_DIGIT = /^\p{Nd}$/u;

function isDecimalDigit(character: string | undefined): boolean {
  return character !== undefined && DECIMAL_DIGIT.test(character);
}

// The first code point from `from` in direction `step` (+1 or -1) that is not section 3
// whitespace, read inside the body and without crossing U+000A; undefined if there is none.
function nextToken(index: PageIndex, from: number, step: 1 | -1): string | undefined {
  const { bodyStart, bodyEnd } = index.page;
  for (let position = from; position >= bodyStart && position < bodyEnd; position += step) {
    const character = index.codePoints[position] ?? "";
    if (character === "\n") return undefined;
    if (!isWhitespace(character.codePointAt(0) ?? 0)) return character;
  }
  return undefined;
}

// A digit at the edge with a digit beyond it, across nothing but whitespace on the same line,
// is one number grouped with spaces (`10 000`): the edge cuts it.
function cutsDigitGroup(index: PageIndex, inner: number, beyond: number, step: 1 | -1): boolean {
  return isDecimalDigit(index.codePoints[inner]) && isDecimalDigit(nextToken(index, beyond, step));
}

// Reads backwards from the code point before the first span, through its page's body and then
// the bodies of the pages before it (as declared, whether or not they pass section 1 or 2),
// skipping edge whitespace. The section starts inside a token if nothing was skipped before the
// first other code point, whatever that code point is (a letter, a digit, `.` of `0.5`, `−` of
// `−20`, U+00A0 of `10 000`), or if that code point is U+00AD. Reading past page 1 is no cut. It
// also starts inside a number when its first code point is a digit and the first non-whitespace
// code point before it on the same line is a digit too.
function startCutsWord(pages: Map<number, PageIndex>, span: SourceSpan): boolean {
  const first = pages.get(span.page);
  if (first !== undefined && cutsDigitGroup(first, span.startOffset, span.startOffset - 1, -1)) {
    return true;
  }
  let skipped = false;
  let pageNumber = span.page;
  let position = span.startOffset - 1;
  let index = first;
  while (index !== undefined) {
    for (; position >= index.page.bodyStart; position -= 1) {
      const character = index.codePoints[position] ?? "";
      if (isEdgeWhitespace(character)) {
        skipped = true;
        continue;
      }
      return character === SOFT_HYPHEN || !skipped;
    }
    pageNumber -= 1;
    index = pages.get(pageNumber);
    if (index !== undefined) position = index.page.bodyEnd - 1;
  }
  return false;
}

// The section ends inside a token if its last span, without trailing edge whitespace, ends in
// U+00AD; or unless the code point at its end offset is edge whitespace or the end offset is at
// or past `bodyEnd` (whose code point before is U+000A, section 1). `1` of `1.5` is a cut: the
// `.` after it is not a boundary. It also ends inside a number when its last code point is a
// digit and the first non-whitespace code point after it on the same line is a digit too.
function endCutsWord(index: PageIndex, span: SourceSpan): boolean {
  let end = span.endOffset;
  while (end > span.startOffset && isEdgeWhitespace(index.codePoints[end - 1])) end -= 1;
  if (end > span.startOffset && index.codePoints[end - 1] === SOFT_HYPHEN) return true;
  if (span.endOffset >= index.page.bodyEnd) return false;
  if (!isEdgeWhitespace(index.codePoints[span.endOffset])) return true;
  return cutsDigitGroup(index, span.endOffset - 1, span.endOffset, 1);
}

// Pieces from consecutive pages are concatenated verbatim: a page body ends with its own line
// terminator (or a soft hyphen when a word continues), so the verifier never inserts one.
function joinPieces(raw: string[]): string {
  return raw.join("");
}

function diffHint(expected: string, actual: string): DiffHint {
  const expectedPoints = Array.from(expected);
  const actualPoints = Array.from(actual);
  let first = 0;
  while (
    first < expectedPoints.length &&
    first < actualPoints.length &&
    expectedPoints[first] === actualPoints[first]
  ) {
    first += 1;
  }
  let suffix = 0;
  while (
    suffix < expectedPoints.length - first &&
    suffix < actualPoints.length - first &&
    expectedPoints[expectedPoints.length - 1 - suffix] ===
      actualPoints[actualPoints.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  return {
    expectedLength: expectedPoints.length,
    actualLength: actualPoints.length,
    firstDifferingOffset: first,
    commonSuffixLength: suffix,
    expectedWordCount: countWords(expected),
    actualWordCount: countWords(actual),
    expectedSha256: sha256Utf8(expected),
    actualSha256: sha256Utf8(actual),
  };
}

// Normalised narrative text of one section, or the reason it cannot be produced.
export function normalizeNarrative(div: string): { text: string } | { reason: string } {
  try {
    const text = normalizeText(xhtmlToText(div));
    return text === "" ? { reason: "empty-narrative" } : { text };
  } catch (error) {
    if (error instanceof XhtmlError) return { reason: error.code };
    if (error instanceof NormalizationError) return { reason: error.code };
    throw error;
  }
}

// Binding of a Bundle's narratives that Zone B can recompute without the source text.
export function computeNarrativeBinding(sections: NarrativeSection[]): {
  bindings: NarrativeBinding[];
  sha256: string;
} {
  const bindings = sections.map(({ sourceKey, div }) => {
    const normalized = normalizeNarrative(div);
    return {
      sourceKey,
      normalizedTextSha256: "text" in normalized ? sha256Utf8(normalized.text) : null,
    };
  });
  return { bindings, sha256: sha256(bindings) };
}

function sectionCode(section: SectionLike, system: string): string | undefined {
  return section.code?.coding?.find((coding) => coding.system === system)?.code;
}

// Walks a Composition in document order and returns every section that carries the canonical
// source code system and a narrative, with the same path convention as transform.ts.
export function collectNarrativeSections(
  composition: { section: SectionLike[] },
  sourceCodeSystem: string,
): NarrativeSection[] {
  const collected: NarrativeSection[] = [];
  const walk = (sections: SectionLike[], basePath: string): void => {
    sections.forEach((section, position) => {
      const path = `${basePath}[${position}]`;
      const code = sectionCode(section, sourceCodeSystem);
      if (code !== undefined && section.text !== undefined) {
        collected.push({ sourceKey: code, path, div: section.text.div });
      }
      if (section.section !== undefined) walk(section.section, `${path}.section`);
    });
  };
  walk(composition.section, "Composition.section");
  return collected;
}

function coverage(
  pages: Map<number, PageIndex>,
  verifiedSpans: SourceSpan[],
): FidelityReport["coverage"] {
  let pageCodePoints = 0;
  let bodyCodePoints = 0;
  let coveredCodePoints = 0;
  let uncoveredGaps = 0;
  for (const index of pages.values()) {
    const { bodyStart, bodyEnd } = index.page;
    // Page totals are reported alongside body totals so a reviewer can see how much text the
    // extractor-declared body range excludes; the body range itself is not trusted blindly.
    pageCodePoints += index.codePoints.length;
    bodyCodePoints += bodyEnd - bodyStart;
    const spans = verifiedSpans
      .filter((span) => span.page === index.page.page)
      .sort((left, right) => left.startOffset - right.startOffset);
    let cursor = bodyStart;
    for (const span of spans) {
      if (!isBlankSlice(index, cursor, span.startOffset)) uncoveredGaps += 1;
      coveredCodePoints += span.endOffset - span.startOffset;
      cursor = span.endOffset;
    }
    if (!isBlankSlice(index, cursor, bodyEnd)) uncoveredGaps += 1;
  }
  return { pageCodePoints, bodyCodePoints, coveredCodePoints, uncoveredGaps };
}

// Every span's page and offsets are integers (a JSON `1.0` is the integer 1). A boolean or a
// fractional number is not an offset, and a language that treats `true` as 1 would otherwise
// read it as page 1: it is a structural error, never a status.
function assertIntegerSpans(provenance: SectionProvenance[]): void {
  const invalid: string[] = [];
  for (const entry of provenance) {
    for (const span of entry.spans) {
      const fields: unknown[] = [span.page, span.startOffset, span.endOffset];
      if (!fields.every((value) => Number.isInteger(value))) {
        invalid.push(`Invalid span in provenance ${entry.sourceKey}`);
      }
    }
  }
  if (invalid.length > 0) throw new FidelityError("Provenance span is invalid", invalid);
}

function assertUniqueKeys(keys: string[], what: string): void {
  const seen = new Set<string>();
  const duplicates: string[] = [];
  for (const key of keys) {
    if (seen.has(key)) duplicates.push(`Ambiguous ${what} ${key}`);
    seen.add(key);
  }
  if (duplicates.length > 0) throw new FidelityError(`Duplicate ${what}`, duplicates);
}

export function verifyNarrativeFidelity(input: FidelityInput): FidelityReport {
  if (input.normalizationVersion !== NORMALIZATION_VERSION) {
    throw new FidelityError("Normalization version mismatch", [
      `Expected ${NORMALIZATION_VERSION}, received ${input.normalizationVersion}`,
    ]);
  }
  assertUniqueKeys(
    input.sections.map(({ sourceKey }) => sourceKey),
    "source section",
  );
  assertUniqueKeys(
    input.provenance.map(({ sourceKey }) => sourceKey),
    "provenance entry",
  );
  assertIntegerSpans(input.provenance);
  const { pages, issues } = indexPages(input.source);
  const provenance = new Map(input.provenance.map((entry) => [entry.sourceKey, entry]));
  const sectionKeys = new Set(input.sections.map(({ sourceKey }) => sourceKey));
  if (input.sections.length === 0) issues.push("No narrative sections to verify");
  for (const key of provenance.keys()) {
    if (!sectionKeys.has(key)) issues.push(`Orphan provenance ${key}`);
  }

  const results: SectionResult[] = [];
  const verifiedSpans: { sourceKey: string; span: SourceSpan }[] = [];

  for (const section of input.sections) {
    const entry = provenance.get(section.sourceKey);
    const base = { sourceKey: section.sourceKey, path: section.path };
    const normalized = normalizeNarrative(section.div);
    const normalizedHash =
      "text" in normalized ? { normalizedTextSha256: sha256Utf8(normalized.text) } : {};

    if (entry === undefined) {
      results.push({ ...base, status: "missing-provenance", spanCount: 0, ...normalizedHash });
      continue;
    }
    const spanCount = entry.spans.length;
    if (!("text" in normalized)) {
      results.push({
        ...base,
        status: "malformed-narrative",
        spanCount,
        reason: normalized.reason,
      });
      continue;
    }
    const resolved = resolveSpans(entry.spans, pages);
    if (!("raw" in resolved)) {
      results.push({
        ...base,
        status: resolved.status,
        spanCount,
        reason: resolved.reason,
        ...normalizedHash,
      });
      continue;
    }
    const expected = normalizeText(joinPieces(resolved.raw));
    if (expected !== normalized.text) {
      results.push({
        ...base,
        status: "mismatch",
        spanCount,
        details: diffHint(expected, normalized.text),
        ...normalizedHash,
      });
      continue;
    }
    results.push({ ...base, status: "verified", spanCount, ...normalizedHash });
    for (const span of entry.spans) verifiedSpans.push({ sourceKey: section.sourceKey, span });
  }

  // Spans of different sections may not overlap: stitching one source passage into two sections
  // would otherwise pass every per-section check.
  const overlapping = new Set<string>();
  const ordered = [...verifiedSpans].sort(
    (left, right) =>
      left.span.page - right.span.page || left.span.startOffset - right.span.startOffset,
  );
  for (let position = 1; position < ordered.length; position += 1) {
    const previous = ordered[position - 1];
    const current = ordered[position];
    if (previous === undefined || current === undefined) continue;
    if (
      previous.span.page === current.span.page &&
      current.span.startOffset < previous.span.endOffset &&
      previous.sourceKey !== current.sourceKey
    ) {
      overlapping.add(previous.sourceKey);
      overlapping.add(current.sourceKey);
    }
  }
  const sections = results.map((result) =>
    overlapping.has(result.sourceKey) && result.status === "verified"
      ? { ...result, status: "invalid-provenance" as const, reason: "overlap" }
      : result,
  );

  const verified = sections.filter(({ status }) => status === "verified").length;
  const status: FidelityReport["status"] =
    verified === sections.length && issues.length === 0 ? "passed" : "failed";
  const body: Omit<FidelityReport, "reportHash"> = {
    reportVersion: "1.0.0",
    normalizationVersion: NORMALIZATION_VERSION,
    extractedTextSha256: sha256(input.source),
    narrativeBindingSha256: computeNarrativeBinding(input.sections).sha256,
    status,
    sections,
    issues,
    summary: { total: sections.length, verified },
    coverage: coverage(
      pages,
      verifiedSpans.filter(({ sourceKey }) => !overlapping.has(sourceKey)).map(({ span }) => span),
    ),
  };
  return { ...body, reportHash: sha256(body) };
}

// Recomputes a report's hash from its own content; `true` when the report is self-consistent.
export function verifyReportHash(report: FidelityReport): boolean {
  const { reportHash, ...body } = report;
  return sha256(body) === reportHash;
}
