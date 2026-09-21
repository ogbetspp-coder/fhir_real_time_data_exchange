import { describe, expect, it } from "vitest";

import { loadEmaMapping } from "../src/fhir/mapping.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import type { FhirResource } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { buildPersistTransaction } from "../src/gcp/healthcare.js";
import { SYNTHETIC_PRODUCT_IDS, SYNTHETIC_VERSIONS } from "../src/fixtures/synthetic-products.js";

// The Cloud Healthcare API refused every transaction this pipeline ever sent, with
// `invalid_full_url` on `urn:uuid:synthetic-pharma`. `urn:uuid:` is only well formed when what
// follows is an RFC 4122 UUID, and most of these resource ids are readable identifiers instead.
// The refusal was atomic, so the store stayed empty and the defect stayed invisible behind an
// earlier validation failure. These tests pin the shape of the transaction so it cannot return.

const RUN_ID = "00000000-0000-4000-8000-000000000001";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function transactionFor(
  product: (typeof SYNTHETIC_PRODUCT_IDS)[number],
  version: (typeof SYNTHETIC_VERSIONS)[number],
) {
  const mapping = await loadEmaMapping();
  const source = createSyntheticType2Bundle(mapping, { product, version });
  const target = transformType2ToEma(source, mapping);
  return buildPersistTransaction(target.list, target.documentBundle, RUN_ID);
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
    const transaction = buildPersistTransaction(target.list, target.documentBundle, RUN_ID);

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
    const transaction = buildPersistTransaction(target.list, target.documentBundle, RUN_ID);

    expect(transaction.entry).toHaveLength(target.documentBundle.entry.length + 2);
    const types = transaction.entry.map((entry) => entry.resource.resourceType);
    expect(types[0]).toBe("List");
    expect(types.at(-1)).toBe("Bundle");
  });

  it("refuses a resource with no id rather than letting the server allocate one", async () => {
    const mapping = await loadEmaMapping();
    const source = createSyntheticType2Bundle(mapping);
    const target = transformType2ToEma(source, mapping);
    const anonymous = { resourceType: "Organization" } as FhirResource;

    expect(() =>
      buildPersistTransaction(target.list, target.documentBundle, RUN_ID, [anonymous]),
    ).toThrow(/requires an id/);
  });
});
