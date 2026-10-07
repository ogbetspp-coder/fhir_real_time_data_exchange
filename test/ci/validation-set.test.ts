import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { CanonicalSubmission } from "../../src/contracts/index.js";
import type { FidelityReport } from "../../src/fidelity/index.js";
import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { toProvenanceResource } from "../../src/fhir/provenance.js";
import { transformType2ToEma } from "../../src/fhir/transform.js";
import type { FhirBundle, FhirResource } from "../../src/fhir/types.js";
import { importCertifiedWord } from "../../src/certified-word/import.js";
import {
  RUN as CERTIFIED_WORD_RUN,
  caseRequest,
  recomputed,
  recomputedCases,
} from "../../src/certified-word/vectors.js";
import { SMOKE_PRODUCT_ID } from "../../src/fixtures/synthetic-products.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";
import { officialValidationTargets } from "../../src/pipeline.js";

// The set CI's "Official validation" job validates (scripts/ci/emit-validation-set.ts) is the
// pipeline's own list of targets (officialValidationTargets), for each of its three cases, and not a
// copy of it: until audit B15 the script restated the profile and the four resources by hand, so
// a resource the pipeline began validating would have left the gate green on the old set. Each
// case's targets end with the Provenance a document run persists, built from the contract
// fixtures.

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

const contract = (name: string): unknown =>
  JSON.parse(readFileSync(path.join("test/fixtures/contracts", name), "utf8"));

afterAll(() => {
  rmSync(output, { recursive: true, force: true });
});

describe("the official validation set", () => {
  it("is the pipeline's targets for a fixture run with an attested Provenance, resource for resource", () => {
    const source = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
    const transformed = transformType2ToEma(source, mapping);
    const provenance = toProvenanceResource(
      contract("canonical-submission.json") as CanonicalSubmission,
      contract("fidelity-report.json") as FidelityReport,
      {
        bundleId: transformed.documentBundle.id ?? "",
        compositionId: transformed.documentBundle.entry[0]?.resource.id ?? "",
      },
    );
    const targets = officialValidationTargets(source, transformed, mapping, provenance);
    expect(targets.map(({ name }) => name)).toEqual([
      "source",
      "ema-list",
      "ema-bundle",
      "ema-composition",
      "provenance",
    ]);
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

  it("is the same targets for an authority import's Type 1 record and its Provenance", () => {
    const source = createSyntheticType2Bundle(mapping, { product: SMOKE_PRODUCT_ID });
    const targets = officialValidationTargets(
      source,
      transformType2ToEma(source, mapping),
      mapping,
      { resourceType: "Provenance" },
    );
    expect(entries.slice(targets.length, 2 * targets.length)).toEqual(
      targets.map(({ name, resource, profiles }) => ({
        file: name === "source" ? "source-type1.json" : `${name}-type1.json`,
        resourceType: resource.resourceType,
        profiles,
      })),
    );
    const provenance = JSON.parse(
      readFileSync(path.join(output, "provenance-type1.json"), "utf8"),
    ) as { activity: { coding: { code: string }[] } };
    expect(provenance.activity.coding[0]?.code).toBe("authority-import");
  });

  // ADR 0006 P4, D1: a certified Word source's record, its titles carried as written.
  it("is the same targets for a certified Word source's Type 1 record and its Provenance", () => {
    const [label] = recomputedCases();
    if (label === undefined) throw new Error("no recomputed Word label");
    const word = importCertifiedWord(
      recomputed(label.name),
      caseRequest(label),
      mapping,
      CERTIFIED_WORD_RUN,
    );
    const source = word.submission.bundle as unknown as FhirBundle;
    const transformed = transformType2ToEma(source, mapping, undefined, "as-written");
    const targets = officialValidationTargets(source, transformed, mapping, {
      resourceType: "Provenance",
    });
    expect(entries.slice(2 * targets.length, 3 * targets.length)).toEqual(
      targets.map(({ name, resource, profiles }) => ({
        file: name === "source" ? "source-certified-word.json" : `${name}-certified-word.json`,
        resourceType: resource.resourceType,
        profiles,
      })),
    );
    const emitted = JSON.parse(
      readFileSync(path.join(output, "source-certified-word.json"), "utf8"),
    ) as FhirResource;
    expect(emitted).toEqual(source);
    const provenance = JSON.parse(
      readFileSync(path.join(output, "provenance-certified-word.json"), "utf8"),
    ) as { activity: { coding: { code: string }[] } };
    expect(provenance.activity.coding[0]?.code).toBe("structuring");
  });

  it("adds the repository's own definitions, against no profile, and nothing else", () => {
    const artifacts = readdirSync("fhir/generated")
      .filter((file) => file.endsWith(".json"))
      .sort();
    expect(entries.slice(15).map(({ file, profiles }) => ({ file, profiles }))).toEqual(
      artifacts.map((file) => ({ file, profiles: [] })),
    );
    expect(entries).toHaveLength(15 + artifacts.length);
  });

  it("restates no profile of its own", () => {
    expect(readFileSync(SCRIPT, "utf8")).not.toMatch(/StructureDefinition\//);
  });
});
