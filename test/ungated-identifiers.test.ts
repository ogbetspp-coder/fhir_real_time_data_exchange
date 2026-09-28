import { beforeAll, describe, expect, it } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { sourceIdentifierValue, transformType2ToEma } from "../src/fhir/transform.js";
import type { FhirBundle } from "../src/fhir/types.js";
import {
  DEMONSTRATION_PRODUCT_IDS,
  SMOKE_PRODUCT_ID,
  SYNTHETIC_VERSIONS,
  syntheticProduct,
} from "../src/fixtures/synthetic-products.js";
import {
  SEEDED_SOURCE_IDENTIFIER,
  createSyntheticSmpcFromPublishedType2,
  createSyntheticType2Bundle,
} from "../src/fixtures/synthetic.js";

// The ungated run sources (`fixture`, and `healthcare-api` over what scripts/gcp/bootstrap.sh
// seeds) write a version with no Provenance. The transform keys the EMA document Bundle id on
// the source's identifier value, so an ungated source whose default identifier were a
// demonstration label's would write an unapproved version over that label, which the query
// service would then serve with the previous version's approval.

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

// The shape of the HL7 DrugX example the bootstrap reads, reduced to what the seed builder
// touches: the vendored example itself is not committed.
const PUBLISHED: FhirBundle = {
  resourceType: "Bundle",
  id: "drugx",
  identifier: { system: "http://example.org/documents", value: "drugx-example" },
  type: "document",
  timestamp: "2023-01-01T00:00:00Z",
  entry: [
    {
      fullUrl: "http://example.org/Composition/drugx",
      resource: { resourceType: "Composition", id: "drugx", section: [] },
    },
  ],
};

function ungatedIdentifiers(): string[] {
  return [
    sourceIdentifierValue(createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID })),
    sourceIdentifierValue(createSyntheticSmpcFromPublishedType2(PUBLISHED, mapping)),
  ];
}

describe("the ungated sources' default identifiers", () => {
  it("are their own: the healthcare-api seed carries its own identifier", () => {
    expect(ungatedIdentifiers()).toEqual([
      syntheticProduct(SMOKE_PRODUCT_ID).bundleIdentifier,
      SEEDED_SOURCE_IDENTIFIER,
    ]);
  });

  it("never equal a demonstration label's identifier, at any version", () => {
    const demonstrated = new Set(
      DEMONSTRATION_PRODUCT_IDS.flatMap((product) =>
        SYNTHETIC_VERSIONS.map((version) =>
          sourceIdentifierValue(createSyntheticType2Bundle(mapping, { product, version })),
        ),
      ),
    );
    for (const identifier of ungatedIdentifiers()) {
      expect(demonstrated.has(identifier), identifier).toBe(false);
    }
  });

  it("so never map to a demonstration label's EMA document Bundle", () => {
    const demonstrated = new Set(
      DEMONSTRATION_PRODUCT_IDS.map(
        (product) =>
          transformType2ToEma(createSyntheticType2Bundle(mapping, { product }), mapping)
            .documentBundle.id,
      ),
    );
    const smoke = transformType2ToEma(
      createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID }),
      mapping,
    ).documentBundle.id;
    expect(demonstrated.has(smoke)).toBe(false);
  });
});
