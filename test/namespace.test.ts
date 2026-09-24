import { beforeAll, describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import type { EmaMapping } from "../src/fhir/mapping.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { TransformationError, transformType2ToEma } from "../src/fhir/transform.js";
import type { FhirBundle } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { buildPersistTransaction } from "../src/gcp/healthcare.js";
import { runPipeline } from "../src/pipeline.js";

// Every id a run persists, and every reference in what it persists, derives from its source
// Bundle's identifier value, so a run can write only into its own namespace
// (docs/design/authority-import-contract.md, D7).

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

function withIdentifier(bundle: FhirBundle, value: string): FhirBundle {
  return { ...structuredClone(bundle), identifier: { ...bundle.identifier, value } };
}

function references(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(references);
  if (value === null || typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    key === "reference" && typeof child === "string" ? [child] : references(child),
  );
}

function persisted(bundle: FhirBundle): { urls: Set<string>; refs: Set<string> } {
  const ema = transformType2ToEma(bundle, mapping);
  const transaction = buildPersistTransaction(ema.list, ema.documentBundle, "run");
  const resources = transaction.entry.map(({ resource }) => resource);
  return {
    urls: new Set(transaction.entry.map(({ request }) => request.url)),
    refs: new Set(resources.flatMap(references)),
  };
}

describe("run namespaces", () => {
  it("gives every copied entry an id and fullUrl derived from the identifier value", () => {
    const source = createSyntheticType2Bundle(mapping);
    const ema = transformType2ToEma(source, mapping);
    const sourceIds = new Set(source.entry.map(({ resource }) => resource.id));
    const sourceUrls = new Set(source.entry.map(({ fullUrl }) => fullUrl));

    for (const { fullUrl, resource } of ema.documentBundle.entry) {
      expect(sourceIds.has(resource.id)).toBe(false);
      expect(sourceUrls.has(fullUrl)).toBe(false);
      expect(fullUrl).toBe(`urn:uuid:${resource.id ?? ""}`);
    }
    // References between entries follow them: every one names an entry of the output Bundle.
    const outputUrls = new Set(ema.documentBundle.entry.map(({ fullUrl }) => fullUrl));
    for (const reference of references(ema.documentBundle.entry)) {
      expect(outputUrls.has(reference)).toBe(true);
    }
  });

  it("keeps two runs' persisted ids and references apart when they reuse each other's ids", () => {
    const first = createSyntheticType2Bundle(mapping);
    // Same entry ids, fullUrls and references; only the identifier value differs.
    const second = withIdentifier(first, "synthetic-another-document");
    const a = persisted(first);
    const b = persisted(second);

    expect([...a.urls].filter((url) => b.urls.has(url))).toEqual([]);
    expect([...a.refs].filter((ref) => b.refs.has(ref))).toEqual([]);
  });

  it("refuses a reference that names no entry of the Bundle", () => {
    const source = createSyntheticType2Bundle(mapping);
    const organization = source.entry.find(
      ({ resource }) => resource.resourceType === "RegulatedAuthorization",
    );
    if (organization === undefined) throw new Error("fixture has a RegulatedAuthorization");
    organization.resource.holder = { reference: "Organization/another-runs-organization" };

    expect(() => transformType2ToEma(source, mapping)).toThrow(TransformationError);
  });

  it("refuses a source without an identifier value", () => {
    const source = createSyntheticType2Bundle(mapping);
    source.identifier = { system: "https://khs.dev/fhir/identifier/epi" };

    expect(() => transformType2ToEma(source, mapping)).toThrow(/no identifier value/);
  });

  it("refuses the authority-import namespace on every route the pipeline has today", async () => {
    const config = loadConfig({ DRY_RUN: "true", ALLOW_SYNTHETIC_SOURCES: "true" });
    const source = withIdentifier(
      createSyntheticType2Bundle(mapping),
      "authority-import:ema:00000000-0000-4000-8000-000000000001",
    );

    await expect(
      runPipeline(
        {
          runId: "00000000-0000-4000-8000-00000000000a",
          source,
          sourceKind: "fixture",
          sourceResource: "fixture:test",
        },
        mapping,
        config,
      ),
    ).rejects.toThrow("Source identifier is in the reserved authority-import namespace");
  });
});
