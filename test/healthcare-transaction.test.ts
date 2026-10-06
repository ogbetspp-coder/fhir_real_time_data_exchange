import { describe, expect, it } from "vitest";

import { loadEmaMapping } from "../src/fhir/mapping.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import type { FhirResource } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { buildPersistTransaction, persistedVersion } from "../src/gcp/healthcare.js";
import { SYNTHETIC_PRODUCT_IDS, SYNTHETIC_VERSIONS } from "../src/fixtures/synthetic-products.js";

// The Cloud Healthcare API refused every transaction this pipeline ever sent, with
// `invalid_full_url` on `urn:uuid:synthetic-pharma`. `urn:uuid:` is only well formed when what
// follows is an RFC 4122 UUID, and most of these resource ids are readable identifiers instead.
// The refusal was atomic, so the store stayed empty and the defect stayed invisible behind an
// earlier validation failure. These tests pin the shape of the transaction so it cannot return.

const RUN_ID = "00000000-0000-4000-8000-000000000001";

function references(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(references);
  if (value === null || typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    key === "reference" && typeof child === "string" ? [child] : references(child),
  );
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function transactionFor(
  product: (typeof SYNTHETIC_PRODUCT_IDS)[number],
  version: (typeof SYNTHETIC_VERSIONS)[number],
) {
  const mapping = await loadEmaMapping();
  const source = createSyntheticType2Bundle(mapping, { product, version });
  const target = transformType2ToEma(source, mapping);
  return buildPersistTransaction(target.list, target.documentBundle, RUN_ID, "absent");
}

describe("the transaction a run persists", () => {
  it("names no entry with a urn:uuid it cannot honour", async () => {
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      for (const version of SYNTHETIC_VERSIONS) {
        const transaction = await transactionFor(product, version);
        for (const entry of transaction.entry) {
          const fullUrl = (entry as { fullUrl?: string }).fullUrl;
          if (fullUrl === undefined) continue;
          // If a fullUrl is ever reintroduced it must be one the server can parse: a urn:uuid
          // whose suffix is a real UUID, or an absolute http(s) URL.
          if (fullUrl.startsWith("urn:uuid:")) {
            expect(UUID.test(fullUrl.slice("urn:uuid:".length))).toBe(true);
          } else {
            expect(fullUrl).toMatch(/^https?:\/\//);
          }
        }
      }
    }
  });

  it("addresses every entry by resource type and id, so a rerun updates rather than duplicates", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const transaction = buildPersistTransaction(
      target.list,
      target.documentBundle,
      RUN_ID,
      "absent",
    );

    expect(transaction.type).toBe("transaction");
    expect(transaction.entry.length).toBeGreaterThan(0);
    for (const { request, resource } of transaction.entry) {
      expect(request.method).toBe("PUT");
      expect(request.url).toBe(`${resource.resourceType}/${resource.id}`);
    }
  });

  it("carries the List, every document entry, and the document Bundle itself", async () => {
    const mapping = await loadEmaMapping();
    const source = createSyntheticType2Bundle(mapping);
    const target = transformType2ToEma(source, mapping);
    const transaction = buildPersistTransaction(
      target.list,
      target.documentBundle,
      RUN_ID,
      "absent",
    );

    expect(transaction.entry).toHaveLength(target.documentBundle.entry.length + 2);
    const urls = transaction.entry.map(({ request }) => request.url).sort();
    expect(urls).toEqual(
      [
        `List/${target.list.id ?? ""}`,
        `Bundle/${target.documentBundle.id ?? ""}`,
        ...target.documentBundle.entry.map(
          ({ resource }) => `${resource.resourceType}/${resource.id ?? ""}`,
        ),
      ].sort(),
    );
  });

  // Every reference names an entry written before it, so none points forward to a resource the
  // server has not yet written: the product graph's leaves, then what references them, the List
  // after the document Bundle, the approval Provenance after the Composition and the Bundle.
  it("orders its entries so every reference names an earlier entry", async () => {
    const mapping = await loadEmaMapping();
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      const target = transformType2ToEma(createSyntheticType2Bundle(mapping, { product }), mapping);
      const provenance: FhirResource = {
        resourceType: "Provenance",
        id: "p1",
        target: [
          { reference: `Composition/${target.documentBundle.entry[0]?.resource.id ?? ""}` },
          { reference: `Bundle/${target.documentBundle.id ?? ""}` },
        ],
      };
      const transaction = buildPersistTransaction(
        target.list,
        target.documentBundle,
        RUN_ID,
        "absent",
        [provenance],
      );
      const written = new Set<string>();
      let checked = 0;
      for (const { request, resource } of transaction.entry) {
        for (const reference of references(resource).filter((value) => !value.startsWith("urn:"))) {
          expect(written.has(reference), `${request.url} -> ${reference}`).toBe(true);
          checked += 1;
        }
        written.add(request.url);
      }
      expect(checked).toBeGreaterThanOrEqual(7);
      expect(transaction.entry.at(-1)?.request.url).toBe("Provenance/p1");
    }
  });

  it("refuses resources that reference each other in a cycle", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const a: FhirResource = {
      resourceType: "Provenance",
      id: "a",
      target: [{ reference: "Provenance/b" }],
    };
    const b: FhirResource = {
      resourceType: "Provenance",
      id: "b",
      target: [{ reference: "Provenance/a" }],
    };
    expect(() =>
      buildPersistTransaction(target.list, target.documentBundle, RUN_ID, "absent", [a, b]),
    ).toThrow("The persisted resources reference each other in a cycle");
  });

  // The profile validators check the List and the Composition as the transform wrote them, with
  // `urn:uuid` references. What is stored differs from that in reference strings only, each
  // naming the same resource: mapping every `Type/id` back to the fullUrl of the entry it names
  // gives back exactly what was validated.
  it("stores each standalone resource as validated, but for the form of its references", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const transaction = buildPersistTransaction(
      target.list,
      target.documentBundle,
      RUN_ID,
      "absent",
    );
    const fullUrlOf = new Map<string, string>([
      [`Bundle/${target.documentBundle.id ?? ""}`, `urn:uuid:${target.documentBundle.id ?? ""}`],
      ...target.documentBundle.entry.map(
        ({ fullUrl, resource }) =>
          [`${resource.resourceType}/${resource.id ?? ""}`, fullUrl] as [string, string],
      ),
    ]);
    const validated = new Map<string, FhirResource>([
      [`List/${target.list.id ?? ""}`, target.list],
      ...target.documentBundle.entry.map(
        ({ resource }) =>
          [`${resource.resourceType}/${resource.id ?? ""}`, resource] as [string, FhirResource],
      ),
    ]);
    const back = (node: unknown): unknown => {
      if (Array.isArray(node)) return node.map(back);
      if (node === null || typeof node !== "object") return node;
      return Object.fromEntries(
        Object.entries(node as Record<string, unknown>).map(([key, child]) => [
          key,
          key === "reference" && typeof child === "string" ? fullUrlOf.get(child) : back(child),
        ]),
      );
    };
    for (const { request, resource } of transaction.entry) {
      if (resource.resourceType === "Bundle") continue;
      expect(back(resource), request.url).toEqual(validated.get(request.url));
    }
  });

  // Every inter-entry reference the transform writes is a `urn:uuid`, which only a document's
  // own entries resolve. Written on their own, the List, the Composition and the product graph
  // held literal `urn:uuid:` references that no join, `_include` or integrity check followed.
  it("resolves every reference in a standalone resource to a resource of the same transaction", async () => {
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      const transaction = await transactionFor(product, 1);
      const urls = new Set(transaction.entry.map(({ request }) => request.url));
      let checked = 0;
      for (const { resource } of transaction.entry) {
        if (resource.resourceType === "Bundle") continue;
        for (const reference of references(resource)) {
          expect(urls.has(reference), reference).toBe(true);
          checked += 1;
        }
      }
      // Composition.subject and author, the authorisation's subject and holder, the
      // ingredient's `for`, the List's entry, and the rest of the graph's edges.
      expect(checked).toBeGreaterThanOrEqual(5);
    }
  });

  it("writes the List's entry as the document Bundle's Type/id", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const transaction = buildPersistTransaction(
      target.list,
      target.documentBundle,
      RUN_ID,
      "absent",
    );

    const list = transaction.entry.find(({ resource }) => resource.resourceType === "List");
    expect(references(list?.resource)).toEqual([`Bundle/${target.documentBundle.id ?? ""}`]);
    // The transform's own List is not changed: what it hashed stays what it was.

    expect(references(target.list)).toEqual([`urn:uuid:${target.documentBundle.id ?? ""}`]);
  });

  it("writes the document Bundle unchanged, its references resolving against its own entries", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const transaction = buildPersistTransaction(
      target.list,
      target.documentBundle,
      RUN_ID,
      "absent",
    );

    const bundle = transaction.entry.find(({ resource }) => resource.resourceType === "Bundle");
    expect(bundle?.resource).toEqual(target.documentBundle);
    const fullUrls = new Set(target.documentBundle.entry.map(({ fullUrl }) => fullUrl));
    for (const reference of references(target.documentBundle)) {
      expect(fullUrls.has(reference), reference).toBe(true);
    }
  });

  it("refuses a standalone reference that names nothing in the transaction", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const stray: FhirResource = {
      resourceType: "Provenance",
      id: "p1",
      target: [{ reference: "Bundle/another-runs-bundle" }],
    };

    expect(() =>
      buildPersistTransaction(target.list, target.documentBundle, RUN_ID, "absent", [stray]),
    ).toThrow("A persisted reference names no resource in the transaction");
  });

  it("keeps a reference that already names a transaction entry by Type/id", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const provenance: FhirResource = {
      resourceType: "Provenance",
      id: "p1",
      target: [{ reference: `Bundle/${target.documentBundle.id ?? ""}` }],
    };
    const transaction = buildPersistTransaction(
      target.list,
      target.documentBundle,
      RUN_ID,
      "absent",
      [provenance],
    );

    expect(transaction.entry.at(-1)?.resource).toEqual(provenance);
  });

  it("is a pure function of its inputs and timestamp, so its hash names it", async () => {
    const mapping = await loadEmaMapping();
    const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
    const at = "2026-09-27T00:00:00.000Z";

    expect(
      buildPersistTransaction(target.list, target.documentBundle, RUN_ID, "absent", [], at),
    ).toEqual(
      buildPersistTransaction(target.list, target.documentBundle, RUN_ID, "absent", [], at),
    );
  });

  it("refuses a resource with no id rather than letting the server allocate one", async () => {
    const mapping = await loadEmaMapping();
    const source = createSyntheticType2Bundle(mapping);
    const target = transformType2ToEma(source, mapping);
    const anonymous = { resourceType: "Organization" } as FhirResource;

    expect(() =>
      buildPersistTransaction(target.list, target.documentBundle, RUN_ID, "absent", [anonymous]),
    ).toThrow(/requires an id/);
  });
});

// The workflow's BigQuery check looks for the version a run wrote. The Bundle id is the same for
// every version of a label, so checking the id alone was satisfied by an earlier version's row.
describe("the version a transaction wrote", () => {
  const BUNDLE = "0c18c50e-0000-5000-a000-000000000000";
  const response = (entries: unknown[]): unknown => ({
    resourceType: "Bundle",
    type: "transaction-response",
    entry: entries,
  });

  it("is read from the document Bundle's entry: its _history location and lastModified", () => {
    expect(
      persistedVersion(
        response([
          { response: { location: "https://h/fhir/List/l1/_history/AAA", lastModified: "x" } },
          {
            response: {
              status: "200 OK",
              location: `https://healthcare.googleapis.com/v1/projects/p/locations/l/datasets/d/fhirStores/s/fhir/Bundle/${BUNDLE}/_history/MTc5MDUyNzYzMTkyMTk4NTAwMA`,
              lastModified: "2026-09-27T16:47:11.921985+00:00",
            },
          },
        ]),
        "Bundle",
        BUNDLE,
      ),
    ).toEqual({
      versionId: "MTc5MDUyNzYzMTkyMTk4NTAwMA",
      lastUpdated: "2026-09-27T16:47:11.921985+00:00",
    });
  });

  it("falls back to the returned resource's meta.lastUpdated, and accepts a relative location", () => {
    expect(
      persistedVersion(
        response([
          {
            response: { location: `Bundle/${BUNDLE}/_history/7` },
            resource: { meta: { lastUpdated: "2026-09-27T00:00:00Z" } },
          },
        ]),
        "Bundle",
        BUNDLE,
      ),
    ).toEqual({ versionId: "7", lastUpdated: "2026-09-27T00:00:00Z" });
  });

  it("is the location's version even when the response gives no time", () => {
    expect(
      persistedVersion(
        response([{ response: { location: `Bundle/${BUNDLE}/_history/1` } }]),
        "Bundle",
        BUNDLE,
      ),
    ).toEqual({ versionId: "1" });
  });

  it.each([
    ["no entries", { resourceType: "Bundle" }],
    ["not an object", null],
    ["no entry for this Bundle", response([{ response: { location: "Bundle/other/_history/1" } }])],
    [
      "an id that only ends with this one",
      response([{ response: { location: `Bundle/x${BUNDLE}/_history/1`, lastModified: "t" } }]),
    ],
    [
      "an empty version",
      response([{ response: { location: `Bundle/${BUNDLE}/_history/`, lastModified: "t" } }]),
    ],
  ])("is undefined for a response with %s", (_name, body) => {
    expect(persistedVersion(body, "Bundle", BUNDLE)).toBeUndefined();
  });
});
