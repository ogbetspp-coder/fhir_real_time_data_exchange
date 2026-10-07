import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { pinnedValidator } from "../../scripts/fhir/compile-map.mjs";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import type { FhirResource } from "../../src/fhir/types.js";
import {
  SYNTHETIC_PRODUCT_IDS,
  SYNTHETIC_VERSIONS,
} from "../../src/fixtures/synthetic-products.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";

// The synthetic route of administration is SNOMED CT, which the offline validator neither checks
// nor reports (docs/design/terminology-server.md). So it is held here to the coding the Global ePI
// package's own example uses, read from the pinned package the validator sidecar loads, checked
// against its SHA-256. Run in CI's Official validation job (npm run test:official).

const EXAMPLE = "package/example/Bundle-bundlepackageleaflet75type2.json";

function globalEpiPackage(): string {
  const lock = JSON.parse(readFileSync("fhir/standards.lock.json", "utf8")) as {
    artifacts: { package?: string; sha256: string }[];
  };
  const pinned = lock.artifacts.find(
    (artifact) => artifact.package === "hl7.fhir.uv.emedicinal-product-info#1.0.0",
  )?.sha256;
  const file = pinnedValidator().packages.find(
    (each) => createHash("sha256").update(readFileSync(each)).digest("hex") === pinned,
  );
  if (pinned === undefined || file === undefined) {
    throw new Error("the sidecar loads no package with the Global ePI 1.0.0 pin");
  }
  return file;
}

function routes(resources: FhirResource[]): unknown[] {
  return resources
    .filter(({ resourceType }) => resourceType === "AdministrableProductDefinition")
    .flatMap((resource) => resource.routeOfAdministration as { code: { coding: unknown[] } }[])
    .flatMap(({ code }) => code.coding);
}

describe("the synthetic route of administration", () => {
  it("is the Global ePI example's SNOMED CT coding, exactly", async () => {
    const read = spawnSync("tar", ["-xzOf", globalEpiPackage(), EXAMPLE], {
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    });
    if (read.status !== 0) throw new Error(`could not read ${EXAMPLE}`);
    const example = JSON.parse(read.stdout) as { entry: { resource: FhirResource }[] };
    const expected = routes(example.entry.map(({ resource }) => resource));
    expect(expected).toEqual([
      { system: "http://snomed.info/sct", code: "26643006", display: "Oral route" },
    ]);

    const mapping = await loadEmaMapping();
    for (const product of SYNTHETIC_PRODUCT_IDS) {
      for (const version of SYNTHETIC_VERSIONS) {
        const bundle = createSyntheticType2Bundle(mapping, { product, version });
        expect([product, version, routes(bundle.entry.map(({ resource }) => resource))]).toEqual([
          product,
          version,
          expected,
        ]);
      }
    }
  });
});
