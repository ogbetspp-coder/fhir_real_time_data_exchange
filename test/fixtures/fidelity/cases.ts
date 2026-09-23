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
    "Dose\tFrequency\n10 mg\tOnce daily\n20 mg\tTwice daily",
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
const TABLE = "Dose\tFrequency\n10 mg\tOnce daily\n20 mg\tTwice daily";

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
    name: "span-ends-before-punctuation-passes",
    input: toInput(
      S,
      single(
        "smpc.4.1",
        paragraphs("Synthetic demonstration content for section 4.1; not for clinical use"),
        [spanFor(S, 1, "Synthetic demonstration content for section 4.1; not for clinical use")],
      ),
    ),
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
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
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
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
  // Rows 2 and 3: a soft hyphen before a line break in narrative text.
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
      reasons: { "smpc.4.4": "soft-hyphen-at-boundary" },
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
      reasons: { "smpc.4.4": "soft-hyphen-at-boundary" },
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
    name: "spanned-cell-rejected",
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
    expect: {
      status: "failed",
      sections: { "smpc.4.5": "malformed-narrative" },
      reasons: { "smpc.4.5": "forbidden-attribute" },
    },
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
];

// fidelity-norm/2.0.0, section 2 and section 3 step 5: C1 controls, U+000B, U+000C and the
// bidirectional controls reject on both sides; the accepting neighbours of each range are kept.
const NORMALIZATION_CASES_2_0_0: NormalizationCase[] = [
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
  { name: "narrow-no-break-space-after-overrides", input: "a\u202fb", expected: "a b" },
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
  { name: "plain", input: "Take one tablet daily.", expected: "Take one tablet daily." },
  {
    name: "collapse-whitespace",
    input: "  Take \t one\n\ntablet daily.  ",
    expected: "Take one tablet daily.",
  },
  { name: "unicode-spaces", input: "a\u2003b\u202fc\u3000d\u2028e", expected: "a b c d e" },
  { name: "nfc", input: "café", expected: "café" },
  { name: "soft-hyphen", input: "intra­venous", expected: "intravenous" },
  { name: "zero-width", input: "a​b﻿c⁠d", expected: "abcd" },
  { name: "ligatures", input: "ﬀ ﬁ ﬂ ﬃ ﬄ ﬆ", expected: "ff fi fl ffi ffl st" },
  { name: "bullets", input: "• one\n● two ▪ three", expected: "one two three" },
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
    name: "accepts-https-href",
    input: div('<p><a href="https://example.org/x/y.html">t</a></p>'),
    expected: "\n\nt\n\n",
  },
  {
    name: "rejects-href-with-query",
    input: div('<p><a href="https://example.org/x?q=text">t</a></p>'),
    expected: { error: "forbidden-attribute" },
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
    expected: { error: "forbidden-attribute" },
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
  { name: "rejects-ol", input: div("<ol><li>a</li></ol>"), expected: { error: "unknown-element" } },
  { name: "rejects-q", input: div("<p><q>a</q></p>"), expected: { error: "unknown-element" } },
  {
    name: "accepts-table-section-order",
    input: div(
      "<table><thead><tr><th>h</th></tr></thead><tbody><tr><td>b</td></tr></tbody><tfoot><tr><td>f</td></tr></tfoot></table>",
    ),
    expected: "\n\n\n\n\nh\n\n\n\n\n\nb\n\n\n\n\n\nf\n\n\n\n\n",
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
    expected: "\n\n\nc\n\n\na\n\n\n\n",
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
    expected: { error: "soft-hyphen-at-boundary" },
  },
  {
    name: "rejects-soft-hyphen-before-block-end",
    input: div("<h2>intra&#173;</h2><p>venous</p>"),
    expected: { error: "soft-hyphen-at-boundary" },
  },
  {
    name: "accepts-soft-hyphen-inside-text",
    input: div("<p>intra&#173;venous</p>"),
    expected: "\n\nintra­venous\n\n",
  },
  {
    name: "rejects-javascript-href",
    input: div('<p><a href="javascript:alert(1)">t</a></p>'),
    expected: { error: "forbidden-attribute" },
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
    expected: "\n\n\n\na\n\nb\n\n\n\n",
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
    input: div("<p>a</p><img/>"),
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
    expected: { error: "void-element" },
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
    input: div("<p><sup>‐‑‒–—−﹣－</sup></p>"),
    expected: `\n\n${"⁻".repeat(8)}\n\n`,
  },
  {
    name: "sup-plus-variants-fold",
    input: div("<p><sup>﹢＋</sup></p>"),
    expected: "\n\n⁺⁺\n\n",
  },
  {
    name: "sub-dashes-fold-to-minus",
    input: div("<p><sub>‐–—−﹣－</sub></p>"),
    expected: `\n\n${"₋".repeat(6)}\n\n`,
  },
  {
    name: "sub-plus-variants-fold",
    input: div("<p><sub>﹢＋</sub></p>"),
    expected: "\n\n₊₊\n\n",
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
  {
    name: "sup-horizontal-bar-kept",
    input: div("<p><sup>―</sup></p>"),
    expected: "\n\n―\n\n",
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
    name: "sub-superscript-digit-kept",
    input: div("<p><sub>²</sub></p>"),
    expected: "\n\n²\n\n",
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
    name: "rejects-soft-hyphen-before-raw-lf",
    input: div("<p>non&#173;\nsmokers</p>"),
    expected: { error: "soft-hyphen-at-boundary" },
  },
  {
    name: "rejects-soft-hyphen-before-lf-reference",
    input: div("<p>non&#173;&#10;smokers</p>"),
    expected: { error: "soft-hyphen-at-boundary" },
  },
  {
    name: "rejects-soft-hyphen-cr-then-br",
    input: div("<p>non&#173;&#13;<br/>smokers</p>"),
    expected: { error: "soft-hyphen-at-boundary" },
  },
  {
    name: "rejects-raw-soft-hyphen-before-crlf",
    input: div("<p>non\u00ad\r\nsmokers</p>"),
    expected: { error: "soft-hyphen-at-boundary" },
  },
  {
    name: "rejects-soft-hyphen-before-hr",
    input: div("<p>non\u00ad<hr/>smokers</p>"),
    expected: { error: "soft-hyphen-at-boundary" },
  },
  {
    name: "accepts-soft-hyphen-before-cr-alone",
    input: div("<p>non&#173;&#13;smokers</p>"),
    expected: "\n\nnon\u00ad\rsmokers\n\n",
  },
  {
    name: "accepts-soft-hyphen-before-space",
    input: div("<p>non&#173; smokers</p>"),
    expected: "\n\nnon\u00ad smokers\n\n",
  },
  {
    name: "soft-hyphen-decided-after-scan",
    input: div("<p>non&#173;</p><img/>"),
    expected: { error: "unknown-element" },
  },
  {
    name: "unbalanced-before-soft-hyphen",
    input: `<div ${XHTML}><p>non&#173;</p>`,
    expected: { error: "unbalanced-tag" },
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
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-scope-on-td",
    input: div('<table><tr><td scope="row">a</td></tr></table>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "accepts-scope-on-th",
    input: div('<table><tr><th scope="row">h</th><td>a</td></tr></table>'),
    expected: "\n\n\n\nh\n\na\n\n\n\n",
  },
  {
    name: "rejects-colspan",
    input: div('<table><tr><td colspan="2">a</td></tr></table>'),
    expected: { error: "forbidden-attribute" },
  },
  {
    name: "rejects-rowspan",
    input: div('<table><tr><td rowspan="2">a</td></tr></table>'),
    expected: { error: "forbidden-attribute" },
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
    expected: "\n\n\n \n\t\n\r\n\nh\n \n\n\n\n \n\na\n\n \n\n\n",
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
    name: "accepts-nested-table-in-cell",
    input: div(
      "<table><tr><td><table><tr><td>a</td><td>b</td></tr></table></td></tr><tr><td>c</td></tr></table>",
    ),
    expected: "\n\n\n\n\n\n\na\n\nb\n\n\n\n\n\n\nc\n\n\n\n",
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
    expected: { error: "table-shape" },
  },
  {
    name: "accepts-header-and-data-cells",
    input: div("<table><tr><th>h</th><td>a</td></tr><tr><td>b</td><td>c</td></tr></table>"),
    expected: "\n\n\n\nh\n\na\n\n\n\nb\n\nc\n\n\n\n",
  },
  { name: "accepts-empty-table", input: div("<table></table>"), expected: "\n\n\n\n" },
  {
    name: "accepts-caption-only-table",
    input: div("<table><caption>c</caption></table>"),
    expected: "\n\n\nc\n\n\n",
  },
  {
    name: "accepts-empty-rows",
    input: div("<table><tr></tr><tr></tr></table>"),
    expected: "\n\n\n\n\n\n\n\n",
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
    input: `<img ${XHTML}/>`,
    expected: { error: "unknown-element" },
  },
  {
    name: "rejects-multiple-roots-before-attributes",
    input: `${div("<p>a</p>")}<div class="c"></div>`,
    expected: { error: "multiple-roots" },
  },
];
