import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { verifyDocumentSubmission, type CanonicalSubmission } from "../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import {
  SYNTHETIC_REPORT_URI,
  SYNTHETIC_SOURCE_TEXT_URI,
  SYNTHETIC_SUBMISSION_BUCKET,
  SYNTHETIC_SUBMISSION_URI,
  createSyntheticSubmission,
  type SyntheticSubmission,
} from "../src/fixtures/synthetic-submission.js";
import {
  GcsSubmissionReader,
  SubmissionReadError,
  submissionDownloadOptions,
  type GcsObjectFetcher,
  type SubmissionPart,
  type SubmissionReadReason,
} from "../src/gcp/submission-reader.js";
import { sha256 } from "../src/lib/hash.js";
import { SYNTHETIC } from "./support/submission.js";

let mapping: EmaMapping;
let fixture: SyntheticSubmission;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  fixture = createSyntheticSubmission(mapping);
});

type Objects = Map<string, Buffer>;

function objectKey(uri: string): string {
  return uri.slice("gs://".length);
}

// Stands in for Cloud Storage. Like a real ranged read it returns at most maxBytes + 1 bytes,
// which is how an oversized object is detected without downloading it.
function fetcherFor(objects: Objects): GcsObjectFetcher {
  return (bucket, object, maxBytes) => {
    const bytes = objects.get(`${bucket}/${object}`);
    return Promise.resolve(bytes === undefined ? undefined : bytes.subarray(0, maxBytes + 1));
  };
}

function serialise(value: unknown, indent = 2): Buffer {
  return Buffer.from(`${JSON.stringify(value, null, indent)}\n`, "utf8");
}

function storeFor(submission: unknown, indent = 2): Objects {
  return new Map([
    [objectKey(SYNTHETIC_SUBMISSION_URI), serialise(submission, indent)],
    [objectKey(SYNTHETIC_REPORT_URI), serialise(fixture.fidelityReport, indent)],
    [objectKey(SYNTHETIC_SOURCE_TEXT_URI), serialise(fixture.sourceText, indent)],
  ]);
}

function configFor(overrides: Record<string, string> = {}): AppConfig {
  return loadConfig({
    ALLOW_SYNTHETIC_SOURCES: "true",
    NODE_ENV: "test",
    DRY_RUN: "true",
    GOOGLE_CLOUD_PROJECT: "test-project",
    SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET,
    ...overrides,
  });
}

function readerFor(objects: Objects, overrides: Record<string, string> = {}): GcsSubmissionReader {
  return new GcsSubmissionReader(configFor(overrides), fetcherFor(objects));
}

const RUN_ID = "33333333-3333-4333-a333-333333333333";

async function failure(read: Promise<unknown>): Promise<SubmissionReadError> {
  const error: unknown = await read.then(
    () => undefined,
    (cause: unknown) => cause,
  );
  expect(error).toBeInstanceOf(SubmissionReadError);
  if (!(error instanceof SubmissionReadError)) throw new Error("expected a read failure");
  return error;
}

async function rejects(
  objects: Objects,
  ref: { uri: string; sha256: string },
  reason: SubmissionReadReason,
  part: SubmissionPart,
  overrides: Record<string, string> = {},
): Promise<void> {
  const error = await failure(readerFor(objects, overrides).read(ref, RUN_ID));
  expect({ reason: error.reason, part: error.part }).toEqual({ reason, part });
}

describe("by-reference submission reader", () => {
  it("resolves the three parts into an input the ingress gate accepts", async () => {
    const objects = storeFor(fixture.submission);
    const ref = { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) };

    const input = await readerFor(objects).read(ref, RUN_ID);

    expect(sha256(input.submission)).toBe(sha256(fixture.submission));
    expect(sha256(input.fidelityReport)).toBe(sha256(fixture.fidelityReport));
    expect(sha256(input.sourceText)).toBe(sha256(fixture.sourceText));
    expect(() =>
      verifyDocumentSubmission(input, mapping.sourceCodeSystem, SYNTHETIC),
    ).not.toThrow();
  });

  // The pinned hash covers the JSON value, so re-serialising an object must not invalidate it
  // while any change to its content must.
  it("accepts a byte-different re-serialisation of the same value", async () => {
    const ref = { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) };
    const compact = storeFor(fixture.submission, 0);

    const input = await readerFor(compact).read(ref, RUN_ID);

    expect(sha256(input.submission)).toBe(ref.sha256);
  });

  it("rejects a single altered character inside the stored submission", async () => {
    const altered = structuredClone(fixture.submission) as CanonicalSubmission & {
      bundle: { id?: string };
    };
    altered.bundle.id = `${altered.bundle.id ?? "x"}-tampered`;
    const objects = storeFor(altered);

    await rejects(
      objects,
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
      "hash-mismatch",
      "submission",
    );
  });

  // Zone A writes to one bucket. Any other URI — in the request or inside the submission —
  // would turn the worker's read permission into a general-purpose fetcher.
  it("rejects a request reference outside the configured bucket", async () => {
    const objects = storeFor(fixture.submission);
    objects.set("other-bucket/elsewhere.json", serialise(fixture.submission));

    await rejects(
      objects,
      { uri: "gs://other-bucket/elsewhere.json", sha256: sha256(fixture.submission) },
      "uri-not-allowed",
      "submission",
    );
  });

  it("rejects an in-submission reference outside the configured bucket", async () => {
    const redirected = structuredClone(fixture.submission);
    redirected.provenance.sourceDocument.extractedText.uri = "gs://other-bucket/pages.json";
    const objects = storeFor(redirected);
    objects.set("other-bucket/pages.json", serialise(fixture.sourceText));

    await rejects(
      objects,
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(redirected) },
      "uri-not-allowed",
      "source-text",
    );
  });

  it("rejects a submission that does not say where its fidelity report lives", async () => {
    const unreferenced = structuredClone(fixture.submission);
    delete unreferenced.provenance.fidelity.reportUri;
    const objects = storeFor(unreferenced);

    await rejects(
      objects,
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(unreferenced) },
      "missing-reference",
      "submission",
    );
  });

  it("reports a missing object rather than failing open", async () => {
    const objects = storeFor(fixture.submission);
    objects.delete(objectKey(SYNTHETIC_REPORT_URI));

    await rejects(
      objects,
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
      "object-not-found",
      "fidelity-report",
    );
  });

  it("refuses an object larger than the configured cap", async () => {
    const objects = storeFor(fixture.submission);

    await rejects(
      objects,
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
      "object-too-large",
      "submission",
      { SUBMISSION_MAX_BYTES: "1024" },
    );
  });

  // The cap is one budget for the three parts together: each part fits under it on its own, and
  // the last one to be read is refused for what the two before it used.
  it("caps the three parts together, not each part", async () => {
    const objects = storeFor(fixture.submission);
    const sizes = [...objects.values()].map(({ length }) => length);
    const largest = Math.max(...sizes);
    const total = sizes.reduce((sum, size) => sum + size, 0);
    const ref = { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) };
    expect(largest).toBeLessThan(total - 1);

    await rejects(objects, ref, "object-too-large", "source-text", {
      SUBMISSION_MAX_BYTES: String(Math.max(1_024, total - 1)),
    });
    await expect(
      readerFor(objects, { SUBMISSION_MAX_BYTES: String(Math.max(1_024, total)) }).read(
        ref,
        RUN_ID,
      ),
    ).resolves.toBeDefined();
  });

  it("reports unparseable bytes as a classified failure", async () => {
    const objects = storeFor(fixture.submission);
    objects.set(objectKey(SYNTHETIC_SOURCE_TEXT_URI), Buffer.from("{ not json", "utf8"));

    await rejects(
      objects,
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
      "invalid-json",
      "source-text",
    );
  });

  // Hashing canonical JSON is recursive, so a deep document must be refused before it is
  // hashed rather than escaping as a RangeError.
  it("refuses pathological nesting before hashing it", async () => {
    const objects = storeFor(fixture.submission);
    let nested = '"x"';
    for (let depth = 0; depth < 200; depth += 1) nested = `[${nested}]`;
    objects.set(objectKey(SYNTHETIC_REPORT_URI), Buffer.from(nested, "utf8"));

    await rejects(
      objects,
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
      "malformed-json",
      "fidelity-report",
    );
  });

  // The size cap is only a cap if the client is told not to decompress: a range request
  // suppresses server-side transcoding, so the client would otherwise gunzip a gzip-stored
  // object and buffer a result up to a thousand times the cap.
  it("asks Cloud Storage for a bounded range and refuses decompression", () => {
    expect(submissionDownloadOptions(64)).toEqual({ start: 0, end: 64, decompress: false });
  });

  it("requires a configured submission bucket", () => {
    expect(
      () =>
        new GcsSubmissionReader(
          loadConfig({
            ALLOW_SYNTHETIC_SOURCES: "true",
            NODE_ENV: "test",
            DRY_RUN: "true",
            GOOGLE_CLOUD_PROJECT: "p",
          }),
          fetcherFor(new Map()),
        ),
    ).toThrow("SUBMISSION_BUCKET is required");
  });

  // The reader proves the report is the one the submission names; proving the report is
  // internally consistent belongs to the gate, which recomputes it on every path.
  it("leaves report recomputation to the ingress gate", async () => {
    const forged = structuredClone(fixture.fidelityReport);
    forged.summary = { ...forged.summary, verified: forged.summary.verified + 1 };
    const objects = storeFor(fixture.submission);
    objects.set(objectKey(SYNTHETIC_REPORT_URI), serialise(forged));

    const input = await readerFor(objects).read(
      { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
      RUN_ID,
    );

    expect(() => verifyDocumentSubmission(input, mapping.sourceCodeSystem, SYNTHETIC)).toThrow(
      "Document submission rejected",
    );
  });

  it("keeps every failure free of document content", async () => {
    const objects = storeFor(fixture.submission);
    objects.set(objectKey(SYNTHETIC_REPORT_URI), serialise({ tampered: "Synthetic" }));

    const error = await failure(
      readerFor(objects).read(
        { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
        RUN_ID,
      ),
    );

    expect(error.message).not.toContain("Synthetic");
    expect(error.message).not.toContain(SYNTHETIC_REPORT_URI);
  });
});
