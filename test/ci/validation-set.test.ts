import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import type { FhirResource } from "../../src/fhir/types.js";
import { SMOKE_PRODUCT_ID } from "../../src/fixtures/synthetic-products.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";
import { officialValidationTargets } from "../../src/pipeline.js";

// The set CI's "Official validation" job validates (scripts/ci/emit-validation-set.ts) is the
// pipeline's own list of targets (officialValidationTargets), for each of its two cases, and not a
// copy of it: until audit B15 the script restated the profile and the four resources by hand, so
// a resource the pipeline began validating would have left the gate green on the old set.

const TSX = path.resolve("node_modules/.bin/tsx");
const SCRIPT = path.resolve("scripts/ci/emit-validation-set.ts");

type Entry = { file: string; resourceType: string; profiles: string[] };

let mapping: EmaMapping;
let output: string;
let entries: Entry[];

beforeAll(async () => {
  mapping = await loadEmaMapping();
  output = mkdtempSync(path.join(tmpdir(), "validation-set-"));
  const run = spawnSync(TSX, [SCRIPT, output], { encoding: "utf8", timeout: 90_000 });
  expect(run.error).toBeUndefined();
  expect(run.status, run.stderr).toBe(0);
  entries = JSON.parse(readFileSync(path.join(output, "validation-set.json"), "utf8")) as Entry[];
}, 120_000);

afterAll(() => {
  rmSync(output, { recursive: true, force: true });
});

describe("the official validation set", () => {
  it("is the pipeline's targets for a fixture run, resource for resource", () => {
    const source = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
    const targets = officialValidationTargets(
      source,
      transformType2ToEma(source, mapping),
      mapping,
    );
    const fixture = entries.slice(0, targets.length);
    expect(fixture).toEqual(
      targets.map(({ name, resource, profiles }) => ({
        file: name === "source" ? "source-type2.json" : `${name}.json`,
        resourceType: resource.resourceType,
        profiles,
      })),
    );
    for (const [index, { resource }] of targets.entries()) {
      const file = fixture[index]?.file ?? "";
      const emitted = JSON.parse(readFileSync(path.join(output, file), "utf8")) as FhirResource;
      expect([file, emitted]).toEqual([file, resource]);
    }
  });

  it("is the same targets for an authority import's Type 1 record", () => {
    const source = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
    const targets = officialValidationTargets(
      source,
      transformType2ToEma(source, mapping),
      mapping,
    );
    expect(entries.slice(targets.length, 2 * targets.length)).toEqual(
      targets.map(({ name, resource, profiles }) => ({
        file: name === "source" ? "source-type1.json" : `${name}-type1.json`,
        resourceType: resource.resourceType,
        profiles,
      })),
    );
  });

  it("adds the published artifacts, against no profile, and nothing else", () => {
    const artifacts = readdirSync("fhir/generated")
      .filter((file) => file.endsWith(".json"))
      .sort();
    expect(entries.slice(8).map(({ file, profiles }) => ({ file, profiles }))).toEqual(
      artifacts.map((file) => ({ file, profiles: [] })),
    );
    expect(entries).toHaveLength(8 + artifacts.length);
  });

  it("restates no profile of its own", () => {
    expect(readFileSync(SCRIPT, "utf8")).not.toMatch(/StructureDefinition\//);
  });
});
