import {
  CanonicalSubmissionSchema,
  SubmissionRejectedError,
  verifyDocumentSubmission,
  type DocumentGateResult,
  type DocumentSubmissionInput,
  type GateOptions,
} from "../contracts/index.js";
import type { EmaMapping } from "../fhir/mapping.js";
import { sha256 } from "../lib/hash.js";
import { AuthorityFetchError, type AuthorityFetcher, type Fetched } from "./fetch.js";
import { IMPORTER_VERSION, ImportRefusedError, importPublication, sha256Bytes } from "./import.js";

// Zone B's gate for an authority import (docs/design/authority-import-contract.md, D1): fetch
// the authority's files itself, require the bytes the submission pinned, run the importer on
// them with the run's own identifiers, and require the very submission, page text and report it
// was sent. Only then does the ordinary gate run, told which submission it recomputed.

export type AuthorityGateResult = {
  gate: DocumentGateResult;
  importerVersion: string;
  fetched: Fetched[];
};

function rejected(issue: string): never {
  throw new SubmissionRejectedError("Document submission rejected", [issue]);
}

export async function verifyAuthorityImport(
  input: DocumentSubmissionInput,
  mapping: EmaMapping,
  options: GateOptions & { dryRun: boolean },
  fetcher: AuthorityFetcher,
): Promise<AuthorityGateResult> {
  const parsed = CanonicalSubmissionSchema.safeParse(input.submission);
  // A submission that does not parse is refused by the ordinary gate, with its own reasons.
  if (!parsed.success) {
    verifyDocumentSubmission(input, mapping.sourceCodeSystem, options);
    return rejected("Canonical submission is invalid");
  }
  const submission = parsed.data;
  const source = submission.provenance.sourceDocument;
  const { approval } = submission;
  if (source.kind !== "authority-publication" || approval.method !== "authority-publication") {
    return rejected("Not an authority import");
  }
  // Nothing an import makes is persisted before PR 5 adds its pilot status to answers (D1).
  if (!options.dryRun) {
    return rejected("An authority import runs only as a dry run until it can be published");
  }
  const reportUri = submission.provenance.fidelity.reportUri;
  if (reportUri === undefined) return rejected("An authority import names its report's location");

  const fetched: Fetched[] = [];
  for (const [file, pin] of [
    [{ kind: "document", id: source.document.id }, source.document],
    [{ kind: "index", id: source.index.id }, source.index],
  ] as const) {
    let result: Fetched;
    try {
      result = await fetcher.fetch(source.authority, file);
    } catch (error) {
      if (error instanceof AuthorityFetchError) {
        return rejected(`The authority's ${file.kind} could not be fetched: ${error.reason}`);
      }
      throw error;
    }
    if (sha256Bytes(result.bytes) !== pin.sha256 || result.bytes.length !== pin.byteLength) {
      return rejected(`The authority now serves another ${file.kind} than the one pinned`);
    }
    fetched.push(result);
  }
  const [document, index] = fetched;
  if (document === undefined || index === undefined) return rejected("Nothing was fetched");
  if (Date.parse(approval.requestedAt) > Date.parse(document.fetchedAt)) {
    return rejected("The import was requested after the gate fetched the authority's files");
  }

  let recomputed: ReturnType<typeof importPublication>;
  try {
    recomputed = importPublication(
      source.request,
      { document: document.bytes, index: index.bytes },
      mapping,
      {
        submissionId: submission.submissionId,
        createdAt: submission.createdAt,
        extractionRunId: submission.provenance.extraction.extractionRunId,
        serviceVersion: submission.provenance.extraction.serviceVersion,
        requestedBy: approval.requestedBy,
        requestedAt: approval.requestedAt,
        sourceTextUri: source.extractedText.uri,
        fidelityReportUri: reportUri,
      },
    );
  } catch (error) {
    if (error instanceof ImportRefusedError) {
      return rejected(
        `The import refuses the authority's files at ${error.stage}: ${error.reason}`,
      );
    }
    throw error;
  }
  const submissionSha256 = sha256(submission);
  if (sha256(recomputed.submission) !== submissionSha256) {
    return rejected("The submission is not what the importer makes of the authority's files");
  }
  if (sha256(recomputed.sourceText) !== sha256(input.sourceText)) {
    return rejected("The page text is not what the importer makes of the authority's files");
  }
  if (sha256(recomputed.fidelityReport) !== sha256(input.fidelityReport)) {
    return rejected("The fidelity report is not the recomputed one");
  }
  const gate = verifyDocumentSubmission(input, mapping.sourceCodeSystem, {
    allowSyntheticSources: options.allowSyntheticSources,
    recomputedImport: { submissionSha256 },
  });
  return { gate, importerVersion: IMPORTER_VERSION, fetched };
}

// Re-verification later, from Zone B's own copies of what it fetched and never the network
// (D1, "Keep"): the same gate, served the copies the run's evidence holds. `verify-import` runs
// it; an auditor learns whether the recorded run would pass again.
export function copiesFetcher(copies: { document: Fetched; index: Fetched }): AuthorityFetcher {
  return {
    fetch: (_authority, file) =>
      Promise.resolve(file.kind === "document" ? copies.document : copies.index),
  };
}
