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
import { sha256, sha256Utf8 } from "../lib/hash.js";
import { jsonShapeIssues } from "../lib/json-shape.js";
import { IsoDateTime, Sha256Hex, Uuid } from "./common.js";
import { FidelityReportSchema, SourceDocumentTextSchema } from "./fidelity-report.js";
import { ApprovalSchema, IngestionProvenanceSchema } from "./ingestion-provenance.js";
import { LooseCompositionSchema, Type2BundleSchema, type Type2Bundle } from "./type2-bundle.js";

export const CANONICAL_SUBMISSION_VERSION = "1.0.0";

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
  bundle: Type2BundleSchema,
  bundleSha256: Sha256Hex,
  provenance: IngestionProvenanceSchema,
  approval: ApprovalSchema,
});

export type CanonicalSubmission = z.infer<typeof CanonicalSubmissionBase>;

// The content an approver attests to. The approval block itself is excluded, and the schema
// version is included so a re-approval is forced whenever the contract changes.
export function approvedContent(
  submission: Pick<CanonicalSubmission, "schemaVersion" | "bundle" | "provenance">,
): { schemaVersion: string; bundle: Type2Bundle; provenance: CanonicalSubmission["provenance"] } {
  return {
    schemaVersion: submission.schemaVersion,
    bundle: submission.bundle,
    provenance: submission.provenance,
  };
}

// Invariants that need nothing but the submission itself. They are Zone B ingress rules and are
// deliberately not expressible in JSON Schema (ADR 0002).
export function structuralInvariantIssues(submission: CanonicalSubmission): string[] {
  const issues: string[] = [];
  const { provenance, approval } = submission;

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
        walk(child, path.length === 0 ? key : `${path}.${key}`);
      }
    }
  };
  walk(bundle, "");
  if (strings > MAX_UNVERIFIED_STRINGS) {
    issues.push(`Bundle carries more than ${MAX_UNVERIFIED_STRINGS} unverified strings`);
  }
  if (totalLength > MAX_UNVERIFIED_TOTAL_LENGTH) {
    issues.push(`Unverified strings exceed ${MAX_UNVERIFIED_TOTAL_LENGTH} characters in total`);
  }
  return issues;
}

// Zone B ingress gate for `source: "document"` runs (ADR 0002 invariants, ADR 0003 gate).
// Throws `SubmissionRejectedError` with reason strings that never contain narrative text.
export function verifyDocumentSubmission(
  input: DocumentSubmissionInput,
  sourceCodeSystem: string,
): DocumentGateResult {
  const structural = [
    ...jsonShapeIssues("submission", input.submission),
    ...jsonShapeIssues("fidelityReport", input.fidelityReport),
    ...jsonShapeIssues("sourceText", input.sourceText),
  ];
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

  issues.push(
    ...unverifiedTextIssues(
      submission.bundle,
      new Set(narrativeSections.map(({ path }) => narrativeDivPath(path))),
    ),
  );

  const sourceText = SourceDocumentTextSchema.safeParse(input.sourceText);
  if (!sourceText.success) {
    issues.push(...zodIssues(sourceText.error).map((issue) => `sourceText.${issue}`));
  } else if (sha256(sourceText.data) !== extractedText.sha256) {
    issues.push("Extracted source text does not match sourceDocument.extractedText.sha256");
  } else {
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
