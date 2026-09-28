import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { loadEmaMapping, type SectionRule } from "../../src/fhir/mapping.js";
import {
  EMA_DOCUMENT_ID_NAMESPACE,
  EMA_DOCUMENT_IDENTIFIER_SYSTEM,
} from "../../src/fhir/transform.js";

// Both artifacts are descriptive, not executed: src/fhir/transform.ts is the crosswalk. Each is
// checked by the official HL7 validator in CI (scripts/ci/emit-validation-set.ts), and
// test/fhir-artifacts.test.ts holds the StructureMap's rules to what the transform does.

const output = path.resolve("fhir/generated");
await mkdir(output, { recursive: true });
const mapping = await loadEmaMapping();

function flatten(rule: SectionRule): SectionRule[] {
  return [rule, ...(rule.children ?? []).flatMap(flatten)];
}

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
    "Deterministic terminology map. It maps section identifiers only and does not generate or alter regulated narrative.",
  // A scope is a value set: R5 types it canonical(ValueSet), and the validator resolves a uri
  // scope as well and refuses a code system there. The target's is the EMA IG's SmPC section-code
  // value set, the one EUEpiCompositionSmPC binds Composition.section.code to, which holds every
  // target code. No value set of the canonical section keys is published, so the source has no
  // scope; group.source names its code system.
  targetScopeCanonical: EMA_SMPC_SECTION_CODES,
  group: [
    {
      source: mapping.sourceCodeSystem,
      target: mapping.targetCodeSystem,
      element: flatten(mapping.root).map((rule) => ({
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
    },
  ],
};

const structureMap = {
  resourceType: "StructureMap",
  id: "type2-to-ema-cap-smpc-en",
  url: "https://khs.dev/fhir/StructureMap/type2-to-ema-cap-smpc-en",
  version: mapping.mappingVersion,
  name: "Type2ToEmaCapSmpcEnglish",
  title: "HL7 Global ePI Type 2 to EMA CAP SmPC English",
  status: "active",
  experimental: true,
  description:
    "Non-normative summary of two Bundle-level rules; it is not executed. The reviewed TypeScript implementation (src/fhir/transform.ts) executes the complete fail-closed mapping and records field-level evidence.",
  structure: [
    {
      url: "http://hl7.org/fhir/uv/emedicinal-product-info/StructureDefinition/Bundle-uv-epi",
      mode: "source",
      alias: "Type2Bundle",
    },
    {
      url: mapping.profiles.bundle,
      mode: "target",
      alias: "EmaBundle",
    },
  ],
  group: [
    {
      name: "Type2BundleToEmaBundle",
      input: [
        { name: "src", mode: "source", type: "Type2Bundle" },
        { name: "tgt", mode: "target", type: "EmaBundle" },
      ],
      rule: [
        {
          name: "deriveDocumentIdentifier",
          documentation: `The target identifier is not the source's: its system is ${EMA_DOCUMENT_IDENTIFIER_SYSTEM} and its value is derived from the source Bundle.identifier.value and is also the target Bundle's id (src/lib/hash.ts stableUuid, namespace "${EMA_DOCUMENT_ID_NAMESPACE}"): take the SHA-256 of the UTF-8 string "${EMA_DOCUMENT_ID_NAMESPACE}:" followed by the value, in lowercase hex; keep its first 32 hex digits; set the 13th digit to 5 and the 17th digit to a; and write the 32 digits as a UUID, in groups of 8-4-4-4-12 joined by hyphens. The FHIR mapping language has no transform for that derivation, so this rule sets the system and leaves the value to the implementation.`,
          source: [{ context: "src", element: "identifier", variable: "identifier" }],
          target: [
            {
              context: "tgt",
              element: "identifier",
              variable: "targetIdentifier",
              transform: "create",
              parameter: [{ valueString: "Identifier" }],
            },
            {
              context: "targetIdentifier",
              element: "system",
              transform: "copy",
              parameter: [{ valueString: EMA_DOCUMENT_IDENTIFIER_SYSTEM }],
            },
          ],
        },
        {
          name: "copyDocumentTimestamp",
          source: [{ context: "src", element: "timestamp", variable: "timestamp" }],
          target: [
            {
              context: "tgt",
              element: "timestamp",
              transform: "copy",
              parameter: [{ valueId: "timestamp" }],
            },
          ],
        },
      ],
    },
  ],
};

await Promise.all([
  writeFile(
    path.join(output, "ConceptMap-canonical-to-ema-cap-smpc-en.json"),
    `${JSON.stringify(conceptMap, null, 2)}\n`,
  ),
  writeFile(
    path.join(output, "StructureMap-type2-to-ema-cap-smpc-en.json"),
    `${JSON.stringify(structureMap, null, 2)}\n`,
  ),
]);

console.log(`Generated interoperability artifacts in ${output}`);
