import { Storage } from "@google-cloud/storage";
import { z } from "zod";

import type { AppConfig } from "../config.js";
import { Sha256Hex, StorageUri } from "../contracts/common.js";
import type { DocumentSubmissionInput, SubmissionRef } from "../contracts/index.js";
import { sha256 } from "../lib/hash.js";
import { jsonShapeIssues } from "../lib/json-shape.js";
import { log } from "../lib/logger.js";

// By-reference transport for Zone A hand-offs (ADR 0002 decision 5): a run request carries only
// a hash-pinned pointer, because a real submission is multi-megabyte and an inline body would
// transit Workflows and Cloud Logging. This module resolves that pointer and nothing more — it
// proves that the bytes it fetched are the bytes the caller pinned, and leaves every contract
// invariant to `verifyDocumentSubmission`.
//
// Trust model: the caller (Workflows, authenticated by IAM) chooses both the URI and the hash,
// so this layer cannot decide whether a submission is legitimate. Its controls are narrow and
// deliberate: reads are confined to one configured bucket, sizes are capped, and every object is
// hash-checked. Legitimacy is the ingress gate's job.

export type SubmissionPart = "submission" | "fidelity-report" | "source-text";

export type SubmissionReadReason =
  | "uri-not-allowed"
  | "object-not-found"
  | "object-too-large"
  | "invalid-json"
  | "malformed-json"
  | "hash-mismatch"
  | "missing-reference";

// Both fields are closed enumerations, so this error can be logged and returned over HTTP
// without any risk of carrying document content.
export class SubmissionReadError extends Error {
  public constructor(
    public readonly reason: SubmissionReadReason,
    public readonly part: SubmissionPart,
  ) {
    super(`Submission reference could not be read (${reason}: ${part})`);
    this.name = "SubmissionReadError";
  }
}

// The submission declares where its own fidelity report and extracted text live, each with the
// hash this reader must reproduce. Only those pointers are read here; the full shape is the
// contract's business.
const SubmissionReferencesSchema = z.looseObject({
  provenance: z.looseObject({
    sourceDocument: z.looseObject({
      extractedText: z.looseObject({ uri: StorageUri, sha256: Sha256Hex }),
    }),
    fidelity: z.looseObject({ reportUri: StorageUri, reportSha256: Sha256Hex }),
  }),
});

export type SubmissionReader = {
  read(ref: SubmissionRef, runId: string): Promise<DocumentSubmissionInput>;
};

// Returns the object's bytes, or undefined when it does not exist. Implementations must never
// return more than `maxBytes + 1` bytes, so an oversized object is detected without being
// downloaded in full.
export type GcsObjectFetcher = (
  bucket: string,
  object: string,
  maxBytes: number,
) => Promise<Buffer | undefined>;

function parseStorageUri(uri: string): { bucket: string; object: string } | undefined {
  if (!StorageUri.safeParse(uri).success) return undefined;
  const rest = uri.slice("gs://".length);
  const slash = rest.indexOf("/");
  if (slash === -1) return undefined;
  const bucket = rest.slice(0, slash);
  const object = rest.slice(slash + 1);
  return bucket.length === 0 || object.length === 0 ? undefined : { bucket, object };
}

type Fetched = { value: unknown; byteLength: number };

function requireHash(actual: string | undefined, expected: string, part: SubmissionPart): void {
  if (actual !== expected) throw new SubmissionReadError("hash-mismatch", part);
}

function declaredReportHash(report: unknown): string | undefined {
  if (report === null || typeof report !== "object") return undefined;
  const value = (report as { reportHash?: unknown }).reportHash;
  return typeof value === "string" ? value : undefined;
}

function isNotFound(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { code?: unknown }).code === 404;
}

function storageFetcher(projectId: string): GcsObjectFetcher {
  const storage = new Storage({ projectId });
  return async (bucket, object, maxBytes) => {
    try {
      // One ranged read of maxBytes + 1 bytes: an oversized object is detected from the result
      // alone, with no separate metadata call for a swap to race against.
      const [contents] = await storage
        .bucket(bucket)
        .file(object)
        .download({ start: 0, end: maxBytes });
      return contents;
    } catch (error) {
      if (isNotFound(error)) return undefined;
      throw error;
    }
  };
}

export class GcsSubmissionReader implements SubmissionReader {
  readonly #bucket: string;
  readonly #maxBytes: number;
  readonly #fetch: GcsObjectFetcher;

  public constructor(config: AppConfig, fetcher?: GcsObjectFetcher) {
    const bucket = config.SUBMISSION_BUCKET;
    if (bucket === undefined) throw new Error("SUBMISSION_BUCKET is required");
    const projectId = config.GOOGLE_CLOUD_PROJECT;
    if (fetcher === undefined && projectId === undefined) {
      throw new Error("GOOGLE_CLOUD_PROJECT is required");
    }
    this.#bucket = bucket;
    this.#maxBytes = config.SUBMISSION_MAX_BYTES;
    this.#fetch = fetcher ?? storageFetcher(projectId ?? "");
  }

  async #fetchJson(uri: string, part: SubmissionPart): Promise<Fetched> {
    const location = parseStorageUri(uri);
    // Zone A writes to exactly one bucket. Without this the worker's read permissions would be
    // available to whoever can name a URI, and a submission could cite objects from anywhere.
    if (location?.bucket !== this.#bucket) {
      throw new SubmissionReadError("uri-not-allowed", part);
    }

    const bytes = await this.#fetch(location.bucket, location.object, this.#maxBytes);
    if (bytes === undefined) throw new SubmissionReadError("object-not-found", part);
    if (bytes.length > this.#maxBytes) throw new SubmissionReadError("object-too-large", part);

    let value: unknown;
    try {
      value = JSON.parse(bytes.toString("utf8"));
    } catch {
      throw new SubmissionReadError("invalid-json", part);
    }
    // Canonical hashing recurses, so a pathological document is refused before it is hashed.
    if (jsonShapeIssues(part, value).length > 0) {
      throw new SubmissionReadError("malformed-json", part);
    }
    return { value, byteLength: bytes.length };
  }

  public async read(ref: SubmissionRef, runId: string): Promise<DocumentSubmissionInput> {
    const submission = await this.#fetchJson(ref.uri, "submission");
    // Pinned hashes cover the JSON value, not the stored bytes, exactly as every other hash in
    // this system: re-serialising an object must not invalidate it, while any change to its
    // content must.
    requireHash(sha256(submission.value), ref.sha256, "submission");

    const references = SubmissionReferencesSchema.safeParse(submission.value);
    if (!references.success) throw new SubmissionReadError("missing-reference", "submission");
    const { fidelity, sourceDocument } = references.data.provenance;

    const fidelityReport = await this.#fetchJson(fidelity.reportUri, "fidelity-report");
    // A fidelity report certifies itself: `reportHash` is a digest of its own contents, and the
    // submission pins that value. Transport only has to prove this is the report the submission
    // names; recomputing the digest from the contents is the ingress gate's job, and it does it
    // whether or not the report arrived by reference.
    requireHash(declaredReportHash(fidelityReport.value), fidelity.reportSha256, "fidelity-report");

    const sourceText = await this.#fetchJson(sourceDocument.extractedText.uri, "source-text");
    requireHash(sha256(sourceText.value), sourceDocument.extractedText.sha256, "source-text");

    log("info", "Canonical submission resolved by reference", {
      runId,
      stage: "submission-read",
      submissionBytes: submission.byteLength,
      reportBytes: fidelityReport.byteLength,
      sourceTextBytes: sourceText.byteLength,
    });

    return {
      submission: submission.value,
      fidelityReport: fidelityReport.value,
      sourceText: sourceText.value,
    };
  }
}
