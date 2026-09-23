import { sha256, stableUuid } from "../lib/hash.js";
import { xhtmlToText } from "../fidelity/xhtml.js";
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

type SourceSection = {
  section: CompositionSection;
  path: string;
  // Where the section sits among its siblings, so manifest order can be checked.
  position: number;
  // Every code the section carries in the source code system, and the one code when there is
  // exactly one. Two codes make a section ambiguous; it is then indexed under neither.
  codes: string[];
  code: string | undefined;
  // The section this one sits directly under; undefined at the top level of Composition.section.
  parent: SourceSection | undefined;
};

// An English language tag in BCP 47: "en", optionally the Latin script subtag, optionally a
// region (two letters or three digits), compared case-insensitively. Anything else — a variant,
// an extension, a private-use subtag such as "en-x-fr", a dangling "en-", or "eng" — is refused:
// the manifest is the English CAP SmPC template and nothing else.
const ENGLISH_LANGUAGE = /^en(?:-latn)?(?:-(?:[a-z]{2}|\d{3}))?$/i;

// The elements of a source section the crosswalk carries or deliberately replaces: id and title
// (the output takes the rule's own id and heading), code (mapped), text (copied byte for byte)
// and section (walked). Any other element — entry, extension, emptyReason, author, focus,
// orderedBy, mode — would be dropped, so a section carrying one is refused instead.
const CARRIED_SECTION_ELEMENTS = new Set(["id", "title", "code", "text", "section"]);

// Characters that show nothing on the page besides whitespace (which already covers U+00A0 and
// U+FEFF): the zero-width space, the zero-width non-joiner and joiner, the word joiner and the
// soft hyphen.
const INVISIBLE_CODE_POINTS = new Set([0x200b, 0x200c, 0x200d, 0x2060, 0x00ad]);

// The scanner's table-grid markers (U+FDD0-U+FDD5) are structure, not text: a table of empty cells
// shows nothing. A picture (U+FFFC) and a list number are drawn, so they count.
function isVisible(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  if (codePoint >= 0xfdd0 && codePoint <= 0xfdef) return false;
  return !/\s/u.test(character) && !INVISIBLE_CODE_POINTS.has(codePoint);
}

type Narrative = "absent" | "present" | "unreadable";

// Whether a section's text.div holds anything a reader would see, read with the same fail-closed
// scanner the fidelity check uses (src/fidelity/xhtml.ts): entities decoded, markup removed,
// invisible characters ignored. A div that scanner rejects — a comment, CDATA, an unknown
// element such as img, an unknown entity such as &nbsp;, a forbidden attribute — is
// "unreadable", and both checks that use this refuse it: an uncoded section might be hiding text
// in it, and a mandatory section cannot be shown to carry any.
function readNarrative(section: CompositionSection): Narrative {
  const div: unknown = section.text?.div;
  if (div === undefined) return "absent";
  if (typeof div !== "string") return "unreadable";
  try {
    return Array.from(xhtmlToText(div)).some(isVisible) ? "present" : "absent";
  } catch {
    // An XhtmlError, or anything else the scanner throws: either way nothing can be shown.
    return "unreadable";
  }
}

// Every section of the source tree, in document order, coded or not. Nothing is skipped here:
// what the manifest does not consume must still be seen, so that it can be refused.
function collectSections(
  sections: CompositionSection[],
  system: string,
  basePath = "Composition.section",
  parent?: SourceSection,
  collected: SourceSection[] = [],
): SourceSection[] {
  sections.forEach((section, position) => {
    const path = `${basePath}[${position}]`;
    // Typed as required, but a source read from a store is not checked against the type; a
    // section without a code is reported below rather than failing here with a TypeError.
    const codings = (section as { code?: CompositionSection["code"] }).code?.coding ?? [];
    const codes = codings
      .filter((coding) => coding.system === system)
      .map((coding) => coding.code ?? "");
    const single = codes.length === 1 ? codes[0] : undefined;
    const entry: SourceSection = {
      section,
      path,
      position,
      codes,
      code: single === "" ? undefined : single,
      parent,
    };
    collected.push(entry);
    if (section.section !== undefined) {
      collectSections(section.section, system, `${path}.section`, entry, collected);
    }
  });
  return collected;
}

function indexSections(sections: SourceSection[]): Map<string, SourceSection[]> {
  const index = new Map<string, SourceSection[]>();
  for (const section of sections) {
    if (section.code !== undefined) {
      index.set(section.code, [...(index.get(section.code) ?? []), section]);
    }
  }
  return index;
}

function ruleKeys(rule: SectionRule, keys = new Set<string>()): Set<string> {
  keys.add(rule.sourceKey);
  (rule.children ?? []).forEach((child) => ruleKeys(child, keys));
  return keys;
}

function describeParent(parent: SourceSection | undefined): string {
  if (parent === undefined) return "top level";
  if (parent.code !== undefined) return parent.code;
  return parent.codes.length > 1
    ? `ambiguously coded section at ${parent.path}`
    : `uncoded section at ${parent.path}`;
}

// Checks every section of the source tree on its own, whether or not a rule consumes it. A
// section the manifest does not consume would otherwise be dropped with its narrative while the
// run succeeds: a section coded in the source code system must match a rule, and a section
// without such a code may exist only as an empty container, never with narrative of its own.
function sourceSectionIssues(sections: SourceSection[], mapping: EmaMapping): string[] {
  const keys = ruleKeys(mapping.root);
  const issues: string[] = [];
  for (const { section, path, codes, code } of sections) {
    if ((section as { code?: unknown }).code === undefined) {
      issues.push(`Source section at ${path} has no code`);
    }
    for (const element of Object.keys(section)) {
      if (!CARRIED_SECTION_ELEMENTS.has(element)) {
        issues.push(`Source section at ${path} carries ${element}, which the mapping would drop`);
      }
    }
    if (codes.length > 1) {
      issues.push(
        `Ambiguous source section at ${path}: ${codes.length} codes in the source code system`,
      );
    } else if (code === undefined) {
      const narrative = readNarrative(section);
      if (narrative === "present") {
        issues.push(`Uncoded source section with narrative at ${path}`);
      } else if (narrative === "unreadable") {
        issues.push(`Uncoded source section with unreadable narrative at ${path}`);
      }
    } else if (!keys.has(code)) {
      issues.push(`Unmapped source section ${code} at ${path}`);
    }
  }
  return issues;
}

// The manifest is English-only, so a source must say it is English: a source that declares
// another language, or none, is refused rather than published under an English language tag.
// Bundle-uv-epi makes Bundle.language mandatory, and every fixture and the published HL7 example
// declare both.
function sourceLanguageIssues(bundle: FhirBundle, composition: FhirComposition): string[] {
  const declared: [string, unknown][] = [
    ["Composition.language", composition.language],
    ["Bundle.language", bundle.language],
  ];
  return declared.flatMap(([element, language]) => {
    if (language === undefined) {
      return [`Source ${element} is missing; the mapping is English-only`];
    }
    if (typeof language === "string" && ENGLISH_LANGUAGE.test(language)) return [];
    const shown = typeof language === "string" ? language : JSON.stringify(language);
    return [`Source ${element} ${shown} is not English; the mapping is English-only`];
  });
}

function mapSection(
  rule: SectionRule,
  // The sourceKey of the parent rule; undefined for a top-level rule.
  parentKey: string | undefined,
  mapping: EmaMapping,
  index: Map<string, SourceSection[]>,
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
    match.parent === undefined
      ? parentKey === undefined
      : match.parent.code !== undefined && match.parent.code === parentKey;
  if (!placed) {
    issues.push(
      `Source section ${rule.sourceKey} is under ${describeParent(match.parent)}, expected under ${parentKey ?? "top level"}`,
    );
  }

  // A leaf must carry narrative. A section with child rules may be a bare heading over its
  // subsections, unless its rule says the section carries text of its own above them.
  const childRules = rule.children ?? [];
  if (rule.narrative === "required" || (rule.required && childRules.length === 0)) {
    const narrative = readNarrative(match.section);
    if (narrative === "absent") {
      issues.push(`Mandatory source section ${rule.sourceKey} has no narrative`);
    } else if (narrative === "unreadable") {
      issues.push(`Mandatory source section ${rule.sourceKey} has unreadable narrative`);
    }
  }

  // Subsections must already be in manifest order. Only those placed directly under this section
  // are compared; one found elsewhere has been reported above.
  let previous: { sourceKey: string; position: number } | undefined;
  for (const child of childRules) {
    const [only, ...others] = index.get(child.sourceKey) ?? [];
    if (only === undefined || others.length > 0 || only.parent !== match) continue;
    if (previous !== undefined && only.position < previous.position) {
      issues.push(
        `Source section ${child.sourceKey} comes before ${previous.sourceKey} under ${rule.sourceKey}; the manifest orders ${previous.sourceKey} first`,
      );
    }
    if (previous === undefined || only.position > previous.position) {
      previous = { sourceKey: child.sourceKey, position: only.position };
    }
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
  issues.push(...sourceSectionIssues(sections, mapping));

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
    // Always English: a source declaring any other language, or none, has already failed above.
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
    // The source declared an English tag in some spelling ("EN", "en-GB"); the output says "en",
    // as the Composition does.
    language: "en",
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
