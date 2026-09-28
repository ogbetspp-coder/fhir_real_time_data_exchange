import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { loadConfig, type AppConfig } from "../src/config.js";
import { AnyRunManifestSchema, RunManifestSchema } from "../src/contracts/index.js";
import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { pinnedPackage, pinnedPackages, STANDARDS_LOCK } from "../src/fhir/standards-lock.js";
import { QRD_TEMPLATE_VERSION } from "../src/fhir/standards.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { runPipeline } from "../src/pipeline.js";

// Run manifest 4.0.0 (audit B07, S-4): the standards a signed manifest names are the packages
// fhir/standards.lock.json pins, each with the SHA-256 of its tarball, and the validator image's
// digest, not literals and not a free-form environment variable.

type LockArtifact = { name: string; package?: string; url: string; sha256: string };
const lock = JSON.parse(readFileSync(STANDARDS_LOCK, "utf8")) as { artifacts: LockArtifact[] };

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

afterEach(() => {
  vi.unstubAllEnvs();
});

function writeLock(artifacts: unknown[]): string {
  const file = path.join(mkdtempSync(path.join(tmpdir(), "standards-lock-")), "lock.json");
  writeFileSync(file, JSON.stringify({ schemaVersion: "1.0.0", artifacts }));
  return file;
}

async function fixtureManifest() {
  const result = await runPipeline(
    {
      runId: "55555555-5555-4555-a555-555555555555",
      source: createSyntheticType2Bundle(mapping),
      sourceKind: "fixture",
      sourceResource: "fixture:standards",
    },
    mapping,
    config,
  );
  return result.evidence.manifest;
}

describe("the pinned standards", () => {
  it("are every package of the lock, with its hash, in the lock's order", () => {
    expect(pinnedPackages()).toEqual(
      lock.artifacts
        .filter((artifact) => artifact.package !== undefined)
        .map(({ package: ref, sha256 }) => ({ package: ref, sha256 })),
    );
    expect(pinnedPackage(pinnedPackages(), "hl7.fhir.uv.emedicinal-product-info")).toBe(
      "hl7.fhir.uv.emedicinal-product-info#1.0.0",
    );
  });

  it.each([
    [
      "a package pinned twice",
      [
        { name: "a", package: "x.y#1.0.0" },
        { name: "b", package: "x.y#2.0.0" },
      ],
    ],
    ["no package at all", [{ name: "a" }]],
    ["a package without a version", [{ name: "a", package: "x.y" }]],
    [
      "an artifact fetched over plain HTTP",
      [{ name: "a", package: "x.y#1.0.0", url: "http://example.org/p" }],
    ],
    ["an artifact without a SHA-256", [{ name: "a", package: "x.y#1.0.0", sha256: "" }]],
  ])("refuse a lock with %s", (_, artifacts) => {
    const file = writeLock(
      artifacts.map((artifact) => ({
        url: "https://example.org/p",
        sha256: "a".repeat(64),
        ...artifact,
      })),
    );
    expect(() => pinnedPackages(file)).toThrow();
  });

  it("name one package per id, or refuse", () => {
    const packages = [{ package: "x.y#1.0.0", sha256: "a".repeat(64) }];
    expect(() => pinnedPackage(packages, "EUePI")).toThrow(/exactly one EUePI/);
  });
});

describe("a signed run manifest's standards", () => {
  it("name the pinned packages with their hashes, and the validator's image", async () => {
    vi.stubEnv("VALIDATOR_IMAGE_DIGEST", `sha256:${"b".repeat(64)}`);
    const manifest = await fixtureManifest();
    expect(manifest.standards.packages).toEqual(pinnedPackages());
    expect(manifest.standards.globalEpiPackage).toBe("hl7.fhir.uv.emedicinal-product-info#1.0.0");
    expect(manifest.standards.emaPackage).toBe("EUePI#1.0.0");
    expect(manifest.standards.qrdTemplate).toBe(QRD_TEMPLATE_VERSION);
    expect(manifest.runtime.validatorImageDigest).toBe(`sha256:${"b".repeat(64)}`);
    expect(() => RunManifestSchema.parse(manifest)).not.toThrow();
  });

  // Until 4.0.0 a free-form variable named the Global ePI package, unchecked, in every manifest.
  it("are not taken from the environment", async () => {
    vi.stubEnv("GLOBAL_EPI_PACKAGE", "hl7.fhir.uv.emedicinal-product-info#9.9.9");
    const manifest = await fixtureManifest();
    expect(manifest.standards.globalEpiPackage).toBe("hl7.fhir.uv.emedicinal-product-info#1.0.0");
  });

  it("record the QRD version the transform stamped on the Composition", async () => {
    const manifest = await fixtureManifest();
    const { documentBundle } = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    expect(JSON.stringify(documentBundle)).toContain(
      `"valueString":"${manifest.standards.qrdTemplate}"`,
    );
  });

  it("refuse a manifest whose named packages are not among those it pins, or pins none", async () => {
    const manifest = await fixtureManifest();
    const packages = manifest.standards.packages.filter(
      ({ package: ref }) => !ref.startsWith("EUePI#"),
    );
    for (const standards of [
      { ...manifest.standards, packages },
      { ...manifest.standards, packages: [] },
      { ...manifest.standards, globalEpiPackage: "hl7.fhir.uv.emedicinal-product-info#9.9.9" },
      {
        ...manifest.standards,
        packages: [...manifest.standards.packages, manifest.standards.packages[0]],
      },
    ]) {
      expect(RunManifestSchema.safeParse({ ...manifest, standards }).success).toBe(false);
    }
  });

  // 3.0.0 rows stay readable, and a 3.0.0 shape is not accepted as 4.0.0.
  it("leave a version 3.0.0 manifest readable", async () => {
    const manifest = await fixtureManifest();
    const standards: Record<string, unknown> = { ...manifest.standards };
    delete standards.packages;
    const runtime: Record<string, unknown> = { ...manifest.runtime };
    delete runtime.validatorImageDigest;
    const v3 = { ...manifest, schemaVersion: "3.0.0", standards, runtime };
    expect(AnyRunManifestSchema.parse(v3).schemaVersion).toBe("3.0.0");
    expect(RunManifestSchema.safeParse({ ...v3, schemaVersion: "4.0.0" }).success).toBe(false);
    expect(AnyRunManifestSchema.safeParse({ ...manifest, schemaVersion: "3.0.0" }).success).toBe(
      false,
    );
  });
});
