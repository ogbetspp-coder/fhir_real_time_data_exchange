import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import {
  RunManifestSchema,
  SubmissionRejectedError,
  approvedContent,
  type CanonicalSubmission,
} from "../src/contracts/index.js";
import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { TransformationError } from "../src/fhir/transform.js";
import type { FhirComposition } from "../src/fhir/types.js";
import { createSyntheticSubmission } from "../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { sha256 } from "../src/lib/hash.js";
import { runPipeline } from "../src/pipeline.js";
import { attested } from "./support/submission.js";

const FIXTURE_RUN_ID = "11111111-1111-4111-a111-111111111111";
const DOCUMENT_RUN_ID = "22222222-2222-4222-a222-222222222222";

let mapping: EmaMapping;
let config: AppConfig;

beforeAll(async () => {
  mapping = await loadEmaMapping();
  config = loadConfig({
    ALLOW_SYNTHETIC_SOURCES: "true",
    NODE_ENV: "test",
    DRY_RUN: "true",
    GCP_LOCATION: "europe-west4",
  });
});

function composition(submission: CanonicalSubmission): FhirComposition {
  const resource = submission.bundle.entry[0]?.resource as unknown as FhirComposition | undefined;
  if (resource === undefined) throw new Error("Synthetic submission requires a Composition");
  return resource;
}

async function rejection(run: Promise<unknown>): Promise<SubmissionRejectedError> {
  const error: unknown = await run.then(
    () => undefined,
    (cause: unknown) => cause,
  );
  expect(error).toBeInstanceOf(SubmissionRejectedError);
  if (!(error instanceof SubmissionRejectedError)) throw new Error("expected a rejection");
  return error;
}

describe("pipeline", () => {
  it("returns signed-evidence-ready validation metadata in dry-run mode", async () => {
    const result = await runPipeline(
      {
        runId: FIXTURE_RUN_ID,
        source: createSyntheticType2Bundle(mapping),
        sourceKind: "fixture",
        sourceResource: "fixture:test",
      },
      mapping,
      config,
    );

    expect(result.status).toBe("validated");
    expect(result.artifactUris).toEqual([]);
    expect(result.evidence.signature).toBeUndefined();
    expect(result.evidence.manifest.schemaVersion).toBe("5.0.0");
    expect(result.evidence.manifest.validation.preflightErrors).toBe(0);
    expect(result.evidence.manifest.validation.officialValidationExecuted).toBe(false);
    expect(result.evidence.manifest.validation.officialProfileErrors).toBe(0);
    expect(result.evidence.manifest.validation.cloudValidationExecuted).toBe(false);
    expect(result.evidence.manifest.validation.cloudProfileErrors).toBe(0);
    expect(result.evidence.manifest.transformation.decisions).toBe(32);
    expect(result.evidence.manifest.ingestion).toBeUndefined();
    expect(() => RunManifestSchema.parse(result.evidence.manifest)).not.toThrow();
  });

  // The allowlist held only at the HTTP surface let scripts/dev/run-pipeline.ts run a fixture
  // against a deployment that enables only `document`, and persist it signed.
  it.each([
    ["the no-synthetic default", {}],
    [
      "an explicit document-only list",
      { ALLOW_SYNTHETIC_SOURCES: "true", ENABLED_RUN_SOURCES: "document" },
    ],
  ])("refuses a source the deployment disabled, under %s", async (_, environment) => {
    const disabled = loadConfig({ NODE_ENV: "test", DRY_RUN: "true", ...environment });
    expect(disabled.ENABLED_RUN_SOURCES).toEqual(["document"]);
    await expect(
      runPipeline(
        {
          runId: FIXTURE_RUN_ID,
          source: createSyntheticType2Bundle(mapping),
          sourceKind: "fixture",
          sourceResource: "fixture:test",
        },
        mapping,
        disabled,
      ),
    ).rejects.toThrow(/^Run source is disabled$/);
  });

  it("publishes an approved document submission with ingestion evidence", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    const fixture = await runPipeline(
      {
        runId: FIXTURE_RUN_ID,
        source: createSyntheticType2Bundle(mapping),
        sourceKind: "fixture",
        sourceResource: "fixture:test",
      },
      mapping,
      config,
    );

    const result = await runPipeline(
      {
        runId: DOCUMENT_RUN_ID,
        sourceKind: "document",
        submission,
        fidelityReport,
        sourceText,
        sourceResource: "document:synthetic-smpc",
      },
      mapping,
      config,
    );
    const { manifest } = result.evidence;

    expect(result.status).toBe("validated");
    expect(manifest.schemaVersion).toBe("5.0.0");
    expect(manifest.source.kind).toBe("document");
    expect(manifest.transformation.decisions).toBe(32);
    // Zone B determinism: the document path must publish exactly what the fixture path publishes.
    expect(manifest.transformation.outputHash).toBe(
      fixture.evidence.manifest.transformation.outputHash,
    );
    expect(manifest.ingestion?.submissionId).toBe(submission.submissionId);
    expect(manifest.ingestion?.contractVersion).toBe("2.0.0");
    expect(manifest.ingestion?.parser).toBe("synthetic-extractor@1.0.0");
    expect(manifest.ingestion?.fidelity.status).toBe("passed");
    expect(manifest.ingestion?.fidelity.coverage.pageCodePoints).toBeGreaterThan(
      manifest.ingestion?.fidelity.coverage.bodyCodePoints ?? Number.POSITIVE_INFINITY,
    );
    expect(manifest.ingestion?.fidelity.coverage.uncoveredGaps).toBe(0);
    expect(manifest.ingestion?.fidelity.sectionsChecked).toBe(32);
    expect(manifest.ingestion?.fidelity.sectionsMatched).toBe(32);
    expect(manifest.ingestion?.fidelity.reportSha256).toBe(fidelityReport.reportHash);
    const ingestionApproval = manifest.ingestion?.approval;
    expect(
      ingestionApproval?.method === "authority-publication"
        ? undefined
        : ingestionApproval?.approverId,
    ).toBe(attested(submission).approverId);
    expect(() => RunManifestSchema.parse(manifest)).not.toThrow();
  });

  it("rejects a tampered approval hash before any transformation", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    const tampered = structuredClone(submission);
    tampered.approval.approvedContentSha256 = "f".repeat(64);

    const error = await rejection(
      runPipeline(
        {
          runId: DOCUMENT_RUN_ID,
          sourceKind: "document",
          submission: tampered,
          fidelityReport,
          sourceText,
          sourceResource: "document:synthetic-smpc",
        },
        mapping,
        config,
      ),
    );

    expect(error.issues).toContain(
      "approval.approvedContentSha256 does not match the submitted content",
    );
  });

  it("rejects a narrative altered after approval even when every hash is recomputed", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    const altered = structuredClone(submission);
    const section = composition(altered).section[0];
    if (section?.text === undefined) throw new Error("Synthetic submission requires a narrative");
    section.text.div = section.text.div.replace("Synthetic", "Revised");
    altered.bundleSha256 = sha256(altered.bundle);
    altered.approval.approvedContentSha256 = sha256(approvedContent(altered));

    const error = await rejection(
      runPipeline(
        {
          runId: DOCUMENT_RUN_ID,
          sourceKind: "document",
          submission: altered,
          fidelityReport,
          sourceText,
          sourceResource: "document:synthetic-smpc",
        },
        mapping,
        config,
      ),
    );

    expect(error.issues).toContain("Bundle narratives do not match the fidelity report binding");
    expect(error.issues).toContain("narrativeDivSha256 does not match section smpc");
  });

  it("fails closed in the transform after a document passes the ingress gate", async () => {
    // A language tag is not narrative, so the gate, the fidelity binding and every recomputed
    // hash accept it; the crosswalk's own check is what stops the run. (A section moved to another
    // parent does not get this far: the re-executed fidelity report binds section paths.)
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    const altered = structuredClone(submission);
    composition(altered).language = "fr";
    altered.bundleSha256 = sha256(altered.bundle);
    altered.approval.approvedContentSha256 = sha256(approvedContent(altered));

    const error: unknown = await runPipeline(
      {
        runId: DOCUMENT_RUN_ID,
        sourceKind: "document",
        submission: altered,
        fidelityReport,
        sourceText,
        sourceResource: "document:synthetic-smpc",
      },
      mapping,
      config,
    ).then(
      () => undefined,
      (cause: unknown) => cause,
    );

    expect(error).toBeInstanceOf(TransformationError);
    expect((error as TransformationError).issues).toEqual([
      "Source Composition.language fr is not English; the mapping is English-only",
    ]);
  });

  it("rejects a fidelity report whose hash no longer recomputes", async () => {
    const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping);
    const report = { ...fidelityReport, reportHash: "a".repeat(64) };

    const error = await rejection(
      runPipeline(
        {
          runId: DOCUMENT_RUN_ID,
          sourceKind: "document",
          submission,
          fidelityReport: report,
          sourceText,
          sourceResource: "document:synthetic-smpc",
        },
        mapping,
        config,
      ),
    );

    expect(error.issues).toContain("Fidelity report hash does not recompute");
    expect(error.issues).toContain(
      "Fidelity report hash does not match provenance.fidelity.reportSha256",
    );
  });
});
