import { readFile } from "node:fs/promises";
import path from "node:path";

import { describe, expect, it } from "vitest";
import { z } from "zod";

import { CONTRACTS, contractId, type ContractDefinition } from "../../src/contracts/index.js";
import {
  REFINEMENTS,
  asciiDigits,
  contractJsonSchema,
  publishedSchema,
  refinedSchemaIds,
} from "../../src/contracts/json-schema.js";
import { sha256 } from "../../src/lib/hash.js";

const GENERATED = path.resolve("contracts/generated");
const DRAFT = "https://json-schema.org/draft/2020-12/schema";

type JsonObject = Record<string, unknown>;

type ContractIndexEntry = {
  name: string;
  version: string;
  $id: string;
  file: string;
  sha256: string;
};

type ContractIndex = {
  generator: string;
  contracts: ContractIndexEntry[];
};

async function readJson<T>(file: string): Promise<T> {
  const text = await readFile(path.join(GENERATED, file), "utf8");
  return JSON.parse(text) as T;
}

// The call scripts/contracts/generate-schemas.ts makes (src/contracts/json-schema.ts).
function generate(contract: ContractDefinition, io: "input" | "output"): JsonObject {
  return contractJsonSchema(contract.schema, io);
}

// What scripts/contracts/generate-schemas.ts writes: the same pure call, not a copy of it.
function document(contract: ContractDefinition): JsonObject {
  return publishedSchema(contract);
}

function structure(value: JsonObject): JsonObject {
  return Object.fromEntries(
    Object.entries(value).filter(([key]) => key !== "$schema" && key !== "$id"),
  );
}

describe("generated contract JSON Schemas", () => {
  it("publishes one schema per contract root", () => {
    expect(CONTRACTS.map(({ name }) => name)).toEqual([
      "canonical-submission",
      "ingestion-provenance",
      "fidelity-report",
      "source-document-text",
      "run-request",
      "query-tools",
      "agent-turn",
      "run-manifest",
    ]);
  });

  for (const contract of CONTRACTS) {
    it(`reproduces the checked-in ${contract.name} schema`, async () => {
      const file = await readJson<JsonObject>(`${contract.name}.schema.json`);

      expect(structure(file)).toEqual(structure(document(contract)));
      expect(file.$id).toBe(contractId(contract.name, contract.version));
      expect(file.$schema).toBe(DRAFT);
    });

    it(`generates ${contract.name} identically for input and output`, () => {
      // Transforms, defaults, and pipes would make the two directions differ; contracts use none,
      // so a Zone A writer and a Zone B reader see the same schema.
      expect(generate(contract, "output")).toEqual(generate(contract, "input"));
    });

    it(`generates ${contract.name} byte-identically twice`, () => {
      expect(JSON.stringify(document(contract))).toBe(JSON.stringify(document(contract)));
    });
  }

  it("indexes every contract with the sha256 of the generated document", async () => {
    const index = await readJson<ContractIndex>("index.json");

    expect(index.generator).toBe("scripts/contracts/generate-schemas.ts");
    expect(index.contracts).toHaveLength(CONTRACTS.length);
    expect(index.contracts).toEqual(
      CONTRACTS.map((contract) => ({
        name: contract.name,
        version: contract.version,
        $id: contractId(contract.name, contract.version),
        file: `${contract.name}.schema.json`,
        sha256: sha256(document(contract)),
      })),
    );
  });

  // Zone A's models validate with Rust's regex crate, where \d is any Unicode decimal digit; the
  // schema's ECMA-262 dialect and Zod mean [0-9] (audit B07 follow-up, Low-2).
  it("publishes no shorthand character class in any pattern", async () => {
    const patterns: string[] = [];
    const walk = (value: unknown): void => {
      if (Array.isArray(value)) value.forEach(walk);
      else if (value !== null && typeof value === "object") {
        for (const [key, inner] of Object.entries(value)) {
          if (key === "pattern" && typeof inner === "string") patterns.push(inner);
          else walk(inner);
        }
      }
    };
    for (const contract of CONTRACTS)
      walk(await readJson<JsonObject>(`${contract.name}.schema.json`));
    expect(patterns.length).toBeGreaterThan(20);
    for (const pattern of patterns)
      expect([pattern, /\\[dDwWsSbB]/.test(pattern)]).toEqual([pattern, false]);
  });

  // Zod emits a string with two .regex() calls as allOf: [{ pattern }, { pattern }]; the rewrite
  // and the refusal reach every pattern at any depth (review L2-b).
  it("rewrites and refuses the patterns of an allOf, nested in an object", () => {
    const refused = z.strictObject({ a: z.string().regex(/^\d+$/).regex(/^\w+$/) });
    expect(() => contractJsonSchema(refused, "input")).toThrow(/uses \\w, which Zone A's regex/);

    const digits = z.strictObject({
      a: z
        .string()
        .regex(/^\d+$/)
        .regex(/^[a-z0-9]+\d$/),
    });
    const published = JSON.stringify(contractJsonSchema(digits, "input"));
    expect(published).toContain('"allOf"');
    expect(published).toContain('"pattern":"^[0-9]+$"');
    expect(published).toContain('"pattern":"^[a-z0-9]+[0-9]$"');
    expect(published).not.toMatch(/\\\\d/);
  });

  it("rewrites \\d to the ASCII class, inside and outside a class, and refuses other shorthands", () => {
    expect(asciiDigits(String.raw`^\d{4}-[12]\d:[\d.]+\\d$`)).toBe(
      String.raw`^[0-9]{4}-[12][0-9]:[0-9.]+\\d$`,
    );
    expect(asciiDigits(String.raw`^a\.b\[c\]$`)).toBe(String.raw`^a\.b\[c\]$`);
    for (const shorthand of ["w", "W", "s", "S", "b", "B", "D"]) {
      expect(() => asciiDigits(`^a\\${shorthand}$`)).toThrow(/reads as Unicode/);
    }
  });

  // z.toJSONSchema drops every .refine and .superRefine silently (audit C-8). Each is listed, and
  // the ones JSON Schema can state are published.
  it("lists every refinement a published contract carries, and no other", () => {
    const carried = new Set(CONTRACTS.flatMap(({ schema }) => refinedSchemaIds(schema)));
    expect([...carried].sort()).toEqual(Object.keys(REFINEMENTS).sort());
  });

  it("refuses to publish a refinement it does not list, or one on a schema without an id", () => {
    const unlisted = z
      .strictObject({ a: z.number(), b: z.number() })
      .refine(({ a, b }) => a < b)
      .meta({ id: "UnlistedRefinement" });
    expect(() => contractJsonSchema(unlisted, "input")).toThrow(
      /UnlistedRefinement carries a refinement/,
    );
    const anonymous = z.strictObject({ a: z.number() }).refine(({ a }) => a > 0);
    expect(() => contractJsonSchema(z.strictObject({ inner: anonymous }), "input")).toThrow(
      /no id/,
    );
  });

  it("publishes the run manifest's two rules between fields", async () => {
    const manifest = await readJson<JsonObject>("run-manifest.schema.json");
    const definitions = manifest.$defs as Record<string, JsonObject>;
    for (const id of ["RunManifest", "IngestionEvidence"]) {
      const disposition = REFINEMENTS[id];
      expect(disposition !== undefined && "expressed" in disposition).toBe(true);
      if (disposition === undefined || !("expressed" in disposition)) continue;
      expect(definitions[id]).toMatchObject(disposition.expressed);
    }
  });

  it("hashes the document including its $id wrapper", async () => {
    const index = await readJson<ContractIndex>("index.json");

    for (const entry of index.contracts) {
      const file = await readJson<JsonObject>(entry.file);
      expect(sha256(file)).toBe(entry.sha256);
      expect(sha256(structure(file))).not.toBe(entry.sha256);
    }
  });
});
