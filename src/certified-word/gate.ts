import {
  CanonicalSubmissionSchema,
  SubmissionRejectedError,
  documentShapeIssues,
  losslessParseIssues,
  verifyDocumentSubmission,
  type DocumentGateResult,
  type DocumentSubmissionInput,
  type GateOptions,
} from "../contracts/index.js";
import { sha256 } from "../lib/hash.js";

// Zone B's gate for a certified Word source (docs/design/certified-word-import.md, D2). Zone B
// does not yet make the sections again from the uploaded bytes: the worker cannot run the Python
// recompute (PR C). Until it can, a certified Word submission passes only a dry run, which
// persists nothing, and is refused, with this closed code, when DRY_RUN is false (ADR 0002
// invariant 11, as ADR 0006 amends it). What the gate will compare once it can is the design
// note's "The gate, once it recomputes".
export const CERTIFIED_WORD_NOT_RECOMPUTED =
  "certified-word-not-recomputed: a certified Word source runs only as a dry run until Zone B recomputes it";

function rejected(issue: string): never {
  throw new SubmissionRejectedError("Document submission rejected", [issue]);
}

export function verifyCertifiedWordImport(
  input: DocumentSubmissionInput,
  sourceCodeSystem: string,
  options: Omit<GateOptions, "certifiedWordDryRun"> & { dryRun: boolean },
): DocumentGateResult {
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
    verifyDocumentSubmission(input, sourceCodeSystem, options);
    return rejected("Canonical submission is invalid");
  }
  if (parsed.data.provenance.sourceDocument.kind !== "certified-word") {
    return rejected("Not a certified Word import");
  }
  if (!options.dryRun) return rejected(CERTIFIED_WORD_NOT_RECOMPUTED);
  return verifyDocumentSubmission(input, sourceCodeSystem, {
    allowSyntheticSources: options.allowSyntheticSources,
    certifiedWordDryRun: { submissionSha256: sha256(parsed.data) },
  });
}
