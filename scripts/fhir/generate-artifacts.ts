import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { loadEmaMapping, type SectionRule } from "../../src/fhir/mapping.js";

const output = path.resolve("fhir/generated");
await mkdir(output, { recursive: true });
const mapping = await loadEmaMapping();

function flatten(rule: SectionRule): SectionRule[] {
  return [rule, ...(rule.children ?? []).flatMap(flatten)];
}

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
  sourceScopeCanonical: mapping.sourceCodeSystem,
  targetScopeCanonical: mapping.targetCodeSystem,
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
            display: rule.title,
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
    "High-level structural contract. The reviewed TypeScript implementation executes the complete fail-closed mapping and records field-level evidence.",
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
      typeMode: "none",
      input: [
        { name: "src", mode: "source", type: "Type2Bundle" },
        { name: "tgt", mode: "target", type: "EmaBundle" },
      ],
      rule: [
        {
          name: "copyDocumentIdentifier",
          source: [{ context: "src", element: "identifier", variable: "identifier" }],
          target: [
            {
              context: "tgt",
              element: "identifier",
              transform: "copy",
              parameter: [{ valueId: "identifier" }],
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
