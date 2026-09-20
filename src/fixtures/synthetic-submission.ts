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
  type Type2Bundle,
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
import { createSyntheticType2Bundle } from "./synthetic.js";

// Deterministic Zone A hand-off for the synthetic Type 2 fixture: the same Bundle the fixture
// path publishes, plus the extracted page text, spans, and approval a real structuring service
// would have produced. Everything is fixed; nothing reads the clock or a random source.

const SUBMISSION_ID = "4b1d2c3e-5f60-4a71-8b92-0c1d2e3f4a5b";
const EXTRACTION_RUN_ID = "7c8d9e0f-1a2b-4c3d-9e4f-5a6b7c8d9e0f";
const CREATED_AT = "2026-09-19T00:00:00Z";
const APPROVED_AT = "2026-09-19T00:00:00Z";
const EXTRACTOR_VERSION = "synthetic-extractor/1.0.0";
const SOURCE_FILENAME = "synthetic-smpc.pdf";
const PAGE_COUNT = 3;
const HEADER = "Synthetic Paracetamol 500 mg tablets - synthetic demonstration extract\n";

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
function buildSourceText(sections: NarrativeSection[]): PagedSource {
  const perPage = Math.ceil(sections.length / PAGE_COUNT);
  const bodyStart = codePointLength(HEADER);
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
      text: `${HEADER}${body}${footer(page)}`,
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

export function createSyntheticSubmission(mapping: EmaMapping): SyntheticSubmission {
  const bundle = createSyntheticType2Bundle(mapping);
  const composition = bundle.entry[0]?.resource;
  if (composition === undefined || !isComposition(composition)) {
    throw new Error("Synthetic Type 2 fixture must have Composition as its first entry");
  }

  const sections = collectNarrativeSections(composition, mapping.sourceCodeSystem);
  const { source, spans } = buildSourceText(sections);
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
    extractionRunId: EXTRACTION_RUN_ID,
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
      sha256: sha256Utf8(SOURCE_FILENAME),
      byteLength: 1024,
      mediaType: "application/pdf",
      filename: SOURCE_FILENAME,
      pageCount: source.pages.length,
      extractedText: {
        uri: "gs://synthetic-bucket/synthetic-smpc.pages.json",
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
    },
  };

  const type2Bundle = bundle as unknown as Type2Bundle;
  const submission: CanonicalSubmission = {
    schemaVersion: CANONICAL_SUBMISSION_VERSION,
    submissionId: SUBMISSION_ID,
    createdAt: CREATED_AT,
    bundle: type2Bundle,
    bundleSha256: sha256(type2Bundle),
    provenance,
    approval: {
      approverId: "urn:reviewer:synthetic-01",
      approverRole: "content-reviewer",
      approvedAt: APPROVED_AT,
      method: "api-attestation",
      meaning: "reviewed-fidelity-and-structure",
      approvedContentSha256: sha256(
        approvedContent({
          schemaVersion: CANONICAL_SUBMISSION_VERSION,
          bundle: type2Bundle,
          provenance,
        }),
      ),
    },
  };

  // The fixture is only useful if it is what Zone B accepts; this throws the moment it drifts.
  verifyDocumentSubmission(
    { submission, fidelityReport: report, sourceText: source },
    mapping.sourceCodeSystem,
  );

  return { submission, fidelityReport: report, sourceText: source };
}
