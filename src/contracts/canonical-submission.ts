import { z } from "zod";

import {
  FidelityError,
  NORMALIZATION_VERSION,
  collectNarrativeSections,
  computeNarrativeBinding,
  verifyNarrativeFidelity,
  verifyReportHash,
  type FidelityReport,
  type NarrativeSection,
} from "../fidelity/index.js";
import type { FhirBundle } from "../fhir/types.js";
import { xhtmlToText } from "../fidelity/xhtml.js";
import { sha256, sha256Utf8 } from "../lib/hash.js";
import { jsonShapeIssues } from "../lib/json-shape.js";
import { IsoDateTime, Sha256Hex, Uuid } from "./common.js";
import { FidelityReportSchema, SourceDocumentTextSchema } from "./fidelity-report.js";
import { ApprovalSchema, IngestionProvenanceSchema } from "./ingestion-provenance.js";
import {
  CanonicalBundleSchema,
  LooseCompositionSchema,
  type CanonicalBundle,
} from "./canonical-bundle.js";

export const CANONICAL_SUBMISSION_VERSION = "2.0.0";

// Type 2: the full product graph. Type 1: an authority import's text-only record, whose product
// identity comes from the authority's index (docs/design/authority-import-contract.md, D9).
export const GraphType = z.enum(["type1", "type2"]).meta({ id: "GraphType" });

// An identifier value in this namespace is written only by the authority importer
// (docs/design/authority-import-contract.md, D7). Every other path refuses a source that has one.
export const AUTHORITY_IMPORT_PREFIX = "authority-import:";

// The importer, the only extractor of an authority's publication; Zone B re-executes it.
export const AUTHORITY_IMPORTER_NAME = "authority-import";
// What a synthetic extractor, terminology service or identifier value begins with.
export const SYNTHETIC_PREFIX = "synthetic-";

// Bounds on strings anywhere in the Bundle outside the verified narratives. Product-graph
// fields are names, codes, identifiers, and URLs; regulated prose is longer than this and must
// travel as a verified narrative section instead. This bounds smuggling; it does not prove the
// structured fields (ADR 0002: those belong to master data and field-level provenance).
const MAX_UNVERIFIED_STRING_LENGTH = 300;
// The longest QRD section title ("6.5 Nature and contents of container and special equipment
// for use, administration or implantation") is 14 words; a sentence of prose is longer. Words
// are counted with the ICU word segmenter so scripts without inter-word spaces are counted too.
const MAX_UNVERIFIED_WORDS = 20;
// Aggregate budget across the whole Bundle, so many short strings cannot add up to a document.
// The synthetic Type 2 Bundle carries ~270 strings / ~7,000 characters outside its narratives.
// Property names count toward the character budget too, each time one is written: a key is
// bounded by JSON_KEY to 64 characters, but thousands of distinct CamelCase keys, or a small
// vocabulary of them repeated in order, would otherwise carry a document outside every budget.
// Keys are not counted as strings: the synthetic Type 2 Bundle writes ~510 of them (~2,800
// characters, ~11,200 with its strings), and a larger label proportionally more, which the
// string count was not sized for. The word rule is not applied to a key, which the segmenter
// reads as one word however it is cased; its bound is JSON_KEY's 64 characters.
const MAX_UNVERIFIED_STRINGS = 3_000;
const MAX_UNVERIFIED_TOTAL_LENGTH = 40_000;
const JSON_KEY = /^_?[A-Za-z][A-Za-z0-9]{0,63}$/;

const WORD_SEGMENTER = new Intl.Segmenter(undefined, { granularity: "word" });

function countWordsAnyScript(value: string): number {
  let words = 0;
  for (const segment of WORD_SEGMENTER.segment(value)) if (segment.isWordLike === true) words += 1;
  return words;
}

const CanonicalSubmissionBase = z.strictObject({
  schemaVersion: z.literal(CANONICAL_SUBMISSION_VERSION),
  submissionId: Uuid,
  createdAt: IsoDateTime,
  graphType: GraphType,
  bundle: CanonicalBundleSchema,
  bundleSha256: Sha256Hex,
  provenance: IngestionProvenanceSchema,
  approval: ApprovalSchema,
});

export type CanonicalSubmission = z.infer<typeof CanonicalSubmissionBase>;

// The content an approver attests to. The approval block itself is excluded, and the schema
// version is included so a re-approval is forced whenever the contract changes.
export function approvedContent(
  submission: Pick<CanonicalSubmission, "schemaVersion" | "graphType" | "bundle" | "provenance">,
): {
  schemaVersion: string;
  graphType: CanonicalSubmission["graphType"];
  bundle: CanonicalBundle;
  provenance: CanonicalSubmission["provenance"];
} {
  return {
    schemaVersion: submission.schemaVersion,
    graphType: submission.graphType,
    bundle: submission.bundle,
    provenance: submission.provenance,
  };
}

// How the source, the graph type, the approval and the extractor must fit together
// (docs/design/authority-import-contract.md, D3, D7, D8).
function sourceIssues(submission: CanonicalSubmission): string[] {
  const issues: string[] = [];
  const { provenance, approval, graphType } = submission;
  const { sourceDocument: source, extraction } = provenance;
  const { parser } = extraction;

  if (parser.name.includes("/") || parser.name.includes("@")) {
    issues.push("extraction.parser.name must not contain / or @");
  }
  if (source.extractedText.extractorVersion !== `${parser.name}/${parser.version}`) {
    issues.push(
      "sourceDocument.extractedText.extractorVersion must be extraction.parser's name/version",
    );
  }

  if (source.kind === "drawn") {
    if (graphType !== "type2") issues.push("A drawn source carries a type2 graph");
    if (approval.method === "authority-publication") {
      issues.push("An authority-publication approval requires an authority-publication source");
    }
    return issues;
  }

  if (graphType !== "type1") issues.push("An authority publication carries a type1 graph");
  if (parser.name !== AUTHORITY_IMPORTER_NAME) {
    issues.push(`An authority publication's extractor is ${AUTHORITY_IMPORTER_NAME}`);
  }
  if (extraction.model !== undefined || extraction.promptTemplate !== undefined) {
    issues.push("An authority import uses no model and no prompt template");
  }
  const { request } = source;
  if (request.authority !== source.authority) issues.push("The request names another authority");
  if (request.documentId !== source.document.id) issues.push("The request names another document");
  if (request.indexId !== source.index.id) issues.push("The request names another index");
  source.sectionPages.forEach(({ page }, position) => {
    if (page !== position + 1) issues.push("sourceDocument.sectionPages must number pages 1..n");
  });
  const paths = source.sectionPages.map(({ path }) => path);
  if (new Set(paths).size !== paths.length) {
    issues.push("sourceDocument.sectionPages repeats a section path");
  }

  if (approval.method !== "authority-publication") {
    issues.push("An authority publication's approval is its authority-publication");
    return issues;
  }
  const { publication } = approval;
  if (approval.authority !== source.authority) issues.push("The approval names another authority");
  if (publication.documentId !== source.document.id) {
    issues.push("The approval names another document");
  }
  if (publication.indexId !== source.index.id) issues.push("The approval names another index");
  if (publication.epiId !== source.index.epiId) issues.push("The approval names another ePI");
  if (publication.versionNumber !== source.index.versionNumber) {
    issues.push("The approval names another version of the ePI");
  }
  return issues;
}

// Invariants that need nothing but the submission itself. They are Zone B ingress rules and are
// deliberately not expressible in JSON Schema (ADR 0002).
export function structuralInvariantIssues(submission: CanonicalSubmission): string[] {
  const issues: string[] = [];
  const { provenance, approval } = submission;
  issues.push(...sourceIssues(submission));

  if (sha256(submission.bundle) !== submission.bundleSha256) {
    issues.push("bundleSha256 does not match the Bundle");
  }
  if (sha256(approvedContent(submission)) !== approval.approvedContentSha256) {
    issues.push("approval.approvedContentSha256 does not match the submitted content");
  }

  const { fidelity } = provenance;
  if (fidelity.status !== "passed") issues.push("fidelity.status must be passed");
  if (fidelity.sectionsMatched !== fidelity.sectionsChecked) {
    issues.push("fidelity.sectionsMatched must equal fidelity.sectionsChecked");
  }
  if (fidelity.sectionsChecked !== provenance.sections.length) {
    issues.push("fidelity.sectionsChecked must equal the number of provenance sections");
  }
  if (fidelity.normalizationVersion !== NORMALIZATION_VERSION) {
    issues.push(`fidelity.normalizationVersion must be ${NORMALIZATION_VERSION}`);
  }

  const keys = provenance.sections.map(({ sourceKey }) => sourceKey);
  if (new Set(keys).size !== keys.length)
    issues.push("provenance.sections contains duplicate sourceKey");

  for (const [position, decision] of provenance.decisions.entries()) {
    const at = `provenance.decisions[${position}]`;
    switch (decision.action) {
      case "code-mapped":
        if (decision.terminologyRef === undefined)
          issues.push(`${at}: code-mapped requires terminologyRef`);
        break;
      case "defaulted-by-rule":
        if (decision.ruleId === undefined) issues.push(`${at}: defaulted-by-rule requires ruleId`);
        break;
      case "human-edited":
        if (decision.editorId === undefined) issues.push(`${at}: human-edited requires editorId`);
        if (decision.reason === undefined) issues.push(`${at}: human-edited requires reason`);
        break;
      case "rejected":
        if (decision.reason === undefined) issues.push(`${at}: rejected requires reason`);
        break;
      case "extracted-verbatim":
        break;
    }
  }
  const codeMapped = provenance.decisions.some(({ action }) => action === "code-mapped");
  if (codeMapped && provenance.extraction.terminologyService === undefined) {
    issues.push("extraction.terminologyService is required when any decision is code-mapped");
  }

  return issues;
}

export const CanonicalSubmissionSchema = CanonicalSubmissionBase.superRefine(
  (submission, context) => {
    for (const message of structuralInvariantIssues(submission)) {
      context.addIssue({ code: "custom", message });
    }
  },
).meta({
  id: "CanonicalSubmission",
  description:
    "Approved hand-off from Zone A structuring to Zone B publishing. Zone B recomputes every hash and re-executes the fidelity check before transforming anything.",
});

export class SubmissionRejectedError extends Error {
  public constructor(
    message: string,
    public readonly issues: string[],
  ) {
    super(message);
    this.name = "SubmissionRejectedError";
  }
}

export type DocumentSubmissionInput = {
  submission: unknown;
  fidelityReport: unknown;
  sourceText: unknown;
};

export type DocumentGateResult = {
  submission: CanonicalSubmission;
  bundle: FhirBundle;
  narrativeSections: NarrativeSection[];
  report: FidelityReport;
};

// The bounds every part of a document submission meets before anything recursive (the contract
// walk, canonical hashing) touches it. Both gates apply them first.
export function documentShapeIssues(input: DocumentSubmissionInput): string[] {
  return [
    ...jsonShapeIssues("submission", input.submission),
    ...jsonShapeIssues("fidelityReport", input.fidelityReport),
    ...jsonShapeIssues("sourceText", input.sourceText),
  ];
}

// A parse is trusted only when it is the input. Every hash the gate checks and every walk it makes
// is over the parsed value, while what is stored and recorded is the input: a member the parser
// dropped (zod drops an own `__proto__`) would be neither hashed nor walked, and the recorded
// proof could not be reproduced from the stored object.
export function losslessParseIssues(name: string, input: unknown, parsed: unknown): string[] {
  return sha256(parsed) === sha256(input) ? [] : [`${name} does not parse to the value it is`];
}

function zodIssues(error: z.ZodError): string[] {
  return error.issues.map((issue) => {
    const path = issue.path.map(String).join(".");
    return path.length === 0 ? issue.message : `${path}: ${issue.message}`;
  });
}

// JSON path of a section's narrative inside the Bundle, in the same notation `unverifiedText`
// produces while walking it.
function narrativeDivPath(sectionPath: string): string {
  return `entry[0].resource${sectionPath.slice("Composition".length)}.text.div`;
}

// Every string in the Bundle that is not a verified narrative must be short and markup-free:
// the fidelity proof covers coded sections, so prose anywhere else is unverified by definition.
function unverifiedTextIssues(bundle: unknown, verifiedDivPaths: Set<string>): string[] {
  const issues: string[] = [];
  let strings = 0;
  let totalLength = 0;
  const walk = (value: unknown, path: string): void => {
    if (typeof value === "string") {
      if (verifiedDivPaths.has(path)) return;
      strings += 1;
      totalLength += value.length;
      if (path.endsWith(".text.div")) {
        issues.push(`Narrative outside verified sections at ${path}`);
      } else if (
        value.length > MAX_UNVERIFIED_STRING_LENGTH ||
        value.includes("<") ||
        countWordsAnyScript(value) > MAX_UNVERIFIED_WORDS
      ) {
        issues.push(`Unverified free text at ${path}`);
      }
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${path}[${index}]`));
      return;
    }
    if (value !== null && typeof value === "object") {
      for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
        // Property names are a channel too: FHIR JSON keys are identifiers, never prose.
        if (!JSON_KEY.test(key)) {
          issues.push(`Unverified free text in a property name at ${path}`);
          continue;
        }
        totalLength += key.length;
        walk(child, path.length === 0 ? key : `${path}.${key}`);
      }
    }
  };
  walk(bundle, "");
  if (strings > MAX_UNVERIFIED_STRINGS) {
    issues.push(`Bundle carries more than ${MAX_UNVERIFIED_STRINGS} unverified strings`);
  }
  if (totalLength > MAX_UNVERIFIED_TOTAL_LENGTH) {
    issues.push(
      `Unverified strings and property names exceed ${MAX_UNVERIFIED_TOTAL_LENGTH} characters in total`,
    );
  }
  return issues;
}

// What a deployment accepts (docs/design/authority-import-contract.md, D7), and, for an
// authority import, the proof that the gate recomputed this very submission from the
// authority's bytes (D1): the submission's hash, set only by src/authority/gate.ts.
export type GateOptions = {
  allowSyntheticSources: boolean;
  recomputedImport?: { submissionSha256: string } | undefined;
};

// The marker every synthetic narrative carries (test/synthetic-only.test.ts).
export const SYNTHETIC_MARKER = "not for clinical use";

// Synthetic content is refused unless the deployment accepts it; where it does, a synthetic
// submission carries every mark of one and any other none. No drawn-document extractor is
// qualified (fidelity §7), so a drawn submission must be synthetic.
function syntheticIssues(
  submission: CanonicalSubmission,
  narratives: NarrativeSection[],
  options: GateOptions,
): string[] {
  const { sourceDocument: source, extraction } = submission.provenance;
  const identifier = submission.bundle.identifier.value;
  const marked = narratives.filter(({ div }) => {
    try {
      return xhtmlToText(div).includes(SYNTHETIC_MARKER);
    } catch {
      return false;
    }
  }).length;
  const terminology = extraction.terminologyService?.name;
  const marks = {
    extractor: extraction.parser.name.startsWith(SYNTHETIC_PREFIX),
    terminology: terminology?.startsWith(SYNTHETIC_PREFIX) === true,
    identifier:
      identifier.startsWith(SYNTHETIC_PREFIX) ||
      identifier.startsWith(`${AUTHORITY_IMPORT_PREFIX}synthetic:`),
    authority: source.kind === "authority-publication" && source.authority === "synthetic",
    narrative: marked > 0,
  };
  const synthetic = source.kind === "drawn" ? marks.extractor : marks.authority;

  if (!options.allowSyntheticSources) {
    if (source.kind === "drawn") {
      return ["No drawn-document extractor is qualified (fidelity §7)"];
    }
    return Object.values(marks).some(Boolean)
      ? ["Synthetic content where the deployment accepts none"]
      : [];
  }
  if (source.kind === "drawn" && !synthetic) {
    return ["No drawn-document extractor is qualified (fidelity §7)"];
  }
  const issues: string[] = [];
  if (synthetic) {
    // A drawn synthetic submission names a synthetic terminology service; an import's is the
    // mapping manifest, for synthetic and real publications alike (D7's table).
    if (source.kind === "drawn" && terminology !== undefined && !marks.terminology) {
      issues.push("A synthetic submission's terminology service is synthetic");
    }
    if (!marks.identifier) issues.push("A synthetic submission's Bundle identifier is synthetic");
    if (marked !== narratives.length) {
      issues.push("Every narrative of a synthetic submission carries the synthetic marker");
    }
  } else if (Object.values(marks).some(Boolean)) {
    issues.push("A non-synthetic submission carries a synthetic mark");
  }
  return issues;
}

// A structured source has one page per section of the authority's document, each wholly body
// (fidelity §7; docs/design/authority-import-contract.md, D4).
function structuredPageIssues(
  source: CanonicalSubmission["provenance"]["sourceDocument"],
  text: z.infer<typeof SourceDocumentTextSchema>,
): string[] {
  if (source.kind !== "authority-publication") return [];
  const issues: string[] = [];
  if (text.pages.length !== source.sectionPages.length) {
    issues.push("A structured source has one page per section");
  }
  for (const page of text.pages) {
    if (page.bodyStart !== 0 || page.bodyEnd !== Array.from(page.text).length) {
      issues.push(`Page ${page.page} of a structured source is not wholly body`);
    }
  }
  return issues;
}

// Zone B ingress gate for `source: "document"` runs (ADR 0002 invariants, ADR 0003 gate).
// Throws `SubmissionRejectedError` with reason strings that never contain narrative text.
export function verifyDocumentSubmission(
  input: DocumentSubmissionInput,
  sourceCodeSystem: string,
  options: GateOptions,
): DocumentGateResult {
  const structural = documentShapeIssues(input);
  if (structural.length > 0) {
    throw new SubmissionRejectedError("Document submission rejected", structural);
  }
  const parsed = CanonicalSubmissionSchema.safeParse(input.submission);
  if (!parsed.success) {
    throw new SubmissionRejectedError("Canonical submission is invalid", zodIssues(parsed.error));
  }
  const parsedReport = FidelityReportSchema.safeParse(input.fidelityReport);
  if (!parsedReport.success) {
    throw new SubmissionRejectedError(
      "Fidelity report is invalid",
      zodIssues(parsedReport.error).map((issue) => `fidelityReport.${issue}`),
    );
  }
  const lossy = [
    ...losslessParseIssues("submission", input.submission, parsed.data),
    ...losslessParseIssues("fidelityReport", input.fidelityReport, parsedReport.data),
  ];
  if (lossy.length > 0) throw new SubmissionRejectedError("Document submission rejected", lossy);
  const submission = parsed.data;
  const report: FidelityReport = parsedReport.data;
  const issues: string[] = [];
  const declared = submission.provenance.fidelity;
  const extractedText = submission.provenance.sourceDocument.extractedText;

  if (report.normalizationVersion !== NORMALIZATION_VERSION) {
    issues.push(`Fidelity report normalization version must be ${NORMALIZATION_VERSION}`);
  }
  if (!verifyReportHash(report)) issues.push("Fidelity report hash does not recompute");
  if (report.status !== "passed") issues.push("Fidelity report status must be passed");
  if (report.reportHash !== declared.reportSha256) {
    issues.push("Fidelity report hash does not match provenance.fidelity.reportSha256");
  }
  if (report.narrativeBindingSha256 !== declared.narrativeBindingSha256) {
    issues.push(
      "Fidelity report binding does not match provenance.fidelity.narrativeBindingSha256",
    );
  }
  if (report.extractedTextSha256 !== extractedText.sha256) {
    issues.push("Fidelity report was not computed over sourceDocument.extractedText");
  }

  const composition = LooseCompositionSchema.safeParse(submission.bundle.entry[0]?.resource);
  if (!composition.success) {
    throw new SubmissionRejectedError("Canonical submission is invalid", [
      ...issues,
      "Bundle.entry[0].resource must be a Composition with well-formed sections",
    ]);
  }
  const narrativeSections = collectNarrativeSections(composition.data, sourceCodeSystem);
  const provenanceByKey = new Map(
    submission.provenance.sections.map((section) => [section.sourceKey, section]),
  );
  const sectionKeys = new Set(narrativeSections.map(({ sourceKey }) => sourceKey));
  if (sectionKeys.size !== narrativeSections.length)
    issues.push("Bundle narrative sections are ambiguous");
  for (const section of narrativeSections) {
    const entry = provenanceByKey.get(section.sourceKey);
    if (entry === undefined) {
      issues.push(`Missing provenance for section ${section.sourceKey}`);
      continue;
    }
    if (sha256Utf8(section.div) !== entry.narrativeDivSha256) {
      issues.push(`narrativeDivSha256 does not match section ${section.sourceKey}`);
    }
  }
  for (const key of provenanceByKey.keys()) {
    if (!sectionKeys.has(key)) issues.push(`Orphan provenance for section ${key}`);
  }

  const binding = computeNarrativeBinding(narrativeSections);
  if (binding.sha256 !== report.narrativeBindingSha256) {
    issues.push("Bundle narratives do not match the fidelity report binding");
  }
  for (const entry of binding.bindings) {
    const declaredSection = provenanceByKey.get(entry.sourceKey);
    if (
      declaredSection !== undefined &&
      declaredSection.normalizedTextSha256 !== entry.normalizedTextSha256
    ) {
      issues.push(`normalizedTextSha256 does not match section ${entry.sourceKey}`);
    }
  }

  issues.push(...syntheticIssues(submission, narrativeSections, options));
  const identifier = submission.bundle.identifier.value;
  const source = submission.provenance.sourceDocument;
  if (source.kind === "drawn" && identifier.startsWith(AUTHORITY_IMPORT_PREFIX)) {
    issues.push("The authority-import namespace is written only by the importer");
  }
  if (source.kind === "authority-publication") {
    const segment = source.authority === "EMA" ? "ema" : "synthetic";
    if (identifier !== `${AUTHORITY_IMPORT_PREFIX}${segment}:${source.document.id}`) {
      issues.push("An authority import's Bundle identifier is its authority-import value");
    }
    // Zone B recomputes an import from the authority's bytes before trusting any of it (D1).
    if (options.recomputedImport?.submissionSha256 !== sha256(submission)) {
      issues.push("An authority import is accepted only as the gate recomputed it");
    }
  }

  issues.push(
    ...unverifiedTextIssues(
      submission.bundle,
      new Set(narrativeSections.map(({ path }) => narrativeDivPath(path))),
    ),
  );

  const sourceText = SourceDocumentTextSchema.safeParse(input.sourceText);
  const sourceTextLossy = sourceText.success
    ? losslessParseIssues("sourceText", input.sourceText, sourceText.data)
    : [];
  if (!sourceText.success) {
    issues.push(...zodIssues(sourceText.error).map((issue) => `sourceText.${issue}`));
  } else if (sourceTextLossy.length > 0) {
    issues.push(...sourceTextLossy);
  } else if (sha256(sourceText.data) !== extractedText.sha256) {
    issues.push("Extracted source text does not match sourceDocument.extractedText.sha256");
  } else if (sourceText.data.extractorVersion !== extractedText.extractorVersion) {
    issues.push("Extracted source text was written by another extractor");
  } else {
    issues.push(...structuredPageIssues(source, sourceText.data));
    // Every rejection must surface as a classified contract rejection, so structural failures of
    // the re-execution are folded into the issue list rather than escaping as another error type.
    try {
      const fresh = verifyNarrativeFidelity({
        normalizationVersion: NORMALIZATION_VERSION,
        source: sourceText.data,
        sections: narrativeSections,
        provenance: submission.provenance.sections,
      });
      if (fresh.reportHash !== report.reportHash) {
        issues.push("Re-executed fidelity check does not reproduce the declared report");
      }
      // A structured source's page without a span must draw nothing (fidelity §7, ADR 0005).
      if (source.kind === "authority-publication" && fresh.coverage.uncoveredGaps !== 0) {
        issues.push("A page of the authority's document that no narrative covers is not blank");
      }
    } catch (error) {
      if (error instanceof FidelityError) {
        issues.push(...error.issues.map((issue) => `Fidelity re-execution: ${issue}`));
      } else {
        issues.push(
          `Fidelity re-execution failed: ${error instanceof Error ? error.name : "Error"}`,
        );
      }
    }
  }

  if (issues.length > 0) throw new SubmissionRejectedError("Document submission rejected", issues);

  return {
    submission,
    // The Zod schema validated the wire shape; the FHIR types are the repository's structural
    // view of the same JSON, so this is a boundary cast rather than a conversion.
    bundle: submission.bundle as unknown as FhirBundle,
    narrativeSections,
    report,
  };
}
