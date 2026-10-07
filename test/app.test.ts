import { beforeAll, describe, expect, it } from "vitest";

import { createApp } from "../src/app.js";
import { importCertifiedWord } from "../src/certified-word/import.js";
import {
  RUN as CERTIFIED_WORD_RUN,
  caseRequest,
  recomputed,
  recomputedCases,
} from "../src/certified-word/vectors.js";
import { loadConfig, type AppConfig } from "../src/config.js";
import type { DocumentSubmissionInput } from "../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import {
  SYNTHETIC_SUBMISSION_BUCKET,
  SYNTHETIC_SUBMISSION_URI,
  createSyntheticSubmission,
  type SyntheticSubmission,
} from "../src/fixtures/synthetic-submission.js";
import type { SubmissionRef } from "../src/contracts/index.js";
import { SubmissionReadError, type SubmissionReader } from "../src/gcp/submission-reader.js";
import { sha256 } from "../src/lib/hash.js";

let mapping: EmaMapping;
let fixture: SyntheticSubmission;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  fixture = createSyntheticSubmission(mapping);
});

const BASE_ENVIRONMENT = {
  NODE_ENV: "test",
  DRY_RUN: "true",
  GCP_LOCATION: "europe-west4",
  ALLOW_SYNTHETIC_SOURCES: "true",
};

function configFor(overrides: Record<string, string> = {}): AppConfig {
  return loadConfig({ ...BASE_ENVIRONMENT, ...overrides });
}

// The transport is exercised on its own in test/submission-reader.test.ts; here it only has to
// hand the route something, or fail the way the real one fails.
function readerReturning(input: () => DocumentSubmissionInput): SubmissionReader {
  return { read: () => Promise.resolve(input()) };
}

function readerFailing(error: SubmissionReadError): SubmissionReader {
  return { read: () => Promise.reject(error) };
}

type RunResponse = {
  status?: string;
  submissionId?: string;
  mappingDecisions?: number;
};

async function post(app: ReturnType<typeof createApp>, body: unknown): Promise<Response> {
  return await app.request("/v1/runs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

function documentRequest(): { source: "document"; submissionRef: SubmissionRef } {
  return {
    source: "document",
    submissionRef: { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
  };
}

describe("run API", () => {
  it("reports whether the document source is available", async () => {
    const without = await createApp({ config: configFor() }).request("/healthz");
    const with_ = await createApp({
      config: configFor(),
      submissionReader: readerReturning(() => fixture),
    }).request("/healthz");

    expect(await without.json()).toMatchObject({ status: "ok", documentSource: false });
    expect(await with_.json()).toMatchObject({ status: "ok", documentSource: true });
  });

  it("runs the fixture source", async () => {
    const response = await post(createApp({ config: configFor() }), { source: "fixture" });

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      status: "validated",
      mappingDecisions: 32,
      artifacts: [],
    });
  });

  it("runs an approved submission resolved by reference", async () => {
    const app = createApp({
      config: configFor({ SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET }),
      submissionReader: readerReturning(() => fixture),
    });

    const response = await post(app, documentRequest());
    const body = (await response.json()) as RunResponse;

    expect(response.status).toBe(200);
    expect(body.status).toBe("validated");
    expect(body.submissionId).toBe(fixture.submission.submissionId);
    expect(body.mappingDecisions).toBe(32);
  });

  it("answers plainly when no submission bucket is configured", async () => {
    const response = await post(createApp({ config: configFor() }), documentRequest());

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "document-source-not-configured" });
  });

  it("rejects a malformed body and a malformed reference", async () => {
    const app = createApp({
      config: configFor({ SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET }),
      submissionReader: readerReturning(() => fixture),
    });

    const notJson = await post(app, "{");
    const badUri = await post(app, {
      source: "document",
      submissionRef: { uri: "https://example.org/submission.json", sha256: "a".repeat(64) },
    });
    const badBundleId = await post(app, { source: "healthcare-api", bundleId: "../../secrets" });
    // Both are FHIR ids, and both resolve out of the store's Bundle path (run-request 2.0.0).
    const dotBundleIds = await Promise.all(
      [".", ".."].map((bundleId) => post(app, { source: "healthcare-api", bundleId })),
    );

    expect(notJson.status).toBe(400);
    expect(await notJson.json()).toEqual({ error: "invalid-json" });
    expect(badUri.status).toBe(400);
    expect(badBundleId.status).toBe(400);
    expect(dotBundleIds.map(({ status }) => status)).toEqual([400, 400]);
  });

  // A caller learns why the reference failed without learning anything about the document.
  it("maps an unreadable reference to 422 with a closed reason code", async () => {
    const app = createApp({
      config: configFor({ SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET }),
      submissionReader: readerFailing(new SubmissionReadError("hash-mismatch", "source-text")),
    });

    const response = await post(app, documentRequest());

    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({
      error: "submission-unreadable",
      reason: "hash-mismatch",
      part: "source-text",
    });
  });

  it("maps a rejected submission to 422 without echoing any detail", async () => {
    const app = createApp({
      config: configFor({ SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET }),
      submissionReader: readerReturning(() => {
        const tampered = structuredClone(fixture.submission);
        tampered.approval.approvedContentSha256 = "f".repeat(64);
        return { ...fixture, submission: tampered };
      }),
    });

    const response = await post(app, documentRequest());
    const body = await response.text();

    expect(response.status).toBe(422);
    expect(JSON.parse(body)).toEqual({
      error: "submission-rejected",
      errorType: "SubmissionRejectedError",
    });
    expect(body).not.toContain("approvedContentSha256");
    expect(body).not.toContain("Synthetic");
  });

  // A certified Word source is refused when the run is not a dry run, until Zone B recomputes it:
  // the caller learns that by its closed code, and nothing else (review of #193).
  it("gives a certified Word submission's closed code when the run is not a dry run", async () => {
    const [label] = recomputedCases();
    if (label === undefined) throw new Error("no recomputed label");
    const word = importCertifiedWord(
      recomputed(label.name),
      caseRequest(label),
      mapping,
      CERTIFIED_WORD_RUN,
    );
    const app = createApp({
      // The gate refuses before anything the persisted path needs is read.
      config: { ...configFor({ SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET }), DRY_RUN: false },
      submissionReader: readerReturning(() => word),
    });

    const response = await post(app, documentRequest());
    const body = await response.text();

    expect(response.status).toBe(422);
    expect(JSON.parse(body)).toEqual({
      error: "submission-rejected",
      errorType: "SubmissionRejectedError",
      reason: "certified-word-not-recomputed",
    });
    expect(body).not.toContain("Synthetic");
  });
});
