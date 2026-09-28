import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { SPOR_ORGANISATIONS as AUTHORITY_SPOR } from "../src/authority/shape.js";
import { GLOBAL_EPI_PROFILE_BASE, SPOR_ORGANISATIONS } from "../src/fhir/standards.js";

// src/authority/ keeps its own copies of these constants, because its code is hashed by the
// importer lock; they must stay equal to the shared ones until the next importer version imports
// them from src/fhir/standards.ts.
describe("the shared identifier systems and profile base", () => {
  it("are the ones the authority importer declares", () => {
    expect(AUTHORITY_SPOR).toBe(SPOR_ORGANISATIONS);
    expect(readFileSync("src/authority/import.ts", "utf8")).toContain(
      `const GLOBAL_EPI_PROFILE_BASE =\n  "${GLOBAL_EPI_PROFILE_BASE}";`,
    );
  });

  it("are declared once outside the importer", () => {
    for (const file of [
      "src/pipeline.ts",
      "src/fhir/transform.ts",
      "src/fhir/provenance.ts",
      "src/fixtures/synthetic.ts",
    ]) {
      const source = readFileSync(file, "utf8");
      expect(source, file).not.toContain(`"${SPOR_ORGANISATIONS}"`);
      expect(source, file).not.toContain(`"${GLOBAL_EPI_PROFILE_BASE}`);
    }
  });
});
