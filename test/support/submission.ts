import {
  approvedContent,
  type AttestedApproval,
  type CanonicalSubmission,
  type DrawnSourceDocument,
  type GateOptions,
} from "../../src/contracts/index.js";
import { sha256 } from "../../src/lib/hash.js";

// The synthetic fixtures are drawn documents with an attested approval; these narrow a
// submission to that shape so a test can read its fields, and fail loudly if it is not.

export function attested(submission: Pick<CanonicalSubmission, "approval">): AttestedApproval {
  const { approval } = submission;
  if (approval.method === "authority-publication") {
    throw new Error("expected an attested approval");
  }
  return approval;
}

export function drawn(submission: Pick<CanonicalSubmission, "provenance">): DrawnSourceDocument {
  const source = submission.provenance.sourceDocument;
  if (source.kind !== "drawn") throw new Error("expected a drawn source");
  return source;
}

// The deployment the synthetic fixtures run in: one that accepts synthetic sources.
export const SYNTHETIC: GateOptions = { allowSyntheticSources: true };

// Re-hashes a mutated submission so the change under test is the only invariant that fails.
export function seal(submission: CanonicalSubmission): CanonicalSubmission {
  submission.bundleSha256 = sha256(submission.bundle);
  submission.approval.approvedContentSha256 = sha256(approvedContent(submission));
  return submission;
}

// A synthetic authority's document and List ids, in the reserved block
// (docs/design/authority-import-contract.md, D7).
export const DOCUMENT_ID = "00000000-5979-4e74-8000-000000000001";
export const INDEX_ID = "00000000-5979-4e74-8000-000000000002";

// The synthetic fixture recast as a synthetic authority's publication: only the fields the
// rules read change, so each test sees the rule it names.
export function asImport(base: CanonicalSubmission): CanonicalSubmission {
  const submission = structuredClone(base);
  const { extractedText } = drawn(submission);
  submission.graphType = "type1";
  submission.bundle.identifier.value = `authority-import:synthetic:${DOCUMENT_ID}`;
  submission.provenance.extraction = {
    ...submission.provenance.extraction,
    parser: { name: "authority-import", version: "1.0.0" },
  };
  submission.provenance.sourceDocument = {
    kind: "authority-publication",
    mediaType: "application/fhir+json",
    authority: "synthetic",
    request: { authority: "synthetic", documentId: DOCUMENT_ID, indexId: INDEX_ID, language: "en" },
    document: { id: DOCUMENT_ID, sha256: sha256("document"), byteLength: 10 },
    index: {
      id: INDEX_ID,
      sha256: sha256("index"),
      byteLength: 10,
      epiId: "SYNTHETIC-EPI-1",
      versionNumber: "1",
      metaVersionId: "1",
      status: "current",
    },
    pictures: [],
    sectionPages: [{ page: 1, path: "Composition.section[0]", code: "100000155538" }],
    extractedText: { ...extractedText, extractorVersion: "authority-import/1.0.0" },
  };
  submission.approval = {
    method: "authority-publication",
    meaning: "authority-publication-imported",
    authority: "synthetic",
    authorityStatus: "pilot",
    publication: {
      epiId: "SYNTHETIC-EPI-1",
      documentId: DOCUMENT_ID,
      indexId: INDEX_ID,
      versionNumber: "1",
      procedureNumber: "SYNTHETIC-PROCEDURE-1",
      authorityTimestamp: "2026-09-24T12:00:00.000000+00:00",
    },
    requestedBy: "urn:requester:synthetic-01",
    requestedAt: "2026-09-24T12:00:00Z",
    approvedContentSha256: "",
  };
  return seal(submission);
}
