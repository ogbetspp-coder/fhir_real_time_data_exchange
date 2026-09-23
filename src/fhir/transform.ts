import { sha256, stableUuid } from "../lib/hash.js";
import { duplicateRuleIssues, type EmaMapping, type SectionRule } from "./mapping.js";
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
// The List indexes every document of the ePI package. EMA's Document Type code system has no
// "master list" concept; the concept it does have for the whole set of documents is
// 100000155539, and the EMA EPI-23-1022 English sample (fhir/standards.lock.json) codes its
// List with exactly this coding. The display must be the code system's own string: the
// validator rejects any other.
const EMA_LIST_CODE = "100000155539";
const EMA_LIST_DISPLAY = "Combined File of all Documents";
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
  // The source code of the section this one sits directly under, and that section's path. Both
  // are undefined at the top level of Composition.section.
  parentCode: string | undefined;
  parentPath: string | undefined;
};

type SourceSection = IndexedSection & { code: string | undefined };

// An English language tag: "en" itself or any "en-" subtag, compared case-insensitively as
// BCP 47 requires. The manifest is the English CAP SmPC template and nothing else.
const ENGLISH_LANGUAGE = /^en(-|$)/i;

function sectionCode(section: CompositionSection, system: string): string | undefined {
  return section.code.coding?.find((coding) => coding.system === system)?.code;
}

// A section carries narrative when its text.div holds something a reader would see: text once
// the markup is removed, or an image. An absent text, an empty root div, or a div holding only
// empty elements is no narrative.
function hasNarrative(section: CompositionSection): boolean {
  const div = section.text?.div;
  if (div === undefined) return false;
  return div.replace(/<[^>]*>/g, "").trim() !== "" || /<img\b/i.test(div);
}

// Every section of the source tree, in document order, coded or not. Nothing is skipped here:
// what the manifest does not consume must still be seen, so that it can be refused.
function collectSections(
  sections: CompositionSection[],
  system: string,
  basePath = "Composition.section",
  parent?: { code: string | undefined; path: string },
  collected: SourceSection[] = [],
): SourceSection[] {
  sections.forEach((section, position) => {
    const path = `${basePath}[${position}]`;
    const code = sectionCode(section, system);
    collected.push({ section, path, code, parentCode: parent?.code, parentPath: parent?.path });
    if (section.section !== undefined) {
      collectSections(section.section, system, `${path}.section`, { code, path }, collected);
    }
  });
  return collected;
}

function indexSections(sections: SourceSection[]): Map<string, IndexedSection[]> {
  const index = new Map<string, IndexedSection[]>();
  for (const { code, ...indexed } of sections) {
    if (code !== undefined) index.set(code, [...(index.get(code) ?? []), indexed]);
  }
  return index;
}

function ruleKeys(rule: SectionRule, keys = new Set<string>()): Set<string> {
  keys.add(rule.sourceKey);
  (rule.children ?? []).forEach((child) => ruleKeys(child, keys));
  return keys;
}

// A source section the manifest does not consume would otherwise be dropped with its narrative
// while the run succeeds. A section coded in the source code system must match a rule; a section
// without such a code may exist only as an empty container, never with narrative of its own.
function unconsumedSectionIssues(sections: SourceSection[], mapping: EmaMapping): string[] {
  const keys = ruleKeys(mapping.root);
  const issues: string[] = [];
  for (const { section, path, code } of sections) {
    if (code === undefined) {
      if (hasNarrative(section)) issues.push(`Uncoded source section with narrative at ${path}`);
    } else if (!keys.has(code)) {
      issues.push(`Unmapped source section ${code} at ${path}`);
    }
  }
  return issues;
}

// The manifest is English-only, so a source that says it is written in another language is
// refused rather than published under an English language tag.
function sourceLanguageIssues(bundle: FhirBundle, composition: FhirComposition): string[] {
  const declared: [string, unknown][] = [
    ["Composition.language", composition.language],
    ["Bundle.language", bundle.language],
  ];
  return declared
    .filter(
      ([, language]) =>
        language !== undefined &&
        (typeof language !== "string" || !ENGLISH_LANGUAGE.test(language)),
    )
    .map(
      ([element, language]) =>
        `Source ${element} ${typeof language === "string" ? language : JSON.stringify(language)} is not English; the mapping is English-only`,
    );
}

function mapSection(
  rule: SectionRule,
  // The sourceKey of the parent rule; undefined for a top-level rule.
  parentKey: string | undefined,
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

  // The source tree must already have the manifest's shape: a section sits directly under the
  // section its parent rule maps, and a top-level rule's section at the top level. The index is
  // flat, so without this a misplaced section would be quietly moved back into place.
  const placed =
    match.parentPath === undefined
      ? parentKey === undefined
      : match.parentCode !== undefined && match.parentCode === parentKey;
  if (!placed) {
    const actual =
      match.parentPath === undefined
        ? "top level"
        : (match.parentCode ?? `uncoded section at ${match.parentPath}`);
    issues.push(
      `Source section ${rule.sourceKey} is under ${actual}, expected under ${parentKey ?? "top level"}`,
    );
  }

  // Only a leaf must carry narrative; a section with child rules may be a bare heading over its
  // subsections.
  const childRules = rule.children ?? [];
  if (rule.required && childRules.length === 0 && !hasNarrative(match.section)) {
    issues.push(`Mandatory source section ${rule.sourceKey} has no narrative`);
  }

  const children = childRules
    .map((child, position) =>
      mapSection(
        child,
        rule.sourceKey,
        mapping,
        index,
        `${targetPath}.section[${position}]`,
        decisions,
        issues,
      ),
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
          display: rule.display ?? rule.title,
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
          code: EMA_LIST_CODE,
          display: EMA_LIST_DISPLAY,
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
  const sections = collectSections(sourceComposition.section, mapping.sourceCodeSystem);
  const index = indexSections(sections);
  const decisions: MappingDecision[] = [];
  const issues: string[] = [
    ...duplicateRuleIssues(mapping.root),
    ...sourceLanguageIssues(sourceBundle, sourceComposition),
  ];
  const root = mapSection(
    mapping.root,
    undefined,
    mapping,
    index,
    "Composition.section[0]",
    decisions,
    issues,
  );
  issues.push(...unconsumedSectionIssues(sections, mapping));

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
    // Always English: a source declaring any other language has already failed above.
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
