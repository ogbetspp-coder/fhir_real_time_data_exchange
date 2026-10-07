import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { importerVectors } from "../../src/certified-word/vectors.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";

// The certified Word importer's golden vectors are what this importer makes: `npm run
// contracts:check` regenerates and compares them, and this holds the suite to the same.

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

describe("the certified Word importer's vectors", () => {
  it("record each import's outcome", () => {
    const committed = JSON.parse(
      readFileSync("test/fixtures/certified-word/vectors.json", "utf8"),
    ) as { imports: { name: string; outcome: unknown }[] };
    const vectors = importerVectors(mapping);
    expect(vectors).toEqual(committed.imports);
    const outcome = (name: string): unknown =>
      vectors.find((vector) => vector.name === name)?.outcome;
    for (const name of ["smpc", "smpc-tracked", "smpc-assigned"]) {
      expect([name, Object.keys(outcome(name) as object)]).toEqual([name, ["imported"]]);
    }
    expect(outcome("pl")).toEqual({ refused: "binding: document-not-carried" });
  });
});
