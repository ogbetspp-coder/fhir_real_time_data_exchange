import { mkdir, readdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

import { format, resolveConfig } from "prettier";

import { CONTRACTS, contractId, type ContractDefinition } from "../../src/contracts/index.js";
import { contractJsonSchema } from "../../src/contracts/json-schema.js";
import { sha256 } from "../../src/lib/hash.js";

// Emits one JSON Schema (draft 2020-12) per contract root plus an index with content hashes.
// Output is Prettier-formatted so `format:check` and `contracts:check` agree byte-for-byte.
// Deterministic by construction: no timestamps, stable key order, pinned zod.

const output = path.resolve("contracts/generated");
await mkdir(output, { recursive: true });
const prettierOptions = { ...(await resolveConfig(output)), parser: "json" as const };

export function buildJsonSchema(contract: ContractDefinition): Record<string, unknown> {
  const generated = contractJsonSchema(contract.schema, "input");
  const { $schema, ...rest } = generated;
  return {
    $schema: $schema ?? "https://json-schema.org/draft/2020-12/schema",
    $id: contractId(contract.name, contract.version),
    ...rest,
  };
}

const index: { name: string; version: string; $id: string; file: string; sha256: string }[] = [];

for (const contract of CONTRACTS) {
  const document = buildJsonSchema(contract);
  const file = `${contract.name}.schema.json`;
  const text = await format(JSON.stringify(document, null, 2), prettierOptions);
  await writeFile(path.join(output, file), text);
  index.push({
    name: contract.name,
    version: contract.version,
    $id: contractId(contract.name, contract.version),
    file,
    sha256: sha256(document),
  });
}

// A renamed or removed contract must not leave its old schema behind.
for (const name of await readdir(output)) {
  if (name.endsWith(".schema.json") && !index.some(({ file }) => file === name)) {
    await unlink(path.join(output, name));
  }
}

await writeFile(
  path.join(output, "index.json"),
  await format(
    JSON.stringify(
      { generator: "scripts/contracts/generate-schemas.ts", contracts: index },
      null,
      2,
    ),
    prettierOptions,
  ),
);

console.log(`Generated ${index.length} contract schemas in ${output}`);
