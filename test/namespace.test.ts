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

function issuesOf(action: () => unknown): string[] {
  try {
    action();
  } catch (error) {
    if (error instanceof TransformationError) return error.issues;
    throw error;
  }
  return [];
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

  it("persists no reference but to an entry of the run's own output", () => {
    const source = createSyntheticType2Bundle(mapping);
    const organization = source.entry.find(
      ({ resource }) => resource.resourceType === "Organization",
    );
    if (organization === undefined) throw new Error("fixture has an Organization");
    // A reference in a Composition extension is rewritten like any other.
    const composition = source.entry[0]?.resource;
    if (composition === undefined) throw new Error("fixture has a Composition");
    composition.extension = [
      { url: "https://example.org/ext", valueReference: { reference: organization.fullUrl } },
    ];
    const ema = transformType2ToEma(source, mapping);
    const transaction = buildPersistTransaction(ema.list, ema.documentBundle, "run");
    const outputs = new Set([
      ...ema.documentBundle.entry.map(({ fullUrl }) => fullUrl),
      `urn:uuid:${ema.documentBundle.id ?? ""}`,
    ]);
    for (const { resource } of transaction.entry) {
      for (const reference of references(resource))
        expect(outputs.has(reference), reference).toBe(true);
    }
    const rewritten = ema.documentBundle.entry[0]?.resource.extension as {
      valueReference?: unknown;
    }[];
    expect(outputs.has((rewritten[0]?.valueReference as { reference: string }).reference)).toBe(
      true,
    );
  });

  it("refuses a Bundle element or an entry element it does not carry", () => {
    const signed: FhirBundle & Record<string, unknown> = createSyntheticType2Bundle(mapping);
    signed.signature = { who: { reference: "Organization/another-runs-organization" } };
    expect(issuesOf(() => transformType2ToEma(signed, mapping))).toEqual([
      "Source Bundle carries signature, which the crosswalk does not carry",
    ]);

    const requested = createSyntheticType2Bundle(mapping);
    const entry = requested.entry[1] as unknown as Record<string, unknown>;
    entry.request = { method: "PUT", url: "Organization/another-runs-organization" };
    expect(issuesOf(() => transformType2ToEma(requested, mapping))).toEqual([
      "Source Bundle.entry[1] carries request, which the crosswalk does not carry",
    ]);
  });

  it("refuses two entries with one fullUrl, and an empty identifier value", () => {
    const duplicated = createSyntheticType2Bundle(mapping);
    const [, second, third] = duplicated.entry;
    if (second === undefined || third === undefined) throw new Error("fixture has entries");
    third.fullUrl = second.fullUrl;
    expect(() => transformType2ToEma(duplicated, mapping)).toThrow(/ambiguous/);

    const empty = withIdentifier(createSyntheticType2Bundle(mapping), "");
    expect(() => transformType2ToEma(empty, mapping)).toThrow(/no identifier value/);
  });

  it("refuses the authority-import namespace on the healthcare-api route too", async () => {
    const config = loadConfig({ DRY_RUN: "true", ALLOW_SYNTHETIC_SOURCES: "true" });
    const source = withIdentifier(
      createSyntheticType2Bundle(mapping),
      "authority-import:ema:00000000-0000-4000-8000-000000000001",
    );
    await expect(
      runPipeline(
        {
          runId: "00000000-0000-4000-8000-00000000000b",
          source,
          sourceKind: "healthcare-api",
          sourceResource: "Bundle/synthetic",
        },
        mapping,
        config,
      ),
    ).rejects.toThrow("Source identifier is in the reserved authority-import namespace");
  });

  it("refuses a meta element but the store's own and the profiles", () => {
    const tagged = createSyntheticType2Bundle(mapping);
    tagged.meta = {
      ...tagged.meta,
      extension: [{ url: "https://example.org/ext", valueReference: { reference: "Binary/x" } }],
    } as never;
    expect(issuesOf(() => transformType2ToEma(tagged, mapping))).toEqual([
      "Source Bundle.meta carries extension, which the crosswalk does not carry",
    ]);

    const composed = createSyntheticType2Bundle(mapping);
    const composition = composed.entry[0]?.resource;
    if (composition === undefined) throw new Error("fixture has a Composition");
    composition.meta = { ...composition.meta, source: "https://example.org/elsewhere" } as never;
    expect(issuesOf(() => transformType2ToEma(composed, mapping))).toEqual([
      "Source Composition.meta carries source, which the crosswalk does not carry",
    ]);

    const stored = createSyntheticType2Bundle(mapping);
    stored.meta = { ...stored.meta, versionId: "3", lastUpdated: "2026-09-24T12:00:00Z" };
    const ema = transformType2ToEma(stored, mapping);
    expect(ema.documentBundle.meta).toEqual({ profile: [mapping.profiles.bundle] });
  });

  it("limits every persisted resource alike: meta, contained resources, implicit rules", () => {
    for (const key of ["tag", "security", "source", "extension", "id"]) {
      const tagged = createSyntheticType2Bundle(mapping);
      tagged.meta = { ...tagged.meta, [key]: [{ code: "x" }] };
      expect(
        issuesOf(() => transformType2ToEma(tagged, mapping)),
        key,
      ).toEqual([`Source Bundle.meta carries ${key}, which the crosswalk does not carry`]);
    }

    const entryMeta = createSyntheticType2Bundle(mapping);
    const copied = entryMeta.entry[2]?.resource;
    if (copied === undefined) throw new Error("fixture has entries");
    copied.meta = { ...copied.meta, source: "https://example.org/elsewhere" } as never;
    expect(issuesOf(() => transformType2ToEma(entryMeta, mapping))).toEqual([
      "Source Bundle.entry[2].meta carries source, which the crosswalk does not carry",
    ]);

    const contained = createSyntheticType2Bundle(mapping);
    const holder = contained.entry[1]?.resource;
    if (holder === undefined) throw new Error("fixture has entries");
    holder.contained = [{ resourceType: "Binary", id: "b" }];
    holder.implicitRules = "https://example.org/rules";
    expect(issuesOf(() => transformType2ToEma(contained, mapping))).toEqual([
      "Source Bundle.entry[1] carries contained, which the crosswalk does not carry",
      "Source Bundle.entry[1] carries implicitRules, which the crosswalk does not carry",
    ]);

    const stored = createSyntheticType2Bundle(mapping);
    const composition = stored.entry[0]?.resource;
    if (composition === undefined) throw new Error("fixture has a Composition");
    composition.meta = {
      ...composition.meta,
      versionId: "2",
      lastUpdated: "2026-09-24T12:00:00Z",
    };
    expect(transformType2ToEma(stored, mapping).documentBundle.entry[0]?.resource.meta).toEqual({
      profile: mapping.profiles.composition,
    });
  });
});
