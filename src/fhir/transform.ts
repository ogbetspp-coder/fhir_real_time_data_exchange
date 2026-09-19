import { sha256, stableUuid } from "../lib/hash.js";
import type { EmaMapping, SectionRule } from "./mapping.js";
import {
  isComposition,
  type BundleEntry,
  type CompositionSection,
  type FhirBundle,
  type FhirComposition,
  type FhirResource,
} from "./types.js";

const EMA_DOCUMENT_TYPE_SYSTEM = "http://ema.europa.eu/fhir/CodeSystem/100000155531";
const EMA_SMPC_CODE = "100000155532";
const QRD_TEMPLATE_EXTENSION =
  "http://ema.europa.eu/fhir/StructureDefinition/ext-epi-qrdtemplate-version";

export type MappingDecision = {
  sourceKey: string;
  sourcePath: string;
  targetPath: string;
  targetCode: string;
  action: "code-mapped" | "structurally-moved";
  sourceHash: string;
  targetHash: string;
  narrativePreserved: boolean;
};

export type EmaPackage = {
  list: FhirResource;
  documentBundle: FhirBundle;
  mappingDecisions: MappingDecision[];
  inputHash: string;
  outputHash: string;
};

export class TransformationError extends Error {
  public constructor(
    message: string,
    public readonly issues: string[],
  ) {
    super(message);
    this.name = "TransformationError";
  }
}

type IndexedSection = {
  section: CompositionSection;
  path: string;
};

function sectionCode(section: CompositionSection, system: string): string | undefined {
  return section.code.coding?.find((coding) => coding.system === system)?.code;
}

function indexSections(
  sections: CompositionSection[],
  system: string,
  basePath = "Composition.section",
  index = new Map<string, IndexedSection[]>(),
): Map<string, IndexedSection[]> {
  sections.forEach((section, position) => {
    const path = `${basePath}[${position}]`;
    const code = sectionCode(section, system);
    if (code !== undefined) {
      index.set(code, [...(index.get(code) ?? []), { section, path }]);
    }
    if (section.section !== undefined) {
      indexSections(section.section, system, `${path}.section`, index);
    }
  });
  return index;
}

function mapSection(
  rule: SectionRule,
  mapping: EmaMapping,
  index: Map<string, IndexedSection[]>,
  targetPath: string,
  decisions: MappingDecision[],
  issues: string[],
): CompositionSection | undefined {
  const matches = index.get(rule.sourceKey) ?? [];
  if (matches.length === 0) {
    if (rule.required) issues.push(`Missing mandatory source section ${rule.sourceKey}`);
    return undefined;
  }
  if (matches.length > 1) {
    issues.push(`Ambiguous source section ${rule.sourceKey}: found ${matches.length}`);
    return undefined;
  }

  const match = matches[0];
  if (match === undefined) return undefined;

  const children = (rule.children ?? [])
    .map((child, position) =>
      mapSection(child, mapping, index, `${targetPath}.section[${position}]`, decisions, issues),
    )
    .filter((child): child is CompositionSection => child !== undefined);

  const target: CompositionSection = {
    id: stableUuid("ema-qrd-section", rule.sourceKey),
    title: rule.title,
    code: {
      coding: [
        {
          system: mapping.targetCodeSystem,
          code: rule.targetCode,
          display: rule.title,
        },
      ],
    },
    ...(match.section.text === undefined ? {} : { text: structuredClone(match.section.text) }),
    ...(children.length === 0 ? {} : { section: children }),
  };

  decisions.push({
    sourceKey: rule.sourceKey,
    sourcePath: match.path,
    targetPath,
    targetCode: rule.targetCode,
    action: rule.targetCode === rule.sourceKey ? "structurally-moved" : "code-mapped",
    sourceHash: sha256(match.section),
    targetHash: sha256(target),
    narrativePreserved: match.section.text?.div === target.text?.div,
  });

  return target;
}

function findComposition(bundle: FhirBundle): { composition: FhirComposition; entry: BundleEntry } {
  const firstEntry = bundle.entry[0];
  if (firstEntry === undefined || !isComposition(firstEntry.resource)) {
    throw new TransformationError("The first document entry must be a Composition", [
      "Bundle.entry[0].resource is not a Composition",
    ]);
  }
  return { composition: firstEntry.resource, entry: firstEntry };
}

function createEmaList(
  mapping: EmaMapping,
  packageId: string,
  documentFullUrl: string,
  title: string,
): FhirResource {
  return {
    resourceType: "List",
    id: stableUuid("ema-epi-list", packageId),
    meta: { profile: [mapping.profiles.list] },
    identifier: [
      {
        system: "https://khs.dev/fhir/identifier/epi-package",
        value: packageId,
      },
    ],
    status: "current",
    mode: "working",
    title: `${title} — EMA ePI document index`,
    code: {
      coding: [
        {
          system: EMA_DOCUMENT_TYPE_SYSTEM,
          code: "100000155527",
          display: "ePI Master List",
        },
      ],
    },
    entry: [
      {
        item: {
          reference: documentFullUrl,
          display: title,
        },
      },
    ],
  };
}

export function transformType2ToEma(
  sourceBundle: FhirBundle,
  mapping: EmaMapping,
  qrdTemplateVersion = "10.4",
): EmaPackage {
  if (sourceBundle.type !== "document") {
    throw new TransformationError("Source ePI must be a document Bundle", [
      `Expected Bundle.type=document, received ${sourceBundle.type}`,
    ]);
  }

  const { composition: sourceComposition } = findComposition(sourceBundle);
  const index = indexSections(sourceComposition.section, mapping.sourceCodeSystem);
  const decisions: MappingDecision[] = [];
  const issues: string[] = [];
  const root = mapSection(
    mapping.root,
    mapping,
    index,
    "Composition.section[0]",
    decisions,
    issues,
  );

  if (issues.length > 0 || root === undefined) {
    throw new TransformationError("EMA QRD transformation failed closed", issues);
  }
  if (decisions.some((decision) => !decision.narrativePreserved)) {
    throw new TransformationError("Narrative preservation check failed", [
      "At least one source XHTML narrative changed during transformation",
    ]);
  }

  const sourceIdentifier =
    sourceBundle.identifier.value ?? sourceBundle.id ?? sha256(sourceBundle).slice(0, 24);
  const packageId = `ema-${sourceIdentifier}`;
  const compositionId = stableUuid("ema-composition", sourceIdentifier);
  const bundleId = stableUuid("ema-bundle", sourceIdentifier);
  const compositionFullUrl = `urn:uuid:${compositionId}`;
  const bundleFullUrl = `urn:uuid:${bundleId}`;

  const targetComposition: FhirComposition = {
    ...structuredClone(sourceComposition),
    id: compositionId,
    meta: { ...sourceComposition.meta, profile: mapping.profiles.composition },
    language: "en",
    extension: [
      ...((sourceComposition.extension as unknown[] | undefined) ?? []).filter(
        (extension) => (extension as { url?: string }).url !== QRD_TEMPLATE_EXTENSION,
      ),
      {
        url: QRD_TEMPLATE_EXTENSION,
        valueString: qrdTemplateVersion,
      },
    ],
    identifier: [
      {
        system: "https://khs.dev/fhir/identifier/ema-composition",
        value: compositionId,
      },
    ],
    type: {
      coding: [
        {
          system: EMA_DOCUMENT_TYPE_SYSTEM,
          code: EMA_SMPC_CODE,
          display: "Summary of Product Characteristics",
        },
      ],
    },
    section: [root],
  };

  const targetBundle: FhirBundle = {
    ...structuredClone(sourceBundle),
    id: bundleId,
    meta: { ...sourceBundle.meta, profile: [mapping.profiles.bundle] },
    identifier: {
      system: "https://khs.dev/fhir/identifier/ema-document",
      value: bundleId,
    },
    timestamp: sourceBundle.timestamp,
    entry: [
      { fullUrl: compositionFullUrl, resource: targetComposition },
      ...sourceBundle.entry.slice(1).map((entry) => structuredClone(entry)),
    ],
  };

  const list = createEmaList(mapping, packageId, bundleFullUrl, targetComposition.title);
  const inputHash = sha256(sourceBundle);
  const outputHash = sha256({ list, documentBundle: targetBundle });

  return {
    list,
    documentBundle: targetBundle,
    mappingDecisions: decisions,
    inputHash,
    outputHash,
  };
}
