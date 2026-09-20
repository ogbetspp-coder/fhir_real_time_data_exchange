import { readFile } from "node:fs/promises";
import path from "node:path";

import { describe, expect, it } from "vitest";
import { z } from "zod";

import { CONTRACTS, contractId, type ContractDefinition } from "../../src/contracts/index.js";
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

// The exact call scripts/contracts/generate-schemas.ts makes; any drift here is drift there.
function generate(contract: ContractDefinition, io: "input" | "output"): JsonObject {
  return z.toJSONSchema(contract.schema, {
    target: "draft-2020-12",
    io,
    unrepresentable: "throw",
    cycles: "ref",
    reused: "inline",
  });
}

function document(contract: ContractDefinition): JsonObject {
  const { $schema, ...rest } = generate(contract, "input");
  return { $schema: $schema ?? DRAFT, $id: contractId(contract.name, contract.version), ...rest };
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

  it("hashes the document including its $id wrapper", async () => {
    const index = await readJson<ContractIndex>("index.json");

    for (const entry of index.contracts) {
      const file = await readJson<JsonObject>(entry.file);
      expect(sha256(file)).toBe(entry.sha256);
      expect(sha256(structure(file))).not.toBe(entry.sha256);
    }
  });
});
