import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { importerVectors, labelSectionVectors } from "../../src/authority/vectors.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { VECTORS } from "../../scripts/authority/lock-hashes.js";

// The importer's golden vectors (docs/design/authority-import-contract.md, D10) are what this
// importer makes: `npm run contracts:check` regenerates and compares them, and this holds the suite
// to the same, so a change of outcome fails here as well as in the lock.

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

const committed = JSON.parse(readFileSync(VECTORS, "utf8")) as {
  imports: unknown;
  sections: unknown;
};

describe("the importer's vectors", () => {
  it("record each import's outcome, the EMA-shaped one refused only at rendering", () => {
    const imports = importerVectors(mapping);
    expect(imports).toEqual(committed.imports);
    expect(imports.find(({ name }) => name === "ema-shaped")?.outcome).toEqual({
      refused: "rendering: renderer-evidence-missing",
    });
  });

  it("record T's outcome for every section of every pinned label", () => {
    expect(labelSectionVectors()).toEqual(committed.sections);
  });
});
