import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";
import type { z } from "zod";

import { loadConfig } from "../../src/config.js";
import {
  AnyRunManifestSchema,
  RUN_MANIFEST_VERSION,
  RunManifestSchema,
  RunManifestV11Schema,
  RunManifestV1Schema,
  RunManifestV2Schema,
  RunManifestV3Schema,
  RunManifestV4Schema,
} from "../../src/contracts/index.js";
import { manifestRuntime } from "../../src/pipeline.js";

// Old manifest versions frozen from copies of their own parts, read against the manifests each
// version's own code emitted, and 5.0.0's grammars for what names the code and the images
// (audit C-9).
//
// test/fixtures/run-manifest/ holds, per version, the dry-run manifest of a fixture
// run and (from 1.1.0, when the document source arrived) of a document run over the synthetic
// submission, each emitted by that version's code: 1.0.0 at 499e2b2, 1.1.0 at dd78a14, 2.0.0 at
// 2a035f3 (#113), 3.0.0 at 95e7207 (#128), 4.0.0 at 9b4cb2a, 5.0.0 at this change. They are never
// regenerated: each is the evidence that its version reads what that code wrote.

const FIXTURES = path.resolve("test/fixtures/run-manifest");

const BY_VERSION: Record<string, z.ZodType> = {
  "1.0.0": RunManifestV1Schema,
  "1.1.0": RunManifestV11Schema,
  "2.0.0": RunManifestV2Schema,
  "3.0.0": RunManifestV3Schema,
  "4.0.0": RunManifestV4Schema,
  [RUN_MANIFEST_VERSION]: RunManifestSchema,
};

function emitted(): { file: string; version: string; manifest: Record<string, unknown> }[] {
  return readdirSync(FIXTURES)
    .filter((file) => file.endsWith(".json"))
    .sort()
    .map((file) => {
      const manifest = JSON.parse(readFileSync(path.join(FIXTURES, file), "utf8")) as Record<
        string,
        unknown
      >;
      return { file, version: String(manifest.schemaVersion), manifest };
    });
}

describe("the run manifest's released versions", () => {
  it("each has the manifests its own code emitted", () => {
    const files = emitted().map(({ file }) => file);
    for (const version of Object.keys(BY_VERSION)) {
      expect(files).toContain(`${version}-fixture.json`);
      if (version !== "1.0.0") expect(files).toContain(`${version}-document.json`);
    }
  });

  it("reads each emitted manifest under its own version, and under no other", () => {
    for (const { file, version, manifest } of emitted()) {
      expect([file, file.startsWith(`${version}-`)]).toEqual([file, true]);
      for (const [other, schema] of Object.entries(BY_VERSION)) {
        expect([file, other, schema.safeParse(manifest).success]).toEqual([
          file,
          other,
          other === version,
        ]);
      }
      expect([file, AnyRunManifestSchema.safeParse(manifest).success]).toEqual([file, true]);
    }
  });

  it("builds the old versions from nothing live", () => {
    const source = readFileSync("src/contracts/run-manifest-frozen.ts", "utf8");
    const imports = [...source.matchAll(/^import .* from "([^"]+)";$/gmu)].map(([, from]) => from);
    expect(imports).toEqual(["zod"]);
  });

  // 4.0.0 took any text up to 1,024 characters for the code, the images and the store.
  it("keeps 4.0.0's free text readable as 4.0.0, and refuses it in 5.0.0", () => {
    const at = (version: string, kind: string): Record<string, unknown> => {
      const found = emitted().find(({ file }) => file === `${version}-${kind}.json`);
      if (found === undefined) throw new Error(`no ${version}-${kind}.json`);
      return found.manifest;
    };
    const v4 = at("4.0.0", "fixture");
    const loose = {
      sourceCommit: "local",
      imageDigest: "latest",
      workflowRevision: "any text",
      validatorImageDigest: "x",
    };
    expect(RunManifestV4Schema.safeParse({ ...v4, runtime: loose }).success).toBe(true);

    const current = at(RUN_MANIFEST_VERSION, "fixture");
    const runtime = current.runtime as Record<string, unknown>;
    const standards = current.standards as Record<string, unknown>;
    for (const change of [
      { runtime: { ...runtime, sourceCommit: "local" } },
      { runtime: { ...runtime, sourceCommit: "0123456" } },
      { runtime: { ...runtime, imageDigest: "latest" } },
      { runtime: { ...runtime, validatorImageDigest: `sha256:${"A".repeat(64)}` } },
      { runtime: { ...runtime, workflowRevision: "a revision with spaces" } },
      { standards: { ...standards, qrdTemplate: "10.4 draft" } },
      { standards: { ...standards, mappingVersion: "" } },
    ]) {
      expect([change, RunManifestSchema.safeParse({ ...current, ...change }).success]).toEqual([
        change,
        false,
      ]);
    }
    const named = {
      sourceCommit: "0123456789abcdef0123456789abcdef01234567",
      imageDigest: `sha256:${"a".repeat(64)}`,
      validatorImageDigest: `sha256:${"b".repeat(64)}`,
      workflowRevision: "ema-flow-worker-00042-xyz",
    };
    expect(RunManifestSchema.safeParse({ ...current, runtime: named }).success).toBe(true);
  });

  it("refuses a store the persistence block names in free text", () => {
    const found = emitted().find(({ file }) => file === `${RUN_MANIFEST_VERSION}-fixture.json`);
    if (found === undefined) throw new Error("no current fixture manifest");
    const authorised = {
      ...found.manifest,
      status: "authorised",
      dryRun: false,
      persistence: { targetStore: "ema-validated", transactionSha256: "a".repeat(64) },
    };
    expect(RunManifestSchema.safeParse(authorised).success).toBe(true);
    for (const targetStore of ["", "a store", "x".repeat(129)]) {
      const persistence = { ...authorised.persistence, targetStore };
      expect(RunManifestSchema.safeParse({ ...authorised, persistence }).success).toBe(false);
    }
  });
});

describe("the manifest's runtime, read through the configuration", () => {
  const base = { ALLOW_SYNTHETIC_SOURCES: "true", NODE_ENV: "test", DRY_RUN: "true" };

  it("records development where Cloud Run set nothing", () => {
    expect(manifestRuntime(loadConfig(base))).toEqual({
      sourceCommit: "development",
      imageDigest: "development",
      validatorImageDigest: "development",
      workflowRevision: "development",
    });
  });

  it("records what infra/run.tf and Cloud Run set", () => {
    const config = loadConfig({
      ...base,
      GIT_COMMIT: "0123456789abcdef0123456789abcdef01234567",
      IMAGE_DIGEST: `sha256:${"a".repeat(64)}`,
      VALIDATOR_IMAGE_DIGEST: `sha256:${"b".repeat(64)}`,
      K_REVISION: "ema-flow-worker-00042-xyz",
    });
    expect(manifestRuntime(config)).toEqual({
      sourceCommit: "0123456789abcdef0123456789abcdef01234567",
      imageDigest: `sha256:${"a".repeat(64)}`,
      validatorImageDigest: `sha256:${"b".repeat(64)}`,
      workflowRevision: "ema-flow-worker-00042-xyz",
    });
    expect(manifestRuntime({ ...config, WORKFLOW_REVISION: "wf-7" }).workflowRevision).toBe("wf-7");
  });

  // A value the manifest could not carry stops the worker at startup, not a run after its work.
  it.each([
    ["GIT_COMMIT", "local"],
    ["GIT_COMMIT", "0123456"],
    ["IMAGE_DIGEST", "europe-docker.pkg.dev/p/r/worker@sha256:" + "a".repeat(64)],
    ["VALIDATOR_IMAGE_DIGEST", "latest"],
    ["K_REVISION", "a revision"],
  ])("refuses %s=%s at startup", (name, value) => {
    expect(() => loadConfig({ ...base, [name]: value })).toThrow();
  });
});
