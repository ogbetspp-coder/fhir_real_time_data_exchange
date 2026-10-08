import type { SectionProvenance, SourceSpan } from "../../../src/contracts/ingestion-provenance.js";
import { NORMALIZATION_VERSION } from "../../../src/fidelity/normalize.js";
import type {
  FidelityInput,
  NarrativeSection,
  SectionStatus,
  SourceDocumentText,
  SourcePage,
} from "../../../src/fidelity/verify.js";
import type { XhtmlErrorCode } from "../../../src/fidelity/xhtml.js";
import { sha256Utf8 } from "../../../src/lib/hash.js";

// Shared, deterministic case definitions. `scripts/fidelity/generate-vectors.ts` turns them
// into test/fixtures/fidelity/vectors.json (the language-neutral golden vectors) and
// test/fidelity.test.ts asserts both the vectors and the semantic expectations below.
// All text is synthetic demonstration content; nothing here is product information.

export type NormalizationCase = {
  name: string;
  input: string;
  expected: string | { error: "forbidden-character" };
};

export type XhtmlCase = {
  name: string;
  input: string;
  expected: string | { error: XhtmlErrorCode };
};

export type VerifyExpectation = {
  status: "passed" | "failed";
  sections?: Record<string, SectionStatus>;
  reasons?: Record<string, string>;
  issueCount?: number;
};

export type VerifyCase = {
  name: string;
  input: FidelityInput;
  expect: VerifyExpectation;
};

export type ThrowCase = {
  name: string;
  input: FidelityInput;
};

const XHTML = 'xmlns="http://www.w3.org/1999/xhtml"';

export function div(inner: string): string {
  return `<div ${XHTML}>${inner}</div>`;
}

export function paragraphs(...texts: string[]): string {
  return div(texts.map((text) => `<p>${text}</p>`).join(""));
}

// ---------------------------------------------------------------------------------------------
// Synthetic three-page source document. Headers and footers sit outside the body ranges.

const HEADER = "ACME Demo Product — Synthetic SmPC\n";
const FOOTER = (page: number): string => `Page ${page} of 3`;

// U+FDD0 table, U+FDD1 end of table, U+FDD2 row, U+FDD3 cell (fidelity-norm/3.0.0).
const TABLE =
  "\ufdd0\n\ufdd2\t\ufdd3\tDose\t\ufdd3\tFrequency\n\ufdd2\t\ufdd3\t10 mg\t\ufdd3\tOnce daily\n" +
  "\ufdd2\t\ufdd3\t20 mg\t\ufdd3\tTwice daily\n\ufdd1";

const PAGE_BODIES = [
  [
    "4.1 Therapeutic indications",
    "Synthetic demonstration content for section 4.1; not for clinical use.",
    "The ﬁnal dose is given by intra­venous infusion over 10 minutes.",
    "Body surface area is expressed in m2 and the volume in H2O.",
    "The ‘quoted’ phrase uses typographic quotes.",
    "4.2 Posology and method of administration",
    "Posology text part one continues on the next page.",
  ].join("\n"),
  [
    "Part two of the posology text is on page two.",
    "The recommended dose is 10 mg once daily for a long-\nterm course.",
    "4.3 Contraindications",
    "Hypersensitivity to the active substance is a contraindication.",
  ].join("\n"),
  [
    "4.4 Special warnings",
    "• first warning item",
    "• second warning item",
    "4.5 Interactions",
    // A table as the extractor writes it (section 7, fidelity-norm/3.0.0): its grid markers,
    // then each row, then each cell's slot and text.
    TABLE,
  ].join("\n"),
];

// A page body ends with its own line terminator (spec section 7), so bodyEnd sits just after it.
function buildPage(page: number, body: string): SourcePage {
  const terminated = `${body}\n`;
  const text = `${HEADER}${terminated}${FOOTER(page)}`;
  const bodyStart = Array.from(HEADER).length;
  const bodyEnd = bodyStart + Array.from(terminated).length;
  return { page, text, bodyStart, bodyEnd };
}

export function buildSource(): SourceDocumentText {
  return {
    extractorVersion: "synthetic-extractor/1.0.0",
    pages: PAGE_BODIES.map((body, index) => buildPage(index + 1, body)),
  };
}

// A source with arbitrary page bodies, for cases that need a specific page-break layout.
export function customSource(bodies: string[]): SourceDocumentText {
  return {
    extractorVersion: "synthetic-extractor/1.0.0",
    pages: bodies.map((body, index) => buildPage(index + 1, body)),
  };
}

// Code-point offsets of a unique needle on a page, as a hashed span.
export function spanFor(source: SourceDocumentText, page: number, needle: string): SourceSpan {
  const pageText = source.pages[page - 1];
  if (pageText === undefined) throw new Error(`No page ${page}`);
  const points = Array.from(pageText.text);
  const needlePoints = Array.from(needle);
  const matches: number[] = [];
  for (let start = 0; start + needlePoints.length <= points.length; start += 1) {
    if (needlePoints.every((point, offset) => points[start + offset] === point))
      matches.push(start);
  }
  const start = matches[0];
  if (start === undefined || matches.length !== 1) {
    throw new Error(`Needle must occur exactly once on page ${page}: ${matches.length} matches`);
  }
  return {
    page,
    startOffset: start,
    endOffset: start + needlePoints.length,
    textSha256: sha256Utf8(needle),
  };
}

type SectionSpec = {
  sourceKey: string;
  path: string;
  div: string;
  spans: SourceSpan[];
};

function toInput(source: SourceDocumentText, specs: SectionSpec[]): FidelityInput {
  const sections: NarrativeSection[] = specs.map(({ sourceKey, path, div: narrative }) => ({
    sourceKey,
    path,
    div: narrative,
  }));
  const provenance: SectionProvenance[] = specs.map(({ sourceKey, spans, div: narrative }) => ({
    sourceKey,
    spans,
    narrativeDivSha256: sha256Utf8(narrative),
    // The generator fills the true value; the verifier does not read this field.
    normalizedTextSha256: "0".repeat(64),
  }));
  return { normalizationVersion: NORMALIZATION_VERSION, source, sections, provenance };
}

const S = buildSource();

const INDICATIONS = "Synthetic demonstration content for section 4.1; not for clinical use.";
const INFUSION = "The ﬁnal dose is given by intra­venous infusion over 10 minutes.";
const AREA = "Body surface area is expressed in m2 and the volume in H2O.";
const QUOTES = "The ‘quoted’ phrase uses typographic quotes.";
const POSOLOGY_ONE = "Posology text part one continues on the next page.";
const POSOLOGY_TWO = "Part two of the posology text is on page two.";
const DOSE = "The recommended dose is 10 mg once daily for a long-\nterm course.";
const CONTRA = "Hypersensitivity to the active substance is a contraindication.";
const WARNINGS = "• first warning item\n• second warning item";

const baseSpecs = (): SectionSpec[] => [
  {
    sourceKey: "smpc.4.1",
    path: "Composition.section[0].section[0]",
    div: paragraphs(INDICATIONS),
    spans: [spanFor(S, 1, INDICATIONS)],
  },
  {
    sourceKey: "smpc.4.2.posology",
    path: "Composition.section[0].section[1].section[0]",
    div: paragraphs(POSOLOGY_ONE, POSOLOGY_TWO),
    spans: [spanFor(S, 1, POSOLOGY_ONE), spanFor(S, 2, POSOLOGY_TWO)],
  },
  {
    sourceKey: "smpc.4.3",
    path: "Composition.section[0].section[2]",
    div: paragraphs(CONTRA),
    spans: [spanFor(S, 2, CONTRA)],
  },
  {
    sourceKey: "smpc.4.4",
    path: "Composition.section[0].section[3]",
    div: div("<ul><li>first warning item</li><li>second warning item</li></ul>"),
    spans: [spanFor(S, 3, WARNINGS)],
  },
  {
    sourceKey: "smpc.4.5",
    path: "Composition.section[0].section[4]",
    div: div(
      "<table><thead><tr><th>Dose</th><th>Frequency</th></tr></thead><tbody><tr><td>10 mg</td><td>Once daily</td></tr><tr><td>20 mg</td><td>Twice daily</td></tr></tbody></table>",
    ),
    spans: [spanFor(S, 3, TABLE)],
  },
];

function single(sourceKey: string, narrative: string, spans: SourceSpan[]): SectionSpec[] {
  return [{ sourceKey, path: "Composition.section[0].section[0]", div: narrative, spans }];
}

const verifiedAll = (keys: string[]): Record<string, SectionStatus> =>
  Object.fromEntries(keys.map((key) => [key, "verified"]));

const CONTRA_HEAD = "Hypersensitivity to the active substance is a contra";
const CONTRA_TAIL = "indication.";
const INFUSION_HEAD = "The ﬁnal dose is given by intra";
const INFUSION_TAIL = "venous infusion over 10 minutes.";

// Page 1 with its body cut at the line break before INFUSION: a legal boundary, but the excluded
// tail far exceeds what a header/footer could be.
function shrunkenBodySource(): SourceDocumentText {
  const source = buildSource();
  const [first] = source.pages;
  if (first === undefined) throw new Error("fixture");
  const cut = first.text.indexOf(INFUSION);
  const bodyEnd = Array.from(first.text.slice(0, cut)).length;
  return { ...source, pages: [{ ...first, bodyEnd }, ...source.pages.slice(1)] };
}

// Page 1 with its body cut in the middle of "100 mg": an illegal boundary.
function midLineBodySource(): SourceDocumentText {
  const source = customSource(["Do not exceed 100 mg per day."]);
  const [first] = source.pages;
  if (first === undefined) throw new Error("fixture");
  const bodyEnd = Array.from(first.text.slice(0, first.text.indexOf("100") + 2)).length;
  return { ...source, pages: [{ ...first, bodyEnd }] };
}

// A page whose declared body starts right after a soft-hyphen line break: the header would
// have to end mid-word, so the boundary is illegal.
const BODY_AFTER_SOFT_HYPHEN: SourceDocumentText = {
  extractorVersion: "synthetic-extractor/1.0.0",
  pages: [
    (() => {
      const text = "ACME intra­\nvenous infusion.\n";
      const bodyStart = Array.from(text).indexOf("v");
      return { page: 1, text, bodyStart, bodyEnd: Array.from(text).length };
    })(),
  ],
};

const HYPHEN_ACROSS_PAGES = customSource([
  "The dose is given by intra­",
  "venous infusion over 10 minutes.",
]);
const WORD_ACROSS_PAGES = customSource(["Give nor", "​floxacin twice daily."]);

// A page carrying an unpaired UTF-16 surrogate. Section 2 rejects the page, so every span on it
// is `span-not-found`; the report's `extractedTextSha256` is nevertheless the canonical JSON
// hash of a source containing it, which is what makes this case worth pinning.
const LONE_SURROGATE_SOURCE = customSource(["Dose information.\uD800 Tail text."]);

// ---------------------------------------------------------------------------------------------
// Sources for the fidelity-norm/2.0.0 cases.

// Pages given whole: the body is the entire page unless a range is stated.
function rawSource(
  pages: { text: string; bodyStart?: number; bodyEnd?: number }[],
): SourceDocumentText {
  return {
    extractorVersion: "synthetic-extractor/1.0.0",
    pages: pages.map(({ text, bodyStart, bodyEnd }, index) => ({
      page: index + 1,
      text,
      bodyStart: bodyStart ?? 0,
      bodyEnd: bodyEnd ?? Array.from(text).length,
    })),
  };
}

const CARTON = "Keep in the outer carton.";
const PREGNANCY = "safe for pregnant women.";
const RUNNING_HEADER = "ACME running header\n";

// Page 1 ends "... is 1" with no line terminator at the end of the page; page 2 continues it.
const NO_FINAL_LF = rawSource([{ text: "The maximum daily dose is 1" }, { text: "0 mg.\n" }]);
const LF_AT_PAGE_END = rawSource([{ text: `${CARTON}\n` }]);
const EMPTY_FIRST_BODY = rawSource([
  {
    text: RUNNING_HEADER,
    bodyStart: Array.from(RUNNING_HEADER).length,
    bodyEnd: Array.from(RUNNING_HEADER).length,
  },
  { text: `${CARTON}\n` },
]);
const HYPHEN_THEN_PAGE = customSource(["Not safe for un\u00ad", PREGNANCY]);
const FULL_STOP_THEN_PAGE = customSource(["Store below 25 degrees.", CARTON]);
const HYPHEN_BLANK_LINE = customSource([`Not safe for un\u00ad\n\n${PREGNANCY}`]);
const WORD_BLANK_LINE = customSource([`Store below 25 degrees\n\n${CARTON}`]);
const HYPHEN_THEN_BLANK_HEAD = customSource(["Not safe for un\u00ad", `\n${PREGNANCY}`]);
const FULL_STOP_THEN_BLANK_HEAD = customSource(["Store below 25 degrees.", `\n${CARTON}`]);
// Page 1 carries a C1 control (a text layer decoded as Latin-1), so it fails section 2; it is
// still read, as declared, when a section on page 2 looks back for a word boundary.
const MALFORMED_THEN_HYPHEN = customSource(["Dose\u0092 for un\u00ad", PREGNANCY]);
const MALFORMED_THEN_FULL_STOP = customSource(["Dose\u0092 information.", CARTON]);
const HYPHEN_SPACE_LINE_END = customSource(["The dose is given by intra\u00ad \nvenous infusion."]);
const SCRIPT_AREA = "Body surface area is expressed in m² and the volume in H₂O.";
const SCRIPT_SOURCE = customSource([SCRIPT_AREA]);
const PLAIN_NUMBER = "The count is 106 per litre.";
const PLAIN_NUMBER_SOURCE = customSource([PLAIN_NUMBER]);
const NEGATIVE_EXPONENT = "The count is 10⁻⁶ per litre.";
const NEGATIVE_EXPONENT_SOURCE = customSource([NEGATIVE_EXPONENT]);
const SMOKERS = "Suitable for nonsmokers only.";
const SMOKERS_SOURCE = customSource([SMOKERS]);
const SYMBOL = "The symbol \u{1d6fc} marks the dose.";
const SYMBOL_SOURCE = customSource([SYMBOL]);
const DO_NOT_TAKE = "Do not take with alcohol.";
const DO_NOT_TAKE_SOURCE = customSource([DO_NOT_TAKE]);
const AGE = "Age 2 11 years.";
const AGE_SOURCE = customSource([AGE]);
const C1_PAGE_SOURCE = customSource(["Take one tablet daily.\u0096 Swallow it whole."]);
const FORM_FEED_PAGE_SOURCE = customSource(["Take one tablet daily.\u000cSwallow it whole."]);
const BIDI_PAGE_SOURCE = customSource(["Take one tablet daily. \u202eSwallow it whole."]);

// Sources for the review findings folded into fidelity-norm/2.0.0 (C1-C3).
const DECIMAL = "Maximum dose is 1.5 mg daily.";
const DECIMAL_SOURCE = customSource([DECIMAL]);
const MINUS_TEMPERATURE = "Store at \u221220 \u00b0C.";
const MINUS_SOURCE = customSource([MINUS_TEMPERATURE]);
const HALF_DOSE = "Dose 0.5 mg.";
const HALF_DOSE_SOURCE = customSource([HALF_DOSE]);
const THOUSANDS = "Give 1,000 units.";
const THOUSANDS_SOURCE = customSource([THOUSANDS]);
const COMPOUND = "Use non-steroidal drugs.";
const COMPOUND_SOURCE = customSource([COMPOUND]);
const TABLE_ROW = "Dose (mg)\t2\t10";
const TABLE_ROW_SOURCE = customSource([TABLE_ROW]);
const RAISED = "Count 10\u2076/L";
const RAISED_SOURCE = customSource([RAISED]);
// Round 2 of the second review.
const UNSAFE = "Not safe: unsafe.";
const UNSAFE_SOURCE = customSource([UNSAFE]);
const UNSAFE_SPLIT = "Not safe: un safe.";
const UNSAFE_SPLIT_SOURCE = customSource([UNSAFE_SPLIT]);
const BULLET_LINE = "Take 2\n\u2022 10 mg";
const BULLET_LINE_SOURCE = customSource([BULLET_LINE]);
const PLAIN_LINE = "Take 2 10 mg";
const PLAIN_LINE_SOURCE = customSource([PLAIN_LINE]);
const WHITE_BULLET_LINE = "Store below 25\n\u25e6 C";
const WHITE_BULLET_SOURCE = customSource([WHITE_BULLET_LINE]);
const GROUPED = (separator: string): string => `The maximum dose is 10${separator}000 IU daily.`;
const GROUPED_SPACE_SOURCE = customSource([GROUPED(" ")]);
const GROUPED_NBSP_SOURCE = customSource([GROUPED("\u00a0")]);
const GROUPED_NNBSP_SOURCE = customSource([GROUPED("\u202f")]);
const GROUPED_THIN_SOURCE = customSource([GROUPED("\u2009")]);
const GROUPED_FIGURE_SOURCE = customSource([GROUPED("\u2007")]);
// Review round 3: groups separated by two code points of whitespace.
const GROUPED_DOUBLE_SPACE_SOURCE = customSource([GROUPED("  ")]);
const GROUPED_THIN_THEN_SPACE_SOURCE = customSource([GROUPED("\u2009 ")]);
const GROUPED_NNBSP_THEN_SPACE_SOURCE = customSource([GROUPED("\u202f ")]);
const INTRO_THEN_ROW = "Intro text\n\u2022 Adults\t10 mg";
const INTRO_THEN_ROW_SOURCE = customSource([INTRO_THEN_ROW]);
const LIST_TAB_ITEM = "\u2022\tAdults: 10 mg";
const LIST_TAB_ITEM_SOURCE = customSource([LIST_TAB_ITEM]);
const LIST_SPACE_ITEM = "\u2022 Adults: 10 mg";
const LIST_SPACE_ITEM_SOURCE = customSource([LIST_SPACE_ITEM]);
const NUMBER_AT_LINE_END = "Take 2\n10 mg is the daily dose.";
const NUMBER_AT_LINE_END_SOURCE = customSource([NUMBER_AT_LINE_END]);
// One-row tables as the extractor writes them (section 7, fidelity-norm/3.0.0).
const gridRow = (...cells: string[]): string =>
  `\ufdd0\n\ufdd2${cells.map((cell) => `\t\ufdd3\t${cell}`).join("")}\n\ufdd1`;
const ROW = gridRow("2", "10");
const ROW_SOURCE = customSource([ROW]);
const BULLET_ROW = gridRow("2", "\u2022 10");
const BULLET_ROW_SOURCE = customSource([BULLET_ROW]);
const FIRST_CELL_BULLET_ROW = gridRow("\u2022 10", "2");
const FIRST_CELL_BULLET_SOURCE = customSource([FIRST_CELL_BULLET_ROW]);
const FIRST_CELL_ROW = gridRow("10", "2");
const FIRST_CELL_ROW_SOURCE = customSource([FIRST_CELL_ROW]);
// fidelity-norm/3.0.0: numbered lists, table grids and pictures, as the extractor writes them.
const NUMBERED_ITEM = "3. Take one tablet.";
const NUMBERED_ITEM_SOURCE = customSource([NUMBERED_ITEM]);
// A dose drawn against every age group (a row span), and the same words with empty cells.
const SPANNED_DOSE =
  "\ufdd0\n\ufdd2\t\ufdd3\tAdults\t\ufdd3\t10 mg\n\ufdd2\t\ufdd3\tChildren\t\ufdd5\t\n" +
  "\ufdd2\t\ufdd3\tElderly\t\ufdd5\t\n\ufdd1";
const SPANNED_DOSE_SOURCE = customSource([SPANNED_DOSE]);
const EMPTY_CELLS_DOSE =
  "\ufdd0\n\ufdd2\t\ufdd3\tAdults\t\ufdd3\t10 mg\n\ufdd2\t\ufdd3\tChildren\t\ufdd3\t\n" +
  "\ufdd2\t\ufdd3\tElderly\t\ufdd3\t\n\ufdd1";
const EMPTY_CELLS_DOSE_SOURCE = customSource([EMPTY_CELLS_DOSE]);
const DOSE_IN_SECOND_COLUMN = gridRow("Adults", "10 mg", "");
const DOSE_IN_SECOND_COLUMN_SOURCE = customSource([DOSE_IN_SECOND_COLUMN]);
// A picture is its `data:` URI's hash between two U+FFFC (section 5).
const PICTURE_SOURCE = "data:image/png;base64,iVBORw0KGgo=";
const PICTURE_LINE = `See \ufffc${sha256Utf8(PICTURE_SOURCE)}\ufffc below.`;
const PICTURE_LINE_SOURCE = customSource([PICTURE_LINE]);
// A row whose last cell continues on the next page (section 7): the continuation begins with
// U+0009, so the bullet at its start is content, as in any cell.
const CELL_ACROSS_PAGES = ["\ufdd0\n\ufdd2\t\ufdd3\tDose\t\ufdd3\t2", "\t\u2022 10\n\ufdd1"];
const CELL_ACROSS_PAGES_SOURCE = customSource(CELL_ACROSS_PAGES);
const cellAcrossPagesSpans = (): SourceSpan[] =>
  CELL_ACROSS_PAGES.map((body, index) => spanFor(CELL_ACROSS_PAGES_SOURCE, index + 1, body));
// A word in a cell hyphenated at a page break (section 7): the continuation begins with the rest
// of the word, which section 3 step 1 joins across the soft hyphen and the line feed.
const CELL_WORD_ACROSS_PAGES = [
  "\ufdd0\n\ufdd2\t\ufdd3\tDose\t\ufdd3\tAdults with renal impair\u00ad",
  "ment\t\ufdd3\t10 mg\n\ufdd1",
];
const CELL_WORD_ACROSS_PAGES_SOURCE = customSource(CELL_WORD_ACROSS_PAGES);
const cellWordAcrossPagesSpans = (): SourceSpan[] =>
  CELL_WORD_ACROSS_PAGES.map((body, index) =>
    spanFor(CELL_WORD_ACROSS_PAGES_SOURCE, index + 1, body),
  );
// A row broken across a page in two cells (section 7): the earlier page holds the row up to the
// break in its first continued cell; the rest of that cell and every later slot, including "Oral",
// which the earlier page draws, are on the later page.
const ROW_ACROSS_PAGES = [
  "\ufdd0\n\ufdd2\t\ufdd3\tDose\t\ufdd3\tAdults with renal",
  "\timpairment\t\ufdd3\t10 mg once daily\t\ufdd3\tOral\n\ufdd1",
];
const ROW_ACROSS_PAGES_SOURCE = customSource(ROW_ACROSS_PAGES);
const rowAcrossPagesSpans = (): SourceSpan[] =>
  ROW_ACROSS_PAGES.map((body, index) => spanFor(ROW_ACROSS_PAGES_SOURCE, index + 1, body));
// Review round 7 (section 7, tables across a page break). A page footnote drawn between the table's
// parts is written after the table.
const FOOTNOTE_ACROSS_PAGES = [
  "\ufdd0\n\ufdd2\t\ufdd3\tAdults\t\ufdd3\t10 mg\u00b9",
  "\ufdd2\t\ufdd3\tChildren\t\ufdd3\t5 mg\n\ufdd1\n\u00b9 Not studied in hepatic impairment.",
];
const FOOTNOTE_ACROSS_PAGES_SOURCE = customSource(FOOTNOTE_ACROSS_PAGES);
// A cell that spans rows, broken at the page: its text stays in its own slot, and the later row,
// which the earlier page draws, follows it on the later page.
const SPAN_ACROSS_PAGES = [
  "\ufdd0\n\ufdd2\t\ufdd3\tAdults with renal",
  "\timpairment\t\ufdd3\t10 mg\n\ufdd2\t\ufdd5\t\t\ufdd3\t5 mg\n\ufdd1",
];
const SPAN_ACROSS_PAGES_SOURCE = customSource(SPAN_ACROSS_PAGES);
// A footer row repeated on every page is written where the table last has it.
const FOOTER_ACROSS_PAGES = [
  "\ufdd0\n\ufdd2\t\ufdd3\ta\t\ufdd3\t1",
  "\ufdd2\t\ufdd3\tb\t\ufdd3\t1\n\ufdd2\t\ufdd3\tTotal\t\ufdd3\t2\n\ufdd1",
];
const FOOTER_ACROSS_PAGES_SOURCE = customSource(FOOTER_ACROSS_PAGES);
const spansOf = (bodies: string[], source: SourceDocumentText): SourceSpan[] =>
  bodies.map((body, index) => spanFor(source, index + 1, body));
// Review round 8 (section 7). A caption continued across a page break: the extractor inserts
// U+0009 before the continuation, so a bullet at its start is content.
const CAPTION_ACROSS_PAGES = ["\ufdd0\nDose 2", "\t\u2022 10 mg\n\ufdd2\t\ufdd3\tA\n\ufdd1"];
const CAPTION_ACROSS_PAGES_SOURCE = customSource(CAPTION_ACROSS_PAGES);
// A paragraph the document wraps just before a mid-line bullet: the continuation line begins with
// U+0009, so "Take 2 • 10 mg" does not read "Take 2 10 mg".
const WRAP_BEFORE_BULLET = "Take 2\n\t\u2022 10 mg daily.";
const WRAP_BEFORE_BULLET_SOURCE = customSource([WRAP_BEFORE_BULLET]);
// A header row the document repeats on page 2 is excluded from that page's body, after the
// running header.
const REPEATED_HEADER_ROW = "\ufdd2\t\ufdd3\tPopulation\t\ufdd3\tDose\n";
const REPEATED_HEADER_BODIES = [
  `\ufdd0\n${REPEATED_HEADER_ROW}\ufdd2\t\ufdd3\tAdults\t\ufdd3\t10 mg`,
  "\ufdd2\t\ufdd3\tChildren\t\ufdd3\t5 mg\n\ufdd1",
];
const REPEATED_HEADER_SOURCE: SourceDocumentText = (() => {
  const [first, second] = REPEATED_HEADER_BODIES.map((body, index) => buildPage(index + 1, body));
  if (first === undefined || second === undefined) throw new Error("fixture");
  const excluded = `${HEADER}${REPEATED_HEADER_ROW}`;
  const body = `${REPEATED_HEADER_BODIES[1] ?? ""}\n`;
  const bodyStart = Array.from(excluded).length;
  return {
    extractorVersion: "synthetic-extractor/1.0.0",
    pages: [
      first,
      {
        page: 2,
        text: `${excluded}${body}${FOOTER(2)}`,
        bodyStart,
        bodyEnd: bodyStart + Array.from(body).length,
      },
    ],
  };
})();
// Review round 12: U+1680 draws as a stroke, "Take 2-10 mg", not as a space.
const OGHAM_LINE = "Take 2\u168010 mg daily.";
const OGHAM_SOURCE = customSource([OGHAM_LINE]);
const SPACE_LINE = "Take 2 10 mg daily.";
const SPACE_SOURCE = customSource([SPACE_LINE]);
// Review round 13: U+200A HAIR SPACE is drawn about a pixel wide, "210 mg", so it is content.
const HAIR_LINE = "Take 2 10 mg tablets.";
const HAIR_SOURCE = customSource([HAIR_LINE]);
// Review round 14: an invisible separator (U+2063, drawn as nothing) between the groups.
const GROUPED_INVISIBLE_THEN_SPACE_SOURCE = customSource([GROUPED("\u2063 ")]);
// Review round 15: U+2800 BRAILLE PATTERN BLANK is drawn as a blank, and a tag character from the
// supplementary planes as nothing; each is a gap between the groups.
const GROUPED_BLANK_THEN_SPACE_SOURCE = customSource([GROUPED("\u2800 ")]);
const GROUPED_TAG_SPACE_THEN_SPACE_SOURCE = customSource([GROUPED("\u{E0020} ")]);
const OGHAM_ALONE_SOURCE = customSource(["\u1680"]);
// Review round 17: a page carrying a code point section 2 adds in 3.0.0.
const ANNOTATION_PAGE_SOURCE = customSource(["Take 10 \ufff9000 IU daily. Dose information."]);
// Review round 16: letters the default serif face draws as an em-wide blank are gaps.
const GROUPED_YI_BLANK_THEN_SPACE_SOURCE = customSource([GROUPED("\ua4c5 ")]);
const SPANNED_DOSE_TABLE =
  '<table><tr><td>Adults</td><td rowspan="3">10 mg</td></tr><tr><td>Children</td></tr><tr><td>Elderly</td></tr></table>';
const MID_LINE_BULLET = "Take 2 \u2022 10 mg daily.";
const MID_LINE_BULLET_SOURCE = customSource([MID_LINE_BULLET]);
const LIST_ITEM = "\u2022 Keep in the outer carton.";
const LIST_ITEM_SOURCE = customSource([LIST_ITEM]);
const LIST_ITEM_PAGE_START_SOURCE = rawSource([{ text: `${LIST_ITEM}\n` }]);
const BULLET_AT_PAGE_HEAD = customSource(["Keep the bottle", "\u2022 in the outer carton."]);

// fidelity-norm/3.2.0: a certified Word source's page, one per section and wholly body
// (docs/fidelity-normalization.md section 7, the certified Word rule).
function wordSource(page: string): SourceDocumentText {
  return {
    extractorVersion: "word-epi/1.0.0",
    pages: [{ page: 1, text: page, bodyStart: 0, bodyEnd: Array.from(page).length }],
  };
}

const WORD_PAGE =
  "\nSynthetic demonstration content for section 4.2; not for clinical use.\n" +
  "• Adults: 10 mg once daily.\n• Children: 5 mg once daily.\nReduce the dose:\n" +
  "1. if the count is below 10 ⁹/l;\n2. if the AUC₀₋∞ doubles.\n" +
  "﷐\n﷒\t﷓\tDose by weight\t﷔\t\n" +
  "﷒\t﷓\tUnder 50 kg\t﷓\t5 mg\n" +
  "﷒\t﷕\t\t﷓\t2.5 mg in the elderly\n﷑\n";

const WORD_NARRATIVE =
  '<div xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">' +
  "<p>Synthetic demonstration content for section 4.2; not for clinical use.</p>" +
  "<ul><li>Adults: 10 mg once daily.</li><li>Children: 5 mg once daily.</li></ul>" +
  "<p><strong>Reduce</strong> the dose:</p>" +
  "<ol><li>if the count is below 10 <sup>9</sup>/l;</li>" +
  "<li>if the AUC<sub>0-∞</sub> doubles.</li></ol>" +
  '<table><tr><td colspan="2"><p>Dose by weight</p></td></tr>' +
  '<tr><td rowspan="2"><p>Under 50 kg</p></td><td><ul><li>5 mg</li></ul></td></tr>' +
  "<tr><td><p>2.5 mg in the elderly</p></td></tr></table></div>";

// fidelity-norm/3.5.0's grid: as zone_a.word_epi builds it (the vectors below).
const WORD_GRID_NARRATIVE = div(
  "<p> Table 2: Dose by weight</p><table><tr><td><p>Weight</p></td><td><p>Dose</p></td></tr>" +
    "<tr><td><p>Under 40 kg</p></td><td><p>5 mg (one tablet)</p></td></tr>" +
    "<tr><td><p>40 kg or more</p></td><td></td></tr><tr><td></td><td><p>See 4.4</p></td></tr>" +
    "</table><p>Take with food.  </p>",
);

export const verifyCases: VerifyCase[] = [
  // A body boundary inside a line, or a body that excludes more than a header/footer could hold,
  // invalidates the page: the extractor-declared range is bounded, not trusted.
  {
    name: "body-boundary-mid-line",
    input: (() => {
      const source = midLineBodySource();
      return toInput(
        source,
        single("smpc.4.2.posology", paragraphs("Do not exceed 10"), [
          { ...spanFor(source, 1, "Do not exceed 10"), page: 1 },
        ]),
      );
    })(),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "span-not-found" },
      reasons: { "smpc.4.2.posology": "body-boundary" },
    },
  },
  {
    name: "excluded-text-budget",
    input: toInput(
      shrunkenBodySource(),
      single("smpc.4.1", paragraphs(INDICATIONS), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "span-not-found" },
      reasons: { "smpc.4.1": "excluded-text" },
    },
  },
  {
    name: "body-boundary-after-soft-hyphen-break",
    input: toInput(
      BODY_AFTER_SOFT_HYPHEN,
      single("smpc.4.2.posology", paragraphs("venous infusion."), [
        spanFor(BODY_AFTER_SOFT_HYPHEN, 1, "venous infusion."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "span-not-found" },
      reasons: { "smpc.4.2.posology": "body-boundary" },
    },
  },
  // A section may omit words but never begin or end inside one: the outer span edges must fall
  // on word boundaries (punctuation and whitespace are boundaries; a soft hyphen is not).
  {
    name: "span-starts-mid-word",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("nal dose is given by intravenous infusion over 10 minutes."), [
        spanFor(S, 1, "nal dose is given by intra­venous infusion over 10 minutes."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "invalid-provenance" },
      reasons: { "smpc.4.1": "word-cut" },
    },
  },
  {
    name: "span-ends-mid-word",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("Synthetic demonstration content for section 4.1; not for clin"),
        [spanFor(S, 1, "Synthetic demonstration content for section 4.1; not for clin")],
      ),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "invalid-provenance" },
      reasons: { "smpc.4.1": "word-cut" },
    },
  },
  {
    name: "span-ends-before-soft-hyphen",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("The final dose is given by intra"), [
        spanFor(S, 1, "The ﬁnal dose is given by intra"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "invalid-provenance" },
      reasons: { "smpc.4.1": "word-cut" },
    },
  },
  {
    // 2.0.0 (review): a span edge must touch whitespace; a full stop is not a boundary, because
    // the same rule must refuse `1` of `1.5`. Named `span-ends-before-punctuation-passes` under
    // 1.1.1.
    name: "span-ends-before-punctuation-is-word-cut",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("Synthetic demonstration content for section 4.1; not for clinical use"),
        [spanFor(S, 1, "Synthetic demonstration content for section 4.1; not for clinical use")],
      ),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "invalid-provenance" },
      reasons: { "smpc.4.1": "word-cut" },
    },
  },
  {
    name: "section-ends-at-hyphenated-page-end",
    input: toInput(
      HYPHEN_ACROSS_PAGES,
      single("smpc.4.2.posology", paragraphs("The dose is given by intra"), [
        spanFor(HYPHEN_ACROSS_PAGES, 1, "The dose is given by intra­"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  // Across a page break the verifier concatenates the bodies verbatim: a soft hyphen at the end
  // of page 1 joins the word, and an invisible character in the head gap cannot split one.
  {
    name: "soft-hyphen-across-pages-joins-word",
    input: toInput(
      HYPHEN_ACROSS_PAGES,
      single(
        "smpc.4.2.posology",
        paragraphs("The dose is given by intravenous infusion over 10 minutes."),
        [
          spanFor(HYPHEN_ACROSS_PAGES, 1, "The dose is given by intra­"),
          spanFor(HYPHEN_ACROSS_PAGES, 2, "venous infusion over 10 minutes."),
        ],
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "soft-hyphen-across-pages-cannot-split-word",
    input: toInput(
      HYPHEN_ACROSS_PAGES,
      single(
        "smpc.4.2.posology",
        paragraphs("The dose is given by intra venous infusion over 10 minutes."),
        [
          spanFor(HYPHEN_ACROSS_PAGES, 1, "The dose is given by intra­"),
          spanFor(HYPHEN_ACROSS_PAGES, 2, "venous infusion over 10 minutes."),
        ],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Without a soft hyphen, the page-1 body's own line terminator separates the words; an
  // invisible character in the head gap of page 2 cannot join them.
  {
    name: "line-break-across-pages-separates-words",
    input: toInput(
      WORD_ACROSS_PAGES,
      single("smpc.4.2.posology", paragraphs("Give nor floxacin twice daily."), [
        spanFor(WORD_ACROSS_PAGES, 1, "Give nor"),
        spanFor(WORD_ACROSS_PAGES, 2, "floxacin twice daily."),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "invisible-page-gap-cannot-join-word",
    input: toInput(
      WORD_ACROSS_PAGES,
      single("smpc.4.2.posology", paragraphs("Give norfloxacin twice daily."), [
        spanFor(WORD_ACROSS_PAGES, 1, "Give nor"),
        spanFor(WORD_ACROSS_PAGES, 2, "floxacin twice daily."),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Adjacent spans must never let the narrative insert whitespace inside a source word: the
  // source's own characters between spans decide, never a separator of ours.
  {
    name: "adjacent-spans-cannot-split-word",
    input: toInput(
      S,
      single(
        "smpc.4.3",
        paragraphs("Hypersensitivity to the active substance is a contra indication."),
        [spanFor(S, 2, CONTRA_HEAD), spanFor(S, 2, CONTRA_TAIL)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.3": "mismatch" } },
  },
  {
    name: "adjacent-spans-same-word-verifies",
    input: toInput(
      S,
      single("smpc.4.3", paragraphs(CONTRA), [
        spanFor(S, 2, CONTRA_HEAD),
        spanFor(S, 2, CONTRA_TAIL),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.3": "verified" } },
  },
  {
    name: "soft-hyphen-gap-cannot-split-word",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("The final dose is given by intra venous infusion over 10 minutes."),
        [spanFor(S, 1, INFUSION_HEAD), spanFor(S, 1, INFUSION_TAIL)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "soft-hyphen-gap-joins-word",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("The final dose is given by intravenous infusion over 10 minutes."),
        [spanFor(S, 1, INFUSION_HEAD), spanFor(S, 1, INFUSION_TAIL)],
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
  },
  {
    name: "empty-sections-fail",
    input: toInput(S, []),
    expect: { status: "failed", issueCount: 1 },
  },
  {
    name: "exact-pass",
    input: toInput(S, baseSpecs()),
    expect: {
      status: "passed",
      sections: verifiedAll(["smpc.4.1", "smpc.4.2.posology", "smpc.4.3", "smpc.4.4", "smpc.4.5"]),
      issueCount: 0,
    },
  },
  {
    name: "whitespace-only-differences",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        div(
          "<p>  Synthetic   demonstration\n content for<br/>section 4.1;&#160;not for\tclinical</p><p>use.</p>",
        ),
        [spanFor(S, 1, INDICATIONS)],
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
  },
  {
    name: "ligature-and-soft-hyphen",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("The final dose is given by intravenous infusion over 10 minutes."),
        [spanFor(S, 1, INFUSION)],
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
  },
  {
    name: "soft-hyphen-entity-in-narrative",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("The final dose is given by intra&#173;venous infusion over 10 minutes."),
        [spanFor(S, 1, INFUSION)],
      ),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "invisible-character" },
    },
  },
  // 2.0.0: digits inside `sup` and `sub` fold to script code points, so markup can no longer
  // raise a digit the source prints on the line.
  {
    name: "superscript-markup-over-plain-digit",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs(
          "Body surface area is expressed in m<sup>2</sup> and the volume in H<sub>2</sub>O.",
        ),
        [spanFor(S, 1, AREA)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "page-break-two-contiguous-spans",
    input: toInput(S, baseSpecs().slice(1, 2)),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "word-changed",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("Synthetic demonstration content for section 4.1; not for veterinary use."),
        [spanFor(S, 1, INDICATIONS)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "word-deleted",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("Synthetic demonstration content for section 4.1; for clinical use."),
        [spanFor(S, 1, INDICATIONS)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "sentence-appended",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs(INDICATIONS, "Take with food."), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "sentences-reordered",
    input: toInput(
      S,
      single("smpc.4.2.posology", paragraphs(POSOLOGY_TWO, POSOLOGY_ONE), [
        spanFor(S, 1, POSOLOGY_ONE),
        spanFor(S, 2, POSOLOGY_TWO),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "number-changed",
    input: toInput(
      S,
      single(
        "smpc.4.3",
        paragraphs("The recommended dose is 100 mg once daily for a long- term course."),
        [spanFor(S, 2, DOSE)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.3": "mismatch" } },
  },
  {
    name: "unit-changed",
    input: toInput(
      S,
      single(
        "smpc.4.3",
        paragraphs("The recommended dose is 10 mcg once daily for a long- term course."),
        [spanFor(S, 2, DOSE)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.3": "mismatch" } },
  },
  {
    name: "negation-added",
    input: toInput(
      S,
      single(
        "smpc.4.3",
        paragraphs("Hypersensitivity to the active substance is not a contraindication."),
        [spanFor(S, 2, CONTRA)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.3": "mismatch" } },
  },
  {
    name: "hard-hyphen-dehyphenated",
    input: toInput(
      S,
      single(
        "smpc.4.3",
        paragraphs("The recommended dose is 10 mg once daily for a longterm course."),
        [spanFor(S, 2, DOSE)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.3": "mismatch" } },
  },
  {
    name: "straight-vs-typographic-quotes",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("The 'quoted' phrase uses typographic quotes."), [
        spanFor(S, 1, QUOTES),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "superscript-code-point-vs-digit",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("Body surface area is expressed in m² and the volume in H₂O."),
        [spanFor(S, 1, AREA)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "text-not-in-source",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("This sentence does not exist in the document."), [
        spanFor(S, 1, INDICATIONS),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
  },
  {
    name: "hidden-extra-element",
    input: toInput(
      S,
      single("smpc.4.1", div(`<p>${INDICATIONS}</p><p><span class="x">extra</span></p>`), [
        spanFor(S, 1, INDICATIONS),
      ]),
    ),
    // 2.0.0: `class` is not allowed at all, so the markup is refused before any comparison.
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "forbidden-attribute" },
    },
  },
  {
    name: "wrong-page-span",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs(INDICATIONS), [{ ...spanFor(S, 1, INDICATIONS), page: 2 }]),
    ),
    expect: { status: "failed", sections: { "smpc.4.1": "span-not-found" } },
  },
  {
    name: "span-hash-tampered",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs(INDICATIONS), [
        { ...spanFor(S, 1, INDICATIONS), textSha256: "f".repeat(64) },
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "span-not-found" },
      reasons: { "smpc.4.1": "hash-mismatch" },
    },
  },
  {
    name: "span-outside-body",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("ACME Demo Product — Synthetic SmPC"), [
        spanFor(S, 1, "ACME Demo Product — Synthetic SmPC"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "span-not-found" },
      reasons: { "smpc.4.1": "outside-body" },
    },
  },
  {
    name: "stitched-non-adjacent-spans",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs(INDICATIONS, QUOTES), [
        spanFor(S, 1, INDICATIONS),
        spanFor(S, 1, QUOTES),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "invalid-provenance" },
      reasons: { "smpc.4.1": "non-contiguous" },
    },
  },
  {
    name: "overlapping-sections",
    input: toInput(S, [
      ...single("smpc.4.1", paragraphs(INDICATIONS), [spanFor(S, 1, INDICATIONS)]),
      {
        sourceKey: "smpc.4.2",
        path: "Composition.section[0].section[1]",
        div: paragraphs("content for section 4.1; not for clinical use."),
        spans: [spanFor(S, 1, "content for section 4.1; not for clinical use.")],
      },
    ]),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "invalid-provenance", "smpc.4.2": "invalid-provenance" },
      reasons: { "smpc.4.1": "overlap", "smpc.4.2": "overlap" },
    },
  },
  {
    name: "missing-provenance",
    input: {
      ...toInput(S, single("smpc.4.1", paragraphs(INDICATIONS), [spanFor(S, 1, INDICATIONS)])),
      provenance: [],
    },
    expect: { status: "failed", sections: { "smpc.4.1": "missing-provenance" } },
  },
  {
    name: "orphan-provenance",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 2));
      return { ...input, sections: input.sections.slice(0, 1) };
    })(),
    expect: { status: "failed", sections: { "smpc.4.1": "verified" }, issueCount: 1 },
  },
  {
    name: "empty-div",
    input: toInput(S, single("smpc.4.1", div(""), [spanFor(S, 1, INDICATIONS)])),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "empty-narrative" },
    },
  },
  {
    name: "whitespace-only-div",
    input: toInput(S, single("smpc.4.1", div("<p> \n </p>"), [spanFor(S, 1, INDICATIONS)])),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "empty-narrative" },
    },
  },
  {
    // fidelity-norm/3.0.0: grid markers are not text a reader sees, so an empty table draws
    // nothing and a narrative of one is empty.
    name: "empty-table-div",
    input: toInput(
      S,
      single("smpc.4.1", div("<table><tr><td></td><td> </td></tr></table>"), [
        spanFor(S, 1, INDICATIONS),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "empty-narrative" },
    },
  },
  {
    name: "style-attribute",
    input: toInput(
      S,
      single("smpc.4.1", div(`<p>${INDICATIONS}</p><p style="display:none">extra</p>`), [
        spanFor(S, 1, INDICATIONS),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "forbidden-attribute" },
    },
  },
  {
    name: "script-element",
    input: toInput(
      S,
      single("smpc.4.1", div(`<p>${INDICATIONS}</p><script>x()</script>`), [
        spanFor(S, 1, INDICATIONS),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "unknown-element" },
    },
  },
  {
    name: "comment-in-narrative",
    input: toInput(
      S,
      single("smpc.4.1", div(`<p>${INDICATIONS}</p><!-- hidden -->`), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "comment" },
    },
  },
  {
    name: "nbsp-named-entity",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs(INDICATIONS.replace(" ", "&nbsp;")), [
        spanFor(S, 1, INDICATIONS),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "unknown-entity" },
    },
  },
  {
    name: "uppercase-tag",
    input: toInput(
      S,
      single("smpc.4.1", div(`<P>${INDICATIONS}</P>`), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "uppercase-element" },
    },
  },
  {
    name: "unbalanced-tag",
    input: toInput(
      S,
      single("smpc.4.1", `<div ${XHTML}><p>${INDICATIONS}</div>`, [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "misnested-tag" },
    },
  },
  {
    name: "replacement-character",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs(`${INDICATIONS}�`), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "forbidden-character" },
    },
  },
  // The page is rejected by section 2, but the report still hashes the source that contains the
  // unpaired surrogate: `extractedTextSha256` is the canonical JSON of `input.source`. This is
  // the only place in the system where a hash is taken over a string a UTF-8 encoder cannot
  // encode, and it pins the one escape (`\udXXX`) a re-implementation has to reproduce.
  {
    name: "lone-surrogate-page",
    input: toInput(
      LONE_SURROGATE_SOURCE,
      single("smpc.4.1", paragraphs("Dose information."), [
        spanFor(LONE_SURROGATE_SOURCE, 1, "Dose information."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "span-not-found" },
      reasons: { "smpc.4.1": "page-malformed" },
    },
  },
  // -------------------------------------------------------------------------------------------
  // fidelity-norm/2.0.0. Each case below names the rule it pins; most come in pairs, the
  // rejecting side and the accepting side of the same boundary.

  // Section 1: a non-empty body ends with its own line terminator even at the end of a page.
  // Under 1.1.1 a page ending "... is 1" with no line feed let a section end there although the
  // next page continues the number.
  {
    name: "page-body-without-final-lf",
    input: toInput(
      NO_FINAL_LF,
      single("smpc.4.2.posology", paragraphs("The maximum daily dose is 1"), [
        spanFor(NO_FINAL_LF, 1, "The maximum daily dose is 1"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "span-not-found" },
      reasons: { "smpc.4.2.posology": "body-boundary" },
      issueCount: 1,
    },
  },
  {
    name: "section-after-body-without-final-lf",
    input: toInput(
      NO_FINAL_LF,
      single("smpc.4.2.posology", paragraphs("0 mg."), [spanFor(NO_FINAL_LF, 2, "0 mg.")]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
      issueCount: 1,
    },
  },
  {
    name: "body-ends-with-lf-at-page-end",
    input: toInput(
      LF_AT_PAGE_END,
      single("smpc.6.4", paragraphs(CARTON), [spanFor(LF_AT_PAGE_END, 1, CARTON)]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  {
    name: "empty-body-page-before-section",
    input: toInput(
      EMPTY_FIRST_BODY,
      single("smpc.6.4", paragraphs(CARTON), [spanFor(EMPTY_FIRST_BODY, 2, CARTON)]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" }, issueCount: 0 },
  },
  // Section 6 start rule: read back through whitespace, across pages, to the first other code
  // point. A soft hyphen there, or a word character with nothing skipped, is a cut.
  {
    name: "section-starts-at-first-body-start",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("4.1 Therapeutic indications"), [
        spanFor(S, 1, "4.1 Therapeutic indications"),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
  },
  {
    name: "section-starts-after-hyphenated-page-end",
    input: toInput(
      HYPHEN_THEN_PAGE,
      single("smpc.4.6", paragraphs(PREGNANCY), [spanFor(HYPHEN_THEN_PAGE, 2, PREGNANCY)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.6": "invalid-provenance" },
      reasons: { "smpc.4.6": "word-cut" },
    },
  },
  {
    name: "section-starts-after-full-stop-page-end",
    input: toInput(
      FULL_STOP_THEN_PAGE,
      single("smpc.6.4", paragraphs(CARTON), [spanFor(FULL_STOP_THEN_PAGE, 2, CARTON)]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  {
    name: "section-starts-after-blank-line-after-soft-hyphen",
    input: toInput(
      HYPHEN_BLANK_LINE,
      single("smpc.4.6", paragraphs(PREGNANCY), [spanFor(HYPHEN_BLANK_LINE, 1, PREGNANCY)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.6": "invalid-provenance" },
      reasons: { "smpc.4.6": "word-cut" },
    },
  },
  {
    name: "section-starts-after-blank-line-after-word",
    input: toInput(
      WORD_BLANK_LINE,
      single("smpc.6.4", paragraphs(CARTON), [spanFor(WORD_BLANK_LINE, 1, CARTON)]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  {
    name: "section-starts-after-blank-page-head-after-soft-hyphen",
    input: toInput(
      HYPHEN_THEN_BLANK_HEAD,
      single("smpc.4.6", paragraphs(PREGNANCY), [spanFor(HYPHEN_THEN_BLANK_HEAD, 2, PREGNANCY)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.6": "invalid-provenance" },
      reasons: { "smpc.4.6": "word-cut" },
    },
  },
  {
    name: "section-starts-after-blank-page-head-after-full-stop",
    input: toInput(
      FULL_STOP_THEN_BLANK_HEAD,
      single("smpc.6.4", paragraphs(CARTON), [spanFor(FULL_STOP_THEN_BLANK_HEAD, 2, CARTON)]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  // Earlier pages are read as declared even when they fail section 2 themselves.
  {
    name: "section-starts-after-malformed-page-ending-in-soft-hyphen",
    input: toInput(
      MALFORMED_THEN_HYPHEN,
      single("smpc.4.6", paragraphs(PREGNANCY), [spanFor(MALFORMED_THEN_HYPHEN, 2, PREGNANCY)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.6": "invalid-provenance" },
      reasons: { "smpc.4.6": "word-cut" },
    },
  },
  {
    name: "section-starts-after-malformed-page-ending-in-full-stop",
    input: toInput(
      MALFORMED_THEN_FULL_STOP,
      single("smpc.6.4", paragraphs(CARTON), [spanFor(MALFORMED_THEN_FULL_STOP, 2, CARTON)]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  // Section 6 end rule: trailing whitespace does not hide a soft hyphen.
  {
    name: "span-ends-soft-hyphen-before-space",
    input: toInput(
      HYPHEN_SPACE_LINE_END,
      single("smpc.4.2.posology", paragraphs("The dose is given by intra"), [
        spanFor(HYPHEN_SPACE_LINE_END, 1, "The dose is given by intra\u00ad "),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-at-body-end-before-footer",
    input: toInput(
      S,
      single(
        "smpc.4.5",
        div(
          "<table><thead><tr><th>Dose</th><th>Frequency</th></tr></thead><tbody><tr><td>10 mg</td><td>Once daily</td></tr><tr><td>20 mg</td><td>Twice daily</td></tr></tbody></table>",
        ),
        [spanFor(S, 3, `${TABLE}\n`)],
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.5": "verified" } },
  },
  // Section 5, super- and subscripts. Rows 1 and 1b of the design note.
  {
    name: "superscript-markup-over-script-code-point",
    input: toInput(
      SCRIPT_SOURCE,
      single(
        "smpc.4.1",
        paragraphs(
          "Body surface area is expressed in m<sup>2</sup> and the volume in H<sub>2</sub>O.",
        ),
        [spanFor(SCRIPT_SOURCE, 1, SCRIPT_AREA)],
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
  },
  {
    name: "superscript-markup-changes-number",
    input: toInput(
      PLAIN_NUMBER_SOURCE,
      single("smpc.4.2.posology", paragraphs("The count is 10<sup>6</sup> per litre."), [
        spanFor(PLAIN_NUMBER_SOURCE, 1, PLAIN_NUMBER),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "self-closing-superscript-rejected",
    input: toInput(
      PLAIN_NUMBER_SOURCE,
      single("smpc.4.2.posology", paragraphs("The count is 10<sup/>6 per litre."), [
        spanFor(PLAIN_NUMBER_SOURCE, 1, PLAIN_NUMBER),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "malformed-narrative" },
      reasons: { "smpc.4.2.posology": "void-element" },
    },
  },
  {
    name: "superscript-dash-folds-to-minus",
    input: toInput(
      NEGATIVE_EXPONENT_SOURCE,
      single("smpc.4.2.posology", paragraphs("The count is 10<sup>–6</sup> per litre."), [
        spanFor(NEGATIVE_EXPONENT_SOURCE, 1, NEGATIVE_EXPONENT),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "superscript-plus-minus-rejected",
    input: toInput(
      NEGATIVE_EXPONENT_SOURCE,
      single("smpc.4.2.posology", paragraphs("The count is 10<sup>±6</sup> per litre."), [
        spanFor(NEGATIVE_EXPONENT_SOURCE, 1, NEGATIVE_EXPONENT),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "malformed-narrative" },
      reasons: { "smpc.4.2.posology": "unmappable-script" },
    },
  },
  // Rows 2 and 3: a soft hyphen before a line break in narrative text. A line break in text
  // (raw or referenced U+000A or U+000D) is emitted as a space, so no word is joined: the
  // narrative reads "non smokers" and mismatches the source's "nonsmokers". A block boundary or
  // `br` directly after U+00AD still rejects (`rejects-soft-hyphen-before-br`).
  {
    name: "soft-hyphen-before-cr-and-br",
    input: toInput(
      SMOKERS_SOURCE,
      single("smpc.4.4", paragraphs("Suitable for non&#173;&#13;<br/>smokers only."), [
        spanFor(SMOKERS_SOURCE, 1, SMOKERS),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "invisible-character" },
    },
  },
  {
    name: "soft-hyphen-before-raw-line-feed",
    input: toInput(
      SMOKERS_SOURCE,
      single("smpc.4.4", paragraphs("Suitable for non&#173;\nsmokers only."), [
        spanFor(SMOKERS_SOURCE, 1, SMOKERS),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "invisible-character" },
    },
  },
  {
    name: "soft-hyphen-before-raw-line-feed-against-split-word",
    input: toInput(
      UNSAFE_SPLIT_SOURCE,
      single("smpc.4.4", paragraphs("Not safe: un&#173;\nsafe."), [
        spanFor(UNSAFE_SPLIT_SOURCE, 1, UNSAFE_SPLIT),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "invisible-character" },
    },
  },
  {
    name: "soft-hyphen-before-raw-line-feed-against-joined-word",
    input: toInput(
      UNSAFE_SOURCE,
      single("smpc.4.4", paragraphs("Not safe: un&#173;\nsafe."), [
        spanFor(UNSAFE_SOURCE, 1, UNSAFE),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "invisible-character" },
    },
  },
  // Row 5: section 2 on the div as received and on each reference as decoded.
  {
    name: "surrogate-pair-by-references-rejected",
    input: toInput(
      SYMBOL_SOURCE,
      single("smpc.4.4", paragraphs("The symbol &#xD835;&#xDEFC; marks the dose."), [
        spanFor(SYMBOL_SOURCE, 1, SYMBOL),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "forbidden-character" },
    },
  },
  {
    name: "surrogates-split-by-markup-rejected",
    input: toInput(
      SYMBOL_SOURCE,
      single("smpc.4.4", paragraphs("The symbol \uD835<b></b>\uDEFC marks the dose."), [
        spanFor(SYMBOL_SOURCE, 1, SYMBOL),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "forbidden-character" },
    },
  },
  {
    name: "supplementary-reference-verifies",
    input: toInput(
      SYMBOL_SOURCE,
      single("smpc.4.4", paragraphs("The symbol &#x1D6FC; marks the dose."), [
        spanFor(SYMBOL_SOURCE, 1, SYMBOL),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.4": "verified" } },
  },
  // Row 6: an attribute a viewer's stylesheet can key on.
  {
    name: "class-attribute-rejected",
    input: toInput(
      DO_NOT_TAKE_SOURCE,
      single("smpc.4.4", paragraphs('Do <span class="d-none">not</span> take with alcohol.'), [
        spanFor(DO_NOT_TAKE_SOURCE, 1, DO_NOT_TAKE),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "forbidden-attribute" },
    },
  },
  // Rows 7 and 8: tables.
  {
    name: "text-directly-inside-table-rejected",
    input: toInput(
      S,
      single(
        "smpc.4.5",
        div(
          "<table>Dose<thead><tr><th>Frequency</th></tr></thead><tbody><tr><td>10 mg</td></tr><tr><td>Once daily</td></tr><tr><td>20 mg</td></tr><tr><td>Twice daily</td></tr></tbody></table>",
        ),
        [spanFor(S, 3, TABLE)],
      ),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.5": "malformed-narrative" },
      reasons: { "smpc.4.5": "table-content" },
    },
  },
  {
    // fidelity-norm/3.0.0: a span is allowed, and the grid is compared. Two source cells drawn as
    // one spanned cell is a different table, so it is a mismatch (it rejected in 2.0.0).
    name: "spanned-cell-against-separate-cells",
    input: toInput(
      S,
      single(
        "smpc.4.5",
        div(
          '<table><tr><th>Dose</th><th>Frequency</th></tr><tr><td colspan="2">10 mg Once daily</td></tr><tr><td>20 mg</td><td>Twice daily</td></tr></table>',
        ),
        [spanFor(S, 3, TABLE)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.5": "mismatch" } },
  },
  {
    name: "merged-cells-uneven-rows-rejected",
    input: toInput(
      S,
      single(
        "smpc.4.5",
        div(
          "<table><tr><th>Dose</th><th>Frequency</th></tr><tr><td>10 mg Once daily</td></tr><tr><td>20 mg</td><td>Twice daily</td></tr></table>",
        ),
        [spanFor(S, 3, TABLE)],
      ),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.5": "malformed-narrative" },
      reasons: { "smpc.4.5": "table-shape" },
    },
  },
  // Row 9: C1 controls and VT, decoded from references.
  {
    name: "c1-reference-rejected",
    input: toInput(
      AGE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Age 2&#133;11 years."), [
        spanFor(AGE_SOURCE, 1, AGE),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "malformed-narrative" },
      reasons: { "smpc.4.2.posology": "forbidden-character" },
    },
  },
  {
    name: "vertical-tab-reference-rejected",
    input: toInput(
      AGE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Age 2&#11;11 years."), [spanFor(AGE_SOURCE, 1, AGE)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "malformed-narrative" },
      reasons: { "smpc.4.2.posology": "forbidden-character" },
    },
  },
  // Row 10: `<br>` as a start tag.
  {
    name: "br-start-tag-rejected",
    input: toInput(
      DO_NOT_TAKE_SOURCE,
      single("smpc.4.4", paragraphs("Do<br>not</br> take with alcohol."), [
        spanFor(DO_NOT_TAKE_SOURCE, 1, DO_NOT_TAKE),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "malformed-narrative" },
      reasons: { "smpc.4.4": "void-element" },
    },
  },
  // Section 2 on page text: a text layer decoded as Latin-1 carries C1 controls, and a page
  // break written as U+000C is not page text.
  {
    name: "c1-control-in-page-text",
    input: toInput(
      C1_PAGE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take one tablet daily."), [
        spanFor(C1_PAGE_SOURCE, 1, "Take one tablet daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "span-not-found" },
      reasons: { "smpc.4.2.posology": "page-malformed" },
    },
  },
  {
    name: "form-feed-in-page-text",
    input: toInput(
      FORM_FEED_PAGE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take one tablet daily."), [
        spanFor(FORM_FEED_PAGE_SOURCE, 1, "Take one tablet daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "span-not-found" },
      reasons: { "smpc.4.2.posology": "page-malformed" },
    },
  },
  {
    name: "bidi-control-in-page-text",
    input: toInput(
      BIDI_PAGE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take one tablet daily."), [
        spanFor(BIDI_PAGE_SOURCE, 1, "Take one tablet daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "span-not-found" },
      reasons: { "smpc.4.2.posology": "page-malformed" },
    },
  },
  // Review C2: a span edge must touch whitespace or a body edge. Punctuation inside a number
  // (`.` of `1.5`, `−` of `−20`, `,` of `1,000`) and a hyphen inside a word are not boundaries.
  {
    name: "span-ends-inside-decimal",
    input: toInput(
      DECIMAL_SOURCE,
      single("smpc.4.2.posology", paragraphs("Maximum dose is 1"), [
        spanFor(DECIMAL_SOURCE, 1, "Maximum dose is 1"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-after-decimal-before-space",
    input: toInput(
      DECIMAL_SOURCE,
      single("smpc.4.2.posology", paragraphs("Maximum dose is 1.5"), [
        spanFor(DECIMAL_SOURCE, 1, "Maximum dose is 1.5"),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "span-ends-before-thousands-separator",
    input: toInput(
      THOUSANDS_SOURCE,
      single("smpc.4.2.posology", paragraphs("Give 1"), [spanFor(THOUSANDS_SOURCE, 1, "Give 1")]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-starts-after-minus-sign",
    input: toInput(
      MINUS_SOURCE,
      single("smpc.6.4", paragraphs("20 \u00b0C."), [spanFor(MINUS_SOURCE, 1, "20 \u00b0C.")]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.6.4": "invalid-provenance" },
      reasons: { "smpc.6.4": "word-cut" },
    },
  },
  {
    name: "span-starts-at-minus-sign-after-space",
    input: toInput(
      MINUS_SOURCE,
      single("smpc.6.4", paragraphs("\u221220 \u00b0C."), [
        spanFor(MINUS_SOURCE, 1, "\u221220 \u00b0C."),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  {
    name: "span-starts-after-decimal-point",
    input: toInput(
      HALF_DOSE_SOURCE,
      single("smpc.4.2.posology", paragraphs("5 mg."), [spanFor(HALF_DOSE_SOURCE, 1, "5 mg.")]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-starts-after-hyphen-in-word",
    input: toInput(
      COMPOUND_SOURCE,
      single("smpc.4.4", paragraphs("steroidal drugs."), [
        spanFor(COMPOUND_SOURCE, 1, "steroidal drugs."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "invalid-provenance" },
      reasons: { "smpc.4.4": "word-cut" },
    },
  },
  {
    // A false failure the rule accepts: a span that begins with a space still has to be preceded
    // by whitespace, because only the code points before it are read.
    name: "span-starting-with-space-after-word",
    input: toInput(
      COMPOUND_SOURCE,
      single("smpc.4.4", paragraphs("drugs."), [spanFor(COMPOUND_SOURCE, 1, " drugs.")]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.4": "invalid-provenance" },
      reasons: { "smpc.4.4": "word-cut" },
    },
  },
  // Review C3: a bullet glyph mid-line is content, and U+2219 and U+2043 are never bullets.
  {
    name: "bullet-operator-between-numbers",
    input: toInput(
      TABLE_ROW_SOURCE,
      single("smpc.4.2.posology", paragraphs("Dose (mg) 2\u221910"), [
        spanFor(TABLE_ROW_SOURCE, 1, TABLE_ROW),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "bullet-between-numbers-mid-line",
    input: toInput(
      TABLE_ROW_SOURCE,
      single("smpc.4.2.posology", paragraphs("Dose (mg) 2 \u2022 10"), [
        spanFor(TABLE_ROW_SOURCE, 1, TABLE_ROW),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review C1: whitespace other than TAB, LF, CR and SPACE inside a tag makes it malformed.
  {
    name: "no-break-space-in-sup-tag",
    input: toInput(
      RAISED_SOURCE,
      single("smpc.4.2.posology", paragraphs("Count 10<sup\u00a0>6</sup\u00a0>/L"), [
        spanFor(RAISED_SOURCE, 1, RAISED),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "malformed-narrative" },
      reasons: { "smpc.4.2.posology": "malformed-tag" },
    },
  },
  {
    name: "ascii-space-in-sup-tag-verifies",
    input: toInput(
      RAISED_SOURCE,
      single("smpc.4.2.posology", paragraphs("Count 10<sup >6</sup\t>/L"), [
        spanFor(RAISED_SOURCE, 1, RAISED),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  // Second review, round 2, item 1: a line break in narrative text is a space, so a bullet after
  // it is mid-line (content), as a renderer draws it.
  {
    name: "bullet-after-raw-line-feed-in-paragraph",
    input: toInput(
      BULLET_LINE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2\n\u2022 10 mg"), [
        spanFor(BULLET_LINE_SOURCE, 1, BULLET_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "bullet-after-line-feed-reference-in-paragraph",
    input: toInput(
      BULLET_LINE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2&#10;\u2022 10 mg"), [
        spanFor(BULLET_LINE_SOURCE, 1, BULLET_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "bullet-after-raw-line-feed-against-plain-text",
    input: toInput(
      PLAIN_LINE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2\n\u2022 10 mg"), [
        spanFor(PLAIN_LINE_SOURCE, 1, PLAIN_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "white-bullet-after-raw-line-feed",
    input: toInput(
      WHITE_BULLET_SOURCE,
      single("smpc.6.4", paragraphs("Store below 25\n\u25e6 C"), [
        spanFor(WHITE_BULLET_SOURCE, 1, WHITE_BULLET_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.6.4": "mismatch" } },
  },
  {
    name: "bullet-after-br-is-a-list-item",
    input: toInput(
      BULLET_LINE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2<br/>\u2022 10 mg"), [
        spanFor(BULLET_LINE_SOURCE, 1, BULLET_LINE),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  // Item 2: a number grouped with a space, U+00A0, U+202F, U+2009 or U+2007 cannot be cut.
  {
    name: "span-ends-inside-space-grouped-number",
    input: toInput(
      GROUPED_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10"), [
        spanFor(GROUPED_SPACE_SOURCE, 1, "The maximum dose is 10"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-inside-no-break-space-grouped-number",
    input: toInput(
      GROUPED_NBSP_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10"), [
        spanFor(GROUPED_NBSP_SOURCE, 1, "The maximum dose is 10"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-inside-narrow-no-break-space-grouped-number",
    input: toInput(
      GROUPED_NNBSP_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10"), [
        spanFor(GROUPED_NNBSP_SOURCE, 1, "The maximum dose is 10"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-inside-thin-space-grouped-number",
    input: toInput(
      GROUPED_THIN_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10"), [
        spanFor(GROUPED_THIN_SOURCE, 1, "The maximum dose is 10"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-inside-figure-space-grouped-number",
    input: toInput(
      GROUPED_FIGURE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10"), [
        spanFor(GROUPED_FIGURE_SOURCE, 1, "The maximum dose is 10"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-starts-inside-space-grouped-number",
    input: toInput(
      GROUPED_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("000 IU daily."), [
        spanFor(GROUPED_SPACE_SOURCE, 1, "000 IU daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-starts-inside-no-break-space-grouped-number",
    input: toInput(
      GROUPED_NBSP_SOURCE,
      single("smpc.4.2.posology", paragraphs("000 IU daily."), [
        spanFor(GROUPED_NBSP_SOURCE, 1, "000 IU daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-after-grouped-number",
    input: toInput(
      GROUPED_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10 000 IU"), [
        spanFor(GROUPED_SPACE_SOURCE, 1, "The maximum dose is 10 000 IU"),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "span-ends-at-number-before-line-feed-and-number",
    input: toInput(
      NUMBER_AT_LINE_END_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2"), [
        spanFor(NUMBER_AT_LINE_END_SOURCE, 1, "Take 2"),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "span-starts-at-number-after-line-feed-and-number",
    input: toInput(
      NUMBER_AT_LINE_END_SOURCE,
      single("smpc.4.2.posology", paragraphs("10 mg is the daily dose."), [
        spanFor(NUMBER_AT_LINE_END_SOURCE, 1, "10 mg is the daily dose."),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  // Item 3: a bullet in a table cell is content on both sides (a line with U+0009 is a row).
  {
    name: "bullet-in-later-cell-against-row",
    input: toInput(
      ROW_SOURCE,
      single("smpc.4.2.posology", div("<table><tr><td>2</td><td>\u2022 10</td></tr></table>"), [
        spanFor(ROW_SOURCE, 1, ROW),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "bullet-in-later-cell-against-bullet-row",
    input: toInput(
      BULLET_ROW_SOURCE,
      single("smpc.4.2.posology", div("<table><tr><td>2</td><td>\u2022 10</td></tr></table>"), [
        spanFor(BULLET_ROW_SOURCE, 1, BULLET_ROW),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "bullet-in-first-cell-against-row",
    input: toInput(
      FIRST_CELL_ROW_SOURCE,
      single("smpc.4.2.posology", div("<table><tr><td>\u2022 10</td><td>2</td></tr></table>"), [
        spanFor(FIRST_CELL_ROW_SOURCE, 1, FIRST_CELL_ROW),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "bullet-in-first-cell-against-bullet-row",
    input: toInput(
      FIRST_CELL_BULLET_SOURCE,
      single("smpc.4.2.posology", div("<table><tr><td>\u2022 10</td><td>2</td></tr></table>"), [
        spanFor(FIRST_CELL_BULLET_SOURCE, 1, FIRST_CELL_BULLET_ROW),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "bullet-row-against-cells-without-bullet",
    input: toInput(
      FIRST_CELL_BULLET_SOURCE,
      single("smpc.4.2.posology", div("<table><tr><td>10</td><td>2</td></tr></table>"), [
        spanFor(FIRST_CELL_BULLET_SOURCE, 1, FIRST_CELL_BULLET_ROW),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "bullet-in-cell-paragraph-against-row",
    input: toInput(
      ROW_SOURCE,
      single(
        "smpc.4.2.posology",
        div("<table><tr><td>2</td><td><p>\u2022 10</p></td></tr></table>"),
        [spanFor(ROW_SOURCE, 1, ROW)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // The verifier reads a page slice from its line terminator (section 6): a bullet at the start
  // of a line is a list item on both sides, and a bullet mid-line is content even when a span
  // starts at it.
  {
    name: "list-item-at-body-start-verifies",
    input: toInput(
      LIST_ITEM_SOURCE,
      single("smpc.6.4", div("<ul><li>Keep in the outer carton.</li></ul>"), [
        spanFor(LIST_ITEM_SOURCE, 1, LIST_ITEM),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  {
    // A false failure the rule accepts: at a page start with no header there is no line
    // terminator to read from, so the bullet is content.
    name: "list-item-at-page-start-without-header",
    input: toInput(
      LIST_ITEM_PAGE_START_SOURCE,
      single("smpc.6.4", div("<ul><li>Keep in the outer carton.</li></ul>"), [
        spanFor(LIST_ITEM_PAGE_START_SOURCE, 1, LIST_ITEM),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.6.4": "mismatch" } },
  },
  {
    name: "span-starting-at-mid-line-bullet",
    input: toInput(
      MID_LINE_BULLET_SOURCE,
      single("smpc.4.2.posology", paragraphs("10 mg daily."), [
        spanFor(MID_LINE_BULLET_SOURCE, 1, "\u2022 10 mg daily."),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "section-continues-over-bullet-at-page-head",
    input: toInput(
      BULLET_AT_PAGE_HEAD,
      single("smpc.6.4", paragraphs("Keep the bottle in the outer carton."), [
        spanFor(BULLET_AT_PAGE_HEAD, 1, "Keep the bottle"),
        spanFor(BULLET_AT_PAGE_HEAD, 2, "in the outer carton."),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.6.4": "verified" } },
  },
  // Review round 3, fix 1: the digit-group rule reads the span's first and last code points that
  // are not edge whitespace, so a span ending or starting in the whitespace between two groups
  // still cuts the number.
  {
    name: "span-ends-with-space-inside-double-spaced-number",
    input: toInput(
      GROUPED_DOUBLE_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10"), [
        spanFor(GROUPED_DOUBLE_SPACE_SOURCE, 1, "The maximum dose is 10 "),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-with-thin-space-inside-number",
    input: toInput(
      GROUPED_THIN_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10\u2009"), [
        spanFor(GROUPED_THIN_THEN_SPACE_SOURCE, 1, "The maximum dose is 10\u2009"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    // The inner code point skips every gap (section 6), a thin space too: a span ending in U+202F
    // before a space still ends inside the number, even with the U+202F in the narrative.
    name: "span-ends-with-narrow-no-break-space-inside-number",
    input: toInput(
      GROUPED_NNBSP_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10\u202f"), [
        spanFor(GROUPED_NNBSP_THEN_SPACE_SOURCE, 1, "The maximum dose is 10\u202f"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-starts-with-space-inside-double-spaced-number",
    input: toInput(
      GROUPED_DOUBLE_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("000 IU daily."), [
        spanFor(GROUPED_DOUBLE_SPACE_SOURCE, 1, " 000 IU daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  // Fix 2: the U+0009 status of a slice's last line is that of the whole page line, so a span
  // that stops before a row's U+0009 still keeps a bullet in its first cell as content.
  {
    name: "row-cut-before-tab-bullet-kept",
    input: toInput(
      INTRO_THEN_ROW_SOURCE,
      single("smpc.4.2.posology", paragraphs("Intro text", "Adults"), [
        spanFor(INTRO_THEN_ROW_SOURCE, 1, "Intro text\n\u2022 Adults"),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "row-cell-cut-before-tab-bullet-kept",
    input: toInput(
      INTRO_THEN_ROW_SOURCE,
      single("smpc.4.2.posology", paragraphs("Adults"), [
        spanFor(INTRO_THEN_ROW_SOURCE, 1, "\u2022 Adults"),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    // Since fidelity-norm/3.0.0 a narrative table carries its grid, so it verifies only against a
    // page that carries the same grid, which this 2.0.0-shaped row does not.
    name: "row-cell-cut-before-tab-against-cell",
    input: toInput(
      INTRO_THEN_ROW_SOURCE,
      single("smpc.4.2.posology", div("<table><tr><td>\u2022 Adults</td></tr></table>"), [
        spanFor(INTRO_THEN_ROW_SOURCE, 1, "\u2022 Adults"),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Fix 3 (section 7): a list bullet must be followed by U+0020, not U+0009; with U+0009 the
  // line reads as a table row and a list narrative fails (safe, a false failure).
  {
    name: "list-bullet-then-tab-against-list",
    input: toInput(
      LIST_TAB_ITEM_SOURCE,
      single("smpc.4.2.posology", div("<ul><li>Adults: 10 mg</li></ul>"), [
        spanFor(LIST_TAB_ITEM_SOURCE, 1, LIST_TAB_ITEM),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "list-bullet-then-space-against-list",
    input: toInput(
      LIST_SPACE_ITEM_SOURCE,
      single("smpc.4.2.posology", div("<ul><li>Adults: 10 mg</li></ul>"), [
        spanFor(LIST_SPACE_ITEM_SOURCE, 1, LIST_SPACE_ITEM),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  // -------------------------------------------------------------------------------------------
  // fidelity-norm/3.0.0: an `ol`'s numbers, a table's grid and a picture's source are compared.
  {
    name: "ordered-list-number-verifies",
    input: toInput(
      NUMBERED_ITEM_SOURCE,
      single("smpc.4.2.posology", div('<ol start="3"><li>Take one tablet.</li></ol>'), [
        spanFor(NUMBERED_ITEM_SOURCE, 1, NUMBERED_ITEM),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "ordered-list-other-number-mismatches",
    input: toInput(
      NUMBERED_ITEM_SOURCE,
      single("smpc.4.2.posology", div("<ol><li>Take one tablet.</li></ol>"), [
        spanFor(NUMBERED_ITEM_SOURCE, 1, NUMBERED_ITEM),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "ordered-list-other-style-mismatches",
    input: toInput(
      NUMBERED_ITEM_SOURCE,
      single("smpc.4.2.posology", div('<ol type="i" start="3"><li>Take one tablet.</li></ol>'), [
        spanFor(NUMBERED_ITEM_SOURCE, 1, NUMBERED_ITEM),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "row-span-against-row-span-verifies",
    input: toInput(
      SPANNED_DOSE_SOURCE,
      single("smpc.4.2.posology", div(SPANNED_DOSE_TABLE), [
        spanFor(SPANNED_DOSE_SOURCE, 1, SPANNED_DOSE),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    // The first review's case 3: the same words, but the span draws the dose against every row.
    name: "row-span-against-empty-cells-mismatches",
    input: toInput(
      EMPTY_CELLS_DOSE_SOURCE,
      single("smpc.4.2.posology", div(SPANNED_DOSE_TABLE), [
        spanFor(EMPTY_CELLS_DOSE_SOURCE, 1, EMPTY_CELLS_DOSE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "empty-cells-against-row-span-mismatches",
    input: toInput(
      SPANNED_DOSE_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>Adults</td><td>10 mg</td></tr><tr><td>Children</td><td></td></tr><tr><td>Elderly</td><td></td></tr></table>",
        ),
        [spanFor(SPANNED_DOSE_SOURCE, 1, SPANNED_DOSE)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    // 2.0.0's stated residual, closed: a value in another column is a different table.
    name: "value-in-another-column-mismatches",
    input: toInput(
      DOSE_IN_SECOND_COLUMN_SOURCE,
      single(
        "smpc.4.2.posology",
        div("<table><tr><td>Adults</td><td></td><td>10 mg</td></tr></table>"),
        [spanFor(DOSE_IN_SECOND_COLUMN_SOURCE, 1, DOSE_IN_SECOND_COLUMN)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "picture-with-source-bytes-verifies",
    input: toInput(
      PICTURE_LINE_SOURCE,
      single("smpc.4.2.posology", div(`<p>See <img src="${PICTURE_SOURCE}"/> below.</p>`), [
        spanFor(PICTURE_LINE_SOURCE, 1, PICTURE_LINE),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "picture-with-other-bytes-mismatches",
    input: toInput(
      PICTURE_LINE_SOURCE,
      single(
        "smpc.4.2.posology",
        div('<p>See <img src="data:image/png;base64,AA=="/> below.</p>'),
        [spanFor(PICTURE_LINE_SOURCE, 1, PICTURE_LINE)],
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "picture-missing-mismatches",
    input: toInput(
      PICTURE_LINE_SOURCE,
      single("smpc.4.2.posology", paragraphs("See below."), [
        spanFor(PICTURE_LINE_SOURCE, 1, PICTURE_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review round 4: a cell continued across a page break keeps its bullet as content.
  {
    name: "cell-across-page-break-keeps-its-bullet",
    input: toInput(
      CELL_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div("<table><tr><td>Dose</td><td>2 \u2022 10</td></tr></table>"),
        cellAcrossPagesSpans(),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "cell-across-page-break-without-its-bullet-mismatches",
    input: toInput(
      CELL_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div("<table><tr><td>Dose</td><td>2 10</td></tr></table>"),
        cellAcrossPagesSpans(),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review round 5: a word in a cell hyphenated at a page break is one word.
  {
    name: "cell-word-hyphenated-across-page-break-joins",
    input: toInput(
      CELL_WORD_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>Dose</td><td>Adults with renal impairment</td><td>10 mg</td></tr></table>",
        ),
        cellWordAcrossPagesSpans(),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "cell-word-hyphenated-across-page-break-split-mismatches",
    input: toInput(
      CELL_WORD_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>Dose</td><td>Adults with renal impair ment</td><td>10 mg</td></tr></table>",
        ),
        cellWordAcrossPagesSpans(),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review round 6: a row broken across a page keeps each cell's text whole and in slot order.
  {
    name: "row-broken-across-page-keeps-its-cells",
    input: toInput(
      ROW_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>Dose</td><td>Adults with renal impairment</td><td>10 mg once daily</td><td>Oral</td></tr></table>",
        ),
        rowAcrossPagesSpans(),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "row-broken-across-page-with-a-word-in-the-next-cell-mismatches",
    input: toInput(
      ROW_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>Dose</td><td>Adults with renal</td><td>impairment 10 mg once daily</td><td>Oral</td></tr></table>",
        ),
        rowAcrossPagesSpans(),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review round 7: tables across a page break (section 7).
  {
    name: "page-footnote-between-table-parts-after-the-table",
    input: toInput(
      FOOTNOTE_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>Adults</td><td>10 mg\u00b9</td></tr><tr><td>Children</td><td>5 mg</td></tr></table><p>\u00b9 Not studied in hepatic impairment.</p>",
        ),
        spansOf(FOOTNOTE_ACROSS_PAGES, FOOTNOTE_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "page-footnote-moved-into-a-cell-mismatches",
    input: toInput(
      FOOTNOTE_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>Adults</td><td>10 mg\u00b9 \u00b9 Not studied in hepatic impairment.</td></tr><tr><td>Children</td><td>5 mg</td></tr></table>",
        ),
        spansOf(FOOTNOTE_ACROSS_PAGES, FOOTNOTE_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "row-span-broken-across-page-keeps-its-slot",
    input: toInput(
      SPAN_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          '<table><tr><td rowspan="2">Adults with renal impairment</td><td>10 mg</td></tr><tr><td>5 mg</td></tr></table>',
        ),
        spansOf(SPAN_ACROSS_PAGES, SPAN_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "row-span-word-moved-to-the-next-row-mismatches",
    input: toInput(
      SPAN_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          '<table><tr><td rowspan="2">Adults with renal</td><td>10 mg</td></tr><tr><td>impairment 5 mg</td></tr></table>',
        ),
        spansOf(SPAN_ACROSS_PAGES, SPAN_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "repeated-footer-row-where-the-table-last-has-it",
    input: toInput(
      FOOTER_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tbody><tr><td>a</td><td>1</td></tr><tr><td>b</td><td>1</td></tr></tbody><tfoot><tr><td>Total</td><td>2</td></tr></tfoot></table>",
        ),
        spansOf(FOOTER_ACROSS_PAGES, FOOTER_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "repeated-footer-row-mid-table-mismatches",
    input: toInput(
      FOOTER_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><td>a</td><td>1</td></tr><tr><td>Total</td><td>2</td></tr><tr><td>b</td><td>1</td></tr></table>",
        ),
        spansOf(FOOTER_ACROSS_PAGES, FOOTER_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review round 8: a caption across a page break, a wrap before a bullet, a repeated header row.
  {
    name: "caption-across-page-break-keeps-its-bullet",
    input: toInput(
      CAPTION_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div("<table><caption>Dose 2 \u2022 10 mg</caption><tr><td>A</td></tr></table>"),
        spansOf(CAPTION_ACROSS_PAGES, CAPTION_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "caption-across-page-break-without-its-bullet-mismatches",
    input: toInput(
      CAPTION_ACROSS_PAGES_SOURCE,
      single(
        "smpc.4.2.posology",
        div("<table><caption>Dose 2 10 mg</caption><tr><td>A</td></tr></table>"),
        spansOf(CAPTION_ACROSS_PAGES, CAPTION_ACROSS_PAGES_SOURCE),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "wrap-before-mid-line-bullet-keeps-it",
    input: toInput(
      WRAP_BEFORE_BULLET_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2 \u2022 10 mg daily."), [
        spanFor(WRAP_BEFORE_BULLET_SOURCE, 1, WRAP_BEFORE_BULLET),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "wrap-before-mid-line-bullet-dropped-mismatches",
    input: toInput(
      WRAP_BEFORE_BULLET_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2 10 mg daily."), [
        spanFor(WRAP_BEFORE_BULLET_SOURCE, 1, WRAP_BEFORE_BULLET),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "wrap-before-mid-line-bullet-as-a-list-item-mismatches",
    input: toInput(
      WRAP_BEFORE_BULLET_SOURCE,
      single("smpc.4.2.posology", div("<p>Take 2</p><ul><li>10 mg daily.</li></ul>"), [
        spanFor(WRAP_BEFORE_BULLET_SOURCE, 1, WRAP_BEFORE_BULLET),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "repeated-header-row-excluded-verifies-once",
    input: toInput(
      REPEATED_HEADER_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><thead><tr><th>Population</th><th>Dose</th></tr></thead><tbody><tr><td>Adults</td><td>10 mg</td></tr><tr><td>Children</td><td>5 mg</td></tr></tbody></table>",
        ),
        spansOf(REPEATED_HEADER_BODIES, REPEATED_HEADER_SOURCE),
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "repeated-header-row-written-twice-mismatches",
    input: toInput(
      REPEATED_HEADER_SOURCE,
      single(
        "smpc.4.2.posology",
        div(
          "<table><tr><th>Population</th><th>Dose</th></tr><tr><td>Adults</td><td>10 mg</td></tr><tr><th>Population</th><th>Dose</th></tr><tr><td>Children</td><td>5 mg</td></tr></table>",
        ),
        spansOf(REPEATED_HEADER_BODIES, REPEATED_HEADER_SOURCE),
      ),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review round 12: U+1680 is content, drawn as a stroke.
  {
    name: "ogham-space-mark-against-a-space-mismatches",
    input: toInput(
      OGHAM_SOURCE,
      single("smpc.4.2.posology", paragraphs(SPACE_LINE), [spanFor(OGHAM_SOURCE, 1, OGHAM_LINE)]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "space-against-an-ogham-space-mark-mismatches",
    input: toInput(
      SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2&#x1680;10 mg daily."), [
        spanFor(SPACE_SOURCE, 1, SPACE_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  {
    name: "hair-space-against-a-space-mismatches",
    input: toInput(
      HAIR_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2&#x200A;10 mg tablets."), [
        spanFor(HAIR_SOURCE, 1, HAIR_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // Review round 14: the digit-group rule reads past every gap, content spaces and code points
  // drawn as nothing included, on both edges.
  {
    name: "span-starts-after-thin-space-inside-number",
    input: toInput(
      GROUPED_THIN_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("000 IU daily."), [
        spanFor(GROUPED_THIN_THEN_SPACE_SOURCE, 1, " 000 IU daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-with-invisible-separator-inside-number",
    input: toInput(
      GROUPED_INVISIBLE_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10&#x2063;"), [
        spanFor(GROUPED_INVISIBLE_THEN_SPACE_SOURCE, 1, "The maximum dose is 10\u2063"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-starts-after-invisible-separator-inside-number",
    input: toInput(
      GROUPED_INVISIBLE_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("000 IU daily."), [
        spanFor(GROUPED_INVISIBLE_THEN_SPACE_SOURCE, 1, " 000 IU daily."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  // Review round 15: U+2800 and a supplementary-plane Default_Ignorable code point are gaps;
  // narrative of gaps alone draws nothing inked; U+1680, a stroke, is drawn; U+205F is content.
  {
    name: "span-ends-with-braille-blank-inside-number",
    input: toInput(
      GROUPED_BLANK_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10&#x2800;"), [
        spanFor(GROUPED_BLANK_THEN_SPACE_SOURCE, 1, "The maximum dose is 10\u2800"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "span-ends-with-tag-space-inside-number",
    input: toInput(
      GROUPED_TAG_SPACE_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10&#xE0020;"), [
        spanFor(GROUPED_TAG_SPACE_THEN_SPACE_SOURCE, 1, "The maximum dose is 10\u{E0020}"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "thin-space-alone-is-empty",
    input: toInput(S, single("smpc.4.1", paragraphs("&#x2009;"), [spanFor(S, 1, INDICATIONS)])),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "empty-narrative" },
    },
  },
  {
    name: "invisible-separator-and-braille-blank-alone-are-empty",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("&#x2063;&#x2800;&#x200D;"), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "empty-narrative" },
    },
  },
  {
    name: "ogham-space-mark-alone-is-drawn",
    input: toInput(
      OGHAM_ALONE_SOURCE,
      single("smpc.4.2.posology", paragraphs("&#x1680;"), [
        spanFor(OGHAM_ALONE_SOURCE, 1, "\u1680"),
      ]),
    ),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  {
    name: "span-ends-with-yi-blank-inside-number",
    input: toInput(
      GROUPED_YI_BLANK_THEN_SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("The maximum dose is 10&#xA4C5;"), [
        spanFor(GROUPED_YI_BLANK_THEN_SPACE_SOURCE, 1, "The maximum dose is 10\ua4c5"),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.2.posology": "invalid-provenance" },
      reasons: { "smpc.4.2.posology": "word-cut" },
    },
  },
  {
    name: "page-with-interlinear-annotation",
    input: toInput(
      ANNOTATION_PAGE_SOURCE,
      single("smpc.4.1", paragraphs("Dose information."), [
        spanFor(ANNOTATION_PAGE_SOURCE, 1, "Dose information."),
      ]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "span-not-found" },
      reasons: { "smpc.4.1": "page-malformed" },
    },
  },
  {
    name: "blank-letters-alone-are-empty",
    input: toInput(
      S,
      single("smpc.4.1", paragraphs("&#x1878;&#xA4C5;"), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: {
      status: "failed",
      sections: { "smpc.4.1": "malformed-narrative" },
      reasons: { "smpc.4.1": "empty-narrative" },
    },
  },
  {
    name: "medium-mathematical-space-against-a-space-mismatches",
    input: toInput(
      SPACE_SOURCE,
      single("smpc.4.2.posology", paragraphs("Take 2&#x205F;10 mg daily."), [
        spanFor(SPACE_SOURCE, 1, SPACE_LINE),
      ]),
    ),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // fidelity-norm/3.2.0: a certified Word source (section 7's certified Word rule, ADR 0006).
  // One page per section, wholly body; the page as zone_a.word_epi writes it from a read, the
  // narrative as its builder writes it from the same read. Reviewed by hand: the page begins with
  // a line break, writes each list label and a space (a bullet is then removed on both sides),
  // folds the raised 9 and the lowered 0 and minus (keeping the lowered infinity), leaves the
  // bullet in a cell out, and writes the colspan and the rowspan as grid slots.
  {
    name: "certified-word-section",
    input: (() => {
      const source = wordSource(WORD_PAGE);
      return toInput(
        source,
        single("smpc.4.2.posology", WORD_NARRATIVE, [spanFor(source, 1, WORD_PAGE)]),
      );
    })(),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  // The same page against a narrative whose merged cell is not merged: the third row's first slot
  // is a cell of its own, not the cell above.
  {
    name: "certified-word-rowspan-dropped",
    input: (() => {
      const source = wordSource(WORD_PAGE);
      const unmerged = WORD_NARRATIVE.replace('<td rowspan="2">', "<td>").replace(
        "<tr><td><p>2.5 mg",
        "<tr><td></td><td><p>2.5 mg",
      );
      return toInput(
        source,
        single("smpc.4.2.posology", unmerged, [spanFor(source, 1, WORD_PAGE)]),
      );
    })(),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // ... and against the numbered list drawn as bullets, its numbers lost.
  {
    name: "certified-word-list-number",
    input: (() => {
      const source = wordSource(WORD_PAGE);
      const later = WORD_NARRATIVE.replace("<ol><li>if", "<ul><li>if").replace(
        "doubles.</li></ol>",
        "doubles.</li></ul>",
      );
      return toInput(source, single("smpc.4.2.posology", later, [spanFor(source, 1, WORD_PAGE)]));
    })(),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // A line after a break that starts with a bullet glyph is written with a leading tab, so the
  // glyph is content on the page; a narrative drawing it after a br reads it as a list bullet, and
  // fails (zone_a.word_epi refuses such a paragraph before it gets here).
  {
    name: "certified-word-bullet-after-break",
    input: (() => {
      const page = "\nTake 2\n\t• 10 mg\n";
      const source = wordSource(page);
      return toInput(
        source,
        single("smpc.4.2.posology", div("<p>Take 2<br/>• 10 mg</p>"), [spanFor(source, 1, page)]),
      );
    })(),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
  // fidelity-norm/3.3.0: the template's grey is a silver span the page does not mark, and a list
  // an HTML list cannot draw (a dash, "a)") is written as its labels' text, each item a p, which
  // is the line the page already writes. Reviewed by hand.
  {
    name: "certified-word-grey-and-labels-as-text",
    input: (() => {
      const page =
        "\nReport it via the national system.\n- Adults: 10 mg.\n- Children: 5 mg.\n" +
        "a) if the count falls;\n";
      const narrative = div(
        '<p>Report it <span style="background-color: silver;">via the national system</span>.</p>' +
          "<p>- Adults: 10 mg.</p><p>- Children: 5 mg.</p><p>a) if the count falls;</p>",
      );
      const source = wordSource(page);
      return toInput(source, single("smpc.4.8", narrative, [spanFor(source, 1, page)]));
    })(),
    expect: { status: "passed", sections: { "smpc.4.8": "verified" } },
  },
  // ... and the same dashes drawn as an HTML list: its discs are no dash, and fail.
  {
    name: "certified-word-dashes-as-discs",
    input: (() => {
      const page = "\n- Adults: 10 mg.\n- Children: 5 mg.\n";
      const narrative = div("<ul><li>Adults: 10 mg.</li><li>Children: 5 mg.</li></ul>");
      const source = wordSource(page);
      return toInput(source, single("smpc.4.8", narrative, [spanFor(source, 1, page)]));
    })(),
    expect: { status: "failed", sections: { "smpc.4.8": "mismatch" } },
  },
  // fidelity-norm/3.4.0: the tab after a typed label is a space in a table cell as outside one,
  // and a caption's "Table 1:", a raised footnote key and a non-breaking hyphen are typed labels.
  // The page as zone_a.word_epi writes it from the read and the narrative it builds from the same
  // read. Reviewed by hand: the caption and the raised key below the table are lines; in the cells
  // the raised "a" is kept as a letter on the page and the hyphen kept as U+2011, each followed by
  // the space written for its tab.
  {
    name: "certified-word-typed-labels-in-a-cell",
    input: (() => {
      const page =
        "\nTable 1: Dose by age\n﷐\n﷒\t﷓\tAge\t﷓\tDose\n" +
        "﷒\t﷓\ta Over 18 years\t﷓\t‑ 10 mg\n﷑\na See section 5.2.\n";
      const narrative = div(
        "<p>Table 1: Dose by age</p><table><tr><td><p>Age</p></td><td><p>Dose</p></td></tr>" +
          "<tr><td><p><sup>a</sup> Over 18 years</p></td><td><p>‑ 10 mg</p></td></tr>" +
          "</table><p><sup>a</sup> See section 5.2.</p>",
      );
      const source = wordSource(page);
      return toInput(source, single("smpc.4.2.posology", narrative, [spanFor(source, 1, page)]));
    })(),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  // fidelity-norm/3.5.0: a grid column no cell starts at is dropped; the grid columns a row leaves
  // out at its start or end are an empty cell (ADR 0006 decision 10); a lone tab and an indent's
  // tab are spaces (decisions 11 and 12); a strike over trailing spaces is left out. The page as
  // zone_a.word_epi writes it from a read of three grid columns (column 1 starts no cell; the
  // third row leaves out column 2, the fourth columns 0 and 1) and the narrative it builds from
  // the same read. Reviewed by hand: two columns, each value in its cell, an empty cell where
  // Word draws none, and the spaces written for the tabs.
  {
    name: "certified-word-dropped-column-and-empty-cells",
    input: (() => {
      const page =
        "\n Table 2: Dose by weight\n﷐\n﷒\t﷓\tWeight\t﷓\tDose\n" +
        "﷒\t﷓\tUnder 40 kg\t﷓\t5 mg (one tablet)\n" +
        "﷒\t﷓\t40 kg or more\t﷓\t\n﷒\t﷓\t\t﷓\tSee 4.4\n﷑\n" +
        "Take with food.  \n";
      const source = wordSource(page);
      return toInput(
        source,
        single("smpc.4.2.posology", WORD_GRID_NARRATIVE, [spanFor(source, 1, page)]),
      );
    })(),
    expect: { status: "passed", sections: { "smpc.4.2.posology": "verified" } },
  },
  // ... and against a narrative with the third row's empty cell before its value, not after it:
  // the value is in another column, and fails.
  {
    name: "certified-word-empty-cell-on-the-wrong-side",
    input: (() => {
      const page =
        "\n Table 2: Dose by weight\n﷐\n﷒\t﷓\tWeight\t﷓\tDose\n" +
        "﷒\t﷓\tUnder 40 kg\t﷓\t5 mg (one tablet)\n" +
        "﷒\t﷓\t40 kg or more\t﷓\t\n﷒\t﷓\t\t﷓\tSee 4.4\n﷑\n" +
        "Take with food.  \n";
      const moved = WORD_GRID_NARRATIVE.replace(
        "<tr><td><p>40 kg or more</p></td><td></td></tr>",
        "<tr><td></td><td><p>40 kg or more</p></td></tr>",
      );
      const source = wordSource(page);
      return toInput(source, single("smpc.4.2.posology", moved, [spanFor(source, 1, page)]));
    })(),
    expect: { status: "failed", sections: { "smpc.4.2.posology": "mismatch" } },
  },
];

export const throwCases: ThrowCase[] = [
  {
    name: "normalization-version-mismatch",
    input: { ...toInput(S, baseSpecs().slice(0, 1)), normalizationVersion: "fidelity-norm/0.9.0" },
  },
  {
    name: "duplicate-section-key",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      return { ...input, sections: [...input.sections, ...input.sections] };
    })(),
  },
  {
    name: "duplicate-provenance-key",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      return { ...input, provenance: [...input.provenance, ...input.provenance] };
    })(),
  },
  {
    name: "duplicate-page-number",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const [first] = input.source.pages;
      if (first === undefined) throw new Error("fixture");
      return { ...input, source: { ...input.source, pages: [...input.source.pages, first] } };
    })(),
  },
  {
    name: "invalid-body-range",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const pages = input.source.pages.map((page) => ({ ...page, bodyEnd: page.text.length + 5 }));
      return { ...input, source: { ...input.source, pages } };
    })(),
  },
  // 2.0.0: pages are numbered 1..N in array order, so a document cannot leave out the page a
  // section's first word would be read against.
  {
    name: "missing-page",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(2, 3));
      const pages = input.source.pages.filter(({ page }) => page !== 1);
      return { ...input, source: { ...input.source, pages } };
    })(),
  },
  {
    name: "pages-out-of-order",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const pages = [...input.source.pages].reverse();
      return { ...input, source: { ...input.source, pages } };
    })(),
  },
  {
    name: "page-numbering-starts-at-two",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const pages = input.source.pages.map((page) => ({ ...page, page: page.page + 1 }));
      return { ...input, source: { ...input.source, pages } };
    })(),
  },
  // Review L2: a span's page and offsets are integers, and a page number is not a boolean.
  {
    name: "span-page-boolean",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const [entry] = input.provenance;
      const [span] = entry?.spans ?? [];
      if (entry === undefined || span === undefined) throw new Error("fixture");
      const spans = [{ ...span, page: true as unknown as number }];
      return { ...input, provenance: [{ ...entry, spans }] };
    })(),
  },
  {
    name: "span-offset-boolean",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const [entry] = input.provenance;
      const [span] = entry?.spans ?? [];
      if (entry === undefined || span === undefined) throw new Error("fixture");
      const spans = [{ ...span, startOffset: false as unknown as number }];
      return { ...input, provenance: [{ ...entry, spans }] };
    })(),
  },
  {
    name: "span-offset-fractional",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const [entry] = input.provenance;
      const [span] = entry?.spans ?? [];
      if (entry === undefined || span === undefined) throw new Error("fixture");
      const spans = [{ ...span, endOffset: span.endOffset + 0.5 }];
      return { ...input, provenance: [{ ...entry, spans }] };
    })(),
  },
  {
    name: "page-number-boolean",
    input: (() => {
      const input = toInput(S, baseSpecs().slice(0, 1));
      const [first, ...rest] = input.source.pages;
      if (first === undefined) throw new Error("fixture");
      const pages = [{ ...first, page: true as unknown as number }, ...rest];
      return { ...input, source: { ...input.source, pages } };
    })(),
  },
];

// fidelity-norm/2.0.0, section 2 and section 3 step 5: C1 controls, U+000B, U+000C and the
// bidirectional controls reject on both sides; the accepting neighbours of each range are kept.
const NORMALIZATION_CASES_2_0_0: NormalizationCase[] = [
  // Second review, round 2, item 3: a bullet on a line that contains U+0009 (a table row) is
  // content; a U+0009 on another line does not matter.
  { name: "bullet-on-tab-line-kept", input: "x\n\u2022 10\t2", expected: "x \u2022 10 2" },
  { name: "bullet-after-tab-kept", input: "x\n\t\u2022 a", expected: "x \u2022 a" },
  { name: "bullet-on-later-tab-line-kept", input: "x\n\u2022 a\tb", expected: "x \u2022 a b" },
  {
    name: "bullet-with-tab-on-another-line-replaced",
    input: "x\t\ny\n\u2022 z",
    expected: "x y z",
  },
  // Step 4 (review C3): a bullet glyph is list structure only at the start of a line, followed
  // by whitespace; U+2219 and U+2043 are never bullets.
  { name: "bullet-line-start-replaced", input: "x\n• y", expected: "x y" },
  { name: "bullet-after-indent-replaced", input: "x\n  ◦ y", expected: "x y" },
  // The start of a text is not a line start (round 2): normalised text has no U+000A, so the
  // procedure stays idempotent. The scanner's text begins with U+000A and the verifier reads a
  // page slice from its line terminator (section 6), so both sides still see a list item there.
  { name: "bullet-at-text-start-kept", input: "▪ y", expected: "▪ y" },
  { name: "bullet-after-line-feed-at-text-start-replaced", input: "\n▪ y", expected: "y" },
  {
    name: "bullet-after-invisible-at-line-start-replaced",
    input: "\n\u200b• x",
    expected: "x",
  },
  { name: "bullets-in-a-row-replaced", input: "\n• • x", expected: "x" },
  { name: "bullet-mid-line-kept", input: "2 • 10", expected: "2 • 10" },
  { name: "bullet-without-following-space-kept", input: "•x", expected: "•x" },
  { name: "bullet-at-end-kept", input: "x\n•", expected: "x •" },
  { name: "bullet-after-cr-only-kept", input: "x\r• y", expected: "x • y" },
  { name: "bullet-before-bullet-kept", input: "•• x", expected: "•• x" },
  { name: "bullet-operator-is-content", input: "2∙10", expected: "2∙10" },
  { name: "bullet-operator-at-line-start-is-content", input: "∙ x", expected: "∙ x" },
  { name: "hyphen-bullet-is-content", input: "⁃ x", expected: "⁃ x" },
  { name: "allows-tab-lf-cr", input: "a\tb\nc\rd", expected: "a b c d" },
  { name: "rejects-vertical-tab", input: "a\u000bb", expected: { error: "forbidden-character" } },
  { name: "rejects-form-feed", input: "a\u000cb", expected: { error: "forbidden-character" } },
  { name: "rejects-next-line", input: "a\u0085b", expected: { error: "forbidden-character" } },
  { name: "rejects-c1-first", input: "a\u0080b", expected: { error: "forbidden-character" } },
  { name: "rejects-c1-last", input: "a\u009fb", expected: { error: "forbidden-character" } },
  {
    name: "rejects-latin-1-decoded-quote",
    input: "the patient\u0092s dose",
    expected: { error: "forbidden-character" },
  },
  { name: "accepts-tilde-before-delete", input: "a~b", expected: "a~b" },
  { name: "accepts-no-break-space-after-c1", input: "a\u00a0b", expected: "a b" },
  {
    name: "rejects-left-to-right-mark",
    input: "a\u200eb",
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-right-to-left-mark",
    input: "a\u200fb",
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-arabic-letter-mark",
    input: "a\u061cb",
    expected: { error: "forbidden-character" },
  },
  { name: "keeps-arabic-semicolon", input: "a؛b", expected: "a؛b" },
  {
    name: "rejects-bidi-embedding-first",
    input: "a\u202ab",
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-bidi-override-last",
    input: "a\u202eb",
    expected: { error: "forbidden-character" },
  },
  // Accepted next to the forbidden overrides; content from fidelity-norm/3.0.0, not a space.
  { name: "narrow-no-break-space-after-overrides", input: "a\u202fb", expected: "a\u202fb" },
  {
    name: "rejects-bidi-isolate-first",
    input: "a\u2066b",
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-bidi-isolate-last",
    input: "a\u2069b",
    expected: { error: "forbidden-character" },
  },
  { name: "keeps-format-character-after-isolates", input: "a\u206ab", expected: "a\u206ab" },
];

export const normalizationCases: NormalizationCase[] = [
  // Invisible characters are removed before NFC, so a composition they would otherwise block
  // happens in the first pass and the procedure stays idempotent.
  { name: "invisible-blocks-nfc", input: "cafe​́", expected: "café" },
  { name: "soft-hyphen-line-break", input: "intra­\nvenous", expected: "intravenous" },
  { name: "soft-hyphen-crlf", input: "intra­\r\nvenous", expected: "intravenous" },
  { name: "soft-hyphen-then-space-stays", input: "intra­ venous", expected: "intra venous" },
  { name: "ligature-then-combining", input: "ﬁ́", expected: "fí" },
  // Spaces drawn one or two pixels wide are content (fidelity-norm/3.0.0): "2" U+200A "10" looks
  // like "210".
  { name: "hair-space-is-content", input: "Take 2\u200a10 mg", expected: "Take 2\u200a10 mg" },
  { name: "thin-space-is-content", input: "10\u2009000 IU", expected: "10\u2009000 IU" },
  { name: "six-per-em-space-is-content", input: "2\u200610", expected: "2\u200610" },
  {
    name: "medium-mathematical-space-is-content",
    input: "Take 2\u205f10 mg",
    expected: "Take 2\u205f10 mg",
  },
  { name: "narrow-no-break-space-is-content", input: "10\u202f000", expected: "10\u202f000" },
  { name: "four-per-em-space-is-still-a-space", input: "2\u200510", expected: "2 10" },
  // U+1680 OGHAM SPACE MARK is drawn as a stroke, so it is content (fidelity-norm/3.0.0).
  {
    name: "ogham-space-mark-is-content",
    input: "Take 2\u168010 mg",
    expected: "Take 2\u168010 mg",
  },
  // A picture token is closed by U+FFFC, which composes with nothing, so NFC leaves its digits.
  {
    name: "combining-mark-after-picture-token",
    input: `\ufffc${"e".repeat(64)}\ufffc\u0301x`,
    expected: `\ufffc${"e".repeat(64)}\ufffc\u0301x`,
  },
  { name: "plain", input: "Take one tablet daily.", expected: "Take one tablet daily." },
  {
    name: "collapse-whitespace",
    input: "  Take \t one\n\ntablet daily.  ",
    expected: "Take one tablet daily.",
  },
  // U+202F is content from fidelity-norm/3.0.0 (drawn a pixel or two wide); the others are spaces.
  { name: "unicode-spaces", input: "a\u2003b\u202fc\u3000d\u2028e", expected: "a b\u202fc d e" },
  { name: "nfc", input: "café", expected: "café" },
  { name: "soft-hyphen", input: "intra­venous", expected: "intravenous" },
  { name: "zero-width", input: "a​b﻿c⁠d", expected: "abcd" },
  { name: "ligatures", input: "ﬀ ﬁ ﬂ ﬃ ﬄ ﬆ", expected: "ff fi fl ffi ffl st" },
  // 2.0.0 (review): a bullet glyph is list structure only at the start of a line and followed by
  // whitespace; the mid-line one is content; and the start of the text is not a line start, so
  // the first one is content too (the verifier reads a page slice from its line terminator).
  // The name is kept for review against 1.1.1.
  {
    name: "bullets",
    input: "• one\n● two ▪ three",
    expected: "• one two ▪ three",
  },
  { name: "keeps-case", input: "Take ONE", expected: "Take ONE" },
  { name: "keeps-typographic-quotes", input: "‘a’ “b”", expected: "‘a’ “b”" },
  {
    name: "keeps-dashes",
    input: "long-term – long—term − x",
    expected: "long-term – long—term − x",
  },
  { name: "keeps-superscripts", input: "m² H₂O", expected: "m² H₂O" },
  { name: "keeps-zwj-zwnj", input: "a‌b‍c", expected: "a‌b‍c" },
  { name: "keeps-hyphen-bullet", input: "- one - two", expected: "- one - two" },
  { name: "empty", input: "   \n\t ", expected: "" },
  {
    name: "rejects-replacement-character",
    input: "a�b",
    expected: { error: "forbidden-character" },
  },
  { name: "rejects-control", input: "ab", expected: { error: "forbidden-character" } },
  { name: "rejects-delete", input: "ab", expected: { error: "forbidden-character" } },
  { name: "rejects-lone-surrogate", input: "a\uD800b", expected: { error: "forbidden-character" } },
  {
    name: "rejects-interlinear-annotation-separator",
    input: "a\ufffab",
    expected: { error: "forbidden-character" },
  },
  // 2.0.0: U+000B and U+000C are rejected on both sides (section 2); the name is kept so the
  // change is reviewable against the 1.1.1 vector of the same name.
  {
    name: "allows-tab-lf-cr-ff-vt",
    input: "a\tb\nc\rd\fe\u000bf",
    expected: { error: "forbidden-character" },
  },
  ...NORMALIZATION_CASES_2_0_0,
];

export const xhtmlCases: XhtmlCase[] = [
  // Attribute values are never compared against the source, so they are token-limited.
  {
    name: "rejects-https-link",
    input: div('<p><a href="https://example.org/x/y.html">t</a></p>'),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-href-with-query",
    input: div('<p><a href="https://example.org/x?q=text">t</a></p>'),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-four-class-tokens",
    input: div('<p class="a b c d">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  // An attribute grammar is a whole-value grammar. A re-implementation whose "matches" means
  // "matches a prefix", or whose `$` also matches before a trailing newline (Python's does),
  // accepts a value with text after the last line break — exactly the channel section 5 closes.
  {
    name: "rejects-id-with-trailing-newline",
    input: div('<p id="a\n">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-class-with-text-after-newline",
    input: div('<p class="a\nhidden text">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-href-with-trailing-newline",
    input: div('<p><a href="#x\n">t</a></p>'),
    expected: { error: "unknown-element" },
  },
  // `\d` is ASCII in this dialect and Unicode-aware in Python's. A numeric character reference
  // written with fullwidth digits is not a character reference at all: the `&` is stray.
  {
    name: "rejects-fullwidth-digit-entity",
    input: div("<p>&#６５;</p>"),
    expected: { error: "stray-amp" },
  },
  {
    name: "rejects-fullwidth-digit-hex-entity",
    input: div("<p>&#x４２;</p>"),
    expected: { error: "stray-amp" },
  },
  { name: "accepts-decimal-entity", input: div("<p>&#65;</p>"), expected: "\n\nA\n\n" },
  // Renderer-generated characters (list numbers, quotation marks) and table sections placed
  // out of document order would show text or an order the source does not contain.
  // An `ol`'s numbers are emitted as text (fidelity-norm/3.0.0).
  { name: "accepts-ol", input: div("<ol><li>a</li></ol>"), expected: "\n\n\n1. a\n\n\n" },
  { name: "rejects-q", input: div("<p><q>a</q></p>"), expected: { error: "unknown-element" } },
  {
    name: "accepts-table-section-order",
    input: div(
      "<table><thead><tr><th>h</th></tr></thead><tbody><tr><td>b</td></tr></tbody><tfoot><tr><td>f</td></tr></tfoot></table>",
    ),
    expected:
      "\n\n\ufdd0\n\n\ufdd2\t\ufdd3\th\t\n\n\n\n\ufdd2\t\ufdd3\tb\t\n\n\n\n\ufdd2\t\ufdd3\tf\t\n\n\n\ufdd1\n\n",
  },
  {
    name: "rejects-tfoot-before-tbody",
    input: div(
      "<table><tfoot><tr><td>f</td></tr></tfoot><tbody><tr><td>b</td></tr></tbody></table>",
    ),
    expected: { error: "table-section-order" },
  },
  {
    name: "rejects-thead-after-tbody",
    input: div(
      "<table><tbody><tr><td>b</td></tr></tbody><thead><tr><th>h</th></tr></thead></table>",
    ),
    expected: { error: "table-section-order" },
  },
  {
    name: "rejects-tbody-outside-table",
    input: div("<tbody><tr><td>b</td></tr></tbody>"),
    expected: { error: "misnested-tag" },
  },
  // Rows directly under `table` render inside an implicit body and a caption always renders
  // first, so they may not be mixed with sections or placed after content.
  {
    name: "rejects-loose-row-after-tfoot",
    input: div("<table><tfoot><tr><td>f</td></tr></tfoot><tr><td>b</td></tr></table>"),
    expected: { error: "table-structure" },
  },
  {
    name: "rejects-loose-row-before-thead",
    input: div("<table><tr><td>b</td></tr><thead><tr><th>h</th></tr></thead></table>"),
    expected: { error: "table-structure" },
  },
  {
    name: "rejects-caption-after-body",
    input: div("<table><tbody><tr><td>a</td></tr></tbody><caption>c</caption></table>"),
    expected: { error: "table-structure" },
  },
  {
    name: "accepts-caption-first",
    input: div("<table><caption>c</caption><tr><td>a</td></tr></table>"),
    expected: "\n\n\ufdd0\nc\n\n\ufdd2\t\ufdd3\ta\t\n\n\ufdd1\n\n",
  },
  {
    name: "rejects-cell-outside-row",
    input: div("<table><td>a</td></table>"),
    expected: { error: "misnested-tag" },
  },
  // A soft hyphen directly before a structural line break would render as a hyphenated break
  // while normalisation joins the word.
  {
    name: "rejects-soft-hyphen-before-br",
    input: div("<p>intra&#173;<br/>venous</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-soft-hyphen-before-block-end",
    input: div("<h2>intra&#173;</h2><p>venous</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-soft-hyphen-inside-text",
    input: div("<p>intra&#173;venous</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-javascript-href",
    input: div('<p><a href="javascript:alert(1)">t</a></p>'),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-duplicate-attribute",
    input: div('<p id="a" id="b">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-free-text-class",
    input: div('<p class="do not take more than two tablets in any day">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-non-token-id",
    input: div('<p id="take two tablets">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  { name: "paragraphs", input: paragraphs("One.", "Two."), expected: "\n\nOne.\n\nTwo.\n\n" },
  {
    name: "inline-dropped",
    input: div("<p>a <b>b</b> <i>c</i> <sup>2</sup></p>"),
    // 2.0.0: inline markup is dropped, except that a digit inside `sup` folds to its script code
    // point.
    expected: "\n\na b c ²\n\n",
  },
  { name: "br", input: div("<p>a<br/>b</p>"), expected: "\n\na\nb\n\n" },
  { name: "list", input: div("<ul><li>x</li><li>y</li></ul>"), expected: "\n\n\nx\n\ny\n\n\n" },
  {
    name: "table",
    input: div("<table><tr><td>a</td><td>b</td></tr></table>"),
    expected: "\n\n\ufdd0\n\ufdd2\t\ufdd3\ta\t\t\ufdd3\tb\t\n\n\ufdd1\n\n",
  },
  {
    name: "entities",
    input: div("<p>&lt;a&gt; &amp; &quot;b&quot; &apos;c&apos; &#65;&#x42;</p>"),
    expected: "\n\n<a> & \"b\" 'c' AB\n\n",
  },
  {
    name: "allowed-attributes",
    input: `<div ${XHTML} xml:lang="en" lang="en" id="s1" class="c"><p><a href="#x">t</a></p></div>`,
    // 2.0.0: `id` and `class` are no longer allowed, even on the root; the first one decides.
    // `accepts-lang-on-root` is the accepting form.
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "self-closing-block",
    input: div("<p>a</p><hr/><p>b</p>"),
    expected: "\n\na\n\n\n\nb\n\n",
  },
  { name: "whitespace-outside-root", input: `  ${div("<p>a</p>")}\n`, expected: "\n\na\n\n" },
  {
    name: "rejects-missing-namespace",
    input: "<div><p>a</p></div>",
    expected: { error: "root-not-div" },
  },
  { name: "rejects-non-div-root", input: `<p ${XHTML}>a</p>`, expected: { error: "root-not-div" } },
  {
    name: "rejects-text-outside-root",
    input: `x${div("<p>a</p>")}`,
    expected: { error: "text-outside-root" },
  },
  {
    name: "rejects-multiple-roots",
    input: `${div("<p>a</p>")}${div("<p>b</p>")}`,
    expected: { error: "multiple-roots" },
  },
  {
    name: "rejects-unknown-element",
    input: div("<p>a</p><iframe/>"),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-style-element",
    input: div("<style>p{}</style><p>a</p>"),
    expected: { error: "unknown-element" },
  },
  { name: "rejects-uppercase", input: div("<P>a</P>"), expected: { error: "uppercase-element" } },
  {
    name: "rejects-style-attribute",
    input: div('<p style="x">a</p>'),
    expected: { error: "forbidden-attribute" },
  },
  // From 3.3.0 one style is allowed: the EMA ePI style guide's grey for QRD "not printed" text,
  // on a `span`, in exactly this spelling. Any other value, element or spelling still rejects; a
  // second declaration could hide the text (`color: silver`).
  {
    name: "accepts-grey-span",
    input: div('<p>a <span style="background-color: silver;">b</span> c</p>'),
    expected: "\n\na b c\n\n",
  },
  {
    name: "accepts-grey-span-single-quoted",
    input: div("<p><span style='background-color: silver;'>b</span></p>"),
    expected: "\n\nb\n\n",
  },
  {
    name: "rejects-grey-style-on-p",
    input: div('<p style="background-color: silver;">a</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-on-strong",
    input: div('<p><strong style="background-color: silver;">a</strong></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-without-semicolon",
    input: div('<p><span style="background-color: silver">a</span></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-other-spacing",
    input: div('<p><span style="background-color:silver;">a</span></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-capitals",
    input: div('<p><span style="Background-color: silver;">a</span></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-other-colour",
    input: div('<p><span style="background-color: white;">a</span></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-second-declaration",
    input: div('<p><span style="background-color: silver; color: silver;">a</span></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-reference",
    input: div('<p><span style="background-color&#58; silver;">a</span></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-grey-style-twice",
    input: div(
      '<p><span style="background-color: silver;" style="background-color: silver;">a</span></p>',
    ),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-hidden-attribute",
    input: div('<p hidden="hidden">a</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-title-attribute",
    input: div('<p title="t">a</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-href-off-anchor",
    input: div('<p href="#x">a</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-nested-xmlns",
    input: div(`<p ${XHTML}>a</p>`),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-unquoted-attribute",
    input: div("<p class=x>a</p>"),
    expected: { error: "malformed-tag" },
  },
  { name: "rejects-comment", input: div("<!-- c --><p>a</p>"), expected: { error: "comment" } },
  {
    name: "rejects-processing-instruction",
    input: div("<?x?><p>a</p>"),
    expected: { error: "processing-instruction" },
  },
  { name: "rejects-cdata", input: div("<![CDATA[x]]><p>a</p>"), expected: { error: "cdata" } },
  {
    name: "rejects-doctype",
    input: `<!DOCTYPE html>${div("<p>a</p>")}`,
    expected: { error: "doctype" },
  },
  { name: "rejects-stray-lt", input: div("<p>a < b</p>"), expected: { error: "stray-lt" } },
  { name: "rejects-stray-amp", input: div("<p>a & b</p>"), expected: { error: "stray-amp" } },
  { name: "rejects-nbsp", input: div("<p>a&nbsp;b</p>"), expected: { error: "unknown-entity" } },
  {
    name: "rejects-out-of-range-entity",
    input: div("<p>&#x110000;</p>"),
    expected: { error: "unknown-entity" },
  },
  {
    name: "rejects-unbalanced",
    input: `<div ${XHTML}><p>a`,
    expected: { error: "unbalanced-tag" },
  },
  {
    name: "rejects-misnested",
    input: div("<p><b>a</p></b>"),
    expected: { error: "misnested-tag" },
  },
  {
    name: "rejects-stray-end-tag",
    input: `${div("<p>a</p>")}</p>`,
    expected: { error: "unbalanced-tag" },
  },
  // -------------------------------------------------------------------------------------------
  // fidelity-norm/2.0.0.

  // Only `br` and `hr` may be self-closing, and they must be (`void-element`).
  {
    name: "rejects-self-closing-sup",
    input: div("<p>10<sup/>6 mg</p>"),
    expected: { error: "void-element" },
  },
  {
    name: "rejects-self-closing-anchor",
    input: div('<p><a href="https://example.org/"/>text</p>'),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-self-closing-li",
    input: div("<ul><li/></ul>"),
    expected: { error: "void-element" },
  },
  {
    name: "rejects-self-closing-cell",
    input: div("<table><tr><td/></tr></table>"),
    expected: { error: "void-element" },
  },
  {
    name: "rejects-self-closing-root",
    input: `<div ${XHTML}/>`,
    expected: { error: "void-element" },
  },
  {
    name: "rejects-br-start-tag",
    input: div("<p>Do<br>not</br> take</p>"),
    expected: { error: "void-element" },
  },
  {
    name: "rejects-hr-start-tag",
    input: div("<p>a</p><hr><p>b</p>"),
    expected: { error: "void-element" },
  },
  { name: "rejects-hr-with-end-tag", input: div("<hr></hr>"), expected: { error: "void-element" } },
  {
    name: "accepts-self-closing-with-space",
    input: div("<p>a<br />b</p><hr />"),
    expected: "\n\na\nb\n\n\n\n",
  },
  {
    name: "rejects-forbidden-attribute-before-void",
    input: div('<p>a<br class="x"></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-void-before-parent",
    input: div("<table><br></table>"),
    expected: { error: "void-element" },
  },
  // Digits and signs inside `sup` and `sub` fold to script code points; other characters are
  // kept; a number with no script form and a plus-minus sign reject (`unmappable-script`).
  {
    name: "sup-digits-fold",
    input: div("<p><sup>0123456789</sup></p>"),
    expected: "\n\n⁰¹²³⁴⁵⁶⁷⁸⁹\n\n",
  },
  {
    name: "sub-digits-fold",
    input: div("<p><sub>0123456789</sub></p>"),
    expected: "\n\n₀₁₂₃₄₅₆₇₈₉\n\n",
  },
  {
    name: "sup-signs-fold",
    input: div("<p><sup>+-=()</sup></p>"),
    expected: "\n\n⁺⁻⁼⁽⁾\n\n",
  },
  {
    name: "sub-signs-fold",
    input: div("<p><sub>+-=()</sub></p>"),
    expected: "\n\n₊₋₌₍₎\n\n",
  },
  {
    name: "sup-dashes-fold-to-minus",
    input: div("<p><sup>‐‑‒–—―−\u02d7﹘﹣－➖</sup></p>"),
    expected: `\n\n${"⁻".repeat(12)}\n\n`,
  },
  {
    name: "sup-plus-variants-fold",
    input: div("<p><sup>﹢＋➕</sup></p>"),
    expected: "\n\n⁺⁺⁺\n\n",
  },
  {
    name: "sub-dashes-fold-to-minus",
    input: div("<p><sub>‐–—―−\u02d7﹘﹣－➖</sub></p>"),
    expected: `\n\n${"₋".repeat(10)}\n\n`,
  },
  {
    name: "sub-plus-variants-fold",
    input: div("<p><sub>﹢＋➕</sub></p>"),
    expected: "\n\n₊₊₊\n\n",
  },
  {
    name: "sup-en-dash-exponent",
    input: div("<p>10<sup>–6</sup></p>"),
    expected: "\n\n10⁻⁶\n\n",
  },
  {
    name: "sup-references-fold",
    input: div("<p><sup>&#x2212;&#54;</sup></p>"),
    expected: "\n\n⁻⁶\n\n",
  },
  // A dash-like character outside the fold tables is kept as it is.
  {
    name: "rejects-sup-tilde",
    input: div("<p><sup>~</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "sup-horizontal-bar-folds",
    input: div("<p><sup>―</sup></p>"),
    expected: "\n\n⁻\n\n",
  },
  { name: "sup-letter-kept", input: div("<p><sup>a</sup></p>"), expected: "\n\na\n\n" },
  { name: "sub-letters-kept", input: div("<p>C<sub>max</sub></p>"), expected: "\n\nCmax\n\n" },
  {
    name: "sub-fraction-folds-digits",
    input: div("<p>t<sub>1/2</sub></p>"),
    expected: "\n\nt₁/₂\n\n",
  },
  { name: "sup-registered-kept", input: div("<p><sup>®</sup></p>"), expected: "\n\n®\n\n" },
  { name: "sup-footnote-mark-kept", input: div("<p><sup>*</sup></p>"), expected: "\n\n*\n\n" },
  {
    name: "sup-script-digit-kept",
    input: div("<p><sup>²</sup></p>"),
    expected: "\n\n²\n\n",
  },
  {
    name: "sub-script-digit-kept",
    input: div("<p><sub>₂</sub></p>"),
    expected: "\n\n₂\n\n",
  },
  {
    name: "sup-own-signs-kept",
    input: div("<p><sup>⁺⁻⁼⁽⁾</sup></p>"),
    expected: "\n\n⁺⁻⁼⁽⁾\n\n",
  },
  {
    name: "sub-own-signs-kept",
    input: div("<p><sub>₊₋₌₍₎</sub></p>"),
    expected: "\n\n₊₋₌₍₎\n\n",
  },
  {
    name: "rejects-sub-superscript-digit",
    input: div("<p><sub>²</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-superscript-digit-zero",
    input: div("<p><sub>⁰</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-superscript-sign",
    input: div("<p><sub>⁻</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-subscript-digit",
    input: div("<p>10<sup>₆</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-subscript-sign",
    input: div("<p><sup>₎</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-subscript-digit-reference",
    input: div("<p><sup>&#x2089;</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  { name: "sup-whitespace-kept", input: div("<p><sup> 2 </sup></p>"), expected: "\n\n ² \n\n" },
  {
    name: "digits-outside-scripts-kept",
    input: div("<p>2<sup>n</sup> 10-6</p>"),
    expected: "\n\n2n 10-6\n\n",
  },
  {
    name: "rejects-sup-plus-minus",
    input: div("<p><sup>±1</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-minus-plus",
    input: div("<p><sub>∓</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-plus-minus-reference",
    input: div("<p><sup>&#xB1;</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-arabic-indic-digit",
    input: div("<p><sup>٣</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-fullwidth-digit",
    input: div("<p><sup>２</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-vulgar-fraction",
    input: div("<p><sup>½</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  // fidelity-norm/3.1.0: inside `sub`, ∞ is kept unchanged, and ½ only as the half-life, the
  // element's whole content right after a `t` that starts a word and right before a break, a
  // space or `) . , ; :` (the real labels' half-life and AUC); inside `sup` both still reject, and
  // so does every other number inside `sub`.
  {
    name: "sub-vulgar-half-kept",
    input: div("<p>the t<sub>½</sub> was approximately 18 hours</p>"),
    expected: "\n\nthe t½ was approximately 18 hours\n\n",
  },
  {
    name: "sub-vulgar-half-reference-kept",
    input: div("<p>t<sub>&#189;</sub> and t<sub>&#xBD;</sub></p>"),
    expected: "\n\nt½ and t½\n\n",
  },
  {
    name: "sub-infinity-kept-among-folds",
    input: div("<p>AUC<sub>(0-∞)</sub> and AUC<sub>0&#8211;&#x221E;</sub></p>"),
    expected: "\n\nAUC₍₀₋∞₎ and AUC₀₋∞\n\n",
  },
  {
    name: "sub-half-life-between-word-and-break-kept",
    input: div("<p><em>t</em><sub>½</sub> 2, (t<sub>½</sub>) and t<sub>½</sub>.</p>"),
    expected: "\n\nt½ 2, (t½) and t½.\n\n",
  },
  {
    name: "sub-half-at-cell-end-kept",
    input: div("<table><tr><td>t<sub>½</sub></td></tr></table>"),
    expected: "\n\n\ufdd0\n\ufdd2\t\ufdd3\tt½\t\n\n\ufdd1\n\n",
  },
  {
    name: "rejects-sub-half-after-digit",
    input: div("<p>1<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-joined-to-index",
    input: div("<p>log<sub>2½</sub> x</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-sign",
    input: div("<p>x<sub>-½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-in-formula",
    input: div("<p>CaSO<sub>4·½</sub>H<sub>2</sub>O</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-with-space",
    input: div("<p>t<sub>½ </sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-at-line-start",
    input: div("<p><sub>½</sub> dose</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-digit-past-joiner",
    input: div("<p>t<sub>½</sub>&#x2060;2</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-subscript-digit",
    input: div("<p>t<sub>½</sub><sub>2</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-superscript-sign",
    input: div("<p>t<sub>½</sub><sup>+</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-joiner",
    input: div("<p>t&#x2060;<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-subscript-letter",
    input: div("<p>log\u2099<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-lowered-letter",
    input: div("<p>log<sub>n</sub><sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-lowered-letter",
    input: div("<p>x<sub>½</sub><sub>n</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-superscript-letter",
    input: div("<p>t<sub>½</sub>\u207f</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-thin-space",
    input: div("<p>t<sub>½</sub>&#x200A;<sub>2</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-braille-blank",
    input: div("<p>t<sub>½</sub>&#x2800;2</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-blank-glyph-letter",
    input: div("<p>log<sub>2</sub>&#x1878;<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-modifier-letter",
    input: div("<p>2\u02b9<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-ordinal",
    input: div("<p>1\u00aa<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-ideograph-numeral",
    input: div("<p>\u4e8c<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-mathematical-letter",
    input: div("<p>&#x1D4C9;<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-before-mark",
    input: div("<p>t<sub>½</sub>&#x301;</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "sub-half-before-other-breaks-kept",
    input: div(
      "<p>t<sub>½</sub>; t<sub>½</sub>: t<sub>½</sub>, t<sub>½</sub><br/>t<sub>½</sub></p>",
    ),
    expected: "\n\nt½; t½: t½, t½\nt½\n\n",
  },
  {
    name: "rejects-sub-half-before-digit",
    input: div("<p>t<sub>½</sub>2</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-operator-name",
    input: div("<p>log<sub>½</sub> 8</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-roman-numeral",
    input: div("<p>VIII<sub>½</sub>.</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-hex-digit",
    input: div("<p>0xA<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-t-in-word",
    input: div("<p>at<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-t-after-index",
    input: div("<p>log<sub>2</sub>t<sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-half-after-lowered-t",
    input: div("<p><sub>t</sub><sub>½</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  // Checked after the scan: an error the scan finds later wins, and it comes before
  // combining-across-markup.
  {
    name: "scan-error-after-sub-half-first",
    input: div("<p>1<sub>½</sub> &bogus;</p>"),
    expected: { error: "unknown-entity" },
  },
  {
    name: "sub-half-before-combining-across-markup",
    input: div("<p>1<sub>½</sub> q<b>&#x301;</b></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "sub-half-after-earlier-combining-across-markup",
    input: div("<p>q<b>&#x301;</b> t<sub>½</sub>2</p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-infinity",
    input: div("<p>10<sup>∞</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-half-reference",
    input: div("<p>2<sup>&#189;</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-other-fraction",
    input: div("<p>t<sub>¼</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-fraction-zero-thirds",
    input: div("<p>t<sub>\u2189</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-infinity-look-alike-sign",
    input: div("<p>x<sub>\u29dc</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-roman-numeral",
    input: div("<p><sub>Ⅳ</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-supplementary-digit",
    input: div("<p><sup>\u{1d7ce}</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-supplementary-digit-reference",
    input: div("<p><sup>&#x1D7CE;</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-element-in-sup",
    input: div("<p><sup><b>2</b></sup></p>"),
    expected: { error: "script-content" },
  },
  {
    name: "rejects-sup-in-sub",
    input: div("<p><sub><sup>2</sup></sub></p>"),
    expected: { error: "script-content" },
  },
  {
    name: "rejects-br-in-sup",
    input: div("<p><sup>1<br/>2</sup></p>"),
    expected: { error: "script-content" },
  },
  {
    name: "rejects-script-content-before-misnesting",
    input: div("<table><tr><td><sup><td>x</td></sup></td></tr></table>"),
    expected: { error: "script-content" },
  },
  // U+00AD followed by a line break anywhere in the emitted text, decided after the scan.
  {
    name: "soft-hyphen-before-raw-lf-is-a-space",
    input: div("<p>non&#173;\nsmokers</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "soft-hyphen-before-lf-reference-is-a-space",
    input: div("<p>non&#173;&#10;smokers</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "soft-hyphen-cr-then-br-is-a-space",
    input: div("<p>non&#173;&#13;<br/>smokers</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "raw-soft-hyphen-before-crlf-is-a-space",
    input: div("<p>non\u00ad\r\nsmokers</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-soft-hyphen-before-hr",
    input: div("<p>non\u00ad<hr/>smokers</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-soft-hyphen-before-cr-alone",
    input: div("<p>non&#173;&#13;smokers</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-soft-hyphen-before-space",
    input: div("<p>non&#173; smokers</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "soft-hyphen-decided-after-scan",
    input: div("<p>non&#173;</p><iframe/>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "unbalanced-before-soft-hyphen",
    input: `<div ${XHTML}><p>non&#173;</p>`,
    expected: { error: "invisible-character" },
  },
  // Section 2 on the div as received, markup included, and on each reference as decoded.
  {
    name: "rejects-surrogate-pair-references",
    input: div("<p>&#xD835;&#xDEFC;</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-surrogates-split-by-markup",
    input: div("<p>\uD835<b></b>\uDEFC</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "accepts-raw-supplementary-character",
    input: div("<p>\u{1d6fc}</p>"),
    expected: "\n\n\u{1d6fc}\n\n",
  },
  {
    name: "accepts-supplementary-reference",
    input: div("<p>&#x1D6FC;</p>"),
    expected: "\n\n\u{1d6fc}\n\n",
  },
  {
    name: "rejects-c1-reference",
    input: div("<p>2&#133;11</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-c1-hex-reference",
    input: div("<p>&#x92;</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-raw-c1",
    input: div("<p>2\u008511</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-vertical-tab-reference",
    input: div("<p>&#11;</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-form-feed-reference",
    input: div("<p>&#xC;</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-raw-form-feed",
    input: div("<p>a\u000cb</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-nul-reference",
    input: div("<p>&#0;</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-bidi-override-reference",
    input: div("<p>&#x202E;</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-left-to-right-mark-reference",
    input: div("<p>&#8206;</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-raw-bidi-isolate",
    input: div("<p>a\u2066b</p>"),
    expected: { error: "forbidden-character" },
  },
  { name: "accepts-tilde-reference", input: div("<p>&#x7E;</p>"), expected: "\n\n~\n\n" },
  {
    name: "accepts-no-break-space-reference",
    input: div("<p>&#xA0;</p>"),
    expected: "\n\n\u00a0\n\n",
  },
  {
    name: "rejects-forbidden-character-in-attribute",
    input: `<div ${XHTML} lang="en\u200e"><p>a</p></div>`,
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-forbidden-character-outside-root",
    input: `\u000c${div("<p>a</p>")}`,
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-forbidden-character-in-tag",
    input: div("<p\u000b>a</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-text-outside-root-before-forbidden-reference",
    input: `&#133;${div("<p>a</p>")}`,
    expected: { error: "text-outside-root" },
  },
  {
    name: "rejects-forbidden-reference-before-table-content",
    input: div("<table>&#133;</table>"),
    expected: { error: "forbidden-character" },
  },
  // No attribute a stylesheet or script can key on: no `class`, no `id`, no language tag below
  // the root, no in-page link; `scope` on `th` only.
  {
    name: "rejects-class",
    input: div('<p class="c">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-id-below-root",
    input: div('<p id="s1">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-id-on-root",
    input: `<div ${XHTML} id="s1"><p>t</p></div>`,
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-lang-below-root",
    input: div('<p lang="en">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-xml-lang-below-root",
    input: div('<p xml:lang="en">t</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "accepts-lang-on-root",
    input: `<div ${XHTML} xml:lang="en" lang="en"><p>t</p></div>`,
    expected: "\n\nt\n\n",
  },
  {
    name: "rejects-duplicate-root-lang",
    input: `<div ${XHTML} lang="en" lang="de"><p>t</p></div>`,
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-fragment-href",
    input: div('<p><a href="#x">t</a></p>'),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-scope-on-td",
    input: div('<table><tr><td scope="row">a</td></tr></table>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "accepts-scope-on-th",
    input: div('<table><tr><th scope="row">h</th><td>a</td></tr></table>'),
    expected: "\n\n\ufdd0\n\ufdd2\t\ufdd3\th\t\t\ufdd3\ta\t\n\n\ufdd1\n\n",
  },
  {
    name: "accepts-colspan",
    input: div('<table><tr><td colspan="2">a</td></tr><tr><td>b</td><td>c</td></tr></table>'),
    expected:
      "\n\n\ufdd0\n\ufdd2\t\ufdd3\ta\t\t\ufdd4\t\n\n\ufdd2\t\ufdd3\tb\t\t\ufdd3\tc\t\n\n\ufdd1\n\n",
  },
  {
    // A column in which no cell spanning one column starts is drawn at zero width.
    name: "rejects-column-no-single-cell-starts-in",
    input: div('<table><tr><td colspan="2">a</td></tr></table>'),
    expected: { error: "table-shape" },
  },
  {
    // A renderer clips a row span that runs past its row group.
    name: "rejects-rowspan-past-group",
    input: div('<table><tr><td rowspan="2">a</td></tr></table>'),
    expected: { error: "table-shape" },
  },
  {
    name: "rejects-root-attribute-before-missing-namespace",
    input: '<div class="c"><p>a</p></div>',
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-root-lang-without-namespace",
    input: '<div lang="en"><p>a</p></div>',
    expected: { error: "root-not-div" },
  },
  // Tables contain only table parts, whitespace between them, and rows of one width.
  {
    name: "rejects-text-in-table",
    input: div("<table>x<tr><td>a</td></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-text-in-thead",
    input: div("<table><thead>x<tr><th>h</th></tr></thead></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-text-in-tbody",
    input: div("<table><tbody><tr><td>a</td></tr>x</tbody></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-text-in-tfoot",
    input: div("<table><tfoot>x</tfoot></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-text-in-tr",
    input: div("<table><tr>x<td>a</td></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-space-reference-in-tr",
    input: div("<table><tr>&#32;<td>a</td></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-no-break-space-in-tr",
    input: div("<table><tr>\u00a0<td>a</td></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "accepts-whitespace-in-table-parts",
    input: div(
      "<table>\n <thead>\t<tr>\r\n<th>h</th> </tr></thead>\n<tbody> <tr><td>a</td></tr> </tbody></table>",
    ),
    expected:
      "\n\n\ufdd0  \n\t\n\ufdd2  \t\ufdd3\th\t \n\n \n \n\ufdd2\t\ufdd3\ta\t\n \n\n\ufdd1\n\n",
  },
  {
    name: "rejects-span-in-table",
    input: div("<table><span>x</span></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-paragraph-in-tbody",
    input: div("<table><tbody><p>x</p></tbody></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-bold-in-tr",
    input: div("<table><tr><b>x</b></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-table-in-tr",
    input: div("<table><tr><table></table></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-br-in-table",
    input: div("<table><br/></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-caption-in-tr",
    input: div("<table><tr><caption>c</caption></tr></table>"),
    expected: { error: "misnested-tag" },
  },
  {
    // A table inside a cell is refused, so the grid text never nests (fidelity-norm/3.0.0).
    name: "rejects-nested-table-in-cell",
    input: div(
      "<table><tr><td><table><tr><td>a</td><td>b</td></tr></table></td></tr><tr><td>c</td></tr></table>",
    ),
    expected: { error: "table-structure" },
  },
  {
    name: "rejects-uneven-rows",
    input: div("<table><tr><td>a</td></tr><tr><td>b</td><td>c</td></tr></table>"),
    expected: { error: "table-shape" },
  },
  {
    name: "rejects-uneven-rows-across-sections",
    input: div(
      "<table><thead><tr><th>h</th><th>i</th></tr></thead><tbody><tr><td>a</td></tr></tbody></table>",
    ),
    expected: { error: "table-shape" },
  },
  {
    name: "rejects-uneven-nested-table",
    input: div("<table><tr><td><table><tr><td>a</td></tr><tr></tr></table></td></tr></table>"),
    expected: { error: "table-structure" },
  },
  {
    name: "accepts-header-and-data-cells",
    input: div("<table><tr><th>h</th><td>a</td></tr><tr><td>b</td><td>c</td></tr></table>"),
    expected:
      "\n\n\ufdd0\n\ufdd2\t\ufdd3\th\t\t\ufdd3\ta\t\n\n\ufdd2\t\ufdd3\tb\t\t\ufdd3\tc\t\n\n\ufdd1\n\n",
  },
  {
    name: "accepts-empty-table",
    input: div("<table></table>"),
    expected: "\n\n\ufdd0\n\ufdd1\n\n",
  },
  {
    name: "accepts-caption-only-table",
    input: div("<table><caption>c</caption></table>"),
    expected: "\n\n\ufdd0\nc\n\n\ufdd1\n\n",
  },
  {
    name: "accepts-empty-rows",
    input: div("<table><tr></tr><tr></tr></table>"),
    expected: "\n\n\ufdd0\n\ufdd2\n\n\ufdd2\n\n\ufdd1\n\n",
  },
  {
    name: "rejects-empty-and-full-row",
    input: div("<table><tr></tr><tr><td>a</td></tr></table>"),
    expected: { error: "table-shape" },
  },
  { name: "rejects-pre", input: div("<pre>a  b</pre>"), expected: { error: "unknown-element" } },
  // Order of decision at a start tag.
  {
    name: "rejects-uppercase-before-unknown",
    input: div("<IMG/>"),
    expected: { error: "uppercase-element" },
  },
  {
    name: "rejects-unknown-before-root",
    input: `<iframe ${XHTML}/>`,
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-multiple-roots-before-attributes",
    input: `${div("<p>a</p>")}<div class="c"></div>`,
    expected: { error: "multiple-roots" },
  },
  // Review C1: inside a tag, whitespace is U+0009, U+000A, U+000D and U+0020 only. An HTML parser
  // reads any other code point as part of the tag name, so the element would not be what the
  // scanner saw.
  {
    name: "rejects-no-break-space-in-start-tag",
    input: div("<p>10<sup\u00a0>6</sup></p>"),
    expected: { error: "malformed-tag" },
  },
  {
    name: "rejects-no-break-space-in-end-tag",
    input: div("<p>10<sup>6</sup\u00a0></p>"),
    expected: { error: "malformed-tag" },
  },
  {
    name: "rejects-no-break-space-in-self-closing-br",
    input: div("<p>a<br\u00a0/>b</p>"),
    expected: { error: "malformed-tag" },
  },
  {
    name: "rejects-ideographic-space-in-table-tag",
    input: div("<table\u3000><tr><td>a</td></tr></table>"),
    expected: { error: "malformed-tag" },
  },
  {
    name: "rejects-zero-width-no-break-space-in-td-tag",
    input: div("<table><tr><td\ufeff>a</td></tr></table>"),
    expected: { error: "malformed-tag" },
  },
  {
    name: "rejects-em-space-before-attribute",
    input: `<div\u2003${XHTML}><p>a</p></div>`,
    expected: { error: "malformed-tag" },
  },
  {
    name: "rejects-no-break-space-around-equals",
    input: div('<p><a href\u00a0="https://example.org/">a</a></p>'),
    expected: { error: "malformed-tag" },
  },
  {
    name: "accepts-ascii-whitespace-in-tags",
    input: `<div\n${XHTML}\r\n><p\t>a<br\t/>b</p ><table\n><tr ><td\r>c</td\n></tr></table></div\t>`,
    expected: "\n\na\nb\n\n\ufdd0\n\ufdd2\t\ufdd3\tc\t\n\n\ufdd1\n\n",
  },
  // Second review, round 2, item 1: a line break in text is emitted as a space.
  {
    name: "raw-line-feed-in-text-is-a-space",
    input: div("<p>a\nb</p>"),
    expected: "\n\na b\n\n",
  },
  {
    name: "line-feed-reference-is-a-space",
    input: div("<p>a&#10;b</p>"),
    expected: "\n\na b\n\n",
  },
  {
    name: "carriage-return-reference-is-a-space",
    input: div("<p>a&#13;b</p>"),
    expected: "\n\na b\n\n",
  },
  {
    name: "raw-crlf-in-text-is-two-spaces",
    input: div("<p>a\r\nb</p>"),
    expected: "\n\na  b\n\n",
  },
  {
    name: "line-feed-in-sup-is-a-space",
    input: div("<p>x<sup>2\n3</sup></p>"),
    expected: "\n\nx\u00b2 \u00b3\n\n",
  },
  {
    name: "rejects-line-feed-reference-in-tr",
    input: div("<table><tr>&#10;<td>a</td></tr></table>"),
    expected: { error: "table-content" },
  },
  // Item 3: cells are U+0009-separated, and so is everything inside a cell.
  {
    name: "cell-breaks-are-tabs",
    input: div("<table><tr><td><p>a</p>b<br/>c</td></tr></table>"),
    expected: "\n\n\ufdd0\n\ufdd2\t\ufdd3\t\ta\tb\tc\t\n\n\ufdd1\n\n",
  },
  {
    name: "rejects-soft-hyphen-before-br-in-cell",
    input: div("<table><tr><td>intra&#173;<br/>venous</td></tr></table>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-soft-hyphen-before-caption-end",
    input: div("<table><caption>intra&#173;</caption></table>"),
    expected: { error: "invisible-character" },
  },
  // Item 4: the other kind of script letter, and any other symbol, bracket or dash, rejects.
  {
    name: "rejects-sub-superscript-i",
    input: div("<p>x<sub>\u2071</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-superscript-n",
    input: div("<p>x<sub>\u207f</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-subscript-a",
    input: div("<p>x<sup>\u2090</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-subscript-t",
    input: div("<p>x<sup>\u209c</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "sup-superscript-n-kept",
    input: div("<p>2<sup>\u207f</sup></p>"),
    expected: "\n\n2\u207f\n\n",
  },
  {
    name: "sub-subscript-a-kept",
    input: div("<p>x<sub>\u2090</sub></p>"),
    expected: "\n\nx\u2090\n\n",
  },
  {
    name: "rejects-sup-fullwidth-equals",
    input: div("<p>x<sup>\uff1d</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-small-parenthesis",
    input: div("<p>x<sup>\ufe59</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-two-em-dash",
    input: div("<p>x<sup>\u2e3a</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-vertical-em-dash",
    input: div("<p>x<sup>\ufe31</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sub-less-than",
    input: div("<p>x<sub>&lt;</sub></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "rejects-sup-square-bracket",
    input: div("<p>x<sup>[1]</sup></p>"),
    expected: { error: "unmappable-script" },
  },
  {
    name: "sup-slash-kept",
    input: div("<p><sup>1/2</sup></p>"),
    expected: "\n\n\u00b9/\u00b2\n\n",
  },
  // -------------------------------------------------------------------------------------------
  // fidelity-norm/3.0.0: numbered lists, table grids and pictures.
  {
    name: "accepts-ol-start",
    input: div('<ol start="3"><li>Take</li></ol>'),
    expected: "\n\n\n3. Take\n\n\n",
  },
  {
    name: "accepts-ol-alpha-past-z",
    input: div(
      '<ol type="a"><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li><li>x</li></ol>',
    ),
    expected:
      "\n\n\na. x\n\nb. x\n\nc. x\n\nd. x\n\ne. x\n\nf. x\n\ng. x\n\nh. x\n\ni. x\n\nj. x\n\nk. x\n\nl. x\n\nm. x\n\nn. x\n\no. x\n\np. x\n\nq. x\n\nr. x\n\ns. x\n\nt. x\n\nu. x\n\nv. x\n\nw. x\n\nx. x\n\ny. x\n\nz. x\n\naa. x\n\n\n",
  },
  {
    name: "accepts-ol-upper-roman-from-negative",
    input: div('<ol type="I" start="-1"><li>a</li><li>b</li><li>c</li></ol>'),
    expected: "\n\n\n-1. a\n\n0. b\n\nI. c\n\n\n",
  },
  {
    name: "accepts-ol-roman-past-range",
    input: div('<ol type="i" start="3998"><li>a</li><li>b</li><li>c</li></ol>'),
    expected: "\n\n\nmmmcmxcviii. a\n\nmmmcmxcix. b\n\n4000. c\n\n\n",
  },
  {
    name: "accepts-ol-upper-alpha-large",
    input: div('<ol type="A" start="703"><li>a</li></ol>'),
    expected: "\n\n\nAAA. a\n\n\n",
  },
  {
    name: "accepts-ol-nested-restarts-decimal",
    input: div('<ol type="a"><li>x<ol><li>y</li></ol></li><li>z</li></ol>'),
    expected: "\n\n\na. x\n\n1. y\n\n\n\nb. z\n\n\n",
  },
  {
    name: "accepts-ol-in-cell",
    input: div('<table><tr><td><ol start="2"><li>a</li></ol></td></tr></table>'),
    expected: "\n\n\ufdd0\n\ufdd2\t\ufdd3\t\t\t2. a\t\t\t\n\n\ufdd1\n\n",
  },
  {
    name: "accepts-ol-whitespace-between-items",
    input: div("<ol>\n <li>a</li>\t<li>b</li>\r\n</ol>"),
    expected: "\n\n  \n1. a\n\t\n2. b\n  \n\n",
  },
  {
    name: "accepts-ul-items-emit-nothing",
    input: div("<ul><li>a</li><li>b</li></ul>"),
    expected: "\n\n\na\n\nb\n\n\n",
  },
  {
    name: "rejects-text-in-ol",
    input: div("<ol> text <li>x</li></ol>"),
    expected: { error: "list-content" },
  },
  {
    name: "rejects-p-in-ul",
    input: div("<ul><p>x</p></ul>"),
    expected: { error: "list-content" },
  },
  {
    name: "rejects-reference-in-ol",
    input: div("<ol>&#32;<li>a</li></ol>"),
    expected: { error: "list-content" },
  },
  {
    name: "rejects-li-in-li",
    input: div("<ol><li>a<li>b</li></li></ol>"),
    expected: { error: "misnested-tag" },
  },
  {
    name: "rejects-li-in-div",
    input: div("<div><li>x</li></div>"),
    expected: { error: "misnested-tag" },
  },
  {
    name: "rejects-li-in-blockquote-in-li",
    input: div("<ol><li>a<blockquote><li>b</li></blockquote></li></ol>"),
    expected: { error: "misnested-tag" },
  },
  {
    name: "rejects-ol-reversed",
    input: div('<ol reversed="reversed"><li>a</li></ol>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-ol-start-negative-zero",
    input: div('<ol start="-0"><li>a</li></ol>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-ol-start-leading-zero",
    input: div('<ol start="007"><li>a</li></ol>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-ol-start-five-digits",
    input: div('<ol start="10000"><li>a</li></ol>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-ol-type-disc",
    input: div('<ol type="disc"><li>a</li></ol>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-li-value",
    input: div('<ol><li value="3">a</li></ol>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-ul-start",
    input: div('<ul start="2"><li>a</li></ul>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "accepts-rowspan-grid",
    input: div(
      '<table><tr><td rowspan="2">A</td><td>B</td><td>C</td></tr><tr><td>D</td><td>E</td></tr></table>',
    ),
    expected:
      "\n\n\ufdd0\n\ufdd2\t\ufdd3\tA\t\t\ufdd3\tB\t\t\ufdd3\tC\t\n\n\ufdd2\t\ufdd5\t\t\ufdd3\tD\t\t\ufdd3\tE\t\n\n\ufdd1\n\n",
  },
  {
    name: "accepts-rowspan-trailing-slot",
    input: div(
      '<table><tr><td>a</td><td>b</td><td rowspan="2">c</td></tr><tr><td>d</td><td>e</td></tr></table>',
    ),
    expected:
      "\n\n\ufdd0\n\ufdd2\t\ufdd3\ta\t\t\ufdd3\tb\t\t\ufdd3\tc\t\n\n\ufdd2\t\ufdd3\td\t\t\ufdd3\te\t\t\ufdd5\t\n\n\ufdd1\n\n",
  },
  {
    name: "accepts-colspan-and-rowspan",
    input: div(
      '<table><caption>Cap</caption><tr><td colspan="2" rowspan="2">X</td><td>a</td></tr><tr><td>b</td></tr><tr><td>c</td><td></td><td>d</td></tr></table>',
    ),
    expected:
      "\n\n\ufdd0\nCap\n\n\ufdd2\t\ufdd3\tX\t\t\ufdd4\t\t\ufdd3\ta\t\n\n\ufdd2\t\ufdd5\t\t\ufdd5\t\t\ufdd3\tb\t\n\n\ufdd2\t\ufdd3\tc\t\t\ufdd3\t\t\t\ufdd3\td\t\n\n\ufdd1\n\n",
  },
  {
    name: "accepts-rowspan-to-group-end",
    input: div(
      '<table><thead><tr><th>h</th><th>i</th></tr></thead><tbody><tr><td rowspan="2">a</td><td>b</td></tr><tr><td>c</td></tr></tbody></table>',
    ),
    expected:
      "\n\n\ufdd0\n\n\ufdd2\t\ufdd3\th\t\t\ufdd3\ti\t\n\n\n\n\ufdd2\t\ufdd3\ta\t\t\ufdd3\tb\t\n\n\ufdd2\t\ufdd5\t\t\ufdd3\tc\t\n\n\n\ufdd1\n\n",
  },
  {
    name: "rejects-colspan-1001",
    input: div('<table><tr><td colspan="1001">a</td></tr></table>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-colspan-zero",
    input: div('<table><tr><td colspan="0">a</td></tr></table>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-rowspan-leading-zero",
    input: div('<table><tr><td rowspan="02">a</td></tr></table>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-span-overlap",
    input: div(
      '<table><tr><td>Adults</td><td rowspan="2">10 mg</td></tr><tr><td colspan="2">Children</td></tr></table>',
    ),
    expected: { error: "table-shape" },
  },
  {
    name: "rejects-rowspan-into-tbody",
    input: div(
      '<table><thead><tr><td rowspan="2">a</td><td>b</td></tr></thead><tbody><tr><td>c</td></tr></tbody></table>',
    ),
    expected: { error: "table-shape" },
  },
  {
    name: "rejects-rowspan-past-bare-rows",
    input: div('<table><tr><td rowspan="3">a</td><td>b</td></tr><tr><td>c</td></tr></table>'),
    expected: { error: "table-shape" },
  },
  {
    name: "accepts-colspan-counted-in-width",
    input: div(
      '<table><tr><td colspan="2">a</td><td>b</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>',
    ),
    expected:
      "\n\n\ufdd0\n\ufdd2\t\ufdd3\ta\t\t\ufdd4\t\t\ufdd3\tb\t\n\n\ufdd2\t\ufdd3\t1\t\t\ufdd3\t2\t\t\ufdd3\t3\t\n\n\ufdd1\n\n",
  },
  {
    name: "rejects-colspan-ragged",
    input: div(
      '<table><tr><td colspan="2">a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>',
    ),
    expected: { error: "table-shape" },
  },
  {
    name: "rejects-row-with-hole",
    input: div(
      '<table><tr><td>a</td><td>b</td><td rowspan="2">c</td></tr><tr><td>d</td></tr></table>',
    ),
    expected: { error: "table-shape" },
  },
  {
    // A reference draws whatever the viewer's origin serves, or nothing: only `data:` is bound.
    name: "rejects-picture-reference",
    input: div('<p>see <img src="~/_entity/annotation/0c1d"/> here</p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-relative-path",
    input: div('<p><img src="images/logo.png"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "accepts-picture-data",
    input: div('<p><img src="data:image/png;base64,AA=="/>x</p>'),
    expected:
      "\n\n\ufffce2c4bf98685a8d0674e42fe055e6768d7da848691d4fa7c9dbd5b0703d9dfaf4\ufffcx\n\n",
  },
  {
    name: "accepts-combining-mark-after-picture",
    input: div('<p><img src="data:image/png;base64,AA=="/>&#x301;x</p>'),
    expected:
      "\n\n\ufffce2c4bf98685a8d0674e42fe055e6768d7da848691d4fa7c9dbd5b0703d9dfaf4\ufffc\u0301x\n\n",
  },
  {
    name: "rejects-table-in-caption",
    input: div(
      "<table><caption>X<table><tr><td>y</td></tr></table></caption><tr><td>z</td></tr></table>",
    ),
    expected: { error: "table-structure" },
  },
  {
    name: "rejects-table-over-slot-limit",
    input: div(`<table>${'<tr><td colspan="1000">a</td></tr>'.repeat(51)}</table>`),
    expected: { error: "table-size" },
  },
  {
    name: "rejects-picture-alt",
    input: div('<p><img src="data:image/png;base64,AA==" alt="Take 10 mg"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-without-source",
    input: div("<p><img/></p>"),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-start-tag",
    input: div('<p><img src="data:image/png;base64,AA=="></img></p>'),
    expected: { error: "void-element" },
  },
  {
    name: "rejects-picture-javascript",
    input: div('<p><img src="javascript:x"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-protocol-relative",
    input: div('<p><img src="//evil/x"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-parent-segment",
    input: div('<p><img src="a/../x"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-dot-segment",
    input: div('<p><img src="./x"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-absolute-path",
    input: div('<p><img src="/x"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-https",
    input: div('<p><img src="https://example.org/x"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-svg-data",
    input: div('<p><img src="data:image/svg+xml;base64,AAAA"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-data-length",
    input: div('<p><img src="data:image/png;base64,AAAAA"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-data-inner-padding",
    input: div('<p><img src="data:image/png;base64,AA=A"/></p>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-picture-in-sup",
    input: div('<p>10<sup><img src="data:image/png;base64,AA=="/></sup></p>'),
    expected: { error: "script-content" },
  },
  {
    name: "rejects-picture-in-row",
    input: div('<table><tr><img src="data:image/png;base64,AA=="/></tr></table>'),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-reserved-object-replacement",
    input: div("<p>&#xFFFC;</p>"),
    expected: { error: "reserved-character" },
  },
  {
    name: "rejects-reserved-raw-cell-marker",
    input: div("<p>\ufdd3</p>"),
    expected: { error: "reserved-character" },
  },
  {
    name: "rejects-reserved-before-table-content",
    input: div("<table><tr>&#xFFFC;</tr></table>"),
    expected: { error: "reserved-character" },
  },
  {
    name: "rejects-reserved-before-unmappable",
    input: div("<p><sup>&#xFDD0;</sup></p>"),
    expected: { error: "reserved-character" },
  },
  {
    name: "rejects-reserved-last-noncharacter",
    input: div("<p>&#xFDEF;</p>"),
    expected: { error: "reserved-character" },
  },
  {
    name: "accepts-near-reserved",
    input: div("<p>&#xFDCF;&#xFDF0;&#xFFF8;</p>"),
    expected: "\n\n\ufdcf\ufdf0\ufff8\n\n",
  },
  {
    // Review round 3: a row in which a cell starts, but every such cell spans down, is drawn at
    // zero height, so 600 mg reads against Children only.
    name: "rejects-row-whose-cells-all-span-down",
    input: div(
      '<table><tr><th>Population</th><th>Dose</th></tr><tr><td rowspan="2">Adults</td><td>400 mg</td></tr><tr><td rowspan="2">600 mg</td></tr><tr><td>Children</td></tr></table>',
    ),
    expected: { error: "table-shape" },
  },
  {
    name: "accepts-row-with-a-single-row-cell-beside-a-span",
    input: div(
      '<table><tr><td rowspan="2">Adults</td><td>400 mg</td></tr><tr><td>600 mg</td></tr></table>',
    ),
    expected:
      "\n\n\ufdd0\n\ufdd2\t\ufdd3\tAdults\t\t\ufdd3\t400 mg\t\n\n\ufdd2\t\ufdd5\t\t\ufdd3\t600 mg\t\n\n\ufdd1\n\n",
  },
  // Review round 3: the precedence rules section 5 states, each pinned.
  {
    name: "forbidden-character-before-reserved-character",
    input: div("<p>\ufdd0\u0001</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "missing-picture-source-before-void-element",
    input: div("<p><img></img></p>"),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "overlap-before-table-size",
    input: div(
      `<table><tr><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td><td>a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr><tr><td colspan="1000">a</td></tr></table><table><tr><td>a</td><td rowspan="2">b</td></tr><tr><td colspan="1000">c</td></tr></table>`,
    ),
    expected: { error: "table-shape" },
  },
  {
    name: "accepts-two-trailing-covered-slots",
    input: div(
      '<table><tr><td>a</td><td rowspan="2">b</td><td rowspan="2">c</td></tr><tr><td>d</td></tr></table>',
    ),
    expected:
      "\n\n\ufdd0\n\ufdd2\t\ufdd3\ta\t\t\ufdd3\tb\t\t\ufdd3\tc\t\n\n\ufdd2\t\ufdd3\td\t\t\ufdd5\t\t\ufdd5\t\n\n\ufdd1\n\n",
  },
  // Review round 13: nesting, "]]>", composition across inline markup, invisible breaks.
  {
    name: "accepts-nesting-32-below-root",
    input: div(
      "<span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span>x</span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span>",
    ),
    expected: "\nx\n",
  },
  {
    name: "rejects-nesting-33-below-root",
    input: div(
      "<span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span>x</span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span>",
    ),
    expected: { error: "nesting-depth" },
  },
  {
    name: "rejects-small-inside-small",
    input: div("<p>Do <small><small>not</small></small> exceed.</p>"),
    expected: { error: "nesting-depth" },
  },
  {
    name: "rejects-heading-inside-heading",
    input: div("<h1>a<h2>b</h2></h1>"),
    expected: { error: "nesting-depth" },
  },
  {
    name: "accepts-six-indenting-containers",
    input: div(
      "<blockquote><blockquote><blockquote><blockquote><blockquote><blockquote><p>x</p></blockquote></blockquote></blockquote></blockquote></blockquote></blockquote>",
    ),
    expected: "\n\n\n\n\n\n\n\nx\n\n\n\n\n\n\n\n",
  },
  {
    name: "rejects-seven-indenting-containers",
    input: div(
      "<blockquote><blockquote><blockquote><blockquote><blockquote><blockquote><blockquote><p>x</p></blockquote></blockquote></blockquote></blockquote></blockquote></blockquote></blockquote>",
    ),
    expected: { error: "nesting-depth" },
  },
  {
    name: "rejects-seven-indents-mixing-lists",
    input: div(
      "<ul><li><ol><li><blockquote><ul><li><ol><li><dl><dd><blockquote>x</blockquote></dd></dl></li></ol></li></ul></blockquote></li></ol></li></ul>",
    ),
    expected: { error: "nesting-depth" },
  },
  {
    name: "rejects-cdata-end-in-text",
    input: div("<p>a[b[0]]> 5</p>"),
    expected: { error: "cdata" },
  },
  {
    name: "accepts-cdata-end-escaped",
    input: div("<p>a[b[0]]&gt; 5</p>"),
    expected: "\n\na[b[0]]> 5\n\n",
  },
  {
    name: "rejects-not-less-than-across-bold",
    input: div("<p>CrCl &lt;<b>&#x338;</b> 30 ml/min</p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-not-equal-across-sup",
    input: div("<p>x =<sup>&#x338;</sup> y</p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-acute-across-bold",
    input: div("<p>caf<b>e</b>&#x301;</p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-acute-after-ligature-across-bold",
    input: div("<p>\ufb01<b>&#x301;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "accepts-acute-inside-one-run",
    input: div("<p><b>cafe&#x301;</b></p>"),
    expected: "\n\ncafe\u0301\n\n",
  },
  {
    name: "rejects-zero-width-space",
    input: div("<p>Take 2\u200b10 mg</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-zero-width-space-reference",
    input: div("<p>Take 2&#x200B;10 mg</p>"),
    expected: { error: "invisible-character" },
  },
  {
    name: "rejects-soft-hyphen-in-a-number",
    input: div("<p>Take 2&#xAD;10 mg</p>"),
    expected: { error: "invisible-character" },
  },
  // Review round 14: marks after inline tags, the shrink bound, and precedence pinned.
  {
    name: "rejects-mark-that-does-not-compose-after-bold",
    input: div("<p>CrCl &#x2A7D;<b>&#x338;</b> 30</p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-acute-after-q-across-bold",
    input: div("<p>q<b>&#x301;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-mark-after-a-long-run-across-bold",
    input: div(
      "<p>e&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;&#x316;<b>&#x301;</b></p>",
    ),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-hangul-vowel-across-bold",
    input: div("<p>&#x1100;<b>&#x1161;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "accepts-mark-before-an-end-tag",
    input: div("<p><b>e&#x301;</b>x</p>"),
    expected: "\n\ne\u0301x\n\n",
  },
  {
    name: "rejects-small-inside-code",
    input: div("<p><code>a<small>b</small></code></p>"),
    expected: { error: "nesting-depth" },
  },
  {
    name: "rejects-small-inside-h6",
    input: div("<h6>a<small>b</small></h6>"),
    expected: { error: "nesting-depth" },
  },
  {
    name: "accepts-sup-inside-h6",
    input: div("<h6>10<sup>9</sup></h6>"),
    expected: "\n\n10\u2079\n\n",
  },
  {
    name: "nesting-depth-before-parent-check",
    input: div(
      "<span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><li>x</li></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span>",
    ),
    expected: { error: "nesting-depth" },
  },
  {
    name: "void-element-counts-toward-depth",
    input: div(
      "<span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><span><br/></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span></span>",
    ),
    expected: { error: "nesting-depth" },
  },
  {
    name: "reserved-before-invisible-character",
    input: div("<p>\u00ad\ufffc</p>"),
    expected: { error: "reserved-character" },
  },
  {
    name: "cdata-end-before-unmappable-script",
    input: div("<p><sup>]]></sup></p>"),
    expected: { error: "cdata" },
  },
  {
    name: "table-content-before-cdata-end",
    input: div("<table><tr>]]></tr></table>"),
    expected: { error: "table-content" },
  }, // Review round 15: a mark after code points drawn as nothing, marks of every kind, the shrink
  // bound for h5, and signs under an underline.
  {
    name: "rejects-mark-after-word-joiner-across-bold",
    input: div("<p>q<b>&#x2060;&#x301;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-mark-after-bom-after-end-tag",
    input: div("<p><b>q</b>&#xFEFF;&#x301;</p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-stroke-after-word-joiner-across-bold",
    input: div("<p>&#x2A7D;<b>&#x2060;&#x338;</b> 30</p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-stroke-after-invisible-separator-across-bold",
    input: div("<p>CrCl &lt;<b>&#x2063;&#x338;</b> 30</p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-mark-after-zero-width-joiner-across-bold",
    input: div("<p>q<b>&#x200D;&#x301;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "accepts-word-joiner-then-letter-across-bold",
    input: div("<p>q<b>&#x2060;x</b></p>"),
    expected: "\n\nq\u2060x\n\n",
  },
  {
    name: "accepts-space-then-mark-across-bold",
    input: div("<p>q<b> &#x301;</b></p>"),
    expected: "\n\nq \u0301\n\n",
  },
  {
    name: "rejects-spacing-mark-across-bold",
    input: div("<p>&#x915;<b>&#x93E;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-enclosing-mark-across-bold",
    input: div("<p>1<b>&#x20DD;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-small-inside-h5",
    input: div("<h5>a<small>b</small></h5>"),
    expected: { error: "nesting-depth" },
  },
  // Review round 16: underline and links are refused (an underline turns a sign into another,
  // and no closed list of code points bounds which), and a mark that is itself ignorable.
  // Review round 17: a rule in a cell or a caption is drawn as a fraction bar.
  {
    name: "rejects-rule-in-cell",
    input: div("<table><tr><td>Take</td><td>1<hr/>2</td><td>tablet daily</td></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-rule-in-header-cell",
    input: div("<table><tr><th>1<hr/>2</th></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-rule-in-caption",
    input: div("<table><caption>1<hr/>4</caption><tr><td>x</td></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    name: "rejects-rule-in-block-in-cell",
    input: div("<table><tr><td><div>1<hr/>2</div></td></tr></table>"),
    expected: { error: "table-content" },
  },
  {
    // The rule is decided after the parent check (review round 18).
    name: "list-content-before-rule-in-cell",
    input: div("<table><tr><td><ul><hr/></ul></td></tr></table>"),
    expected: { error: "list-content" },
  },
  {
    name: "script-content-before-rule-in-cell",
    input: div("<table><tr><td><sup><hr/></sup></td></tr></table>"),
    expected: { error: "script-content" },
  },
  {
    name: "accepts-rule-after-table",
    input: div("<table><tr><td>1</td></tr></table><hr/><p>2</p>"),
    expected: "\n\n\ufdd0\n\ufdd2\t\ufdd3\t1\t\n\n\ufdd1\n\n\n\n2\n\n",
  },
  {
    name: "rejects-underline",
    input: div("<p>Contraindicated if CrCl <u>&lt;</u> 30 ml/min.</p>"),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-underlined-modifier-arrowhead",
    input: div("<p>CrCl <u>&#x2C2;</u> 30 ml/min</p>"),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-link",
    input: div('<p>CrCl <a href="https://example.org/">&lt;</a> 30</p>'),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-link-without-target",
    input: div("<p><a>see section 4.4</a></p>"),
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-grapheme-joiner-across-bold",
    input: div("<p>q<b>&#x34F;x</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-variation-selector-across-bold",
    input: div("<p>&#x2764;<b>&#xFE0F;</b></p>"),
    expected: { error: "combining-across-markup" },
  },
  {
    name: "rejects-interlinear-annotation-anchor",
    input: div("<p>Take 10 &#xFFF9;000 IU daily.</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-raw-interlinear-annotation-terminator",
    input: div("<p>Take 10 \ufffb000 IU daily.</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-syriac-abbreviation-mark",
    input: div("<p>10&#x70F;000</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "rejects-supplementary-concatenation-mark",
    input: div("<p>10&#x110BD;000</p>"),
    expected: { error: "forbidden-character" },
  },
  {
    name: "accepts-neighbour-of-concatenation-marks",
    input: div("<p>10&#x606;000</p>"),
    expected: "\n\n10\u0606000\n\n",
  },
];
