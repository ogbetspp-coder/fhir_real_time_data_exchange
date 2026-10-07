import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { crc32 } from "node:zlib";

import { ApproverRole } from "../../src/contracts/index.js";
import { loadEmaMapping, type SectionRule } from "../../src/fhir/mapping.js";
import {
  ACTIVITY_SYSTEM,
  APPROVAL_CONTENT_EXTENSION_URL,
  APPROVER_IDENTIFIER_SYSTEM,
  APPROVER_ROLE_SYSTEM,
  AUTHORITY_FILE_IDENTIFIER_SYSTEM,
  FIDELITY_REPORT_IDENTIFIER_SYSTEM,
  IMPORT_REQUESTER_IDENTIFIER_SYSTEM,
  MODEL_IDENTIFIER_SYSTEM,
  SOURCE_DOCUMENT_IDENTIFIER_SYSTEM,
} from "../../src/fhir/provenance.js";
import {
  EU_AUTHORISATION_NUMBER_PATTERN,
  EU_AUTHORISATION_NUMBER_SYSTEM,
  EU_PRODUCT_IDENTITY_PROFILE,
  EU_PRODUCT_NUMBER_PATTERN,
  EU_PRODUCT_NUMBER_SYSTEM,
  KHS_CANONICAL,
} from "../../src/fhir/standards.js";
import {
  EMA_COMPOSITION_VERSION_SYSTEM,
  EMA_DOCUMENT_IDENTIFIER_SYSTEM,
} from "../../src/fhir/transform.js";

// The repository's own FHIR definitions, and the package that carries them to the official
// validator. src/fhir/transform.ts is the crosswalk; the StructureMap is its executed twin
// (fhir/maps/), which runs through the ConceptMap, and test/official/structuremap-twin.test.ts
// holds the two to the same output. The code systems, value sets, extension and naming systems
// define every https://khs.dev/fhir/ system and extension the pipeline and its fixtures write
// (test/version-identity.test.ts finds every identifier system they write and requires its
// NamingSystem), one profile states the EU number rules, and the SubscriptionTopic states the
// event the validated store publishes. Every resource is checked by the official HL7 validator in
// CI (scripts/ci/emit-validation-set.ts), and the package is loaded by it there and in the sidecar
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
const PACKAGE_VERSION = "0.5.0";
const PACKAGE_FILE = `${PACKAGE_NAME}.tgz`;
const CANONICAL = KHS_CANONICAL;

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

// Every identifier system the pipeline and its fixtures write, each a NamingSystem
// (docs/design/version-identity.md). Whether a value persists across an ePI's versions is said
// where it matters. A retired system is no longer written, but versions written before stay in
// the store's history.
const NAMING_SYSTEMS_DATE = "2026-10-06";
const synthetic = (what: string): string =>
  `A synthetic ${what}'s identifier in the fixtures (src/fixtures/synthetic.ts). Synthetic content only: never a real product's.`;
const identifierSystems: { system: string; title: string; description: string; retired?: true }[] =
  [
    {
      system: EMA_DOCUMENT_IDENTIFIER_SYSTEM,
      title: "EMA document Bundle",
      description:
        "The EMA document Bundle's identifier. Its value is the Bundle's id, derived from the source Bundle.identifier.value (src/fhir/transform.ts), and is the same for every version of the ePI document (Bundle-uv-epi).",
    },
    {
      system: EMA_COMPOSITION_VERSION_SYSTEM,
      title: "EMA Composition version",
      description:
        "One version of the EMA Composition: a UUID derived from the source Bundle.identifier.value and the Composition's content without its identifier (src/fhir/transform.ts). Each new version has a new value (Composition-uv-epi, EUEpiComposition); the same content has the same value.",
    },
    {
      system: `${CANONICAL}/identifier/ema-composition`,
      title: "EMA Composition (retired)",
      description:
        "The EMA Composition's identifier until 2026-10-06: the Composition's id, the same for every version, which the Global ePI and EMA profiles define as one per version. No longer written; versions written before then keep it in the store's history.",
      retired: true,
    },
    {
      system: `${CANONICAL}/identifier/epi-package`,
      title: "EMA ePI List",
      description:
        "The EMA List's identifier: 'ema-' and the source Bundle.identifier.value, the same for every version of the ePI (EUEpiList).",
    },
    {
      system: `${CANONICAL}/identifier/transaction`,
      title: "Persist transaction",
      description: "The identifier of a run's persist transaction Bundle: the run's id, a UUID.",
    },
    {
      system: EU_AUTHORISATION_NUMBER_SYSTEM,
      title: "EU authorisation number",
      description: `An EU marketing authorisation number, EU/1/YY/NNN/PPP (YY the year, NNN the product in three or four digits, PPP the presentation), as a RegulatedAuthorization's identifier: one RegulatedAuthorization per number, one number per RegulatedAuthorization. ${EU_PRODUCT_IDENTITY_PROFILE} holds every value to ${EU_AUTHORISATION_NUMBER_PATTERN} (khs-eu-1, khs-eu-3).`,
    },
    {
      system: EU_PRODUCT_NUMBER_SYSTEM,
      title: "EU product number",
      description: `An EU product number, EU/1/YY/NNN (an authorisation number without its presentation), as a MedicinalProductDefinition's identifier. ${EU_PRODUCT_IDENTITY_PROFILE} holds every value to ${EU_PRODUCT_NUMBER_PATTERN}, and to the products of the RegulatedAuthorizations' numbers (khs-eu-2, khs-eu-4).`,
    },
    {
      system: MODEL_IDENTIFIER_SYSTEM,
      title: "Extraction model",
      description: "The model a structuring run used, by the id its submission records.",
    },
    {
      system: APPROVER_IDENTIFIER_SYSTEM,
      title: "Approver",
      description:
        "The approver of a submission, by the opaque id its approval records, never an e-mail address.",
    },
    {
      system: SOURCE_DOCUMENT_IDENTIFIER_SYSTEM,
      title: "Source document SHA-256",
      description:
        "A source document, by the SHA-256 of its bytes, 64 lowercase hexadecimal digits.",
    },
    {
      system: FIDELITY_REPORT_IDENTIFIER_SYSTEM,
      title: "Fidelity report SHA-256",
      description: "A fidelity report, by its reportHash, 64 lowercase hexadecimal digits.",
    },
    {
      system: IMPORT_REQUESTER_IDENTIFIER_SYSTEM,
      title: "Import requester",
      description: "Who requested an authority import, by the id the import request records.",
    },
    {
      system: AUTHORITY_FILE_IDENTIFIER_SYSTEM,
      title: "Authority file",
      description:
        "An authority's published file an import read: the authority's segment, then Bundle/ or List/ and the authority's id for the file.",
    },
    {
      system: `${CANONICAL}/identifier/authority-import`,
      title: "Authority import record",
      description:
        "An authority import's canonical record: the Bundle's identifier, authority-import:, the authority's segment, a colon and the authority's id for the document; and the Composition's, the same followed by :composition.",
    },
    {
      system: `${CANONICAL}/identifier/certified-word`,
      title: "Certified Word record",
      description:
        "A certified Word source's canonical record (ADR 0006): the Bundle's identifier, certified-word: and our id for the ePI the label is a version of; and the Composition's, the same followed by :composition.",
    },
    {
      system: `${CANONICAL}/identifier/canonical-product`,
      title: "Canonical product",
      description:
        "Our own id for a medicinal product a person confirmed from its label, above each regulator's identifiers for it (ADR 0006 decision 5).",
    },
    {
      system: `${CANONICAL}/identifier/canonical-organization`,
      title: "Canonical organisation",
      description:
        "Our own id for a marketing authorisation holder a person confirmed from a label (ADR 0006 decision 5).",
    },
    {
      system: `${CANONICAL}/identifier/type2-document`,
      title: "Synthetic Type 2 document",
      description: `${synthetic("Type 2 source Bundle")} The same for every version of a document (Bundle-uv-epi).`,
    },
    {
      system: `${CANONICAL}/identifier/composition`,
      title: "Synthetic Composition",
      description: `${synthetic("Type 2 source Composition")} One per version (Composition-uv-epi).`,
    },
    {
      system: `${CANONICAL}/identifier/organization`,
      title: "Synthetic organisation",
      description: synthetic("organisation"),
    },
    {
      system: `${CANONICAL}/identifier/product`,
      title: "Synthetic product",
      description: synthetic("medicinal product"),
    },
    {
      system: `${CANONICAL}/identifier/authorization`,
      title: "Synthetic authorisation",
      description: `${synthetic("authorisation")} Never an EU authorisation number.`,
    },
    {
      system: `${CANONICAL}/identifier/package`,
      title: "Synthetic package",
      description: synthetic("packaged product"),
    },
    {
      system: `${CANONICAL}/identifier/packaging`,
      title: "Synthetic packaging",
      description: synthetic("packaging"),
    },
    {
      system: `${CANONICAL}/identifier/manufactured-item`,
      title: "Synthetic manufactured item",
      description: synthetic("manufactured item"),
    },
    {
      system: `${CANONICAL}/identifier/administrable-product`,
      title: "Synthetic administrable product",
      description: synthetic("administrable product"),
    },
    {
      system: `${CANONICAL}/identifier/substance`,
      title: "Synthetic substance",
      description: synthetic("substance"),
    },
    ...(["org", "product"] as const).map((kind) => ({
      system: `${CANONICAL}/example/sid/${kind}`,
      title: `Example ${kind}`,
      description: `The HL7 Global ePI DrugX example's http://example.org/sid/${kind}, moved into this namespace by the seeded synthetic source (createSyntheticSmpcFromPublishedType2, src/fixtures/synthetic.ts). Example content only.`,
    })),
  ];

const namingSystems = identifierSystems.map(({ system, title, description, retired }) => {
  const id = system
    .slice(`${CANONICAL}/`.length)
    .replace(/^identifier\//, "")
    .replaceAll("/", "-");
  return {
    resourceType: "NamingSystem",
    id,
    url: `${CANONICAL}/NamingSystem/${id}`,
    version: PACKAGE_VERSION,
    name: id
      .split("-")
      .map((word) => `${word.charAt(0).toUpperCase()}${word.slice(1)}`)
      .join(""),
    title,
    status: retired === true ? "retired" : "active",
    kind: "identifier",
    experimental: true,
    date: NAMING_SYSTEMS_DATE,
    description,
    uniqueId: [{ type: "uri", value: system, preferred: true }],
  };
});

// The EU number rules (docs/design/version-identity.md) for the official validator, as invariants
// on a document Bundle. src/fhir/preflight.ts states the same rules (euNumberIssues); every rule
// holds for a graph with no EU number.
const authorisationNumbers = `entry.resource.ofType(RegulatedAuthorization).identifier.where(system = '${EU_AUTHORISATION_NUMBER_SYSTEM}')`;
const productNumbers = `entry.resource.ofType(MedicinalProductDefinition).identifier.where(system = '${EU_PRODUCT_NUMBER_SYSTEM}')`;
const theirProducts = `${authorisationNumbers}.select(value.replaceMatches('/[0-9]{3}$', ''))`;
const euInvariants = [
  {
    key: "khs-eu-1",
    human: `Every EU authorisation number is ${EU_AUTHORISATION_NUMBER_PATTERN}, and only a RegulatedAuthorization carries one`,
    expression: `${authorisationNumbers}.all(value.matches('${EU_AUTHORISATION_NUMBER_PATTERN}')) and entry.resource.where(($this is RegulatedAuthorization).not()).descendants().ofType(Identifier).where(system = '${EU_AUTHORISATION_NUMBER_SYSTEM}').empty()`,
  },
  {
    key: "khs-eu-2",
    human: `Every EU product number is ${EU_PRODUCT_NUMBER_PATTERN}, and only a MedicinalProductDefinition carries one`,
    expression: `${productNumbers}.all(value.matches('${EU_PRODUCT_NUMBER_PATTERN}')) and entry.resource.where(($this is MedicinalProductDefinition).not()).descendants().ofType(Identifier).where(system = '${EU_PRODUCT_NUMBER_SYSTEM}').empty()`,
  },
  {
    key: "khs-eu-3",
    human:
      "One RegulatedAuthorization per EU authorisation number: none carries two, no two carry one",
    expression: `entry.resource.ofType(RegulatedAuthorization).all(identifier.where(system = '${EU_AUTHORISATION_NUMBER_SYSTEM}').count() <= 1) and ${authorisationNumbers}.value.isDistinct()`,
  },
  {
    key: "khs-eu-4",
    human: "The EU product numbers are exactly the products of the EU authorisation numbers",
    expression: `${theirProducts}.subsetOf(${productNumbers}.value) and ${productNumbers}.value.subsetOf(${theirProducts})`,
  },
];
const euProductIdentity = {
  resourceType: "StructureDefinition",
  id: EU_PRODUCT_IDENTITY_PROFILE.split("/").at(-1),
  url: EU_PRODUCT_IDENTITY_PROFILE,
  version: PACKAGE_VERSION,
  name: "EuProductIdentity",
  title: "EU product identity of an ePI document",
  status: "active",
  experimental: true,
  description:
    "The EU number rules of an ePI document Bundle's product graph: EU authorisation numbers (EU/1/YY/NNN/PPP), one per RegulatedAuthorization, and the MedicinalProductDefinition's EU product numbers (EU/1/YY/NNN), exactly the authorisations' products. A graph with no EU number meets every rule.",
  fhirVersion: "5.0.0",
  kind: "resource",
  abstract: false,
  type: "Bundle",
  baseDefinition: "http://hl7.org/fhir/StructureDefinition/Bundle",
  derivation: "constraint",
  differential: {
    element: [
      {
        id: "Bundle",
        path: "Bundle",
        constraint: euInvariants.map(({ key, human, expression }) => ({
          key,
          severity: "error",
          human,
          expression,
          source: EU_PRODUCT_IDENTITY_PROFILE,
        })),
      },
    ],
  },
};

// The event downstream systems subscribe to. The Healthcare API has no R5 topic-based
// Subscriptions: the validated store publishes one Pub/Sub message per resource it writes
// (scripts/gcp/reconcile-fhir-stores.sh, notificationConfigs), and
// docs/design/epi-published-notifications.md is the contract that maps this topic onto them.
const epiPublishedTopic = {
  resourceType: "SubscriptionTopic",
  id: "epi-published",
  url: `${CANONICAL}/SubscriptionTopic/epi-published`,
  version: PACKAGE_VERSION,
  name: "EpiPublished",
  title: "ePI document published or superseded",
  status: "active",
  experimental: true,
  description:
    "A version of an EMA ePI document Bundle was written to the validated FHIR store: its first version (published) or a later one, which supersedes the version before it. The pipeline writes the Bundle with its List, its entries and, for a document run, its Provenance, in one transaction, and never deletes one. Delivered as Pub/Sub messages from the store, not by R5 Subscriptions: a notification carries the Bundle's resource name and version id, never its content (docs/design/epi-published-notifications.md).",
  resourceTrigger: [
    {
      description: "A Bundle of type document is created, or updated to a new version.",
      // Relative to http://hl7.org/fhir/StructureDefinition/, as R5 defines the element.
      resource: "Bundle",
      supportedInteraction: ["create", "update"],
      fhirPathCriteria: "%current.type = 'document'",
    },
  ],
  notificationShape: [{ resource: "Bundle" }],
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
  euProductIdentity,
  ...namingSystems,
  epiPublishedTopic,
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
    "The code systems, value sets, extension, naming systems, profile, SubscriptionTopic, ConceptMap and StructureMap this repository's ePI pipeline writes or publishes (scripts/fhir/generate-artifacts.ts).",
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
