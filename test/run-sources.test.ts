import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { createApp } from "../src/app.js";
import { loadConfig, RUN_SOURCES, type AppConfig } from "../src/config.js";
import { RunRequestSchema } from "../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import {
  SYNTHETIC_SUBMISSION_BUCKET,
  SYNTHETIC_SUBMISSION_URI,
  createSyntheticSubmission,
  type SyntheticSubmission,
} from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { HealthcareApiClient } from "../src/gcp/healthcare.js";
import type { SubmissionReader } from "../src/gcp/submission-reader.js";
import { sha256 } from "../src/lib/hash.js";

// The fixture module is replaced by a recording copy of itself so a test can prove that the
// fixture source did (or did not) build a bundle; every other export is the real one.
vi.mock("../src/fixtures/synthetic.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/fixtures/synthetic.js")>();
  return { ...actual, createSyntheticType2Bundle: vi.fn(actual.createSyntheticType2Bundle) };
});

let mapping: EmaMapping;
let fixture: SyntheticSubmission;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  fixture = createSyntheticSubmission(mapping);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(createSyntheticType2Bundle).mockClear();
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

function recordingReader(): SubmissionReader & { calls: number } {
  const reader = {
    calls: 0,
    read: () => {
      reader.calls += 1;
      return Promise.resolve(fixture);
    },
  };
  return reader;
}

async function post(app: ReturnType<typeof createApp>, body: unknown): Promise<Response> {
  return await app.request("/v1/runs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

const fixtureRequest = { source: "fixture" } as const;
const healthcareRequest = { source: "healthcare-api", bundleId: "synthetic-type2" } as const;
function documentRequest() {
  return {
    source: "document" as const,
    submissionRef: { uri: SYNTHETIC_SUBMISSION_URI, sha256: sha256(fixture.submission) },
  };
}

describe("ENABLED_RUN_SOURCES parsing", () => {
  it("lists exactly the sources the RunRequest contract names", () => {
    const contractSources = RunRequestSchema.options.map((option) => option.shape.source.value);
    expect([...RUN_SOURCES].sort()).toEqual([...contractSources].sort());
  });

  it("enables every source when unset, where synthetic sources are allowed", () => {
    expect(configFor().ENABLED_RUN_SOURCES).toEqual(["fixture", "healthcare-api", "document"]);
  });

  it("enables only the gated document source when unset and synthetic sources are not allowed", () => {
    expect(configFor({ ALLOW_SYNTHETIC_SOURCES: "false" }).ENABLED_RUN_SOURCES).toEqual([
      "document",
    ]);
    // The default of the flag itself is off.
    expect(loadConfig({ NODE_ENV: "test", DRY_RUN: "true" }).ALLOW_SYNTHETIC_SOURCES).toBe(false);
  });

  it("refuses to start with a gate-bypassing source where synthetic sources are not allowed", () => {
    for (const sources of ["fixture", "healthcare-api", "document,fixture"]) {
      expect(() =>
        configFor({ ALLOW_SYNTHETIC_SOURCES: "false", ENABLED_RUN_SOURCES: sources }),
      ).toThrow(/bypass the document gate/);
    }
    expect(
      configFor({ ALLOW_SYNTHETIC_SOURCES: "false", ENABLED_RUN_SOURCES: "document" })
        .ENABLED_RUN_SOURCES,
    ).toEqual(["document"]);
  });

  it("accepts a comma-separated subset, trimming and de-duplicating entries", () => {
    expect(configFor({ ENABLED_RUN_SOURCES: "document" }).ENABLED_RUN_SOURCES).toEqual([
      "document",
    ]);
    expect(
      configFor({ ENABLED_RUN_SOURCES: " document , fixture,document" }).ENABLED_RUN_SOURCES,
    ).toEqual(["document", "fixture"]);
  });

  it("rejects an unknown source name, an empty value, and a dangling comma", () => {
    expect(() => configFor({ ENABLED_RUN_SOURCES: "document,workflow" })).toThrow();
    expect(() => configFor({ ENABLED_RUN_SOURCES: "" })).toThrow();
    expect(() => configFor({ ENABLED_RUN_SOURCES: "   " })).toThrow();
    expect(() => configFor({ ENABLED_RUN_SOURCES: "fixture," })).toThrow();
  });
});

describe("run-source allowlist on POST /v1/runs", () => {
  it("runs an enabled fixture source exactly as before", async () => {
    const response = await post(
      createApp({ config: configFor({ ENABLED_RUN_SOURCES: "fixture" }) }),
      fixtureRequest,
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      status: "validated",
      mappingDecisions: 32,
      artifacts: [],
    });
    expect(vi.mocked(createSyntheticType2Bundle)).toHaveBeenCalledTimes(1);
  });

  it("runs an enabled healthcare-api source through the source store read", async () => {
    const read = vi
      .spyOn(HealthcareApiClient.prototype, "readSourceResource")
      .mockImplementation(() => Promise.resolve(createSyntheticType2Bundle(mapping)));
    vi.mocked(createSyntheticType2Bundle).mockClear();

    const response = await post(
      createApp({
        config: configFor({
          ENABLED_RUN_SOURCES: "healthcare-api",
          GOOGLE_CLOUD_PROJECT: "p",
          HEALTHCARE_DATASET_ID: "d",
          SOURCE_FHIR_STORE_ID: "s",
        }),
      }),
      healthcareRequest,
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ status: "validated", mappingDecisions: 32 });
    expect(read).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0]?.slice(0, 2)).toEqual(["Bundle", healthcareRequest.bundleId]);
  });

  it("runs an enabled document source through the submission reader", async () => {
    const reader = recordingReader();
    const response = await post(
      createApp({
        config: configFor({
          ENABLED_RUN_SOURCES: "document",
          SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET,
        }),
        submissionReader: reader,
      }),
      documentRequest(),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      status: "validated",
      submissionId: fixture.submission.submissionId,
    });
    expect(reader.calls).toBe(1);
  });

  it("rejects a disabled source before any reader, fixture, or client is touched", async () => {
    const read = vi
      .spyOn(HealthcareApiClient.prototype, "readSourceResource")
      .mockImplementation(() => Promise.reject(new Error("must not be called")));
    const reader = recordingReader();
    const lines: string[] = [];
    const push = (line: string): void => {
      lines.push(line);
    };
    vi.spyOn(console, "log").mockImplementation(push);
    vi.spyOn(console, "error").mockImplementation(push);

    const app = createApp({
      config: configFor({
        ENABLED_RUN_SOURCES: "document",
        SUBMISSION_BUCKET: SYNTHETIC_SUBMISSION_BUCKET,
        GOOGLE_CLOUD_PROJECT: "p",
        HEALTHCARE_DATASET_ID: "d",
        SOURCE_FHIR_STORE_ID: "s",
      }),
      submissionReader: reader,
    });

    const fixtureResponse = await post(app, fixtureRequest);
    const healthcareResponse = await post(app, healthcareRequest);

    expect(fixtureResponse.status).toBe(422);
    expect(await fixtureResponse.json()).toEqual({ error: "source-disabled" });
    expect(healthcareResponse.status).toBe(422);
    expect(await healthcareResponse.json()).toEqual({ error: "source-disabled" });
    expect(vi.mocked(createSyntheticType2Bundle)).not.toHaveBeenCalled();
    expect(read).not.toHaveBeenCalled();
    expect(reader.calls).toBe(0);

    // The rejection leaves exactly one structured line per request, carrying source and stage
    // only; no runId is minted and nothing else is logged.
    const entries = lines.map((line) => JSON.parse(line) as Record<string, unknown>);
    expect(entries).toHaveLength(2);
    for (const [index, entry] of entries.entries()) {
      const { severity, message, timestamp, ...fields } = entry;
      expect(severity).toBe("WARNING");
      expect(typeof message).toBe("string");
      expect(typeof timestamp).toBe("string");
      expect(fields).toEqual({
        stage: "http",
        source: index === 0 ? "fixture" : "healthcare-api",
      });
    }
  });

  it("rejects a disabled document source ahead of the bucket-not-configured answer", async () => {
    const response = await post(
      createApp({ config: configFor({ ENABLED_RUN_SOURCES: "fixture" }) }),
      documentRequest(),
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({ error: "source-disabled" });
  });
});
