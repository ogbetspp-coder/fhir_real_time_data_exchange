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
  RunManifestV5Schema,
} from "../../src/contracts/index.js";
import { manifestRuntime } from "../../src/pipeline.js";

// Old manifest versions frozen from copies of their own parts, read against the manifests each
// version's own code emitted, and 5.0.0's grammars for what names the code and the images
// (audit C-9).
//
// test/fixtures/run-manifest/ holds, per version, the dry-run manifest of a fixture
// run and (from 1.1.0, when the document source arrived) of a document run over the synthetic
// submission, each emitted by that version's code: 1.0.0 at 499e2b2, 1.1.0 at dd78a14, 2.0.0 at
// 2a035f3 (#113), 3.0.0 at 95e7207 (#128), 4.0.0 at 9b4cb2a, 5.0.0 at 430939f, 5.1.0 at the change
// that made `certified-word` a source kind, with a dry run of a certified Word submission beside
// them (`5.1.0-certified-word.json`). They are never regenerated: each is the evidence that its
// version reads what that code wrote.
//
// Those are dry runs, which carry no persistence block. `*.synthetic.json` beside them are
// persist-mode manifests of the frozen versions (review of #148, part A L5), each derived from its
// version's own dry-run manifest by the one change a persisting run made: `persisted` with the
// transaction response's hash up to 2.0.0, `authorised` with the transaction's hash from 3.0.0,
// both validations executed. They are synthetic, not emitted: no code of those versions ran against
// a store here. The real persisted manifests in the dev evidence bucket are read by
// scripts/dev/check-evidence-manifests.ts, run by hand.

const FIXTURES = path.resolve("test/fixtures/run-manifest");

const BY_VERSION: Record<string, z.ZodType> = {
  "1.0.0": RunManifestV1Schema,
  "1.1.0": RunManifestV11Schema,
  "2.0.0": RunManifestV2Schema,
  "3.0.0": RunManifestV3Schema,
  "4.0.0": RunManifestV4Schema,
  "5.0.0": RunManifestV5Schema,
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

  it("reads a persist-mode manifest of every frozen version, with its own persistence only", () => {
    const persisted = emitted().filter(({ file }) => file.endsWith(".synthetic.json"));
    expect(persisted.map(({ version }) => version)).toEqual([
      "1.0.0",
      "1.1.0",
      "2.0.0",
      "3.0.0",
      "4.0.0",
      "5.0.0",
    ]);
    for (const { file, version, manifest } of persisted) {
      const schema = BY_VERSION[version];
      if (schema === undefined) throw new Error(`no schema for ${version}`);
      expect([file, schema.safeParse(manifest).success]).toEqual([file, true]);
      const persistence = manifest.persistence as Record<string, string>;
      const signedBefore = "transactionSha256" in persistence;
      expect([file, signedBefore]).toEqual([file, version >= "3.0.0"]);
      // The other era's persistence block, or none, is refused.
      const { transactionSha256, transactionResponseHash, targetStore } = persistence;
      const other = signedBefore
        ? { targetStore, transactionResponseHash: transactionSha256 }
        : { targetStore, transactionSha256: transactionResponseHash };
      expect(schema.safeParse({ ...manifest, persistence: other }).success).toBe(false);
      if (signedBefore) {
        const without = Object.fromEntries(
          Object.entries(manifest).filter(([key]) => key !== "persistence"),
        );
        expect(schema.safeParse(without).success).toBe(false);
      }
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

  // 5.1.0 (ADR 0006 P4, D1): a certified Word source's run, which 5.0.0 never wrote.
  it("reads a certified Word source's dry run in 5.1.0 only", () => {
    const found = emitted().find(
      ({ file }) => file === `${RUN_MANIFEST_VERSION}-certified-word.json`,
    );
    if (found === undefined) throw new Error("no certified Word manifest");
    const ingestion = found.manifest.ingestion as Record<string, unknown>;
    expect([ingestion.sourceKind, ingestion.graphType, ingestion.contractVersion]).toEqual([
      "certified-word",
      "type1",
      "2.1.0",
    ]);
    expect(RunManifestSchema.safeParse(found.manifest).success).toBe(true);
    const asV5 = { ...found.manifest, schemaVersion: "5.0.0" };
    expect(RunManifestV5Schema.safeParse(asV5).success).toBe(false);
    const asV5Contract = { ...asV5, ingestion: { ...ingestion, contractVersion: "2.0.0" } };
    expect(RunManifestV5Schema.safeParse(asV5Contract).success).toBe(false);
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

// The rules between fields each version held, held by its frozen copy too: a copy that lost a
// refinement would read rows its version refused.
describe("the rules between a manifest's fields, in every version that has them", () => {
  const at = (file: string): Record<string, unknown> => {
    const found = emitted().find((entry) => entry.file === file);
    if (found === undefined) throw new Error(`no ${file}`);
    return found.manifest;
  };
  const AUTHORITY = {
    importerVersion: "2.2.0",
    fetched: [1, 2].map((index) => ({
      url: `https://epi.example/${String(index)}`,
      sha256: String(index).repeat(64),
      byteLength: 1,
      fetchedAt: "2026-09-28T00:00:00Z",
    })),
  };

  it.each(["1.1.0", "2.0.0", "3.0.0", "4.0.0", "5.0.0", RUN_MANIFEST_VERSION])(
    "%s: a document run, and only one, carries an ingestion block",
    (version) => {
      const schema = BY_VERSION[version];
      if (schema === undefined) throw new Error(`no schema for ${version}`);
      const document = at(`${version}-document.json`);
      const fixture = at(`${version}-fixture.json`);
      const without = Object.fromEntries(
        Object.entries(document).filter(([key]) => key !== "ingestion"),
      );
      expect(schema.safeParse(without).success).toBe(false);
      expect(schema.safeParse({ ...fixture, ingestion: document.ingestion }).success).toBe(false);
    },
  );

  it.each(["2.0.0", "3.0.0", "4.0.0", "5.0.0", RUN_MANIFEST_VERSION])(
    "%s: an authority import, and only one, records what Zone B fetched",
    (version) => {
      const schema = BY_VERSION[version];
      if (schema === undefined) throw new Error(`no schema for ${version}`);
      const document = at(`${version}-document.json`);
      const drawn = document.ingestion as Record<string, unknown>;
      const refusal = (ingestion: Record<string, unknown>): string[] => {
        const parsed = schema.safeParse({ ...document, ingestion });
        return parsed.success ? [] : parsed.error.issues.map(({ message }) => message);
      };
      const RULE = "an authority import, and only one, records what Zone B fetched";
      expect(refusal(drawn)).toEqual([]);
      // A drawn source that records a fetch, and (review of #148, round 2, L-3) an authority
      // import that records none: each refused by the rule, and by nothing else.
      expect(refusal({ ...drawn, authority: AUTHORITY })).toEqual([RULE]);
      expect(refusal({ ...drawn, sourceKind: "authority-publication" })).toEqual([RULE]);
      expect(
        refusal({ ...drawn, sourceKind: "authority-publication", authority: AUTHORITY }),
      ).toEqual([]);
    },
  );

  it.each(["4.0.0", "5.0.0", RUN_MANIFEST_VERSION])(
    "%s: the named packages are pinned, each once",
    (version) => {
      const schema = BY_VERSION[version];
      if (schema === undefined) throw new Error(`no schema for ${version}`);
      const manifest = at(`${version}-fixture.json`);
      const standards = manifest.standards as { packages: { package: string }[] };
      const unpinned = standards.packages.filter(({ package: ref }) => !ref.startsWith("EUePI#"));
      const twice = [...standards.packages, ...standards.packages.slice(0, 1)];
      for (const packages of [unpinned, twice]) {
        expect(
          schema.safeParse({ ...manifest, standards: { ...standards, packages } }).success,
        ).toBe(false);
      }
    },
  );
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
