import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { FAILURE_REASONS, pipelineFailure } from "../src/app.js";
import { OfficialValidatorError } from "../src/fhir/official-validator.js";
import { TransformationError } from "../src/fhir/transform.js";
import { HealthcareApiError } from "../src/gcp/healthcare.js";

// The run API answers a failure with a closed reason code, keyed by the exact message this
// service threw. A message with no entry answered `unclassified`, which is how a crosswalk
// refusal, a lineage failure and a missing-id failure all looked the same to an operator.

const SOURCES = [
  "src/pipeline.ts",
  "src/app.ts",
  "src/fhir/official-validator.ts",
  ...readdirSync("src/gcp")
    .filter((name) => name.endsWith(".ts"))
    .map((name) => path.join("src/gcp", name)),
];

// `throw new Error(` followed by anything: a string literal is checked below, and anything else
// (a template or a variable) could never match an entry, so it is refused outright.
function thrownErrors(file: string): { literal?: string; at: string }[] {
  const source = readFileSync(file, "utf8");
  return [...source.matchAll(/throw new Error\(\s*/g)].map((match) => {
    const rest = source.slice(match.index + match[0].length);
    const literal = /^"((?:[^"\\]|\\.)*)"\s*[,)]/.exec(rest)?.[1];
    return {
      ...(literal === undefined ? {} : { literal: JSON.parse(`"${literal}"`) as string }),
      at: `${file}:${source.slice(0, match.index).split("\n").length.toString()}`,
    };
  });
}

describe("the run API's failure reasons", () => {
  it("name every Error message thrown in the pipeline, its Google clients and the app", () => {
    const thrown = SOURCES.flatMap(thrownErrors);
    expect(thrown.length).toBeGreaterThan(20);
    for (const { literal, at } of thrown) {
      expect(literal, `${at} throws something other than a string literal`).toBeDefined();
      expect(FAILURE_REASONS[literal ?? ""], `${at}: ${literal ?? ""}`).toBeDefined();
    }
  });

  it("classifies the typed errors by type, and answers the caller's faults with 4xx", () => {
    expect(pipelineFailure(new TransformationError("refused", ["x"]))).toEqual({
      reason: "crosswalk-refused",
      status: 422,
    });
    expect(pipelineFailure(new HealthcareApiError("execute-bundle", 503, [], "0"))).toEqual({
      reason: "healthcare-execute-bundle-refused",
      status: 500,
    });
    expect(pipelineFailure(new OfficialValidatorError(502, "0"))).toEqual({
      reason: "official-validator-refused",
      status: 500,
    });
    expect(pipelineFailure(new Error("Run evidence already exists for this run id"))).toEqual({
      reason: "run-id-reused",
      status: 409,
    });
    expect(pipelineFailure(new Error("an upstream body's words"))).toEqual({
      reason: "unclassified",
      status: 500,
    });
  });

  it("are closed codes, never prose", () => {
    for (const reason of Object.values(FAILURE_REASONS)) {
      expect(reason).toMatch(/^[a-z][a-z0-9-]*$/);
    }
  });
});
