import { z } from "zod";

import { sha256 } from "../lib/hash.js";

// The one way a contract's JSON Schema is generated: scripts/contracts/generate-schemas.ts writes
// what `publishedSchema` returns, and test/contracts/schema-generation.test.ts calls the same
// function rather than a copy of it (audit C-8).
//
// Published patterns use no shorthand character class (audit B07 follow-up, Low-2). JSON Schema
// reads a pattern as an ECMA-262 regular expression, where `\d` is exactly [0-9], and Zod (Zone B)
// agrees; but Pydantic, which Zone A's generated models validate with, compiles patterns with
// Rust's regex crate, where `\d` is any Unicode decimal digit: Zone A accepted a package version
// or a normalisation version written in Arabic-Indic or fullwidth digits that Zone B refuses. The
// contracts' own patterns are written with [0-9]; the one Zod writes itself (the ISO date-time
// pattern of `z.iso.datetime`) is rewritten here to the same ASCII class, which is the same
// language under the schema's own dialect. Any other shorthand (\w, \s, \b and their negations,
// \D) would diverge the same way, so generation refuses it rather than publishing it.

// `\d` as `[0-9]` (or `0-9` inside a class), leaving every other escape as it is.
export function asciiDigits(pattern: string): string {
  let out = "";
  let inClass = false;
  for (let index = 0; index < pattern.length; index += 1) {
    const char = pattern[index] ?? "";
    if (char === "\\") {
      const next = pattern[index + 1] ?? "";
      index += 1;
      if (next === "d") out += inClass ? "0-9" : "[0-9]";
      else if (/[DwWsSbB]/.test(next)) {
        throw new Error(`pattern ${pattern} uses \\${next}, which Zone A's regex reads as Unicode`);
      } else out += `\\${next}`;
      continue;
    }
    if (char === "[" && !inClass) inClass = true;
    else if (char === "]" && inClass) inClass = false;
    out += char;
  }
  return out;
}

// Every `pattern` of the document, at any depth: a string with two `.regex()` calls is emitted as
// `allOf: [{ pattern }, { pattern }]`, which a per-node hook that saw only the string's own node
// missed (audit B07 follow-up, review L2-b).
function asciiPatterns(node: unknown): void {
  if (Array.isArray(node)) {
    for (const item of node) asciiPatterns(item);
    return;
  }
  if (node === null || typeof node !== "object") return;
  const record = node as Record<string, unknown>;
  for (const [key, value] of Object.entries(record)) {
    if (key === "pattern" && typeof value === "string") record[key] = asciiDigits(value);
    else asciiPatterns(value);
  }
}

// Refinements (`.refine`, `.superRefine`). `z.toJSONSchema` drops every one of them silently, so
// a published schema would accept what Zod refuses and say nothing about it (audit C-8). Each
// refinement in a published contract is listed here, by the `id` of the schema that carries it,
// with what the published schema does about it: `expressed`, a JSON Schema fragment merged into
// that schema's `$defs` entry that states the same rule; or `unexpressed`, why JSON Schema cannot
// state it, and where it is enforced instead. Each entry also counts the refinements that schema
// carries (`checks`): an id names a schema, not a rule, so a second `.refine` on a listed schema
// would otherwise be taken as the one already described and dropped as silently as before (review
// of #148, part A L3). Generation refuses a refinement not listed here, a count that differs, a
// refinement on a schema without an id, and a listed one no contract carries. What the count
// cannot see: a rule added inside the body of a refinement already counted (one `superRefine` that
// checks two things counts once). Such a change is a change to that entry's rule and must be
// described here by whoever makes it; review is the only control on it.
export type RefinementDisposition = { checks: number } & (
  { expressed: Record<string, unknown> } | { unexpressed: string }
);

export const REFINEMENTS: Readonly<Record<string, RefinementDisposition>> = {
  CanonicalSubmission: {
    checks: 1,
    unexpressed:
      "Zone B's gate (structuralInvariantIssues): recomputed hashes, fidelity counts, span limits and unique source keys, which JSON Schema cannot compute; and the fields each decision action requires, which it could state but which would change canonical-submission 3.0.0's published language (listed for its next major, 4.0.0).",
  },
  SourceSpan: {
    checks: 1,
    unexpressed: "startOffset < endOffset: JSON Schema cannot compare two fields.",
  },
  QuoteMatch: {
    checks: 1,
    unexpressed: "startOffset < endOffset: JSON Schema cannot compare two fields.",
  },
  ManifestStandards: {
    checks: 1,
    unexpressed:
      "Stated in the ManifestStandards description (STANDARDS_RULES) and enforced by Zone A's VerifiedRunManifest: JSON Schema cannot say that a value is among an array's members, or that an array's members are unique by one field.",
  },
  IngestionEvidence: {
    checks: 1,
    expressed: {
      if: { properties: { sourceKind: { const: "authority-publication" } } },
      then: { required: ["authority"] },
      else: { not: { required: ["authority"] } },
    },
  },
  RunManifest: {
    checks: 1,
    expressed: {
      if: { properties: { source: { properties: { kind: { const: "document" } } } } },
      then: { required: ["ingestion"] },
      else: { not: { required: ["ingestion"] } },
    },
  },
};

type ZodDefinition = {
  type: string;
  checks?: { _zod: { def: { check: string } } }[];
  shape?: Record<string, z.ZodType>;
  catchall?: z.ZodType;
  innerType?: z.ZodType;
  element?: z.ZodType;
  in?: z.ZodType;
  out?: z.ZodType;
  keyType?: z.ZodType;
  valueType?: z.ZodType;
  left?: z.ZodType;
  right?: z.ZodType;
  rest?: z.ZodType | null;
  options?: z.ZodType[];
  items?: z.ZodType[];
  getter?: () => z.ZodType;
};

function definition(schema: z.ZodType): ZodDefinition {
  return (schema as unknown as { _zod: { def: ZodDefinition } })._zod.def;
}

function schemaId(schema: z.ZodType): string | undefined {
  const { id } = (z.globalRegistry.get(schema) ?? {}) as { id?: unknown };
  return typeof id === "string" ? id : undefined;
}

// Every schema in `schema`, each once, found by walking the Zod schema itself through every kind
// of child a contract can contain.
function everySchema(schema: z.ZodType): z.ZodType[] {
  const seen = new Set<z.ZodType>();
  const stack: z.ZodType[] = [schema];
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === undefined || seen.has(current)) continue;
    seen.add(current);
    const def = definition(current);
    const children = [
      ...Object.values(def.shape ?? {}),
      ...(def.options ?? []),
      ...(def.items ?? []),
      ...[def.catchall, def.innerType, def.element, def.in, def.out, def.keyType, def.valueType],
      ...[def.left, def.right, def.rest ?? undefined, def.getter?.()],
    ];
    for (const child of children) if (child !== undefined) stack.push(child);
  }
  return [...seen];
}

// The schemas in `schema` that carry an id, by id: what each `$defs` entry was generated from.
export function namedSchemas(schema: z.ZodType): Map<string, z.ZodType> {
  const named = new Map<string, z.ZodType>();
  for (const current of everySchema(schema)) {
    const id = schemaId(current);
    if (id !== undefined) named.set(id, current);
  }
  return named;
}

// How many refinements the schemas in `schema` carry, by the id of the schema carrying them.
export function refinementCounts(schema: z.ZodType): Map<string, number> {
  const counts = new Map<string, number>();
  for (const current of everySchema(schema)) {
    const def = definition(current);
    const custom = (def.checks ?? []).filter((check) => check._zod.def.check === "custom").length;
    if (custom === 0) continue;
    const id = schemaId(current);
    if (id === undefined) {
      throw new Error(
        `a ${def.type} schema carries a refinement but no id, so it cannot be listed in REFINEMENTS`,
      );
    }
    counts.set(id, (counts.get(id) ?? 0) + custom);
  }
  return counts;
}

// The ids of the schemas in `schema` that carry a refinement.
export function refinedSchemaIds(schema: z.ZodType): string[] {
  return [...refinementCounts(schema).keys()].sort();
}

// Merges each expressed refinement into its schema's `$defs` entry; refuses an unlisted one, and
// a count that differs from the one listed.
function publishRefinements(
  schema: z.ZodType,
  document: Record<string, unknown>,
  refinements: Readonly<Record<string, RefinementDisposition>>,
): void {
  const definitions = (document.$defs ?? {}) as Record<string, Record<string, unknown>>;
  for (const [id, count] of refinementCounts(schema)) {
    const disposition = refinements[id];
    if (disposition === undefined) {
      throw new Error(
        `${id} carries a refinement the published schema would drop silently; list it in REFINEMENTS (src/contracts/json-schema.ts)`,
      );
    }
    if (disposition.checks !== count) {
      throw new Error(
        `${id} carries ${String(count)} refinements and REFINEMENTS describes ${String(disposition.checks)}; describe each (src/contracts/json-schema.ts)`,
      );
    }
    if (!("expressed" in disposition)) continue;
    const target = definitions[id];
    if (target === undefined) throw new Error(`${id} is not a $defs entry of the published schema`);
    for (const key of Object.keys(disposition.expressed)) {
      if (key in target)
        throw new Error(`${id} already has ${key}; the refinement would replace it`);
    }
    Object.assign(target, structuredClone(disposition.expressed));
  }
}

export function contractJsonSchema(
  schema: z.ZodType,
  io: "input" | "output",
  // Tests pass their own; every contract is generated with REFINEMENTS.
  refinements: Readonly<Record<string, RefinementDisposition>> = REFINEMENTS,
): Record<string, unknown> {
  const document = z.toJSONSchema(schema, {
    target: "draft-2020-12",
    io,
    unrepresentable: "throw",
    cycles: "ref",
    reused: "inline",
  });
  asciiPatterns(document);
  publishRefinements(schema, document, refinements);
  return document;
}

// A root contract published into contracts/generated/ (src/contracts/index.ts lists them).
export type ContractDefinition = {
  name: string;
  version: string;
  schema: z.ZodType;
};

export function contractId(name: string, version: string): string {
  return `https://khs.dev/contracts/${name}/${version}/schema.json`;
}

// The document published for a contract: its JSON Schema under its `$id`. Pure, so the generator
// (scripts/contracts/generate-schemas.ts) and the tests build it with the same call.
export function publishedSchema(contract: ContractDefinition): Record<string, unknown> {
  const { $schema, ...rest } = contractJsonSchema(contract.schema, "input");
  return {
    $schema: $schema ?? "https://json-schema.org/draft/2020-12/schema",
    $id: contractId(contract.name, contract.version),
    ...rest,
  };
}

// The hash of what a contract's version names (contracts/versions.lock.json): the published
// document without its `$id`, which only restates the name and the version.
export function structureSha256(document: Record<string, unknown>): string {
  return sha256(Object.fromEntries(Object.entries(document).filter(([key]) => key !== "$id")));
}
