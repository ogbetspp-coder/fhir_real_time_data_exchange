import { beforeAll, describe, expect, it } from "vitest";

import { syntheticFetcher } from "../../src/authority/fetch.js";
import { importPublication } from "../../src/authority/import.js";
import { syntheticPublication } from "../../src/authority/synthetic.js";
import { loadConfig } from "../../src/config.js";
import { RunManifestSchema, SubmissionRejectedError } from "../../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { runPipeline } from "../../src/pipeline.js";
import { RUN } from "./support.js";

// An authority import through the worker's pipeline, dry run only until PR 5
// (docs/design/authority-import-contract.md, D1, D12).

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

const RUN_ID = "00000000-0000-4000-8000-0000000000cc";

function input() {
  const publication = syntheticPublication(mapping);
  const { submission, fidelityReport, sourceText } = importPublication(
    publication.request,
    publication,
    mapping,
    RUN,
  );
  return {
    runId: RUN_ID,
    sourceKind: "document" as const,
    submission,
    fidelityReport,
    sourceText,
    sourceResource: "document:synthetic-import",
  };
}

const fetchedAt = () => new Date("2026-09-24T12:00:00Z");

describe("an authority import in the pipeline", () => {
  it("runs dry through the gate, the Type 1 preflight and the crosswalk, and records what was fetched", async () => {
    const config = loadConfig({
      NODE_ENV: "test",
      DRY_RUN: "true",
      ALLOW_SYNTHETIC_SOURCES: "true",
    });
    const result = await runPipeline(input(), mapping, config, {
      authorityFetcher: { fetch: syntheticFetcher(mapping, true, fetchedAt) },
    });
    const { ingestion } = result.evidence.manifest;

    expect(result.status).toBe("validated");
    expect(ingestion?.sourceKind).toBe("authority-publication");
    expect(ingestion?.graphType).toBe("type1");
    expect(ingestion?.allowSyntheticSources).toBe(true);
    expect(ingestion?.approval.method).toBe("authority-publication");
    expect(ingestion?.authority?.importerVersion).toBe("2.0.0");
    expect(ingestion?.authority?.fetched.map(({ fetchedAt: at }) => at)).toEqual([
      "2026-09-24T12:00:00.000Z",
      "2026-09-24T12:00:00.000Z",
    ]);
    expect(() => RunManifestSchema.parse(result.evidence.manifest)).not.toThrow();
  });

  it("is refused where synthetic content is not accepted", async () => {
    const config = loadConfig({ NODE_ENV: "test", DRY_RUN: "true" });
    await expect(runPipeline(input(), mapping, config)).rejects.toBeInstanceOf(
      SubmissionRejectedError,
    );
  });
});
