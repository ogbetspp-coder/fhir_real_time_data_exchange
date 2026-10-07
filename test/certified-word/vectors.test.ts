import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { importerVectors } from "../../src/certified-word/vectors.js";
import { loadEmaMappings, type EmaMapping } from "../../src/fhir/mapping.js";

// The certified Word importer's golden vectors are what this importer makes: `npm run
// contracts:check` regenerates and compares them, and this holds the suite to the same.

let mappings: EmaMapping[];

beforeAll(async () => {
  mappings = await loadEmaMappings();
});

describe("the certified Word importer's vectors", () => {
  it("record each import's outcome", () => {
    const committed = JSON.parse(
      readFileSync("test/fixtures/certified-word/vectors.json", "utf8"),
    ) as { imports: { name: string; outcome: unknown }[] };
    const vectors = importerVectors(mappings);
    expect(vectors).toEqual(committed.imports);
    const outcome = (name: string): unknown =>
      vectors.find((vector) => vector.name === name)?.outcome;
    for (const name of ["smpc", "smpc-tracked", "smpc-assigned", "pl"]) {
      expect([name, Object.keys(outcome(name) as object)]).toEqual([name, ["imported"]]);
    }
    expect(outcome("pl-name-retyped")).toEqual({ refused: "product: name-not-in-section-1" });
    expect(outcome("pl-holder-retyped")).toEqual({ refused: "product: holder-not-in-section-6" });
    expect(outcome("pl-a-number-confirmed")).toEqual({
      refused: "product: eu-numbers-not-in-leaflet",
    });
  });
});
