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
const FOOTER = (page: number): string => `\nPage ${page} of 3`;

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

function buildPage(page: number, body: string): SourcePage {
  const text = `${HEADER}${body}${FOOTER(page)}`;
  const bodyStart = Array.from(HEADER).length;
  const bodyEnd = bodyStart + Array.from(body).length;
  return { page, text, bodyStart, bodyEnd };
}

export function buildSource(): SourceDocumentText {
  return {
    extractorVersion: "synthetic-extractor/1.0.0",
    pages: PAGE_BODIES.map((body, index) => buildPage(index + 1, body)),
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

function shrunkenBodySource(): SourceDocumentText {
  const source = buildSource();
  const [first] = source.pages;
  if (first === undefined) throw new Error("fixture");
  const cut = Array.from(first.text).join("").indexOf(INFUSION);
  const bodyEnd = Array.from(first.text.slice(0, cut)).length;
  return { ...source, pages: [{ ...first, bodyEnd }, ...source.pages.slice(1)] };
}

export const verifyCases: VerifyCase[] = [
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
  // A body range that excludes page text still verifies, but the report exposes the excluded
  // amount through pageCodePoints so a reviewer can see it.
  {
    name: "shrunken-body-reports-page-total",
    input: toInput(
      shrunkenBodySource(),
      single("smpc.4.1", paragraphs(INDICATIONS), [spanFor(S, 1, INDICATIONS)]),
    ),
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
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
    expect: { status: "passed", sections: { "smpc.4.1": "verified" } },
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
    expect: { status: "failed", sections: { "smpc.4.1": "mismatch" } },
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
];

export const normalizationCases: NormalizationCase[] = [
  // Invisible characters are removed before NFC, so a composition they would otherwise block
  // happens in the first pass and the procedure stays idempotent.
  { name: "invisible-blocks-nfc", input: "cafe​́", expected: "café" },
  { name: "ligature-then-combining", input: "ﬁ́", expected: "fí" },
  { name: "plain", input: "Take one tablet daily.", expected: "Take one tablet daily." },
  {
    name: "collapse-whitespace",
    input: "  Take \t one\n\ntablet daily.  ",
    expected: "Take one tablet daily.",
  },
  { name: "unicode-spaces", input: "a b c　d e", expected: "a b c d e" },
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
  { name: "allows-tab-lf-cr-ff-vt", input: "a\tb\nc\rd\fef", expected: "a b c d e f" },
];

export const xhtmlCases: XhtmlCase[] = [
  // Attribute values are never compared against the source, so they are token-limited.
  {
    name: "accepts-https-href",
    input: div('<p><a href="https://example.org/x?y=1#z">t</a></p>'),
    expected: "\n\nt\n\n",
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
    expected: "\n\na b c 2\n\n",
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
    expected: "\n\nt\n\n",
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
];
