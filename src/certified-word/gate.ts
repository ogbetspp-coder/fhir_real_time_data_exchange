import { sha256Bytes } from "../authority/import.js";
import {
  CERTIFIED_WORD_PREFIX,
  CanonicalSubmissionSchema,
  SubmissionRejectedError,
  documentShapeIssues,
  losslessParseIssues,
  verifyDocumentSubmission,
  type CanonicalSubmission,
  type CertifiedWordSourceDocument,
  type DocumentGateResult,
  type DocumentSubmissionInput,
  type GateOptions,
  type SubmissionRefusal,
} from "../contracts/index.js";
import type { EmaMapping } from "../fhir/mapping.js";
import { EU_AUTHORISATION_NUMBER_SYSTEM } from "../fhir/standards.js";
import { sha256 } from "../lib/hash.js";
import { drawingVerdict } from "./drawing.js";
import {
  CANONICAL_ORGANIZATION_SYSTEM,
  CANONICAL_PRODUCT_SYSTEM,
  CERTIFIED_WORD_IMPORTER,
  CertifiedWordRefusedError,
  importCertifiedWord,
} from "./import.js";
import {
  RecomputeFailedError,
  UploadRefusedError,
  type CertifiedWordSources,
} from "./recompute.js";

// Zone B's gate for a certified Word source (docs/design/certified-word-import.md, "The gate"):
// read the uploaded .docx itself and require the bytes the source pins (D4), run
// `python -m zone_a.recompute` on them with the source's request (D2), make the importer's request
// again from the submission, run the importer this build contains on the recompute's result, and
// require the very submission, page text and fidelity report it was sent, as the authority gate
// does; then find and verify the signed drawing record of the .docx and request (D3, step 5:
// docs/design/certified-word-drawing.md, ./drawing.ts). Until P5 binds the ePI's document id, a
// submission that passes all of that is still refused unless the run is a dry run, which persists
// nothing (ADR 0002 invariant 11, as ADR 0006 amends it). Where the worker cannot recompute (no
// bucket or no Python configured), a dry run checks only what the submission holds, and any other
// run is refused.

export const CERTIFIED_WORD_NOT_RECOMPUTED =
  "certified-word-not-recomputed: this worker cannot recompute a certified Word source, so it runs only as a dry run";
export const CERTIFIED_WORD_DRAWING_MISSING =
  "certified-word-drawing-missing: the sections were recomputed, but no drawing record (D3) is stored for them";
export const CERTIFIED_WORD_DRAWING_INVALID =
  "certified-word-drawing-invalid: the object at the drawing record's path is not a record this build verifies";
export const CERTIFIED_WORD_DRAWING_MISMATCH =
  "certified-word-drawing-mismatch: the signed drawing record is not this submission's";
export const CERTIFIED_WORD_DOCUMENT_UNBOUND =
  "certified-word-document-unbound: the drawing is verified, but the ePI's document id is bound to no product until P5";

// Whether the gate made the sections again from the upload (D2, D4) and compared, and found their
// signed drawing record (D3), or, where the worker cannot recompute, checked only what a dry run's
// submission holds. The run's answer and its log carry it, so a dry run's `validated` says which it
// proves.
export type CertifiedWordCheck = "drawn" | "recomputed" | "submission-only";

function rejected(issue: string, reason?: SubmissionRefusal): never {
  throw new SubmissionRejectedError("Document submission rejected", [issue], reason);
}

type Resource = { resourceType?: unknown; identifier?: unknown; name?: unknown };

function identifierValue(resource: Resource | undefined, system: string): unknown {
  return Array.isArray(resource?.identifier)
    ? (resource.identifier as ({ system?: unknown; value?: unknown } | null)[]).find(
        (identifier) => identifier?.system === system,
      )?.value
    : undefined;
}

// Step 3: the importer's request, made again from the submission: the upload and the recompute's
// request from the source, the document id from the Bundle's identifier, the product from the
// record and the approval's own fields. What is not there, or is otherwise than the importer wrote
// it, refuses at the importer's request or makes another submission than this one: either refuses.
function requestOf(submission: CanonicalSubmission, source: CertifiedWordSourceDocument): unknown {
  const resources = submission.bundle.entry.map(({ resource }) => resource as Resource);
  const [product] = resources.filter((r) => r.resourceType === "MedicinalProductDefinition");
  const [holder] = resources.filter((r) => r.resourceType === "Organization");
  // An attestation: the parse holds a certified Word source's approval to one (invariant 7).
  const approval = submission.approval as Exclude<
    CanonicalSubmission["approval"],
    { method: "authority-publication" }
  >;
  return {
    upload: { filename: source.document.filename, storageUri: source.document.storageUri },
    recompute: source.recompute,
    documentId: submission.bundle.identifier.value.slice(CERTIFIED_WORD_PREFIX.length),
    product: {
      id: identifierValue(product, CANONICAL_PRODUCT_SYSTEM),
      name: (product?.name as { productName?: unknown }[] | undefined)?.[0]?.productName,
      holder: { id: identifierValue(holder, CANONICAL_ORGANIZATION_SYSTEM), name: holder?.name },
      euAuthorisationNumbers: resources
        .filter((r) => r.resourceType === "RegulatedAuthorization")
        .map((r) => identifierValue(r, EU_AUTHORISATION_NUMBER_SYSTEM)),
    },
    approval: {
      approverId: approval.approverId,
      approverRole: approval.approverRole,
      approvedAt: approval.approvedAt,
      method: approval.method,
      ...(approval.recordRef === undefined ? {} : { recordRef: approval.recordRef }),
    },
  };
}

export async function verifyCertifiedWordImport(
  input: DocumentSubmissionInput,
  mapping: EmaMapping,
  options: Omit<GateOptions, "certifiedWordDryRun" | "recomputedImport"> & { dryRun: boolean },
  sources: CertifiedWordSources | undefined,
): Promise<{ gate: DocumentGateResult; check: CertifiedWordCheck }> {
  // The shape bound comes before the parse, whose refinement hashes the Bundle, as in the other
  // gates: a pathological document is a classified rejection, never a RangeError.
  const structural = documentShapeIssues(input);
  if (structural.length > 0) {
    throw new SubmissionRejectedError("Document submission rejected", structural);
  }
  const parsed = CanonicalSubmissionSchema.safeParse(input.submission);
  // A submission that does not parse, or parses to another value than it is, is refused by the
  // ordinary gate, with its own reasons.
  if (
    !parsed.success ||
    losslessParseIssues("submission", input.submission, parsed.data).length > 0
  ) {
    verifyDocumentSubmission(input, mapping.sourceCodeSystem, options);
    return rejected("Canonical submission is invalid");
  }
  const submission = parsed.data;
  const source = submission.provenance.sourceDocument;
  if (source.kind !== "certified-word") return rejected("Not a certified Word import");
  // The gate makes the record again with the importer it runs, so a submission another version
  // made is refused before anything is read, as the authority gate refuses one.
  if (source.importer !== CERTIFIED_WORD_IMPORTER) {
    return rejected("The submission was made by another importer version than the gate runs");
  }
  const dryRunProof = { certifiedWordDryRun: { submissionSha256: sha256(submission) } };
  if (sources === undefined) {
    // Its closed code reaches the HTTP caller (src/app.ts).
    if (!options.dryRun) rejected(CERTIFIED_WORD_NOT_RECOMPUTED, "certified-word-not-recomputed");
    const gate = verifyDocumentSubmission(input, mapping.sourceCodeSystem, {
      allowSyntheticSources: options.allowSyntheticSources,
      ...dryRunProof,
    });
    return { gate, check: "submission-only" };
  }
  const reportUri = submission.provenance.fidelity.reportUri;
  if (reportUri === undefined)
    return rejected("A certified Word import names its report's location");

  // Steps 1 to 4. The bytes the source pins, read under the worker's own identity (D4); the
  // recompute on them with the source's request (D2), whose refusal refuses the run, a request
  // naming versions this build does not have among them (`versions`); and the importer on its
  // result, with the request made again from the submission and the run's own fields, which must
  // make this very submission, page text and report.
  let made: ReturnType<typeof importCertifiedWord>;
  let outputSha256: string;
  try {
    const outcome = await sources.recompute(
      await sources.upload(source.document),
      source.recompute,
    );
    if ("refused" in outcome) {
      rejected(
        `The recompute refuses the uploaded document: ${outcome.refused}`,
        "certified-word-recompute-refused",
      );
    }
    outputSha256 = sha256Bytes(outcome.made);
    made = importCertifiedWord(outcome.made, requestOf(submission, source), mapping, {
      submissionId: submission.submissionId,
      createdAt: submission.createdAt,
      extractionRunId: submission.provenance.extraction.extractionRunId,
      serviceVersion: submission.provenance.extraction.serviceVersion,
      sourceTextUri: source.extractedText.uri,
      fidelityReportUri: reportUri,
    });
  } catch (error) {
    // A closed refusal of the upload, the recompute or the importer; anything else (a Storage
    // outage, a bug, the refusal above) is not dressed up as one.
    const issue =
      error instanceof UploadRefusedError
        ? `The uploaded document is refused: ${error.reason}`
        : error instanceof RecomputeFailedError
          ? `The recompute gave no answer: ${error.reason}`
          : error instanceof CertifiedWordRefusedError
            ? `The importer refuses the recompute's result at ${error.stage}: ${error.reason}`
            : undefined;
    if (issue === undefined) throw error;
    return rejected(issue);
  }
  if (sha256(made.submission) !== dryRunProof.certifiedWordDryRun.submissionSha256) {
    return rejected("The submission is not what the importer makes of the recomputed sections");
  }
  if (sha256(made.sourceText) !== sha256(input.sourceText)) {
    return rejected("The page text is not what the importer makes of the recomputed sections");
  }
  if (sha256(made.fidelityReport) !== sha256(input.fidelityReport)) {
    return rejected("The fidelity report is not the recomputed one");
  }
  // Step 5 (D3): the signed record that Chrome draws these narratives as the .docx was read. A
  // Storage error other than not-found is thrown, never read as `missing`.
  const drawing =
    sources.drawing === undefined
      ? "missing"
      : await drawingVerdict(sources.drawing, submission, source, outputSha256);
  if (drawing === "invalid") {
    rejected(CERTIFIED_WORD_DRAWING_INVALID, "certified-word-drawing-invalid");
  }
  if (drawing === "mismatch") {
    rejected(CERTIFIED_WORD_DRAWING_MISMATCH, "certified-word-drawing-mismatch");
  }
  // Until P5 binds the document id to its product, no run that is not a dry run passes.
  if (!options.dryRun) {
    if (drawing === "missing") {
      rejected(CERTIFIED_WORD_DRAWING_MISSING, "certified-word-drawing-missing");
    }
    rejected(CERTIFIED_WORD_DOCUMENT_UNBOUND, "certified-word-document-unbound");
  }
  const gate = verifyDocumentSubmission(input, mapping.sourceCodeSystem, {
    allowSyntheticSources: options.allowSyntheticSources,
    ...dryRunProof,
  });
  return { gate, check: drawing === "drawn" ? "drawn" : "recomputed" };
}
