import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { createApp } from "../src/app.js";
import { loadConfig, type AppConfig } from "../src/config.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { PROVENANCE_PROFILE } from "../src/fhir/provenance.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import type { FhirBundle, FhirResource, OperationOutcome } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import type { SignedManifest } from "../src/gcp/evidence.js";
import type { PersistTransaction } from "../src/gcp/healthcare.js";
import { sha256 } from "../src/lib/hash.js";
import { officialValidationTargets, runPipeline } from "../src/pipeline.js";

// A persisted run (DRY_RUN=false) with every Google client and both validators replaced, so what
// is asserted is the pipeline's own behaviour: that each validation gate stops the run before any
// side effect, and the order of the side effects once it passes. Before this file no test
// exercised a gate's refusal in persist mode, and every gate could be disabled with the suite
// still green.

type Event = { kind: string; name?: string };

const state = vi.hoisted(() => ({
  events: [] as Event[],
  failCanonical: false,
  failEma: false,
  failOfficial: false,
  failCloud: false,
  failWrite: undefined as string | undefined,
  failSign: false,
  failExecute: false,
  failLedger: false,
  failLineage: false,
  objects: new Map<string, unknown>(),
  executed: [] as unknown[],
  // What each validator was asked: the resource and its profiles, in order.
  official: [] as { resource: FhirResource; profiles: string[] }[],
  cloud: [] as { resource: FhirResource; profiles: string[] }[],
}));

const VERSION = "MTc5MDUyNzYzMTkyMTk4NTAwMA";

vi.mock("../src/fhir/preflight.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/fhir/preflight.js")>();
  const failed: OperationOutcome = {
    resourceType: "OperationOutcome",
    issue: [{ severity: "error", code: "invalid", diagnostics: "stubbed refusal" }],
  };
  return {
    ...actual,
    validateCanonicalPreflight: (...args: Parameters<typeof actual.validateCanonicalPreflight>) =>
      state.failCanonical ? failed : actual.validateCanonicalPreflight(...args),
    validateEmaPreflight: (...args: Parameters<typeof actual.validateEmaPreflight>) =>
      state.failEma ? failed : actual.validateEmaPreflight(...args),
  };
});

vi.mock("../src/fhir/official-validator.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/fhir/official-validator.js")>()),
  OfficialFhirValidatorClient: class {
    public validate(resource: FhirResource, profiles: string[]): Promise<OperationOutcome> {
      state.events.push({ kind: "official-validate" });
      state.official.push({ resource, profiles });
      return Promise.resolve(
        state.failOfficial
          ? { resourceType: "OperationOutcome", issue: [{ severity: "error", code: "invalid" }] }
          : { resourceType: "OperationOutcome", issue: [] },
      );
    }
  },
}));

vi.mock("../src/gcp/healthcare.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/gcp/healthcare.js")>()),
  HealthcareApiClient: class {
    public validate(resource: FhirResource, profile: string): Promise<OperationOutcome> {
      state.events.push({ kind: "cloud-validate" });
      state.cloud.push({ resource, profiles: [profile] });
      return Promise.resolve(
        state.failCloud
          ? { resourceType: "OperationOutcome", issue: [{ severity: "fatal", code: "invalid" }] }
          : { resourceType: "OperationOutcome", issue: [] },
      );
    }
    public executeTransaction(transaction: PersistTransaction): Promise<unknown> {
      state.events.push({ kind: "execute" });
      state.executed.push(transaction);
      if (state.failExecute) return Promise.reject(new Error("Healthcare API request timed out"));
      const bundle = transaction.entry.find(({ resource }) => resource.resourceType === "Bundle");
      return Promise.resolve({
        resourceType: "Bundle",
        type: "transaction-response",
        entry: transaction.entry.map(({ request }) => ({
          response: {
            status: "200 OK",
            location: `${request.url}/_history/${request.url === `Bundle/${bundle?.resource.id ?? ""}` ? VERSION : "1"}`,
            lastModified: "2026-09-27T16:47:11.921985+00:00",
          },
        })),
      });
    }
  },
}));

vi.mock("../src/gcp/evidence.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/gcp/evidence.js")>()),
  GcpEvidenceStore: class {
    public writeJson(runId: string, name: string, value: unknown): Promise<string> {
      state.events.push({ kind: "write", name });
      if (state.failWrite === name) {
        return Promise.reject(new Error("Run evidence already exists for this run id"));
      }
      state.objects.set(name, structuredClone(value));
      return Promise.resolve(`gs://evidence/runs/${runId}/${name}.json`);
    }
    public signManifest(manifest: SignedManifest["manifest"]): Promise<SignedManifest> {
      state.events.push({ kind: "sign" });
      if (state.failSign)
        return Promise.reject(new Error("Cloud KMS returned no manifest signature"));
      return Promise.resolve({
        manifest,
        manifestHash: sha256(manifest),
        signature: {
          algorithm: "RSA_SIGN_PSS_2048_SHA256",
          keyVersion: "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1",
          valueBase64: "c2lnbmF0dXJl",
        },
      });
    }
    public writeLedger(): Promise<void> {
      state.events.push({ kind: "ledger" });
      if (state.failLedger) {
        return Promise.reject(new Error("TRANSFORMATION_LEDGER_DATASET is required"));
      }
      return Promise.resolve();
    }
  },
}));

vi.mock("@google-cloud/lineage", () => ({
  LineageClient: class {
    public createProcess(): Promise<{ name: string }[]> {
      state.events.push({ kind: "lineage" });
      if (state.failLineage) return Promise.reject(new Error("lineage unavailable"));
      return Promise.resolve([{ name: "processes/p" }]);
    }
    public createRun(): Promise<{ name: string }[]> {
      return Promise.resolve([{ name: "processes/p/runs/r" }]);
    }
    public createLineageEvent(): Promise<{ name: string }[]> {
      return Promise.resolve([{ name: "processes/p/runs/r/events/e" }]);
    }
  },
}));

const RUN_ID = "88888888-8888-4888-a888-888888888888";

let mapping: EmaMapping;
let config: AppConfig;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  config = loadConfig({
    ALLOW_SYNTHETIC_SOURCES: "true",
    NODE_ENV: "test",
    DRY_RUN: "false",
    GOOGLE_CLOUD_PROJECT: "synthetic-project",
    HEALTHCARE_DATASET_ID: "dataset",
    SOURCE_FHIR_STORE_ID: "source",
    TARGET_FHIR_STORE_ID: "target",
    EVIDENCE_BUCKET: "evidence",
    FHIR_VALIDATOR_URL: "http://validator.invalid",
    FHIR_ANALYTICS_DATASET: "analytics",
    KMS_MANIFEST_KEY:
      "projects/p/locations/europe-west4/keyRings/evidence/cryptoKeys/manifest-signing/cryptoKeyVersions/1",
    TRANSFORMATION_LEDGER_DATASET: "ledger",
  });
});

beforeEach(() => {
  state.events.length = 0;
  state.failCanonical = false;
  state.failEma = false;
  state.failOfficial = false;
  state.failCloud = false;
  state.failWrite = undefined;
  state.failSign = false;
  state.failExecute = false;
  state.failLedger = false;
  state.failLineage = false;
  state.objects.clear();
  state.executed.length = 0;
  state.official.length = 0;
  state.cloud.length = 0;
});

function kinds(): string[] {
  return state.events.map(({ kind, name }) => (name === undefined ? kind : `${kind}:${name}`));
}

function sideEffects(): string[] {
  return kinds().filter((kind) => !kind.endsWith("-validate"));
}

async function post(body: unknown = { source: "fixture", runId: RUN_ID }): Promise<Response> {
  return await createApp({ config }).request("/v1/runs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("a persisted run's validation gates", () => {
  it.each([
    ["the canonical preflight", "failCanonical", "source-preflight-failed"],
    ["the EMA preflight", "failEma", "ema-preflight-failed"],
    ["the official HL7 validator", "failOfficial", "official-validation-failed"],
    ["the Cloud Healthcare API $validate", "failCloud", "cloud-validation-failed"],
  ] as const)("stop at %s, before any side effect", async (_gate, flag, reason) => {
    state[flag] = true;

    const response = await post();

    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "pipeline-failed", errorType: "Error", reason });
    expect(sideEffects()).toEqual([]);
    expect(state.executed).toEqual([]);
  });

  it("run the official validator only once the preflights pass, and $validate only after it", async () => {
    state.failCloud = true;
    await post();
    const order = kinds();
    expect(order.filter((kind) => kind === "official-validate")).toHaveLength(4);
    expect(order.lastIndexOf("official-validate")).toBeLessThan(order.indexOf("cloud-validate"));
  });

  // The one list CI's "Official validation" job validates too (scripts/ci/emit-validation-set.ts):
  // the validators are asked about exactly officialValidationTargets, the official one per
  // resource with all its profiles and $validate per resource and profile.
  it("validate exactly the pipeline's validation targets", async () => {
    await post();
    const source = state.official[0]?.resource as FhirBundle;
    const targets = officialValidationTargets(
      source,
      transformType2ToEma(source, mapping),
      mapping,
    );
    expect(targets.map(({ name }) => name)).toEqual([
      "source",
      "ema-list",
      "ema-bundle",
      "ema-composition",
    ]);
    expect(state.official).toEqual(
      targets.map(({ resource, profiles }) => ({ resource, profiles })),
    );
    expect(state.cloud).toEqual(
      targets.flatMap(({ resource, profiles }) =>
        profiles.map((profile) => ({ resource, profiles: [profile] })),
      ),
    );
    const signed = state.objects.get("signed-manifest") as SignedManifest | undefined;
    expect(signed?.manifest.validation.profiles).toEqual(
      targets.flatMap(({ profiles }) => profiles),
    );
  });

  it("finds no validation target in a document Bundle without a Composition", () => {
    const source = createSyntheticType2Bundle(mapping);
    const transformed = transformType2ToEma(source, mapping);
    expect(() =>
      officialValidationTargets(
        source,
        { ...transformed, documentBundle: { ...transformed.documentBundle, entry: [] } },
        mapping,
      ),
    ).toThrow("Transformed Composition is missing");
  });
});

describe("a persisted run's commit order", () => {
  it("writes evidence and the signed manifest before the transaction, and the ledger row last", async () => {
    const response = await post();
    expect(response.status).toBe(200);

    expect(sideEffects()).toEqual([
      "write:source-type2",
      "write:ema-list",
      "write:ema-document-bundle",
      "write:mapping-decisions",
      "write:validation-outcomes",
      "write:persist-transaction",
      "sign",
      "write:signed-manifest",
      "execute",
      "write:commit",
      "ledger",
      "lineage",
      "write:lineage-resources",
    ]);
  });

  it("signs a manifest naming the exact transaction it then sends", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    const result = await runPipeline(
      {
        runId: RUN_ID,
        sourceKind: "document",
        submission,
        fidelityReport,
        sourceText,
        sourceResource: `document:${"0".repeat(64)}`,
      },
      mapping,
      config,
    );

    const [sent] = state.executed;
    // Signed before the transaction, so it says `authorised`; the run itself is `persisted`.
    expect(result.status).toBe("persisted");
    expect(result.evidence.manifest).toMatchObject({
      status: "authorised",
      dryRun: false,
      persistence: { targetStore: "target", transactionSha256: sha256(sent) },
    });
    expect(state.objects.get("persist-transaction")).toEqual(sent);
    expect(state.objects.get("signed-manifest")).toEqual(result.evidence);
    // The approval Provenance travels in the same transaction, after it was signed for, as the
    // official validator saw it, against base R5; $validate is not asked about it (the store has
    // no definition of its extension or code systems).
    const persisted = (sent as PersistTransaction).entry.find(
      ({ resource }) => resource.resourceType === "Provenance",
    )?.resource;
    expect(persisted).toBeDefined();
    expect(state.official.at(-1)).toEqual({ resource: persisted, profiles: [PROVENANCE_PROFILE] });
    expect(state.cloud.some(({ resource }) => resource.resourceType === "Provenance")).toBe(false);
    expect(result.evidence.manifest.validation.profiles.at(-1)).toBe(PROVENANCE_PROFILE);
    expect(state.objects.get("commit")).toEqual({
      runId: RUN_ID,
      manifestHash: result.evidence.manifestHash,
      transactionSha256: sha256(sent),
      transactionResponseSha256: expect.stringMatching(/^[0-9a-f]{64}$/) as unknown,
      committedAt: expect.stringMatching(/^\d{4}-/) as unknown,
      targetBundle: {
        id: result.emaBundle.id,
        versionId: VERSION,
        lastUpdated: "2026-09-27T16:47:11.921985+00:00",
      },
    });
    expect(result.persistedBundle).toEqual({
      versionId: VERSION,
      lastUpdated: "2026-09-27T16:47:11.921985+00:00",
    });
  });

  it("answers with the version the transaction wrote, for the workflow's stream check", async () => {
    const body = (await (await post()).json()) as Record<string, unknown>;
    expect(body.status).toBe("persisted");
    expect(body.targetBundleVersionId).toBe(VERSION);
    expect(body.targetBundleLastUpdated).toBe("2026-09-27T16:47:11.921985+00:00");
  });

  it("refuses a runId whose evidence already exists before the FHIR store is touched", async () => {
    state.failWrite = "source-type2";

    const response = await post();

    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ reason: "run-id-reused" });
    expect(state.executed).toEqual([]);
    expect(kinds()).not.toContain("sign");
  });

  it("persists nothing when the manifest cannot be signed", async () => {
    state.failSign = true;

    const response = await post();

    expect(await response.json()).toMatchObject({ reason: "kms-no-signature" });
    expect(state.executed).toEqual([]);
    expect(kinds()).not.toContain("ledger");
  });

  it("writes no ledger row, its commit record, when the transaction fails", async () => {
    state.failExecute = true;

    const response = await post();

    expect(await response.json()).toMatchObject({ reason: "healthcare-timeout" });
    expect(kinds()).toContain("write:signed-manifest");
    expect(kinds()).not.toContain("ledger");
    expect(kinds()).not.toContain("write:commit");
  });

  // The transaction is live by then: the answer says so, rather than that the run failed.
  it("answers committed-unrecorded when the ledger row cannot be written after the commit", async () => {
    state.failLedger = true;
    const response = await post();
    expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ reason: "committed-unrecorded" });
    expect(state.executed).toHaveLength(1);
  });

  it("answers committed-unrecorded when the commit object cannot be written", async () => {
    state.failWrite = "commit";
    const response = await post();
    expect(await response.json()).toMatchObject({ reason: "committed-unrecorded" });
    expect(kinds()).not.toContain("ledger");
  });

  // Lineage used to throw after the transaction, the signature and the ledger row, answering 500
  // for a committed run and so inviting a retry that duplicated it.
  it("answers success for a committed run whose lineage could not be published", async () => {
    state.failLineage = true;

    const response = await post();

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ status: "persisted" });
    expect(kinds()).toContain("ledger");
    expect(kinds()).not.toContain("write:lineage-resources");
  });
});
