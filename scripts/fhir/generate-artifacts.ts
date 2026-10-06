import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { crc32 } from "node:zlib";

import { ApproverRole } from "../../src/contracts/index.js";
import { loadEmaMapping, type SectionRule } from "../../src/fhir/mapping.js";
import {
  ACTIVITY_SYSTEM,
  APPROVAL_CONTENT_EXTENSION_URL,
  APPROVER_ROLE_SYSTEM,
} from "../../src/fhir/provenance.js";

// The repository's own FHIR definitions, and the package that carries them to the official
// validator. src/fhir/transform.ts is the crosswalk; the StructureMap is its executed twin
// (fhir/maps/), which runs through the ConceptMap, and test/official/structuremap-twin.test.ts
// holds the two to the same output. The code systems, value sets and extension define every
// https://khs.dev/fhir/ system and extension the pipeline writes (identifier systems need no
// definition). Every resource is checked by the official HL7 validator in CI
// (scripts/ci/emit-validation-set.ts), and the package is loaded by it there and in the sidecar
// (Dockerfile.validator, a fifth -ig), so what the pipeline writes is validated against them.
//
// The package, PACKAGE_FILE, is built here byte for byte reproducibly: a ustar archive with
// sorted entries, fixed mode, owner and time, in a gzip stream of stored (uncompressed) deflate
// blocks with no time and a fixed OS byte. Stored blocks make the bytes depend on the inputs alone:
// zlib's compressed output depends on its version and build (Node's zlib writes OS byte 19 on
// macOS and 3 on Linux), and the SHA-256 of these bytes is pinned in Dockerfile.validator and
// fhir/standards.lock.json. A change to any resource changes that hash: re-pin both, and move
// PACKAGE_VERSION.

const output = path.resolve("fhir/generated");
await mkdir(output, { recursive: true });
const mapping = await loadEmaMapping();

const PACKAGE_NAME = "dev.khs.fhir.epi";
const PACKAGE_VERSION = "0.2.0";
const PACKAGE_FILE = `${PACKAGE_NAME}.tgz`;
const CANONICAL = "https://khs.dev/fhir";

function flatten(rule: SectionRule): SectionRule[] {
  return [rule, ...(rule.children ?? []).flatMap(flatten)];
}

type Concept = { code: string; display: string; definition?: string; concept?: Concept[] };

const valueSetUrl = (system: string): string => system.replace("/CodeSystem/", "/ValueSet/");

// A code system with every code the pipeline writes in it, and the value set of all its codes.
function codeSystem(
  url: string,
  version: string,
  name: string,
  title: string,
  description: string,
  concept: Concept[],
  hierarchyMeaning?: "part-of",
) {
  const id = url.split("/").at(-1) ?? "";
  const common = { version, status: "active", experimental: true };
  return [
    {
      resourceType: "CodeSystem",
      id,
      url,
      ...common,
      name,
      title,
      description,
      caseSensitive: true,
      valueSet: valueSetUrl(url),
      ...(hierarchyMeaning === undefined ? {} : { hierarchyMeaning }),
      content: "complete",
      concept,
    },
    {
      resourceType: "ValueSet",
      id,
      url: valueSetUrl(url),
      ...common,
      name: `${name}All`,
      title: `${title}: all codes`,
      description: `Every code of ${url}.`,
      compose: { include: [{ system: url, version }] },
    },
  ];
}

// The section tree of the mapping manifest, its keys as codes and its headings as displays.
const sectionConcept = (rule: SectionRule): Concept => ({
  code: rule.sourceKey,
  display: rule.title,
  ...(rule.children === undefined ? {} : { concept: rule.children.map(sectionConcept) }),
});

const terminology = [
  ...codeSystem(
    mapping.sourceCodeSystem,
    mapping.mappingVersion,
    "CanonicalSmpcSections",
    "Canonical SmPC sections",
    "The canonical SmPC section keys of the mapping manifest (fhir/mappings/cap-smpc-en.json), each with its QRD heading, nested as the sections are, and the keys of the EMA profile's slots the crosswalk does not carry (the manifest's unmapped list, each with its reason). A key identifies a section of the canonical record; it is not a clinical concept.",
    [
      sectionConcept(mapping.root),
      ...(mapping.unmapped ?? []).map((slot) => ({
        code: slot.sourceKey,
        display: slot.title,
        definition: `Not carried by the crosswalk (EMA code ${slot.targetCode}): ${slot.reason}`,
      })),
    ],
    "part-of",
  ),
  ...codeSystem(
    `${CANONICAL}/CodeSystem/document-type`,
    PACKAGE_VERSION,
    "DocumentType",
    "Document type",
    "The type of a canonical record's Composition.",
    [{ code: "smpc", display: "Summary of Product Characteristics" }],
  ),
  ...codeSystem(
    ACTIVITY_SYSTEM,
    PACKAGE_VERSION,
    "ProvenanceActivity",
    "Provenance activity",
    "What an ingestion Provenance records (src/fhir/provenance.ts).",
    [
      { code: "structuring", display: "Structuring of a drawn document, attested by an approver" },
      { code: "authority-import", display: "Import of an authority's published ePI" },
    ],
  ),
  ...codeSystem(
    APPROVER_ROLE_SYSTEM,
    PACKAGE_VERSION,
    "ApproverRole",
    "Approver role",
    "The regulatory role of the person who attested an approval (contracts ApproverRole).",
    ApproverRole.options.map((code) => ({
      code,
      display: { "content-reviewer": "Content reviewer", "qa-reviewer": "QA reviewer" }[code],
    })),
  ),
];

const approvalContentExtension = {
  resourceType: "StructureDefinition",
  id: APPROVAL_CONTENT_EXTENSION_URL.split("/").at(-1),
  url: APPROVAL_CONTENT_EXTENSION_URL,
  version: PACKAGE_VERSION,
  name: "ApprovalContentSha256",
  title: "Approved content SHA-256",
  status: "active",
  experimental: true,
  description:
    "The SHA-256 of the content an approval covers (the canonical submission's approval.approvedContentSha256), as 64 lowercase hexadecimal digits.",
  fhirVersion: "5.0.0",
  kind: "complex-type",
  abstract: false,
  context: [{ type: "element", expression: "Provenance" }],
  type: "Extension",
  baseDefinition: "http://hl7.org/fhir/StructureDefinition/Extension",
  derivation: "constraint",
  differential: {
    element: [
      {
        id: "Extension",
        path: "Extension",
        short: "Approved content SHA-256",
        definition:
          "The SHA-256 of the content an approval covers, as 64 lowercase hexadecimal digits.",
        constraint: [
          {
            key: "khs-sha256-1",
            severity: "error",
            human: "The value is 64 lowercase hexadecimal digits",
            expression: "value.matches('^[0-9a-f]{64}$')",
            source: APPROVAL_CONTENT_EXTENSION_URL,
          },
        ],
      },
      { id: "Extension.extension", path: "Extension.extension", max: "0" },
      { id: "Extension.url", path: "Extension.url", fixedUri: APPROVAL_CONTENT_EXTENSION_URL },
      { id: "Extension.value[x]", path: "Extension.value[x]", min: 1, type: [{ code: "string" }] },
    ],
  },
};

const EMA_SMPC_SECTION_CODES = "http://ema.europa.eu/fhir/ValueSet/EUepismpcqrdcodesVs";

const conceptMap = {
  resourceType: "ConceptMap",
  id: "canonical-to-ema-cap-smpc-en",
  url: "https://khs.dev/fhir/ConceptMap/canonical-to-ema-cap-smpc-en",
  version: mapping.mappingVersion,
  name: "CanonicalToEmaCapSmpcEnglish",
  title: "Canonical SmPC sections to EMA CAP SmPC English QRD sections",
  status: "active",
  experimental: true,
  description:
    "Deterministic terminology map. It maps section identifiers only and does not generate or alter regulated narrative. Every section slot of the EMA profile EUQRD-CAP-template-new-SmPC-en has a key here: each of the template's own sections maps equivalent to its EMA code, and a slot the crosswalk does not carry is noMap, its reason in the source code system's definition of the key.",
  // A scope is a value set: R5 types it canonical(ValueSet), and the validator resolves a uri
  // scope as well and refuses a code system there. The source's is the value set of every
  // canonical section key, above; the target's is the EMA IG's SmPC section-code value set, the
  // one EUEpiCompositionSmPC binds Composition.section.code to, which holds every target code.
  sourceScopeCanonical: valueSetUrl(mapping.sourceCodeSystem),
  targetScopeCanonical: EMA_SMPC_SECTION_CODES,
  group: [
    {
      source: mapping.sourceCodeSystem,
      target: mapping.targetCodeSystem,
      element: [
        ...flatten(mapping.root).map((rule) => ({
          code: rule.sourceKey,
          display: rule.title,
          target: [
            {
              code: rule.targetCode,
              display: rule.display ?? rule.title,
              relationship: "equivalent",
            },
          ],
        })),
        ...(mapping.unmapped ?? []).map((slot) => ({
          code: slot.sourceKey,
          display: slot.title,
          noMap: true,
        })),
      ],
    },
  ],
};

// The StructureMap twin as the pinned validator compiled it from fhir/maps/ (scripts/fhir/
// compile-map.mjs, which needs Java; CI's Official validation job compiles it again and fails on
// any difference). Copied as it is, once its version is the mapping's.
const structureMap = JSON.parse(
  await readFile(path.resolve("fhir/maps/StructureMap-type2-to-ema-cap-smpc-en.json"), "utf8"),
) as Record<string, unknown>;
if (structureMap.version !== mapping.mappingVersion) {
  throw new Error(
    `fhir/maps/StructureMap-type2-to-ema-cap-smpc-en.json is at version ${String(structureMap.version)}, the mapping at ${mapping.mappingVersion}: run node scripts/fhir/compile-map.mjs`,
  );
}

const resources: Record<string, unknown>[] = [
  ...terminology,
  approvalContentExtension,
  conceptMap,
  structureMap,
];
const files = new Map<string, Buffer>(
  resources.map((resource) => [
    `${String(resource.resourceType)}-${String(resource.id)}.json`,
    Buffer.from(`${JSON.stringify(resource, null, 2)}\n`),
  ]),
);

// The package: package.json, the index the FHIR package specification describes, and every
// resource above.
const packageJson = {
  name: PACKAGE_NAME,
  version: PACKAGE_VERSION,
  type: "Conformance",
  canonical: CANONICAL,
  title: "ema-flow ePI definitions",
  description:
    "The code systems, value sets, extension, ConceptMap and StructureMap this repository's ePI pipeline writes or publishes (scripts/fhir/generate-artifacts.ts).",
  fhirVersions: ["5.0.0"],
  dependencies: { "hl7.fhir.r5.core": "5.0.0" },
  author: "khs-dev",
};
const index = {
  "index-version": 2,
  files: [...files].map(([filename, bytes]) => {
    const { resourceType, id, url, version, kind, type, derivation } = JSON.parse(
      bytes.toString("utf8"),
    ) as Record<string, unknown>;
    return { filename, resourceType, id, url, version, kind, type, derivation };
  }),
};
const entries = new Map<string, Buffer>([
  ["package/package.json", Buffer.from(`${JSON.stringify(packageJson, null, 2)}\n`)],
  ["package/.index.json", Buffer.from(`${JSON.stringify(index, null, 2)}\n`)],
  ...[...files].map(([file, bytes]): [string, Buffer] => [`package/${file}`, bytes]),
]);

// npm's own fixed time for reproducible packages, 1985-10-26T08:15:00Z: an archive time of 0 makes
// GNU tar warn on extraction.
const MTIME = 499162500;

function octal(value: number, width: number): string {
  return `${value.toString(8).padStart(width - 1, "0")}\0`;
}

// One ustar member: a regular file, mode 0644, owner and group 0 with no names.
function tarMember(name: string, data: Buffer): Buffer {
  if (Buffer.byteLength(name) > 100) throw new Error(`${name} is longer than a ustar name`);
  const header = Buffer.alloc(512);
  header.write(name, 0);
  header.write(octal(0o644, 8), 100);
  header.write(octal(0, 8), 108);
  header.write(octal(0, 8), 116);
  header.write(octal(data.length, 12), 124);
  header.write(octal(MTIME, 12), 136);
  header.write(" ".repeat(8), 148);
  header.write("0", 156);
  header.write("ustar\u000000", 257);
  const checksum = header.reduce((sum, byte) => sum + byte, 0);
  header.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148);
  return Buffer.concat([header, data, Buffer.alloc((512 - (data.length % 512)) % 512)]);
}

// A gzip member of stored deflate blocks: no time (0), no extra flags, OS 255 ("unknown").
function gzipStored(data: Buffer): Buffer {
  const parts: Buffer[] = [Buffer.from([0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 255])];
  for (let at = 0; at < data.length; at += 0xffff) {
    const block = data.subarray(at, at + 0xffff);
    const head = Buffer.alloc(5);
    head[0] = at + 0xffff >= data.length ? 1 : 0;
    head.writeUInt16LE(block.length, 1);
    head.writeUInt16LE(block.length ^ 0xffff, 3);
    parts.push(head, block);
  }
  const trailer = Buffer.alloc(8);
  trailer.writeUInt32LE(crc32(data), 0);
  trailer.writeUInt32LE(data.length % 2 ** 32, 4);
  return Buffer.concat([...parts, trailer]);
}

const archive = Buffer.concat([
  ...[...entries]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([name, data]) => tarMember(name, data)),
  Buffer.alloc(1024),
]);

await Promise.all([
  ...[...files].map(([file, bytes]) => writeFile(path.join(output, file), bytes)),
  writeFile(path.join(output, PACKAGE_FILE), gzipStored(archive)),
]);

console.log(`Generated ${files.size} resources and ${PACKAGE_FILE} in ${output}`);
