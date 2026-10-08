import type { z } from "zod";

import {
  CONTRACTS,
  CanonicalSubmissionBase,
  type ContractDefinition,
} from "../../src/contracts/index.js";
import { namedSchemas, publishedSchema } from "../../src/contracts/json-schema.js";

// The contract verdict corpus (audit C-7): values and documents, each with the verdict Zod gives
// it, which every Python reader of the published schemas must reproduce. Zone A's generated
// models (zone-a/tests/test_contract_verdicts.py) and the agent's schema validator
// (agent/tests/test_contract_verdicts.py) read it. It exists because each of them once accepted
// what Zod refuses, by the defaults of its own language: a pattern's `\d` read as any Unicode digit
// and `$` before a final newline (Python `re`, Rust `regex`), a string "1" or `true` taken for an
// integer and `null` for an absent field (pydantic's lax mode).
//
// Primitives: every named string definition with a pattern in any published schema, each from
// one valid example and the same example bent the ways the languages disagree. Documents: the
// exported fixtures with one field changed. A document's verdict is the published language's:
// Zod's schema for the contract, except that a canonical submission is judged by its shape
// (CanonicalSubmissionBase), without the gate's recomputed hashes and counts, which no schema
// can state (src/contracts/json-schema.ts, REFINEMENTS). No mutation here reaches a refinement the
// schema leaves unexpressed; one that did would be a verdict no generated model could reproduce.

type Json = unknown;

// One valid example of each named, patterned string definition. A definition without one fails
// the export, so a new one cannot be published untested.
const EXAMPLES: Readonly<Record<string, string>> = {
  AddressableFhirId: "synthetic-type2-smpc",
  AuthorityId: "00000000-5979-4e74-8000-000000000003",
  CanonicalUri: "https://khs.dev/fhir/identifier",
  ContractVersion: "4.1.0",
  ErrorClassName: "ValidationError",
  FhirId: "synthetic-type2-smpc",
  GitCommit: "0123456789abcdef0123456789abcdef01234567",
  HttpUrl: "https://epi.example:8443/fhir/Bundle/12|2.0",
  ImageDigest: `sha256:${"0123456789abcdef".repeat(4)}`,
  IsoDateTime: "2026-09-28T12:30:05.250+02:00",
  LanguageTag: "en-GB",
  NormalizationVersion: "fidelity-norm/3.4.0",
  PackageRef: "hl7.fhir.uv.extensions.r5#5.3.0",
  PictureReference: "~/_entity/annotation/00000000-5979-4e74-8000-000000000009",
  PrincipalId: "urn:reviewer:synthetic-01",
  RecordRef: "https://records.example/approval?id=12&v=3",
  SectionPath: "Composition.section[0].section[12]",
  Sha256Hex: "0123456789abcdef".repeat(4),
  SourceKey: "smpc.4.2",
  SourcePath: "Composition.section[3].title",
  StorageUri: "gs://synthetic-bucket/path/submission-1.json",
  TargetPath: "Composition.section[0].section[1].code",
  Token: "synthetic-extractor/1.0.0",
  Uuid: "00000000-0000-4000-8000-0000000000aa",
};

const ARABIC_INDIC_ZERO = 0x0660;
const FULLWIDTH_ZERO = 0xff10;

function digitsFrom(value: string, zero: number): string {
  return value.replace(/[0-9]/g, (digit) => String.fromCodePoint(zero + Number(digit)));
}

// The example bent the ways Python's and Rust's readers have disagreed with ECMA-262 and Zod.
function variants(example: string): { variant: string; value: Json }[] {
  const out: { variant: string; value: Json }[] = [
    { variant: "example", value: example },
    { variant: "trailing-newline", value: `${example}\n` },
    { variant: "trailing-crlf", value: `${example}\r\n` },
    { variant: "leading-newline", value: `\n${example}` },
    { variant: "trailing-space", value: `${example} ` },
    { variant: "trailing-line-separator", value: `${example}\u2028` },
    { variant: "trailing-latin-letter", value: `${example}\u00e9` },
    { variant: "empty", value: "" },
    { variant: "number", value: 1 },
    { variant: "boolean", value: true },
    { variant: "null", value: null },
  ];
  if (/[0-9]/.test(example)) {
    out.push({ variant: "arabic-indic-digits", value: digitsFrom(example, ARABIC_INDIC_ZERO) });
    out.push({ variant: "fullwidth-digits", value: digitsFrom(example, FULLWIDTH_ZERO) });
  }
  if (example.toUpperCase() !== example) {
    out.push({ variant: "upper-case", value: example.toUpperCase() });
  }
  if (example.toLowerCase() !== example) {
    out.push({ variant: "lower-case", value: example.toLowerCase() });
  }
  return out;
}

export type PrimitiveVerdict = {
  contract: string;
  definition: string;
  variant: string;
  value: Json;
  accepted: boolean;
};

function patterned(definition: unknown): boolean {
  if (definition === null || typeof definition !== "object") return false;
  const node = definition as { type?: unknown; pattern?: unknown };
  return node.type === "string" && typeof node.pattern === "string";
}

export function primitiveVerdicts(): PrimitiveVerdict[] {
  const out: PrimitiveVerdict[] = [];
  for (const contract of CONTRACTS) {
    const definitions = (publishedSchema(contract).$defs ?? {}) as Record<string, unknown>;
    const named = namedSchemas(contract.schema);
    for (const [definition, node] of Object.entries(definitions).sort(([a], [b]) =>
      a < b ? -1 : 1,
    )) {
      if (!patterned(node)) continue;
      const example = EXAMPLES[definition];
      const schema = named.get(definition);
      if (example === undefined || schema === undefined) {
        throw new Error(`${contract.name}: ${definition} needs an example in contract-verdicts.ts`);
      }
      if (!schema.safeParse(example).success) {
        throw new Error(`${definition}'s example is not one Zod accepts`);
      }
      for (const { variant, value } of variants(example)) {
        out.push({
          contract: contract.name,
          definition,
          variant,
          value,
          accepted: schema.safeParse(value).success,
        });
      }
    }
  }
  return out;
}

// A document with the value at `path` replaced (`value`) or removed (`delete`).
type Mutation = { name: string; path: (string | number)[]; value?: Json; delete?: true };

function mutate(document: Json, { path, value, delete: remove }: Mutation): Json {
  const copy = structuredClone(document);
  let node = copy as Record<string | number, Json>;
  for (const key of path.slice(0, -1)) {
    const next = node[key];
    if (next === null || typeof next !== "object") {
      throw new Error(`no ${path.join(".")} in the document`);
    }
    node = next as Record<string | number, Json>;
  }
  const last = path.at(-1) ?? "";
  if (remove === true) Reflect.deleteProperty(node, last);
  else node[last] = value;
  return copy;
}

// A verdict names its base document by its fixture file under test/fixtures/ and the
// change made to it, which a reader applies to that file, rather than carrying the document.
export type DocumentVerdict = {
  contract: string;
  base: string;
  mutation: string;
  path: (string | number)[];
  value?: Json;
  delete?: true;
  accepted: boolean;
};

export type DocumentBase = {
  contract: string;
  base: string;
  document: Json;
  mutations: Mutation[];
};

function contractSchema(name: string): z.ZodType {
  if (name === "canonical-submission") return CanonicalSubmissionBase;
  const contract = CONTRACTS.find((candidate: ContractDefinition) => candidate.name === name);
  if (contract === undefined) throw new Error(`no contract ${name}`);
  return contract.schema;
}

export function documentVerdicts(bases: DocumentBase[]): DocumentVerdict[] {
  return bases.flatMap(({ contract, base, document, mutations }) => {
    const schema = contractSchema(contract);
    if (!schema.safeParse(document).success) {
      throw new Error(`${contract} ${base} is not a document Zod accepts`);
    }
    return [{ name: "unchanged", path: [] } as Mutation, ...mutations].map((mutation) => {
      const changed = mutation.path.length === 0 ? document : mutate(document, mutation);
      return {
        contract,
        base,
        mutation: mutation.name,
        path: mutation.path,
        ...(mutation.delete === true ? { delete: true as const } : {}),
        ...(mutation.path.length === 0 || mutation.delete === true
          ? {}
          : { value: mutation.value }),
        accepted: schema.safeParse(changed).success,
      };
    });
  });
}

// The changes each document takes: the type coercions pydantic's lax mode made (a string or a
// boolean for an integer, `null` for an absent field), and the grammar cases of the primitives,
// at the fields where each contract carries them.
export function withNewline(value: unknown): string {
  return `${String(value)}\n`;
}

export function arabicIndic(value: unknown): string {
  return digitsFrom(String(value), ARABIC_INDIC_ZERO);
}

export function fullwidth(value: unknown): string {
  return digitsFrom(String(value), FULLWIDTH_ZERO);
}
