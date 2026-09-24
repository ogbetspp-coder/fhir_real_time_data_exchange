import {
  CANONICAL_SUBMISSION_VERSION,
  approvedContent,
  verifyDocumentSubmission,
  type CanonicalSubmission,
  type ExtractionTooling,
  type IngestionProvenance,
  type SectionProvenance,
  type SourceSpan,
  type StructuringDecision,
  type CanonicalBundle,
} from "../contracts/index.js";
import {
  NORMALIZATION_VERSION,
  collectNarrativeSections,
  normalizeText,
  verifyNarrativeFidelity,
  xhtmlToText,
  type FidelityReport,
  type NarrativeSection,
  type SourceDocumentText,
  type SourcePage,
} from "../fidelity/index.js";
import type { EmaMapping } from "../fhir/mapping.js";
import { isComposition } from "../fhir/types.js";
import { sha256, sha256Utf8 } from "../lib/hash.js";
import {
  DEFAULT_SYNTHETIC_PRODUCT_ID,
  syntheticProduct,
  syntheticSourceStem,
  type SyntheticFixtureOptions,
  type SyntheticProduct,
  type SyntheticVersion,
} from "./synthetic-products.js";
import { createSyntheticType2Bundle } from "./synthetic.js";

// Deterministic Zone A hand-off for the synthetic Type 2 fixture: the same Bundle the fixture
// path publishes, plus the extracted page text, spans, and approval a real structuring service
// would have produced. Everything is fixed; nothing reads the clock or a random source.
//
// The default product at version 1 is the frozen fixture that `test/fixtures/contracts/*.json`
// was exported from; `src/fixtures/synthetic-products.ts` explains what may and may not move.

const EXTRACTOR_VERSION = "synthetic-extractor/1.0.0";

// Where a Zone A service would have written the three by-reference parts. The fixture carries
// them so it is a complete example of what the submission reader must resolve, not only of what
// the ingress gate must accept.
export const SYNTHETIC_SUBMISSION_BUCKET = "synthetic-bucket";

export type SyntheticPartUris = {
  fidelityReport: string;
  sourceText: string;
};

export type SyntheticSubmissionOptions = SyntheticFixtureOptions & {
  // Where the fidelity report and the extracted page text will actually be stored. Both are
  // inside the approved content, so a seeding run has to set them before anything is hashed.
  partUris?: SyntheticPartUris;
};

function resolve(options: SyntheticFixtureOptions): {
  product: SyntheticProduct;
  version: SyntheticVersion;
} {
  return {
    product: syntheticProduct(options.product ?? DEFAULT_SYNTHETIC_PRODUCT_ID),
    version: options.version ?? 1,
  };
}

// The default locations of the three by-reference parts, one set per product and version.
export function syntheticSubmissionUris(
  options: SyntheticFixtureOptions = {},
): SyntheticPartUris & {
  submission: string;
} {
  const { product, version } = resolve(options);
  const stem = syntheticSourceStem(product, version);
  return {
    submission: `gs://${SYNTHETIC_SUBMISSION_BUCKET}/${stem}.submission.json`,
    fidelityReport: `gs://${SYNTHETIC_SUBMISSION_BUCKET}/${stem}.fidelity-report.json`,
    sourceText: `gs://${SYNTHETIC_SUBMISSION_BUCKET}/${stem}.pages.json`,
  };
}

const DEFAULT_URIS = syntheticSubmissionUris();
export const SYNTHETIC_SUBMISSION_URI = DEFAULT_URIS.submission;
export const SYNTHETIC_REPORT_URI = DEFAULT_URIS.fidelityReport;
export const SYNTHETIC_SOURCE_TEXT_URI = DEFAULT_URIS.sourceText;

const PAGE_COUNT = 3;

function header(product: SyntheticProduct): string {
  return `${product.productName} - synthetic demonstration extract\n`;
}

function footer(page: number): string {
  return `Page ${page} of ${PAGE_COUNT}`;
}

function narrativeText(div: string): string {
  return normalizeText(xhtmlToText(div));
}

function codePointLength(text: string): number {
  return Array.from(text).length;
}

type PagedSource = {
  source: SourceDocumentText;
  spans: Map<string, SourceSpan>;
};

// One line per narrative section, in document order, spread over PAGE_COUNT pages so that the
// fixture exercises multi-page extraction. Offsets are code points, as ADR 0002 requires.
function buildSourceText(sections: NarrativeSection[], product: SyntheticProduct): PagedSource {
  const perPage = Math.ceil(sections.length / PAGE_COUNT);
  const pageHeader = header(product);
  const bodyStart = codePointLength(pageHeader);
  const pages: SourcePage[] = [];
  const spans = new Map<string, SourceSpan>();

  for (let page = 1; page <= PAGE_COUNT; page += 1) {
    const onPage = sections.slice((page - 1) * perPage, page * perPage);
    if (onPage.length === 0) {
      throw new Error(`Synthetic source document page ${page} would be empty`);
    }
    const lines: string[] = [];
    let cursor = bodyStart;
    for (const section of onPage) {
      const line = narrativeText(section.div);
      const endOffset = cursor + codePointLength(line);
      spans.set(section.sourceKey, {
        page,
        startOffset: cursor,
        endOffset,
        textSha256: sha256Utf8(line),
      });
      lines.push(line);
      cursor = endOffset + 1;
    }
    // A page body ends with its final line terminator (docs/fidelity-normalization.md section 7).
    const body = `${lines.join("\n")}\n`;
    pages.push({
      page,
      text: `${pageHeader}${body}${footer(page)}`,
      bodyStart,
      bodyEnd: bodyStart + codePointLength(body),
    });
  }

  return { source: { extractorVersion: EXTRACTOR_VERSION, pages }, spans };
}

function buildSectionProvenance(
  sections: NarrativeSection[],
  spans: Map<string, SourceSpan>,
): SectionProvenance[] {
  return sections.map((section) => {
    const span = spans.get(section.sourceKey);
    if (span === undefined) {
      throw new Error(`Synthetic source document has no span for ${section.sourceKey}`);
    }
    return {
      sourceKey: section.sourceKey,
      spans: [span],
      narrativeDivSha256: sha256Utf8(section.div),
      normalizedTextSha256: sha256Utf8(narrativeText(section.div)),
    };
  });
}

function buildDecisions(sections: NarrativeSection[]): StructuringDecision[] {
  return [
    ...sections.map((section) => ({
      target: section.path,
      sourceKey: section.sourceKey,
      action: "extracted-verbatim" as const,
    })),
    {
      target: "Composition.type",
      action: "code-mapped" as const,
      terminologyRef: {
        system: "https://khs.dev/fhir/CodeSystem/document-type",
        code: "smpc",
        lookupId: "synthetic-lookup-1",
      },
    },
  ];
}

export type SyntheticSubmission = {
  submission: CanonicalSubmission;
  fidelityReport: FidelityReport;
  sourceText: SourceDocumentText;
};

export function createSyntheticSubmission(
  mapping: EmaMapping,
  options: SyntheticSubmissionOptions = {},
): SyntheticSubmission {
  const { product, version } = resolve(options);
  const bundle = createSyntheticType2Bundle(mapping, { product: product.id, version });
  const composition = bundle.entry[0]?.resource;
  if (composition === undefined || !isComposition(composition)) {
    throw new Error("Synthetic Type 2 fixture must have Composition as its first entry");
  }

  const identity = product.submissions[version];
  const partUris = options.partUris ?? syntheticSubmissionUris({ product: product.id, version });
  const sourceFilename = `${syntheticSourceStem(product, version)}.pdf`;

  const sections = collectNarrativeSections(composition, mapping.sourceCodeSystem);
  const { source, spans } = buildSourceText(sections, product);
  const sectionProvenance = buildSectionProvenance(sections, spans);

  const report = verifyNarrativeFidelity({
    normalizationVersion: NORMALIZATION_VERSION,
    source,
    sections,
    provenance: sectionProvenance,
  });
  if (report.status !== "passed") {
    throw new Error("Synthetic submission fixture must produce a passing fidelity report");
  }

  const extraction: ExtractionTooling = {
    extractionRunId: identity.extractionRunId,
    serviceVersion: "synthetic",
    parser: { name: "synthetic-extractor", version: "1.0.0" },
    terminologyService: {
      name: "synthetic-terminology",
      version: "1.0.0",
      snapshotSha256: sha256Utf8("synthetic-terminology-snapshot"),
    },
  };

  const provenance: IngestionProvenance = {
    sourceDocument: {
      kind: "drawn",
      sha256: sha256Utf8(sourceFilename),
      byteLength: 1024,
      mediaType: "application/pdf",
      filename: sourceFilename,
      pageCount: source.pages.length,
      extractedText: {
        uri: partUris.sourceText,
        sha256: sha256(source),
        extractorVersion: EXTRACTOR_VERSION,
      },
    },
    extraction,
    sections: sectionProvenance,
    decisions: buildDecisions(sections),
    fidelity: {
      normalizationVersion: NORMALIZATION_VERSION,
      status: "passed",
      sectionsChecked: report.summary.total,
      sectionsMatched: report.summary.verified,
      narrativeBindingSha256: report.narrativeBindingSha256,
      reportSha256: report.reportHash,
      reportUri: partUris.fidelityReport,
    },
  };

  const type2Bundle = bundle as unknown as CanonicalBundle;
  const submission: CanonicalSubmission = {
    schemaVersion: CANONICAL_SUBMISSION_VERSION,
    submissionId: identity.submissionId,
    createdAt: identity.createdAt,
    graphType: "type2",
    bundle: type2Bundle,
    bundleSha256: sha256(type2Bundle),
    provenance,
    approval: {
      approverId: "urn:reviewer:synthetic-01",
      approverRole: "content-reviewer",
      approvedAt: identity.approvedAt,
      method: "api-attestation",
      meaning: "reviewed-fidelity-and-structure",
      approvedContentSha256: sha256(
        approvedContent({
          schemaVersion: CANONICAL_SUBMISSION_VERSION,
          graphType: "type2",
          bundle: type2Bundle,
          provenance,
        }),
      ),
    },
  };

  // The fixture is only useful if it is what Zone B accepts where synthetic sources are allowed;
  // this throws the moment it drifts.
  verifyDocumentSubmission(
    { submission, fidelityReport: report, sourceText: source },
    mapping.sourceCodeSystem,
    { allowSyntheticSources: true },
  );

  return { submission, fidelityReport: report, sourceText: source };
}
