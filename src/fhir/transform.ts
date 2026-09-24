import { sha256, stableUuid } from "../lib/hash.js";
import { isGap } from "../fidelity/normalize.js";
import { isGridMarker, xhtmlToText } from "../fidelity/xhtml.js";
import {
  duplicateRuleIssues,
  permittedTitles,
  type EmaMapping,
  type SectionRule,
} from "./mapping.js";
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

const CARRIED_META_ELEMENTS = new Set(["versionId", "lastUpdated", "profile"]);

// The elements of a source Bundle the crosswalk carries or replaces; any other is refused
// (docs/design/authority-import-contract.md, D7: every reference a run persists derives from its
// identifier value).
const CARRIED_BUNDLE_ELEMENTS = new Set([
  "resourceType",
  "id",
  "meta",
  "language",
  "identifier",
  "type",
  "timestamp",
  "entry",
]);

// What a reader sees inked, by the rule section 5 uses for `empty-narrative`: not a gap (section
// 6: whitespace, a thin space, a blank glyph, a code point Unicode says to ignore) and not one of
// the scanner's table-grid markers, which are structure (a table of empty cells shows nothing).
// U+1680 OGHAM SPACE MARK, drawn as a stroke, is not a gap; a picture and a list number are drawn.
function isVisible(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  return !isGap(codePoint) && !isGridMarker(codePoint);
}

type Narrative = "absent" | "present" | "unreadable";

// A picture in the scanner's text: U+FFFC, the SHA-256 of its source, U+FFFC.
const PICTURE_TOKEN = /\ufffc[0-9a-f]{64}\ufffc/gu;

// Whether a section's text.div holds anything a reader would see, read with the same fail-closed
// scanner the fidelity check uses (src/fidelity/xhtml.ts): entities decoded, markup removed,
// invisible characters ignored. A div that scanner rejects — a comment, CDATA, an unknown
// element, an unknown entity such as &nbsp;, a forbidden attribute, a picture by reference — is
// "unreadable", and both checks that use this refuse it: an uncoded section might be hiding text
// in it, and a mandatory section cannot be shown to carry any. `pictures` says whether a picture
// counts: it does where a section would otherwise be dropped (a picture would be lost with it),
// and it does not where a mandatory section must carry text (a picture can draw nothing, and what
// one shows is never read).
function readNarrative(section: CompositionSection, pictures: boolean): Narrative {
  const div: unknown = section.text?.div;
  if (div === undefined) return "absent";
  if (typeof div !== "string") return "unreadable";
  try {
    const text = pictures ? xhtmlToText(div) : xhtmlToText(div).replace(PICTURE_TOKEN, "");
    return Array.from(text).some(isVisible) ? "present" : "absent";
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
      const narrative = readNarrative(section, true);
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
    const narrative = readNarrative(match.section, false);
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

  const heading: unknown = (match.section as { title?: unknown }).title;
  const sourceTitle = typeof heading === "string" ? heading : undefined;
  const target: CompositionSection = {
    id: stableUuid("ema-qrd-section", rule.sourceKey),
    // The heading the source carries when the QRD template permits it (a label may omit a
    // heading's optional wording); otherwise the manifest's.
    title:
      sourceTitle !== undefined && permittedTitles(rule).includes(sourceTitle)
        ? sourceTitle
        : rule.title,
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

const EXTENSION_BASE = "http://ema.europa.eu/fhir/StructureDefinition/";
const SPOR_ORGANISATIONS = "https://spor.ema.europa.eu/v1/organisations/";
export const PROCEDURE_NUMBER_SYSTEM = "http://ema.europa.eu/fhir/procedureIdentifierNumber";

type Identifier = { system?: unknown; value?: unknown };

function identifiersIn(value: unknown): Identifier[] {
  if (Array.isArray(value)) return value as Identifier[];
  return value === undefined || value === null ? [] : [value];
}

// The one identifier in `system`, or none; two are ambiguous and refused.
function identifierInSystem(value: unknown, system: string, where: string): string | undefined {
  const matches = identifiersIn(value).filter(
    (identifier) => identifier.system === system && typeof identifier.value === "string",
  );
  if (matches.length > 1) {
    throw new TransformationError("Product identity is ambiguous", [
      `${where} has more than one identifier in ${system}`,
    ]);
  }
  return matches[0]?.value as string | undefined;
}

type ListExtension = { url: string } & Record<string, unknown>;
type ListIdentity = { productName: string | undefined; extensions: ListExtension[] };

// What the EMA List states about the product (EUEpiList extensions and title), selected from
// the graph by path and identifier system, and nothing it does not state
// (docs/design/authority-import-contract.md, D11). The synthetic Type 2 graph has no value in
// these systems, so it gets no extension.
function listIdentity(sourceBundle: FhirBundle): ListIdentity {
  const resources = sourceBundle.entry;
  const byUrl = new Map(resources.map((entry) => [entry.fullUrl, entry.resource]));
  const authorisations = resources.filter(
    ({ resource }) => resource.resourceType === "RegulatedAuthorization",
  );
  if (authorisations.length > 1) {
    throw new TransformationError("Product identity is ambiguous", [
      "The graph has more than one RegulatedAuthorization",
    ]);
  }
  const products = resources.filter(
    ({ resource }) => resource.resourceType === "MedicinalProductDefinition",
  );
  const names: unknown = products.length === 1 ? products[0]?.resource.name : undefined;
  if (Array.isArray(names) && names.length > 1) {
    throw new TransformationError("Product identity is ambiguous", [
      "The product has more than one name",
    ]);
  }
  const productName = Array.isArray(names)
    ? ((names[0] as { productName?: unknown } | undefined)?.productName as string | undefined)
    : undefined;

  const extensions: ListExtension[] = [];
  const authorisation = authorisations[0]?.resource;
  if (authorisation === undefined) return { productName, extensions };
  const add = (name: string, value: Record<string, unknown>): void => {
    extensions.push({ url: `${EXTENSION_BASE}${name}`, ...value });
  };

  const holderUrl = (authorisation.holder as { reference?: unknown } | undefined)?.reference;
  const holder = typeof holderUrl === "string" ? byUrl.get(holderUrl) : undefined;
  const holderId = identifierInSystem(holder?.identifier, SPOR_ORGANISATIONS, "The holder");
  if (holderId !== undefined) {
    add("ext-epi-marketing-authorisation-holder", {
      valueIdentifier: { system: SPOR_ORGANISATIONS, value: holderId },
    });
    if (typeof holder?.name === "string") {
      add("ext-epi-marketing-authorisation-holder-display", { valueString: holder.name });
    }
  }
  const regulator = authorisation.regulator as
    { identifier?: unknown; display?: unknown } | undefined;
  const agencyId = identifierInSystem(regulator?.identifier, SPOR_ORGANISATIONS, "The regulator");
  if (agencyId !== undefined) {
    add("ext-epi-regulatory-agency", {
      valueIdentifier: { system: SPOR_ORGANISATIONS, value: agencyId },
    });
    if (typeof regulator?.display === "string") {
      add("ext-epi-regulatory-agency-display", { valueString: regulator.display });
    }
  }
  const procedure = identifierInSystem(
    (authorisation.case as { identifier?: unknown } | undefined)?.identifier,
    PROCEDURE_NUMBER_SYSTEM,
    "The authorisation's procedure",
  );
  if (procedure !== undefined) {
    add("ext-epi-procedure-number", {
      valueIdentifier: { system: PROCEDURE_NUMBER_SYSTEM, value: procedure },
    });
  }
  return { productName, extensions };
}

function createEmaList(
  mapping: EmaMapping,
  packageId: string,
  documentFullUrl: string,
  title: string,
  identity: ListIdentity,
): FhirResource {
  return {
    resourceType: "List",
    id: stableUuid("ema-epi-list", packageId),
    meta: { profile: [mapping.profiles.list] },
    ...(identity.extensions.length === 0 ? {} : { extension: identity.extensions }),
    identifier: [
      {
        system: "https://khs.dev/fhir/identifier/epi-package",
        value: packageId,
      },
    ],
    status: "current",
    mode: "working",
    // The product's name, as the EMA's own Lists are titled; the document's title where the
    // graph names no product.
    title: identity.productName ?? `${title} — EMA ePI document index`,
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

// The source Bundle's identifier value, the one key every persisted id derives from. Required:
// a source without one is refused rather than keyed by something its producer chose less visibly.
export function sourceIdentifierValue(sourceBundle: FhirBundle): string {
  const value: unknown = (sourceBundle.identifier as { value?: unknown } | undefined)?.value;
  if (typeof value !== "string" || value.length === 0) {
    throw new TransformationError("Source Bundle has no identifier value", [
      "Bundle.identifier.value is required: every persisted id derives from it",
    ]);
  }
  return value;
}

type Reidentified = { entries: BundleEntry[]; fullUrls: Map<string, string> };

// Gives every entry after the Composition a new id and `urn:uuid` fullUrl derived from the source
// identifier value and its position, and rewrites the references between them. An entry keeps no
// id or fullUrl its source chose, so a run cannot PUT into another run's resources.
function reidentifiedEntries(
  sourceBundle: FhirBundle,
  sourceIdentifier: string,
  compositionFullUrl: string,
): Reidentified {
  const fullUrls = new Map<string, string>();
  const composition = sourceBundle.entry[0];
  if (composition !== undefined) fullUrls.set(composition.fullUrl, compositionFullUrl);
  const planned = sourceBundle.entry.slice(1).map((entry, offset) => {
    const id = stableUuid(
      `ema-entry:${entry.resource.resourceType}`,
      `${sourceIdentifier}:${offset + 1}`,
    );
    const fullUrl = `urn:uuid:${id}`;
    if (fullUrls.has(entry.fullUrl)) {
      throw new TransformationError("Source Bundle entries are ambiguous", [
        `Two entries share the fullUrl at Bundle.entry[${offset + 1}]`,
      ]);
    }
    fullUrls.set(entry.fullUrl, fullUrl);
    return { entry, id, fullUrl, position: offset + 1 };
  });
  const entries = planned.map(({ entry, id, fullUrl, position }) => ({
    ...structuredClone(entry),
    fullUrl,
    resource: {
      ...rewriteReferences(structuredClone(entry.resource), fullUrls, `Bundle.entry[${position}]`),
      id,
    },
  }));
  return { entries, fullUrls };
}

// Rewrites every string-valued `reference` to the new fullUrl of the entry it names. A reference
// that names no entry of the Bundle (relative, versioned, `#contained`, external) is refused: it
// could point into another run's resources. A Reference by identifier alone carries no
// `reference` and is kept.
function rewriteReferences<T>(value: T, fullUrls: Map<string, string>, at: string): T {
  const walk = (node: unknown, path: string): unknown => {
    if (Array.isArray(node)) return node.map((item, index) => walk(item, `${path}[${index}]`));
    if (node === null || typeof node !== "object") return node;
    const out: Record<string, unknown> = {};
    for (const [key, child] of Object.entries(node as Record<string, unknown>)) {
      if (key === "reference" && typeof child === "string") {
        const target = fullUrls.get(child);
        if (target === undefined) {
          throw new TransformationError("Source reference names no entry of the Bundle", [
            `${path}.reference names no entry of the Bundle`,
          ]);
        }
        out[key] = target;
      } else {
        out[key] = walk(child, `${path}.${key}`);
      }
    }
    return out;
  };
  return walk(value, at) as T;
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

  // Every id the run persists derives from this one checked value, so a run writes only into its
  // own namespace (docs/design/authority-import-contract.md, D7): no fallback to Bundle.id.
  const sourceIdentifier = sourceIdentifierValue(sourceBundle);
  const packageId = `ema-${sourceIdentifier}`;
  const compositionId = stableUuid("ema-composition", sourceIdentifier);
  const bundleId = stableUuid("ema-bundle", sourceIdentifier);
  const compositionFullUrl = `urn:uuid:${compositionId}`;
  const bundleFullUrl = `urn:uuid:${bundleId}`;
  const copied = reidentifiedEntries(sourceBundle, sourceIdentifier, compositionFullUrl);

  // The Composition with its references rewritten; everything below is built from this, never
  // from the source, so no source reference reaches the output unrewritten.
  const rewritten = rewriteReferences(
    structuredClone(sourceComposition),
    copied.fullUrls,
    "Composition",
  );
  const targetComposition: FhirComposition = {
    ...rewritten,
    id: compositionId,
    meta: { profile: mapping.profiles.composition },
    // Always English: a source declaring any other language, or none, has already failed above.
    language: "en",
    extension: [
      ...((rewritten.extension as unknown[] | undefined) ?? []).filter(
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

  // The output Bundle carries only these elements; any other the source has (a signature, a link,
  // an entry's request or search) is refused, not dropped and not copied: it could carry a
  // reference or a URL outside the run's namespace.
  // A source's meta may carry what the store manages (versionId, lastUpdated) and the profiles
  // the crosswalk replaces; anything else (an extension, a tag, a source URI) is refused, since
  // it could carry a reference outside the run's namespace. The output's meta is the profile.
  const metaIssues = [
    ...Object.keys((sourceBundle.meta as Record<string, unknown> | undefined) ?? {})
      .filter((key) => !CARRIED_META_ELEMENTS.has(key))
      .map((key) => `Source Bundle.meta carries ${key}, which the crosswalk does not carry`),
    // Every resource the run persists, the Composition and each copied entry: its meta is
    // limited alike, and it holds no contained resource and no implicit rules, which could carry
    // what the rewrite does not reach.
    ...sourceBundle.entry.flatMap(({ resource }, position) => {
      const where = position === 0 ? "Composition" : `Bundle.entry[${position}]`;
      const meta = (resource.meta as Record<string, unknown> | undefined) ?? {};
      return [
        ...Object.keys(meta)
          .filter((key) => !CARRIED_META_ELEMENTS.has(key))
          .map((key) => `Source ${where}.meta carries ${key}, which the crosswalk does not carry`),
        ...["contained", "implicitRules"]
          .filter((key) => resource[key] !== undefined)
          .map((key) => `Source ${where} carries ${key}, which the crosswalk does not carry`),
      ];
    }),
  ];
  const bundleIssues = [
    ...metaIssues,
    ...Object.keys(sourceBundle)
      .filter((key) => !CARRIED_BUNDLE_ELEMENTS.has(key))
      .map((key) => `Source Bundle carries ${key}, which the crosswalk does not carry`),
    ...sourceBundle.entry.flatMap((entry, position) =>
      Object.keys(entry)
        .filter((key) => key !== "fullUrl" && key !== "resource")
        .map(
          (key) =>
            `Source Bundle.entry[${position}] carries ${key}, which the crosswalk does not carry`,
        ),
    ),
  ];
  if (bundleIssues.length > 0) {
    throw new TransformationError("Source Bundle carries what the crosswalk refuses", bundleIssues);
  }
  const targetBundle: FhirBundle = {
    resourceType: "Bundle",
    id: bundleId,
    meta: { profile: [mapping.profiles.bundle] },
    // The source declared an English tag in some spelling ("EN", "en-GB"); the output says "en",
    // as the Composition does.
    language: "en",
    identifier: {
      system: "https://khs.dev/fhir/identifier/ema-document",
      value: bundleId,
    },
    type: "document",
    timestamp: sourceBundle.timestamp,
    entry: [{ fullUrl: compositionFullUrl, resource: targetComposition }, ...copied.entries],
  };

  const list = createEmaList(
    mapping,
    packageId,
    bundleFullUrl,
    targetComposition.title,
    listIdentity(sourceBundle),
  );
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
