import { beforeAll, describe, expect, it } from "vitest";

import { importCertifiedWord } from "../../src/certified-word/import.js";
import { RUN, caseRequest, recomputed, recomputedCases } from "../../src/certified-word/vectors.js";
import { FindProductOutputSchema } from "../../src/contracts/query-tools.js";
import { loadEmaMappings } from "../../src/fhir/mapping.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import type { FhirBundle } from "../../src/fhir/types.js";
import {
  LAST_UPDATED,
  PRINCIPAL_A,
  SECTION_KEY,
  VERSION_ID,
  buildQueryStore,
  callTool,
  connectHarness,
  type QueryStore,
  type SeededDocument,
} from "./fixtures.js";

// The query service loads the SmPC's manifest, by which it indexes sections. A stored document of
// another type (a package leaflet, which the worker does not publish: src/pipeline.ts) is not
// found by any tool, never answered in part: verify_quote searched none of its sections and
// answered `no-match`, and find_product listed it with no sections.

let store: QueryStore;
let leafletId: string;
let leaflet: SeededDocument;

beforeAll(async () => {
  const mappings = await loadEmaMappings();
  store = buildQueryStore(mappings.smpc);
  const found = recomputedCases().find(({ name }) => name === "pl");
  if (found === undefined) throw new Error("no leaflet");
  const record = importCertifiedWord(recomputed("pl"), caseRequest(found), mappings.pl, RUN)
    .submission.bundle as unknown as FhirBundle;
  const ema = transformType2ToEma(record, mappings.pl, undefined, "as-written").documentBundle;
  leafletId = ema.id ?? "";
  leaflet = {
    bundle: { ...ema, meta: { ...ema.meta, versionId: VERSION_ID, lastUpdated: LAST_UPDATED } },
  };
});

describe("a stored package leaflet, by the SmPC's manifest", () => {
  it("is not found by any tool, and the SmPC beside it is", async () => {
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: [leafletId, store.bundleIdA] },
      documents: new Map([...store.documents, [leafletId, leaflet]]),
    });
    try {
      const quote = "Synthetic text, not for clinical use.";
      for (const [tool, args] of [
        ["get_section", { bundleId: leafletId, sourceKey: "pl.1" }],
        ["get_section", { bundleId: leafletId, sourceKey: SECTION_KEY }],
        ["get_provenance", { bundleId: leafletId }],
        ["verify_quote", { bundleId: leafletId, quote }],
      ] as const) {
        const answer = await callTool(harness, tool, args);
        expect([tool, answer.structured]).toEqual([tool, { tool, error: "document-not-found" }]);
      }
      const products = await callTool(harness, "find_product", { query: "Synthetic" });
      const listed = FindProductOutputSchema.parse(products.structured);
      expect(listed.products.map(({ document }) => document.bundleId)).toEqual([store.bundleIdA]);
      expect(listed.truncated).toBe(false);
      const smpc = await callTool(harness, "get_section", {
        bundleId: store.bundleIdA,
        sourceKey: SECTION_KEY,
      });
      expect(smpc.isError).toBe(false);
    } finally {
      await harness.close();
    }
  });
});
